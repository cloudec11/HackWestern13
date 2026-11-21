# Agent 1 log

Append-only. Written by agent-1 only.

## 2026-11-21 14:11:02 | agent-1 | P0-5 | DOING
**What:** Claiming P0-5: Pi SSH + camera bring-up
**Notes:** SSH key login to qnxuser@qnxpi.local works. Pi 5 on QNX 8.0.0, clang 22.1.8, cmake, python 3.14. USB cam Logitech C270 and Nooelec RTL-SDR detected. Repo cloned at ~/repos/HackWestern13.

## 2026-11-21 14:13:14 | agent-1 | P0-5 | REVIEW
**What:** Gate 0 partly met: QNX camera API delivers live frames from USB webcam as camera unit 5
**Notes:** SSH works. Run: camera_example1_callback -u 5 (callback API, libcamapi). Unit 5 = Logitech C270 (live: channel avgs ~140,129,127, varies per frame). Units 1 and 2 return constant frames (likely CSI ports, no sensor attached). Per-frame callback work ~5 ms. NOT yet verified: frames displayed on a monitor (camera_example3_viewfinder) and fps. P1-1 should open unit 5 via camera_open + callback.

## 2026-11-21 14:16:29 | agent-1 | P0-2 | DOING
**What:** Claiming P0-2: drafting shared/pod_msgs.h (draft only, needs P0-4 sign-off)

## 2026-11-21 14:17:42 | agent-1 | P0-2 | REVIEW
**What:** Drafted shared/pod_msgs.h v1: frame header, detection, detection result, visit event, heartbeat
**Files:** `shared/pod_msgs.h`
**Notes:** Draft needs P0-4 sign-off from all agents. Sizes: frame hdr 64 B, detection 32 B, detections msg 544 B (max 16 per frame), event 120 B, heartbeat 72 B. Compiles clean as C11 and C++17 (-Wall -Wextra -Werror) on the Pi with static size asserts. Timestamps: *_ns = CLOCK_MONOTONIC for latency, *_ms = Unix wall clock for ground station. (pod_id, seq) is the event idempotency key. No person class exists by design. Class ids come from the label table in models/ (agent-2). agent-3: please align event_schema.json field names with pod_event_t and pod_heartbeat_t. agent-2: check that pod_detection_t and pod_detections_t fit what pod-infer outputs.

## 2026-11-21 14:19:15 | agent-1 | P0-7 | DOING
**What:** Claiming P0-7: network plan (same subnet, Ethernet static-IP fallback)

