/*
 * fake_track -- stands in for pod-track (agent-2) until it exists. Owner: agent-1 (P2-2).
 * Sends made-up visit events to pod-log over /pod_events, exactly as pod-track will.
 *
 *   fake_track [--count 20] [--interval-ms 200] [--invalid 0]
 *
 * --invalid N  also sends N events with max_score = 1.5 (pod-log must skip them).
 */
#include <time.h>
#include <unistd.h>

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

#include "events_mq.h"
#include "pod_msgs.h"

static int64_t wall_ms() {
    timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    return static_cast<int64_t>(ts.tv_sec) * 1000 + ts.tv_nsec / 1000000;
}

int main(int argc, char** argv) {
    int count = 20, interval_ms = 200, invalid = 0;
    for (int i = 1; i + 1 < argc; i += 2) {
        std::string a = argv[i];
        if (a == "--count") count = std::atoi(argv[i + 1]);
        else if (a == "--interval-ms") interval_ms = std::atoi(argv[i + 1]);
        else if (a == "--invalid") invalid = std::atoi(argv[i + 1]);
    }
    pod::EventSender tx;
    int sent = 0, failed = 0;
    int bad_sent = 0;
    const int total = count + invalid;
    for (int i = 0; i < total; i++) {
        /* every third event is invalid until `invalid` of them have gone out */
        bool bad = bad_sent < invalid && (i % 3 == 1 || total - i <= invalid - bad_sent);
        if (bad) bad_sent++;
        pod_event_t e;
        std::memset(&e, 0, sizeof e);
        int64_t now = wall_ms();
        e.t_end_ms = now;
        e.t_start_ms = now - 2000 - (i % 7) * 500;
        e.class_id = static_cast<uint16_t>(1 + i % 10);
        e.max_score = bad ? 1.5f : 0.55f + 0.04f * static_cast<float>(i % 10);
        e.track_id = static_cast<uint32_t>(i + 1);
        e.n_frames = static_cast<uint32_t>(10 + i % 50);
        bool ok = false;
        for (int t = 0; t < 40 && !(ok = tx.send(e)); t++) usleep(50000);   /* queue full or pod-log not up: wait up to 2 s */
        ok ? sent++ : failed++;
        if (interval_ms > 0) usleep(static_cast<useconds_t>(interval_ms) * 1000);
    }
    std::printf("fake_track: sent=%d (of which invalid=%d) failed=%d\n", sent, bad_sent, failed);
    return failed ? 1 : 0;
}
