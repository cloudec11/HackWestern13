/*
 * ring.h -- frame ring buffer between pod-capture (producer) and pod-infer (consumer).
 * Owner: agent-1. Header-only C++17. Uses shared/pod_msgs.h (build with -I shared).
 *
 * Model
 *  - ONE producer, ONE consumer, in different processes, sharing a POSIX shm object.
 *  - POD_FRAME_SLOTS (4) slots, each: pod_frame_hdr_t + up to max_payload bytes of pixels.
 *  - "Newest wins": the consumer always gets the latest published frame and skips
 *    older ones. The producer never blocks and never waits for the consumer, so a
 *    slow model cannot stall the camera. Skipped frames show up as gaps in hdr->seq.
 *  - Lock-free: the producer never writes the slot published as "latest" or the slot
 *    the consumer says it is reading. With 4 slots a free slot always exists.
 *  - "Pulse": a process-shared semaphore is posted on every publish so the consumer
 *    can sleep instead of polling.
 *
 * Producer:                               Consumer:
 *   ring.create("/pod_frames", maxpix);     ring.open("/pod_frames");
 *   uint32_t slot;                          uint64_t last = 0;
 *   auto* h = ring.begin_write(&slot);      while (ring.wait(500)) {
 *   fill h fields + payload(h)...             auto* h = ring.acquire_latest(last);
 *   ring.publish(slot);                       if (!h) continue;
 *                                             use h, ring.payload(h) ...
 *                                             last = h->seq; ring.release();
 *                                           }
 */
#ifndef POD_RING_H
#define POD_RING_H

#include <atomic>
#include <cerrno>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <ctime>

#include <fcntl.h>
#include <semaphore.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

#include "pod_msgs.h"

namespace pod {

constexpr uint32_t kRingMagic = 0x504F5247u;  /* 'PORG' */
constexpr uint32_t kRingVersion = 1;
constexpr uint32_t kNoSlot = 0xFFFFFFFFu;

static_assert(POD_FRAME_SLOTS >= 3, "need at least 3 slots so a free slot always exists");
static_assert(std::atomic<uint64_t>::is_always_lock_free, "64-bit atomics must be lock-free");
static_assert(std::atomic<uint32_t>::is_always_lock_free, "32-bit atomics must be lock-free");

/* Control block at the start of the shared region. */
struct alignas(64) RingCtrl {
    uint32_t magic;
    uint32_t version;
    uint32_t slot_count;
    uint32_t slot_stride;   /* bytes per slot, multiple of 64 */
    uint32_t max_payload;   /* pixel bytes per slot */
    uint32_t reserved;
    std::atomic<uint64_t> published_seq;  /* seq of the newest published frame, 0 = none */
    std::atomic<uint32_t> latest_slot;    /* slot holding the newest frame, kNoSlot = none */
    std::atomic<uint32_t> reading_slot;   /* slot the consumer is using, kNoSlot = none */
    sem_t pulse;                          /* posted on every publish */
};

class Ring {
public:
    static uint32_t slot_stride_for(uint32_t max_payload) {
        return static_cast<uint32_t>((sizeof(pod_frame_hdr_t) + max_payload + 63u) & ~63u);
    }
    static size_t bytes_needed(uint32_t max_payload) {
        return sizeof(RingCtrl) + static_cast<size_t>(POD_FRAME_SLOTS) * slot_stride_for(max_payload);
    }

    /* Producer: initialise a ZEROED region of at least bytes_needed(max_payload) bytes.
     * pshared=true when the region is shared memory used by two processes. */
    bool init(void* mem, size_t len, uint32_t max_payload, bool pshared) {
        if (!mem || len < bytes_needed(max_payload)) return false;
        std::memset(mem, 0, bytes_needed(max_payload));
        ctrl_ = static_cast<RingCtrl*>(mem);
        ctrl_->version = kRingVersion;
        ctrl_->slot_count = POD_FRAME_SLOTS;
        ctrl_->slot_stride = slot_stride_for(max_payload);
        ctrl_->max_payload = max_payload;
        ctrl_->published_seq.store(0);
        ctrl_->latest_slot.store(kNoSlot);
        ctrl_->reading_slot.store(kNoSlot);
        if (sem_init(&ctrl_->pulse, pshared ? 1 : 0, 0) != 0) { ctrl_ = nullptr; return false; }
        base_ = static_cast<uint8_t*>(mem) + sizeof(RingCtrl);
        /* Magic goes last: attach() refuses a region until it is fully initialised. */
        std::atomic_thread_fence(std::memory_order_seq_cst);
        ctrl_->magic = kRingMagic;
        return true;
    }

    /* Consumer: attach to a region another process already init()ed. */
    bool attach(void* mem, size_t len) {
        if (!mem || len < sizeof(RingCtrl)) return false;
        auto* c = static_cast<RingCtrl*>(mem);
        if (c->magic != kRingMagic || c->version != kRingVersion || c->slot_count != POD_FRAME_SLOTS)
            return false;
        if (len < sizeof(RingCtrl) + static_cast<size_t>(c->slot_count) * c->slot_stride) return false;
        ctrl_ = c;
        base_ = static_cast<uint8_t*>(mem) + sizeof(RingCtrl);
        return true;
    }

    uint32_t max_payload() const { return ctrl_->max_payload; }
    uint64_t published_seq() const { return ctrl_->published_seq.load(std::memory_order_acquire); }

    /* ---- producer side ---- */

    /* Returns a slot to fill (header + payload), never one a reader can see. */
    pod_frame_hdr_t* begin_write(uint32_t* slot_out) {
        uint32_t latest = ctrl_->latest_slot.load(std::memory_order_seq_cst);
        uint32_t reading = ctrl_->reading_slot.load(std::memory_order_seq_cst);
        for (uint32_t s = 0; s < ctrl_->slot_count; ++s) {
            if (s != latest && s != reading) {
                *slot_out = s;
                return hdr_at(s);
            }
        }
        return nullptr; /* unreachable with >= 3 slots */
    }

    /* Make the filled slot the newest frame and wake the consumer.
     * The caller must have set hdr->seq, t_capture_ns, width, height, stride,
     * data_size and pixel_format. magic, version and slot are set here. */
    void publish(uint32_t slot) {
        pod_frame_hdr_t* h = hdr_at(slot);
        h->magic = POD_FRAME_MAGIC;
        h->version = POD_MSGS_VERSION;
        h->slot = slot;
        ctrl_->latest_slot.store(slot, std::memory_order_seq_cst);
        ctrl_->published_seq.store(h->seq, std::memory_order_seq_cst);
        sem_post(&ctrl_->pulse);
    }

    /* ---- consumer side ---- */

    /* Sleep until a frame is published or timeout_ms passes. Returns true on a pulse.
     * Extra pulses are drained: only the newest frame matters. */
    bool wait(int timeout_ms) {
        timespec ts;
        clock_gettime(CLOCK_REALTIME, &ts);
        ts.tv_sec += timeout_ms / 1000;
        ts.tv_nsec += static_cast<long>(timeout_ms % 1000) * 1000000L;
        if (ts.tv_nsec >= 1000000000L) { ts.tv_sec += 1; ts.tv_nsec -= 1000000000L; }
        int rc;
        do { rc = sem_timedwait(&ctrl_->pulse, &ts); } while (rc != 0 && errno == EINTR);
        if (rc != 0) return false;
        while (sem_trywait(&ctrl_->pulse) == 0) {}
        return true;
    }

    /* Newest frame if its seq > last_seq, else nullptr. The pointer stays valid
     * (the producer will not touch that slot) until release(). */
    const pod_frame_hdr_t* acquire_latest(uint64_t last_seq) {
        for (;;) {
            uint32_t s = ctrl_->latest_slot.load(std::memory_order_seq_cst);
            if (s == kNoSlot) return nullptr;
            ctrl_->reading_slot.store(s, std::memory_order_seq_cst);
            /* The producer may have moved on and started rewriting s before our claim
             * was visible. If latest changed, our claim may be stale: retry. */
            if (ctrl_->latest_slot.load(std::memory_order_seq_cst) != s) continue;
            const pod_frame_hdr_t* h = hdr_at(s);
            if (h->seq <= last_seq) {
                ctrl_->reading_slot.store(kNoSlot, std::memory_order_seq_cst);
                return nullptr;
            }
            return h;
        }
    }

    void release() { ctrl_->reading_slot.store(kNoSlot, std::memory_order_seq_cst); }

    /* ---- observer side (e.g. pod-view) ---- */

    /* Copy the newest frame WITHOUT claiming it: never blocks the producer, never touches
     * reading_slot or the pulse, so the real consumer is not disturbed. Any number of
     * observers may call this. Returns false if there is no frame newer than last_seq, it
     * does not fit in cap bytes, or the producer moved on during the copy (try again later).
     * Why the check is enough: the producer only rewrites a slot after it stops being
     * latest, which needs a publish, which changes published_seq. */
    bool peek_latest(uint64_t last_seq, pod_frame_hdr_t* hdr_out, uint8_t* dst, size_t cap) const {
        const uint64_t before = ctrl_->published_seq.load(std::memory_order_seq_cst);
        const uint32_t s = ctrl_->latest_slot.load(std::memory_order_seq_cst);
        if (s == kNoSlot || before == 0 || before <= last_seq) return false;
        const pod_frame_hdr_t* h = hdr_at(s);
        pod_frame_hdr_t hc;
        std::memcpy(&hc, h, sizeof hc);
        if (hc.seq != before || hc.data_size > cap) return false;   /* a publish slipped in between the loads */
        std::memcpy(dst, payload(h), hc.data_size);
        std::atomic_thread_fence(std::memory_order_seq_cst);
        if (ctrl_->published_seq.load(std::memory_order_seq_cst) != before) return false;
        *hdr_out = hc;
        return true;
    }

    /* ---- both sides ---- */

    uint8_t* payload(pod_frame_hdr_t* h) const { return reinterpret_cast<uint8_t*>(h) + sizeof(pod_frame_hdr_t); }
    const uint8_t* payload(const pod_frame_hdr_t* h) const {
        return reinterpret_cast<const uint8_t*>(h) + sizeof(pod_frame_hdr_t);
    }

private:
    pod_frame_hdr_t* hdr_at(uint32_t slot) const {
        return reinterpret_cast<pod_frame_hdr_t*>(base_ + static_cast<size_t>(slot) * ctrl_->slot_stride);
    }
    RingCtrl* ctrl_ = nullptr;
    uint8_t* base_ = nullptr;
};

/* POSIX shared-memory wrapper: create (producer) or open (consumer) a named ring. */
class ShmRing {
public:
    ~ShmRing() { close(); }

    bool create(const char* name, uint32_t max_payload) {
        close();
        shm_unlink(name);
        fd_ = shm_open(name, O_CREAT | O_EXCL | O_RDWR, 0600);
        if (fd_ < 0) return false;
        len_ = Ring::bytes_needed(max_payload);
        if (ftruncate(fd_, static_cast<off_t>(len_)) != 0) { close(); return false; }
        mem_ = mmap(nullptr, len_, PROT_READ | PROT_WRITE, MAP_SHARED, fd_, 0);
        if (mem_ == MAP_FAILED) { mem_ = nullptr; close(); return false; }
        if (!ring.init(mem_, len_, max_payload, true)) { close(); return false; }
        name_ = name;
        owner_ = true;
        return true;
    }

    bool open(const char* name) {
        close();
        fd_ = shm_open(name, O_RDWR, 0600);
        if (fd_ < 0) return false;
        struct stat st;
        if (fstat(fd_, &st) != 0) { close(); return false; }
        len_ = static_cast<size_t>(st.st_size);
        mem_ = mmap(nullptr, len_, PROT_READ | PROT_WRITE, MAP_SHARED, fd_, 0);
        if (mem_ == MAP_FAILED) { mem_ = nullptr; close(); return false; }
        if (!ring.attach(mem_, len_)) { close(); return false; }
        name_ = name;
        return true;
    }

    void close() {
        if (mem_) { munmap(mem_, len_); mem_ = nullptr; }
        if (fd_ >= 0) { ::close(fd_); fd_ = -1; }
        if (owner_) { shm_unlink(name_); owner_ = false; }
    }

    Ring ring;

private:
    int fd_ = -1;
    void* mem_ = nullptr;
    size_t len_ = 0;
    const char* name_ = nullptr;
    bool owner_ = false;
};

}  // namespace pod

#endif /* POD_RING_H */
