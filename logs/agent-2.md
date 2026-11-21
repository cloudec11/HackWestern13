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

