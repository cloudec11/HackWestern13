# pod-track (agent-2, P2-1)

Turns `pod-infer`'s per-frame detections into **one visit event per animal visit**, and sends each visit to agent-1's `pod-log`.

```
pod-capture --(shm /pod_frames)--> pod-infer --(mqueue /pod_dets)--> pod-track --(mqueue /pod_events)--> pod-log
```

## Build and run (on the Pi)

```
cmake -S pod/track -B build/track && cmake --build build/track
./build/track/pod-track --stats 30            # start before or after pod-infer, either order works
./build/track/track_test                      # unit tests (also run on the laptop)
```

`pod-track` doesn't need root.

| Option | Default | Meaning |
|--------|---------|---------|
| `--dets-mq` | `/pod_dets` | Queue from pod-infer. pod-track creates it. |
| `--events-mq` | `/pod_events` | Queue to pod-log (created by pod-log) |
| `--iou` | 0.3 | Minimum box overlap to count as the same animal |
| `--gap` | 2.0 | Seconds unseen before a visit ends |
| `--min-frames` | 3 | Shorter tracks are discarded as false positives |
| `--max-visit` | 0 | Split visits longer than N seconds (0 = never; see below) |
| `--prio` | 0 | SCHED_FIFO priority (0 = leave as is) |
| `--stats` | 30 | Status line every N seconds |
| `--quiet` | off | Don't print each visit |

## How it decides what a visit is

- **Same animal:** a detection continues a track if its box overlaps that track's last box (IoU at least 0.3). Matching takes the best overlaps first and ignores class, because the model sometimes flickers between similar classes (cat/dog).
- **Species:** a score-weighted vote over every frame of the visit.
- **End of visit:** not seen for 2 s, so a short occlusion stays part of the same visit. If `pod-infer` stops sending, visits still end on time.
- **False positives:** tracks seen in fewer than 3 frames are dropped.
- **Shutdown:** on SIGINT/SIGTERM, open visits are ended and sent before exiting.
- **Event fields:** `t_start_ms`/`t_end_ms` (Unix ms, converted from frame capture times), `class_id`, `max_score`, `track_id`, `n_frames`. `seq` and `pod_id` are left 0 for pod-log to fill in, and `thumb_path` is empty (no thumbnails yet).
- **pod-log not running or its queue full:** events are kept (up to 256) and retried.

`--max-visit` is off by default on purpose. An animal that stays for 10 minutes is one encounter, and splitting it would inflate the counts used by the density estimate. The catch is that something that never leaves (a plush toy fixed in front of the camera) produces no event until it is removed. For a demo that needs regular events, set `--max-visit 30`.

## Tests

- `track_test` (unit, no OS needed): one animal walking, two animals at once, 1–2 frame flickers ignored, a 1 s occlusion stays one visit, a 3 s absence makes two visits, class vote, track ids follow boxes, `--max-visit` splitting, flush on exit.
- `track_e2e` (needs a running pod-track): plays 6 s of scripted frames in real time through private queues and checks that exactly the cat (30 frames) and bird (31 frames) visits come out, and not the one-frame dog flicker. It refuses to run on the live queue names.

```
./build/track/pod-track --dets-mq /a2_test_dets --events-mq /a2_test_events &
./build/track/track_e2e --dets-mq /a2_test_dets --events-mq /a2_test_events
```

Results (Pi, 2026-11-21): `track_test` all passed; `track_e2e` PASS. With the live camera, pod-infer sent 177 frames in 12 s and pod-track received all 177 (no animal in view, so no visits).
