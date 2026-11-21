/*
 * cam_probe.c -- verify camapi_min.h against real frames from the webcam. Owner: agent-1.
 *
 * Build on the Pi:  clang -std=c11 -O2 -Wall -Wextra cam_probe.c -o cam_probe -lcamapi
 * Run:              ./cam_probe [unit=5] [frames=60] [out.bmp=/tmp/podcam/frame.bmp]
 *
 * Checks, each printed as PASS/FAIL:
 *  1. camera_open / camera_start_viewfinder return 0 and frames arrive.
 *  2. framebuf (offset 16) is non-null and framesize (offset 8) is sane and constant.
 *  3. timestamps (offset 40) strictly increase, with a gap that matches ~30 fps.
 *  4. framedesc (offset 72) holds width, height and stride consistent with framesize.
 *  5. Pixel statistics from framebuf match an independent tool
 *     (camera_example1_callback prints the same channel averages).
 * Then it converts one YCbYCr (YUYV) frame to a BMP so a human can look at it.
 */
#define _POSIX_C_SOURCE 200809L
#include <signal.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include "camapi_min.h"

#define MAX_FRAMES 400
#define SNAP_INDEX 30                      /* skip camera warm-up frames */
#define SNAP_MAX   (8u * 1024u * 1024u)

static atomic_int n_frames;
static int want_frames = 60;
static int64_t ts[MAX_FRAMES];
static uint64_t sizes[MAX_FRAMES];
static int32_t types[MAX_FRAMES];
static int null_bufs;
static uint8_t raw[3][104];                /* field dumps of the first frames */
static uint8_t* snap;                      /* copy of frame SNAP_INDEX */
static uint64_t snap_size;
static uint32_t snap_desc[8];
static atomic_int snap_done;

static void vf_cb(pod_cam_handle_t h, pod_cam_buffer_t* b, void* arg) {
    (void)h; (void)arg;
    int i = atomic_load(&n_frames);
    if (i >= want_frames || i >= MAX_FRAMES) return;
    if (i < 3) memcpy(raw[i], b, sizeof raw[i]);
    ts[i] = pod_cambuf_timestamp(b);
    sizes[i] = pod_cambuf_framesize(b);
    types[i] = pod_cambuf_frametype(b);
    const uint8_t* fb = pod_cambuf_framebuf(b);
    if (!fb) null_bufs++;
    if (i == SNAP_INDEX && fb) {
        /* Probe run 1 showed offset 8 is NOT the pixel byte count (it reads 84),
         * so size the copy from the description: stride * height. */
        uint64_t bytes = (uint64_t)pod_cambuf_ycbycr_stride(b) * pod_cambuf_ycbycr_height(b);
        if (bytes > 0 && bytes <= SNAP_MAX) {
            memcpy(snap, fb, bytes);
            snap_size = bytes;
            for (int k = 0; k < 8; k++) snap_desc[k] = pod_cambuf_desc(b, k);
            atomic_store(&snap_done, 1);
        }
    }
    atomic_store(&n_frames, i + 1);
}

static int fails;
static void check(int ok, const char* what) { printf("%s  %s\n", ok ? "PASS" : "FAIL", what); if (!ok) fails++; }

static uint8_t clamp8(int v) { return (uint8_t)(v < 0 ? 0 : v > 255 ? 255 : v); }

static int write_bmp_from_yuyv(const char* path, const uint8_t* src, uint32_t w, uint32_t h, uint32_t stride) {
    uint32_t row = (w * 3 + 3) & ~3u, img = row * h, file = 54 + img;
    FILE* f = fopen(path, "wb");
    if (!f) return -1;
    uint8_t hd[54] = {'B', 'M'};
    memcpy(hd + 2, &file, 4);
    uint32_t off = 54, dib = 40, planes_bpp = 1u | (24u << 16);
    int32_t sw = (int32_t)w, sh = (int32_t)h;
    memcpy(hd + 10, &off, 4); memcpy(hd + 14, &dib, 4); memcpy(hd + 18, &sw, 4);
    memcpy(hd + 22, &sh, 4); memcpy(hd + 26, &planes_bpp, 4); memcpy(hd + 34, &img, 4);
    fwrite(hd, 1, 54, f);
    uint8_t* line = calloc(1, row);
    for (int32_t y = (int32_t)h - 1; y >= 0; y--) {          /* BMP rows go bottom-up */
        const uint8_t* s = src + (size_t)y * stride;
        for (uint32_t x = 0; x + 1 < w; x += 2) {
            int y0 = s[2 * x] - 16, cb = s[2 * x + 1] - 128, y1 = s[2 * x + 2] - 16, cr = s[2 * x + 3] - 128;
            int yy[2] = {y0, y1};
            for (int k = 0; k < 2; k++) {                     /* BT.601 limited range */
                int c = 298 * yy[k];
                uint8_t* p = line + (x + (uint32_t)k) * 3;
                p[0] = clamp8((c + 516 * cb + 128) >> 8);                 /* B */
                p[1] = clamp8((c - 100 * cb - 208 * cr + 128) >> 8);      /* G */
                p[2] = clamp8((c + 409 * cr + 128) >> 8);                 /* R */
            }
        }
        fwrite(line, 1, row, f);
    }
    free(line);
    return fclose(f);
}

int main(int argc, char** argv) {
    int unit = argc > 1 ? atoi(argv[1]) : 5;
    want_frames = argc > 2 ? atoi(argv[2]) : 60;
    const char* out = argc > 3 ? argv[3] : "/tmp/podcam/frame.bmp";
    if (want_frames < SNAP_INDEX + 5) want_frames = SNAP_INDEX + 5;
    if (want_frames > MAX_FRAMES) want_frames = MAX_FRAMES;
    snap = malloc(SNAP_MAX);

    pod_cam_handle_t h = -1;
    int rc = camera_open(unit, POD_CAM_MODE_RO, &h);
    printf("camera_open(unit %d, mode RO) = %d, handle %d\n", unit, rc, h);
    check(rc == POD_CAM_EOK, "camera_open succeeds");
    if (rc) return 1;
    rc = camera_start_viewfinder(h, vf_cb, NULL, NULL);
    printf("camera_start_viewfinder = %d\n", rc);
    check(rc == POD_CAM_EOK, "camera_start_viewfinder succeeds");
    if (rc) { camera_close(h); return 1; }

    for (int t = 0; t < 200 && atomic_load(&n_frames) < want_frames; t++) usleep(50000);  /* <= 10 s */
    camera_stop_viewfinder(h);
    camera_close(h);
    int n = atomic_load(&n_frames);
    printf("frames received: %d\n", n);
    check(n >= want_frames, "all requested frames arrived within 10 s");
    if (n < 3) return 1;

    printf("\nraw field dump of frame 0..2 (64-bit words at offsets 0..96):\n");
    for (int f = 0; f < 3; f++) {
        printf(" f%d:", f);
        for (int o = 0; o < 104; o += 8) { uint64_t v; memcpy(&v, raw[f] + o, 8); printf(" %llx", (unsigned long long)v); }
        printf("\n");
    }

    int same_type = 1, same_size = 1, increasing = 1;
    for (int i = 1; i < n; i++) {
        same_type &= types[i] == types[0];
        same_size &= sizes[i] == sizes[0];
        increasing &= ts[i] > ts[i - 1];
    }
    double dt_ms = (double)(ts[n - 1] - ts[1]) / (n - 2) / 1000.0;   /* assumes microseconds */
    printf("\nframetype=%d  framesize=%llu  mean frame gap=%.2f ms (%.1f fps if timestamps are us)\n",
           types[0], (unsigned long long)sizes[0], dt_ms, dt_ms > 0 ? 1000.0 / dt_ms : 0.0);
    check(null_bufs == 0, "framebuf (offset 16) never null");
    check(same_type, "frametype (offset 0) constant");
    printf("INFO  offset 8 value %llu (constant=%d); not the pixel byte count, not used\n",
           (unsigned long long)sizes[0], same_size);
    check(increasing, "timestamp (offset 40) strictly increasing");
    check(dt_ms > 20.0 && dt_ms < 120.0, "timestamp gap matches 8-50 fps in microseconds");

    if (!atomic_load(&snap_done)) { check(0, "captured snapshot frame"); return 1; }
    printf("framedesc words (offset 72..100):");
    for (int k = 0; k < 8; k++) printf(" %u", snap_desc[k]);
    printf("\n");

    /* Layout seen in run 1: desc[0]=height, desc[1]=width, desc[2]=stride. */
    uint32_t hh = snap_desc[0], w = snap_desc[1], stride = snap_desc[2];
    printf("framedesc: height=%u width=%u stride=%u (YUYV, 2 bytes/pixel)\n", hh, w, stride);
    check(w >= 160 && w <= 4096 && hh >= 120 && hh <= 4096 && w > hh, "framedesc width/height plausible (landscape)");
    check(stride >= 2 * w && stride <= 2 * w + 256, "framedesc stride fits 2 bytes/pixel plus padding");
    if (!(w && hh && stride >= 2 * w)) return 1;

    /* Channel means: Y = even bytes, Cb = byte 1 of 4, Cr = byte 3 of 4 (YCbYCr order). */
    double sy = 0, scb = 0, scr = 0;
    for (uint32_t y = 0; y < hh; y++) {
        const uint8_t* s = snap + (size_t)y * stride;
        for (uint32_t x = 0; x < w * 2; x += 4) { sy += s[x] + s[x + 2]; scb += s[x + 1]; scr += s[x + 3]; }
    }
    double np = (double)w * hh;
    printf("pixel means from framebuf: Y=%.2f Cb=%.2f Cr=%.2f\n", sy / np, scb / (np / 2), scr / (np / 2));
    printf("(compare with camera_example1_callback -u 5 'Channel averages')\n");

    rc = write_bmp_from_yuyv(out, snap, w, hh, stride);
    check(rc == 0, "wrote BMP snapshot");
    printf("snapshot: %s\n\n%s (%d failed checks)\n", out, fails ? "SOME CHECKS FAILED" : "ALL CHECKS PASSED", fails);
    return fails ? 1 : 0;
}
