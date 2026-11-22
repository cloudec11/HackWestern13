# pod-infer (agent-2, P1-2)

Reads the newest camera frame from `pod-capture`'s shared-memory ring, runs the TFLite detector, and keeps only the animals listed in `models/labels.csv`. Person is never in that table, so person detections are dropped here and never stored.

## Build and run (on the Pi)

You need TFLite built once with `pod/infer/tflite/build_tflite.sh` (see `tflite/README.md`), and the model file in `models/` (`sh models/fetch_models.sh`).

```
cmake -S pod/infer -B build/infer && cmake --build build/infer
sudo ./build/infer/pod-infer --stats 5            # needs root: see "Permissions" in models/README.md
sudo ./build/infer/pod-infer --ppm photo.ppm      # one still image (binary PPM), then exit
./build/infer/infer_test models/labels.csv        # unit tests, no TFLite needed (also runs on the laptop)
```

Start `pod-capture` first. `pod-infer` waits up to 5 s for the ring `/pod_frames`.

| Option | Default | Meaning |
|--------|---------|---------|
| `--model` | `models/efficientdet_lite0.tflite` | Detector. Must end in `TFLite_Detection_PostProcess`. |
| `--labels` | `models/labels.csv` | Label table |
| `--shm` | `/pod_frames` | Frame ring name |
| `--dets-mq` | `/pod_dets` | Queue to pod-track (`""` = don't send) |
| `--threads` | 3 | Inference threads. 4 is much slower on the Pi. |
| `--prio` | 0 | SCHED_FIFO priority (0 = leave as is). Inference threads inherit it. |
| `--min-score` | 0.4 | Drop detections below this score |
| `--seconds` | 0 | Stop after N seconds (0 = run until Ctrl-C) |
| `--stats` | 5 | Print a latency summary every N seconds (0 = only at exit) |
| `--quiet` | off | Don't print a line for each frame with detections |
| `--health-out` | off | Write the heartbeat health file here every 10 s (P3-3). Use `pod-data/uplink/heartbeat.json`. |
| `--status` | `pod-data/supervisor/status.json` | Supervisor status file: source of `temp_c` and `restarts` |
| `--observe` | off | Read frames as a read-only observer (`Ring::peek_latest`), so a second pod-infer can run next to the real one for tests. Use with `--dets-mq ""`. It competes for CPU, so keep such tests short. |

## Heartbeat health file (P3-3)

With `--health-out`, pod-infer writes the 7 health fields that `pod-uplink` sends as `/api/heartbeat` (see `heartbeat.h`). The file is rewritten atomically every 10 s:

| Field | Source |
|-------|--------|
| `infer_p50_ms`, `infer_p99_ms` | model run time, frames from the last 60 s |
| `e2e_p50_ms`, `e2e_p99_ms` | camera callback to detections ready, last 60 s |
| `temp_c` | supervisor `status.json` if under 60 s old, else `/dev/thermal` |
| `restarts` | supervisor `status.json` (0 without a supervisor) |
| `disk_free_mb` | free space on the file's filesystem |

The file is **deleted** on exit, when no frame arrived in 60 s, or when no temperature reading exists, so heartbeats stop rather than repeat old or invented numbers. A heartbeat therefore means "the Pod was observing", which is how agent-3's density code uses them (as camera time).

Pi test, 2026-11-22: an observer instance (`--observe --threads 1`) wrote the file and the ground station accepted 3 heartbeats (`pod-data/uplink/heartbeat_seq` went from none to 3), e.g. `{"infer_p50_ms":181.86,"infer_p99_ms":205.65,"e2e_p50_ms":210.49,"e2e_p99_ms":251.23,"temp_c":52.7,"disk_free_mb":13486,"restarts":11}`. Those latencies are from the slower 1-thread test instance. The file was gone after exit.

## What it does per frame

1. Wait for the ring's pulse and take the newest frame. Older frames are skipped, so a slow model never stalls the camera.
2. Convert YUYV 640x480 straight into the model's 320x320 RGB input (nearest neighbour, BT.601). This takes about 1 ms. The ring slot is released right after, before the model runs.
3. Run the model, then keep animal classes with a score of at least `--min-score`, at most 16 (`POD_MAX_DETECTIONS`).
4. Fill a `pod_detections_t` (`shared/pod_msgs.h`) with `t_capture_ns` from the frame and `t_infer_done_ns`, and call `HealthBeat::beat()` for the supervisor.
5. If no frame arrives for 2 s, reopen the ring (pod-capture may have restarted).

Every frame's `pod_detections_t` (including frames with no detections) is sent to `pod-track` over the POSIX queue `--dets-mq` (default `/pod_dets`, see `pod/track/dets_mq.h`). The send never blocks: if pod-track is slow or not running, the frame is dropped and counted as "not sent to track" in the stats.

## Measured (2026-11-21, live camera, idle Pi, EfficientDet-Lite0, 3 threads)

15 s run, 221 frames, while agent-1's capture, log and uplink were running:

| Stage | p50 | p99 | max |
|-------|-----|-----|-----|
| pre (frame -> model input) | 0.95 ms | 1.44 ms | 1.59 ms |
| infer (model run) | 67.0 ms | 68.5 ms | 77.0 ms |
| e2e (camera callback -> detections) | 93.6 ms | 134.1 ms | 135.4 ms |

Rate: **14.7 frames/s** (Gate 1 needs 5+). e2e is larger than pre + infer because a new frame waits while the model is still busy with the previous one.

Still-image check (`--ppm`): COCO "two cats" photo gives 2 cats (0.72, 0.65), TF sample dog photo gives 1 dog (0.82), and the Grace Hopper (person) photo gives 0 detections.
