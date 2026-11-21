/*
 * tracker.h -- IoU tracker: per-frame detections -> one visit per animal. Owner: agent-2 (P2-1).
 * Header-only C++17, no OS or TFLite dependency, so it is unit-tested on the laptop.
 *
 * Each frame:
 *  1. End tracks not seen for more than gap_ns (by frame capture time).
 *  2. Match detections to tracks greedily by IoU (best pairs first, IoU >= iou_min).
 *     Class is ignored when matching: the model flickers between similar classes
 *     (cat/dog) and that must not split one visit into two.
 *  3. Unmatched detections start new tracks.
 * A finished track becomes a Visit only if it was seen in >= min_frames frames (filters
 * one-frame false positives). Its class is the score-weighted vote over all its frames.
 * Optional max_visit_ns > 0 splits very long visits (off by default: splitting one stay
 * into several events would inflate encounter counts for the density estimate).
 */
#ifndef POD_TRACKER_H
#define POD_TRACKER_H

#include <algorithm>
#include <array>
#include <cstdint>
#include <vector>

#include "pod_msgs.h"

namespace pod {

struct TrackerConfig {
    float iou_min = 0.3f;
    int64_t gap_ns = 2000000000LL;     /* end a track after 2 s unseen */
    uint32_t min_frames = 3;
    int64_t max_visit_ns = 0;          /* 0 = never split */
};

struct Visit {
    uint32_t track_id = 0;
    uint16_t class_id = 0;
    int64_t t_start_ns = 0;            /* CLOCK_MONOTONIC capture time of the first frame */
    int64_t t_end_ns = 0;              /* ... of the last frame */
    uint32_t n_frames = 0;
    float max_score = 0.0f;
};

inline float iou(const pod_detection_t& a, float bx, float by, float bw, float bh) {
    const float x0 = std::max(a.x, bx), y0 = std::max(a.y, by);
    const float x1 = std::min(a.x + a.w, bx + bw), y1 = std::min(a.y + a.h, by + bh);
    const float inter = std::max(0.0f, x1 - x0) * std::max(0.0f, y1 - y0);
    const float uni = a.w * a.h + bw * bh - inter;
    return uni > 0.0f ? inter / uni : 0.0f;
}

class Tracker {
public:
    static constexpr int kMaxClass = 64;

    explicit Tracker(TrackerConfig c = {}) : cfg_(c) {}

    /* Feed one frame. Sets det[i].track_id in r. Appends finished visits to *out. */
    void update(pod_detections_t& r, std::vector<Visit>* out) {
        const int64_t t = r.t_capture_ns;
        expire(t, out);

        struct Pair { float iou; size_t trk; uint16_t det; };
        std::vector<Pair> pairs;
        for (size_t k = 0; k < tracks_.size(); ++k)
            for (uint16_t i = 0; i < r.n_detections; ++i) {
                const Track& tr = tracks_[k];
                const float v = iou(r.det[i], tr.x, tr.y, tr.w, tr.h);
                if (v >= cfg_.iou_min) pairs.push_back({v, k, i});
            }
        std::sort(pairs.begin(), pairs.end(), [](const Pair& a, const Pair& b) { return a.iou > b.iou; });

        std::vector<bool> trk_used(tracks_.size(), false), det_used(r.n_detections, false);
        for (const Pair& p : pairs) {
            if (trk_used[p.trk] || det_used[p.det]) continue;
            trk_used[p.trk] = det_used[p.det] = true;
            add(tracks_[p.trk], r.det[p.det], t);
        }
        for (uint16_t i = 0; i < r.n_detections; ++i) {
            if (det_used[i]) continue;
            Track tr;
            tr.id = next_id_++;
            tr.t_first_ns = t;
            add(tr, r.det[i], t);
            tracks_.push_back(tr);
        }

        if (cfg_.max_visit_ns > 0) {
            for (Track& tr : tracks_) {
                if (tr.t_last_ns - tr.t_first_ns < cfg_.max_visit_ns) continue;
                finish(tr, out);
                const Track keep = tr;
                tr = Track{};
                tr.id = next_id_++;
                tr.x = keep.x; tr.y = keep.y; tr.w = keep.w; tr.h = keep.h;
                tr.t_first_ns = tr.t_last_ns = keep.t_last_ns;
            }
        }
    }

    /* End tracks not seen since now_ns - gap_ns (call when no frames arrive). */
    void expire(int64_t now_ns, std::vector<Visit>* out) {
        for (size_t k = 0; k < tracks_.size();) {
            if (now_ns - tracks_[k].t_last_ns > cfg_.gap_ns) {
                finish(tracks_[k], out);
                tracks_.erase(tracks_.begin() + static_cast<long>(k));
            } else {
                ++k;
            }
        }
    }

    /* End every track now (shutdown). */
    void flush(std::vector<Visit>* out) {
        for (Track& tr : tracks_) finish(tr, out);
        tracks_.clear();
    }

    size_t active() const { return tracks_.size(); }

private:
    struct Track {
        uint32_t id = 0;
        float x = 0, y = 0, w = 0, h = 0;      /* last box */
        int64_t t_first_ns = 0, t_last_ns = 0;
        uint32_t n_frames = 0;
        float max_score = 0.0f;
        std::array<float, kMaxClass> votes{};  /* summed score per class_id */
    };

    static void add(Track& tr, pod_detection_t& d, int64_t t) {
        d.track_id = tr.id;
        tr.x = d.x; tr.y = d.y; tr.w = d.w; tr.h = d.h;
        tr.t_last_ns = t;
        tr.n_frames++;
        tr.max_score = std::max(tr.max_score, d.score);
        if (d.class_id < kMaxClass) tr.votes[d.class_id] += d.score;
    }

    void finish(const Track& tr, std::vector<Visit>* out) const {
        if (tr.n_frames < cfg_.min_frames) return;
        Visit v;
        v.track_id = tr.id;
        v.class_id = static_cast<uint16_t>(std::max_element(tr.votes.begin(), tr.votes.end()) - tr.votes.begin());
        v.t_start_ns = tr.t_first_ns;
        v.t_end_ns = tr.t_last_ns;
        v.n_frames = tr.n_frames;
        v.max_score = tr.max_score;
        out->push_back(v);
    }

    TrackerConfig cfg_;
    std::vector<Track> tracks_;
    uint32_t next_id_ = 1;
};

}  // namespace pod

#endif /* POD_TRACKER_H */
