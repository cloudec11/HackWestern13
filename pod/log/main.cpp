/*
 * pod-log -- durable visit-event log. Owner: agent-1 (P2-2).
 *
 *   pod-log --pod-id pod-01 [--dir pod-data/events] [--uplink-state pod-data/uplink]
 *           [--mq /pod_events] [--max-file-kb 1024] [--max-total-kb 204800]
 *
 * Receives pod_event_t from pod-track over the POSIX message queue (pod/ipc/events_mq.h),
 * checks it against the ground contract, assigns seq and pod_id, and appends one JSON line
 * per event to <dir>/events-YYYYMMDD-NNN.jsonl (the format pod-uplink reads), fsync'd
 * before the event counts as logged.
 *
 * seq is never reused: the last seq is kept in <dir>/last_seq AND recovered from the
 * logs at startup (the larger wins), so it survives crashes and deleted files.
 * A line half-written by a crash is cut off at startup.
 * Rotation: a new file when the date (UTC) changes or the file would pass --max-file-kb.
 * Retention: when all logs pass --max-total-kb, the oldest files are deleted, but only
 * files whose events the ground station has acked (<uplink-state>/cursor). Never others.
 * Invalid events (they would be rejected by the ground station) are counted and skipped,
 * before a seq is assigned.
 * Thumbnails: not yet (second pass, needs agreement with agent-2 on the frame source).
 */
#include <dirent.h>
#include <fcntl.h>
#include <signal.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#include <algorithm>
#include <atomic>
#include <cctype>
#include <cerrno>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include "events_mq.h"
#include "health.h"
#include "pod_msgs.h"

namespace {

std::atomic<bool> g_stop{false};
void on_signal(int) { g_stop = true; }

struct Options {
    std::string dir = "pod-data/events";
    std::string uplink_state = "pod-data/uplink";
    std::string mq = pod::kEventsMq;
    std::string pod_id;
    uint64_t max_file = 1024ull * 1024;
    uint64_t max_total = 200ull * 1024 * 1024;
};

bool valid_pod_id(const std::string& s) {   /* ^[a-z0-9][a-z0-9-]{0,14}$ */
    if (s.empty() || s.size() > 15) return false;
    for (size_t i = 0; i < s.size(); i++) {
        char c = s[i];
        bool ok = (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || (c == '-' && i > 0);
        if (!ok) return false;
    }
    return true;
}

bool write_all(int fd, const char* p, size_t n) {
    while (n) {
        ssize_t w = write(fd, p, n);
        if (w < 0) { if (errno == EINTR) continue; return false; }
        p += w; n -= static_cast<size_t>(w);
    }
    return true;
}

bool atomic_write(const std::string& path, const std::string& text) {
    std::string tmp = path + ".tmp";
    int fd = open(tmp.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) return false;
    bool ok = write_all(fd, text.data(), text.size()) && fsync(fd) == 0;
    close(fd);
    return ok && rename(tmp.c_str(), path.c_str()) == 0;
}

uint64_t read_u64(const std::string& path) {
    FILE* f = std::fopen(path.c_str(), "r");
    if (!f) return 0;
    unsigned long long v = 0;
    if (std::fscanf(f, "%llu", &v) != 1) v = 0;
    std::fclose(f);
    return v;
}

void mkdirs(const std::string& path) {
    for (size_t i = 1; i <= path.size(); i++)
        if (i == path.size() || path[i] == '/') mkdir(path.substr(0, i).c_str(), 0755);
}

std::vector<std::string> list_logs(const std::string& dir) {
    std::vector<std::string> out;
    if (DIR* d = opendir(dir.c_str())) {
        while (dirent* e = readdir(d)) {
            std::string n = e->d_name;
            if (n.size() > 12 && n.compare(0, 7, "events-") == 0 && n.compare(n.size() - 6, 6, ".jsonl") == 0)
                out.push_back(n);
        }
        closedir(d);
    }
    std::sort(out.begin(), out.end());
    return out;
}

/* Max seq among complete lines. Cuts off a trailing partial line (crash mid-write). */
uint64_t scan_file(const std::string& path, bool repair) {
    std::string data;
    if (FILE* f = std::fopen(path.c_str(), "rb")) {
        char buf[65536];
        size_t n;
        while ((n = std::fread(buf, 1, sizeof buf, f)) > 0) data.append(buf, n);
        std::fclose(f);
    }
    size_t end = data.rfind('\n');
    size_t complete = (end == std::string::npos) ? 0 : end + 1;
    if (repair && complete < data.size()) {
        if (truncate(path.c_str(), static_cast<off_t>(complete)) == 0)
            std::fprintf(stderr, "pod-log: cut %zu bytes of a half-written line from %s\n", data.size() - complete, path.c_str());
    }
    uint64_t best = 0;
    size_t pos = 0;
    while (pos < complete) {
        size_t nl = data.find('\n', pos);
        size_t k = data.find("\"seq\":", pos);
        if (k != std::string::npos && k < nl) best = std::max<uint64_t>(best, std::strtoull(data.c_str() + k + 6, nullptr, 10));
        pos = nl + 1;
    }
    return best;
}

uint64_t file_size(const std::string& path) {
    struct stat st;
    return stat(path.c_str(), &st) == 0 ? static_cast<uint64_t>(st.st_size) : 0;
}

std::string utc_date(int64_t ms) {
    time_t t = static_cast<time_t>(ms / 1000);
    tm g;
    gmtime_r(&t, &g);
    char b[16];
    std::snprintf(b, sizeof b, "%04d%02d%02d", g.tm_year + 1900, g.tm_mon + 1, g.tm_mday);
    return b;
}

int64_t wall_ms() {
    timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    return static_cast<int64_t>(ts.tv_sec) * 1000 + ts.tv_nsec / 1000000;
}

/* Same checks the ground station applies (shared/event_schema.json v1). "" = valid. */
const char* invalid_reason(const pod_event_t& e) {
    if (e.t_start_ms < 1600000000000LL) return "t_start_ms before 2020";
    if (e.t_end_ms < e.t_start_ms) return "t_end_ms < t_start_ms";
    if (!std::isfinite(e.max_score) || e.max_score < 0.0f || e.max_score > 1.0f) return "max_score outside 0..1";
    if (e.n_frames < 1) return "n_frames < 1";
    return "";
}

std::string safe_thumb(const pod_event_t& e) {
    std::string s(e.thumb_path, strnlen(e.thumb_path, POD_THUMB_PATH_LEN));
    for (char c : s)
        if (!(std::isalnum(static_cast<unsigned char>(c)) || c == '.' || c == '_' || c == '/' || c == '-')) return "";
    return s;
}

class Log {
public:
    explicit Log(const Options& o) : o_(o) {}

    bool open() {
        mkdirs(o_.dir);
        last_seq_ = read_u64(o_.dir + "/last_seq");
        for (const auto& n : list_logs(o_.dir)) last_seq_ = std::max(last_seq_, scan_file(o_.dir + "/" + n, true));
        auto logs = list_logs(o_.dir);
        if (!logs.empty() && logs.back().compare(7, 8, utc_date(wall_ms())) == 0 &&
            file_size(o_.dir + "/" + logs.back()) < o_.max_file)
            return open_file(logs.back());
        return rotate();
    }

    uint64_t last_seq() const { return last_seq_; }
    const std::string& current() const { return cur_name_; }

    /* Assign seq, append, fsync. Returns the seq, or 0 on failure. */
    uint64_t append(pod_event_t e) {
        e.seq = last_seq_ + 1;
        char line[512];
        int n = std::snprintf(line, sizeof line,
            "{\"pod_id\":\"%s\",\"seq\":%llu,\"t_start_ms\":%lld,\"t_end_ms\":%lld,\"class_id\":%u,"
            "\"max_score\":%.4f,\"track_id\":%u,\"n_frames\":%u,\"thumb_path\":\"%s\"}\n",
            o_.pod_id.c_str(), static_cast<unsigned long long>(e.seq), static_cast<long long>(e.t_start_ms),
            static_cast<long long>(e.t_end_ms), static_cast<unsigned>(e.class_id), static_cast<double>(e.max_score),
            e.track_id, e.n_frames, safe_thumb(e).c_str());
        if (n <= 0 || n >= static_cast<int>(sizeof line)) return 0;
        if (utc_date(wall_ms()) != cur_date_ || cur_size_ + static_cast<uint64_t>(n) > o_.max_file)
            if (!rotate()) return 0;
        if (!write_all(fd_, line, static_cast<size_t>(n)) || fsync(fd_) != 0) return 0;
        cur_size_ += static_cast<uint64_t>(n);
        last_seq_ = e.seq;
        atomic_write(o_.dir + "/last_seq", std::to_string(last_seq_) + "\n");
        retain();
        return e.seq;
    }

    void close_file() { if (fd_ >= 0) { close(fd_); fd_ = -1; } }

private:
    bool open_file(const std::string& name) {
        close_file();
        std::string path = o_.dir + "/" + name;
        fd_ = ::open(path.c_str(), O_WRONLY | O_CREAT | O_APPEND, 0644);
        if (fd_ < 0) { std::perror(("pod-log: open " + path).c_str()); return false; }
        int dfd = ::open(o_.dir.c_str(), O_RDONLY);   /* make the new directory entry durable */
        if (dfd >= 0) { fsync(dfd); close(dfd); }
        cur_name_ = name;
        cur_date_ = name.substr(7, 8);
        cur_size_ = file_size(path);
        return true;
    }

    bool rotate() {
        std::string date = utc_date(wall_ms());
        int idx = 0;
        for (const auto& n : list_logs(o_.dir))
            if (n.compare(7, 8, date) == 0) idx = std::max(idx, std::atoi(n.c_str() + 16));
        char name[64];
        std::snprintf(name, sizeof name, "events-%s-%03d.jsonl", date.c_str(), idx + 1);
        return open_file(name);
    }

    void retain() {
        auto logs = list_logs(o_.dir);
        uint64_t total = 0;
        for (const auto& n : logs) total += file_size(o_.dir + "/" + n);
        if (total <= o_.max_total) return;
        uint64_t acked = read_u64(o_.uplink_state + "/cursor");
        for (const auto& n : logs) {
            if (total <= o_.max_total || n == cur_name_) break;
            std::string path = o_.dir + "/" + n;
            uint64_t top = scan_file(path, false);
            if (top > acked) break;          /* not yet held by the ground station: keep it and everything newer */
            uint64_t sz = file_size(path);
            if (unlink(path.c_str()) == 0) {
                total -= sz;
                std::printf("pod-log: retention deleted %s (max seq %llu <= acked %llu)\n", n.c_str(),
                            static_cast<unsigned long long>(top), static_cast<unsigned long long>(acked));
            }
        }
    }

    Options o_;
    int fd_ = -1;
    std::string cur_name_, cur_date_;
    uint64_t cur_size_ = 0;
    uint64_t last_seq_ = 0;
};

}  // namespace

int main(int argc, char** argv) {
    std::setvbuf(stdout, nullptr, _IOLBF, 0);   /* line-buffered: messages reach log files immediately */
    Options o;
    if (const char* p = std::getenv("POD_ID")) o.pod_id = p;
    for (int i = 1; i + 1 < argc; i += 2) {
        std::string a = argv[i], v = argv[i + 1];
        if (a == "--pod-id") o.pod_id = v;
        else if (a == "--dir") o.dir = v;
        else if (a == "--uplink-state") o.uplink_state = v;
        else if (a == "--mq") o.mq = v;
        else if (a == "--max-file-kb") o.max_file = std::strtoull(v.c_str(), nullptr, 10) * 1024;
        else if (a == "--max-total-kb") o.max_total = std::strtoull(v.c_str(), nullptr, 10) * 1024;
        else { std::fprintf(stderr, "pod-log: unknown option %s\n", a.c_str()); return 2; }
    }
    if (!valid_pod_id(o.pod_id)) {
        std::fprintf(stderr, "pod-log: --pod-id (or POD_ID) must match ^[a-z0-9][a-z0-9-]{0,14}$, got '%s'\n", o.pod_id.c_str());
        return 2;
    }

    struct sigaction sa{};
    sa.sa_handler = on_signal;
    sigaction(SIGINT, &sa, nullptr);
    sigaction(SIGTERM, &sa, nullptr);

    Log log(o);
    if (!log.open()) return 1;
    pod::EventReceiver rx;
    if (!rx.create(o.mq.c_str())) {
        if (errno == EINVAL) {                               /* old queue with another message size */
            mq_unlink(o.mq.c_str());
            if (!rx.create(o.mq.c_str())) { std::perror("pod-log: mq_open"); return 1; }
        } else { std::perror("pod-log: mq_open"); return 1; }
    }
    pod::HealthBeat hb;
    std::printf("pod-log: %s -> %s/%s, last seq %llu%s\n", o.mq.c_str(), o.dir.c_str(), log.current().c_str(),
                static_cast<unsigned long long>(log.last_seq()), hb.attached() ? ", supervised" : "");
    std::fflush(stdout);

    uint64_t logged = 0, invalid = 0, failed = 0;
    while (!g_stop) {
        hb.beat();
        pod_event_t e;
        int r = rx.receive(&e, 500);
        if (r == 0) continue;
        if (r < 0) { std::perror("pod-log: mq_receive"); sleep(1); continue; }
        if (const char* why = invalid_reason(e); *why) {
            invalid++;
            std::fprintf(stderr, "pod-log: skipped invalid event (track %u): %s\n", e.track_id, why);
            continue;
        }
        if (log.append(e)) logged++;
        else { failed++; std::perror("pod-log: append failed"); }
    }
    log.close_file();
    std::printf("pod-log: stopped. logged=%llu invalid=%llu failed=%llu last_seq=%llu\n",
                static_cast<unsigned long long>(logged), static_cast<unsigned long long>(invalid),
                static_cast<unsigned long long>(failed), static_cast<unsigned long long>(log.last_seq()));
    return 0;
}
