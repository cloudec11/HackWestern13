/*
 * health.h -- liveness table shared by pod-supervisor and the Pod processes.
 * Owner: agent-1. Header-only C++17.
 *
 * pod-supervisor creates shm "/pod_health" with named slots and starts each child with
 * the environment variable POD_HEALTH_NAME=<slot name>. A child calls beat() whenever it
 * makes real progress (pod-capture: each published frame; pod-log: each loop pass).
 * The supervisor restarts a child whose last beat is older than 3 x 500 ms.
 * Without a supervisor (no shm or no env var) beat() does nothing, so every program
 * still runs on its own.
 *
 *   pod::HealthBeat hb;      // reads POD_HEALTH_NAME, attaches if the table exists
 *   ... hb.beat();
 */
#ifndef POD_HEALTH_H
#define POD_HEALTH_H

#include <fcntl.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

#include <atomic>
#include <cstdint>
#include <cstdlib>
#include <cstring>

namespace pod {

constexpr const char* kHealthShm = "/pod_health";
constexpr uint32_t kHealthMagic = 0x504F4448u;   /* 'PODH' */
constexpr int kHealthSlots = 8;
constexpr int kHealthNameLen = 16;

static_assert(std::atomic<int64_t>::is_always_lock_free, "need lock-free 64-bit atomics");

struct alignas(64) HealthSlot {
    char name[kHealthNameLen];          /* "" = unused; written by the supervisor only */
    std::atomic<int32_t> pid;
    std::atomic<uint64_t> beats;
    std::atomic<int64_t> t_last_ns;     /* CLOCK_MONOTONIC of the last beat */
};

struct HealthTable {
    uint32_t magic;
    uint32_t nslots;
    HealthSlot slot[kHealthSlots];
};

inline int64_t health_now_ns() {
    timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return static_cast<int64_t>(ts.tv_sec) * 1000000000LL + ts.tv_nsec;
}

class HealthBeat {
public:
    HealthBeat() {
        const char* name = std::getenv("POD_HEALTH_NAME");
        if (!name || !*name) return;
        int fd = shm_open(kHealthShm, O_RDWR, 0600);
        if (fd < 0) return;
        void* m = mmap(nullptr, sizeof(HealthTable), PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
        close(fd);
        if (m == MAP_FAILED) return;
        auto* t = static_cast<HealthTable*>(m);
        if (t->magic != kHealthMagic) { munmap(m, sizeof(HealthTable)); return; }
        for (int i = 0; i < kHealthSlots; i++) {
            if (std::strncmp(t->slot[i].name, name, kHealthNameLen) == 0) {
                table_ = t;
                slot_ = &t->slot[i];
                slot_->pid.store(static_cast<int32_t>(getpid()));
                beat();
                return;
            }
        }
        munmap(m, sizeof(HealthTable));
    }
    ~HealthBeat() { if (table_) munmap(table_, sizeof(HealthTable)); }
    HealthBeat(const HealthBeat&) = delete;
    HealthBeat& operator=(const HealthBeat&) = delete;

    void beat() {
        if (!slot_) return;
        slot_->t_last_ns.store(health_now_ns(), std::memory_order_release);
        slot_->beats.fetch_add(1, std::memory_order_relaxed);
    }
    bool attached() const { return slot_ != nullptr; }

private:
    HealthTable* table_ = nullptr;
    HealthSlot* slot_ = nullptr;
};

}  // namespace pod

#endif /* POD_HEALTH_H */
