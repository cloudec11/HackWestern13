# Agent 1 log

Append-only. Written by agent-1 only.

## 2026-11-21 14:11:02 | agent-1 | P0-5 | DOING
**What:** Claiming P0-5: Pi SSH + camera bring-up
**Notes:** SSH key login to qnxuser@qnxpi.local works. Pi 5 on QNX 8.0.0, clang 22.1.8, cmake, python 3.14. USB cam Logitech C270 and Nooelec RTL-SDR detected. Repo cloned at ~/repos/HackWestern13.

## 2026-11-21 14:13:14 | agent-1 | P0-5 | REVIEW
**What:** Gate 0 partly met: QNX camera API delivers live frames from USB webcam as camera unit 5
**Notes:** SSH works. Run: camera_example1_callback -u 5 (callback API, libcamapi). Unit 5 = Logitech C270 (live: channel avgs ~140,129,127, varies per frame). Units 1 and 2 return constant frames (likely CSI ports, no sensor attached). Per-frame callback work ~5 ms. NOT yet verified: frames displayed on a monitor (camera_example3_viewfinder) and fps. P1-1 should open unit 5 via camera_open + callback.

