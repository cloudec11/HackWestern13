# models/ (agent-2)

## Files

- `labels.csv` is the label table. It maps the raw model class index to the Pod `class_id` and `species` name. `pod-infer` uses it to keep only animal classes. `pod-uplink` uses it to fill `species` in the JSON events.
- `fetch_models.sh` downloads the model files. They are git-ignored.

## Models

| File | Input | Notes |
|------|-------|-------|
| `efficientdet_lite0.tflite` | 320x320 RGB, uint8 | More accurate. Default choice if it meets the 5 fps budget on the Pi. |
| `ssd_mobilenet_v1_quant.tflite` | 300x300 RGB, uint8 | Faster and less accurate. Fallback. |

Both are trained on COCO and share the same class numbering, so `labels.csv` works for either.

## Speed on the Pi (P0-6, 2026-11-21)

Raspberry Pi 5 on QNX 8.0, CPU reported at 1.5 GHz, TFLite 2.16.1 with XNNPACK. Measured with `benchmark_model`, model inference only (no camera or preprocessing), 100 runs after 5 warm-up runs, idle system.

| Model | Threads | Avg | Min | Max | Rate |
|-------|---------|-----|-----|-----|------|
| EfficientDet-Lite0 | 1 | 132.2 ms | 127.0 | 137.9 | 7.6 /s |
| EfficientDet-Lite0 | 2 | 69.7 ms | 67.4 | 72.1 | 14.4 /s |
| **EfficientDet-Lite0** | **3** | **48.2 ms** | **47.6** | **50.6** | **20.7 /s** |
| EfficientDet-Lite0 | 4 | 287.8 ms | | | 3.5 /s (avoid) |
| SSD MobileNet v1 | 1 | 164.6 ms | | | 6.1 /s |
| SSD MobileNet v1 | 2 | 83.3 ms | 81.0 | 87.9 | 12.0 /s |
| SSD MobileNet v1 | 4 | 106.6 ms | | | 9.4 /s |

Choice: **EfficientDet-Lite0 with 3 threads.** It is both the more accurate model and the faster one at its best setting. Four threads is much slower, probably because the thread pool fights everything else on the 4 cores. Three threads leaves one core for capture, tracking, logging and uplink.

## Permissions

The QNX build of cpuinfo (used by XNNPACK) reads the CPU ID register with a privileged instruction, so it calls `ThreadCtl(_NTO_TCTL_IO_PRIV)` at startup. Without I/O privilege the process exits with `Fatal error in cpuinfo: Failed to get I/O access permission`. The numbers above were measured with `sudo`. `pod-infer` must be started with I/O privilege (root, or the QNX `io` ability).

## Label rules

- `class_id` 0 means unknown. Ids are never renumbered or reused, because events stored on the ground station keep them.
- `teddy_bear` (class 11) has `demo_only` = 1. The model often labels stuffed animals as "teddy bear", so it is kept for the plush demo set. It is not a species: leave it out of density estimates and population maths.
- Person is not in the table. `pod-infer` drops every class that isn't listed, so person detections never leave the inference step.
