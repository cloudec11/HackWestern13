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

## 2026-11-21 17:06:46 | agent-2 | P1-2 | DONE
**What:** Gate 1 AI side met: pod-infer runs on the live camera at 14.7 frames/s, infer p50 67.0 / p99 68.5 ms, e2e p50 93.6 / p99 134.1 ms
**Files:** `pod/infer/main.cpp`, `pod/infer/preprocess.h`, `pod/infer/labels.h`, `pod/infer/infer_test.cpp`, `pod/infer/CMakeLists.txt`, `pod/infer/README.md`, `models/fetch_models.sh`, `models/README.md`, `TASKS.md`
**Notes:** EfficientDet-Lite0 (TFLite Task Library build, has TFLite_Detection_PostProcess), 3 threads, run as root, Pi idle apart from agent-1's capture/log/uplink. pre (YUYV 640x480 -> RGB 320x320, no OpenCV) p50 0.95 ms; the ring slot is released before Invoke. Swapped the default model: the MediaPipe EfficientDet build has no post-processing (raw 19206 anchors), so fetch_models.sh now downloads the TF build. New benchmark: 67.1 ms avg at 3 threads (the 48.2 ms in my P0-6 entry was the MediaPipe build without post-processing, not comparable). SSD MobileNet fallback: 56.5 ms at 3 threads. Still-image check (--ppm): COCO two-cats photo -> 2 cats 0.72/0.65, TF dog photo -> dog 0.82, Grace Hopper person photo -> 0 detections (person dropped). Not yet seen: a real animal live in front of the camera. infer_test (preprocess + labels) passes on the laptop (g++) and on the Pi. Detections are printed for now; handing them to pod-track is P2-1. Handoff row to agent-1: deploy needs models/*.tflite, plus a pod.conf line. Removed agent-1's P1-1 row (used).

## 2026-11-21 17:07:45 | agent-2 | P2-1 | DOING
**What:** Claiming P2-1: pod-track (IoU tracker, one visit event per animal visit)

## 2026-11-21 17:14:28 | agent-2 | P2-1 | DONE
**What:** pod-track: IoU tracker turns per-frame detections into one visit event per animal, sent to pod-log over /pod_events. Unit and end-to-end tests pass on the Pi
**Files:** `pod/track/tracker.h`, `pod/track/dets_mq.h`, `pod/track/main.cpp`, `pod/track/track_test.cpp`, `pod/track/track_e2e.cpp`, `pod/track/CMakeLists.txt`, `pod/track/README.md`, `pod/infer/main.cpp`, `pod/infer/CMakeLists.txt`, `pod/infer/README.md`, `TASKS.md`
**Notes:** New queue /pod_dets (pod/track/dets_mq.h, my lane): pod-infer sends every frame's pod_detections_t non-blocking; pod-track creates it and never unlinks it. Tracker: greedy IoU >= 0.3, class ignored when matching, species = score-weighted vote, visit ends after 2 s unseen, < 3 frames discarded, open visits flushed on SIGTERM, up to 256 unsent events retried. --max-visit N splits long stays; off by default because splitting inflates REM encounter counts (static plush toys then give no event until removed; use --max-visit 30 for the demo if needed). Events: t_start_ms/t_end_ms Unix ms from capture times, seq/pod_id 0 for pod-log, thumb_path empty (no thumbnails yet). Tests on the Pi: track_test all passed; track_e2e PASS over private queues /a2_test_* (cat 30 frames + bird 31 frames, dog flicker ignored); live camera -> pod-infer -> pod-track 177/177 frames delivered in 12 s, 14.7 frames/s. Not yet: a real animal in front of the camera, and running under the supervisor (handoff row to agent-1 updated with pod.conf lines). Removed agent-1's P2-1 row (used).

## 2026-11-21 17:32:45 | agent-2 | P2-1 | DONE
**What:** Live end-to-end test passed: phone animal photo -> pod-capture -> pod-infer -> pod-track -> pod-log -> uplink; 3 visits stored (seq 1-3) and acked by the ground station (uplink cursor = 3)
**Notes:** 90 s run 22:30 UTC on the Pi, agent-1's supervisor/capture/log/uplink live, my pod-infer (sudo) + pod-track started by hand from ~/agent2 (not yet in pod.conf). pod-infer: 1301 frames at 14.5/s (797 older frames skipped by newest-wins; camera ~23 fps), infer p50 68.0 / p99 72.7 / max 89.3 ms, e2e p50 88.4 / p99 129.8 / max 137.9 ms, 0 frames not delivered to pod-track (1301/1301). Visits: cat 59 frames 7.9 s max 0.69; cat 50 frames 4.3 s max 0.72; plus a weak cat 3 frames 0.43 at the start, probably a false positive (3 frames exactly meets min-frames). Detections: 115 cat, 2 bird, 1 dog frames. Possible tuning: --min-frames 5 or --min-score 0.5 to cut weak false positives. Laptop IP is now 10.206.112.236 (new hotspot), Pi 10.206.112.26.

## 2026-11-21 19:44:04 | agent-2 | P3-3 | DOING
**What:** Claiming P3-3: Pod heartbeat health fields (heartbeat.json for pod-uplink)
**Notes:** Started before P3-1 is DONE with my human's OK: everything P3-3 needs from the supervisor (status.json, uplink heartbeat.json contract) already exists, and agent-3's densities stay insufficient_data until heartbeats flow. Correction to my P2-1 live-test entry: the 3 visits were acked but at first NOT stored, because the laptop API was on agent-1's old test DB (duplicate seq); agent-1 moved it to a fresh DB and they are now stored (see agent-1 log 17:32).

