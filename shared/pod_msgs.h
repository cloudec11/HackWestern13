/*
 * pod_msgs.h  --  v1 (DRAFT, pending P0-4 sign-off by all four agents)
 *
 * Contract between Pod processes (pod-capture, pod-infer, pod-track, pod-log,
 * pod-uplink, pod-supervisor). Plain C structs, usable from C and C++.
 * Changes go through the contract change process in AGENTS.md section 6.
 *
 * Conventions
 *  - All integers are fixed width. Structs are naturally aligned, with explicit
 *    reserved fields instead of implicit padding. Sizes are checked below.
 *  - Byte order is native (little endian on Raspberry Pi 5 / aarch64le). These
 *    structs never leave the Pod. The uplink turns events into JSON per
 *    shared/event_schema.json.
 *  - t_*_ns   = CLOCK_MONOTONIC nanoseconds. Use for latency maths only.
 *    t_*_ms   = Unix wall-clock milliseconds. Use for anything the ground station sees.
 *  - Bounding boxes are normalised to 0..1 of the frame, origin top-left.
 *  - PRIVACY: person detections are dropped in pod-infer. No struct below may
 *    carry a "person" class, and thumbnails are never written for one.
 */
#ifndef POD_MSGS_H
#define POD_MSGS_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#define POD_STATIC_ASSERT(c, m) static_assert(c, m)
#else
#define POD_STATIC_ASSERT(c, m) _Static_assert(c, m)
#endif

#define POD_MSGS_VERSION      1

/* ---- Limits ------------------------------------------------------------ */
#define POD_FRAME_SLOTS       4      /* ring buffer slots between capture and infer */
#define POD_MAX_DETECTIONS    16     /* per frame */
#define POD_ID_LEN            16     /* NUL-terminated pod id, e.g. "pod-01" */
#define POD_THUMB_PATH_LEN    64     /* thumbnail file name, relative to the log dir */

/* ---- Magic numbers (sanity check when reading shared memory) ----------- */
#define POD_FRAME_MAGIC       0x50464D31u   /* 'PFM1' */
#define POD_DETS_MAGIC        0x50444531u   /* 'PDE1' */

/* ---- Pixel formats of the frame payload -------------------------------- */
enum pod_pixel_format {
    POD_PIX_UNKNOWN = 0,
    POD_PIX_NV12    = 1,   /* Y plane then interleaved UV, 4:2:0 */
    POD_PIX_YUY2    = 2,   /* packed 4:2:2 */
    POD_PIX_RGB24   = 3
};

/* ---- Class ids ----------------------------------------------------------
 * The label table lives with the model in models/ (agent-2) and is the source
 * of truth. 0 is reserved for "unknown". There is deliberately no person id.
 */
#define POD_CLASS_UNKNOWN     0

/* ---- 1. Frame header ----------------------------------------------------
 * Sits at the start of every ring buffer slot, followed by data_size bytes of
 * pixels. pod-capture writes it and pulses pod-infer. 64 bytes.
 */
typedef struct {
    uint32_t magic;          /* POD_FRAME_MAGIC */
    uint16_t version;        /* POD_MSGS_VERSION */
    uint16_t pixel_format;   /* enum pod_pixel_format */
    uint64_t seq;            /* frame counter, starts at 1, +1 per captured frame */
    int64_t  t_capture_ns;   /* CLOCK_MONOTONIC when the camera callback fired */
    uint32_t width;
    uint32_t height;
    uint32_t stride;         /* bytes per row of the first plane */
    uint32_t data_size;      /* payload bytes following this header */
    uint32_t slot;           /* ring slot index, 0..POD_FRAME_SLOTS-1 */
    uint32_t reserved[5];
} pod_frame_hdr_t;
POD_STATIC_ASSERT(sizeof(pod_frame_hdr_t) == 64, "pod_frame_hdr_t must be 64 bytes");

/* ---- 2. Detection --------------------------------------------------------
 * One detected animal in one frame. 32 bytes.
 */
typedef struct {
    float    x, y, w, h;     /* normalised box: top-left corner plus size */
    float    score;          /* 0..1 */
    uint32_t track_id;       /* 0 until pod-track assigns one */
    uint16_t class_id;       /* see label table in models/ */
    uint16_t reserved16;
    uint32_t reserved32;
} pod_detection_t;
POD_STATIC_ASSERT(sizeof(pod_detection_t) == 32, "pod_detection_t must be 32 bytes");

/* ---- 2b. Detection result -------------------------------------------------
 * pod-infer output for one frame, consumed by pod-track. Carries both
 * timestamps so end-to-end latency is t_infer_done_ns - t_capture_ns.
 * 32-byte header plus POD_MAX_DETECTIONS detections = 544 bytes.
 */
typedef struct {
    uint32_t magic;            /* POD_DETS_MAGIC */
    uint16_t n_detections;     /* valid entries in det[], 0..POD_MAX_DETECTIONS */
    uint16_t reserved16;
    uint64_t frame_seq;        /* pod_frame_hdr_t.seq this result belongs to */
    int64_t  t_capture_ns;     /* copied from the frame header */
    int64_t  t_infer_done_ns;  /* CLOCK_MONOTONIC when inference finished */
    pod_detection_t det[POD_MAX_DETECTIONS];
} pod_detections_t;
POD_STATIC_ASSERT(sizeof(pod_detections_t) == 544, "pod_detections_t must be 544 bytes");

/* ---- 3. Visit event ------------------------------------------------------
 * One record per animal visit, written by pod-track, stored by pod-log and
 * sent by pod-uplink. (pod_id, seq) is the idempotency key on the ground
 * station: resending the same event must be harmless. 120 bytes.
 */
typedef struct {
    uint64_t seq;              /* per-pod event counter, starts at 1, never reused */
    int64_t  t_start_ms;       /* visit start, Unix ms */
    int64_t  t_end_ms;         /* visit end, Unix ms */
    float    max_score;        /* best detection score during the visit */
    uint32_t track_id;
    uint32_t n_frames;         /* frames in which the animal was seen */
    uint16_t class_id;
    uint16_t reserved16;
    char     pod_id[POD_ID_LEN];
    char     thumb_path[POD_THUMB_PATH_LEN];  /* "" if no thumbnail */
} pod_event_t;
POD_STATIC_ASSERT(sizeof(pod_event_t) == 120, "pod_event_t must be 120 bytes");

/* ---- 4. Heartbeat --------------------------------------------------------
 * Health summary built by the supervisor path and sent by pod-uplink. 72 bytes.
 */
typedef struct {
    char     pod_id[POD_ID_LEN];
    uint64_t seq;              /* heartbeat counter */
    int64_t  t_wall_ms;        /* Unix ms */
    float    infer_p50_ms;     /* inference latency percentiles over the last window */
    float    infer_p99_ms;
    float    e2e_p50_ms;       /* capture-to-detection latency percentiles */
    float    e2e_p99_ms;
    float    temp_c;           /* SoC temperature */
    uint32_t disk_free_mb;
    uint32_t restarts;         /* supervisor-triggered process restarts since boot */
    uint32_t queue_depth;      /* events logged but not yet acked */
    uint64_t last_ack_seq;     /* highest event seq acked by the ground station */
} pod_heartbeat_t;
POD_STATIC_ASSERT(sizeof(pod_heartbeat_t) == 72, "pod_heartbeat_t must be 72 bytes");

#ifdef __cplusplus
}
#endif

#endif /* POD_MSGS_H */
