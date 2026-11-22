/*
 * pod-supervisor -- starts the Pod processes and keeps them alive. Owner: agent-1 (P3-1).
 *
 *   pod-supervisor [--conf pod/supervisor/pod.conf] [--state pod-data/supervisor] [--prio 40]
 *
 * Config: one process per line, '#' starts a comment:
 *     <name> <prio> <watch> <command> [args...]
 *   name   up to 15 chars, also the health slot name
 *   prio   0 = normal scheduling, 1..63 = SCHED_FIFO at that priority
 *   watch  beat = restart after 3 missed 500 ms beats (the process uses pod/ipc/health.h)
 *          exit = only restart when it exits (programs that do not beat, e.g. pod-uplink)
 *
 * Rules:
 *   - A child that exits or crashes is restarted after 1 s, doubling up to 30 s while it
 *     keeps failing; the delay resets once it has run for 60 s.
 *   - A "beat" child gets 5 s to start, then must beat at least every 1.5 s (3 x 500 ms).
 *     If not, it gets SIGTERM, then SIGKILL 1 s later, and is restarted.
 *   - Every 10 s: reads the SoC temperature from /dev/thermal (rpi_thermal) if present and
 *     writes <state>/status.json (temp_c, restarts, per-child pid/beats/uptime). That file is
 *     where the heartbeat (P3-3) can take temp_c and restarts from.
 *   - SIGINT/SIGTERM: stops all children (TERM, then KILL after 3 s), removes the health table.
 */
#include <fcntl.h>
#include <signal.h>
#include <spawn.h>
#include <sched.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#include <algorithm>
#include <atomic>
#include <cerrno>
#include <cmath>
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

#include "health.h"

extern char** environ;

namespace {

std::atomic<bool> g_stop{false};
void on_signal(int) { g_stop = true; }

constexpr int64_t kMs = 1000000LL;
constexpr int64_t kBeatTimeout = 1500 * kMs;
constexpr int64_t kGrace = 5000 * kMs;
constexpr int64_t kKillAfter = 1000 * kMs;

struct Child {
    std::string name;
    int prio = 0;
    bool watch_beat = false;
    std::vector<std::string> argv;
    pid_t pid = 0;
    int64_t started = 0;
    int64_t restart_at = 0;        /* when to (re)start; 0 = now */
    int64_t term_sent = 0;         /* SIGTERM time while we are killing it */
    int64_t backoff = 1000 * kMs;
    uint32_t restarts = 0;
    pod::HealthSlot* slot = nullptr;
};

int64_t now_ns() { return pod::health_now_ns(); }

int64_t wall_ms() {
    timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    return static_cast<int64_t>(ts.tv_sec) * 1000 + ts.tv_nsec / 1000000;
}

void logf(const char* fmt, ...) __attribute__((format(printf, 1, 2)));
void logf(const char* fmt, ...) {
    time_t t = time(nullptr);
    tm lt;
    localtime_r(&t, &lt);
    char ts[16];
    std::strftime(ts, sizeof ts, "%H:%M:%S", &lt);
    std::printf("%s pod-supervisor: ", ts);
    va_list ap;
    va_start(ap, fmt);
    std::vprintf(fmt, ap);
    va_end(ap);
    std::printf("\n");
}

bool load_conf(const std::string& path, std::vector<Child>& out) {
    std::ifstream f(path);
    if (!f) { std::fprintf(stderr, "pod-supervisor: cannot read %s\n", path.c_str()); return false; }
    std::string line;
    int n = 0;
    while (std::getline(f, line)) {
        n++;
        if (auto h = line.find('#'); h != std::string::npos) line.erase(h);
        std::istringstream ss(line);
        Child c;
        std::string watch;
        if (!(ss >> c.name)) continue;
        if (!(ss >> c.prio >> watch) || c.prio < 0 || c.prio > 63 || (watch != "beat" && watch != "exit") ||
            c.name.size() >= static_cast<size_t>(pod::kHealthNameLen)) {
            std::fprintf(stderr, "pod-supervisor: %s:%d: expected <name> <prio 0..63> <beat|exit> <command...>\n", path.c_str(), n);
            return false;
        }
        c.watch_beat = watch == "beat";
        for (std::string a; ss >> a;) c.argv.push_back(a);
        if (c.argv.empty()) { std::fprintf(stderr, "pod-supervisor: %s:%d: no command\n", path.c_str(), n); return false; }
        out.push_back(c);
    }
    if (out.empty() || out.size() > static_cast<size_t>(pod::kHealthSlots)) {
        std::fprintf(stderr, "pod-supervisor: need 1..%d processes in %s\n", pod::kHealthSlots, path.c_str());
        return false;
    }
    return true;
}

pod::HealthTable* create_health() {
    shm_unlink(pod::kHealthShm);
    int fd = shm_open(pod::kHealthShm, O_CREAT | O_EXCL | O_RDWR, 0600);
    if (fd < 0) return nullptr;
    if (ftruncate(fd, sizeof(pod::HealthTable)) != 0) { close(fd); return nullptr; }
    void* m = mmap(nullptr, sizeof(pod::HealthTable), PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    close(fd);
    if (m == MAP_FAILED) return nullptr;
    std::memset(m, 0, sizeof(pod::HealthTable));
    auto* t = static_cast<pod::HealthTable*>(m);
    t->nslots = pod::kHealthSlots;
    return t;
}

void start(Child& c) {
    std::vector<char*> argv;
    for (auto& a : c.argv) argv.push_back(const_cast<char*>(a.c_str()));
    argv.push_back(nullptr);

    std::string health_env = "POD_HEALTH_NAME=" + c.name;
    std::vector<char*> envp;
    for (char** e = environ; *e; e++)
        if (std::strncmp(*e, "POD_HEALTH_NAME=", 16) != 0) envp.push_back(*e);
    envp.push_back(const_cast<char*>(health_env.c_str()));
    envp.push_back(nullptr);

    posix_spawnattr_t attr;
    posix_spawnattr_init(&attr);
    short flags = POSIX_SPAWN_SETSIGMASK | POSIX_SPAWN_SETSIGDEF;
    sigset_t none, all;
    sigemptyset(&none);
    sigfillset(&all);
    posix_spawnattr_setsigmask(&attr, &none);
    posix_spawnattr_setsigdefault(&attr, &all);
    /* prio 0 must mean "normal", not "inherit": the supervisor itself runs at SCHED_FIFO 40, so a child spawned
     * without an explicit policy inherited FIFO 40, above pod-capture (30) and the camera service. pod-infer
     * (3 busy threads) then starved the camera: capture stalled for seconds and was restarted in a loop. */
    {
        sched_param sp{};
        if (c.prio > 0) {
            sp.sched_priority = c.prio;
            posix_spawnattr_setschedpolicy(&attr, SCHED_FIFO);
        } else {
            sp.sched_priority = 10;
            posix_spawnattr_setschedpolicy(&attr, SCHED_OTHER);
        }
        posix_spawnattr_setschedparam(&attr, &sp);
        flags |= POSIX_SPAWN_SETSCHEDULER;
    }
    posix_spawnattr_setflags(&attr, flags);

    c.slot->pid.store(0);
    c.slot->t_last_ns.store(0);
    pid_t pid = 0;
    int rc = posix_spawnp(&pid, argv[0], nullptr, &attr, argv.data(), envp.data());
    posix_spawnattr_destroy(&attr);
    const int64_t now = now_ns();
    if (rc != 0) {
        logf("%s: start failed: %s (retry in %lld s)", c.name.c_str(), std::strerror(rc),
             static_cast<long long>(c.backoff / 1000 / kMs));
        c.restart_at = now + c.backoff;
        c.backoff = std::min<int64_t>(c.backoff * 2, 30000 * kMs);
        return;
    }
    c.pid = pid;
    c.started = now;
    c.term_sent = 0;
    logf("%s: started pid %d (%s, %s)", c.name.c_str(), pid,
         c.prio ? ("SCHED_FIFO " + std::to_string(c.prio)).c_str() : "normal priority",
         c.watch_beat ? "watchdog on beats" : "restart on exit");
}

void on_exit(Child& c, int status) {
    const int64_t now = now_ns();
    char why[64];
    if (WIFEXITED(status)) std::snprintf(why, sizeof why, "exited with code %d", WEXITSTATUS(status));
    else if (WIFSIGNALED(status)) std::snprintf(why, sizeof why, "killed by signal %d", WTERMSIG(status));
    else std::snprintf(why, sizeof why, "stopped (status %d)", status);
    if (now - c.started >= 60000 * kMs) c.backoff = 1000 * kMs;   /* it had been healthy */
    c.pid = 0;
    c.restarts++;
    c.restart_at = now + c.backoff;
    logf("%s: %s after %.1f s; restart #%u in %lld s", c.name.c_str(), why,
         static_cast<double>(now - c.started) / 1e9, c.restarts, static_cast<long long>(c.backoff / 1000 / kMs));
    c.backoff = std::min<int64_t>(c.backoff * 2, 30000 * kMs);
}

double read_temp_c() {
    int fd = open("/dev/thermal", O_RDONLY);
    if (fd < 0) return NAN;
    char b[32] = {};
    ssize_t n = read(fd, b, sizeof b - 1);
    close(fd);
    return n > 0 ? std::strtod(b, nullptr) : NAN;
}

void write_status(const std::string& dir, const std::vector<Child>& kids, double temp) {
    const int64_t now = now_ns();
    uint32_t total = 0;
    for (auto& c : kids) total += c.restarts;
    std::string s = "{\"t_wall_ms\":" + std::to_string(wall_ms()) + ",\"temp_c\":";
    if (std::isnan(temp)) s += "null"; else { char b[16]; std::snprintf(b, sizeof b, "%.1f", temp); s += b; }
    s += ",\"restarts\":" + std::to_string(total) + ",\"children\":[";
    for (size_t i = 0; i < kids.size(); i++) {
        const Child& c = kids[i];
        char b[256];
        std::snprintf(b, sizeof b, "%s{\"name\":\"%s\",\"pid\":%d,\"restarts\":%u,\"beats\":%llu,\"up_s\":%.0f}",
                      i ? "," : "", c.name.c_str(), static_cast<int>(c.pid), c.restarts,
                      static_cast<unsigned long long>(c.slot->beats.load()),
                      c.pid ? static_cast<double>(now - c.started) / 1e9 : 0.0);
        s += b;
    }
    s += "]}\n";
    std::string tmp = dir + "/status.json.tmp";
    if (FILE* f = std::fopen(tmp.c_str(), "w")) {
        std::fputs(s.c_str(), f);
        std::fclose(f);
        std::rename(tmp.c_str(), (dir + "/status.json").c_str());
    }
}

}  // namespace

int main(int argc, char** argv) {
    std::setvbuf(stdout, nullptr, _IOLBF, 0);
    std::string conf = "pod/supervisor/pod.conf", state = "pod-data/supervisor";
    int prio = 40;
    for (int i = 1; i + 1 < argc; i += 2) {
        std::string a = argv[i];
        if (a == "--conf") conf = argv[i + 1];
        else if (a == "--state") state = argv[i + 1];
        else if (a == "--prio") prio = std::atoi(argv[i + 1]);
        else { std::fprintf(stderr, "pod-supervisor: unknown option %s\n", a.c_str()); return 2; }
    }
    std::vector<Child> kids;
    if (!load_conf(conf, kids)) return 2;
    for (size_t i = 1; i <= state.size(); i++)
        if (i == state.size() || state[i] == '/') mkdir(state.substr(0, i).c_str(), 0755);

    if (prio > 0) {   /* the supervisor itself must outrank what it supervises */
        sched_param sp{};
        sp.sched_priority = prio;
        if (sched_setscheduler(0, SCHED_FIFO, &sp) != 0)
            logf("could not set own SCHED_FIFO %d: %s", prio, std::strerror(errno));
    }

    pod::HealthTable* table = create_health();
    if (!table) { std::perror("pod-supervisor: health table"); return 1; }
    for (size_t i = 0; i < kids.size(); i++) {
        std::strncpy(table->slot[i].name, kids[i].name.c_str(), pod::kHealthNameLen - 1);
        kids[i].slot = &table->slot[i];
    }
    table->magic = pod::kHealthMagic;   /* last: children only attach to a complete table */

    struct sigaction sa{};
    sa.sa_handler = on_signal;
    sigaction(SIGINT, &sa, nullptr);
    sigaction(SIGTERM, &sa, nullptr);

    double temp = read_temp_c();
    logf("%zu processes from %s, own prio %d, temperature %s", kids.size(), conf.c_str(), prio,
         std::isnan(temp) ? "unavailable (/dev/thermal missing: rpi_thermal not running)" : (std::to_string(temp) + " C").c_str());

    int64_t next_status = 0;
    while (!g_stop) {
        const int64_t now = now_ns();
        for (auto& c : kids)
            if (!c.pid && now >= c.restart_at) start(c);

        int status;
        pid_t p;
        while ((p = waitpid(-1, &status, WNOHANG)) > 0)
            for (auto& c : kids)
                if (c.pid == p) on_exit(c, status);

        for (auto& c : kids) {
            if (!c.pid) continue;
            if (c.term_sent) {
                if (now - c.term_sent >= kKillAfter) { kill(c.pid, SIGKILL); c.term_sent = now; }
                continue;
            }
            if (!c.watch_beat || now - c.started < kGrace) continue;
            const int64_t last = std::max(c.slot->t_last_ns.load(std::memory_order_acquire), c.started + kGrace - kBeatTimeout);
            if (now - last > kBeatTimeout) {
                logf("%s: no beat for %.1f s (pid %d) -> SIGTERM", c.name.c_str(), static_cast<double>(now - last) / 1e9, c.pid);
                kill(c.pid, SIGTERM);
                kill(c.pid, SIGCONT);   /* a stopped process cannot act on SIGTERM */
                c.term_sent = now;
            }
        }

        if (now >= next_status) {
            double t = read_temp_c();
            if (!std::isnan(t)) logf("temperature %.1f C", t);
            write_status(state, kids, t);
            next_status = now + 10000 * kMs;
        }
        usleep(100000);
    }

    logf("stopping");
    for (auto& c : kids) if (c.pid) kill(c.pid, SIGTERM);
    const int64_t deadline = now_ns() + 3000 * kMs;
    for (;;) {
        bool any = false;
        for (auto& c : kids) {
            if (!c.pid) continue;
            int status;
            if (waitpid(c.pid, &status, WNOHANG) == c.pid) c.pid = 0; else any = true;
        }
        if (!any) break;
        if (now_ns() >= deadline) {
            for (auto& c : kids) if (c.pid) { logf("%s: did not stop, SIGKILL", c.name.c_str()); kill(c.pid, SIGKILL); waitpid(c.pid, nullptr, 0); c.pid = 0; }
            break;
        }
        usleep(50000);
    }
    write_status(state, kids, read_temp_c());
    munmap(table, sizeof(pod::HealthTable));
    shm_unlink(pod::kHealthShm);
    logf("stopped");
    return 0;
}
