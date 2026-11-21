# models/ (agent-2)

## Files

- `labels.csv` is the label table. It maps the raw model class index to the Pod `class_id` and `species` name. `pod-infer` uses it to keep only animal classes. `pod-uplink` uses it to fill `species` in the JSON events.
- `fetch_models.sh` downloads the model files. They are git-ignored.

## Models

| File | Input | Notes |
|------|-------|-------|
| `efficientdet_lite0.tflite` | 320x320 RGB, uint8 | **Default.** More accurate. TFLite Task Library build, which includes box decoding and NMS. |
| `ssd_mobilenet_v1_quant.tflite` | 300x300 RGB, uint8 | Faster, less accurate. Fallback. |

Both are trained on COCO and share the same class numbering, so `labels.csv` works for either. `pod-infer` needs a model whose last op is `TFLite_Detection_PostProcess` (outputs: boxes, classes, scores, count). The MediaPipe build of EfficientDet-Lite0 outputs raw anchors instead, so it doesn't work.

## Speed on the Pi (2026-11-21)

Raspberry Pi 5 on QNX 8.0, CPU reported at 1.5 GHz, TFLite 2.16.1 with XNNPACK. Measured with `benchmark_model`: model only (no camera or preprocessing), 100 runs after 5 warm-up runs, idle system, run as root.

| Model | Threads | Avg | Min | Max | Rate |
|-------|---------|-----|-----|-----|------|
| EfficientDet-Lite0 | 1 | 147.5 ms | 143.9 | 151.4 | 6.8 /s |
| EfficientDet-Lite0 | 2 | 86.6 ms | 84.8 | 88.6 | 11.5 /s |
| **EfficientDet-Lite0** | **3** | **67.1 ms** | **65.9** | **69.7** | **14.9 /s** |
| EfficientDet-Lite0 | 4 | 310.1 ms | 299.9 | 324.5 | 3.2 /s (avoid) |
| SSD MobileNet v1 | 1 | 164.6 ms | | | 6.1 /s |
| SSD MobileNet v1 | 2 | 83.3 ms | 81.0 | 87.9 | 12.0 /s |
| SSD MobileNet v1 | 3 | 56.5 ms | 55.5 | 57.9 | 17.7 /s |
| SSD MobileNet v1 | 4 | 106.6 ms | | | 9.4 /s |

Choice: **EfficientDet-Lite0 with 3 threads.** Four threads is much slower for both models, probably because the thread pool fights everything else on the 4 cores. Three threads leaves one core for capture, tracking, logging and uplink.

(The earlier P0-6 log entry gave 48.2 ms for EfficientDet. That was the MediaPipe build, which skips the post-processing step, so it isn't comparable.)

Full `pod-infer` numbers, including camera latency, are in `pod/infer/README.md`.

## Permissions

The QNX build of cpuinfo (used by XNNPACK) reads the CPU ID register with a privileged instruction, so it calls `ThreadCtl(_NTO_TCTL_IO_PRIV)` at startup. Without I/O privilege the process exits with `Fatal error in cpuinfo: Failed to get I/O access permission`. `pod-infer` must be started with I/O privilege (root, or the QNX `io` ability).

## Label rules

- `class_id` 0 means unknown. Ids are never renumbered or reused, because events stored on the ground station keep them.
- `teddy_bear` (class 11) has `demo_only` = 1. The model often labels stuffed animals as "teddy bear", so it is kept for the plush demo set. It is not a species: leave it out of density estimates and population maths.
- Person is not in the table. `pod-infer` drops every class that isn't listed, so person detections never leave the inference step.
