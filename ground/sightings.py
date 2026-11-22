"""Expected sightings and early-warning alerts (agent-3).

Standard library only, so it runs on the QNX Pi as well as the laptop.

Question: given how often a species has been seen at a zone's cameras, how
many sightings should the last few days have had? And is the real number
surprisingly low (or high)?

    window    the last `window_days` up to the latest data (what we check)
    baseline  the `baseline_days` before the window (what "normal" is)

The expected range uses ONLY the baseline. It's a block bootstrap: draw whole
past days at random (as many as the window has camera-days), add up their
sightings, and scale to the window's actual camera time. Repeat 2,000 times; the
5th-95th percentiles are the expected range. Resampling whole days keeps
real-world lumpiness (herds, busy days), so normal variation doesn't look like
an emergency the way a plain Poisson assumption would.

    status  below        fewer than the 5th percentile: something may have changed
            above        more than the 95th percentile
            new          never seen in the baseline, seen now
            as_expected  inside the range
            not_enough_data

Camera time comes only from heartbeats (as in density.py), so a camera that
was off doesn't count as "saw nothing".

It also turns the 10-year forecast (in animals) into expected sightings per month,
using the same Random Encounter Model run backwards:
    sightings per camera-day = (N / area) * v * r * (2 + theta) / pi
"""
from __future__ import annotations

import math
import random
import sqlite3
import zlib
from collections import defaultdict

from ground import params
from ground.density import _covered, running_intervals

DAY_MS = 86_400_000
SIMS = 2000
MIN_BASELINE_DAYS = 3.0      # camera-days of baseline needed before judging anything
MIN_WINDOW_DAYS = 0.25       # 6 h of camera time in the window
FULL_BLOCK = 0.5             # a baseline day must have >= 12 h of camera time to be resampled
# Alerts fire at 1% per direction, not at the edge of the 90% band shown on screen. A screen runs
# ~15 zone/species pairs x 2 checks x 2 directions at once; at 5% per direction that left about
# 4 false alerts showing at any time (measured), which teaches people to ignore alerts.
ALERT_TAIL = 0.01
Z_ALERT = 2.3263             # normal quantile for 1% per tail


def _overlap(intervals: list[tuple[int, int]], a: int, b: int) -> float:
    """Camera-days of running time inside [a, b)."""
    return sum(max(0, min(e, b) - max(s, a)) for s, e in intervals) / DAY_MS


def expected_range(blocks: list[tuple[float, int]], window_effort: float, seed: str) -> tuple[float, ...]:
    """Block bootstrap of the window's sighting count from past (effort_days, count) days."""
    pool = [(e, c) for e, c in blocks if e >= FULL_BLOCK]
    m = max(1, round(window_effort))
    rng = random.Random(zlib.crc32(seed.encode()))
    sims = []
    n = len(pool)
    for _ in range(SIMS):
        # two stages: (1) resample the baseline itself, because 28 days only estimate
        # "normal"; (2) draw the window's days from that resample. Stage 1 alone was
        # missing at first and gave 11% false alarms on steady data instead of ~5%.
        world = [pool[rng.randrange(n)] for _ in range(n)]
        e = c = 0.0
        for _ in range(m):
            be, bc = world[rng.randrange(n)]
            e += be
            c += bc
        sims.append(c * window_effort / e)
    sims.sort()
    q = lambda f: sims[int(f * (len(sims) - 1))]
    return q(0.05), q(0.5), q(0.95), q(ALERT_TAIL), q(1 - ALERT_TAIL)


def trend(days: list[tuple[float, int, float]]) -> dict | None:
    """Is the sighting rate drifting? Poisson log-linear regression of daily counts on time,
    with camera time as exposure: rate(t) = exp(a + b*t). The standard error is inflated
    by the observed over-dispersion (quasi-Poisson), so lumpy days don't fake a trend.

    days: (effort_days, count, t_days) with t in days (negative = in the past).
    Returns the weekly % change with a 90% interval, or None if there's too little data.
    This catches SLOW declines, which the window-vs-baseline check misses because a
    gradual fall drags the baseline down with it."""
    pts = [(e, c, t) for e, c, t in days if e >= FULL_BLOCK]
    if len(pts) < 7 or sum(c for _, c, _ in pts) < 5:
        return None
    a, b = math.log(sum(c for _, c, _ in pts) / sum(e for e, _, _ in pts)), 0.0
    for _ in range(50):                                   # Newton-Raphson (IRLS) on (a, b)
        ua = ub = iaa = iab = ibb = 0.0
        for e, c, t in pts:
            mu = e * math.exp(a + b * t)
            ua += c - mu; ub += t * (c - mu)
            iaa += mu; iab += t * mu; ibb += t * t * mu
        det = iaa * ibb - iab * iab
        if det <= 0:
            return None
        da, db = (ibb * ua - iab * ub) / det, (iaa * ub - iab * ua) / det
        a, b = a + da, b + db
        if abs(da) + abs(db) < 1e-9:
            break
    mus = [e * math.exp(a + b * t) for e, _, t in pts]
    phi = max(1.0, sum((c - mu) ** 2 / mu for (e, c, t), mu in zip(pts, mus) if mu > 0) / (len(pts) - 2))
    se_b = math.sqrt(phi * iaa / det)
    z90 = 1.6449
    wk = lambda x: round(100 * (math.exp(7 * x) - 1), 1)
    lo, hi = b - z90 * se_b, b + z90 * se_b
    alo, ahi = b - Z_ALERT * se_b, b + Z_ALERT * se_b      # stricter limits decide the alert
    return {"weekly_change_pct": wk(b), "ci90_pct": [wk(lo), wk(hi)], "days_used": len(pts),
            "dispersion": round(phi, 2),
            "status": "declining" if ahi < 0 else "increasing" if alo > 0 else "no_clear_trend"}


def zone_sightings(conn: sqlite3.Connection, zone: dict, window_days: float, baseline_days: int,
                   species_filter: str | None = None) -> list[dict]:
    pods = zone["pods"]
    if not pods:
        return []
    marks = ",".join("?" for _ in pods)
    hb: dict[str, list[int]] = defaultdict(list)
    for pod, t in conn.execute(f"SELECT pod_id, t_wall_ms FROM heartbeats WHERE pod_id IN ({marks})", pods):
        hb[pod].append(t)
    if not hb:
        return []
    spans = {p: running_intervals(ts) for p, ts in hb.items()}
    as_of = max(max(ts) for ts in hb.values())
    w0 = int(as_of - window_days * DAY_MS)
    b0 = w0 - baseline_days * DAY_MS
    excluded = params.demo_only_species()
    # counts per species: window, and per baseline day-block (aligned back from the window start)
    win: dict[str, int] = defaultdict(int)
    base: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    daily: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))   # day k back from as_of, for trend
    for pod, t, sp in conn.execute(
            f"SELECT pod_id, t_start_ms, species FROM events WHERE pod_id IN ({marks}) AND t_start_ms >= ?",
            [*pods, b0]):
        if sp in excluded or not _covered(spans.get(pod, []), t):
            continue
        daily[sp][(as_of - t) // DAY_MS] += 1
        if t >= w0:
            win[sp] += 1
        else:
            base[sp][(w0 - t - 1) // DAY_MS] += 1           # block k = 0 is the day just before the window
    eff_w = sum(_overlap(iv, w0, as_of + 1) for iv in spans.values())
    eff_b = [(sum(_overlap(iv, w0 - (k + 1) * DAY_MS, w0 - k * DAY_MS) for iv in spans.values())) for k in range(baseline_days)]
    span = int(math.ceil(window_days)) + baseline_days
    eff_day = [sum(_overlap(iv, as_of - (k + 1) * DAY_MS, as_of - k * DAY_MS + 1) for iv in spans.values())
               for k in range(span)]
    out = []
    species = [species_filter] if species_filter else sorted(set(win) | set(base))
    for sp in species:
        if sp in excluded:
            continue
        blocks = [(eff_b[k], base[sp].get(k, 0)) for k in range(baseline_days)]
        actual = win.get(sp, 0)
        base_visits = sum(c for _, c in blocks)
        r = {
            "zone_id": zone["zone_id"], "species": sp,
            "window": {"start_ms": w0, "end_ms": as_of, "camera_days": round(eff_w, 2), "actual": actual},
            "baseline": {"days": baseline_days, "camera_days": round(sum(e for e, _ in blocks), 2),
                         "visits": base_visits},
            "expected": None, "status": "not_enough_data", "change_pct": None,
        }
        r["trend"] = trend([(eff_day[k], daily[sp].get(k, 0), -(k + 0.5)) for k in range(span)])
        full_days = sum(1 for e, _ in blocks if e >= FULL_BLOCK)
        if eff_w < MIN_WINDOW_DAYS or sum(e for e, _ in blocks) < MIN_BASELINE_DAYS or full_days < 3:
            r["note"] = (f"needs {MIN_BASELINE_DAYS:g}+ camera-days of history and "
                         f"{MIN_WINDOW_DAYS * 24:g}+ h in the window")
            out.append(r)
            continue
        if base_visits == 0:
            r.update(expected={"p5": 0, "p50": 0, "p95": 0},
                     status="new" if actual else "as_expected",
                     note="never seen in the baseline" if actual else "not seen before or now")
            out.append(r)
            continue
        lo, mid, hi, alo, ahi = expected_range(blocks, eff_w, f"{zone['zone_id']}/{sp}/{w0 // DAY_MS}")
        r["expected"] = {"p5": round(lo, 1), "p50": round(mid, 1), "p95": round(hi, 1)}
        r["change_pct"] = round(100 * (actual - mid) / mid, 1) if mid else None
        r["expected"]["alert_limits"] = [round(alo, 1), round(ahi, 1)]
        r["status"] = "below" if actual < alo else "above" if actual > ahi else "as_expected"
        out.append(r)
    return out


def per_animal_daily_rate(species: str, area_km2: float) -> float | None:
    """Expected sightings per camera-day contributed by one animal in the zone (REM inverted)."""
    sp = params.SPECIES.get(species, {})
    if not sp.get("rem_ok") or not sp.get("v_km_per_day"):
        return None
    cam = params.CAMERA
    return sp["v_km_per_day"] * cam["r_km"] * (2 + cam["theta_rad"]) / math.pi / area_km2


def forecast_in_sightings(sim_result: dict, area_km2: float, n_cameras: int) -> dict | None:
    """The 10-year forecast (animals) expressed as expected sightings per 30 days across the zone's cameras."""
    k = per_animal_daily_rate(sim_result["species"], area_km2)
    if k is None or not sim_result.get("p50"):
        return None
    f = k * 30 * max(1, n_cameras)
    return {"years": sim_result["years"],
            **{p: [round(v * f, 1) for v in sim_result[p]] for p in ("p5", "p50", "p95")},
            "per_animal_per_camera_day": round(k, 5), "cameras": max(1, n_cameras)}
