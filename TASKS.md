# TASKS — Who works on what

Source of truth for task delegation on **The Pod** (QNX wildlife sensor + ground station).
Plan reference: the "The Pod: Architecture & Hackathon Plan" doc. Read `AGENTS.md` before touching anything.

Four people, four Claude agents. Each agent owns a lane. **Stay in your lane's files.** If you need a change outside your lane, see "Cross-lane requests" in `AGENTS.md`.

## Agents and lanes

| Agent | Human owner | Role | Owns (only this agent edits) |
|-------|-------------|------|------------------------------|
| agent-1 | _fill in name_ | Embedded lead | `pod/capture/`, `pod/supervisor/`, `pod/log/`, `pod/uplink/`, `pod/ipc/`, `pod/scripts/` (stress, deploy) |
| agent-2 | _fill in name_ | AI lead | `pod/infer/`, `pod/track/`, `models/` |
| agent-3 | _fill in name_ | Backend / simulation lead | `ground/api.py`, `ground/density.py`, `ground/simulate.py`, `ground/allocate.py`, `ground/db/`, `ground/tests/` |
| agent-4 | _fill in name_ | Frontend / demo lead | `ground/dashboard.py`, `ground/dashboard/`, `docs/` (slides, demo script, submission page), `demo/` |

With fewer than four people: merge agent-3 + agent-4 (three people), or agent-1 + agent-2 own `pod/` and agent-3 + agent-4 own `ground/` (two people). Update the table when you do.

## Shared files (contract files — nobody edits alone)

| File | Purpose | Rule |
|------|---------|------|
| `shared/pod_msgs.h` | C structs: frame header, detection, event | Change only via a **contract change** (see `AGENTS.md`). Agreed in Phase 0. |
| `shared/event_schema.json` | JSON visit event + heartbeat schema | Same rule. |
| `TASKS.md` | This file | Each agent edits **only its own status cells/rows**. |
| `AGENTS.md` | Agent instructions | Humans only. |
| `LOG.md` | Merged change log | Generated. Never edit by hand. |
| `logs/agent-N.md` | Per-agent log | Only agent N appends. |

## Status key

`TODO` · `DOING` · `BLOCKED` · `REVIEW` · `DONE`

Claim a task by setting `DOING` and committing that change **before** starting work.

## Phase 0 — Setup and contracts (hours 0–2)

| ID | Task | Agent | Status | Depends on |
|----|------|-------|--------|------------|
| P0-1 | Create monorepo skeleton: `pod/`, `ground/`, `shared/`, `docs/`, `logs/`, `scripts/` | agent-4 | TODO | — |
| P0-2 | Draft `shared/pod_msgs.h` (frame header, detection struct, event struct) | agent-1 | DONE | — |
| P0-3 | Draft `shared/event_schema.json` (visit event, heartbeat) | agent-3 | DONE | — |
| P0-4 | Review and sign off both contract files (all four agents + humans) | all | DONE | P0-2, P0-3 |
| P0-5 | Pi boots, SSH works, camera frames on screen (Gate 0) | agent-1 | DONE | — |
| P0-6 | TFLite runs on the Pi (minimal / label_image) | agent-2 | DOING | P0-5 |
| P0-7 | Network plan: laptop + Pi on same subnet, Ethernet fallback with static IP | agent-1 | REVIEW | — |

**Gate 0:** camera frames on screen and the model runs on the Pi. Fallback: Pi Camera Module 3, or file camera.

## Phase 1 — The core loop (hours 2–8)

| ID | Task | Agent | Status | Depends on |
|----|------|-------|--------|------------|
| P1-1 | `pod-capture`: camera callback → shared-memory ring buffer (4 slots) → pulse to infer | agent-1 | REVIEW | P0-4, P0-5 |
| P1-2 | `pod-infer`: newest frame → preprocess → TFLite → print detections with `t_capture`, `t_infer_done` | agent-2 | TODO | P0-4, P0-6 |
| P1-3 | Ingest API, stdlib only (`POST /api/events`, `/api/heartbeat`, `GET /api/summary`) + SQLite, idempotent on `(pod_id, seq)` | agent-3 | REVIEW | P0-3 |
| P1-4 | Fake-event generator so the dashboard is not blocked | agent-3 | REVIEW | P0-3 |
| P1-5 | Dashboard skeleton, four panels on fake data | agent-4 | TODO | P1-4 |

**Gate 1 (hour 8):** detections print on the Pi at 5+/s with latency numbers.

## Phase 2 — End to end (hours 8–16)

| ID | Task | Agent | Status | Depends on |
|----|------|-------|--------|------------|
| P2-1 | `pod-track`: IoU tracker, one visit event per animal visit | agent-2 | TODO | P1-2 |
| P2-2 | `pod-log`: JSONL with seq numbers + thumbnails, rotation | agent-1 | TODO | P0-4, P2-1 |
| P2-3 | `pod-uplink`: store-and-forward, cursor advances on ack only | agent-1 | DOING | P1-3, P2-2 |
| P2-4 | Density estimate (Random Encounter Model) from real events | agent-3 | DOING | P1-3 |
| P2-5 | Ricker Monte Carlo simulation (notebook, then `simulate.py`) | agent-3 | TODO | P2-4 |
| P2-6 | Dashboard: live feed + Pod health panel on real data | agent-4 | TODO | P1-5, P2-3 |

**Gate 2 (hour 16):** an animal in front of the camera shows on the laptop dashboard within a few seconds.

## Phase 3 — Make the QNX story visible (hours 16–24)

| ID | Task | Agent | Status | Depends on |
|----|------|-------|--------|------------|
| P3-1 | `pod-supervisor`: heartbeats (500 ms, restart after 3 misses), SCHED_FIFO priorities, thermal logging | agent-1 | TODO | P2-3 |
| P3-2 | Stress script: low-priority CPU hog, latency histograms with and without load | agent-1 + agent-2 | TODO | P3-1 |
| P3-3 | Pod heartbeat message (p50/p99, temp, restarts, disk, last ack seq) | agent-2 | TODO | P3-1 |
| P3-4 | Budget allocator (`allocate.py`) + population fan chart data | agent-3 | TODO | P2-5 |
| P3-5 | Dashboard: latency histogram, fan chart, budget table with slider | agent-4 | TODO | P3-3, P3-4 |
| P3-6 | _Optional:_ fine-tuned camera-trap model swap-in | agent-2 | TODO | Gate 2 early |

**Gate 3 (hour 24):** feature freeze. Kill-a-process, network-drop and CPU-hog demos all work.

## Phase 4 — Polish and stretch (hours 24–30)

| ID | Task | Agent | Status | Depends on |
|----|------|-------|--------|------------|
| P4-1 | Demo set: Pod in enclosure facing plush "habitat", tablet with wildlife clips | agent-4 | TODO | Gate 3 |
| P4-2 | Mobile mode: cart/RC car with GPS or simulated coordinates | agent-1 | TODO | Gate 3 |
| P4-3 | Fix every bug seen in rehearsal (each agent in its own lane) | all | TODO | Gate 3 |

Nothing new after hour 30.

## Phase 5 — Ship the story (hours 30–36)

| ID | Task | Agent | Status | Depends on |
|----|------|-------|--------|------------|
| P5-1 | Record 2-minute backup demo video | agent-4 | TODO | P4-3 |
| P5-2 | Devpost submission page + 5-slide deck | agent-4 | TODO | P4-3 |
| P5-3 | Science and QNX Q&A prep sheets (feed into slides) | agent-3 + agent-1 | TODO | — |
| P5-4 | Rehearse demo three times with a timer | all | TODO | P5-1 |

## Blockers and handoffs

Add a row when you are blocked or when your output is ready for someone else. Remove the row when resolved.

| Date/time | From | To | What |
|-----------|------|----|------|
| 2026-11-21 | agent-3 | agent-4 | Q1 + Q2 answered: results endpoint shapes (`/api/density`, `/api/simulation`, `/api/allocate`, `/api/zones`) and the overfitting approach are in `ground/README.md` under "Results endpoints". Mostly your proposal; changes listed there. Remove this row once you have read it. |
| 2026-11-21 | agent-4 | agent-2 | Q2 in `docs/OPEN_QUESTIONS.md`: overfitting. agent-2: if fine-tuning (P3-6), split train/test by clip, not frame. (agent-3 part answered in `ground/README.md`.) |
| 2026-11-21 | agent-2 | agent-1 | Label table ready: `models/labels.csv` (`class_id,species,model_index,demo_only`). Pod ids are compact (1 bird … 10 giraffe), not the raw model indices used in the `event_schema.json` examples. Class 11 `teddy_bear` has `demo_only=1` (plush demo set). (agent-3: read; fake_pod now uses this file, and density/simulation will skip demo_only.) agent-1: pod-uplink reads `species` from this file. Remove this row once read. |
| 2026-11-21 | agent-4 | humans, agent-1 | Q3 + Q4 in `docs/OPEN_QUESTIONS.md`: extra sensors (PIR/BME280) and the NESDR Smartee SDR. Recommendation: at most one sensor after Gate 2; SDR goes on the laptop (not ESP32) as an optional Phase 4 stretch. Scope decision for humans. |
| 2026-11-21 | agent-1 | agent-2 | P1-1 ready for pod-infer: `pod-capture` publishes 640x480 YUYV (`POD_PIX_YUY2`) frames to shm ring `/pod_frames` via `pod/ipc/ring.h`. Read them the way `pod/capture/capture_check.cpp` does (`ShmRing::open`, `wait`, `acquire_latest`, `release`); `t_capture_ns` is set for your latency numbers. Run order on the Pi: `sh pod/scripts/camera_service.sh pod`, then `pod-capture --prio 30`, then pod-infer. Remove this row once read. |
