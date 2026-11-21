/*
 * track_e2e -- end-to-end test of a running pod-track over real POSIX queues. Owner: agent-2 (P2-1).
 * Uses its own queue names, so it never touches the live /pod_dets or /pod_events.
 *
 *   ./build/track/pod-track --dets-mq /a2_test_dets --events-mq /a2_test_events &
 *   ./build/track/track_e2e --dets-mq /a2_test_dets --events-mq /a2_test_events
 *
 * Plays ~6 s of frames at 15 fps in real time, playing the part of pod-infer:
 *   - a cat (class 2) walking across for 30 frames
 *   - a bird (class 1) in another corner for 31 frames, overlapping in time
 *   - a one-frame dog (class 3) flicker, which must NOT become a visit
 * then waits for pod-track to end the tracks (2 s gap). Expects exactly two events.
 * Exit 0 on pass. Unlinks both test queues at the end.
 */
#include <mqueue.h>
#include <time.h>
#include <unistd.h>

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include "dets_mq.h"
#include "events_mq.h"
#include "pod_msgs.h"

static int64_t mono_ns() {
    timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return static_cast<int64_t>(ts.tv_sec) * 1000000000LL + ts.tv_nsec;
}

static void add(pod_detections_t& r, float x, float y, float w, float h, uint16_t cls, float score) {
    pod_detection_t& d = r.det[r.n_detections++];
    d.x = x; d.y = y; d.w = w; d.h = h; d.class_id = cls; d.score = score;
}

int main(int argc, char** argv) {
    std::string dets = "/a2_test_dets", events = "/a2_test_events";
    for (int i = 1; i + 1 < argc; i += 2) {
        std::string a = argv[i];
        if (a == "--dets-mq") dets = argv[i + 1];
        else if (a == "--events-mq") events = argv[i + 1];
    }
    if (dets == pod::kDetsMq || events == pod::kEventsMq) {
        std::fprintf(stderr, "track_e2e: refusing to use the live queues\n");
        return 2;
    }

    pod::EventReceiver rx;
    if (!rx.create(events.c_str())) { std::perror("track_e2e: events queue"); return 1; }
    pod::DetsSender tx(dets.c_str());

    /* pod-track creates the dets queue: wait for it (up to 5 s). */
    pod_detections_t probe{};
    probe.magic = POD_DETS_MAGIC;
    probe.t_capture_ns = mono_ns();
    bool up = false;
    for (int t = 0; t < 50 && !(up = tx.send(probe)); ++t) usleep(100000);
    if (!up) { std::fprintf(stderr, "track_e2e: %s not there (is pod-track running?)\n", dets.c_str()); return 1; }

    int sent = 0, failed = 0;
    for (int i = 0; i < 90; ++i) {                     /* 6 s at 15 fps */
        pod_detections_t r{};
        r.magic = POD_DETS_MAGIC;
        r.frame_seq = static_cast<uint64_t>(i + 1);
        r.t_capture_ns = mono_ns();
        if (i < 30) add(r, 0.05f + 0.01f * i, 0.4f, 0.25f, 0.25f, 2, 0.6f + 0.005f * i);    /* cat */
        if (i >= 10 && i < 41) add(r, 0.75f, 0.05f, 0.15f, 0.15f, 1, 0.7f);                 /* bird */
        if (i == 50) add(r, 0.4f, 0.7f, 0.2f, 0.2f, 3, 0.5f);                                /* flicker */
        r.t_infer_done_ns = mono_ns();
        tx.send(r) ? sent++ : failed++;
        usleep(66000);
    }

    std::vector<pod_event_t> got;
    const int64_t until = mono_ns() + 4000000000LL;   /* tracks end 2 s after last seen */
    while (mono_ns() < until) {
        pod_event_t e;
        if (rx.receive(&e, 200) == 1) got.push_back(e);
    }
    mq_unlink(events.c_str());
    mq_unlink(dets.c_str());

    bool cat = false, bird = false;
    for (const pod_event_t& e : got) {
        std::printf("track_e2e: event class %u track %u n_frames %u max %.3f duration %lld ms seq %llu\n",
                    e.class_id, e.track_id, e.n_frames, static_cast<double>(e.max_score),
                    static_cast<long long>(e.t_end_ms - e.t_start_ms), static_cast<unsigned long long>(e.seq));
        if (e.class_id == 2 && e.n_frames == 30 && e.seq == 0) cat = true;
        if (e.class_id == 1 && e.n_frames == 31 && e.seq == 0) bird = true;
    }
    const bool pass = got.size() == 2 && cat && bird && failed == 0;
    std::printf("track_e2e: sent %d frames (%d failed), got %zu events: %s\n", sent, failed, got.size(),
                pass ? "PASS" : "FAIL (want cat 30 frames + bird 31 frames, nothing else)");
    return pass ? 0 : 1;
}
