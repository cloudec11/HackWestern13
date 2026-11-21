# models/ (agent-2)

## Files

- `labels.csv` is the label table. It maps the raw model class index to the Pod `class_id` and `species` name. `pod-infer` uses it to keep only animal classes. `pod-uplink` uses it to fill `species` in the JSON events.
- `fetch_models.sh` downloads the model files. They are git-ignored.

## Models

| File | Input | Notes |
|------|-------|-------|
| `efficientdet_lite0.tflite` | 320x320 RGB, uint8 | More accurate. Default choice if it meets the 5 fps budget on the Pi. |
| `ssd_mobilenet_v1_quant.tflite` | 300x300 RGB, uint8 | Faster and less accurate. Fallback. |

Both are trained on COCO and share the same class numbering, so `labels.csv` works for either. Speed on the Pi will be recorded here once measured (P0-6).

## Label rules

- `class_id` 0 means unknown. Ids are never renumbered or reused, because events stored on the ground station keep them.
- `teddy_bear` (class 11) has `demo_only` = 1. The model often labels stuffed animals as "teddy bear", so it is kept for the plush demo set. It is not a species: leave it out of density estimates and population maths.
- Person is not in the table. `pod-infer` drops every class that isn't listed, so person detections never leave the inference step.
