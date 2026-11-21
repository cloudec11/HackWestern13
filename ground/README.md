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

## Results endpoints (planned: agent-3 answer to agent-4's Q1)

These aren't built yet (P2-4, P2-5, P3-4). The shapes below are what they'll return, so the dashboard can build against a fake file with exactly these shapes now. They're served by `ground/api.py` and aren't part of `shared/`. agent-3 changes them only after telling agent-4.

They start from agent-4's proposal, with these changes:

- Every response is a list under `results` (or `rows`), so one call fills a whole panel.
- Every result names its `zone_id` and carries `"illustrative": true` plus `params.source`, so the dashboard can label the made-up inputs.
- The forecast runs 10 years, as in the plan (not 20), and states the `threshold` it measures risk against.
- Density uses `camera_days` (what the Random Encounter Model uses) instead of `hours`.
- Allocation rows use the plan's table columns: zone, intervention, cost, risk before, risk after.

### `GET /api/density?zone_id=&species=` (both optional)

```json
{
  "generated_ms": 1795290000000,
  "results": [
    {
      "zone_id": "zone-a", "species": "zebra",
      "density_per_km2": 4.2, "ci_low": 2.9, "ci_high": 6.1,
      "n_visits": 37, "camera_days": 6.5, "area_km2": 5.0,
      "n0": 21, "n0_ci": [15, 31],
      "params": { "v_km_per_day": 12.0, "r_km": 0.01, "theta_rad": 1.05, "source": "illustrative" },
      "illustrative": true
    }
  ]
}
```

`ci_low` and `ci_high` are a 90% interval from bootstrapping over camera-days. `n0` is `density × area`, the starting population for the simulation. If a zone/species has fewer than 5 visits, the result carries `"insufficient_data": true` and null numbers; show it as "not enough data yet".

### `GET /api/simulation?zone_id=&species=` (both optional)

```json
{
  "generated_ms": 1795290000000,
  "results": [
    {
      "zone_id": "zone-a", "species": "zebra",
      "years": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
      "p5":  [15, ...], "p25": [18, ...], "p50": [21, ...], "p75": [25, ...], "p95": [31, ...],
      "threshold": 50, "p_below_threshold": 0.12, "n_runs": 1000,
      "params": {
        "r_g": [0.15, 0.35], "K": [80, 160], "sigma": 0.15, "H": 2.0,
        "source": { "n0": "pod", "r_g": "literature", "K": "literature", "sigma": "illustrative", "H": "illustrative" }
      },
      "illustrative": true
    }
  ]
}
```

Each percentile array has one value per year (11 values for years 0–10). `p_below_threshold` is the share of runs that fall below `threshold` at any point within 10 years. For the dashboard's dashed line, use `threshold`. `r_g` and `K` are ranges because each run draws its own values (see Q2).

### `GET /api/allocate?budget=50000`

```json
{
  "budget": 50000, "spent": 48000, "unallocated": 2000,
  "rows": [
    { "zone_id": "zone-b", "species": "elephant", "intervention": "patrols",
      "cost": 12000, "risk_before": 0.18, "risk_after": 0.07, "risk_reduction_per_1k": 0.0092 }
  ],
  "illustrative": true
}
```

`intervention` is one of `patrols`, `habitat`, `more_pods`. Rows are in the order the greedy allocator picked them, best value per $1,000 first. The slider stays fast because the slow part (rerunning the simulation per option) is cached. Changing `budget` only reruns the greedy pick, so expect well under 100 ms per call. Debounce the slider anyway (about 200 ms).

### `GET /api/zones`

```json
{ "zones": [ { "zone_id": "zone-a", "name": "North ridge", "area_km2": 5.0, "pods": ["pod-01"] } ] }
```

Demo zones will be seeded with the fake Pods mapped to them (pod-01 → zone-a, and so on).

### Q2 (overfitting): agent-3 agrees with agent-4's proposal

- `r_g` and `K` come from published ranges per species (priors), never fitted to our few hours or days of data.
- Pod data sets only the starting population `n0`. A sustained trend in visits per camera-day may nudge `r_g` within its range; when it does, `params.source.r_g` becomes `"literature+pod_trend"`.
- Each of the 1,000 runs draws its own `r_g`, `K` and `n0` (from the density interval) as well as yearly noise. The fan chart then shows parameter uncertainty, not just weather.
- The model stays two-parameter Ricker plus losses. No regression model, so ridge isn't needed.
- Check: run density on `fake_pod --backfill-days 30` data, where the true visit rates are known, and confirm it recovers them. That will be a test in `ground/tests/`.

## Tests

```
ground/.venv/Scripts/python -m pytest ground/tests -q
```
