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

## 2026-11-21 14:49:24 | agent-1 | P1-1 | DOING
**What:** Added pod/ipc/ring.h: lock-free 4-slot newest-wins frame ring in POSIX shm with pulse semaphore; tests pass on the Pi
**Files:** `pod/ipc/ring.h`, `pod/ipc/ring_test.cpp`
**Notes:** For agent-2 pod-infer: ShmRing r; r.open(/pod_frames); then loop: r.ring.wait(ms), h = r.ring.acquire_latest(last_seq), read h and r.ring.payload(h), last_seq = h->seq, r.ring.release(). Consumer always gets the newest frame; skipped frames show as seq gaps; producer never blocks. Tested on Pi 5 QNX 8 with clang++ -std=c++17 -Wall -Wextra, no warnings: single-thread semantics, 200k-frame two-thread stress (0 torn reads, seq never backwards), two processes over shm with the semaphore. Camera side of P1-1 still blocked: no camera_api.h on the Pi, needs the QNX SDK. Code landed in commit fa19a89 without this entry because the first log call failed.

## 2026-11-21 16:02:07 | agent-1 | P1-1 | DOING
**What:** Webcam now works headless: Pod camera-service config plus switch script; unit 5 gives about 20 fps with no display
**Files:** `pod/scripts/sensor_pod.conf`, `pod/scripts/camera_service.sh`
**Notes:** Cause of camera failures: Screen (display) service on the Pi stopped, and the stock sensor config allocates camera buffers through Screen, so every camera start failed (err 9, then err 2). sensor_pod.conf = stock /etc/config/sensor/sensor_rpi5.conf plus 2 lines on SENSOR_UNIT_5: create_window = false, memory_allocator_type = from_sensor. Switch with: sh pod/scripts/camera_service.sh pod | stock | status (uses sudo; restarting interrupts other camera users). Installs the config at /data/share/sensor/sensor_pod.conf. Result: camera_example1_callback -u 5 gave 295 frames in 15 s, live values. The Pi service is currently LEFT on the Pod config. Correction to my earlier P0-5 log: units 1 and 2 are a simulator and a video file, not empty CSI ports.

## 2026-11-21 16:07:51 | agent-1 | P1-1 | DOING
**What:** Reverse-engineered the QNX camera buffer layout without SDK headers; probe verifies it on real frames (all checks pass)
**Files:** `pod/capture/camapi_min.h`, `pod/capture/cam_probe.c`
**Notes:** camapi_min.h: our own declarations for camera_open/close/start_viewfinder/stop_viewfinder (link -lcamapi), buffer treated as opaque and read by offset. Verified on the Pi, unit 5, Pod camera config: frametype at 0 = 14 (YCbYCr), pixel pointer at 16, timestamp at 40 in microseconds (~28 fps), framedesc at 72 = height 480, width 640, stride 1280. Offset 8 is NOT the byte count (reads 84): use stride*height = 614400. Pixel means from our buffer matched camera_example1_callback within ~1.5. A 640x480 snapshot decoded to a correct colour image (viewed, then deleted from the Pi since it showed a person; not committed). Frames are YUYV, which matches POD_PIX_YUY2 in pod_msgs.h, so no contract change is needed.

## 2026-11-21 16:14:49 | agent-1 | P1-1 | REVIEW
**What:** pod-capture works end to end on the Pi: webcam -> 4-slot shm ring /pod_frames -> pulse; test consumer verified every frame
**Files:** `pod/capture/main.cpp`, `pod/capture/capture_check.cpp`, `pod/capture/CMakeLists.txt`
**Notes:** Measured on Pi 5 (22 s run, camera thread SCHED_FIFO 30, agent-2 TFLite build may have been loading the CPU): 640x480 YUYV at 15-16 fps, 0 bad frames, 0 no-slot, copy into ring p50 0.3 ms p99 0.5 ms; capture->consumer latency p50 0.48 ms, p99 3.19 ms, max 48 ms (single outlier, not yet explained). capture_check: 230 frames in 15 s, 0 skipped, 0 bad headers. Frame rate is lower than the ~28 fps seen with cam_probe earlier; cause not yet known (CPU load from the TFLite build, or the C270 lowering fps in dim light). Still above the 5/s Gate 1 target. Build: cmake -S pod/capture -B build/capture && cmake --build build/capture (export CMAKE_POLICY_VERSION_MINIMUM=3.5). Handoff row for agent-2 added to TASKS.md.

## 2026-11-21 16:17:20 | agent-1 | P0-5 | DONE
**What:** P0-5 DONE: webcam frames proven on the Pi
**Notes:** Evidence: cam_probe decoded a real 640x480 colour snapshot (viewed, then deleted as it showed a person), pod-capture delivers 15-16 fps into the ring. Display-free by design (sensor_pod.conf), which is what the field Pod needs.

## 2026-11-21 16:17:21 | agent-1 | P2-3 | DOING
**What:** Starting P2-3 pod-uplink early (human decision): its deps P1-3 (REVIEW) and P2-2 (TODO) are not DONE
**Notes:** Building against agent-3's stdlib ground API (ground/README.md) and a fake events file in the JSONL format pod-log (P2-2, also mine) will write. Python stdlib only, so it runs on the Pi.

## 2026-11-21 16:18:56 | agent-1 | - | DONE
**What:** Added pod/scripts/deploy.sh: one command copies pod/, shared/, models/labels.csv to the Pi and builds pod/capture
**Files:** `pod/scripts/deploy.sh`, `pod/scripts/.gitattributes`
**Notes:** Usage from the repo (Git Bash/macOS/Linux): sh pod/scripts/deploy.sh. Env: POD_HOST (default qnxuser@qnxpi.local), POD_DIR (default pod-deploy). Uses tar over SSH, not scp. Tested from Windows Git Bash: copy + build ok. Also pod/scripts/.gitattributes forces LF line endings for scripts in that folder so they run on QNX after a Windows checkout.

## 2026-11-21 16:25:11 | agent-1 | P2-3 | DOING
**What:** pod-uplink written and tested: store-and-forward of pod-log JSONL to the ground API, cursor advances only on acked_seq
**Files:** `pod/uplink/uplink.py`, `pod/uplink/test_uplink.py`, `TASKS.md`
**Notes:** Stdlib Python. Input format (pod-log, P2-2) defined in the uplink.py docstring: events-*.jsonl, one pod_event_t per line. Adds species from models/labels.csv. 8 tests run agent-3's real ground/api.py in-process; all pass on the laptop (Python 3.12) and on the Pi (3.14): batching 50/50/20, restart resumes from the saved cursor with 0 resends, server down keeps everything then catches up, half-written last line waits, species mapping, 422 rejection is reported (not looped), server gap detected, heartbeat only sent when a health file exists. Remaining for DONE: real pod-log output (P2-2) and an end-to-end run Pi -> laptop. Known contract edge: a 422-rejected event stalls acked_seq for good (contiguous ack); uplink reports it loudly. Also logged: camera frame rate is now 15.0 fps for the camera alone (cam_probe) too, so the drop is not pod-capture; likely the C270 auto-exposure in dim light, possibly CPU load (agent-2 ninja -j4 TFLite build running). Handoff row for agent-2 (heartbeat.json fields) added.

## 2026-11-21 16:27:57 | agent-1 | P2-2 | DOING
**What:** Starting P2-2 pod-log early (human decision) against a fake tracker; P2-1 not done
**Notes:** Plan: pod-track sends pod_event_t over POSIX message queue /pod_events (helper header pod/ipc/events_mq.h for agent-2). pod-log assigns seq durably, appends JSONL in the format pod-uplink reads, rotates by size, deletes only files the ground station has acked. Thumbnails in a second pass (needs agreement with agent-2 on which frame).

## 2026-11-21 16:27:58 | agent-1 | P3-1 | DOING
**What:** Starting P3-1 pod-supervisor early (human decision) against pod-capture; P2-3 not done
**Notes:** Plan: supervisor spawns Pod processes with SCHED_FIFO priorities, each process beats a shared-memory health table, restart after 3 missed 500 ms beats or on exit, thermal logging from /dev/thermal. Note: /dev/thermal (rpi_thermal), Screen and drm-rpi5 are not running on the Pi right now; restarting rpi_thermal needs sudo, not done.

## 2026-11-21 16:33:30 | agent-1 | P2-2 | DOING
**What:** pod-log written and tested on the Pi with a fake tracker and the real ground API
**Files:** `pod/log/main.cpp`, `pod/log/fake_track.cpp`, `pod/log/CMakeLists.txt`, `pod/log/test_e2e.sh`, `pod/ipc/events_mq.h`, `pod/ipc/health.h`, `pod/scripts/deploy.sh`, `TASKS.md`
**Notes:** pod-track to pod-log over POSIX mqueue /pod_events (events_mq.h). pod-log validates like the ground schema, assigns seq durably, appends fsynced JSONL, rotates by date and size, deletes old files only after the ground station acked them. Pi test (pod/log/test_e2e.sh): 3 invalid events skipped, kill -9 plus a half-written line recovered with seq continuing at 44, 120 events contiguous, uplink delivered all 120, retention deleted only acked files. health.h added for P3-1. deploy.sh now builds every pod component. Still to do for DONE: thumbnails (needs agent-2 frame source) and the real pod-track. Handoff row for agent-2 added.

## 2026-11-21 16:37:31 | agent-1 | P3-1 | DOING
**What:** pod-supervisor written and tested on the Pi with pod-capture, pod-log and pod-uplink
**Files:** `pod/supervisor/main.cpp`, `pod/supervisor/CMakeLists.txt`, `pod/supervisor/pod.conf`, `pod/supervisor/test_supervisor.sh`, `pod/capture/main.cpp`, `pod/.gitattributes`, `TASKS.md`
**Notes:** Spawns children from pod.conf with SCHED_FIFO priorities (supervisor 40, capture 30, log 20), shm health table, restart after 1.5 s without a beat (3 x 500 ms) or on exit with 1-30 s backoff, writes status.json every 10 s (temp_c, restarts) for the P3-3 heartbeat. pod-capture now beats per published frame. Pi test: kill -9 pod-capture restarted in 1 s and frames resumed; SIGSTOP pod-log detected after 1.5 s and restarted; 15 events went fake_track to log to uplink to ground (acked 15); stopping the supervisor stopped all children. Gap: no temperature, /dev/thermal is missing because rpi_thermal is not running (needs sudo to start, not done). pod-infer and pod-track still to add once agent-2 has them. Handoff row for agent-2 added.

## 2026-11-21 16:45:58 | agent-1 | P2-3 | REVIEW
**What:** pod-uplink end-to-end over the hotspot: Pi (supervisor + pod-log + uplink) to the ground API on the laptop, including a ground outage
**Files:** `TASKS.md`
**Notes:** Laptop 172.20.10.9:8000, Pi 172.20.10.11, Windows firewall let it through. Real pod.conf with POD_GROUND_URL set. 20 events sent, laptop acked 20. Laptop server stopped, 25 more events logged on the Pi, uplink held cursor at 20 and retried. Server restarted on the same DB: acked 45 within about 2 s, 0 duplicates, 0 lost. Also: rpi_thermal on this headless image reads 1.00 C via the legacy mailbox (drm-rpi5 not running), so it is not usable for P3-1 thermal logging yet; while running it holds the fan at 0 percent. Waiting for human approval to stop it and restore the fan.

## 2026-11-21 16:47:02 | agent-1 | P3-1 | DOING
**What:** Stopped rpi_thermal (bad 1.00 C reading, held fan at 0 percent) and restored fan to the boot value 200, with human approval
**Notes:** Temperature logging stays unavailable until drm-rpi5 (vcio mailbox provider) is running. Pi is back to its previous state apart from the camera service on the Pod config.

## 2026-11-21 16:57:36 | agent-1 | P3-1 | DOING
**What:** Pod starts at power-on (boot hook) and temperature logging works (drm-rpi5 + rpi_thermal via vcio)
**Files:** `pod/scripts/pod_boot.sh`, `pod/scripts/boot_hook.sh`
**Notes:** With human approval. drm-rpi5 was not running, so rpi_thermal fell back to the legacy mailbox and read 1 C. Started drm-rpi5 as post_startup.sh does; rpi_thermal -M vcio -f off now reads 38 C, fan stays at boot value 200. /system is read-only, but SLM runs /usr/etc/startup/full_startup.sh at boot and /usr is writable: boot_hook.sh install appends one marked line (backup at full_startup.sh.pod-backup; remove deletes it). pod_boot.sh waits for post_startup, starts drm/thermal if missing, camera_service.sh pod, waits for /dev/sensor/camera5, then pod-supervisor as qnxuser with groups 521 sensor, 36, 523 (on -u drops supplementary groups: camera_open gave EACCES without them). Manual run: capture ~15 fps, temp logged in status.json, 0 restarts. Hook installed. NOT yet verified with a real reboot (needs human OK). Skip at boot: touch ~/pod-noboot.

## 2026-11-21 17:29:05 | agent-1 | P3-1 | DOING
**What:** Switched to Android hotspot; auto-start verified by a real reboot; clock found to boot at 1970
**Files:** `pod/scripts/NETWORK.md`
**Notes:** New network 10.206.112.0/24, laptop .71, Pi .26, qnxpi.local works, Pi reaches laptop API on 8000. Pi ~/pod.env now points POD_GROUND_URL at the laptop. boot.log shows pod_boot ran at boot and pod-supervisor came up with 0 restarts. Risk for all agents: no RTC, Pi clock is 1970 until NTP syncs, so events logged before sync are rejected as before 2020. NETWORK.md has Wi-Fi switch steps and the clock note.

## 2026-11-21 17:32:17 | agent-1 | P2-3 | REVIEW
**What:** Real events now flow Pi to laptop; fixed loss caused by a reused test database
**Notes:** Someone (likely agent-2) is sending real events to /pod_events. The laptop API was still on my test DB where pod-01 was already acked to 45, so the server treated real seq 1 as a duplicate and uplink advanced to 45: event silently not stored. Restarted the API on a fresh DB, reset the Pi uplink cursor, 3 events now stored. Known edge for all: if pod-data on the Pi is wiped (seq restarts at 1) but the ground DB keeps the old pod-01, new events are dropped as duplicates. Rule: never wipe pod-data without also using a fresh ground DB or a new pod_id. TODO agent-1: uplink should warn when the server acked_seq is above the Pod's own last seq.

## 2026-11-21 17:43:24 | agent-1 | ad-hoc | DONE
**What:** pod-view: live camera view with detection boxes in a browser (setup and demo tool, off by default)
**Files:** `pod/capture/view.cpp`, `pod/capture/tj_min.h`, `pod/capture/CMakeLists.txt`, `pod/ipc/ring.h`, `pod/supervisor/pod.conf`, `pod/scripts/deploy.sh`
**Notes:** Human request. Ring::peek_latest added to ring.h: read-only observer copy, never claims a slot or the pulse, rejects frames overwritten mid-copy (published_seq check); no layout change, pod-infer unaffected. Detections via tap: pod-infer --dets-mq /pod_view_dets, pod-view forwards to /pod_dets. JPEG via /usr/lib/libturbojpeg.so.0 with own declarations (no header on Pi). HTTP on 8080: page, MJPEG stream, snapshot, status.json. Pi test: 7.9 fps at about 34 KB per frame, 1 discarded frame, detections received about 15 per s, e2e about 100 ms. Saves nothing. deploy.sh now builds only agent-1 components by default (POD_COMPONENTS to override). Ask for agent-2: a read-only latest-detections copy so the viewer need not sit between infer and track.

## 2026-11-21 18:26:41 | agent-1 | review | DONE
**What:** Reviewed agent-3 ground work (P1-3 API, P2-4 density, P2-5 forecast, P3-4 allocator, backtest): solid, one blocking issue for P2-4
**Files:** `ground/api.py`, `ground/density.py`, `ground/simulate.py`, `ground/allocate.py`, `ground/backtest.py`, `ground/params.py`
**Notes:** Checks: all 75 ground tests pass (pytest in a venv, 4 min 20 s). No contract changes since sign-off. Backtest re-run: calibration keeps K 0.9-1.6, sigma 0.30, test years 2005+ coverage 93.8 percent, claim holds. P1-3 API: OK to sign off, exercised heavily from the Pi. FINDING 1 (blocking P2-4, for agent-3 and agent-2): density effort comes from heartbeats, but no heartbeats are sent yet (schema requires infer and e2e latency from agent-2 P3-3). Effort then falls back to event timestamps only and is near zero: 6 cat visits over 8 h give 4.4 million cats without heartbeats vs about 4600 with them, about 1000x too high, and forecast and allocation inherit it. Fix: density returns insufficient_data for a pod with no heartbeats; agent-2 delivers P3-3 heartbeat.json and pod-uplink already sends it. FINDING 2 (agent-3): allocate scenario table rebuild takes 2.3 s (3 zones x 4 species) and the cache key changes on every heartbeat, so with 10 s heartbeats the budget slider will often stall; key the cache on events only or rebuild in the background. FINDING 3 (agent-3): /api/density and /api/simulation recompute 1000 bootstrap resamples and 1000 runs per species on every request; add a short cache before the dashboard polls them. FINDING 4: test suite is slow (4 min 20 s), cause still being measured. FINDING 5 (info): forecast median error 31.1 percent vs no-change 29.7 percent; documented honestly, expect a judge question.

## 2026-11-21 18:27:19 | agent-1 | P3-1 | DONE
**What:** Fixed pod-capture restart loop on the Pi: restarted pod-supervisor to re-sync the shared health table; capture now stays up
**Notes:** Symptom: from 23:13:34 the supervisor logged 'no beat for 1.5 s' for pod-capture every ~5 s (20+ restarts, 30 s backoff), so the live feed was mostly dead (stream ~0.1 fps). pod-capture itself was healthy (published 15-30 fps, 0 bad). The supervisor's status.json beats counter was frozen at 59440 (the first instance's frame count) while later instances printed 'supervised': they attached to a /pod_health table the running supervisor no longer saw. Cause not proven; the supervisor calls shm_unlink('/pod_health') at startup (supervisor/main.cpp:127), so any other supervisor start (e.g. test_supervisor.sh) orphans a live supervisor's table. Fix: SIGTERM the supervisor, started it again exactly as pod_boot.sh does (sudo, on -d -u 1000:1000,521,36,523). After: capture 40 s up, 0 restarts, beats ~15/s, pod-view stream 7.1 fps (was 0.1). TODO for agent-1: make pod-supervisor refuse to start if another instance is alive (flock/pid file) instead of unlinking the table. Remaining low fps: pod-view encodes JPEG at ~8 fps by design (agent-1's own P2-3 test: 7.9 fps); camera rate varies 15-30 fps with light.

## 2026-11-21 18:28:43 | agent-1 | review | DONE
**What:** Review of agent-3, finding 4 measured: slow tests are the API-level ones
**Notes:** Now 90 tests, 5 min 33 s. Slowest: test_allocate_endpoint_and_cache 52 s, test_density_endpoint 48 s, test_outage_reduces_camera_days_not_density 46 s, fake_pod backfill 40 s and flaky network 32 s, test_simulation_endpoint 31 s. Same paths the dashboard will poll, so it supports findings 2 and 3 (cache density, simulation and allocate). Suggest agent-3 profile test_density_endpoint first.

## 2026-11-21 19:52:02 | agent-1 | P3-1 | DONE
**What:** pod-infer, pod-track and pod-view now run under pod-supervisor; fixed supervisor handing prio-0 children FIFO 40 (starved the camera)
**Files:** `pod/supervisor/pod.conf`, `pod/supervisor/main.cpp`, `pod/scripts/pod_boot.sh`
**Notes:** pod.conf: added track, infer (sudo -n ./build/infer/pod-infer --dets-mq /pod_view_dets) and view (--dets-in /pod_view_dets --dets-out /pod_dets); infer sends to the viewer's queue, which forwards to pod-track, so the viewer must stay on (else set infer --dets-mq back to /pod_dets). All three are 'exit' (restart on exit) watched. BUG FIXED in supervisor/main.cpp start(): prio 0 was 'inherit', and the supervisor runs SCHED_FIFO 40, so every prio-0 child (view, track, infer, uplink) ran at FIFO 40 above pod-capture (30) and the camera service. pod-infer's 3 busy threads then starved the camera: e2e p99 7.8 s, capture 'no beat' restart loop, camera service ended up not running. Now prio 0 = SCHED_OTHER 10 explicitly. pod-infer needs I/O privilege, so it runs via sudo; passwordless sudo comes from group abuild (2001, /etc/sudoers.d/01-abuild), NOT group 27 sudo, so pod_boot.sh POD_IDS gained 2001 (side effect: every supervised process can sudo; narrower option: a sudoers.d rule for pod-infer only). Binaries pod-infer/pod-track and models/efficientdet_lite0.tflite are COPIED by hand from ~/agent2 into ~/pod-deploy (deploy.sh does not build them). Verified on the Pi after redeploy: capture 19.8 fps 0 bad, infer 13.9/s p50 70 ms e2e p50 96 ms, viewer forwarded 612/612 to pod-track 0 fail, no new capture restarts in 40 s. NOT yet tested: an animal in front of the camera through to ground events. Known: pod-infer crashes (signal 11) when pod-capture restarts and recreates the ring (agent-2 lane); if the sensor service dies, capture fails with camera_open ENOENT until 'sh pod/scripts/camera_service.sh pod'.

## 2026-11-21 19:58:54 | agent-1 | P2-3 | DONE
**What:** pod-view keeps detection boxes for 0.8 s after the last hit so weak detections no longer flash
**Files:** `pod/capture/view.cpp`
**Notes:** Cat on a phone screen was detected in only ~7% of frames (22 of 315 in the best 30 s window); pod-infer sends a message for every frame, empty when nothing is seen, and pod-view replaced its detections with each one, so a box lived for one frame. Now an empty message does not replace a result younger than 800 ms (kHoldNs); a new non-empty result replaces it at once. dets_rx_ns still updates on every message so the 'stale feed' logic is unchanged. Boxes are burned into the JPEG, so this changes only the picture. Deployed by building in build/capture_view and swapping build/capture/pod-view (renamed the old binary to pod-view.old: cp onto a running binary fails with 'Resource busy'). Also observed, not fixed (agent-2 lane): pod-infer rate swings 14 -> 6 fps and infer p50 70 -> 160 ms in some 30 s windows while everything else is steady, and recall on a phone-screen cat is low at --min-score 0.4. Ground API on the laptop was down at 00:58 so uplink showed backlog=8 net_error; it resumes when the API is back.

