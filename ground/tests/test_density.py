"""Tests for ground/density.py (P2-4): Random Encounter Model, camera effort, API."""
import math
from collections import Counter

import pytest

from ground import params
from ground.density import DAY_MS, Unit, _bootstrap_ci, effort_units, estimate, rem_density
from ground.fake_pod import FakePod, backfill

NOW = 1795290000000 - (1795290000000 % DAY_MS)    # midnight UTC, so day maths is exact
CAM = params.CAMERA


def truth(species, rate_per_day):
    """Density the fake data was generated to have."""
    return rem_density(rate_per_day, params.SPECIES[species]["v_km_per_day"], CAM["r_km"], CAM["theta_rad"])


# ---- the formula ----

def test_rem_formula_matches_hand_calculation():
    # 0.6 visits/day, v=1.5 km/day, r=0.01 km, theta=1.05 rad
    assert rem_density(0.6, 1.5, 0.01, 1.05) == pytest.approx(0.6 * math.pi / (1.5 * 0.01 * 3.05))
    assert rem_density(0.6, 1.5, 0.01, 1.05) == pytest.approx(41.2, abs=0.05)


def test_rem_scales_sensibly():
    base = rem_density(1, 10, 0.01, 1)
    assert rem_density(2, 10, 0.01, 1) == pytest.approx(2 * base)     # twice the visits -> twice the animals
    assert rem_density(1, 20, 0.01, 1) == pytest.approx(base / 2)     # faster animals -> fewer needed
    with pytest.raises(ValueError):
        rem_density(1, 0, 0.01, 1)


# ---- camera effort ----

def test_effort_counts_running_time_and_splits_at_midnight():
    hb = list(range(NOW, NOW + 2 * DAY_MS + 1, 10 * 60_000))          # every 10 min for 2 days
    units = effort_units({"pod-01": hb}, [])
    assert [u.day for u in units] == [NOW // DAY_MS, NOW // DAY_MS + 1]
    assert sum(u.effort_days for u in units) == pytest.approx(2.0)


def test_effort_skips_outages():
    hb = [t for t in range(NOW, NOW + DAY_MS + 1, 10 * 60_000)
          if not (NOW + 6 * 3_600_000 <= t < NOW + 12 * 3_600_000)]    # 6 h with no heartbeats
    effort = sum(u.effort_days for u in effort_units({"pod-01": hb}, []))
    # the 6 h hole plus the 10 min either side that no longer has a partner
    assert effort == pytest.approx(1 - 6 / 24 - 10 / 1440, abs=1e-9)


def test_visits_attach_to_their_day():
    hb = list(range(NOW, NOW + 2 * DAY_MS + 1, 600_000))
    units = effort_units({"p": hb}, [("p", NOW + 5, "zebra"), ("p", NOW + DAY_MS + 5, "zebra"),
                                      ("p", NOW + DAY_MS + 6, "zebra")])
    assert [u.counts["zebra"] for u in units] == [1, 2]


# ---- estimates ----

def test_insufficient_and_not_applicable():
    units = [Unit("p", 0, 1.0), Unit("p", 1, 1.0)]
    units[0].counts["zebra"] = 3
    r = estimate(units, "zone-a", "zebra", 5.0)
    assert r["insufficient_data"] and r["density_per_km2"] is None and r["n_visits"] == 3
    units[0].counts["bird"] = 50
    b = estimate(units, "zone-a", "bird", 5.0)
    assert b["not_applicable"] and b["density_per_km2"] is None
    assert estimate(units, "zone-a", "unicorn", 5.0)["not_applicable"]


def _fake_units(seed, days=30, rates=None, trend=None):
    rates = rates or {"zebra": 6.0, "elephant": 2.0}
    pod = FakePod("pod-01", rates, trend or {}, seed=seed)
    evs, hbs = [], []

    def sink(path, body):
        if path == "/api/events":
            evs.extend(body["events"])
            return {"pod_id": "pod-01", "acked_seq": body["events"][-1]["seq"]}
        hbs.append(body["t_wall_ms"])
        return {"pod_id": "pod-01", "acked_seq": 0}

    backfill([pod], days, sink, now_ms=NOW, hb_every_min=20)
    stamps = hbs + [t for e in evs for t in (e["t_start_ms"], e["t_end_ms"])]
    return effort_units({"pod-01": stamps}, [("pod-01", e["t_start_ms"], e["species"]) for e in evs])


def test_recovers_known_density_from_fake_data():
    units = _fake_units(seed=11)
    for sp, rate in (("zebra", 6.0), ("elephant", 2.0)):
        r = estimate(units, "zone-a", sp, 5.0)
        assert r["camera_days"] == pytest.approx(30, abs=0.05)
        assert r["density_per_km2"] == pytest.approx(truth(sp, rate), rel=0.25)
        assert r["ci_low"] <= truth(sp, rate) <= r["ci_high"]
        assert r["n0"] == round(r["density_per_km2"] * 5.0, 0) or abs(r["n0"] - r["density_per_km2"] * 5) <= 1


def test_interval_coverage_is_honest():
    """Across independent fake datasets, the 90% interval should contain the truth about 90% of the time."""
    hits = n = 0
    for seed in range(20):
        units = _fake_units(seed=100 + seed, days=21, rates={"zebra": 4.0})
        ci = _bootstrap_ci(units, "zebra", "z", 400, 0.90)
        y = sum(u.counts["zebra"] for u in units)
        t = sum(u.effort_days for u in units)
        n += 1
        hits += ci[0] <= 4.0 <= ci[1]
        assert y / t == pytest.approx(4.0, rel=0.35)
    assert hits >= 15, f"90% interval covered the truth only {hits}/{n} times"


def test_more_data_gives_narrower_interval():
    short = estimate(_fake_units(seed=5, days=7), "z", "zebra", 5.0)
    long = estimate(_fake_units(seed=5, days=42), "z", "zebra", 5.0)
    assert (long["ci_high"] - long["ci_low"]) < (short["ci_high"] - short["ci_low"])


# ---- through the API ----

def _post_backfill(client, pod_id, days, rates, **kw):
    def post(path, body):
        r = client.post(path, json=body)
        assert r.status_code == 200, r.text
        return r.json()
    return backfill([FakePod(pod_id, rates, {}, seed=3)], days, post, now_ms=NOW, hb_every_min=20, **kw)


def test_density_endpoint(client):
    _post_backfill(client, "pod-01", 14, {"zebra": 6.0, "bird": 10.0, "teddy_bear": 5.0})
    res = client.get("/api/density").json()["results"]
    by = {(r["zone_id"], r["species"]): r for r in res}
    z = by[("zone-a", "zebra")]
    assert z["illustrative"] is True and z["params"]["source"] == "illustrative"
    assert z["camera_days"] == pytest.approx(14, abs=0.05) and z["area_km2"] == 5.0
    assert z["density_per_km2"] == pytest.approx(truth("zebra", 6.0), rel=0.3)
    assert len(z["n0_ci"]) == 2 and z["n0_ci"][0] <= z["n0"] <= z["n0_ci"][1]
    assert by[("zone-a", "bird")]["not_applicable"] is True
    assert ("zone-a", "teddy_bear") not in by                       # demo-only class never estimated
    assert client.get("/api/density", params={"zone_id": "zone-a", "species": "zebra"}).json()["results"] == [z]
    # a species filter reports every zone, so empty zones show "not enough data" rather than vanish
    others = [r for r in client.get("/api/density", params={"species": "zebra"}).json()["results"]
              if r["zone_id"] != "zone-a"]
    assert others and all(r["insufficient_data"] and r["camera_days"] == 0 for r in others)
    assert client.get("/api/density", params={"zone_id": "zone-b"}).json()["results"] == []


def test_outage_reduces_camera_days_not_density(client):
    _post_backfill(client, "pod-01", 20, {"zebra": 6.0}, outages=[(5, 10)])
    z = client.get("/api/density", params={"species": "zebra"}).json()["results"][0]
    assert z["camera_days"] == pytest.approx(15, abs=0.1)
    assert z["density_per_km2"] == pytest.approx(truth("zebra", 6.0), rel=0.3)


def test_zones_endpoint_and_unassigned_pods(client):
    _post_backfill(client, "pod-07", 3, {"zebra": 6.0})
    zones = {z["zone_id"]: z for z in client.get("/api/zones").json()["zones"]}
    assert zones["zone-a"]["pods"] == ["pod-01"] and zones["zone-a"]["area_km2"] == 5.0
    assert zones["unassigned"]["pods"] == ["pod-07"]
    res = client.get("/api/density", params={"zone_id": "unassigned"}).json()["results"]
    assert res and res[0]["species"] == "zebra"


def test_density_with_no_data(client):
    assert client.get("/api/density").json()["results"] == []
    r = client.get("/api/density", params={"species": "zebra"}).json()["results"]
    assert all(x["insufficient_data"] for x in r) and {x["zone_id"] for x in r} == {"zone-a", "zone-b", "zone-c"}


# ---- agent-1 review finding 1: no heartbeats must not mean near-zero camera time ----

def _cat_visits(pod="pod-01"):
    return [{"pod_id": pod, "seq": i + 1, "t_start_ms": NOW + i * 80 * 60_000, "t_end_ms": NOW + i * 80 * 60_000 + 5000,
             "class_id": 2, "species": "cat", "max_score": 0.7, "track_id": i + 1, "n_frames": 20} for i in range(6)]


def _hb(seq, t):
    return {"pod_id": "pod-01", "seq": seq, "t_wall_ms": t, "infer_p50_ms": 68, "infer_p99_ms": 72, "e2e_p50_ms": 90,
            "e2e_p99_ms": 130, "temp_c": 55, "disk_free_mb": 9000, "restarts": 0, "queue_depth": 0, "last_ack_seq": 0}


def test_no_heartbeats_gives_insufficient_data_not_millions(client):
    assert client.post("/api/events", json={"pod_id": "pod-01", "events": _cat_visits()}).status_code == 200
    r = client.get("/api/density", params={"zone_id": "zone-a", "species": "cat"}).json()["results"][0]
    assert r["insufficient_data"] and r["density_per_km2"] is None and r["n0"] is None
    assert r["uncovered_visits"] == 6 and "heartbeat" in r["note"]
    assert client.get("/api/simulation", params={"zone_id": "zone-a"}).json()["results"][0]["p50"] == []


def test_same_visits_with_heartbeats_give_a_sane_estimate(client):
    client.post("/api/events", json={"pod_id": "pod-01", "events": _cat_visits()})
    for i, t in enumerate(range(NOW - 60_000, NOW + 8 * 3_600_000 + 1, 10_000)):   # every 10 s for 8 h
        if i % 6 == 0:                                                              # (every 60 s keeps the test quick)
            client.post("/api/heartbeat", json=_hb(i + 1, t))
    r = client.get("/api/density", params={"zone_id": "zone-a", "species": "cat"}).json()["results"][0]
    assert r["uncovered_visits"] == 0 and r["camera_days"] == pytest.approx(8 / 24 + 1 / 1440, abs=0.01)
    # agent-1's review measured about 4,600 cats with heartbeats; 18 visits/day, v=2 km/day -> ~927/km2 x 5 km2
    assert 3000 < r["n0"] < 6000


def test_visits_outside_heartbeat_cover_are_not_counted():
    hb = list(range(NOW, NOW + 3_600_000 + 1, 60_000))                              # first hour only
    unc = Counter()
    units = effort_units({"p": hb}, [("p", NOW + 10, "cat"), ("p", NOW + 5 * 3_600_000, "cat")], uncovered=unc)
    assert sum(u.counts["cat"] for u in units) == 1 and unc[("p", "cat")] == 1
