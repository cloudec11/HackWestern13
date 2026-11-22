/*
 * heartbeat.h -- builds the Pod health file that pod-uplink turns into /api/heartbeat. Owner: agent-2 (P3-3).
 * Header-only C++17, no TFLite. The pure parts (window, status parsing, JSON) are unit-tested
 * in infer_test on the laptop.
 *
 * pod-infer calls HealthWriter::add() per frame and maybe_write() each loop pass. Every
 * `every_ns` it writes <file> atomically with the 7 health fields of shared/event_schema.json:
 *   infer_p50_ms, infer_p99_ms, e2e_p50_ms, e2e_p99_ms   pod-infer, last 60 s of frames
 *   temp_c, restarts                                     supervisor status.json, else /dev/thermal
 *   disk_free_mb                                         statvfs of the file's directory
 * pod-uplink adds pod_id, seq, t_wall_ms, queue_depth, last_ack_seq (pod/uplink/uplink.py).
 *
 * No made-up numbers: if there were no frames in the window (camera down) or no temperature
 * reading exists, the file is REMOVED, so heartbeats stop instead of repeating stale values.
 * remove() on exit does the same. Heartbeats therefore mean "the Pod was observing".
 */
#ifndef POD_INFER_HEARTBEAT_H
#define POD_INFER_HEARTBEAT_H

#if __has_include(<sys/statvfs.h>)
#include <sys/statvfs.h>
#define POD_HAVE_STATVFS 1
#endif
#include <unistd.h>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

namespace pod {

/* Latency samples from the last window_ns, by frame time. */
class LatencyWindow {
public:
    explicit LatencyWindow(int64_t window_ns = 60000000000LL) : window_ns_(window_ns) {}

    void add(int64_t t_ns, int64_t infer_ns, int64_t e2e_ns) {
        s_.push_back({t_ns, infer_ns, e2e_ns});
        trim(t_ns);
    }
    void trim(int64_t now_ns) {
        while (!s_.empty() && now_ns - s_.front().t > window_ns_) s_.pop_front();
    }
    size_t size() const { return s_.size(); }

    /* p in [0,1]; nearest-rank on a sorted copy. Returns ms. */
    double infer_ms(double p) const { return pct(p, true); }
    double e2e_ms(double p) const { return pct(p, false); }

private:
    struct S { int64_t t, infer, e2e; };
    double pct(double p, bool infer) const {
        if (s_.empty()) return 0.0;
        std::vector<int64_t> v;
        v.reserve(s_.size());
        for (const S& x : s_) v.push_back(infer ? x.infer : x.e2e);
        std::sort(v.begin(), v.end());
        const size_t i = static_cast<size_t>(p * static_cast<double>(v.size() - 1) + 0.5);
        return static_cast<double>(v[i]) / 1e6;
    }
    int64_t window_ns_;
    std::deque<S> s_;
};

/* What the supervisor's status.json tells us. temp is NAN when absent or null. */
struct SupervisorStatus {
    bool found = false;
    int64_t t_wall_ms = 0;
    double temp_c = NAN;
    long restarts = 0;
};

/* Minimal reader for the flat top-level fields of agent-1's status.json:
 *   {"t_wall_ms":123,"temp_c":51.2|null,"restarts":0,"children":[...]} */
inline double json_number(const std::string& s, const char* key, bool* ok) {
    *ok = false;
    const std::string k = std::string("\"") + key + "\":";
    size_t p = s.find(k);
    if (p == std::string::npos) return 0.0;
    p += k.size();
    while (p < s.size() && s[p] == ' ') ++p;
    if (s.compare(p, 4, "null") == 0) return 0.0;
    char* end = nullptr;
    const double v = std::strtod(s.c_str() + p, &end);
    if (end == s.c_str() + p) return 0.0;
    *ok = true;
    return v;
}

inline SupervisorStatus parse_status(const std::string& s) {
    SupervisorStatus st;
    bool ok = false;
    st.t_wall_ms = static_cast<int64_t>(json_number(s, "t_wall_ms", &ok));
    st.found = ok;
    const double t = json_number(s, "temp_c", &ok);
    if (ok) st.temp_c = t;
    const double r = json_number(s, "restarts", &ok);
    if (ok && r >= 0) st.restarts = static_cast<long>(r);
    return st;
}

struct Health {
    double infer_p50, infer_p99, e2e_p50, e2e_p99, temp_c;
    long disk_free_mb, restarts;
};

inline std::string health_json(const Health& h) {
    char b[320];
    std::snprintf(b, sizeof b,
                  "{\"infer_p50_ms\":%.2f,\"infer_p99_ms\":%.2f,\"e2e_p50_ms\":%.2f,\"e2e_p99_ms\":%.2f,"
                  "\"temp_c\":%.1f,\"disk_free_mb\":%ld,\"restarts\":%ld}\n",
                  h.infer_p50, h.infer_p99, h.e2e_p50, h.e2e_p99, h.temp_c, h.disk_free_mb, h.restarts);
    return b;
}

class HealthWriter {
public:
    /* file = "" disables everything. */
    HealthWriter(std::string file, std::string status_file, int64_t every_ns = 10000000000LL)
        : file_(std::move(file)), status_(std::move(status_file)), every_ns_(every_ns) {}

    bool enabled() const { return !file_.empty(); }

    void add(int64_t t_ns, int64_t infer_ns, int64_t e2e_ns) {
        if (enabled()) win_.add(t_ns, infer_ns, e2e_ns);
    }

    /* Call every loop pass with CLOCK_MONOTONIC now and Unix-ms now. Writes or removes the
     * file every every_ns. Returns nullptr = not due yet, "" = written, else why it was removed. */
    const char* maybe_write(int64_t now_ns, int64_t now_wall_ms) {
        if (!enabled() || now_ns - last_ns_ < every_ns_) return nullptr;
        last_ns_ = now_ns;
        win_.trim(now_ns);
        if (win_.size() == 0) { remove(); return "no frames in the last 60 s"; }

        SupervisorStatus st;
        std::ifstream f(status_);
        if (f.is_open()) {
            std::stringstream ss;
            ss << f.rdbuf();
            st = parse_status(ss.str());
        }
        /* status.json is rewritten every 10 s; older than 60 s means the supervisor is gone. */
        const bool fresh = st.found && now_wall_ms - st.t_wall_ms < 60000;
        double temp = fresh ? st.temp_c : NAN;
        if (std::isnan(temp)) temp = read_thermal();
        if (std::isnan(temp) || temp < -40.0 || temp > 125.0) { remove(); return "no temperature reading"; }

        Health h;
        h.infer_p50 = win_.infer_ms(0.50);
        h.infer_p99 = win_.infer_ms(0.99);
        h.e2e_p50 = win_.e2e_ms(0.50);
        h.e2e_p99 = win_.e2e_ms(0.99);
        h.temp_c = temp;
        h.restarts = fresh ? st.restarts : 0;   /* no supervisor = no supervisor restarts */
        h.disk_free_mb = disk_free_mb();
        const std::string tmp = file_ + ".tmp";
        if (FILE* out = std::fopen(tmp.c_str(), "w")) {
            const std::string j = health_json(h);
            const bool ok = std::fputs(j.c_str(), out) >= 0;
            std::fclose(out);
#ifdef _WIN32
            std::remove(file_.c_str());   /* laptop test build: Windows rename does not replace */
#endif
            if (!ok || std::rename(tmp.c_str(), file_.c_str()) != 0) return "cannot write the file";
            last_ = j;
            return "";
        }
        return "cannot write the file";
    }

    void remove() {
        if (enabled()) ::unlink(file_.c_str());
        last_.clear();
    }
    const std::string& last_json() const { return last_; }
    void set_thermal_path(std::string p) { thermal_ = std::move(p); }   /* tests */

private:
    double read_thermal() const {
        std::ifstream f(thermal_);
        double t = NAN;
        if (!(f >> t)) return NAN;
        return t;
    }
    long disk_free_mb() const {
        std::string dir = file_;
        const size_t slash = dir.find_last_of('/');
        dir = slash == std::string::npos ? "." : (slash == 0 ? "/" : dir.substr(0, slash));
#ifdef POD_HAVE_STATVFS
        struct statvfs v;
        if (statvfs(dir.c_str(), &v) != 0) return 0;
        return static_cast<long>(static_cast<unsigned long long>(v.f_bavail) * v.f_frsize / (1024ull * 1024ull));
#else
        return 0;   /* laptop unit-test build only */
#endif
    }

    std::string file_, status_;
    std::string thermal_ = "/dev/thermal";
    int64_t every_ns_;
    int64_t last_ns_ = -(1LL << 62);
    LatencyWindow win_;
    std::string last_;
};

}  // namespace pod

#endif /* POD_INFER_HEARTBEAT_H */
