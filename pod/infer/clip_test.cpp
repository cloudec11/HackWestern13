/*
 * clip_test -- laptop/Pi unit tests for clip.h (no TFLite needed).
 *   clip_test                                   voting + crop sanity checks
 *   clip_test --dump in.yuyv W H x0 y0 x1 y1 out.f32
 *        crop a raw YUYV frame like pod-infer does and write the CLIP input tensor
 *        (used with a laptop script to check the whole path against the TFLite model)
 */
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <vector>

#include "clip.h"

using namespace pod::clip;

static int failures = 0;
#define CHECK(c) do { if (!(c)) { std::fprintf(stderr, "FAIL %s:%d %s\n", __FILE__, __LINE__, #c); failures++; } } while (0)

static void test_voter() {
    Voter v;                                   /* 6 of 10, release 2.5 s */
    double t = 0;
    for (int i = 0; i < 5; ++i) CHECK(!v.update(3, t += 0.3));
    CHECK(v.present == -1);                    /* 5 votes: not yet */
    CHECK(v.update(3, t += 0.3));              /* 6th vote: deer confirmed */
    CHECK(v.present == 3);
    for (int i = 0; i < 5; ++i) CHECK(!v.update(3, t += 0.3));   /* same animal: no new visit */
    for (int i = 0; i < 4; ++i) v.update(-1, t += 0.3);          /* 1.2 s of nothing: still present */
    CHECK(v.present == 3);
    for (int i = 0; i < 6; ++i) v.update(-1, t += 0.3);          /* gone > 2.5 s */
    CHECK(v.present == -1);
    Voter w;                                   /* flicker never confirms */
    for (int i = 0; i < 20; ++i) w.update(i % 2 ? 4 : 5, i * 0.3);
    CHECK(w.present == -1);
    Voter x;                                   /* a different animal replaces the first */
    for (int i = 0; i < 6; ++i) x.update(1, i * 0.3);
    CHECK(x.present == 1);
    bool changed = false;
    for (int i = 0; i < 6; ++i) changed |= x.update(2, 2 + i * 0.3);
    CHECK(changed && x.present == 2);
}

static void test_crop() {
    /* flat mid-grey frame (Y=126, U=V=128) -> every input value equals the normalised grey */
    const uint32_t w = 64, h = 48, stride = w * 2;
    std::vector<uint8_t> f(stride * h);
    for (size_t i = 0; i < f.size(); i += 4) { f[i] = 126; f[i + 1] = 128; f[i + 2] = 126; f[i + 3] = 128; }
    std::vector<float> in(kSize * kSize * 3);
    crop_to_input(f.data(), w, h, stride, Box{0.2f, 0.2f, 0.6f, 0.9f}, in.data());
    uint8_t rgb[3];
    pod::yuv_to_rgb(126, 128, 128, rgb);
    for (int k = 0; k < 3; ++k) {
        const float want = (rgb[k] / 255.0f - kMean[k]) / kStd[k];
        CHECK(std::fabs(in[k] - want) < 1e-4f);
        CHECK(std::fabs(in[in.size() - 3 + k] - want) < 1e-4f);   /* edges repeat outside the frame */
    }
}

int main(int argc, char** argv) {
    if (argc == 10 && std::strcmp(argv[1], "--dump") == 0) {
        const uint32_t w = std::atoi(argv[3]), h = std::atoi(argv[4]);
        std::vector<uint8_t> f(w * h * 2);
        std::ifstream(argv[2], std::ios::binary).read(reinterpret_cast<char*>(f.data()), f.size());
        std::vector<float> in(kSize * kSize * 3);
        crop_to_input(f.data(), w, h, w * 2, Box{(float)std::atof(argv[5]), (float)std::atof(argv[6]),
                      (float)std::atof(argv[7]), (float)std::atof(argv[8])}, in.data());
        std::ofstream(argv[9], std::ios::binary).write(reinterpret_cast<const char*>(in.data()), in.size() * sizeof(float));
        return 0;
    }
    test_voter();
    test_crop();
    if (failures) { std::fprintf(stderr, "clip_test: %d failed\n", failures); return 1; }
    std::printf("clip_test: all passed\n");
    return 0;
}
