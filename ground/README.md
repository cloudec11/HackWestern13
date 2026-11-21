# Ground station

Runs on the laptop **or on the QNX Pi**, using the **Python standard library only** (3.10+, no pip packages). Checked on Python 3.12 (Windows) and Python 3.14 (QNX 8.0, Raspberry Pi 5). The contract is `shared/event_schema.json` v1.

## Run the ingest API (P1-3, agent-3)

From the repo root, with any Python 3.10+:

```
python -m ground.api                                   # 0.0.0.0:8000, db ground/db/pod.sqlite3
python -m ground.api --port 8000 --db /data/pod.sqlite3 --quiet
```

On the Pi, it's the same command, run from the repo checkout (`python3` there). The Pod's uploader then uses `POD_GROUND_URL=http://127.0.0.1:8000`. If the API runs on the laptop instead, use `http://<laptop-ip>:8000`, and the laptop must allow inbound TCP 8000 in its firewall.

- The data goes in `ground/db/pod.sqlite3` (git-ignored). `--db` or `POD_DB=<path>` picks another file, and `POD_PORT` sets the port. Delete the file to start fresh.
- Requests are validated against `shared/event_schema.json` by `ground/schema_check.py`, a small stdlib validator. It refuses to load a schema that uses a keyword it doesn't implement, so a contract change can't be silently ignored.
- CORS is open, so a dashboard page from any origin can call it.

| Endpoint | Body / params | Returns |
|----------|---------------|---------|
| `POST /api/events` | `event_batch` (1–50 events, one pod) | `event_ack`: `acked_seq` = highest contiguous seq held |
| `POST /api/heartbeat` | `heartbeat` | `heartbeat_ack` (`acked_seq` for cursor resync) |
| `GET /api/summary?recent=20` | — | pods (acked_seq, n_events, last_heartbeat), per-species visit counts, newest visits |
| `GET /api/events?pod_id=&species=&since_ms=&limit=200` | — | raw visits, newest first |
| `GET /health` | — | `{"ok": true}` |

Errors are JSON, `{"detail": [{"path": ..., "error": ...}]}`: 422 for bad input (nothing from that batch is stored), 404 for an unknown path, 405 for a wrong method, 413 for a body over 1 MB. Resending events the server already has is harmless: they're counted as `duplicates`.

## Fake Pod (P1-4, agent-3)

This script stands in for the real Pod until the Pi side is done. It sends schema-valid visits and heartbeats the same way `pod-uplink` will: batches of up to 50, resending until `acked_seq` covers them. With the server running:

```
python -m ground.fake_pod                                   # live feed, ~6 visits/min, forever (Ctrl+C)
python -m ground.fake_pod --pods 3 --live-rate 20           # 3 Pods, busier
python -m ground.fake_pod --backfill-days 30 --trend elephant=-5   # 30 days of history (about a minute)
python -m ground.fake_pod --backfill-days 21 --outage 5-8          # camera off on days 5 to 8
python -m ground.fake_pod --drop 0.2 --loss 0.1 --dup 0.1   # flaky network: outages, lost and repeated events
python -m ground.fake_pod --mobile                          # adds simulated lat/lon
python -m ground.fake_pod --dry-run --duration 5            # print JSON, send nothing
```

- Rerunning with the same pod id is fine. It asks the server how far it got and continues numbering from there.
- The default URL is `http://127.0.0.1:8000`. On Windows, `localhost` adds about 2 s per request.
- Class ids come from agent-2's `models/labels.csv`. Every number it produces is made up.
- Also runs on the Pi: `python3 -m ground.fake_pod --url http://127.0.0.1:8000`.
- Backfill also sends a heartbeat every `--hb-every-min` simulated minutes (default 10). That's how the density estimate knows the camera was running. Most of the backfill time goes on these (about 17 ms each on the laptop), so raise it up to 29 for a quicker load. Above 30, the camera would look switched off.

If you pulled a schema change in `ground/db/schema.sql`, delete `ground/db/pod.sqlite3` once so it is recreated.

## Results endpoints (agent-3 answer to agent-4's Q1)

**Built:** `/api/density` and `/api/zones` (P2-4). **Planned:** `/api/simulation` (P2-5) and `/api/allocate` (P3-4). The shapes below are what they return, so the dashboard can build against a fake file with exactly these shapes now. They're served by `ground/api.py` and aren't part of `shared/`. agent-3 changes them only after telling agent-4.

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

As built (`ground/density.py`, parameters in `ground/params.py`):

- **Formula:** Random Encounter Model, `D = (visits / camera_days) × π / (v × r × (2 + θ))` (Rowcliffe et al. 2008). `v` is the species' daily travel distance, and `r` and `θ` are the camera's detection radius and angle. All three are **illustrative**, and the result says so in `params.source`.
- **`camera_days`** counts only time the camera was provably running: any stretch with no heartbeat or visit for over 30 minutes counts as an outage.
- **Birds** come back with `"not_applicable": true` and a `note`. The model assumes animals moving on the ground, so it doesn't apply to them.
- **`teddy_bear`** (the label table's `demo_only` class) and `unknown` are never estimated.
- **Zones:** zone-a/b/c are defined in `ground/params.py`, with pod-01 → zone-a, pod-02 → zone-b and pod-03 → zone-c. Any other Pod shows up under `unassigned`.
- **Filtering:** with no filter, each zone lists the species it has seen. Asking for one `species` lists it for every zone, with `insufficient_data` where a zone has no visits.
- **Speed:** about 0.3 s on the laptop for 3 Pods × 21 days. Not yet timed on the Pi.
- **Accuracy on fake data with known rates:** the 90% interval contained the true value in 18 of 20 independent datasets, with a mean error of 8%.

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

### `GET /api/scene?pod_id=&hours=24` (built, for agent-4's habitat view)

This serves the scene JSON that `ground/dashboard/sim` already reads, so the habitat view runs on live data:

```
python -m ground.api                                   # :8000
python -m http.server 8080 --directory ground/dashboard/sim
# open http://127.0.0.1:8080/?scene=http://127.0.0.1:8000/api/scene
```

```json
{ "pod_id": "pod-01", "zone_id": "zone-a", "hours": 24,
  "biome": "savanna", "biome_source": "zone setting",
  "weather": { "condition": "clear", "source": "placeholder (no weather feed yet)" },
  "animals": [ { "species": "bird", "count": 13 }, { "species": "zebra", "count": 7 } ] }
```

- `animals` holds real visit counts for that Pod over the last `hours`, most-seen first. `unknown` is left out, and `teddy_bear` is kept because the plush demo is meant to show it.
- `pod_id` defaults to the Pod that reported most recently.
- `biome` comes from the zone's setting in `ground/params.py`.
- `weather` is an honest placeholder: no one owns a weather feed yet. With no `hour`, the view uses the viewer's clock.

### `GET /api/zones`

```json
{ "zones": [ { "zone_id": "zone-a", "name": "North ridge", "area_km2": 5.0, "pods": ["pod-01"] } ] }
```

Built. Zones come from `ground/params.py` (not the database), and Pods outside them are listed under `unassigned`.

### Q2 (overfitting): agent-3 agrees with agent-4's proposal

- `r_g` and `K` come from published ranges per species (priors), never fitted to our few hours or days of data.
- Pod data sets only the starting population `n0`. A sustained trend in visits per camera-day may nudge `r_g` within its range; when it does, `params.source.r_g` becomes `"literature+pod_trend"`.
- Each of the 1,000 runs draws its own `r_g`, `K` and `n0` (from the density interval) as well as yearly noise. The fan chart then shows parameter uncertainty, not just weather.
- The model stays two-parameter Ricker plus losses. No regression model, so ridge isn't needed.
- Check: density is tested on fake data with known visit rates (`ground/tests/test_density.py`), and it recovers them.

## Tests (laptop)

The tests need `pytest`, plus `jsonschema` for the validator cross-check. These are dev-only and listed in `ground/requirements.txt`:

```
python -m venv ground/.venv
ground/.venv/Scripts/python -m pip install -r ground/requirements.txt
ground/.venv/Scripts/python -m pytest ground/tests -q
```
