/*
 * ring_test.cpp -- tests for pod/ipc/ring.h. Owner: agent-1.
 * Build on the Pi:  clang++ -std=c++17 -O2 -Wall -Wextra -I shared pod/ipc/ring_test.cpp -o ring_test
 * Run:              ./ring_test      (exit code 0 = all passed)
 */
#include <sys/wait.h>

#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <thread>
#include <vector>

#include "ring.h"

using namespace pod;

static int failures = 0;
#define CHECK(cond, msg)                                                      \
    do {                                                                      \
        if (!(cond)) { std::printf("FAIL: %s  (%s:%d)\n", msg, __FILE__, __LINE__); ++failures; } \
    } while (0)

static constexpr uint32_t kPayload = 1024;

/* Fill a slot: every payload byte equals (seq & 0xFF), so a torn read is detectable. */
static void write_frame(Ring& r, uint64_t seq) {
    uint32_t slot;
    pod_frame_hdr_t* h = r.begin_write(&slot);
    h->seq = seq;
    h->t_capture_ns = static_cast<int64_t>(seq) * 1000;
    h->width = 32; h->height = 32; h->stride = 32; h->data_size = kPayload;
    h->pixel_format = POD_PIX_NV12;
    std::memset(r.payload(h), static_cast<int>(seq & 0xFF), kPayload);
    r.publish(slot);
}

static bool payload_ok(const Ring& r, const pod_frame_hdr_t* h) {
    const uint8_t* p = r.payload(h);
    for (uint32_t i = 0; i < kPayload; ++i)
        if (p[i] != static_cast<uint8_t>(h->seq & 0xFF)) return false;
    return h->t_capture_ns == static_cast<int64_t>(h->seq) * 1000;
}

/* 1. Behaviour on one thread, in plain memory. */
static void test_basic() {
    std::vector<uint8_t> mem(Ring::bytes_needed(kPayload) + 64);
    Ring r;
    CHECK(r.init(mem.data(), mem.size(), kPayload, false), "init");
    CHECK(r.acquire_latest(0) == nullptr, "empty ring gives nothing");

    write_frame(r, 1);
    const pod_frame_hdr_t* h = r.acquire_latest(0);
    CHECK(h && h->seq == 1 && payload_ok(r, h), "first frame readable");
    CHECK(h && h->magic == POD_FRAME_MAGIC && h->version == POD_MSGS_VERSION, "header magic/version set");
    r.release();
    CHECK(r.acquire_latest(1) == nullptr, "no newer frame after reading seq 1");

    /* Producer runs ahead: consumer should jump straight to the newest. */
    write_frame(r, 2); write_frame(r, 3); write_frame(r, 4); write_frame(r, 5); write_frame(r, 6);
    h = r.acquire_latest(1);
    CHECK(h && h->seq == 6, "consumer gets newest, skipping 2..5");
    CHECK(h && payload_ok(r, h), "newest payload intact");

    /* While the consumer holds a frame, the producer must never overwrite it. */
    for (uint64_t s = 7; s < 40; ++s) write_frame(r, s);
    CHECK(h->seq == 6 && payload_ok(r, h), "held frame untouched while producer keeps writing");
    r.release();

    CHECK(r.wait(0), "pulses were posted");
    CHECK(!r.wait(10), "wait times out when nothing new (pulses drained)");
}

/* 2. Producer and consumer threads, no pauses, many frames: detect torn reads. */
static void test_threads() {
    std::vector<uint8_t> mem(Ring::bytes_needed(kPayload) + 64);
    Ring r;
    CHECK(r.init(mem.data(), mem.size(), kPayload, false), "init");
    const uint64_t kFrames = 200000;
    std::atomic<bool> done{false};
    uint64_t seen = 0, torn = 0, backwards = 0;

    std::thread consumer([&] {
        uint64_t last = 0;
        while (last < kFrames) {
            const pod_frame_hdr_t* h = r.acquire_latest(last);
            if (!h) { if (done.load() && r.published_seq() <= last) break; continue; }
            if (h->seq <= last) ++backwards;
            if (!payload_ok(r, h)) ++torn;
            last = h->seq;
            ++seen;
            r.release();
        }
    });
    for (uint64_t s = 1; s <= kFrames; ++s) write_frame(r, s);
    done = true;
    consumer.join();
    std::printf("  threads: %llu frames produced, consumer saw %llu, torn=%llu, backwards=%llu\n",
                (unsigned long long)kFrames, (unsigned long long)seen,
                (unsigned long long)torn, (unsigned long long)backwards);
    CHECK(torn == 0, "no torn reads");
    CHECK(backwards == 0, "seq never goes backwards");
    CHECK(seen > 0, "consumer saw frames");
}

/* 3. Two real processes over POSIX shared memory, using the pulse semaphore. */
static void test_processes() {
    const char* name = "/pod_ring_test";
    ShmRing producer;
    CHECK(producer.create(name, kPayload), "shm create");
    pid_t pid = fork();
    if (pid == 0) {
        ShmRing consumer;
        if (!consumer.open(name)) _exit(10);
        uint64_t last = 0;
        int bad = 0;
        while (last < 3000) {
            if (!consumer.ring.wait(2000)) _exit(11);          /* timed out: producer stalled */
            const pod_frame_hdr_t* h = consumer.ring.acquire_latest(last);
            if (!h) continue;
            if (!payload_ok(consumer.ring, h)) ++bad;
            last = h->seq;
            consumer.ring.release();
        }
        _exit(bad ? 12 : 0);
    }
    for (uint64_t s = 1; s <= 3000; ++s) {
        write_frame(producer.ring, s);
        if (s % 50 == 0) usleep(1000);                         /* let the child keep up now and then */
    }
    int status = 0;
    waitpid(pid, &status, 0);
    CHECK(WIFEXITED(status) && WEXITSTATUS(status) == 0, "consumer process exited cleanly (no bad frames, no stall)");
    if (WIFEXITED(status)) std::printf("  processes: consumer exit code %d\n", WEXITSTATUS(status));
}

int main() {
    std::printf("ring_test: slots=%d, payload=%u, ring bytes=%zu\n", POD_FRAME_SLOTS, kPayload,
                Ring::bytes_needed(kPayload));
    test_basic();      std::printf("basic done\n");
    test_threads();    std::printf("threads done\n");
    test_processes();  std::printf("processes done\n");
    std::printf(failures ? "FAILED (%d)\n" : "ALL PASSED\n", failures);
    return failures ? 1 : 0;
}
