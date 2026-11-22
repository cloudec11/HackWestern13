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

#include "heartbeat.h"
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

static bool file_exists(const char* p) { std::ifstream f(p); return f.is_open(); }
static std::string slurp(const char* p) { std::ifstream f(p); std::string s, l; while (std::getline(f, l)) s += l; return s; }
static void put(const char* p, const char* s) { std::ofstream f(p); f << s; }

static void test_latency_window() {
    pod::LatencyWindow w(60000000000LL);
    for (int i = 1; i <= 100; ++i) w.add(i * 100000000LL, i * 1000000LL, i * 2000000LL);   /* 1..100 ms */
    CHECK(w.size() == 100);
    CHECK(near(static_cast<int>(w.infer_ms(0.50)), 51, 1));
    CHECK(static_cast<int>(w.infer_ms(0.99)) == 99);
    CHECK(static_cast<int>(w.e2e_ms(1.0)) == 200);
    w.trim(100000000LL * 100 + 60000000000LL - 450000000LL);       /* keeps t >= 9.55 s: the last 5 */
    CHECK(w.size() == 5);
    w.trim(1LL << 50);
    CHECK(w.size() == 0 && w.infer_ms(0.5) == 0.0);
}

static void test_parse_status() {
    pod::SupervisorStatus s = pod::parse_status(
        "{\"t_wall_ms\":1795300000000,\"temp_c\":52.3,\"restarts\":2,\"children\":[{\"name\":\"capture\",\"restarts\":2}]}");
    CHECK(s.found && s.t_wall_ms == 1795300000000LL);
    CHECK(s.temp_c > 52.2 && s.temp_c < 52.4);
    CHECK(s.restarts == 2);                                          /* top-level, not the child's */
    s = pod::parse_status("{\"t_wall_ms\":5,\"temp_c\":null,\"restarts\":0,\"children\":[]}");
    CHECK(s.found && std::isnan(s.temp_c) && s.restarts == 0);
    s = pod::parse_status("garbage");
    CHECK(!s.found && std::isnan(s.temp_c));
}

static void test_health_writer() {
    const char* out = "infer_test_heartbeat.json";
    const char* status = "infer_test_status.json";
    const char* thermal = "infer_test_thermal";
    const int64_t now_wall = 1795300000000LL;
    std::remove(out); std::remove(thermal);

    pod::HealthWriter h(out, status, 10000000000LL);
    h.set_thermal_path(thermal);
    /* first call only starts the clock; one period later with no frames: no file */
    CHECK(h.maybe_write(0, now_wall) == nullptr);
    CHECK(h.maybe_write(1000000000LL, now_wall) == nullptr);
    const char* r0 = h.maybe_write(10000000000LL, now_wall);
    CHECK(r0 && std::string(r0) == "no frames in the last 60 s" && !file_exists(out));

    for (int i = 0; i < 50; ++i) h.add(2000000000LL + i * 66000000LL, 67000000LL, 90000000LL);
    put(status, "{\"t_wall_ms\":1795300000000,\"temp_c\":48.5,\"restarts\":1,\"children\":[]}");
    CHECK(h.maybe_write(15000000000LL, now_wall) == nullptr);       /* < 10 s since the last try */
    const char* r = h.maybe_write(21000000000LL, now_wall + 1000);
    CHECK(r && !*r && file_exists(out));
    const std::string j = slurp(out);
    CHECK(j == "{\"infer_p50_ms\":67.00,\"infer_p99_ms\":67.00,\"e2e_p50_ms\":90.00,\"e2e_p99_ms\":90.00,"
               "\"temp_c\":48.5,\"disk_free_mb\":" + std::to_string(std::atol(j.substr(j.find("disk_free_mb\":") + 14).c_str())) +
               ",\"restarts\":1}");

    /* supervisor has no temperature and there is no thermal device: file removed */
    put(status, "{\"t_wall_ms\":1795300000000,\"temp_c\":null,\"restarts\":1,\"children\":[]}");
    r = h.maybe_write(31000000000LL, now_wall + 2000);
    CHECK(r && std::string(r) == "no temperature reading" && !file_exists(out));
    /* ... but a thermal device reading is used instead */
    put(thermal, "51.0\n");
    r = h.maybe_write(41000000000LL, now_wall + 3000);
    CHECK(r && !*r && slurp(out).find("\"temp_c\":51.0") != std::string::npos);
    /* stale status.json (supervisor gone): thermal temp, restarts 0 */
    put(status, "{\"t_wall_ms\":1795200000000,\"temp_c\":40.0,\"restarts\":7,\"children\":[]}");
    r = h.maybe_write(51000000000LL, now_wall + 4000);
    CHECK(r && !*r && slurp(out).find("\"restarts\":0") != std::string::npos &&
          slurp(out).find("\"temp_c\":51.0") != std::string::npos);
    /* frames stop for > 60 s: file removed */
    r = h.maybe_write(130000000000LL, now_wall + 90000);
    CHECK(r && std::string(r) == "no frames in the last 60 s" && !file_exists(out));
    /* remove() on exit */
    h.add(131000000000LL, 1, 1);
    h.maybe_write(141000000000LL, now_wall + 100000);
    CHECK(file_exists(out));
    h.remove();
    CHECK(!file_exists(out));
    /* disabled writer never touches anything */
    pod::HealthWriter off("", status);
    off.add(1, 1, 1);
    CHECK(off.maybe_write(1LL << 40, now_wall) == nullptr);

    std::remove(status); std::remove(thermal);
}

int main(int argc, char** argv) {
    const std::string labels = argc > 1 ? argv[1] : "models/labels.csv";
    test_latency_window();
    test_parse_status();
    test_health_writer();
    test_yuv();
    test_sample_map();
    test_yuyv_resize();
    test_labels(labels);
    std::printf(g_fail ? "infer_test: %d FAILED\n" : "infer_test: all passed\n", g_fail);
    return g_fail ? 1 : 0;
}
