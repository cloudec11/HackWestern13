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
ZONES = [
    {"zone_id": "zone-a", "name": "North ridge",   "area_km2": 5.0, "pods": ["pod-01"]},
    {"zone_id": "zone-b", "name": "River valley",  "area_km2": 8.0, "pods": ["pod-02"]},
    {"zone_id": "zone-c", "name": "South plains",  "area_km2": 3.0, "pods": ["pod-03"]},
]
UNASSIGNED_ZONE = {"zone_id": "unassigned", "name": "Unassigned Pods", "area_km2": 1.0}

# ---- Density method settings ----
MIN_VISITS = 5            # fewer visits than this -> "insufficient_data"
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
