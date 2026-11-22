/*
 * pod-infer -- newest camera frame -> TFLite detector -> animal detections. Owner: agent-2 (P1-2).
 *
 *   pod-infer [--model models/efficientdet_lite0.tflite] [--labels models/labels.csv]
 *             [--shm /pod_frames] [--threads 3] [--prio 0] [--min-score 0.4]
 *             [--dets-mq /pod_dets] [--seconds 0] [--stats 5] [--quiet]
 *             [--health-out pod-data/uplink/heartbeat.json] [--status pod-data/supervisor/status.json]
 *   pod-infer --ppm image.ppm [--model ...] [--labels ...]     one still image, then exit
 *
 * Needs I/O privilege on QNX (run as root): XNNPACK's cpuinfo reads a privileged CPU register.
 *
 * Per frame: wait for the newest frame in the shm ring (pod-capture), convert YUYV straight
 * into the model input, release the ring slot, run the model, keep only classes in the label
 * table (person is never in it), fill a pod_detections_t and send it to pod-track over
 * --dets-mq (every frame, non-blocking; "" = don't send). Prints one line per frame with
 * detections (unless --quiet) and a latency summary every --stats seconds:
 *   pre   = ring acquire -> model input ready
 *   infer = model run (Invoke)
 *   e2e   = t_infer_done_ns - t_capture_ns (camera callback -> detections ready)
 *
 * --health-out (P3-3): every 10 s, write the Pod health fields for pod-uplink's heartbeat
 * (see heartbeat.h). Off unless given. The file is removed on exit and whenever there is
 * nothing honest to report (no frames in 60 s, no temperature).
 */
#include <pthread.h>
#include <sched.h>
#include <signal.h>
#include <time.h>
#include <unistd.h>

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <memory>
#include <string>
#include <vector>

#include "tensorflow/lite/interpreter.h"
#include "tensorflow/lite/kernels/register.h"
#include "tensorflow/lite/model.h"

#include "dets_mq.h"
#include "health.h"
#include "heartbeat.h"
#include "labels.h"
#include "pod_msgs.h"
#include "preprocess.h"
#include "ring.h"

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

struct Options {
    std::string model = "models/efficientdet_lite0.tflite";
    std::string labels = "models/labels.csv";
    std::string shm = "/pod_frames";
    std::string dets_mq = pod::kDetsMq;
    std::string health_out;
    std::string status = "pod-data/supervisor/status.json";
    std::string ppm;
    int threads = 3;
    int prio = 0;
    float min_score = 0.4f;
    int seconds = 0;
    int stats = 5;
    bool quiet = false;
};

bool parse(int argc, char** argv, Options& o) {
    for (int i = 1; i < argc; ++i) {
        std::string a = argv[i];
        auto next = [&]() -> const char* { return i + 1 < argc ? argv[++i] : nullptr; };
        const char* v = nullptr;
        if (a == "--model" && (v = next())) o.model = v;
        else if (a == "--labels" && (v = next())) o.labels = v;
        else if (a == "--shm" && (v = next())) o.shm = v;
        else if (a == "--dets-mq" && (v = next())) o.dets_mq = v;
        else if (a == "--health-out" && (v = next())) o.health_out = v;
        else if (a == "--status" && (v = next())) o.status = v;
        else if (a == "--ppm" && (v = next())) o.ppm = v;
        else if (a == "--threads" && (v = next())) o.threads = std::atoi(v);
        else if (a == "--prio" && (v = next())) o.prio = std::atoi(v);
        else if (a == "--min-score" && (v = next())) o.min_score = static_cast<float>(std::atof(v));
        else if (a == "--seconds" && (v = next())) o.seconds = std::atoi(v);
        else if (a == "--stats" && (v = next())) o.stats = std::atoi(v);
        else if (a == "--quiet") o.quiet = true;
        else {
            std::fprintf(stderr,
                "usage: pod-infer [--model F] [--labels F] [--shm NAME] [--dets-mq NAME] [--threads N] [--prio N]\n"
                "                 [--min-score S] [--seconds N] [--stats N] [--quiet] [--ppm IMAGE]\n"
                "                 [--health-out FILE] [--status SUPERVISOR_STATUS_JSON]\n");
            return false;
        }
    }
    return true;
}

/* ---- latency stats ---- */

double pct_ms(std::vector<int64_t> v, double p) {
    if (v.empty()) return 0.0;
    std::sort(v.begin(), v.end());
    return static_cast<double>(v[static_cast<size_t>(p * static_cast<double>(v.size() - 1) + 0.5)]) / 1e6;
}

struct Window {
    std::vector<int64_t> pre, infer, e2e;
    uint64_t frames = 0, skipped = 0, dets = 0, unsent = 0;
    void clear() { pre.clear(); infer.clear(); e2e.clear(); frames = skipped = dets = unsent = 0; }
};

void print_stats(const char* tag, const Window& w, double secs) {
    std::printf("pod-infer: %s %.1f s: %llu frames (%.1f/s), skipped %llu, detections %llu, not sent to track %llu\n"
                "pod-infer:   pre   p50 %6.2f  p99 %6.2f  max %6.2f ms\n"
                "pod-infer:   infer p50 %6.2f  p99 %6.2f  max %6.2f ms\n"
                "pod-infer:   e2e   p50 %6.2f  p99 %6.2f  max %6.2f ms\n",
                tag, secs, static_cast<unsigned long long>(w.frames),
                secs > 0 ? static_cast<double>(w.frames) / secs : 0.0,
                static_cast<unsigned long long>(w.skipped), static_cast<unsigned long long>(w.dets),
                static_cast<unsigned long long>(w.unsent),
                pct_ms(w.pre, 0.5), pct_ms(w.pre, 0.99), pct_ms(w.pre, 1.0),
                pct_ms(w.infer, 0.5), pct_ms(w.infer, 0.99), pct_ms(w.infer, 1.0),
                pct_ms(w.e2e, 0.5), pct_ms(w.e2e, 0.99), pct_ms(w.e2e, 1.0));
    std::fflush(stdout);
}

/* ---- model ---- */

struct Detector {
    std::unique_ptr<tflite::FlatBufferModel> model;
    tflite::ops::builtin::BuiltinOpResolver resolver;
    std::unique_ptr<tflite::Interpreter> interp;
    uint8_t* input = nullptr;
    uint32_t in_w = 0, in_h = 0;
    const float* boxes = nullptr;    /* [N][4] ymin xmin ymax xmax, normalised */
    const float* classes = nullptr;  /* [N] raw model class index */
    const float* scores = nullptr;   /* [N] */
    const float* count = nullptr;    /* [1] */
    int max_n = 0;

    bool load(const Options& o) {
        model = tflite::FlatBufferModel::BuildFromFile(o.model.c_str());
        if (!model) { std::fprintf(stderr, "pod-infer: cannot load model %s\n", o.model.c_str()); return false; }
        tflite::InterpreterBuilder builder(*model, resolver);
        builder.SetNumThreads(o.threads);
        if (builder(&interp) != kTfLiteOk || !interp) { std::fprintf(stderr, "pod-infer: interpreter build failed\n"); return false; }
        if (interp->AllocateTensors() != kTfLiteOk) { std::fprintf(stderr, "pod-infer: AllocateTensors failed\n"); return false; }

        TfLiteTensor* in = interp->input_tensor(0);
        if (in->type != kTfLiteUInt8 || in->dims->size != 4 || in->dims->data[0] != 1 || in->dims->data[3] != 3) {
            std::fprintf(stderr, "pod-infer: expected a uint8 [1,H,W,3] input tensor\n");
            return false;
        }
        in_h = static_cast<uint32_t>(in->dims->data[1]);
        in_w = static_cast<uint32_t>(in->dims->data[2]);
        input = interp->typed_input_tensor<uint8_t>(0);

        /* TFLite_Detection_PostProcess outputs: boxes [1,N,4], classes [1,N], scores [1,N], count [1].
         * Find them by shape, then tell classes from scores by name (fallback: standard order). */
        std::vector<int> pair;
        for (size_t i = 0; i < interp->outputs().size(); ++i) {
            const TfLiteTensor* t = interp->output_tensor(i);
            if (t->type != kTfLiteFloat32) { std::fprintf(stderr, "pod-infer: output %zu is not float32\n", i); return false; }
            const TfLiteIntArray* d = t->dims;
            int n = 1;
            for (int k = 0; k < d->size; ++k) n *= d->data[k];
            if (d->size == 3 && d->data[2] == 4) { boxes = t->data.f; max_n = d->data[1]; }
            else if (n == 1) count = t->data.f;
            else pair.push_back(static_cast<int>(i));
        }
        if (!boxes || !count || pair.size() != 2) {
            std::fprintf(stderr, "pod-infer: model outputs are not a detection head:\n");
            for (size_t i = 0; i < interp->outputs().size(); ++i) {
                const TfLiteTensor* t = interp->output_tensor(i);
                std::fprintf(stderr, "  output %zu '%s' %s [", i, t->name ? t->name : "", TfLiteTypeGetName(t->type));
                for (int k = 0; k < t->dims->size; ++k) std::fprintf(stderr, k ? ",%d" : "%d", t->dims->data[k]);
                std::fprintf(stderr, "]\n");
            }
            return false;
        }
        const TfLiteTensor* a = interp->output_tensor(static_cast<size_t>(pair[0]));
        const TfLiteTensor* b = interp->output_tensor(static_cast<size_t>(pair[1]));
        const bool a_is_score = a->name && (std::strstr(a->name, "score") || std::strstr(a->name, "Score"));
        const bool b_is_score = b->name && (std::strstr(b->name, "score") || std::strstr(b->name, "Score"));
        if (b_is_score || !a_is_score) { classes = a->data.f; scores = b->data.f; }
        else { classes = b->data.f; scores = a->data.f; }
        std::printf("pod-infer: model %s, input %ux%u, up to %d boxes, %d threads\n",
                    o.model.c_str(), in_w, in_h, max_n, o.threads);
        return true;
    }

    bool invoke() { return interp->Invoke() == kTfLiteOk; }

    /* Keep animals above min_score, best first (the model already sorts by score). */
    void collect(const pod::LabelTable& labels, float min_score, pod_detections_t* out) const {
        int n = static_cast<int>(*count + 0.5f);
        if (n > max_n) n = max_n;
        out->n_detections = 0;
        for (int i = 0; i < n && out->n_detections < POD_MAX_DETECTIONS; ++i) {
            if (scores[i] < min_score) continue;
            const int cls = labels.class_for(static_cast<int>(classes[i] + 0.5f));
            if (cls == pod::kDrop) continue;                 /* person and non-animals stop here */
            const float* bx = boxes + 4 * i;
            const float y0 = std::clamp(bx[0], 0.0f, 1.0f), x0 = std::clamp(bx[1], 0.0f, 1.0f);
            const float y1 = std::clamp(bx[2], 0.0f, 1.0f), x1 = std::clamp(bx[3], 0.0f, 1.0f);
            if (x1 <= x0 || y1 <= y0) continue;
            pod_detection_t& d = out->det[out->n_detections++];
            std::memset(&d, 0, sizeof d);
            d.x = x0; d.y = y0; d.w = x1 - x0; d.h = y1 - y0;
            d.score = scores[i];
            d.class_id = static_cast<uint16_t>(cls);
        }
    }
};

void print_dets(const pod_detections_t& r, const pod::LabelTable& labels) {
    std::printf("frame %llu t_capture_ns=%lld t_infer_done_ns=%lld e2e=%.1fms n=%u",
                static_cast<unsigned long long>(r.frame_seq), static_cast<long long>(r.t_capture_ns),
                static_cast<long long>(r.t_infer_done_ns),
                static_cast<double>(r.t_infer_done_ns - r.t_capture_ns) / 1e6, r.n_detections);
    for (uint16_t i = 0; i < r.n_detections; ++i) {
        const pod_detection_t& d = r.det[i];
        std::printf(" | %s %.2f [%.2f %.2f %.2f %.2f]", labels.name(d.class_id), static_cast<double>(d.score),
                    static_cast<double>(d.x), static_cast<double>(d.y), static_cast<double>(d.w), static_cast<double>(d.h));
    }
    std::printf("\n");
    std::fflush(stdout);
}

/* ---- --ppm: one still image (binary P6, maxval 255) ---- */

bool read_ppm(const std::string& path, std::vector<uint8_t>* rgb, uint32_t* w, uint32_t* h) {
    std::ifstream f(path, std::ios::binary);
    if (!f) return false;
    std::string magic;
    f >> magic;
    auto skip_comments = [&]() {
        f >> std::ws;
        while (f.peek() == '#') { std::string c; std::getline(f, c); f >> std::ws; }
    };
    unsigned ww = 0, hh = 0, maxv = 0;
    skip_comments(); f >> ww;
    skip_comments(); f >> hh;
    skip_comments(); f >> maxv;
    f.get();  /* single whitespace before pixel data */
    if (magic != "P6" || ww == 0 || hh == 0 || maxv != 255) return false;
    rgb->resize(static_cast<size_t>(ww) * hh * 3);
    f.read(reinterpret_cast<char*>(rgb->data()), static_cast<std::streamsize>(rgb->size()));
    if (!f) return false;
    *w = ww; *h = hh;
    return true;
}

int run_ppm(const Options& o, Detector& det, const pod::LabelTable& labels) {
    std::vector<uint8_t> rgb;
    uint32_t w = 0, h = 0;
    if (!read_ppm(o.ppm, &rgb, &w, &h)) { std::fprintf(stderr, "pod-infer: cannot read %s (binary P6, maxval 255)\n", o.ppm.c_str()); return 1; }
    pod::rgb_resize(rgb.data(), w, h, det.input, det.in_w, det.in_h);
    pod_detections_t r{};
    r.magic = POD_DETS_MAGIC;
    const int64_t t0 = mono_ns();
    if (!det.invoke()) { std::fprintf(stderr, "pod-infer: Invoke failed\n"); return 1; }
    r.t_capture_ns = t0;
    r.t_infer_done_ns = mono_ns();
    det.collect(labels, o.min_score, &r);
    std::printf("pod-infer: %s %ux%u, infer %.1f ms\n", o.ppm.c_str(), w, h, static_cast<double>(r.t_infer_done_ns - t0) / 1e6);
    print_dets(r, labels);
    return 0;
}

/* ---- main loop ---- */

bool open_ring(pod::ShmRing& r, const std::string& name, int tries) {
    for (int t = 0; t < tries; ++t) {
        if (r.open(name.c_str())) return true;
        if (g_stop) return false;
        usleep(100000);
    }
    return false;
}

bool frame_ok(const pod_frame_hdr_t* f) {
    return f->magic == POD_FRAME_MAGIC && f->version == POD_MSGS_VERSION && f->pixel_format == POD_PIX_YUY2 &&
           f->width >= 2 && f->height >= 1 && f->stride >= 2 * f->width && f->data_size >= f->stride * f->height;
}

}  // namespace

int main(int argc, char** argv) {
    Options o;
    if (!parse(argc, argv, o)) return 2;
    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);

    /* Set SCHED_FIFO before the interpreter exists: its worker threads inherit it. */
    if (o.prio > 0) {
        sched_param sp{};
        sp.sched_priority = o.prio;
        int rc = pthread_setschedparam(pthread_self(), SCHED_FIFO, &sp);
        if (rc) std::fprintf(stderr, "pod-infer: SCHED_FIFO %d failed: %s\n", o.prio, std::strerror(rc));
    }

    pod::LabelTable labels;
    std::string err;
    if (!pod::load_labels(o.labels, &labels, &err)) { std::fprintf(stderr, "pod-infer: labels: %s\n", err.c_str()); return 1; }

    Detector det;
    if (!det.load(o)) return 1;
    if (!o.ppm.empty()) return run_ppm(o, det, labels);

    pod::HealthBeat hb;
    pod::DetsSender tx(o.dets_mq.c_str());
    const bool send_dets = !o.dets_mq.empty();
    pod::HealthWriter health(o.health_out, o.status);
    pod::ShmRing ring;
    if (!open_ring(ring, o.shm, 50)) { std::fprintf(stderr, "pod-infer: cannot open ring %s (is pod-capture running?)\n", o.shm.c_str()); return 1; }
    std::printf("pod-infer: reading %s, sending to %s, min score %.2f, prio %d%s\n", o.shm.c_str(),
                send_dets ? o.dets_mq.c_str() : "(nothing)", static_cast<double>(o.min_score),
                o.prio, hb.attached() ? ", supervised" : "");
    if (health.enabled()) std::printf("pod-infer: health -> %s every 10 s (status from %s)\n", o.health_out.c_str(), o.status.c_str());
    std::fflush(stdout);
    const char* last_health_problem = nullptr;

    Window win, total;
    uint64_t last = 0;
    const int64_t t_start = mono_ns();
    int64_t t_win = t_start, t_last_frame = t_start;
    const int64_t t_end = o.seconds > 0 ? t_start + static_cast<int64_t>(o.seconds) * 1000000000LL : 0;

    while (!g_stop && (t_end == 0 || mono_ns() < t_end)) {
        /* Health file: report the first write, each new problem, and each recovery. */
        if (const char* why = health.maybe_write(mono_ns(), wall_ms())) {
            const bool was_ok = last_health_problem && !*last_health_problem;
            if (*why && (!last_health_problem || std::strcmp(why, last_health_problem) != 0))
                std::fprintf(stderr, "pod-infer: no health file: %s\n", why);
            else if (!*why && !was_ok)
                std::printf("pod-infer: health %s", health.last_json().c_str());
            std::fflush(stdout);
            last_health_problem = why;
        }
        if (!ring.ring.wait(500) && mono_ns() - t_last_frame > 2000000000LL) {
            /* No frames for 2 s: pod-capture may have restarted with a new ring. */
            if (open_ring(ring, o.shm, 1)) last = 0;
            t_last_frame = mono_ns();
            continue;
        }
        const pod_frame_hdr_t* f = ring.ring.acquire_latest(last);
        if (!f) continue;
        const int64_t t0 = mono_ns();
        if (!frame_ok(f)) { ring.ring.release(); continue; }

        pod_detections_t r{};
        r.magic = POD_DETS_MAGIC;
        r.frame_seq = f->seq;
        r.t_capture_ns = f->t_capture_ns;
        const uint64_t skipped = (last && f->seq > last + 1) ? f->seq - last - 1 : 0;
        pod::yuyv_to_rgb_resize(ring.ring.payload(f), f->width, f->height, f->stride, det.input, det.in_w, det.in_h);
        last = f->seq;
        ring.ring.release();                   /* frame copied: give the slot back before the slow part */
        const int64_t t1 = mono_ns();

        if (!det.invoke()) { std::fprintf(stderr, "pod-infer: Invoke failed\n"); return 1; }
        r.t_infer_done_ns = mono_ns();
        det.collect(labels, o.min_score, &r);
        const bool unsent = send_dets && !tx.send(r);   /* every frame, even with 0 detections */
        hb.beat();
        health.add(r.t_infer_done_ns, r.t_infer_done_ns - t1, r.t_infer_done_ns - r.t_capture_ns);
        t_last_frame = r.t_infer_done_ns;

        for (Window* w : {&win, &total}) {
            w->pre.push_back(t1 - t0);
            w->infer.push_back(r.t_infer_done_ns - t1);
            w->e2e.push_back(r.t_infer_done_ns - r.t_capture_ns);
            w->frames++;
            w->skipped += skipped;
            w->dets += r.n_detections;
            w->unsent += unsent ? 1 : 0;
        }
        if (!o.quiet && r.n_detections > 0) print_dets(r, labels);

        if (o.stats > 0 && r.t_infer_done_ns - t_win >= static_cast<int64_t>(o.stats) * 1000000000LL) {
            print_stats("last", win, static_cast<double>(r.t_infer_done_ns - t_win) / 1e9);
            win.clear();
            t_win = r.t_infer_done_ns;
        }
    }
    print_stats("total", total, static_cast<double>(mono_ns() - t_start) / 1e9);
    health.remove();   /* no stale health numbers after we stop */
    return 0;
}
