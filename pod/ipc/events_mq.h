/*
 * events_mq.h -- visit-event queue from pod-track (agent-2) to pod-log (agent-1).
 * Owner: agent-1. Header-only C++17. Build with -I shared.
 *
 * Transport: POSIX message queue "/pod_events", one pod_event_t (120 bytes) per message,
 * up to 64 queued. pod-log creates it and never unlinks it, so events queue up while
 * pod-log restarts. pod-log assigns seq and pod_id: the sender leaves seq = 0.
 *
 * pod-track usage:
 *   pod::EventSender tx;               // opens lazily, retries if pod-log is not up yet
 *   pod_event_t e{}; ... fill t_start_ms, t_end_ms, class_id, max_score, track_id, n_frames
 *   if (!tx.send(e)) { count it: queue full or pod-log not running }
 */
#ifndef POD_EVENTS_MQ_H
#define POD_EVENTS_MQ_H

#include <fcntl.h>
#include <mqueue.h>
#include <time.h>

#include <cerrno>

#include "pod_msgs.h"

namespace pod {

constexpr const char* kEventsMq = "/pod_events";
constexpr long kEventsMqDepth = 64;
const mqd_t kNoMq = static_cast<mqd_t>(-1);

class EventSender {
public:
    explicit EventSender(const char* name = kEventsMq) : name_(name) {}
    ~EventSender() { if (mq_ != kNoMq) mq_close(mq_); }

    /* Non-blocking. false = queue full or pod-log has never created the queue. */
    bool send(const pod_event_t& e) {
        if (mq_ == kNoMq) mq_ = mq_open(name_, O_WRONLY | O_NONBLOCK);
        if (mq_ == kNoMq) return false;
        return mq_send(mq_, reinterpret_cast<const char*>(&e), sizeof e, 0) == 0;
    }

private:
    const char* name_;
    mqd_t mq_ = kNoMq;
};

class EventReceiver {
public:
    ~EventReceiver() { if (mq_ != kNoMq) mq_close(mq_); }

    bool create(const char* name = kEventsMq) {
        mq_attr a{};
        a.mq_maxmsg = kEventsMqDepth;
        a.mq_msgsize = sizeof(pod_event_t);
        mq_ = mq_open(name, O_RDONLY | O_CREAT, 0600, &a);
        if (mq_ == kNoMq) return false;
        mq_attr got{};
        if (mq_getattr(mq_, &got) != 0 || got.mq_msgsize != static_cast<long>(sizeof(pod_event_t))) {
            errno = EINVAL;   /* an old queue with another message size: unlink it and retry */
            return false;
        }
        return true;
    }

    /* 1 = got an event, 0 = timeout, -1 = error (errno set). */
    int receive(pod_event_t* e, int timeout_ms) {
        timespec ts;
        clock_gettime(CLOCK_REALTIME, &ts);
        ts.tv_sec += timeout_ms / 1000;
        ts.tv_nsec += static_cast<long>(timeout_ms % 1000) * 1000000L;
        if (ts.tv_nsec >= 1000000000L) { ts.tv_sec++; ts.tv_nsec -= 1000000000L; }
        ssize_t n = mq_timedreceive(mq_, reinterpret_cast<char*>(e), sizeof *e, nullptr, &ts);
        if (n == static_cast<ssize_t>(sizeof *e)) return 1;
        if (n < 0 && (errno == ETIMEDOUT || errno == EINTR)) return 0;
        if (n >= 0) errno = EMSGSIZE;
        return -1;
    }

private:
    mqd_t mq_ = kNoMq;
};

}  // namespace pod

#endif /* POD_EVENTS_MQ_H */
