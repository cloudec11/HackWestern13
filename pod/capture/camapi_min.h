/*
 * camapi_min.h -- minimal declarations for the QNX Camera Library (libcamapi) on qnxpi.
 * Owner: agent-1. Written from scratch for this project.
 *
 * Why: the Pi ships libcamapi.so but not <camera/camera_api.h>, and the camera
 * package is not in our SDP 8.0 install. Function signatures follow the public QNX
 * Camera Library documentation. Every buffer field offset below is checked on the
 * device by pod/capture/cam_probe.c before pod-capture relies on it.
 *
 * Safety design: camera_buffer_t is treated as OPAQUE. We never allocate or copy the
 * struct; we only read individual fields (by offset, via memcpy) from the pointer the
 * library passes to our callback. So a wrong guess about its total size cannot corrupt
 * memory. Link with -lcamapi.
 */
#ifndef POD_CAMAPI_MIN_H
#define POD_CAMAPI_MIN_H

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef int32_t pod_cam_handle_t;
typedef int     pod_cam_error_t;          /* 0 = success, otherwise an errno value */
#define POD_CAM_EOK 0

/* camera_open() access modes (bit flags). RO = read photo + read device settings. */
#define POD_CAM_MODE_PREAD  0x01u
#define POD_CAM_MODE_PWRITE 0x02u
#define POD_CAM_MODE_DREAD  0x04u
#define POD_CAM_MODE_DWRITE 0x08u
#define POD_CAM_MODE_RO     (POD_CAM_MODE_PREAD | POD_CAM_MODE_DREAD)

typedef struct pod_cam_buffer pod_cam_buffer_t;   /* opaque: read via the accessors below */

typedef void (*pod_cam_vf_cb)(pod_cam_handle_t handle, pod_cam_buffer_t* buf, void* arg);
typedef void (*pod_cam_status_cb)(pod_cam_handle_t handle, int devstatus, uint16_t extra, void* arg);

/* Real symbols exported by libcamapi.so (checked with nm -D on the Pi). */
pod_cam_error_t camera_open(int unit, uint32_t mode, pod_cam_handle_t* handle);
pod_cam_error_t camera_close(pod_cam_handle_t handle);
pod_cam_error_t camera_start_viewfinder(pod_cam_handle_t handle, pod_cam_vf_cb vf_cb,
                                        pod_cam_status_cb status_cb, void* arg);
pod_cam_error_t camera_stop_viewfinder(pod_cam_handle_t handle);

/* Field offsets inside camera_buffer_t (64-bit QNX 8). See cam_probe.c for the checks. */
#define POD_CAMBUF_OFF_FRAMETYPE  0    /* int32 enum; 14 = YCbYCr on this image (matches sensor log "format 14") */
#define POD_CAMBUF_OFF_FRAMESIZE  8    /* WARNING: reads 84 for 640x480 YCbYCr; NOT the pixel byte count. Do not use. */
#define POD_CAMBUF_OFF_FRAMEBUF   16   /* uint8_t*: pixel data */
#define POD_CAMBUF_OFF_TIMESTAMP  40   /* int64: capture time, microseconds (gap ~35 ms at ~28 fps) */
#define POD_CAMBUF_OFF_FRAMEDESC  72   /* union: per-format description; for YCbYCr: height, width, stride */
#define POD_CAM_FRAMETYPE_YCBYCR  14

static inline uint32_t pod_cambuf_u32(const pod_cam_buffer_t* b, size_t off) {
    uint32_t v; memcpy(&v, (const uint8_t*)b + off, sizeof v); return v;
}
static inline uint64_t pod_cambuf_u64(const pod_cam_buffer_t* b, size_t off) {
    uint64_t v; memcpy(&v, (const uint8_t*)b + off, sizeof v); return v;
}
static inline const uint8_t* pod_cambuf_ptr(const pod_cam_buffer_t* b, size_t off) {
    const uint8_t* p; memcpy(&p, (const uint8_t*)b + off, sizeof p); return p;
}

static inline int32_t        pod_cambuf_frametype(const pod_cam_buffer_t* b) { return (int32_t)pod_cambuf_u32(b, POD_CAMBUF_OFF_FRAMETYPE); }
static inline uint64_t       pod_cambuf_framesize(const pod_cam_buffer_t* b) { return pod_cambuf_u64(b, POD_CAMBUF_OFF_FRAMESIZE); }
static inline const uint8_t* pod_cambuf_framebuf(const pod_cam_buffer_t* b)  { return pod_cambuf_ptr(b, POD_CAMBUF_OFF_FRAMEBUF); }
static inline int64_t        pod_cambuf_timestamp(const pod_cam_buffer_t* b) { return (int64_t)pod_cambuf_u64(b, POD_CAMBUF_OFF_TIMESTAMP); }
/* i-th 32-bit word of the frame description union. */
static inline uint32_t       pod_cambuf_desc(const pod_cam_buffer_t* b, int i) { return pod_cambuf_u32(b, POD_CAMBUF_OFF_FRAMEDESC + 4 * (size_t)i); }
/* YCbYCr (YUYV) frame description: frame bytes = stride * height. */
static inline uint32_t       pod_cambuf_ycbycr_height(const pod_cam_buffer_t* b) { return pod_cambuf_desc(b, 0); }
static inline uint32_t       pod_cambuf_ycbycr_width(const pod_cam_buffer_t* b)  { return pod_cambuf_desc(b, 1); }
static inline uint32_t       pod_cambuf_ycbycr_stride(const pod_cam_buffer_t* b) { return pod_cambuf_desc(b, 2); }

#ifdef __cplusplus
}
#endif

#endif /* POD_CAMAPI_MIN_H */
