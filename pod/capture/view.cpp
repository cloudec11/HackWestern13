/*
 * pod-view -- live camera view with detection boxes, in a web browser. Owner: agent-1.
 * A setup/demo tool, NOT part of the core pipeline: off by default, saves nothing.
 *
 *   pod-view [--port 8080] [--fps 8] [--quality 70] [--shm /pod_frames]
 *            [--labels models/labels.csv] [--dets-in ""] [--dets-out ""]
 *   then open http://qnxpi.local:8080 (or http://<pi-ip>:8080) on the laptop.
 *
 * Frames: copies the newest frame from pod-capture's ring with Ring::peek_latest(): a
 * read-only observer that never claims a slot or takes the pulse, so pod-infer is not
 * disturbed. Run it at normal priority: capture/infer/log use SCHED_FIFO above it.
 *
 * Detections (optional), "tap" mode until pod-infer offers a read-only copy:
 *   pod-view --dets-in /pod_view_dets --dets-out /pod_dets
 *   pod-infer ... --dets-mq /pod_view_dets
 * pod-view receives each pod_detections_t, draws it, and forwards it unchanged to
 * --dets-out so pod-track still gets every frame. Without --dets-in: video only.
 *
 * HTTP:  /  page,  /stream  MJPEG,  /snapshot.jpg  one frame,  /status.json  detections + stats.
 * Privacy: frames only ever live in memory and on the wire to the browser. Nothing is written.
 */
#include <arpa/inet.h>
#include <netinet/in.h>
#include <poll.h>
#include <signal.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

#include <algorithm>
#include <atomic>
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <map>
#include <memory>
#include <mutex>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

#include "../track/dets_mq.h"
#include "pod_msgs.h"
#include "ring.h"
#include "tj_min.h"

namespace {

std::atomic<bool> g_stop{false};
void on_signal(int) { g_stop = true; }

int64_t mono_ns() {
    timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return static_cast<int64_t>(ts.tv_sec) * 1000000000LL + ts.tv_nsec;
}

struct Options {
    int port = 8080, fps = 8, quality = 70;
    std::string shm = "/pod_frames", labels = "models/labels.csv", dets_in, dets_out;
};

std::map<int, std::string> load_labels(const std::string& path) {
    std::map<int, std::string> m{{0, "unknown"}};
    std::ifstream f(path);
    std::string line;
    bool header = true;
    while (std::getline(f, line)) {
        if (line.empty() || line[0] == '#') continue;
        if (header) { header = false; continue; }          /* class_id,species,... */
        std::istringstream ss(line);
        std::string id, sp;
        if (std::getline(ss, id, ',') && std::getline(ss, sp, ',')) m[std::atoi(id.c_str())] = sp;
    }
    return m;
}

/* ---- shared state between the threads ---- */
struct State {
    std::mutex mu;
    std::condition_variable cv;
    std::vector<unsigned char> jpeg;    /* newest encoded frame */
    uint64_t jpeg_id = 0;               /* +1 per new jpeg */
    uint64_t frame_seq = 0;
    uint32_t width = 0, height = 0;
    double fps = 0;
    pod_detections_t dets{};            /* newest detections */
    int64_t dets_rx_ns = 0;             /* when any message arrived (also empty ones), 0 = never */
    int64_t dets_hit_ns = 0;            /* when dets last held a detection: boxes are kept for kHoldNs */
    uint64_t dets_rx = 0, forwarded = 0, forward_fail = 0, peek_miss = 0;
    int clients = 0;
};

/* ---- drawing ---- */
const unsigned char kColors[][3] = {{255, 200, 0}, {0, 220, 255}, {255, 80, 80}, {120, 255, 120},
                                    {255, 120, 255}, {255, 255, 255}};

void yuyv_to_rgb(const uint8_t* src, uint32_t w, uint32_t h, uint32_t stride, unsigned char* dst) {
    auto clamp = [](int v) { return static_cast<unsigned char>(v < 0 ? 0 : v > 255 ? 255 : v); };
    for (uint32_t y = 0; y < h; y++) {
        const uint8_t* s = src + static_cast<size_t>(y) * stride;
        unsigned char* d = dst + static_cast<size_t>(y) * w * 3;
        for (uint32_t x = 0; x + 1 < w; x += 2, s += 4, d += 6) {
            int u = s[1] - 128, v = s[3] - 128;
            int ru = (409 * v + 128), gu = (-100 * u - 208 * v + 128), bu = (516 * u + 128);
            for (int k = 0; k < 2; k++) {
                int c = 298 * (s[k * 2] - 16);
                d[k * 3 + 0] = clamp((c + ru) >> 8);
                d[k * 3 + 1] = clamp((c + gu) >> 8);
                d[k * 3 + 2] = clamp((c + bu) >> 8);
            }
        }
    }
}

void draw_box(unsigned char* rgb, int w, int h, const pod_detection_t& d, const unsigned char* c) {
    int x0 = static_cast<int>(d.x * w), y0 = static_cast<int>(d.y * h);
    int x1 = static_cast<int>((d.x + d.w) * w), y1 = static_cast<int>((d.y + d.h) * h);
    x0 = std::clamp(x0, 0, w - 1); x1 = std::clamp(x1, 0, w - 1);
    y0 = std::clamp(y0, 0, h - 1); y1 = std::clamp(y1, 0, h - 1);
    auto px = [&](int x, int y) { unsigned char* p = rgb + (static_cast<size_t>(y) * w + x) * 3; p[0] = c[0]; p[1] = c[1]; p[2] = c[2]; };
    for (int t = 0; t < 3; t++) {
        for (int x = x0; x <= x1; x++) { px(x, std::min(y0 + t, h - 1)); px(x, std::max(y1 - t, 0)); }
        for (int y = y0; y <= y1; y++) { px(std::min(x0 + t, w - 1), y); px(std::max(x1 - t, 0), y); }
    }
}

/* ---- threads ---- */
void frame_loop(const Options& o, State& st) {
    pod::ShmRing ring;
    std::vector<uint8_t> raw;
    std::vector<unsigned char> rgb;
    tjhandle tj = tjInitCompress();
    if (!tj) { std::fprintf(stderr, "pod-view: tjInitCompress failed\n"); g_stop = true; return; }
    uint64_t last_seq = 0;
    int64_t last_frame_ns = mono_ns(), fps_t0 = mono_ns();
    int fps_n = 0;
    const int64_t period = 1000000000LL / std::max(1, o.fps);
    bool attached = false;
    while (!g_stop) {
        if (!attached || mono_ns() - last_frame_ns > 2000000000LL) {
            /* not open yet, or no frame for 2 s (pod-capture restarted: new ring) */
            attached = ring.open(o.shm.c_str());
            if (attached) { raw.resize(ring.ring.max_payload()); last_seq = 0; }
            last_frame_ns = mono_ns();
            if (!attached) { usleep(500000); continue; }
        }
        const int64_t t0 = mono_ns();
        pod_frame_hdr_t hdr;
        if (!ring.ring.peek_latest(last_seq, &hdr, raw.data(), raw.size())) {
            std::lock_guard<std::mutex> lk(st.mu);
            if (ring.ring.published_seq() > last_seq) st.peek_miss++;
            usleep(10000);
            continue;
        }
        last_seq = hdr.seq;
        last_frame_ns = mono_ns();
        if (hdr.pixel_format != POD_PIX_YUY2) { usleep(100000); continue; }
        const int w = static_cast<int>(hdr.width), h = static_cast<int>(hdr.height);
        rgb.resize(static_cast<size_t>(w) * h * 3);
        yuyv_to_rgb(raw.data(), hdr.width, hdr.height, hdr.stride, rgb.data());

        pod_detections_t dets;
        int64_t dets_age = -1;
        {
            std::lock_guard<std::mutex> lk(st.mu);
            dets = st.dets;
            if (st.dets_rx_ns) dets_age = mono_ns() - st.dets_rx_ns;
        }
        if (dets_age >= 0 && dets_age < 1000000000LL)   /* draw boxes younger than 1 s */
            for (int i = 0; i < std::min<int>(dets.n_detections, POD_MAX_DETECTIONS); i++)
                draw_box(rgb.data(), w, h, dets.det[i], kColors[dets.det[i].class_id % 6]);

        unsigned char* jbuf = nullptr;
        unsigned long jsize = 0;
        if (tjCompress2(tj, rgb.data(), w, 0, h, TJPF_RGB, &jbuf, &jsize, TJSAMP_420, o.quality, TJFLAG_FASTDCT) != 0) {
            std::fprintf(stderr, "pod-view: jpeg: %s\n", tjGetErrorStr());
        } else {
            std::lock_guard<std::mutex> lk(st.mu);
            st.jpeg.assign(jbuf, jbuf + jsize);
            st.jpeg_id++;
            st.frame_seq = hdr.seq;
            st.width = hdr.width;
            st.height = hdr.height;
            fps_n++;
            if (mono_ns() - fps_t0 >= 2000000000LL) {
                st.fps = fps_n * 1e9 / static_cast<double>(mono_ns() - fps_t0);
                fps_n = 0;
                fps_t0 = mono_ns();
            }
        }
        if (jbuf) tjFree(jbuf);
        st.cv.notify_all();
        const int64_t spent = mono_ns() - t0;
        if (spent < period) usleep(static_cast<useconds_t>((period - spent) / 1000));
    }
    tjDestroy(tj);
}

void dets_loop(const Options& o, State& st) {
    pod::DetsReceiver rx;
    if (!rx.create(o.dets_in.c_str())) { std::perror("pod-view: dets-in queue"); return; }
    std::unique_ptr<pod::DetsSender> tx;
    if (!o.dets_out.empty()) tx = std::make_unique<pod::DetsSender>(o.dets_out.c_str());
    while (!g_stop) {
        pod_detections_t d;
        if (rx.receive(&d, 200) != 1) continue;
        bool fwd = tx && tx->send(d);           /* forward first: pod-track must not wait on drawing */
        std::lock_guard<std::mutex> lk(st.mu);
        const int64_t now = mono_ns();
        /* pod-infer sends a message for every frame, empty when nothing is seen. A weak detection (a cat on a
         * phone screen) shows up in only some frames, so an empty message would wipe its box at once and it
         * would flash for a fraction of a second. Keep the last non-empty result for kHoldNs. */
        constexpr int64_t kHoldNs = 800000000LL;
        if (d.n_detections > 0) {
            st.dets = d;
            st.dets_hit_ns = now;
        } else if (now - st.dets_hit_ns >= kHoldNs) {
            st.dets = d;
        }
        st.dets_rx_ns = now;
        st.dets_rx++;
        if (tx) (fwd ? st.forwarded : st.forward_fail)++;
    }
}

/* ---- HTTP ---- */
const char* kPage = R"HTML(<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pod live view</title>
<style>
body{margin:0;background:#111;color:#eee;font:15px system-ui,sans-serif}
main{max-width:1100px;margin:0 auto;padding:16px;display:flex;gap:16px;flex-wrap:wrap}
img{width:100%;max-width:720px;border-radius:8px;background:#000}
aside{flex:1;min-width:240px}
h1{font-size:18px;margin:0 0 8px}
.det{padding:8px 10px;margin:6px 0;background:#222;border-radius:6px;display:flex;justify-content:space-between}
.muted{color:#999;font-size:13px}
</style></head><body><main>
<div style="flex:2;min-width:300px"><img src="/stream" alt="live camera"></div>
<aside><h1>Detected now</h1><div id="dets" class="muted">waiting...</div>
<p class="muted" id="stats"></p>
<p class="muted">Live only. Nothing is saved. People are never detected.</p></aside>
</main><script>
async function tick(){
  try{
    const s=await (await fetch('/status.json',{cache:'no-store'})).json();
    const d=document.getElementById('dets');
    if(!s.dets_source){d.textContent='video only (no detection feed)';}
    else if(!s.detections.length){d.textContent='nothing right now';}
    else d.innerHTML=s.detections.map(x=>`<div class="det"><b>${x.species}</b><span>${Math.round(x.score*100)}%</span></div>`).join('');
    document.getElementById('stats').textContent=
      `${s.width}x${s.height} at ${s.fps.toFixed(1)} fps shown`+
      (s.e2e_ms>=0?` | camera to detection ${s.e2e_ms.toFixed(0)} ms`:'');
  }catch(e){}
  setTimeout(tick,500);
}
tick();
</script></body></html>)HTML";

bool send_all(int fd, const void* p, size_t n) {
    const char* c = static_cast<const char*>(p);
    while (n) {
        ssize_t w = send(fd, c, n, 0);
        if (w <= 0) return false;
        c += w;
        n -= static_cast<size_t>(w);
    }
    return true;
}

bool send_text(int fd, const char* type, const std::string& body) {
    std::string h = "HTTP/1.0 200 OK\r\nContent-Type: " + std::string(type) +
                    "\r\nCache-Control: no-store\r\nAccess-Control-Allow-Origin: *\r\nContent-Length: " + std::to_string(body.size()) + "\r\n\r\n";
    return send_all(fd, h.data(), h.size()) && send_all(fd, body.data(), body.size());
}

std::string status_json(State& st, const std::map<int, std::string>& labels, bool tap) {
    std::lock_guard<std::mutex> lk(st.mu);
    const int64_t age = st.dets_rx_ns ? mono_ns() - st.dets_rx_ns : -1;
    const bool fresh = age >= 0 && age < 1000000000LL;
    char b[256];
    std::snprintf(b, sizeof b,
                  "{\"frame_seq\":%llu,\"width\":%u,\"height\":%u,\"fps\":%.2f,\"jpeg_bytes\":%zu,\"dets_source\":%s,"
                  "\"dets_received\":%llu,\"forwarded\":%llu,\"forward_fail\":%llu,\"peek_miss\":%llu,\"e2e_ms\":%.1f,\"detections\":[",
                  static_cast<unsigned long long>(st.frame_seq), st.width, st.height, st.fps, st.jpeg.size(),
                  tap ? "\"tap\"" : "null", static_cast<unsigned long long>(st.dets_rx),
                  static_cast<unsigned long long>(st.forwarded), static_cast<unsigned long long>(st.forward_fail),
                  static_cast<unsigned long long>(st.peek_miss),
                  fresh ? (st.dets.t_infer_done_ns - st.dets.t_capture_ns) / 1e6 : -1.0);
    std::string s = b;
    if (fresh)
        for (int i = 0; i < std::min<int>(st.dets.n_detections, POD_MAX_DETECTIONS); i++) {
            const pod_detection_t& d = st.dets.det[i];
            auto it = labels.find(d.class_id);
            std::snprintf(b, sizeof b, "%s{\"class_id\":%u,\"species\":\"%s\",\"score\":%.3f,\"x\":%.3f,\"y\":%.3f,\"w\":%.3f,\"h\":%.3f}",
                          i ? "," : "", d.class_id, it != labels.end() ? it->second.c_str() : "unknown",
                          d.score, d.x, d.y, d.w, d.h);
            s += b;
        }
    return s + "]}";
}

void serve_client(int fd, State& st, const std::map<int, std::string>& labels, bool tap) {
    timeval tv{5, 0};
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof tv);
    char req[2048];
    ssize_t n = recv(fd, req, sizeof req - 1, 0);
    if (n <= 0) { close(fd); return; }
    req[n] = 0;
    char method[8] = {}, path[256] = {};
    std::sscanf(req, "%7s %255s", method, path);
    std::string p = path;
    if (auto q = p.find('?'); q != std::string::npos) p.erase(q);

    if (p == "/") {
        send_text(fd, "text/html; charset=utf-8", kPage);
    } else if (p == "/status.json") {
        send_text(fd, "application/json", status_json(st, labels, tap));
    } else if (p == "/snapshot.jpg") {
        std::vector<unsigned char> j;
        { std::lock_guard<std::mutex> lk(st.mu); j = st.jpeg; }
        if (j.empty()) send_text(fd, "text/plain", "no frame yet\n");
        else send_text(fd, "image/jpeg", std::string(j.begin(), j.end()));
    } else if (p == "/stream") {
        {
            std::lock_guard<std::mutex> lk(st.mu);
            if (st.clients >= 4) { send_text(fd, "text/plain", "too many viewers\n"); close(fd); return; }
            st.clients++;
        }
        const char* h = "HTTP/1.0 200 OK\r\nCache-Control: no-store\r\n"
                        "Content-Type: multipart/x-mixed-replace; boundary=podframe\r\n\r\n";
        bool ok = send_all(fd, h, std::strlen(h));
        uint64_t seen = 0;
        while (ok && !g_stop) {
            std::vector<unsigned char> j;
            {
                std::unique_lock<std::mutex> lk(st.mu);
                st.cv.wait_for(lk, std::chrono::seconds(2), [&] { return st.jpeg_id != seen || g_stop.load(); });
                if (st.jpeg_id == seen) continue;
                seen = st.jpeg_id;
                j = st.jpeg;
            }
            std::string part = "--podframe\r\nContent-Type: image/jpeg\r\nContent-Length: " + std::to_string(j.size()) + "\r\n\r\n";
            ok = send_all(fd, part.data(), part.size()) && send_all(fd, j.data(), j.size()) && send_all(fd, "\r\n", 2);
        }
        std::lock_guard<std::mutex> lk(st.mu);
        st.clients--;
    } else {
        const char* nf = "HTTP/1.0 404 Not Found\r\nContent-Length: 10\r\n\r\nnot found\n";
        send_all(fd, nf, std::strlen(nf));
    }
    close(fd);
}

}  // namespace

int main(int argc, char** argv) {
    std::setvbuf(stdout, nullptr, _IOLBF, 0);
    Options o;
    for (int i = 1; i + 1 < argc; i += 2) {
        std::string a = argv[i], v = argv[i + 1];
        if (a == "--port") o.port = std::atoi(v.c_str());
        else if (a == "--fps") o.fps = std::atoi(v.c_str());
        else if (a == "--quality") o.quality = std::atoi(v.c_str());
        else if (a == "--shm") o.shm = v;
        else if (a == "--labels") o.labels = v;
        else if (a == "--dets-in") o.dets_in = v;
        else if (a == "--dets-out") o.dets_out = v;
        else { std::fprintf(stderr, "pod-view: unknown option %s\n", a.c_str()); return 2; }
    }
    signal(SIGPIPE, SIG_IGN);
    struct sigaction sa{};
    sa.sa_handler = on_signal;
    sigaction(SIGINT, &sa, nullptr);
    sigaction(SIGTERM, &sa, nullptr);

    const auto labels = load_labels(o.labels);
    int ls = socket(AF_INET, SOCK_STREAM, 0);
    int one = 1;
    setsockopt(ls, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(static_cast<uint16_t>(o.port));
    addr.sin_addr.s_addr = htonl(INADDR_ANY);
    if (ls < 0 || bind(ls, reinterpret_cast<sockaddr*>(&addr), sizeof addr) != 0 || listen(ls, 8) != 0) {
        std::perror("pod-view: listen");
        return 1;
    }

    State st;
    const bool tap = !o.dets_in.empty();
    std::thread frames(frame_loop, std::cref(o), std::ref(st));
    std::thread dets;
    if (tap) dets = std::thread(dets_loop, std::cref(o), std::ref(st));
    std::printf("pod-view: http://<pi>:%d  (%d fps max, quality %d, %zu labels, detections %s%s)\n", o.port, o.fps,
                o.quality, labels.size(), tap ? ("from " + o.dets_in).c_str() : "off",
                o.dets_out.empty() ? "" : (", forwarded to " + o.dets_out).c_str());

    while (!g_stop) {
        pollfd pf{ls, POLLIN, 0};
        if (poll(&pf, 1, 500) <= 0) continue;
        int c = accept(ls, nullptr, nullptr);
        if (c < 0) continue;
        std::thread(serve_client, c, std::ref(st), std::cref(labels), tap).detach();
    }
    close(ls);
    st.cv.notify_all();
    frames.join();
    if (dets.joinable()) dets.join();
    for (int i = 0; i < 40; i++) {           /* let streaming clients see g_stop before st goes away */
        { std::lock_guard<std::mutex> lk(st.mu); if (st.clients == 0) break; }
        st.cv.notify_all();
        usleep(50000);
    }
    std::printf("pod-view: stopped\n");
    return 0;
}
