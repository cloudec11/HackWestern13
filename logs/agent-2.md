# Agent 2 log

Append-only. Written by agent-2 only.

## 2026-11-21 14:42:18 | agent-2 | P0-6 | DOING
**What:** Claiming P0-6: get TFLite running on the Pi (QNX 8, aarch64)
**Notes:** Also reviewed shared/pod_msgs.h v1 for P0-4: pod_detection_t / pod_detections_t fit pod-infer output (box from ymin/xmin/ymax/xmax converts to x,y,w,h; 16 dets/frame is enough). event_schema.json species pattern ^[a-z][a-z_]*$ noted; label table in models/ will use compact ids, 0=unknown, no person. No contract change needed from agent-2.

## 2026-11-21 15:19:35 | agent-2 | P0-6 | DOING
**What:** TFLite on QNX: building qnx-ports/tensorflow (qnx_v2.16.1) natively on the Pi, configure in progress
**Notes:** No ML libs on the Pi, but clang/cmake/ninja/git/python3.14 are native and /data has 17G free, 4x A76, 7G free RAM. qnx-ports/build-files has a TFLite port (XNNPACK, cpuinfo, ruy, abseil, eigen QNX forks) meant for SDP cross-compile; reusing its flags/patches with a native toolchain file at ~/tflite/qnx-native.cmake on the Pi. Build dir ~/tflite/build (outside the repo). First configure was cut off when the laptop dropped off the hotspot; rerunning under nohup. qnxpi.local and the old IP 172.20.10.11 both went unreachable at the same time.

