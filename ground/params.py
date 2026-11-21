"""Model parameters for the ground station science (agent-3).

EVERYTHING HERE IS ILLUSTRATIVE. These are round, plausible-order numbers so
the demo pipeline is complete, not values to make real decisions with. A real
deployment would take each one from ecologists and from measuring the camera.
The API reports `source` for each parameter so the dashboard can say so.
"""
from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LABELS_CSV = ROOT / "models" / "labels.csv"

# ---- Camera (Random Encounter Model detection zone) ----
# r: how far away the camera reliably detects an animal. theta: horizontal
# field of view. Logitech C270 is roughly 60 degrees; r is a guess for a small
# webcam and should be measured (walk a target away until detection stops).
CAMERA = {
    "r_km": 0.010,        # 10 m
    "theta_rad": 1.05,    # ~60 degrees
    "source": "illustrative",
}

# ---- Species ----
# v_km_per_day: average distance travelled per day ("day range"), which the
# Random Encounter Model needs. rem_ok: False where the model doesn't fit
# (birds fly, so the 2-D movement assumption fails), and those get no density.
SPECIES = {
    "zebra":    {"v_km_per_day": 12.0, "rem_ok": True},
    "elephant": {"v_km_per_day": 12.0, "rem_ok": True},
    "giraffe":  {"v_km_per_day": 7.0,  "rem_ok": True},
    "horse":    {"v_km_per_day": 10.0, "rem_ok": True},
    "bear":     {"v_km_per_day": 8.0,  "rem_ok": True},
    "cow":      {"v_km_per_day": 4.0,  "rem_ok": True},
    "sheep":    {"v_km_per_day": 4.0,  "rem_ok": True},
    "dog":      {"v_km_per_day": 6.0,  "rem_ok": True},
    "cat":      {"v_km_per_day": 2.0,  "rem_ok": True},
    "bird":     {"v_km_per_day": None, "rem_ok": False,
                 "note": "Random Encounter Model assumes animals move on the ground; not valid for birds"},
}
SPECIES_SOURCE = "illustrative"

# ---- Zones ----
# Each Pod belongs to one zone. Area is what turns density (per km^2) into a
# head count for the simulation. Pods not listed here go to UNASSIGNED_ZONE.
# biome is what agent-4's habitat view draws (forest, savanna, desert, snow): a per-zone
# setting until something on the Pod can classify habitat.
ZONES = [
    {"zone_id": "zone-a", "name": "North ridge",   "area_km2": 5.0, "pods": ["pod-01"], "biome": "savanna"},
    {"zone_id": "zone-b", "name": "River valley",  "area_km2": 8.0, "pods": ["pod-02"], "biome": "forest"},
    {"zone_id": "zone-c", "name": "South plains",  "area_km2": 3.0, "pods": ["pod-03"], "biome": "desert"},
]
UNASSIGNED_ZONE = {"zone_id": "unassigned", "name": "Unassigned Pods", "area_km2": 1.0, "biome": "savanna"}

# ---- Population forecast (Ricker model, ground/simulate.py) ----
# Per species, ranges that each Monte Carlo run draws from (uniformly):
#   r_g        intrinsic growth rate per year (how fast it grows with room to spare)
#   sigma      year-to-year environmental noise on the log scale (good and bad years)
#   loss_rate  fraction of the population lost to threats each year (poaching, roads)
# Long-lived, slow breeders (elephant, bear) get low r_g. ALL ILLUSTRATIVE.
GROWTH = {
    "elephant": {"r_g": (0.04, 0.07), "sigma": 0.08, "loss_rate": 0.05},
    "bear":     {"r_g": (0.03, 0.08), "sigma": 0.10, "loss_rate": 0.04},
    "giraffe":  {"r_g": (0.08, 0.15), "sigma": 0.12, "loss_rate": 0.04},
    "zebra":    {"r_g": (0.10, 0.20), "sigma": 0.15, "loss_rate": 0.04},
    "horse":    {"r_g": (0.10, 0.20), "sigma": 0.15, "loss_rate": 0.03},
    "cow":      {"r_g": (0.15, 0.30), "sigma": 0.15, "loss_rate": 0.03},
    "sheep":    {"r_g": (0.15, 0.30), "sigma": 0.15, "loss_rate": 0.03},
    "dog":      {"r_g": (0.20, 0.40), "sigma": 0.20, "loss_rate": 0.05},
    "cat":      {"r_g": (0.20, 0.40), "sigma": 0.20, "loss_rate": 0.05},
}
# Carrying capacity K: with no habitat data, assume each zone is near capacity
# today, so K = (today's estimate) x a factor drawn from this range. Stated
# openly in the results; habitat restoration (P3-4) raises it.
K_FACTOR = (0.9, 1.6)
# Threat level per zone, multiplying every species' loss_rate (patrols lower it).
ZONE_THREAT = {"zone-a": 1.0, "zone-b": 0.6, "zone-c": 1.5, "unassigned": 1.0}
# Every species' sigma is multiplied by this. Calibrated, not guessed: on real
# Isle Royale moose counts (ground/backtest.py --calibrate), tuning on 1980-2004
# picked sigma 0.30 against the 0.15 we'd assumed. Tested on 2005-2026 (never
# used for tuning), the 90% band then held the real count 94% of the time
# instead of 58%. Applied as a x2 scale so species keep their relative
# variability (elephants stay calmer than moose).
NOISE_SCALE = 2.0
FORECAST_YEARS = 10
FORECAST_RUNS = 1000
QUASI_EXTINCTION = 50     # "at risk" if the population falls below this within the forecast
GROWTH_SOURCE = "illustrative"

# ---- Budget allocation (ground/allocate.py) ----
# Each action is bought once per zone and helps every species in that zone.
# Costs and effects are ILLUSTRATIVE placeholders until conservation partners
# supply real ones. Effects act on the forecast's inputs:
#   loss_mult  multiplies the zone's yearly loss rate (patrols cut poaching)
#   k_mult     multiplies carrying capacity (restored habitat supports more animals)
#   ci_shrink  shrinks the starting-population interval on a log scale. Two more
#              Pods = 3 cameras instead of 1, and error falls ~1/sqrt(3), about 0.58
INTERVENTIONS = {
    "patrols":   {"label": "Anti-poaching patrols", "cost": 12_000, "loss_mult": 0.5},
    "habitat":   {"label": "Habitat restoration",   "cost": 20_000, "k_mult": 1.3},
    "more_pods": {"label": "Two more Pods",          "cost": 6_000,  "ci_shrink": 0.58},
}
INTERVENTION_SOURCE = "illustrative"
# Don't buy an action whose risk cut is smaller than this (expected species). With
# 1000 runs, a single species' p_below has a Monte Carlo error of roughly 0.01-0.016,
# so smaller "gains" are mostly noise. Example: more Pods barely move 10-year
# risk, because year-to-year swings dominate the uncertainty.
MIN_GAIN = 0.01
DEFAULT_BUDGET = 50_000

# ---- Density method settings ----
MIN_VISITS = 5            # fewer visits than this -> "insufficient_data"
MIN_CAMERA_DAYS = 0.25    # need >= 6 h of heartbeat-proven running time before estimating
OUTAGE_GAP_MIN = 30       # a gap with no heartbeat or event longer than this = camera off
BOOTSTRAP_RESAMPLES = 1000
CI_LEVEL = 0.90


def demo_only_species(path: Path = LABELS_CSV) -> set[str]:
    """Species flagged demo_only in agent-2's label table (e.g. teddy_bear), plus 'unknown'.
    These never enter density or simulation."""
    out = {"unknown"}
    try:
        lines = [l for l in path.read_text(encoding="utf-8").splitlines()
                 if l.strip() and not l.startswith("#")]
        for row in csv.DictReader(lines):
            if row.get("demo_only", "0").strip() == "1":
                out.add(row["species"])
    except OSError:
        out.add("teddy_bear")
    return out


def zone_of(pod_id: str) -> str:
    for z in ZONES:
        if pod_id in z["pods"]:
            return z["zone_id"]
    return UNASSIGNED_ZONE["zone_id"]


def zone_info(zone_id: str) -> dict:
    for z in ZONES:
        if z["zone_id"] == zone_id:
            return z
    return {**UNASSIGNED_ZONE, "pods": []}
