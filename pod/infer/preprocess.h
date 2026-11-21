/*
 * preprocess.h -- camera frame -> model input, no OpenCV. Owner: agent-2 (P1-2).
 * Header-only C++17, no TFLite dependency, so it can be unit-tested anywhere.
 *
 * The frame is resized straight into the model's input buffer (RGB, uint8, HWC) with
 * nearest-neighbour sampling. The aspect ratio is not kept (640x480 -> 320x320
 * stretches), so normalised model boxes map straight back onto the frame.
 */
#ifndef POD_INFER_PREPROCESS_H
#define POD_INFER_PREPROCESS_H

#include <cstdint>
#include <vector>

namespace pod {

inline uint8_t clamp_u8(int v) { return static_cast<uint8_t>(v < 0 ? 0 : (v > 255 ? 255 : v)); }

/* BT.601 limited range (what UVC webcams send) -> RGB, integer maths. */
inline void yuv_to_rgb(int y, int u, int v, uint8_t* rgb) {
    const int c = 298 * (y - 16);
    const int d = u - 128;
    const int e = v - 128;
    rgb[0] = clamp_u8((c + 409 * e + 128) >> 8);
    rgb[1] = clamp_u8((c - 100 * d - 208 * e + 128) >> 8);
    rgb[2] = clamp_u8((c + 516 * d + 128) >> 8);
}

/* Source column/row for each destination column/row (centre sampling). */
inline std::vector<uint32_t> sample_map(uint32_t src, uint32_t dst) {
    std::vector<uint32_t> m(dst);
    for (uint32_t i = 0; i < dst; ++i) {
        uint32_t s = static_cast<uint32_t>((2ull * i + 1) * src / (2ull * dst));
        m[i] = s < src ? s : src - 1;
    }
    return m;
}

/* YUYV 4:2:2 (Y0 U Y1 V per pixel pair) -> RGB24 dst_w x dst_h. stride = bytes per source row. */
inline void yuyv_to_rgb_resize(const uint8_t* src, uint32_t src_w, uint32_t src_h, uint32_t stride,
                               uint8_t* dst, uint32_t dst_w, uint32_t dst_h) {
    const std::vector<uint32_t> xm = sample_map(src_w, dst_w);
    const std::vector<uint32_t> ym = sample_map(src_h, dst_h);
    for (uint32_t oy = 0; oy < dst_h; ++oy) {
        const uint8_t* row = src + static_cast<size_t>(ym[oy]) * stride;
        uint8_t* out = dst + static_cast<size_t>(oy) * dst_w * 3;
        for (uint32_t ox = 0; ox < dst_w; ++ox) {
            const uint32_t sx = xm[ox];
            const uint8_t* pair = row + (sx >> 1) * 4;     /* Y0 U Y1 V */
            const int y = (sx & 1) ? pair[2] : pair[0];
            yuv_to_rgb(y, pair[1], pair[3], out + ox * 3);
        }
    }
}

/* RGB24 -> RGB24 resize (used for still images in --ppm mode). */
inline void rgb_resize(const uint8_t* src, uint32_t src_w, uint32_t src_h,
                       uint8_t* dst, uint32_t dst_w, uint32_t dst_h) {
    const std::vector<uint32_t> xm = sample_map(src_w, dst_w);
    const std::vector<uint32_t> ym = sample_map(src_h, dst_h);
    for (uint32_t oy = 0; oy < dst_h; ++oy) {
        const uint8_t* row = src + static_cast<size_t>(ym[oy]) * src_w * 3;
        uint8_t* out = dst + static_cast<size_t>(oy) * dst_w * 3;
        for (uint32_t ox = 0; ox < dst_w; ++ox) {
            const uint8_t* p = row + static_cast<size_t>(xm[ox]) * 3;
            out[ox * 3 + 0] = p[0];
            out[ox * 3 + 1] = p[1];
            out[ox * 3 + 2] = p[2];
        }
    }
}

}  // namespace pod

#endif /* POD_INFER_PREPROCESS_H */
