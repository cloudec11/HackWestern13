"""Population density from camera visits: the Random Encounter Model (agent-3, P2-4).

Standard library only, so it runs on the QNX Pi as well as the laptop.

The camera can't tell individual animals apart, so visit counts aren't a
population. The Random Encounter Model (Rowcliffe, Field, Turvey & Carbone
2008, J. Applied Ecology 45: 1228-1236) gets around that. Animals moving at
average speed v cross a camera's detection zone (radius r, angle theta) at a
rate proportional to how many of them live there:

    D = (y / t) * pi / (v * r * (2 + theta))

    y      independent visits (one tracker visit event = one contact)
    t      camera-days the camera was actually running (from heartbeats)
    v      species day range, km/day          (params.SPECIES, illustrative)
    r      detection radius, km                (params.CAMERA, illustrative)
    theta  detection angle, radians            (params.CAMERA, illustrative)
    D      animals per km^2

The starting population for the simulation is n0 = D * zone area.

The uncertainty comes from a bootstrap over pod-days: resample the days with
replacement and recompute y/t, BOOTSTRAP_RESAMPLES times, then take the
CI_LEVEL percentile interval.

Model assumptions to state honestly: animals move independently of the
camera, the camera is placed randomly relative to their paths, and v, r and
theta are right. Here v, r and theta are illustrative.
"""
from __future__ import annotations

import bisect
import math
import random
import sqlite3
import zlib
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from ground import params

DAY_MS = 86_400_000


def rem_density(visits_per_day: float, v_km_per_day: float, r_km: float, theta_rad: float) -> float:
    """Random Encounter Model: animals per km^2."""
    if v_km_per_day <= 0 or r_km <= 0 or theta_rad <= 0:
        raise ValueError("v, r and theta must be positive")
    return visits_per_day * math.pi / (v_km_per_day * r_km * (2 + theta_rad))


@dataclass
class Unit:
    """One pod-day: how long the camera ran that day, and the visits it recorded."""
    pod_id: str
    day: int
    effort_days: float = 0.0
    counts: Counter = field(default_factory=Counter)


def running_intervals(timestamps: list[int], gap_ms: int = params.OUTAGE_GAP_MIN * 60_000) -> list[tuple[int, int]]:
    """Merge heartbeat times into [start, end] stretches when the camera was provably on.
    Two heartbeats no more than gap_ms apart count as continuous running."""
    ts = sorted(set(timestamps))
    out: list[list[int]] = []
    for a, b in zip(ts, ts[1:]):
        if b - a > gap_ms:
            continue
        if out and out[-1][1] == a:
            out[-1][1] = b
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def _covered(intervals: list[tuple[int, int]], t: int) -> bool:
    i = bisect.bisect_right(intervals, (t, float("inf"))) - 1
    return i >= 0 and intervals[i][0] <= t <= intervals[i][1]


def effort_units(timestamps_by_pod: dict[str, list[int]],
                 visits: list[tuple[str, int, str]],
                 gap_ms: int = params.OUTAGE_GAP_MIN * 60_000,
                 uncovered: Counter | None = None) -> list[Unit]:
    """Split each pod's running time into calendar-day units and attach visits.

    timestamps_by_pod: heartbeat times, i.e. moments the camera reported it was
    running. Visit times are deliberately NOT used here: a visit proves only a few
    seconds of running time, and counting them made camera time collapse to near
    zero with no heartbeats (agent-1's review: 6 cat visits read as 4.4 million cats).
    visits: (pod_id, t_start_ms, species). A visit counts only if it falls inside a
    running stretch; the rest are tallied in `uncovered` (pod_id, species) if given.
    """
    units: dict[tuple[str, int], Unit] = {}

    def unit(pod: str, day: int) -> Unit:
        key = (pod, day)
        if key not in units:
            units[key] = Unit(pod, day)
        return units[key]

    spans = {pod: running_intervals(ts, gap_ms) for pod, ts in timestamps_by_pod.items()}
    for pod, iv in spans.items():
        for a, b in iv:
            while a < b:                                  # split at UTC midnight
                day = a // DAY_MS
                end = min(b, (day + 1) * DAY_MS)
                unit(pod, day).effort_days += (end - a) / DAY_MS
                a = end
    for pod, t, species in visits:
        if _covered(spans.get(pod, []), t):
            unit(pod, t // DAY_MS).counts[species] += 1
        elif uncovered is not None:
            uncovered[(pod, species)] += 1
    return sorted(units.values(), key=lambda u: (u.pod_id, u.day))


def _rate(units: list[Unit], species: str) -> tuple[int, float]:
    return sum(u.counts[species] for u in units), sum(u.effort_days for u in units)


def _bootstrap_ci(units: list[Unit], species: str, seed: str, n: int, level: float) -> tuple[float, float] | None:
    """Percentile interval of visits/camera-day from resampling pod-days."""
    if len(units) < 2:
        return None
    rng = random.Random(zlib.crc32(seed.encode()))
    rates = []
    for _ in range(n):
        y = t = 0.0
        for _ in range(len(units)):
            u = units[rng.randrange(len(units))]
            y += u.counts[species]
            t += u.effort_days
        if t > 0:
            rates.append(y / t)
    if not rates:
        return None
    rates.sort()
    lo = rates[int((1 - level) / 2 * (len(rates) - 1))]
    hi = rates[int((1 + level) / 2 * (len(rates) - 1))]
    return lo, hi


def _sig(x: float | None, digits: int = 3) -> float | None:
    if x is None or x == 0:
        return x
    return round(x, max(0, digits - 1 - int(math.floor(math.log10(abs(x))))))


def estimate(units: list[Unit], zone_id: str, species: str, area_km2: float,
             uncovered_visits: int = 0) -> dict:
    """One /api/density result for a zone and species."""
    sp = params.SPECIES.get(species, {})
    cam = params.CAMERA
    y, t = _rate(units, species)
    out = {
        "zone_id": zone_id, "species": species,
        "density_per_km2": None, "ci_low": None, "ci_high": None,
        "n_visits": y, "uncovered_visits": uncovered_visits,
        "camera_days": round(t, 2), "area_km2": area_km2,
        "n0": None, "n0_ci": None,
        "params": {"v_km_per_day": sp.get("v_km_per_day"), "r_km": cam["r_km"],
                   "theta_rad": cam["theta_rad"], "source": params.SPECIES_SOURCE},
        "illustrative": True,
    }
    if not sp:
        out.update(not_applicable=True, note=f"no movement parameters for {species!r} yet")
        return out
    if not sp.get("rem_ok"):
        out.update(not_applicable=True, note=sp.get("note", "model not valid for this species"))
        return out
    if t < params.MIN_CAMERA_DAYS:
        why = (f"no heartbeats cover {uncovered_visits} visit(s), so camera running time is unknown"
               if uncovered_visits else "not enough camera running time yet")
        out.update(insufficient_data=True,
                   note=f"{why}; needs at least {params.MIN_CAMERA_DAYS * 24:g} h of heartbeats")
        return out
    if y < params.MIN_VISITS:
        out.update(insufficient_data=True, note=f"needs at least {params.MIN_VISITS} visits during camera running time")
        return out

    def dens(rate: float) -> float:
        return rem_density(rate, sp["v_km_per_day"], cam["r_km"], cam["theta_rad"])

    d = dens(y / t)
    ci = _bootstrap_ci(units, species, f"{zone_id}/{species}", params.BOOTSTRAP_RESAMPLES, params.CI_LEVEL)
    lo, hi = (dens(ci[0]), dens(ci[1])) if ci else (None, None)
    out.update(
        density_per_km2=_sig(d), ci_low=_sig(lo), ci_high=_sig(hi),
        n0=round(d * area_km2),
        n0_ci=[round(lo * area_km2), round(hi * area_km2)] if ci else None,
    )
    return out


# ---- Reading from the ground station database ----

def load_units(conn: sqlite3.Connection, pods: list[str] | None = None,
               uncovered_by_zone: dict | None = None) -> dict[str, list[Unit]]:
    """Effort units grouped by zone, from the events and heartbeats tables.
    If uncovered_by_zone is a dict, it is filled with {zone: Counter(species)} of
    visits that no heartbeat stretch covers."""
    excluded = params.demo_only_species()
    where, args = "", []
    if pods is not None:
        if not pods:
            return {}
        where = f" WHERE pod_id IN ({', '.join('?' for _ in pods)})"
        args = list(pods)
    stamps: dict[str, list[int]] = defaultdict(list)
    visits = []
    for pod, t in conn.execute(f"SELECT pod_id, t_wall_ms FROM heartbeats{where}", args):
        stamps[pod].append(t)
    for pod, t0, species in conn.execute(f"SELECT pod_id, t_start_ms, species FROM events{where}", args):
        if species not in excluded:
            visits.append((pod, t0, species))
    unc: Counter = Counter()
    by_zone: dict[str, list[Unit]] = defaultdict(list)
    for u in effort_units(stamps, visits, uncovered=unc):
        by_zone[params.zone_of(u.pod_id)].append(u)
    if uncovered_by_zone is not None:
        for (pod, sp), n in unc.items():
            uncovered_by_zone.setdefault(params.zone_of(pod), Counter())[sp] += n
    return dict(by_zone)


def zones(conn: sqlite3.Connection) -> list[dict]:
    """Configured zones plus any zone that has a Pod sending data (e.g. 'unassigned')."""
    seen = [r[0] for r in conn.execute("SELECT pod_id FROM pods ORDER BY pod_id")]
    out = []
    for z in params.ZONES:
        pods = sorted(set(z["pods"]) | {p for p in seen if params.zone_of(p) == z["zone_id"]})
        out.append({"zone_id": z["zone_id"], "name": z["name"], "area_km2": z["area_km2"], "pods": pods})
    stray = [p for p in seen if params.zone_of(p) == params.UNASSIGNED_ZONE["zone_id"]]
    if stray:
        u = params.UNASSIGNED_ZONE
        out.append({"zone_id": u["zone_id"], "name": u["name"], "area_km2": u["area_km2"], "pods": stray})
    return out


def density_results(conn: sqlite3.Connection, zone_id: str | None = None,
                    species: str | None = None) -> list[dict]:
    """All /api/density results, optionally filtered. Species with no visits in a
    zone are left out, unless a species filter asks for it explicitly."""
    uncovered: dict = {}
    units_by_zone = load_units(conn, uncovered_by_zone=uncovered)
    excluded = params.demo_only_species()
    results = []
    for z in zones(conn):
        if zone_id and z["zone_id"] != zone_id:
            continue
        units = units_by_zone.get(z["zone_id"], [])
        unc = uncovered.get(z["zone_id"], Counter())
        seen = sorted({s for u in units for s, c in u.counts.items() if c} | {s for s, n in unc.items() if n})
        wanted = [species] if species else seen
        for sp in wanted:
            if sp in excluded:
                continue
            results.append(estimate(units, z["zone_id"], sp, z["area_km2"], unc.get(sp, 0)))
    return results
