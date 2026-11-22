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

## Stress bench: CPU hog vs. priority (P3-2, agent-2's half)

```
sh pod/infer/stress_bench.sh 30 ~/agent2/bench-1          # on the Pi, ~2 min
python3 pod/infer/latency_report.py a=a.csv b=b.csv --md t.md --json t.json   # any CSVs from --latency-log
```

`stress_bench.sh` pauses any running pod-infer (SIGSTOP, found with `sudo pidin`, since a normal user's `pidin` doesn't list root processes) and resumes it at the end, also on Ctrl-C or error. It then runs pod-infer three times as the ring's only consumer:
- `baseline`: no load;
- `hog`: 4 busy-loop shells at normal priority, pod-infer at normal priority;
- `hog_fifo`: the same hog, pod-infer at SCHED_FIFO 15.

While pod-infer is paused, the bench writes the heartbeat file itself, so heartbeats stay honest; the dashboard will show the hog run's numbers for those 30 s. `pod-infer --latency-log FILE.csv` writes one row per frame (`frame_seq,t_capture_ns,pre_us,infer_us,e2e_us,skipped,n_detections`).

### Result (Pi 5, QNX 8.0, 2026-11-22 04:37 UTC, EfficientDet-Lite0, 3 threads, 30 s per run)

| Run | Frames/s | infer p50 | infer p99 | e2e p50 | e2e p99 | e2e max | e2e > 200 ms |
|---|---|---|---|---|---|---|---|
| baseline | 14.1 | 69.1 | 74.4 | 90.4 | 128.8 | 138.6 | 0.0% |
| hog, normal priority | 0.4 | 2416.0 | 2563.8 | 2433.4 | 2605.4 | 2605.4 | 100.0% |
| hog, SCHED_FIFO 15 | 11.2 | 87.8 | 100.0 | 110.3 | 149.8 | 159.3 | 0.0% |

All times are in ms. Raw output: `bench/2026-11-22/` (`report.txt` with histograms, `table.md`, `latency.json` for the dashboard).

What it shows:
- **Without priority, a CPU hog wrecks inference: it gets 35x slower.** That's much worse than a fair share of the CPU would suggest. The 3 inference threads have to meet at the end of every layer, so the job waits for whichever thread the scheduler has parked behind a hog.
- **With SCHED_FIFO 15, inference keeps a bounded, measured latency under the same load:** e2e p99 150 ms against 129 ms idle, and no frame over 200 ms.
- **It isn't free:** infer p50 is 88 ms against 69 ms idle, and the rate drops from 14.1 to 11.2 frames/s. The hogs still use the 4th core, and they share memory bandwidth and cache with the inference threads.

The supervised pod-infer currently runs at priority 0 (`pod.conf`). Before this bench, the supervisor log showed it at infer p99 410 ms at 8.1 frames/s with no hog, just from sharing the CPU with pod-view and the rest. It should run with `--prio 15` (see the handoff in TASKS.md).

## CLIP animal identification (prototype 1, branch `prototype-1`)

**Problem:** with the C270 webcam, the COCO detector finds a phone-screen or plushie animal in only a few frames, and it cannot name most animals (no deer, fox, raccoon...). Lowering `--min-score` gave false "cat" boxes on empty frames.

**Fix (from the laptop scanner, `demo/laptop_detector.py`):** `--clip-model models/clip_vision.tflite` turns on a second model, CLIP ViT-B/32, run by the same QNX TFLite build:

1. The detector only says **where** to look: an animal it knows, or a phone/TV/laptop/book/teddy bear that may show one (score ≥ `--clip-region`, default 0.3). If there is none, it looks at the middle of the view. Person is never chosen.
2. A worker thread (normal priority, below the detector) cuts that region from the **full 640×480 frame**, runs CLIP and compares the result with 53 animal names (`models/clip/labels.txt`, text side precomputed on the laptop in `models/clip/text.f32`).
3. An animal counts only after it wins `--clip-need` of the last `--clip-window` checks (pod.conf: 4 of 6). While it stays in view, every message to pod-track carries it, so pod-track makes **one visit** and it reaches the dashboard as that species (shown as a plushie in the habitat view).

The detector loop keeps its rate and priority; CLIP only takes frames when it's free. `--stats` adds a `clip` line with checks per second and CLIP run time. Without `--quiet`, every check is printed (`CLIP check: deer 0.83 (votes 3/4 for deer)`).

| Option | Default | Meaning |
|---|---|---|
| `--clip-model` | off | `models/clip_vision.tflite` turns CLIP on |
| `--clip-labels`, `--clip-text` | `models/clip/labels.txt`, `models/clip/text.f32` | animal list and its text embeddings |
| `--clip-min-prob` | 0.45 | a check counts as a vote at this probability |
| `--clip-region` | 0.3 | detector score to look closer at a region |
| `--clip-need`, `--clip-window` | 6, 10 | votes needed out of recent checks |
| `--clip-threads` | 1 | CLIP inference threads |
| `--clip-prio` | 10 | worker priority (QNX normal), below the detector |

**Files:** `models/clip_vision.tflite` (88.5 MB, int8 weights, builtin ops only: FULLY_CONNECTED, BATCH_MATMUL, SOFTMAX, CONV_2D, ...) is built by `models/clip/make_clip_tflite.py` with TensorFlow 2.16 (same version as the QNX port). `models/clip/export_text.py` writes the label/text files from `demo/species.txt`. `deploy.sh` copies all three. Every CLIP animal has a row in `models/labels.csv` (ids 12+, no model_index).

**Checked on the laptop (not yet on the Pi):** pod-infer compiles against the TFLite 2.16 headers with `-Wall -Wextra`; `clip_test` passes (voting, crop); the full C++ path (C270-style YUYV 640×480 → `crop_to_input` → TFLite CLIP) names 9 of 9 test photos right (deer, moose, fox, raccoon, owl, bear, cat, teddy → bear, empty room → nothing). **Unknown until it runs on the Pi:** CLIP time per check on the Pi 5 (expect a few hundred ms with 1 thread) and its effect on detector latency.

On the Pi after `deploy.sh`: build as above, then `./build/infer/clip_test` and restart the Pod.
