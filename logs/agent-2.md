# Agent 2 log

Append-only. Written by agent-2 only.

## 2026-11-21 14:42:18 | agent-2 | P0-6 | DOING
**What:** Claiming P0-6: get TFLite running on the Pi (QNX 8, aarch64)
**Notes:** Also reviewed shared/pod_msgs.h v1 for P0-4: pod_detection_t / pod_detections_t fit pod-infer output (box from ymin/xmin/ymax/xmax converts to x,y,w,h; 16 dets/frame is enough). event_schema.json species pattern ^[a-z][a-z_]*$ noted; label table in models/ will use compact ids, 0=unknown, no person. No contract change needed from agent-2.

## 2026-11-21 15:19:35 | agent-2 | P0-6 | DOING
**What:** TFLite on QNX: building qnx-ports/tensorflow (qnx_v2.16.1) natively on the Pi, configure in progress
**Notes:** No ML libs on the Pi, but clang/cmake/ninja/git/python3.14 are native and /data has 17G free, 4x A76, 7G free RAM. qnx-ports/build-files has a TFLite port (XNNPACK, cpuinfo, ruy, abseil, eigen QNX forks) meant for SDP cross-compile; reusing its flags/patches with a native toolchain file at ~/tflite/qnx-native.cmake on the Pi. Build dir ~/tflite/build (outside the repo). First configure was cut off when the laptop dropped off the hotspot; rerunning under nohup. qnxpi.local and the old IP 172.20.10.11 both went unreachable at the same time.

## 2026-11-21 15:54:52 | agent-2 | P0-6 | DOING
**What:** Label table v1 + model fetch script in models/
**Files:** `models/labels.csv`, `models/fetch_models.sh`, `models/README.md`, `models/.gitignore`
**Notes:** labels.csv: class_id,species,model_index. Pod ids are compact: 0 unknown, 1 bird, 2 cat, 3 dog, 4 horse, 5 sheep, 6 cow, 7 elephant, 8 bear, 9 zebra, 10 giraffe. No person. pod-infer drops any model index not listed. agent-1 (pod-uplink): read species from this file. agent-3: the class_id values in event_schema.json examples (19 sheep, 15 bird) are raw model indices; real Pod ids are the compact ones above. Candidate models: EfficientDet-Lite0 int8 320x320 (default) and SSD MobileNet v1 quant 300x300 (fallback); both COCO, same index numbering. Binaries are git-ignored; run sh models/fetch_models.sh.

## 2026-11-21 15:57:43 | agent-2 | P0-6 | DOING
**What:** TFLite build restarted clean on the Pi after two configure fixes
**Notes:** Fix 1: the FP16 dependency download had failed during a Wi-Fi drop and CMake cached the empty path. Downloaded FP16 by hand to ~/tflite/deps/FP16 and passed -DFP16_SOURCE_DIR. Fix 2: the Pi has CMake 4.4.2, which rejects deps with cmake_minimum_required < 3.5. Export CMAKE_POLICY_VERSION_MINIMUM=3.5 as an environment variable (the -D flag alone does not reach dependency sub-builds). agent-1: you will hit this too with any old CMake project on the Pi. Build script: ~/tflite/build.sh, progress in ~/tflite/status.log.

## 2026-11-21 16:04:41 | agent-2 | P0-6 | DOING
**What:** Label table: added teddy_bear (class 11, demo_only=1) for the plush demo set
**Files:** `models/labels.csv`, `models/README.md`, `TASKS.md`
**Notes:** Human decision: plush animals count in the demo. New column demo_only (1 = not a real species). agent-3: exclude demo_only classes from density and simulation. Handoff row added to TASKS.md for agent-3 and agent-1. Read agent-4's Q2 row (split train/test by clip for P3-6); keeping it until P3-6.

## 2026-11-21 16:56:11 | agent-2 | P0-6 | DONE
**What:** Gate 0 AI half met: TFLite 2.16.1 + XNNPACK runs on the Pi. EfficientDet-Lite0 at 48.2 ms avg / 50.6 ms max with 3 threads (~20/s)
**Files:** `pod/infer/tflite/build_tflite.sh`, `pod/infer/tflite/qnx-native.cmake`, `pod/infer/tflite/elementwise_abs.patch`, `pod/infer/tflite/README.md`, `pod/infer/.gitattributes`, `models/README.md`, `models/.gitattributes`, `TASKS.md`
**Notes:** benchmark_model, 100 runs, idle Pi, inference only. EfficientDet-Lite0 int8: 1t 132.2 ms, 2t 69.7 ms, 3t 48.2 ms (min 47.6, max 50.6, std 0.4), 4t 287.8 ms (thread pool contention, avoid). SSD MobileNet v1 quant: 1t 164.6, 2t 83.3, 4t 106.6 ms. Choice: EfficientDet-Lite0, 3 threads, leaving one core for capture/track/log/uplink. CPU shows 1.5 GHz in pidin. TFLite+XNNPACK needs I/O privilege on QNX (cpuinfo ThreadCtl _NTO_TCTL_IO_PRIV); numbers taken with sudo. Handoff row for agent-1 (pod.conf must start pod-infer privileged). Build fixes: CMAKE_SYSTEM_PROCESSOR=aarch64 (native QNX reports aarch64le, which dropped XNNPACK NEON kernels and broke cpuinfo), CMake 4 policy env var, FP16 manual fetch, std::abs<float> patch. Recipe in pod/infer/tflite/; built library at ~/tflite/build on the Pi. build_tflite.sh packages the hand-run steps but has not been run from scratch. Read agent-1's P1-1/P2-1/P3-1/P3-3 handoff rows; keeping them until those tasks start.

## 2026-11-21 16:57:47 | agent-2 | P1-2 | DOING
**What:** Claiming P1-2: pod-infer (newest frame -> YUYV to RGB -> EfficientDet-Lite0 -> detections with t_capture / t_infer_done)

