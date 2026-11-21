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

## 2026-11-21 15:42:08 | agent-3 | - | REVIEW
**What:** Answered agent-4's Q1 (results endpoint shapes) and Q2 (simulation overfitting) in ground/README.md
**Files:** `ground/README.md`, `TASKS.md`
**Notes:** agent-4: build the dashboard fakes against the shapes in ground/README.md 'Results endpoints'. Kept your percentile fan chart (p5/p25/p50/p75/p95). Changes from your proposal: lists under results/rows; zone_id, illustrative:true and params.source on every result; 10-year horizon (plan) with an explicit threshold; camera_days not hours; insufficient_data flag under 5 visits; allocate rows = zone_id, species, intervention (patrols|habitat|more_pods), cost, risk_before, risk_after, risk_reduction_per_1k in greedy order; new GET /api/zones. Slider: per-option sims are cached, budget changes only rerun the greedy pick (<100 ms); debounce ~200 ms. Q2: agreed. Literature priors for r_g and K, Pod sets n0 only (optional trend nudge labelled literature+pod_trend), each run samples r_g, K and n0 plus yearly noise, no regression model. Did not edit docs/OPEN_QUESTIONS.md (agent-4's lane). Q3/Q4 are scope calls for the humans.

## 2026-11-21 16:10:32 | agent-3 | P1-3 | DOING
**What:** Rewriting ground API on the Python standard library only, so it runs on the QNX Pi as well as the laptop
**Notes:** Pi check (Python 3.14 on QNX 8): stdlib sqlite3/http.server/wsgiref all work; numpy, fastapi, uvicorn, pydantic, jsonschema are not installed. Endpoints, JSON shapes and behaviour stay the same. Read agent-2's models/labels.csv: ids match fake_pod placeholders; teddy_bear (demo_only) will be excluded from density/simulation.

## 2026-11-21 16:15:43 | agent-3 | P1-3 | REVIEW
**What:** Ground API rewritten on the Python standard library only; runs unchanged on the laptop and on the QNX Pi
**Files:** `ground/api.py`, `ground/schema_check.py`, `ground/fake_pod.py`, `ground/tests/conftest.py`, `ground/tests/test_api.py`, `ground/tests/test_fake_pod.py`, `ground/tests/test_schema_check.py`, `ground/requirements.txt`, `ground/README.md`, `TASKS.md`
**Notes:** Same endpoints, JSON shapes, 422/ack/duplicate behaviour. Now a wsgiref WSGI app with a threaded server: run 'python -m ground.api [--host --port --db --quiet]' (no uvicorn). New: 404/405/413 JSON errors, CORS preflight, query-param bounds. ground/schema_check.py validates against shared/event_schema.json without jsonschema and refuses unsupported keywords; cross-checked against jsonschema on 15,362 generated valid/invalid payloads with zero disagreements. 46 pytest tests pass. Laptop smoke on plain Python: 14-day backfill + flaky live run, every event acked, person batch 422, 200 concurrent heartbeats 0 errors. Pi smoke (QNX 8, Python 3.14) in /tmp/agent3-smoke on 127.0.0.1:18000, folder deleted after: 621 events all acked, queue 0, person 422, summary in 53 ms, ~11 s for the full run. requirements.txt is now dev/test only (pytest, jsonschema). fake_pod reads models/labels.csv. agent-1: on the Pi, run 'python3 -m ground.api' from the repo and point pod-uplink at http://127.0.0.1:8000 if the team picks the all-on-Pi demo. agent-4: if the dashboard is served from the Pi it must be plain HTML/JS (no Streamlit on QNX).

## 2026-11-21 16:20:54 | agent-3 | P2-4 | DOING
**What:** Claiming P2-4: density estimate (Random Encounter Model) + /api/density and /api/zones
**Notes:** Also updated my P1-3 row wording (no longer FastAPI).

## 2026-11-21 17:30:14 | agent-3 | P2-4 | REVIEW
**What:** Density estimate (Random Encounter Model) in ground/density.py; GET /api/density and GET /api/zones live
**Files:** `ground/density.py`, `ground/params.py`, `ground/api.py`, `ground/fake_pod.py`, `ground/tests/test_density.py`, `ground/README.md`, `TASKS.md`
**Notes:** D = (y/t)*pi/(v*r*(2+theta)), camera-days from heartbeat/visit continuity (gaps >30 min = outage), 90% CI by bootstrapping pod-days, n0 = D*area. Parameters (v per species, camera r/theta, zones a/b/c = pod-01/02/03) are ILLUSTRATIVE in ground/params.py and flagged in every result. Birds: not_applicable (model assumes ground movement). teddy_bear/unknown excluded via models/labels.csv demo_only. fake_pod backfill now sends heartbeats every --hb-every-min (default 10) and supports --outage. 59 tests pass; on fake data with known rates the 90% CI held the truth 18/20 times, mean error 8%. Laptop: 3 pods x 21 days, endpoint 0.3 s. NOT tested on the Pi (offline). agent-4: see the Blockers row for the two extra fields.

## 2026-11-21 17:36:17 | agent-3 | - | REVIEW
**What:** GET /api/scene: live scene JSON for agent-4's habitat view (ground/dashboard/sim), from real visit counts
**Files:** `ground/api.py`, `ground/params.py`, `ground/tests/test_api.py`, `ground/README.md`, `TASKS.md`
**Notes:** Shape follows agent-4's fake_scene.json: pod_id, biome (+biome_source), weather (+source), animals [{species,count}]. animals = visits for the Pod in the last ?hours (default 24), most-seen first, unknown excluded, teddy_bear kept for the plush demo. pod_id defaults to the most recently seen Pod. biome is a per-zone setting added to ground/params.py (zone-a savanna, zone-b forest, zone-c desert). Weather is a labelled placeholder: nobody owns weather yet. Verified in the browser pane: API + fake_pod live feed + http.server on the sim folder, page renders the API data with no console errors and picks up new visits on its 30 s refresh. 60 tests pass.

## 2026-11-21 17:39:46 | agent-3 | P2-5 | DOING
**What:** Claiming P2-5: 10-year Ricker Monte Carlo forecast + /api/simulation

## 2026-11-21 17:44:00 | agent-3 | P2-5 | REVIEW
**What:** 10-year population forecast: stochastic Ricker Monte Carlo in ground/simulate.py, GET /api/simulation live
**Files:** `ground/simulate.py`, `ground/params.py`, `ground/api.py`, `ground/tests/test_simulate.py`, `ground/README.md`, `TASKS.md`
**Notes:** N[t+1] = N exp(r_g(1-N/K)+e) - loss_rate N, 1000 runs x 10 years; each run draws n0 (log-normal from the density 90% CI), r_g (species range) and K (= today x 0.9-1.6, assumes near capacity). loss_rate = species rate x zone threat (a 1.0, b 0.6, c 1.5). Outputs fan-chart percentiles, p_below_threshold (default 50, ?threshold=), starts_below_threshold. Only n0 comes from the Pod; growth/K/noise/losses are ILLUSTRATIVE and labelled so (fixed my earlier README draft that said 'literature'). Deterministic per zone/species. 72 tests pass (12 behaviour tests: converges to K, losses>growth -> decline + risk, wider n0 CI -> wider fan, zone threat scaling, ordering, determinism). Real server: 3 pods x 14 days fake data, endpoint 0.43 s; zone-c elephant 48 -> 27 median by year 10, 99.5% risk; zone-b elephant stable, 1.2%. Not tested on the Pi (offline). Next (user request): backtest against real historical counts.

