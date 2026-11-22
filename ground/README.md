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

### `GET /api/simulation?zone_id=&species=&threshold=50` (built, P2-5)

```json
{
  "generated_ms": 1795290000000,
  "results": [
    {
      "zone_id": "zone-c", "species": "elephant",
      "years": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
      "p5":  [37, 33, 31, ...], "p25": [...], "p50": [48, 45, 42, ...], "p75": [...], "p95": [62, 59, 58, ...],
      "threshold": 50, "p_below_threshold": 0.995, "starts_below_threshold": true, "n_runs": 1000,
      "params": {
        "n0": 48, "n0_ci": [34, 59],
        "r_g": [0.04, 0.07], "K": [43, 77], "sigma": 0.08,
        "loss_rate": 0.075, "H": 3.6, "zone_threat": 1.5,
        "source": { "n0": "pod", "r_g": "illustrative", "K": "illustrative: assumes habitat near capacity today",
                    "sigma": "illustrative", "H": "illustrative" }
      },
      "illustrative": true
    }
  ]
}
```

Each percentile array has one value per year (11 values for years 0–10). `p_below_threshold` is the share of runs that fall below `threshold` in any of years 1–10. For the dashboard's dashed line, use `threshold`.

As built (`ground/simulate.py`, parameters in `ground/params.py`):

- **Model:** `N[t+1] = N[t]·exp(r_g·(1 − N[t]/K) + noise) − loss_rate·N[t]`, run 1,000 times.
- **Per-run draws:** each run draws its own starting population (from the density interval), growth rate `r_g` and capacity `K`. The fan shows parameter uncertainty as well as good and bad years.
- **Sources:** only `n0` comes from the Pod. Growth, noise and losses are **illustrative** per species, and the labels now say so. (The earlier draft said "literature", which wasn't accurate: nobody has checked these values against published studies.)
- **Capacity:** `K` assumes each zone is near capacity today (today's estimate × 0.9–1.6), since there's no habitat data.
- **Threats:** `loss_rate` is the species' rate × the zone's threat level (zone-a 1.0, zone-b 0.6, zone-c 1.5). Patrols in P3-4 will lower it.
- **Not forecast:** birds (`not_applicable`) and zones without enough visits (`insufficient_data`) come back with empty arrays and a `note`.
- **`starts_below_threshold`** is true when today's estimate is already under the threshold.
- **Repeatable:** the same data gives the same numbers on every call, so the dashboard doesn't flicker.
- **Speed:** about 0.4 s on the laptop for 3 zones × 5 species, density included.

### Backtest against real counts (`python -m ground.backtest`)

This tests the forecast on **Isle Royale moose, 1980–2026** (U.S. National Park Service, public domain, `ground/data/`). For every start year, it forecasts the next 1–5 years and compares the forecast with what actually happened. The comparison includes a "nothing changes" baseline. The moose settings were fixed before looking at any errors (`ground/backtest.py`).

**First result, before calibration** (σ 0.15 as originally guessed), over 44 start years:

| Years ahead | Model error | "No change" error | Actual inside 90% band | Bias |
|---|---|---|---|---|
| 1 | 15% | 16% | 81% | −6% |
| 3 | 34% | 33% | 51% | −17% |
| 5 | 41% | 42% | 44% | −21% |

The model was no better than "nothing changes", and its 90% band was far too narrow.

**Calibration** (`python -m ground.backtest --calibrate`):
- **Tuning:** the capacity range and noise σ were tuned using only counts up to 2004, scored with the interval score (Gneiting & Raftery 2007), which rewards bands that are narrow *and* contain the truth.
- **Judging:** the result was then judged only on forecasts starting in 2005 or later, which tuning never saw.
- **Choice:** the capacity range stayed at 0.9–1.6, and σ doubled from 0.15 to 0.30.

| On 2005–2026 (held out), 80 comparisons | Interval score | Median error | "No change" error | Actual inside 90% band |
|---|---|---|---|---|
| Original (σ 0.15) | 2.40 | 29.9% | 29.7% | 57.5% |
| **Calibrated (σ 0.30), now deployed** | **1.56** | 31.1% | 29.7% | **93.8%** |

**Deployed:** `params.NOISE_SCALE = 2.0` doubles every species' σ, so species keep their relative variability instead of all copying the moose value. `params.source.sigma` says the scale was calibrated. A test fails if the deployed scale drifts from what calibration picks.

**What the fix does and doesn't do:**
- **Fixed:** the uncertainty is honest. The 90% band now holds the real count about 90% of the time on years it never saw.
- **Not fixed:** the median forecast is still about 30% off, the same as "nothing changes", and it still leans low. From 2010 (510 moose), it predicts about 500 a year, while the real counts reached 1,250 by 2015 as the wolves collapsed (19 → 3). That year now falls inside the band for 2011–2014, but not for 2015.
- **Why:** Isle Royale moose are driven by wolves, disease and hard winters, and a single-species model can't see any of them. Neither more data nor tuning fixes that; it would need those drivers as inputs.
- **Present it as:** the forecast honestly shows *how uncertain* the future is. It doesn't *predict* the path.
- **Caveats:** this is one population (moose), and the held-out set is small and overlapping (80 comparisons from 19 start years).

### `GET /api/allocate?budget=50000&threshold=50` (built, P3-4)

```json
{
  "budget": 50000, "spent": 50000, "unallocated": 0,
  "species_at_risk_before": 4.275, "species_at_risk_after": 3.596,
  "rows": [
    { "step": 1, "zone_id": "zone-c", "intervention": "patrols", "label": "Anti-poaching patrols",
      "cost": 12000, "risk_before": 2.59, "risk_after": 2.31, "risk_reduction_per_1k": 0.02333,
      "by_species": { "elephant": { "p_below_before": 0.98, "p_below_after": 0.92 }, "...": {} } }
  ],
  "options": [ { "zone_id": "zone-c", "intervention": "patrols", "cost": 12000,
                 "risk_before": 2.59, "risk_after": 2.31, "risk_reduction_per_1k": 0.02333,
                 "band_width_change": -0.02 } ],
  "actions": { "patrols": { "label": "Anti-poaching patrols", "cost": 12000, "source": "illustrative" } },
  "threshold": 50, "cached": true, "illustrative": true, "note": "..."
}
```

**Changes from the planned shape (agent-4):**
- **Rows are per zone, not per species.** A patrol protects every species in its zone, so one row = one zone and one action, with the species detail in `by_species`.
- **`risk_before` / `risk_after` are the zone's expected number of species falling below the threshold** in 10 years: the sum of their probabilities. That can be above 1.
- **New:** `options` (every single action on its own, for a "why" view), `actions` (costs and labels), `step`, and the totals `species_at_risk_before/after`.

As built (`ground/allocate.py`, costs and effects in `params.INTERVENTIONS`, all **illustrative**):

| Action | Cost | Effect on the forecast |
|---|---|---|
| `patrols` | $12,000 | the zone's loss rate × 0.5 |
| `habitat` | $20,000 | carrying capacity × 1.3 |
| `more_pods` | $6,000 | starting-count interval shrinks (×0.58 on a log scale: 3 cameras instead of 1) |

- **Scenarios:** every combination of actions per zone (8 scenarios) is forecast with shared random seeds, so differences come from the action, not from luck.
- **Greedy pick:** it buys the best risk cut per $1,000 given what the zone already has, and each action at most once per zone.
- **Noise floor:** it skips cuts smaller than `MIN_GAIN = 0.01` expected species, which is below the Monte Carlo noise.
- **Speed:** the scenario build takes about 2.3 s on the laptop for 3 zones × 14 days (not timed on the Pi). It's cached until new events or heartbeats arrive, so **slider calls take 9–29 ms**. Debounce the slider anyway (about 200 ms).
- **Example result on fake data, $50k:** patrols in zone-c, then patrols in zone-a, then habitat in zone-a, then more Pods in zone-a. Species at risk fell from 4.28 to 3.60.

**Honest limits:**
- **More Pods hardly move 10-year risk** (the band narrows by under 1%). After calibration, most uncertainty comes from year-to-year swings, not from the starting count. On a small budget, though, more Pods can still be the only affordable action that clears the 0.01 floor: zone-b's gain is 0.015, so $10k buys them. The real value of more cameras is better information for future decisions, which this risk score doesn't capture.
- **Treat this as a comparison of options, not a promise.** It inherits the forecast's limits (see the backtest above).

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

### `GET /api/sightings?zone_id=&species=&window_days=7&baseline_days=28` (built): expected sightings and alerts

This answers the questions "should we have seen more (or fewer) animals lately?" and "is the sighting rate drifting?" It works for every species, birds included, because it needs no ecology parameters.

```json
{ "window_days": 7, "baseline_days": 28, "cached": false, "data_age_s": 0,
  "alerts": [ { "kind": "sudden", "zone_id": "zone-c", "species": "giraffe", "status": "below",
                "actual": 10, "expected": { "p5": 12, "p50": 20, "p95": 29 }, "change_pct": -50 },
              { "kind": "trend", "zone_id": "zone-a", "species": "elephant", "status": "declining",
                "weekly_change_pct": -58.1, "ci90_pct": [-70.2, -41.0] } ],
  "results": [ { "zone_id": "zone-a", "species": "zebra",
                 "window":   { "start_ms": 0, "end_ms": 0, "camera_days": 7.0, "actual": 43 },
                 "baseline": { "days": 28, "camera_days": 28.0, "visits": 170 },
                 "expected": { "p5": 26, "p50": 35, "p95": 42 }, "status": "above", "change_pct": 22.9,
                 "trend": { "weekly_change_pct": 3.1, "ci90_pct": [-4.0, 10.7], "status": "no_clear_trend",
                            "days_used": 35, "dispersion": 1.2 },
                 "p_decline_10y": 0.62,
                 "forecast_monthly": { "years": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
                                       "p5": [], "p50": [], "p95": [],
                                       "per_animal_per_camera_day": 0.0388, "cameras": 1 } } ] }
```

There are two complementary checks:

| Check | Catches | How |
|---|---|---|
| **Sudden** (`status`) | A sharp change in the last `window_days`: a poaching event, a herd leaving, a new species | The expected range comes **only from the baseline days before the window**. It's a two-stage block bootstrap over whole days, which keeps herd and busy-day lumpiness. `below` / `above` = outside the 90% range; `new` = never seen in the baseline |
| **Trend** (`trend`) | A **slow** decline (or rise) | A Poisson log-linear regression of daily counts on time, with camera time as exposure and over-dispersion-corrected errors. It gives the weekly % change with a 90% interval. `declining` = the whole interval is below 0 |

The sudden check misses slow declines, because a gradual fall drags the baseline down with it. That's what the trend check is for.

Alerts fire at the **1%-per-direction** level (`ALERT_TAIL`), while the screen still shows the 90% range. A dashboard runs about 15 zone/species pairs × 2 checks × 2 directions at once. At 5% per direction, that measured as about 4 false alerts showing at any time, which teaches people to ignore alerts. The limits that decide an alert are in `expected.alert_limits`.

Measured on generated data, 150 datasets per case (7-day window vs a 28-day baseline unless noted):

| Situation | Sudden alert | Trend alert |
|---|---|---|
| Steady (6 a day; 2 a day; or 3 d vs 10 d) | 2–5% any direction | 1–3% any direction |
| 10 a day, last week −30% | 51% | 17% |
| 10 a day, last week −50% | 98% | 53% |
| 10 a day, falling 10% a week | 22% | 48% |
| 2 a day, falling 30% a week | 8% | 54% |
| 2 a day, falling 60% a week | missed (baseline falls too) | 93% |

Moderate changes that don't reach an alert still show in the 90% range and in `trend.weekly_change_pct` with its 90% interval.

**Slow declines in rare species are hard to see in 5 weeks with one camera.** That's where more Pods earn their cost.

- `p_decline_10y` is the share of 10-year forecast runs that end below today's estimate (`null` where there's no forecast). It's also in `/api/simulation` as `p_decline`.
- `forecast_monthly` is the 10-year forecast (animals) converted to expected sightings per 30 days across the zone's cameras, using the Random Encounter Model in reverse: one animal gives `v·r·(2+θ)/π / area` sightings per camera-day. It's `null` for birds and other species without a forecast.
- Camera time comes only from heartbeats, the same as density. It needs 3+ camera-days of baseline and 6+ h in the window, otherwise `status` is `not_enough_data`.

### Changes after agent-1's review (2026-11-21)

- **Density no longer trusts visit times as camera time.** Without heartbeats, camera time fell to near zero, and 6 cat visits became 4.4 million cats. Now:
  - camera time comes only from heartbeats;
  - visits outside heartbeat-covered time are counted in `uncovered_visits`, not used;
  - an estimate needs at least 6 h (`MIN_CAMERA_DAYS`) of camera time.

  **Until agent-2's P3-3 heartbeats flow from the real Pod, real-Pod densities say `insufficient_data` ("no heartbeats …").** That's on purpose.
- **Caching:**
  - `/api/density`, `/api/simulation`, `/api/allocate` and `/api/sightings` are cached on the data version.
  - The version changes with new visits, and only every 60 heartbeats.
  - After data changes, a result is still reused for up to 10 s (30 s for `/api/allocate`), so a busy feed can't stall the dashboard. Responses carry `cached` and `data_age_s`.
  - Measured on the laptop: allocate takes 2.4 s first, then 0.03 s; sightings take 0.9 s first, then 0.03 s.
  - **Measured on the QNX Pi 5** (3 Pods × 14 days, lowest priority, alongside the running Pod): density 0.28 s, simulation 0.62 s, allocate 3.26 s, sightings 1.02 s on the first call; **9–12 ms** once cached. Loading history through the API is slow on the Pi's SD card (about 0.1 s per write with durable commits), which is fine for real heartbeats every 10 s but makes big fake backfills take minutes.
- **Tests:** the suite is about 25% faster here (tests skip the per-commit disk sync; the real server keeps it on).

### `GET /api/params` (built)

This returns every setting the science uses, read straight from `ground/params.py`: camera range and angle; per species, travel distance, growth range, noise (with the deployed value after `NOISE_SCALE`) and loss rate; zones with area, threat level, Pods and biome; the budget actions' costs and effects; and the forecast, density and allocation settings. Use it to show "numbers used" without copying values by hand. Everything except `NOISE_SCALE` is illustrative.

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
