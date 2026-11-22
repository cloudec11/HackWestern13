"""Tests for ground/sightings.py: expected-sightings band and early-warning alerts."""
import math
import random
import sqlite3

import pytest

from ground import params, sightings
from ground.api import SQL_PATH

DAY = 86_400_000
END = 1795290000000


def _db(tmp_path, name="s.sqlite3"):
    c = sqlite3.connect(tmp_path / name)
    c.executescript(SQL_PATH.read_text(encoding="utf-8"))
    return c


def _poisson(rng, lam):
    l, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= l:
            return k
        k += 1


def _fill(c, rates_by_day, pod="pod-01", species="zebra", seed=0, hb_every_min=29):
    """rates_by_day: expected visits per day, oldest first, ending at END. Heartbeats throughout."""
    rng = random.Random(seed)
    days = len(rates_by_day)
    start = END - days * DAY
    hb = [(pod, i + 1, t, 1, 2, 1, 2, 50, 1, 0, 0, 0, t)
          for i, t in enumerate(range(start, END + 1, hb_every_min * 60_000))]
    hb.append((pod, len(hb) + 1, END, 1, 2, 1, 2, 50, 1, 0, 0, 0, END))
    c.executemany("INSERT INTO heartbeats VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", hb)
    seq = 0
    rows = []
    for d, lam in enumerate(rates_by_day):
        for _ in range(_poisson(rng, lam)):
            seq += 1
            t = start + d * DAY + int(rng.random() * (DAY - 60_000))
            rows.append((pod, seq, t, t + 5000, 9, species, 0.8, seq, 10, None, None, None, None, t))
    c.executemany("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    c.execute("INSERT INTO pods (pod_id, first_seen_ms, last_seen_ms) VALUES (?,?,?)", (pod, start, END))
    c.commit()


ZONE = {"zone_id": "zone-a", "area_km2": 5.0, "pods": ["pod-01"]}


def test_steady_data_rarely_alarms(tmp_path):
    alarms = 0
    for seed in range(40):
        c = _db(tmp_path, f"s{seed}.sqlite3")
        _fill(c, [10.0] * 35, seed=seed)
        r = sightings.zone_sightings(c, ZONE, 7, 28)[0]
        assert r["status"] in ("as_expected", "below", "above")
        alarms += r["status"] == "below"
    assert alarms <= 5, f"false 'below' alarms in {alarms}/40 steady datasets (target ~5%)"


def test_a_real_drop_is_flagged(tmp_path):
    caught = 0
    for seed in range(20):
        c = _db(tmp_path, f"d{seed}.sqlite3")
        _fill(c, [10.0] * 28 + [4.0] * 7, seed=seed)       # 60% fewer sightings in the last week
        r = sightings.zone_sightings(c, ZONE, 7, 28)[0]
        caught += r["status"] == "below"
        assert r["change_pct"] < 0
    assert caught >= 18, f"a 60% drop was flagged only {caught}/20 times"


def test_expected_range_matches_the_baseline_rate(tmp_path):
    c = _db(tmp_path)
    _fill(c, [10.0] * 35, seed=3)
    r = sightings.zone_sightings(c, ZONE, 7, 28)[0]
    assert r["window"]["camera_days"] == pytest.approx(7, abs=0.05)
    assert 55 < r["expected"]["p50"] < 85                      # ~10/day x 7 days
    assert r["expected"]["p5"] < r["expected"]["p50"] < r["expected"]["p95"]


def test_new_species_and_not_enough_data(tmp_path):
    c = _db(tmp_path)
    _fill(c, [10.0] * 35, seed=1)
    c.execute("INSERT INTO events VALUES ('pod-01', 99999, ?, ?, 8, 'bear', 0.9, 1, 10, NULL, NULL, NULL, NULL, ?)",
              (END - DAY, END - DAY + 5000, END))
    c.commit()
    by = {r["species"]: r for r in sightings.zone_sightings(c, ZONE, 7, 28)}
    assert by["bear"]["status"] == "new"
    c2 = _db(tmp_path, "short.sqlite3")
    _fill(c2, [10.0] * 2, seed=1)
    assert sightings.zone_sightings(c2, ZONE, 7, 28)[0]["status"] == "not_enough_data"


def test_forecast_in_sightings_inverts_the_density_model():
    sim = {"species": "zebra", "years": [0, 1], "p5": [100, 90], "p50": [200, 190], "p95": [300, 290]}
    f = sightings.forecast_in_sightings(sim, 5.0, 1)
    k = sightings.per_animal_daily_rate("zebra", 5.0)
    assert f["p50"][0] == pytest.approx(200 * k * 30, abs=0.1)
    assert sightings.forecast_in_sightings({**sim, "species": "bird"}, 5.0, 1) is None


def test_sightings_endpoint(client):
    from ground.fake_pod import FakePod, backfill

    def post(path, body):
        r = client.post(path, json=body)
        assert r.status_code == 200, r.text
        return r.json()
    backfill([FakePod("pod-01", {"zebra": 6.0, "elephant": 2.0}, {}, seed=5)], 21, post,
             now_ms=END, hb_every_min=29)
    r = client.get("/api/sightings", params={"zone_id": "zone-a", "window_days": 7, "baseline_days": 14}).json()
    by = {x["species"]: x for x in r["results"]}
    z = by["zebra"]
    assert z["status"] in ("as_expected", "below", "above") and z["expected"]["p50"] > 0
    assert 0 <= z["p_decline_10y"] <= 1
    assert z["forecast_monthly"]["years"][0] == 0 and len(z["forecast_monthly"]["p50"]) == params.FORECAST_YEARS + 1
    assert all(a["status"] in ("below", "above", "new") for a in r["alerts"])
    assert client.get("/api/sightings", params={"window_days": 0}).status_code == 422


# ---- trend: slow declines the window check can't see ----

def _decay(r0, weekly_pct, days=35):
    return [r0 * (1 + weekly_pct / 100) ** (d / 7) for d in range(days)]


def test_trend_rarely_claims_a_decline_on_steady_data(tmp_path):
    false = 0
    for seed in range(40):
        c = _db(tmp_path, f"t{seed}.sqlite3")
        _fill(c, [6.0] * 35, seed=200 + seed)
        t = sightings.zone_sightings(c, ZONE, 7, 28)[0]["trend"]
        false += t["status"] == "declining"
    assert false <= 6, f"steady data called 'declining' {false}/40 times (target ~5%)"


def test_trend_catches_the_decline_the_window_check_missed(tmp_path):
    caught = 0
    for seed in range(20):
        c = _db(tmp_path, f"e{seed}.sqlite3")
        _fill(c, _decay(2.0, -60), seed=300 + seed, species="elephant")   # the case from the live demo
        t = sightings.zone_sightings(c, ZONE, 7, 28)[0]["trend"]
        caught += t is not None and t["status"] == "declining" and t["weekly_change_pct"] < -30
    assert caught >= 18


def test_trend_estimate_is_close(tmp_path):
    c = _db(tmp_path)
    _fill(c, _decay(20.0, -20), seed=9)
    t = sightings.zone_sightings(c, ZONE, 7, 28)[0]["trend"]
    assert t["ci90_pct"][0] <= -20 <= t["ci90_pct"][1] and t["status"] == "declining"


def test_trend_alerts_reach_the_api(client):
    from ground.fake_pod import FakePod, backfill

    def post(path, body):
        r = client.post(path, json=body)
        assert r.status_code == 200, r.text
        return r.json()
    backfill([FakePod("pod-01", {"elephant": 4.0, "zebra": 6.0}, {"elephant": -60}, seed=8)], 28, post,
             now_ms=END, hb_every_min=29)
    r = client.get("/api/sightings", params={"zone_id": "zone-a", "window_days": 7, "baseline_days": 21}).json()
    trend = [a for a in r["alerts"] if a["kind"] == "trend" and a["species"] == "elephant"]
    assert trend and trend[0]["status"] == "declining"


def test_alert_limits_are_stricter_than_the_displayed_band(tmp_path):
    c = _db(tmp_path)
    _fill(c, [10.0] * 35, seed=4)
    r = sightings.zone_sightings(c, ZONE, 7, 28)[0]
    lo, hi = r["expected"]["alert_limits"]
    assert lo <= r["expected"]["p5"] and hi >= r["expected"]["p95"]
    assert sightings.ALERT_TAIL == 0.01
