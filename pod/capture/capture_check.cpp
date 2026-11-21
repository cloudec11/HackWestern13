/*
 * capture_check -- test consumer for pod-capture's frame ring. Owner: agent-1 (P1-1).
 * Also a minimal example of how pod-infer (agent-2) reads frames.
 *
 *   capture_check [--shm /pod_frames] [--seconds 10]
 *
 * Verifies every frame header, counts skipped frames (seq gaps: expected, newest wins),
 * and measures capture->consumer latency = now - t_capture_ns (both CLOCK_MONOTONIC).
 * Exit 0 if frames arrived and every header was valid.
 */
#include <time.h>
#include <unistd.h>

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>

#include "pod_msgs.h"
#include "ring.h"

static int64_t mono_ns() {
    timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return static_cast<int64_t>(ts.tv_sec) * 1000000000LL + ts.tv_nsec;
}

static double pct(std::vector<int64_t> v, double p) {
    if (v.empty()) return 0.0;
    std::sort(v.begin(), v.end());
    return static_cast<double>(v[static_cast<size_t>(p * static_cast<double>(v.size() - 1) + 0.5)]);
}

int main(int argc, char** argv) {
    std::string shm = "/pod_frames";
    int seconds = 10;
    for (int i = 1; i + 1 < argc; i += 2) {
        std::string a = argv[i];
        if (a == "--shm") shm = argv[i + 1];
        else if (a == "--seconds") seconds = std::atoi(argv[i + 1]);
    }

    pod::ShmRing r;
    bool opened = false;
    for (int t = 0; t < 50 && !(opened = r.open(shm.c_str())); t++) usleep(100000);  /* wait up to 5 s */
    if (!opened) { std::fprintf(stderr, "capture_check: cannot open ring %s (is pod-capture running?)\n", shm.c_str()); return 1; }

    uint64_t last = 0, frames = 0, skipped = 0, bad = 0;
    uint32_t w = 0, h = 0;
    std::vector<int64_t> lat;
    const int64_t end = mono_ns() + static_cast<int64_t>(seconds) * 1000000000LL;
    while (mono_ns() < end) {
        if (!r.ring.wait(500)) continue;
        const pod_frame_hdr_t* f = r.ring.acquire_latest(last);
        if (!f) continue;
        const int64_t now = mono_ns();
        const bool ok = f->magic == POD_FRAME_MAGIC && f->version == POD_MSGS_VERSION &&
                        f->pixel_format == POD_PIX_YUY2 && f->width > 0 && f->height > 0 &&
                        f->stride >= 2 * f->width && f->data_size == f->stride * f->height &&
                        f->slot < POD_FRAME_SLOTS && f->t_capture_ns > 0 && f->t_capture_ns <= now;
        if (!ok) bad++;
        if (last && f->seq > last + 1) skipped += f->seq - last - 1;
        lat.push_back(now - f->t_capture_ns);
        w = f->width; h = f->height;
        last = f->seq;
        frames++;
        r.ring.release();   /* release quickly: pod-infer would copy or preprocess first */
    }
    std::printf("capture_check: %llu frames in %d s (%.1f fps), %ux%u, skipped=%llu, bad_headers=%llu\n",
                static_cast<unsigned long long>(frames), seconds, static_cast<double>(frames) / seconds, w, h,
                static_cast<unsigned long long>(skipped), static_cast<unsigned long long>(bad));
    std::printf("capture_check: capture->consumer latency p50=%.2f ms p99=%.2f ms max=%.2f ms\n",
                pct(lat, 0.50) / 1e6, pct(lat, 0.99) / 1e6, pct(lat, 1.0) / 1e6);
    return (frames > 0 && bad == 0) ? 0 : 1;
}
