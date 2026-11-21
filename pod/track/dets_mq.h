/*
 * dets_mq.h -- per-frame detections from pod-infer to pod-track. Owner: agent-2 (P2-1).
 * Header-only C++17. Build with -I shared. Same pattern as agent-1's pod/ipc/events_mq.h.
 *
 * Transport: POSIX message queue "/pod_dets", one pod_detections_t (544 bytes) per frame,
 * including frames with no detections (so the tracker knows time has passed).
 * pod-track creates the queue and never unlinks it. pod-infer sends without blocking:
 * if pod-track is slow or not running, frames are dropped, never queued up in pod-infer.
 */
#ifndef POD_DETS_MQ_H
#define POD_DETS_MQ_H

#include <fcntl.h>
#include <mqueue.h>
#include <time.h>

#include <cerrno>

#include "pod_msgs.h"

namespace pod {

constexpr const char* kDetsMq = "/pod_dets";
constexpr long kDetsMqDepth = 16;   /* ~1 s of frames */
const mqd_t kNoDetsMq = static_cast<mqd_t>(-1);

class DetsSender {
public:
    explicit DetsSender(const char* name = kDetsMq) : name_(name) {}
    ~DetsSender() { if (mq_ != kNoDetsMq) mq_close(mq_); }
    DetsSender(const DetsSender&) = delete;
    DetsSender& operator=(const DetsSender&) = delete;

    /* Non-blocking. false = queue full, or pod-track has never created it. */
    bool send(const pod_detections_t& r) {
        if (mq_ == kNoDetsMq) mq_ = mq_open(name_, O_WRONLY | O_NONBLOCK);
        if (mq_ == kNoDetsMq) return false;
        if (mq_send(mq_, reinterpret_cast<const char*>(&r), sizeof r, 0) == 0) return true;
        if (errno != EAGAIN) { mq_close(mq_); mq_ = kNoDetsMq; }   /* reopen on the next call */
        return false;
    }

private:
    const char* name_;
    mqd_t mq_ = kNoDetsMq;
};

class DetsReceiver {
public:
    ~DetsReceiver() { if (mq_ != kNoDetsMq) mq_close(mq_); }

    /* Creates the queue if needed. An old queue with another message size is replaced. */
    bool create(const char* name = kDetsMq) {
        for (int attempt = 0; attempt < 2; ++attempt) {
            mq_attr a{};
            a.mq_maxmsg = kDetsMqDepth;
            a.mq_msgsize = sizeof(pod_detections_t);
            mq_ = mq_open(name, O_RDONLY | O_CREAT, 0600, &a);
            if (mq_ == kNoDetsMq) return false;
            mq_attr got{};
            if (mq_getattr(mq_, &got) == 0 && got.mq_msgsize == static_cast<long>(sizeof(pod_detections_t)))
                return true;
            mq_close(mq_);
            mq_ = kNoDetsMq;
            mq_unlink(name);
        }
        errno = EINVAL;
        return false;
    }

    /* 1 = got a frame, 0 = timeout, -1 = error (errno set). */
    int receive(pod_detections_t* r, int timeout_ms) {
        timespec ts;
        clock_gettime(CLOCK_REALTIME, &ts);
        ts.tv_sec += timeout_ms / 1000;
        ts.tv_nsec += static_cast<long>(timeout_ms % 1000) * 1000000L;
        if (ts.tv_nsec >= 1000000000L) { ts.tv_sec++; ts.tv_nsec -= 1000000000L; }
        ssize_t n = mq_timedreceive(mq_, reinterpret_cast<char*>(r), sizeof *r, nullptr, &ts);
        if (n == static_cast<ssize_t>(sizeof *r)) return 1;
        if (n < 0 && (errno == ETIMEDOUT || errno == EINTR)) return 0;
        if (n >= 0) errno = EMSGSIZE;
        return -1;
    }

private:
    mqd_t mq_ = kNoDetsMq;
};

}  // namespace pod

#endif /* POD_DETS_MQ_H */
