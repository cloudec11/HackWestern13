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
 * --observe: read frames with Ring::peek_latest (read-only copy) instead of being the ring's
 * consumer, so a second pod-infer can run next to the real one for tests. Adds up to 5 ms
 * of polling delay to e2e. Use with --dets-mq "" so it does not feed pod-track twice.
 *
 * --health-out (P3-3): every 10 s, write the Pod health fields for pod-uplink's heartbeat
 * (see heartbeat.h). Off unless given. The file is removed on exit and whenever there is
 * nothing honest to report (no frames in 60 s, no temperature).
 *
 * --clip-model models/clip_vision.tflite (prototype 1): identify the animal with CLIP instead of
 * trusting the COCO class (see clip.h). The detector then only says WHERE to look; a worker thread
 * at normal priority crops that region from the full-resolution frame, runs CLIP on the newest
 * frame it can, and votes (6 of the last 10 checks). While an animal is confirmed, every frame
 * sent to pod-track carries it (class_id from labels.csv, box = where it was last seen), so
 * pod-track makes one visit per animal. The detector loop keeps its priority and frame rate.
 */
#include <pthread.h>
#include <sched.h>
#include <signal.h>
#include <time.h>
#include <unistd.h>

#include <algorithm>
#include <atomic>
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "tensorflow/lite/interpreter.h"
#include "tensorflow/lite/kernels/register.h"
#include "tensorflow/lite/model.h"

#include "clip.h"
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
    bool observe = false;
    std::string latency_log;
    /* CLIP (prototype 1): off unless --clip-model is given */
    std::string clip_model;
    std::string clip_labels = "models/clip/labels.txt";
    std::string clip_text = "models/clip/text.f32";
    float clip_min_prob = 0.45f;     /* a check counts as a vote at this probability */
    float clip_region = 0.3f;        /* detector score to look closer at a region */
    int clip_need = 6, clip_window = 10;
    int clip_threads = 1;
    int clip_prio = 10;              /* worker thread priority (QNX normal = 10), below the detector */
    std::string scan_status;         /* with-motor: small JSON for pod-servo (seeing / confirmed animal) */
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
        else if (a == "--observe") o.observe = true;
        else if (a == "--latency-log" && (v = next())) o.latency_log = v;
        else if (a == "--clip-model" && (v = next())) o.clip_model = v;
        else if (a == "--clip-labels" && (v = next())) o.clip_labels = v;
        else if (a == "--clip-text" && (v = next())) o.clip_text = v;
        else if (a == "--clip-min-prob" && (v = next())) o.clip_min_prob = static_cast<float>(std::atof(v));
        else if (a == "--clip-region" && (v = next())) o.clip_region = static_cast<float>(std::atof(v));
        else if (a == "--clip-need" && (v = next())) o.clip_need = std::atoi(v);
        else if (a == "--clip-window" && (v = next())) o.clip_window = std::atoi(v);
        else if (a == "--clip-threads" && (v = next())) o.clip_threads = std::atoi(v);
        else if (a == "--clip-prio" && (v = next())) o.clip_prio = std::atoi(v);
        else if (a == "--scan-status" && (v = next())) o.scan_status = v;
        else {
            std::fprintf(stderr,
                "usage: pod-infer [--model F] [--labels F] [--shm NAME] [--dets-mq NAME] [--threads N] [--prio N]\n"
                "                 [--min-score S] [--seconds N] [--stats N] [--quiet] [--ppm IMAGE]\n"
                "                 [--health-out FILE] [--status SUPERVISOR_STATUS_JSON] [--observe]\n"
                "                 [--latency-log FILE.csv]\n"
                "                 [--clip-model F] [--clip-labels F] [--clip-text F] [--clip-min-prob P]\n"
                "                 [--clip-region S] [--clip-need N] [--clip-window N] [--clip-threads N] [--clip-prio N]\n"
                "                 [--scan-status FILE]\n");
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

    /* CLIP mode: the best region worth a closer look (an animal the table knows, or something that
     * may hold an animal picture: phone, TV, laptop, book, teddy bear). Person is never chosen. */
    bool best_region(const pod::LabelTable& labels, float min_score, pod::clip::Box* b) const {
        int n = static_cast<int>(*count + 0.5f);
        if (n > max_n) n = max_n;
        for (int i = 0; i < n; ++i) {
            if (scores[i] < min_score) continue;
            const int idx = static_cast<int>(classes[i] + 0.5f);
            if (idx == 0) continue;                                   /* person */
            if (labels.class_for(idx) == pod::kDrop && !pod::clip::is_picture_holder(idx)) continue;
            const float* bx = boxes + 4 * i;
            const float y0 = std::clamp(bx[0], 0.0f, 1.0f), x0 = std::clamp(bx[1], 0.0f, 1.0f);
            const float y1 = std::clamp(bx[2], 0.0f, 1.0f), x1 = std::clamp(bx[3], 0.0f, 1.0f);
            if (x1 - x0 < 0.03f || y1 - y0 < 0.03f) continue;          /* too small to say anything */
            *b = pod::clip::Box{x0, y0, x1, y1};
            return true;
        }
        return false;
    }

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


/* ---- CLIP worker (prototype 1): identifies the animal off the detector's critical path ---- */

struct ClipWorker {
    const Options& o;
    pod::clip::Text text;
    std::vector<int> class_of_label;     /* CLIP label row -> Pod class_id (labels.csv), -1 = never an animal */
    pod::clip::Voter voter;

    std::thread th;
    std::mutex mu;
    std::condition_variable cv;
    bool have_job = false, busy = false, stop = false;
    std::atomic<bool> ready{false}, failed{false};
    std::vector<uint8_t> job;            /* YUYV frame for the next check */
    uint32_t jw = 0, jh = 0, jstride = 0;
    pod::clip::Box jbox{};

    /* results, under mu */
    int present = -1;                    /* CLIP label row of the confirmed animal, or -1 */
    pod::clip::Box present_box{};
    float present_prob = 0.0f;
    uint64_t checks = 0;
    std::vector<int64_t> check_ns;       /* CLIP run times for the stats window */
    int last_vote = -1;                  /* latest check that voted for an animal (for pod-servo) */
    int64_t last_vote_ns = 0;
    uint64_t confirmations = 0;          /* +1 every time a new animal is confirmed ("captured") */

    explicit ClipWorker(const Options& opt) : o(opt) {}
    ~ClipWorker() { shutdown(); }   /* every exit path joins the thread */

    bool init(const pod::LabelTable& labels) {
        std::string err;
        if (!text.load(o.clip_labels, o.clip_text, &err)) { std::fprintf(stderr, "pod-infer: clip: %s\n", err.c_str()); return false; }
        voter.need = o.clip_need;
        voter.window = o.clip_window;
        int animals = 0;
        for (const std::string& name : text.labels) {
            int cls = -1;
            if (name != "_none")
                for (size_t c = 1; c < labels.species.size(); ++c)
                    if (labels.species[c] == name) { cls = static_cast<int>(c); break; }
            if (name != "_none" && cls < 0)
                std::fprintf(stderr, "pod-infer: clip: '%s' is not in the label table, it will never be reported\n", name.c_str());
            if (cls >= 0) animals++;
            class_of_label.push_back(cls);
        }
        std::printf("pod-infer: CLIP %s, %d animals, vote %d of %d at p >= %.2f, region score %.2f\n",
                    o.clip_model.c_str(), animals, o.clip_need, o.clip_window,
                    static_cast<double>(o.clip_min_prob), static_cast<double>(o.clip_region));
        th = std::thread([this] { run(); });
        return true;
    }

    bool wants_frame() {
        std::lock_guard<std::mutex> l(mu);
        return ready && !have_job && !busy;
    }

    void submit(std::vector<uint8_t>& frame, uint32_t w, uint32_t h, uint32_t stride, pod::clip::Box b) {
        {
            std::lock_guard<std::mutex> l(mu);
            job.swap(frame);
            jw = w; jh = h; jstride = stride; jbox = b;
            have_job = true;
        }
        cv.notify_one();
    }

    /* The confirmed animal as a detection, or false when none is in view. */
    bool current(pod_detection_t* d) {
        std::lock_guard<std::mutex> l(mu);
        if (present < 0 || class_of_label[static_cast<size_t>(present)] < 0) return false;
        std::memset(d, 0, sizeof *d);
        d->x = present_box.x0; d->y = present_box.y0;
        d->w = present_box.x1 - present_box.x0; d->h = present_box.y1 - present_box.y0;
        d->score = present_prob;
        d->class_id = static_cast<uint16_t>(class_of_label[static_cast<size_t>(present)]);
        return true;
    }

    void shutdown() {
        { std::lock_guard<std::mutex> l(mu); stop = true; }
        cv.notify_one();
        if (th.joinable()) th.join();
    }

    void run() {
        /* Below the detector: the detector keeps its real-time priority, CLIP gets what is left. */
        if (o.clip_prio > 0) {
            sched_param sp{};
            sp.sched_priority = o.clip_prio;
            pthread_setschedparam(pthread_self(), SCHED_RR, &sp);   /* may fail off QNX; then inherited */
        }
        auto model = tflite::FlatBufferModel::BuildFromFile(o.clip_model.c_str());
        tflite::ops::builtin::BuiltinOpResolver resolver;
        std::unique_ptr<tflite::Interpreter> interp;
        if (!model) { std::fprintf(stderr, "pod-infer: clip: cannot load %s\n", o.clip_model.c_str()); failed = true; return; }
        tflite::InterpreterBuilder builder(*model, resolver);
        builder.SetNumThreads(o.clip_threads);
        if (builder(&interp) != kTfLiteOk || !interp || interp->AllocateTensors() != kTfLiteOk) {
            std::fprintf(stderr, "pod-infer: clip: interpreter build failed\n"); failed = true; return;
        }
        const TfLiteTensor* in = interp->input_tensor(0);
        const TfLiteTensor* out = interp->output_tensor(0);
        if (in->type != kTfLiteFloat32 || in->dims->size != 4 || in->dims->data[1] != pod::clip::kSize ||
            in->dims->data[2] != pod::clip::kSize || in->dims->data[3] != 3 || out->type != kTfLiteFloat32 ||
            out->dims->data[out->dims->size - 1] != pod::clip::kDim) {
            std::fprintf(stderr, "pod-infer: clip: expected float [1,224,224,3] -> [1,512]\n"); failed = true; return;
        }
        float* input = interp->typed_input_tensor<float>(0);
        ready = true;
        std::vector<uint8_t> frame;
        while (true) {
            uint32_t w, h, stride;
            pod::clip::Box b;
            {
                std::unique_lock<std::mutex> l(mu);
                cv.wait(l, [this] { return have_job || stop; });
                if (stop) return;
                frame.swap(job);
                w = jw; h = jh; stride = jstride; b = jbox;
                have_job = false; busy = true;
            }
            const int64_t t0 = mono_ns();
            pod::clip::crop_to_input(frame.data(), w, h, stride, b, input);
            if (interp->Invoke() != kTfLiteOk) { std::fprintf(stderr, "pod-infer: clip: Invoke failed\n"); failed = true; return; }
            const float* e = interp->typed_output_tensor<float>(0);
            float emb[pod::clip::kDim], norm = 0.0f;
            for (int k = 0; k < pod::clip::kDim; ++k) norm += e[k] * e[k];
            norm = std::sqrt(norm) + 1e-9f;
            for (int k = 0; k < pod::clip::kDim; ++k) emb[k] = e[k] / norm;
            float prob = 0.0f;
            const int best = pod::clip::classify(text, emb, &prob);
            const bool animal = class_of_label[static_cast<size_t>(best)] >= 0;
            const int vote = (animal && prob >= o.clip_min_prob) ? best : -1;
            const int64_t t1 = mono_ns();
            const bool changed = voter.update(vote, static_cast<double>(t1) / 1e9);
            {
                std::lock_guard<std::mutex> l(mu);
                present = voter.present;
                if (vote >= 0 && vote == voter.present) { present_box = b; present_prob = prob; }
                if (vote >= 0) { last_vote = vote; last_vote_ns = t1; }
                if (changed) confirmations++;
                checks++;
                check_ns.push_back(t1 - t0);
                busy = false;
            }
            if (changed)
                std::printf("pod-infer: CLIP: %s in view (p %.2f, %.0f ms per check)\n",
                            text.labels[static_cast<size_t>(voter.present)].c_str(), static_cast<double>(prob),
                            static_cast<double>(t1 - t0) / 1e6);
            else if (!o.quiet) {
                int leader = -1;
                const int n = voter.progress(&leader);
                std::printf("pod-infer: CLIP check: %s %.2f%s (votes %d/%d for %s)\n", text.labels[static_cast<size_t>(best)].c_str(),
                            static_cast<double>(prob), vote >= 0 ? "" : " (no vote)", n, o.clip_need,
                            leader >= 0 ? text.labels[static_cast<size_t>(leader)].c_str() : "-");
            }
            std::fflush(stdout);
        }
    }
};

/* with-motor: tell pod-servo what the camera is looking at. Rewritten atomically (tmp + rename).
 *   seeing      animal the latest checks are voting for (within 1.5 s), or null
 *   confirmed   animal currently confirmed in view, or null
 *   captures    +1 each time a new animal is confirmed (the servo resumes after it changes) */
void write_scan_status(const std::string& path, const char* seeing, const char* confirmed, uint64_t captures, int64_t now_ms) {
    const std::string tmp = path + ".tmp";
    FILE* f = std::fopen(tmp.c_str(), "w");
    if (!f) return;
    auto q = [](const char* sp) { return sp ? std::string("\"") + sp + "\"" : std::string("null"); };
    std::fprintf(f, "{\"t_ms\":%lld,\"seeing\":%s,\"confirmed\":%s,\"captures\":%llu}\n", static_cast<long long>(now_ms),
                 q(seeing).c_str(), q(confirmed).c_str(), static_cast<unsigned long long>(captures));
    std::fclose(f);
    std::rename(tmp.c_str(), path.c_str());
}

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

    std::unique_ptr<ClipWorker> clipw;
    if (!o.clip_model.empty()) {
        clipw.reset(new ClipWorker(o));
        if (!clipw->init(labels)) return 1;
    }
    std::vector<uint8_t> clip_frame;     /* full-resolution copy for the CLIP worker */
    bool clip_staged = false;
    int64_t t_scan_status = 0;
    uint64_t plain_captures = 0;         /* --scan-status without CLIP: counts new detections */
    int plain_prev_class = -1;

    pod::HealthBeat hb;
    pod::DetsSender tx(o.dets_mq.c_str());
    const bool send_dets = !o.dets_mq.empty();
    pod::HealthWriter health(o.health_out, o.status);

    /* --latency-log (P3-2): one CSV row per processed frame, for histograms (latency_report.py). */
    FILE* lat_log = nullptr;
    if (!o.latency_log.empty()) {
        lat_log = std::fopen(o.latency_log.c_str(), "w");
        if (!lat_log) { std::perror("pod-infer: --latency-log"); return 1; }
        std::fprintf(lat_log, "frame_seq,t_capture_ns,pre_us,infer_us,e2e_us,skipped,n_detections\n");
    }
    int64_t t_lat_flush = 0;
    pod::ShmRing ring;
    if (!open_ring(ring, o.shm, 50)) { std::fprintf(stderr, "pod-infer: cannot open ring %s (is pod-capture running?)\n", o.shm.c_str()); return 1; }
    std::printf("pod-infer: reading %s, sending to %s, min score %.2f, prio %d%s\n", o.shm.c_str(),
                send_dets ? o.dets_mq.c_str() : "(nothing)", static_cast<double>(o.min_score),
                o.prio, hb.attached() ? ", supervised" : "");
    if (health.enabled()) std::printf("pod-infer: health -> %s every 10 s (status from %s)\n", o.health_out.c_str(), o.status.c_str());
    std::fflush(stdout);
    const char* last_health_problem = nullptr;

    pod_frame_hdr_t obs_hdr{};
    std::vector<uint8_t> obs_buf(o.observe ? ring.ring.max_payload() : 0);
    if (o.observe) std::printf("pod-infer: OBSERVER mode: read-only frame copies, the ring's real consumer is untouched\n");
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
        const pod_frame_hdr_t* f = nullptr;
        const uint8_t* pixels = nullptr;
        int64_t t0 = 0;
        if (!o.observe) {
            if (!ring.ring.wait(500) && mono_ns() - t_last_frame > 2000000000LL) {
                /* No frames for 2 s: pod-capture may have restarted with a new ring. */
                if (open_ring(ring, o.shm, 1)) last = 0;
                t_last_frame = mono_ns();
                continue;
            }
            f = ring.ring.acquire_latest(last);
            if (!f) continue;
            t0 = mono_ns();
            if (!frame_ok(f)) { ring.ring.release(); continue; }
            pixels = ring.ring.payload(f);
        } else {
            /* Observer: copy the newest frame without claiming a slot or the pulse, so the
             * real consumer is not disturbed. No pulse to sleep on, so poll every 5 ms. */
            if (!ring.ring.peek_latest(last, &obs_hdr, obs_buf.data(), obs_buf.size())) {
                if (mono_ns() - t_last_frame > 2000000000LL) {
                    if (open_ring(ring, o.shm, 1)) { last = 0; obs_buf.resize(ring.ring.max_payload()); }
                    t_last_frame = mono_ns();
                }
                usleep(5000);
                continue;
            }
            f = &obs_hdr;
            t0 = mono_ns();
            if (!frame_ok(f)) { last = f->seq; continue; }
            pixels = obs_buf.data();
        }

        pod_detections_t r{};
        r.magic = POD_DETS_MAGIC;
        r.frame_seq = f->seq;
        r.t_capture_ns = f->t_capture_ns;
        const uint64_t skipped = (last && f->seq > last + 1) ? f->seq - last - 1 : 0;
        pod::yuyv_to_rgb_resize(pixels, f->width, f->height, f->stride, det.input, det.in_w, det.in_h);
        clip_staged = false;
        if (clipw && clipw->wants_frame()) {          /* only when the worker is free: ~0.1 ms copy */
            clip_frame.assign(pixels, pixels + static_cast<size_t>(f->stride) * f->height);
            clip_staged = true;
        }
        const uint32_t fw = f->width, fh = f->height, fstride = f->stride;
        last = f->seq;
        if (!o.observe) ring.ring.release();  /* frame copied: give the slot back before the slow part */
        const int64_t t1 = mono_ns();

        if (!det.invoke()) { std::fprintf(stderr, "pod-infer: Invoke failed\n"); return 1; }
        r.t_infer_done_ns = mono_ns();
        det.collect(labels, o.min_score, &r);
        if (clipw) {
            if (clipw->failed) { std::fprintf(stderr, "pod-infer: CLIP worker failed, stopping\n"); return 1; }
            if (clip_staged) {
                pod::clip::Box b;
                if (!det.best_region(labels, o.clip_region, &b)) b = pod::clip::centre_box();
                clipw->submit(clip_frame, fw, fh, fstride, b);
            }
            /* CLIP decides what the animal is: report only the confirmed one (or nothing). */
            r.n_detections = 0;
            if (clipw->current(&r.det[0])) r.n_detections = 1;
        }
        if (!o.scan_status.empty() && r.t_infer_done_ns - t_scan_status >= 200000000LL) {   /* 5x per second */
            t_scan_status = r.t_infer_done_ns;
            if (clipw) {
                std::string seeing, confirmed;
                uint64_t caps;
                {
                    std::lock_guard<std::mutex> l(clipw->mu);
                    if (clipw->last_vote >= 0 && mono_ns() - clipw->last_vote_ns < 1500000000LL)
                        seeing = clipw->text.labels[static_cast<size_t>(clipw->last_vote)];
                    if (clipw->present >= 0) confirmed = clipw->text.labels[static_cast<size_t>(clipw->present)];
                    caps = clipw->confirmations;
                }
                write_scan_status(o.scan_status, seeing.empty() ? nullptr : seeing.c_str(),
                                  confirmed.empty() ? nullptr : confirmed.c_str(), caps, wall_ms());
            } else {
                const int cls = r.n_detections ? r.det[0].class_id : -1;
                if (cls >= 0 && cls != plain_prev_class) plain_captures++;
                plain_prev_class = cls;
                const char* sp = cls >= 0 ? labels.name(cls) : nullptr;
                write_scan_status(o.scan_status, sp, sp, plain_captures, wall_ms());
            }
        }
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
        if (lat_log) {
            std::fprintf(lat_log, "%llu,%lld,%lld,%lld,%lld,%llu,%u\n", static_cast<unsigned long long>(r.frame_seq),
                         static_cast<long long>(r.t_capture_ns), static_cast<long long>((t1 - t0) / 1000),
                         static_cast<long long>((r.t_infer_done_ns - t1) / 1000),
                         static_cast<long long>((r.t_infer_done_ns - r.t_capture_ns) / 1000),
                         static_cast<unsigned long long>(skipped), r.n_detections);
            if (r.t_infer_done_ns - t_lat_flush > 1000000000LL) { std::fflush(lat_log); t_lat_flush = r.t_infer_done_ns; }
        }
        if (!o.quiet && r.n_detections > 0) print_dets(r, labels);

        if (o.stats > 0 && r.t_infer_done_ns - t_win >= static_cast<int64_t>(o.stats) * 1000000000LL) {
            print_stats("last", win, static_cast<double>(r.t_infer_done_ns - t_win) / 1e9);
            if (clipw) {
                std::vector<int64_t> v;
                uint64_t checks;
                { std::lock_guard<std::mutex> l(clipw->mu); v.swap(clipw->check_ns); checks = clipw->checks; }
                std::printf("pod-infer:   clip  %zu checks (%.1f/s), p50 %6.1f  p99 %6.1f ms, total %llu\n", v.size(),
                            static_cast<double>(v.size()) / (static_cast<double>(r.t_infer_done_ns - t_win) / 1e9),
                            pct_ms(v, 0.5), pct_ms(v, 0.99), static_cast<unsigned long long>(checks));
                std::fflush(stdout);
            }
            win.clear();
            t_win = r.t_infer_done_ns;
        }
    }
    print_stats("total", total, static_cast<double>(mono_ns() - t_start) / 1e9);
    if (clipw) clipw->shutdown();
    health.remove();   /* no stale health numbers after we stop */
    if (lat_log) std::fclose(lat_log);
    return 0;
}
