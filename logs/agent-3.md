# Agent 3 log

Append-only. Written by agent-3 only.

## 2026-11-21 14:30:22 | agent-3 | P0-3 | DOING
**What:** Claiming P0-3: drafting shared/event_schema.json (draft only, needs P0-4 sign-off)

## 2026-11-21 14:31:44 | agent-3 | P0-3 | REVIEW
**What:** Drafted shared/event_schema.json v1: visit_event, heartbeat, event_batch, event_ack, heartbeat_ack (JSON Schema 2020-12)
**Files:** `shared/event_schema.json`, `TASKS.md`
**Notes:** Field names match pod_event_t / pod_heartbeat_t exactly. JSON-only additions: species (string from label table, filled by pod-uplink; 'person' rejected), optional lat/lon/loc_source (gps|simulated) for mobile mode. Batches max 50 events from one pod. acked_seq = highest CONTIGUOUS seq held, so mid-batch losses are resent, never skipped. additionalProperties false everywhere. Heartbeat reply also returns acked_seq for cursor resync after reboot. Validated with jsonschema 4.26: examples pass; person, extra fields, seq 0, score>1, lat without loc_source, 51-event batch all rejected. Needs P0-4 sign-off.

## 2026-11-21 14:59:22 | agent-3 | P0-4 | DONE
**What:** P0-4 DONE: agent-3's human reports all four agents signed off pod_msgs.h v1 and event_schema.json v1
**Files:** `TASKS.md`, `shared/event_schema.json`
**Notes:** Marked P0-3 and P0-4 DONE, removed the sign-off row from Blockers. event_schema.json header changed from DRAFT to signed off (wording only, no field changes). agent-1: pod_msgs.h header still says DRAFT; update it in your lane when convenient. Reminder for pod-uplink: fill species from the label table; resend everything after acked_seq (highest contiguous).

## 2026-11-21 14:59:23 | agent-3 | P1-3 | DOING
**What:** Claiming P1-3: FastAPI ingest + SQLite in ground/ (api.py, db/, tests/, requirements.txt)

## 2026-11-21 15:02:29 | agent-3 | P1-3 | REVIEW
**What:** Ground ingest API: FastAPI + SQLite. POST /api/events, POST /api/heartbeat, GET /api/summary, GET /api/events, GET /health
**Files:** `ground/api.py`, `ground/db/schema.sql`, `ground/tests/test_api.py`, `ground/requirements.txt`, `ground/README.md`, `ground/.gitignore`, `ground/__init__.py`
**Notes:** Validates every request against shared/event_schema.json directly (no duplicated models). Whole batch rejected with 422 + {path,error} list if any event is bad (incl. person, pod_id mismatch, t_end<t_start). INSERT OR IGNORE on (pod_id, seq); acked_seq = highest contiguous seq per pod, also returned on heartbeat. 21 pytest tests pass; smoke-tested uvicorn on 0.0.0.0:8000 over HTTP. agent-1: run with 'ground/.venv/Scripts/python -m uvicorn ground.api:app --host 0.0.0.0 --port 8000', POD_GROUND_URL=http://<laptop-ip>:8000. NOT tested from the Pi: this laptop is not on the 172.20.10.x hotspot, and Windows Firewall inbound 8000 must be allowed by the human. agent-4: GET /api/summary is your dashboard feed (see ground/README.md); CORS open. ground/__init__.py added so 'ground.api' imports; harmless to agent-4. I own ground/requirements.txt: request additions via TASKS.md.

## 2026-11-21 15:10:36 | agent-3 | P1-4 | DOING
**What:** Claiming P1-4: fake Pod generator (ground/fake_pod.py) so the dashboard and simulation are not blocked
**Notes:** No label table in models/ yet; using COCO animal ids as placeholders until agent-2 publishes one.

## 2026-11-21 15:16:26 | agent-3 | P1-4 | REVIEW
**What:** Fake Pod generator ground/fake_pod.py: live feed, multi-pod, N-day backfill with species trends, flaky-network modes, mobile lat/lon, dry run
**Files:** `ground/fake_pod.py`, `ground/tests/test_fake_pod.py`, `ground/db/schema.sql`, `ground/README.md`
**Notes:** agent-4: dashboard is unblocked. Start the API, then 'python -m ground.fake_pod' for a live feed or '--backfill-days 14' for history; GET /api/summary to read. Behaves like pod-uplink (batches <=50, resend until acked_seq covers them) and resyncs seq from the server on start, so reruns with the same pod id keep adding data. Class ids are PLACEHOLDERS (bird=1..giraffe=10) until agent-2's label table exists. Server change: heartbeats key is now (pod_id, seq, t_wall_ms) so a rebooted Pod whose heartbeat counter restarts at 1 is still recorded; delete ground/db/pod.sqlite3 once after pulling. agent-1: same reboot issue applies to event seq on the real Pod; persist the seq counter (or resync from the heartbeat ack's acked_seq) or post-reboot events get ignored as duplicates. Also use the laptop IP, not a hostname: 'localhost' on Windows costs ~2 s per request. Verified: 29 pytest tests pass; against a live server, 14-day backfill (616 visits, 2 pods) plus a flaky live run (drop/loss/dup 0.2) stored every event, acked_seq == n_events, queue_depth 0, zero 422s.

