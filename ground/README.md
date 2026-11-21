# Ground station (laptop)

Python 3.11+. The contract is `shared/event_schema.json` v1.

## Setup (once)

```
python -m venv ground/.venv
ground/.venv/Scripts/python -m pip install -r ground/requirements.txt   # Windows
# macOS/Linux: ground/.venv/bin/python -m pip install -r ground/requirements.txt
```

## Run the ingest API (P1-3, agent-3)

From the repo root:

```
ground/.venv/Scripts/python -m uvicorn ground.api:app --host 0.0.0.0 --port 8000
```

- The Pod uses `POD_GROUND_URL=http://<laptop-ip>:8000`. The laptop must allow inbound TCP 8000 in its firewall.
- The data goes in `ground/db/pod.sqlite3` (git-ignored). Set `POD_DB=<path>` to use another file. Delete the file to start fresh.
- Interactive API docs: http://localhost:8000/docs

| Endpoint | Body / params | Returns |
|----------|---------------|---------|
| `POST /api/events` | `event_batch` (1–50 events, one pod) | `event_ack`: `acked_seq` = highest contiguous seq held |
| `POST /api/heartbeat` | `heartbeat` | `heartbeat_ack` (`acked_seq` for cursor resync) |
| `GET /api/summary?recent=20` | — | pods (acked_seq, n_events, last_heartbeat), per-species visit counts, newest visits |
| `GET /api/events?pod_id=&species=&since_ms=&limit=200` | — | raw visits, newest first |
| `GET /health` | — | `{"ok": true}` |

A malformed batch gets a 422 with a list of `{path, error}`, and nothing from that batch is stored. Resending events the server already has is harmless: they're counted as `duplicates`.

## Fake Pod (P1-4, agent-3)

This script stands in for the real Pod until the Pi side is done. It sends schema-valid visits and heartbeats the same way `pod-uplink` will: batches of up to 50, resending until `acked_seq` covers them. With the server running:

```
python -m ground.fake_pod                                   # live feed, ~6 visits/min, forever (Ctrl+C)
python -m ground.fake_pod --pods 3 --live-rate 20           # 3 Pods, busier
python -m ground.fake_pod --backfill-days 30 --trend elephant=-5   # 30 days of history in seconds
python -m ground.fake_pod --drop 0.2 --loss 0.1 --dup 0.1   # flaky network: outages, lost and repeated events
python -m ground.fake_pod --mobile                          # adds simulated lat/lon
python -m ground.fake_pod --dry-run --duration 5            # print JSON, send nothing
```

- Rerunning with the same pod id is fine. It asks the server how far it got and continues numbering from there.
- The default URL is `http://127.0.0.1:8000`. On Windows, `localhost` adds about 2 s per request.
- Class ids are placeholders until agent-2's label table lands in `models/`. Every number it produces is made up.

If you pulled a schema change in `ground/db/schema.sql`, delete `ground/db/pod.sqlite3` once so it is recreated.

## Tests

```
ground/.venv/Scripts/python -m pytest ground/tests -q
```
