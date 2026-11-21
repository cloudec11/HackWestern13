/*
 * track_test -- unit tests for the IoU tracker. Owner: agent-2 (P2-1).
 * No OS dependencies: builds and runs on the laptop as well as the Pi.
 */
#include <cstdio>
#include <vector>

#include "tracker.h"

static int g_fail = 0;
#define CHECK(c) do { if (!(c)) { std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #c); ++g_fail; } } while (0)

constexpr int64_t kFrame = 66000000;   /* ~15 fps */

struct Box { float x, y, w, h; uint16_t cls; float score; };

static pod_detections_t frame(int64_t t, std::vector<Box> boxes) {
    pod_detections_t r{};
    r.magic = POD_DETS_MAGIC;
    r.t_capture_ns = t;
    for (const Box& b : boxes) {
        pod_detection_t& d = r.det[r.n_detections++];
        d.x = b.x; d.y = b.y; d.w = b.w; d.h = b.h; d.class_id = b.cls; d.score = b.score;
    }
    return r;
}

/* Run frames 0..n-1 with boxes from f(i), then enough empty frames to end every track. */
template <class F>
static std::vector<pod::Visit> run(int n, F f, pod::TrackerConfig cfg = {}) {
    pod::Tracker t(cfg);
    std::vector<pod::Visit> out;
    int64_t ts = 1000000000LL;
    for (int i = 0; i < n; ++i, ts += kFrame) {
        pod_detections_t r = frame(ts, f(i));
        t.update(r, &out);
    }
    for (int i = 0; i < 40; ++i, ts += kFrame) {
        pod_detections_t r = frame(ts, {});
        t.update(r, &out);
    }
    CHECK(t.active() == 0);
    return out;
}

static void test_one_animal_walking() {
    /* A cat walks left to right for 30 frames: one visit, 30 frames. */
    auto v = run(30, [](int i) { return std::vector<Box>{{0.1f + 0.01f * i, 0.3f, 0.2f, 0.2f, 2, 0.5f + 0.01f * i}}; });
    CHECK(v.size() == 1);
    if (v.size() == 1) {
        CHECK(v[0].class_id == 2);
        CHECK(v[0].n_frames == 30);
        CHECK(v[0].max_score > 0.78f && v[0].max_score < 0.80f);
        CHECK(v[0].t_end_ns - v[0].t_start_ns == 29 * kFrame);
    }
}

static void test_two_animals() {
    /* A bird top-left and a dog bottom-right at the same time: two visits. */
    auto v = run(20, [](int) {
        return std::vector<Box>{{0.05f, 0.05f, 0.15f, 0.15f, 1, 0.7f}, {0.6f, 0.6f, 0.3f, 0.3f, 3, 0.8f}};
    });
    CHECK(v.size() == 2);
    if (v.size() == 2) {
        CHECK(v[0].track_id != v[1].track_id);
        CHECK((v[0].class_id == 1 && v[1].class_id == 3) || (v[0].class_id == 3 && v[1].class_id == 1));
        CHECK(v[0].n_frames == 20 && v[1].n_frames == 20);
    }
}

static void test_flicker_ignored() {
    /* One-frame and two-frame false positives are not visits (min_frames = 3). */
    auto v = run(10, [](int i) {
        if (i == 2) return std::vector<Box>{{0.4f, 0.4f, 0.1f, 0.1f, 3, 0.45f}};
        if (i == 6 || i == 7) return std::vector<Box>{{0.7f, 0.1f, 0.1f, 0.1f, 1, 0.45f}};
        return std::vector<Box>{};
    });
    CHECK(v.empty());
}

static void test_short_gap_same_visit() {
    /* Missed for 1 s in the middle (occlusion): still one visit. */
    auto v = run(45, [](int i) {
        if (i >= 15 && i < 30) return std::vector<Box>{};           /* 15 frames ~ 1 s gap */
        return std::vector<Box>{{0.3f, 0.3f, 0.3f, 0.3f, 5, 0.6f}};
    });
    CHECK(v.size() == 1);
    if (v.size() == 1) CHECK(v[0].n_frames == 30);
}

static void test_long_gap_two_visits() {
    /* Gone for 3 s (> 2 s gap): two visits. */
    auto v = run(80, [](int i) {
        if (i >= 15 && i < 60) return std::vector<Box>{};           /* 45 frames ~ 3 s */
        return std::vector<Box>{{0.3f, 0.3f, 0.3f, 0.3f, 5, 0.6f}};
    });
    CHECK(v.size() == 2);
}

static void test_class_vote() {
    /* Model flickers cat/dog on one animal: one visit, majority class wins. */
    auto v = run(20, [](int i) {
        const uint16_t cls = (i % 4 == 0) ? 3 : 2;                  /* 15 x cat(2), 5 x dog(3) */
        return std::vector<Box>{{0.2f, 0.2f, 0.4f, 0.4f, cls, 0.6f}};
    });
    CHECK(v.size() == 1);
    if (v.size() == 1) CHECK(v[0].class_id == 2);
}

static void test_track_ids_written() {
    pod::Tracker t;
    std::vector<pod::Visit> out;
    pod_detections_t a = frame(0, {{0.1f, 0.1f, 0.2f, 0.2f, 1, 0.9f}, {0.6f, 0.6f, 0.2f, 0.2f, 1, 0.9f}});
    t.update(a, &out);
    pod_detections_t b = frame(kFrame, {{0.61f, 0.6f, 0.2f, 0.2f, 1, 0.9f}, {0.11f, 0.1f, 0.2f, 0.2f, 1, 0.9f}});
    t.update(b, &out);
    CHECK(a.det[0].track_id != 0 && a.det[1].track_id != 0);
    CHECK(b.det[0].track_id == a.det[1].track_id);                 /* order swapped, ids follow the box */
    CHECK(b.det[1].track_id == a.det[0].track_id);
}

static void test_max_visit_split() {
    /* 10 s stay with max_visit = 4 s: split into 3 visits (4 + 4 + 2 s). */
    pod::TrackerConfig cfg;
    cfg.max_visit_ns = 4000000000LL;
    auto v = run(152, [](int) { return std::vector<Box>{{0.3f, 0.3f, 0.3f, 0.3f, 11, 0.6f}}; }, cfg);
    CHECK(v.size() == 3);
    auto v0 = run(152, [](int) { return std::vector<Box>{{0.3f, 0.3f, 0.3f, 0.3f, 11, 0.6f}}; });
    CHECK(v0.size() == 1);                                          /* default: no split */
}

static void test_flush() {
    pod::Tracker t;
    std::vector<pod::Visit> out;
    for (int i = 0; i < 5; ++i) {
        pod_detections_t r = frame(i * kFrame, {{0.3f, 0.3f, 0.3f, 0.3f, 4, 0.6f}});
        t.update(r, &out);
    }
    CHECK(out.empty() && t.active() == 1);
    t.flush(&out);
    CHECK(out.size() == 1 && t.active() == 0);
}

int main() {
    test_one_animal_walking();
    test_two_animals();
    test_flicker_ignored();
    test_short_gap_same_visit();
    test_long_gap_two_visits();
    test_class_vote();
    test_track_ids_written();
    test_max_visit_split();
    test_flush();
    std::printf(g_fail ? "track_test: %d FAILED\n" : "track_test: all passed\n", g_fail);
    return g_fail ? 1 : 0;
}
