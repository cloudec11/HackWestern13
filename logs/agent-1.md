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

## 2026-11-21 14:20:24 | agent-1 | P0-7 | REVIEW
**What:** Network plan written: Wi-Fi hotspot primary, direct Ethernet static-IP fallback
**Files:** `pod/scripts/NETWORK.md`, `pod/scripts/eth_static.sh`
**Notes:** Pi 172.20.10.11 and laptop 172.20.10.9 both on hotspot 172.20.10.0/28. Fallback: Pi cgem0 192.168.50.2, laptop 192.168.50.1. Pod finds the ground station via POD_GROUND_URL (agent-3/4: ground API listens on 0.0.0.0:8000 and needs an inbound firewall allow on the laptop). Ethernet fallback NOT tested: no cable, and setting a static IP on the Pi needs sudo.

## 2026-11-21 14:31:33 | agent-1 | P0-5 | REVIEW
**What:** Measured camera rate on unit 5 (USB C270): ~20 fps via callback API
**Notes:** 299 frames in 15 s with camera_example1_callback -u 5 (includes startup, so steady rate is at least 20 fps). Per-frame callback cost about 2 ms. Gate 1 needs 5+ detections/s, so capture is not the limit. Frames-on-screen still not shown: no camera dev headers in /usr/include/camera on the Pi, and camera_example4_record hangs without writing a file (a 30 MB core from it already sits in ~/cores). Needs a monitor + camera_example3_viewfinder to close. Also: qnxpi.local sometimes fails to resolve for a while; just retry by name.

## 2026-11-21 14:38:52 | agent-1 | P0-2 | DONE
**What:** P0-2 DONE: pod_msgs.h v1 accepted. Human owner reports P0-4 sign-off by the other agents
**Notes:** Sign-off reported verbally by agent-1's human, not recorded by the other agents in the repo. The P0-4 row is shared ('all'), so agent-1 left it for a human to mark DONE.

## 2026-11-21 14:38:52 | agent-1 | P1-1 | DOING
**What:** Claiming P1-1: pod-capture (camera callback -> 4-slot shm ring -> pulse to infer)
**Notes:** Plan: pod/ipc/ring.h first (portable, testable on the laptop), then pod/capture/ on QNX. Risk: no camera dev headers found in /usr/include/camera on the Pi; checking before writing the camera callback.

