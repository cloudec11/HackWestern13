/*
 * pod-capture -- camera callback -> 4-slot shared-memory ring -> pulse to pod-infer.
 * Owner: agent-1 (P1-1).
 *
 *   pod-capture [--unit 5] [--shm /pod_frames] [--prio 0] [--seconds 0] [--stats 5]
 *               [--max-payload 4147200]
 *
 *   --unit         camera unit (5 = USB webcam under pod/scripts/sensor_pod.conf)
 *   --shm          shared-memory name of the frame ring (pod-infer opens the same name)
 *   --prio         SCHED_FIFO priority for the camera callback thread (0 = leave as is)
 *   --seconds      stop after N seconds (0 = run until SIGINT/SIGTERM)
 *   --stats        print a stats line every N seconds (0 = off)
 *   --max-payload  bytes reserved per ring slot (default fits 1920x1080 YUYV)
 *
 * Needs the camera service on the headless Pod config:  sh pod/scripts/camera_service.sh pod
 *
 * Each frame: the camera library calls vf_cb on its own thread. We timestamp it
 * (CLOCK_MONOTONIC, the contract's t_capture_ns), copy the YUYV pixels into a free ring
 * slot, fill pod_frame_hdr_t and publish. The camera thread never waits for pod-infer.
 */
#include <pthread.h>
#include <sched.h>
#include <signal.h>
#include <time.h>
#include <unistd.h>

#include <algorithm>
#include <atomic>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <string>
#include <vector>

#include "camapi_min.h"
#include "pod_msgs.h"
#include "ring.h"

namespace {

struct Options {
    int unit = 5;
    std::string shm = "/pod_frames";
    int prio = 0;
    int seconds = 0;
    int stats_every = 5;
    uint32_t max_payload = 1920u * 1080u * 2u;
};

int64_t mono_ns() {
    timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return static_cast<int64_t>(ts.tv_sec) * 1000000000LL + ts.tv_nsec;
}

std::atomic<bool> g_stop{false};
void on_signal(int) { g_stop = true; }

double pct(std::vector<int64_t> v, double p) {
    if (v.empty()) return 0.0;
    std::sort(v.begin(), v.end());
    size_t i = static_cast<size_t>(p * static_cast<double>(v.size() - 1) + 0.5);
    return static_cast<double>(v[i]);
}

struct Stats {
    std::mutex mu;
    uint64_t published = 0, bad_frame = 0, too_big = 0, no_slot = 0;
    int32_t last_type = 0;
    uint32_t width = 0, height = 0, stride = 0;
    std::vector<int64_t> copy_ns;     /* callback entry -> publish */
    std::vector<int64_t> cam_lag_us;  /* callback entry minus camera timestamp (if same clock) */
};

struct Ctx {
    pod::Ring* ring = nullptr;
    Stats* st = nullptr;
    uint64_t seq = 0;              /* only touched on the camera thread */
    int prio = 0;
    std::atomic<bool> prio_done{false};
};

void vf_cb(pod_cam_handle_t, pod_cam_buffer_t* b, void* arg) {
    const int64_t t0 = mono_ns();
    Ctx* c = static_cast<Ctx*>(arg);

    if (c->prio > 0 && !c->prio_done.exchange(true)) {
        sched_param sp{};
        sp.sched_priority = c->prio;
        int rc = pthread_setschedparam(pthread_self(), SCHED_FIFO, &sp);
        if (rc) std::fprintf(stderr, "pod-capture: SCHED_FIFO %d on camera thread failed: %s\n", c->prio, std::strerror(rc));
        else    std::fprintf(stderr, "pod-capture: camera thread now SCHED_FIFO priority %d\n", c->prio);
    }

    const int32_t type = pod_cambuf_frametype(b);
    const uint8_t* src = pod_cambuf_framebuf(b);
    const uint32_t h = pod_cambuf_ycbycr_height(b);
    const uint32_t w = pod_cambuf_ycbycr_width(b);
    const uint32_t stride = pod_cambuf_ycbycr_stride(b);
    const uint64_t bytes = static_cast<uint64_t>(stride) * h;
    Stats& st = *c->st;

    if (type != POD_CAM_FRAMETYPE_YCBYCR || !src || w == 0 || h == 0 || stride < 2 * w) {
        std::lock_guard<std::mutex> lk(st.mu);
        st.bad_frame++;
        st.last_type = type;
        return;
    }
    if (bytes > c->ring->max_payload()) {
        std::lock_guard<std::mutex> lk(st.mu);
        st.too_big++;
        return;
    }
    uint32_t slot = 0;
    pod_frame_hdr_t* hdr = c->ring->begin_write(&slot);
    if (!hdr) {
        std::lock_guard<std::mutex> lk(st.mu);
        st.no_slot++;
        return;
    }
    std::memcpy(c->ring->payload(hdr), src, bytes);
    hdr->seq = ++c->seq;
    hdr->t_capture_ns = t0;
    hdr->pixel_format = POD_PIX_YUY2;
    hdr->width = w;
    hdr->height = h;
    hdr->stride = stride;
    hdr->data_size = static_cast<uint32_t>(bytes);
    std::memset(hdr->reserved, 0, sizeof hdr->reserved);
    c->ring->publish(slot);
    const int64_t t1 = mono_ns();

    std::lock_guard<std::mutex> lk(st.mu);
    st.published++;
    st.last_type = type;
    st.width = w; st.height = h; st.stride = stride;
    st.copy_ns.push_back(t1 - t0);
    st.cam_lag_us.push_back(t0 / 1000 - pod_cambuf_timestamp(b));
}

bool parse(int argc, char** argv, Options& o) {
    for (int i = 1; i < argc; i++) {
        std::string a = argv[i];
        auto next = [&](void) -> const char* { return i + 1 < argc ? argv[++i] : nullptr; };
        const char* v = nullptr;
        if (a == "--unit" && (v = next())) o.unit = std::atoi(v);
        else if (a == "--shm" && (v = next())) o.shm = v;
        else if (a == "--prio" && (v = next())) o.prio = std::atoi(v);
        else if (a == "--seconds" && (v = next())) o.seconds = std::atoi(v);
        else if (a == "--stats" && (v = next())) o.stats_every = std::atoi(v);
        else if (a == "--max-payload" && (v = next())) o.max_payload = static_cast<uint32_t>(std::strtoul(v, nullptr, 10));
        else { std::fprintf(stderr, "usage: pod-capture [--unit N] [--shm NAME] [--prio N] [--seconds N] [--stats N] [--max-payload BYTES]\n"); return false; }
    }
    return true;
}

}  // namespace

int main(int argc, char** argv) {
    Options o;
    if (!parse(argc, argv, o)) return 2;

    struct sigaction sa{};
    sa.sa_handler = on_signal;
    sigaction(SIGINT, &sa, nullptr);
    sigaction(SIGTERM, &sa, nullptr);

    pod::ShmRing ring;
    if (!ring.create(o.shm.c_str(), o.max_payload)) {
        std::fprintf(stderr, "pod-capture: cannot create ring %s: %s\n", o.shm.c_str(), std::strerror(errno));
        return 1;
    }

    Stats st;
    Ctx ctx;
    ctx.ring = &ring.ring;
    ctx.st = &st;
    ctx.prio = o.prio;

    pod_cam_handle_t cam = -1;
    int rc = camera_open(o.unit, POD_CAM_MODE_RO, &cam);
    if (rc != POD_CAM_EOK) {
        std::fprintf(stderr, "pod-capture: camera_open(unit %d) failed: %d (%s). Is the camera service on the Pod config?\n",
                     o.unit, rc, std::strerror(rc));
        return 1;
    }
    rc = camera_start_viewfinder(cam, vf_cb, nullptr, &ctx);
    if (rc != POD_CAM_EOK) {
        std::fprintf(stderr, "pod-capture: camera_start_viewfinder failed: %d (%s)\n", rc, std::strerror(rc));
        camera_close(cam);
        return 1;
    }
    std::printf("pod-capture: unit %d -> ring %s (%d slots, %u B/slot), prio %d\n",
                o.unit, o.shm.c_str(), POD_FRAME_SLOTS, o.max_payload, o.prio);
    std::fflush(stdout);

    const int64_t start = mono_ns();
    int64_t last_stats = start;
    uint64_t last_published = 0;
    while (!g_stop) {
        usleep(100000);
        const int64_t now = mono_ns();
        if (o.seconds > 0 && now - start >= static_cast<int64_t>(o.seconds) * 1000000000LL) break;
        if (o.stats_every > 0 && now - last_stats >= static_cast<int64_t>(o.stats_every) * 1000000000LL) {
            std::vector<int64_t> copy, lag;
            uint64_t pub, bad, big, noslot;
            uint32_t w, h;
            {
                std::lock_guard<std::mutex> lk(st.mu);
                copy.swap(st.copy_ns);
                lag.swap(st.cam_lag_us);
                pub = st.published; bad = st.bad_frame; big = st.too_big; noslot = st.no_slot;
                w = st.width; h = st.height;
            }
            const double secs = static_cast<double>(now - last_stats) / 1e9;
            std::printf("pod-capture: %ux%u  %.1f fps  published=%llu bad=%llu too_big=%llu no_slot=%llu  "
                        "copy p50=%.0fus p99=%.0fus  cam->callback p50=%.1fms\n",
                        w, h, static_cast<double>(pub - last_published) / secs,
                        static_cast<unsigned long long>(pub), static_cast<unsigned long long>(bad),
                        static_cast<unsigned long long>(big), static_cast<unsigned long long>(noslot),
                        pct(copy, 0.50) / 1000.0, pct(copy, 0.99) / 1000.0, pct(lag, 0.50) / 1000.0);
            std::fflush(stdout);
            last_published = pub;
            last_stats = now;
        }
    }

    camera_stop_viewfinder(cam);
    camera_close(cam);
    {
        std::lock_guard<std::mutex> lk(st.mu);
        std::printf("pod-capture: stopped. published=%llu bad=%llu (last frametype %d) too_big=%llu no_slot=%llu\n",
                    static_cast<unsigned long long>(st.published), static_cast<unsigned long long>(st.bad_frame),
                    st.last_type, static_cast<unsigned long long>(st.too_big), static_cast<unsigned long long>(st.no_slot));
    }
    return 0;  /* ShmRing destructor unmaps and unlinks the ring */
}
