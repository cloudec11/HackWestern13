/*
 * infer_test -- unit tests for pod-infer's preprocessing and label table. Owner: agent-2 (P1-2).
 * No TFLite needed, so it builds and runs on the laptop as well as the Pi.
 *
 *   infer_test [path/to/models/labels.csv]
 */
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <string>
#include <vector>

#include "labels.h"
#include "preprocess.h"

static int g_fail = 0;
#define CHECK(c) do { if (!(c)) { std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #c); ++g_fail; } } while (0)

static bool near(int a, int b, int tol = 2) { return a - b <= tol && b - a <= tol; }

static void test_yuv() {
    uint8_t p[3];
    pod::yuv_to_rgb(16, 128, 128, p);   CHECK(p[0] == 0 && p[1] == 0 && p[2] == 0);           /* black */
    pod::yuv_to_rgb(235, 128, 128, p);  CHECK(p[0] == 255 && p[1] == 255 && p[2] == 255);     /* white */
    pod::yuv_to_rgb(81, 90, 240, p);    CHECK(near(p[0], 255) && near(p[1], 0) && near(p[2], 0)); /* BT.601 red */
    pod::yuv_to_rgb(145, 54, 34, p);    CHECK(near(p[0], 0) && near(p[1], 255) && near(p[2], 0)); /* BT.601 green */
    pod::yuv_to_rgb(41, 240, 110, p);   CHECK(near(p[0], 0) && near(p[1], 0) && near(p[2], 255)); /* BT.601 blue */
}

static void test_sample_map() {
    std::vector<uint32_t> m = pod::sample_map(640, 320);
    CHECK(m.size() == 320 && m[0] == 1 && m[319] == 639);
    m = pod::sample_map(480, 320);
    CHECK(m[0] == 0 && m[319] == 479);
    for (size_t i = 1; i < m.size(); ++i) CHECK(m[i] > m[i - 1]);
    m = pod::sample_map(100, 300);      /* upscale stays in range */
    CHECK(m[0] == 0 && m[299] == 99);
}

static void test_yuyv_resize() {
    /* 4x2 YUYV image: left half red, right half blue. Downscale to 2x1. */
    const uint32_t w = 4, h = 2, stride = w * 2;
    std::vector<uint8_t> src(stride * h);
    for (uint32_t y = 0; y < h; ++y) {
        uint8_t* r = src.data() + y * stride;
        r[0] = 81; r[1] = 90;  r[2] = 81; r[3] = 240;   /* red pair */
        r[4] = 41; r[5] = 240; r[6] = 41; r[7] = 110;   /* blue pair */
    }
    uint8_t out[2 * 1 * 3];
    pod::yuyv_to_rgb_resize(src.data(), w, h, stride, out, 2, 1);
    CHECK(near(out[0], 255) && near(out[2], 0));    /* left = red */
    CHECK(near(out[3], 0) && near(out[5], 255));    /* right = blue */
}

static void test_labels(const std::string& real) {
    pod::LabelTable t;
    std::string err;
    CHECK(pod::load_labels(real, &t, &err));
    if (!err.empty()) std::printf("  labels: %s\n", err.c_str());
    CHECK(t.class_for(0) == pod::kDrop);            /* person: dropped */
    CHECK(t.class_for(15) == 1 && std::string(t.name(1)) == "bird");
    CHECK(t.class_for(24) == 10 && std::string(t.name(10)) == "giraffe");
    CHECK(t.class_for(87) == 11 && std::string(t.name(11)) == "teddy_bear");
    CHECK(t.class_for(2) == pod::kDrop);            /* car: dropped */
    CHECK(t.class_for(-1) == pod::kDrop && t.class_for(9999) == pod::kDrop);
    CHECK(std::string(t.name(0)) == "unknown");

    const char* bad = "infer_test_person.csv";
    { std::ofstream f(bad); f << "class_id,species,model_index,demo_only\r\n1,bird,15,0\r\n2,person,0,0\r\n"; }
    pod::LabelTable t2;
    CHECK(!pod::load_labels(bad, &t2, &err));       /* person row is refused */
    std::remove(bad);
}

int main(int argc, char** argv) {
    const std::string labels = argc > 1 ? argv[1] : "models/labels.csv";
    test_yuv();
    test_sample_map();
    test_yuyv_resize();
    test_labels(labels);
    std::printf(g_fail ? "infer_test: %d FAILED\n" : "infer_test: all passed\n", g_fail);
    return g_fail ? 1 : 0;
}
