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

## Tests

```
ground/.venv/Scripts/python -m pytest ground/tests -q
```
