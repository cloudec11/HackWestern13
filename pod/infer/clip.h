/*
 * clip.h -- animal identification for pod-infer with CLIP (prototype 1, agent-4 for agent-2's lane).
 * Header-only C++17, no TFLite dependency (unit-tested on the laptop by clip_test.cpp).
 *
 * Why: the COCO detector only knows 10 animals (a deer comes out as "cat") and shrinks the whole
 * 640x480 frame to 320x320, so a phone screen or plushie is tiny. The laptop scanner fixed this by
 *   1. using the detector only to find WHERE to look (an animal, a teddy bear, a phone/TV screen,
 *      a book), else the middle of the view,
 *   2. cutting that region out of the FULL-resolution frame and asking CLIP WHAT it is, against
 *      the animal list in models/clip/labels.txt (text embeddings precomputed on the laptop),
 *   3. accepting an animal only when it wins most of the recent checks (6 of 10), so it can be
 *      sensitive without single-frame false alarms.
 * This file holds steps 2's maths and step 3. The CLIP image model is models/clip_vision.tflite
 * (built by models/clip/make_clip_tflite.py, builtin TFLite ops only).
 */
#ifndef POD_INFER_CLIP_H
#define POD_INFER_CLIP_H

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <deque>
#include <fstream>
#include <map>
#include <string>
#include <vector>

#include "preprocess.h"   /* pod::yuv_to_rgb (BT.601 limited range) */

namespace pod {
namespace clip {

constexpr int kSize = 224;          /* CLIP input is 224x224 RGB */
constexpr int kDim = 512;           /* embedding length */
constexpr float kMean[3] = {0.48145466f, 0.4578275f, 0.40821073f};
constexpr float kStd[3] = {0.26862954f, 0.26130258f, 0.27577711f};

/* COCO indices (as output by the detector) for things that may hold an animal picture. */
inline bool is_picture_holder(int model_index) {
    return model_index == 71 /* tv */ || model_index == 72 /* laptop */ || model_index == 76 /* cell phone */ ||
           model_index == 83 /* book */ || model_index == 87 /* teddy bear */;
}

struct Box { float x0, y0, x1, y1; };   /* normalised 0..1 */

/* The middle of the view, used when the detector finds nothing worth a closer look. */
inline Box centre_box() { return Box{0.15f, 0.10f, 0.85f, 0.90f}; }

/* ---- labels + text embeddings (models/clip/labels.txt, models/clip/text.f32) ---- */

struct Text {
    std::vector<std::string> labels;   /* "_none" rows mean "not an animal" */
    std::vector<float> emb;            /* labels.size() x kDim, L2-normalised */

    bool load(const std::string& labels_path, const std::string& text_path, std::string* err) {
        std::ifstream lf(labels_path);
        if (!lf) { *err = "cannot open " + labels_path; return false; }
        std::string line;
        labels.clear();
        while (std::getline(lf, line)) {
            if (!line.empty() && line.back() == '\r') line.pop_back();
            if (line.empty()) continue;
            if (line == "person") { *err = "person is not allowed in " + labels_path; return false; }
            labels.push_back(line);
        }
        std::ifstream tf(text_path, std::ios::binary);
        if (!tf) { *err = "cannot open " + text_path; return false; }
        emb.assign(labels.size() * kDim, 0.0f);
        tf.read(reinterpret_cast<char*>(emb.data()), static_cast<std::streamsize>(emb.size() * sizeof(float)));
        if (!tf || labels.empty()) { *err = text_path + " does not match " + labels_path; return false; }
        return true;
    }
};

/* Softmax over 100 * cosine similarity (CLIP's logit scale). image must be L2-normalised.
 * Returns the best row; *prob gets its probability. */
inline int classify(const Text& t, const float* image, float* prob, std::vector<float>* probs = nullptr) {
    const size_t n = t.labels.size();
    std::vector<float> p(n);
    float mx = -1e30f;
    for (size_t i = 0; i < n; ++i) {
        const float* e = &t.emb[i * kDim];
        float s = 0.0f;
        for (int k = 0; k < kDim; ++k) s += e[k] * image[k];
        p[i] = 100.0f * s;
        mx = std::max(mx, p[i]);
    }
    float sum = 0.0f;
    for (float& v : p) { v = std::exp(v - mx); sum += v; }
    size_t best = 0;
    for (size_t i = 0; i < n; ++i) { p[i] /= sum; if (p[i] > p[best]) best = i; }
    *prob = p[best];
    if (probs) *probs = p;
    return static_cast<int>(best);
}

/* ---- crop: square region of a YUYV frame -> CLIP input (NHWC float, normalised) ----
 * The box is padded by `pad` on each side and made square around its centre (the long side),
 * sampled bilinearly from the full-resolution frame. Outside the frame, edge pixels repeat. */
inline void crop_to_input(const uint8_t* yuyv, uint32_t w, uint32_t h, uint32_t stride, Box b, float* out,
                          float pad = 0.12f) {
    const float bw = (b.x1 - b.x0) * static_cast<float>(w), bh = (b.y1 - b.y0) * static_cast<float>(h);
    const float cx = (b.x0 + b.x1) * 0.5f * static_cast<float>(w), cy = (b.y0 + b.y1) * 0.5f * static_cast<float>(h);
    const float side = std::max(8.0f, std::max(bw, bh) * (1.0f + 2.0f * pad));
    const float step = side / kSize, ox = cx - side * 0.5f, oy = cy - side * 0.5f;
    auto rgb_at = [&](int x, int y, float* c) {
        x = std::clamp(x, 0, static_cast<int>(w) - 1);
        y = std::clamp(y, 0, static_cast<int>(h) - 1);
        const uint8_t* row = yuyv + static_cast<size_t>(y) * stride;
        const uint8_t* pair = row + static_cast<size_t>(x / 2) * 4;   /* Y0 U Y1 V */
        uint8_t px[3];
        yuv_to_rgb(pair[(x & 1) ? 2 : 0], pair[1], pair[3], px);
        c[0] = px[0]; c[1] = px[1]; c[2] = px[2];
    };
    for (int j = 0; j < kSize; ++j) {
        const float sy = oy + (static_cast<float>(j) + 0.5f) * step - 0.5f;
        const int y0 = static_cast<int>(std::floor(sy));
        const float fy = sy - static_cast<float>(y0);
        for (int i = 0; i < kSize; ++i) {
            const float sx = ox + (static_cast<float>(i) + 0.5f) * step - 0.5f;
            const int x0 = static_cast<int>(std::floor(sx));
            const float fx = sx - static_cast<float>(x0);
            float a[3], bb[3], c[3], d[3];
            rgb_at(x0, y0, a); rgb_at(x0 + 1, y0, bb); rgb_at(x0, y0 + 1, c); rgb_at(x0 + 1, y0 + 1, d);
            float* o = out + (static_cast<size_t>(j) * kSize + static_cast<size_t>(i)) * 3;
            for (int k = 0; k < 3; ++k) {
                const float v = (a[k] * (1 - fx) + bb[k] * fx) * (1 - fy) + (c[k] * (1 - fx) + d[k] * fx) * fy;
                o[k] = (v / 255.0f - kMean[k]) / kStd[k];
            }
        }
    }
}

/* ---- voting: accept an animal only when it wins most recent checks ----
 * update() gets each check's best label (or -1 for "not an animal / not sure").
 * An animal becomes `present` after winning `need` of the last `window` checks; it stays
 * present while it keeps being seen, and is dropped after `release_s` without a vote. */
struct Voter {
    int window = 10, need = 6;
    double release_s = 2.5;
    std::deque<int> votes;
    int present = -1;                    /* label index of the animal in view, or -1 */
    double last_seen_present = 0.0;

    /* Returns true when `present` changed to a new animal (a new visit starts). */
    bool update(int label, double now_s) {
        votes.push_back(label);
        while (static_cast<int>(votes.size()) > window) votes.pop_front();
        if (present >= 0 && label == present) last_seen_present = now_s;
        if (present >= 0 && now_s - last_seen_present > release_s) present = -1;   /* it left */
        std::map<int, int> count;
        for (int v : votes) if (v >= 0) count[v]++;
        int leader = -1, n = 0;
        for (const auto& kv : count) if (kv.second > n) { leader = kv.first; n = kv.second; }
        if (leader >= 0 && n >= need && leader != present) {
            present = leader;
            last_seen_present = now_s;
            votes.clear();
            return true;
        }
        return false;
    }
    int progress(int* leader) const {    /* for logs: current leader and its vote count */
        std::map<int, int> count;
        for (int v : votes) if (v >= 0) count[v]++;
        int n = 0; *leader = -1;
        for (const auto& kv : count) if (kv.second > n) { *leader = kv.first; n = kv.second; }
        return n;
    }
};

}  // namespace clip
}  // namespace pod

#endif
