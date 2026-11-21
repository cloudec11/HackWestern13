/*
 * pod-track -- per-frame detections -> one visit event per animal visit. Owner: agent-2 (P2-1).
 *
 *   pod-track [--dets-mq /pod_dets] [--events-mq /pod_events] [--iou 0.3] [--gap 2.0]
 *             [--min-frames 3] [--max-visit 0] [--prio 0] [--stats 30] [--quiet]
 *
 * Reads pod_detections_t from pod-infer (pod/track/dets_mq.h), tracks animals with an IoU
 * tracker (pod/track/tracker.h), and sends each finished visit to pod-log as a pod_event_t
 * over agent-1's /pod_events queue (pod/ipc/events_mq.h). seq and pod_id are left 0: pod-log
 * assigns them. Events pod-log cannot take yet are kept (up to 256) and retried.
 * On SIGINT/SIGTERM, open tracks are ended and sent before exit.
 */
#include <pthread.h>
#include <sched.h>
#include <signal.h>
#include <time.h>

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <string>
#include <vector>

#include "dets_mq.h"
#include "events_mq.h"
#include "health.h"
#include "pod_msgs.h"
#include "tracker.h"

namespace {

volatile sig_atomic_t g_stop = 0;
void on_signal(int) { g_stop = 1; }

int64_t mono_ns() {
    timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return static_cast<int64_t>(ts.tv_sec) * 1000000000LL + ts.tv_nsec;
}
int64_t wall_ms() {
    timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    return static_cast<int64_t>(ts.tv_sec) * 1000 + ts.tv_nsec / 1000000;
}
/* CLOCK_MONOTONIC ns (frame capture time) -> Unix ms, using the current offset. */
int64_t mono_to_wall_ms(int64_t t_ns) { return wall_ms() - (mono_ns() - t_ns) / 1000000; }

struct Options {
    std::string dets_mq = pod::kDetsMq;
    std::string events_mq = pod::kEventsMq;
    pod::TrackerConfig cfg;
    int prio = 0;
    int stats = 30;
    bool quiet = false;
};

bool parse(int argc, char** argv, Options& o) {
    for (int i = 1; i < argc; ++i) {
        std::string a = argv[i];
        auto next = [&]() -> const char* { return i + 1 < argc ? argv[++i] : nullptr; };
        const char* v = nullptr;
        if (a == "--dets-mq" && (v = next())) o.dets_mq = v;
        else if (a == "--events-mq" && (v = next())) o.events_mq = v;
        else if (a == "--iou" && (v = next())) o.cfg.iou_min = static_cast<float>(std::atof(v));
        else if (a == "--gap" && (v = next())) o.cfg.gap_ns = static_cast<int64_t>(std::atof(v) * 1e9);
        else if (a == "--min-frames" && (v = next())) o.cfg.min_frames = static_cast<uint32_t>(std::atoi(v));
        else if (a == "--max-visit" && (v = next())) o.cfg.max_visit_ns = static_cast<int64_t>(std::atof(v) * 1e9);
        else if (a == "--prio" && (v = next())) o.prio = std::atoi(v);
        else if (a == "--stats" && (v = next())) o.stats = std::atoi(v);
        else if (a == "--quiet") o.quiet = true;
        else {
            std::fprintf(stderr,
                "usage: pod-track [--dets-mq NAME] [--events-mq NAME] [--iou F] [--gap SECONDS]\n"
                "                 [--min-frames N] [--max-visit SECONDS] [--prio N] [--stats N] [--quiet]\n");
            return false;
        }
    }
    return true;
}

pod_event_t to_event(const pod::Visit& v) {
    pod_event_t e;
    std::memset(&e, 0, sizeof e);          /* seq, pod_id, thumb_path stay empty: pod-log fills seq/pod_id */
    e.t_start_ms = mono_to_wall_ms(v.t_start_ns);
    e.t_end_ms = mono_to_wall_ms(v.t_end_ns);
    if (e.t_end_ms < e.t_start_ms) e.t_end_ms = e.t_start_ms;
    e.max_score = v.max_score;
    e.track_id = v.track_id;
    e.n_frames = v.n_frames;
    e.class_id = v.class_id;
    return e;
}

}  // namespace

int main(int argc, char** argv) {
    Options o;
    if (!parse(argc, argv, o)) return 2;
    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);
    if (o.prio > 0) {
        sched_param sp{};
        sp.sched_priority = o.prio;
        int rc = pthread_setschedparam(pthread_self(), SCHED_FIFO, &sp);
        if (rc) std::fprintf(stderr, "pod-track: SCHED_FIFO %d failed: %s\n", o.prio, std::strerror(rc));
    }

    pod::DetsReceiver rx;
    if (!rx.create(o.dets_mq.c_str())) { std::perror("pod-track: mq_open dets"); return 1; }
    pod::EventSender tx(o.events_mq.c_str());
    pod::HealthBeat hb;
    pod::Tracker tracker(o.cfg);

    std::printf("pod-track: %s -> %s, iou %.2f, gap %.1f s, min %u frames, max visit %s%s\n",
                o.dets_mq.c_str(), o.events_mq.c_str(), static_cast<double>(o.cfg.iou_min),
                static_cast<double>(o.cfg.gap_ns) / 1e9, o.cfg.min_frames,
                o.cfg.max_visit_ns > 0 ? (std::to_string(o.cfg.max_visit_ns / 1000000000LL) + " s").c_str() : "off",
                hb.attached() ? ", supervised" : "");
    std::fflush(stdout);

    std::deque<pod_event_t> pending;
    std::vector<pod::Visit> done;
    uint64_t frames = 0, bad = 0, sent = 0, dropped = 0, frames_win = 0;
    int64_t t_win = mono_ns();

    auto queue_visits = [&]() {
        for (const pod::Visit& v : done) {
            if (pending.size() >= 256) { pending.pop_front(); dropped++; }
            pending.push_back(to_event(v));
            if (!o.quiet)
                std::printf("pod-track: visit track %u class %u, %.1f s, %u frames, max score %.2f\n",
                            v.track_id, v.class_id, static_cast<double>(v.t_end_ns - v.t_start_ns) / 1e9,
                            v.n_frames, static_cast<double>(v.max_score));
        }
        done.clear();
        while (!pending.empty() && tx.send(pending.front())) { pending.pop_front(); sent++; }
        std::fflush(stdout);
    };

    while (!g_stop) {
        pod_detections_t r;
        const int got = rx.receive(&r, 200);
        if (got == 1) {
            if (r.magic != POD_DETS_MAGIC || r.n_detections > POD_MAX_DETECTIONS) { bad++; continue; }
            tracker.update(r, &done);
            frames++;
            frames_win++;
        } else if (got == 0) {
            tracker.expire(mono_ns(), &done);   /* no frames: pod-infer is idle or down */
        } else {
            std::perror("pod-track: mq_receive");
            return 1;
        }
        queue_visits();
        hb.beat();

        const int64_t now = mono_ns();
        if (o.stats > 0 && now - t_win >= static_cast<int64_t>(o.stats) * 1000000000LL) {
            std::printf("pod-track: %.1f frames/s, %zu active tracks, events sent %llu, waiting %zu, dropped %llu, bad msgs %llu\n",
                        static_cast<double>(frames_win) * 1e9 / static_cast<double>(now - t_win), tracker.active(),
                        static_cast<unsigned long long>(sent), pending.size(),
                        static_cast<unsigned long long>(dropped), static_cast<unsigned long long>(bad));
            std::fflush(stdout);
            frames_win = 0;
            t_win = now;
        }
    }

    tracker.flush(&done);
    queue_visits();
    std::printf("pod-track: exit, %llu frames, events sent %llu, unsent %zu, dropped %llu\n",
                static_cast<unsigned long long>(frames), static_cast<unsigned long long>(sent), pending.size(),
                static_cast<unsigned long long>(dropped));
    return 0;
}
