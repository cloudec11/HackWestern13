"""Tests for ground/simulate.py (P2-5): the forecast behaves like a population should."""
from dataclasses import replace

import pytest

from ground import params
from ground.fake_pod import FakePod, backfill
from ground.simulate import PCTS, Inputs, forecast_result, run_forecast

BASE = Inputs(n0=200, n0_ci=None, r_g=(0.3, 0.3), sigma=0.0, loss_rate=0.0, k_factor=(1.0, 1.0))


def p50(inp, seed="s"):
    return run_forecast(inp, seed)["p50"]


def test_shape_and_order():
    fc = run_forecast(replace(BASE, sigma=0.15, n0_ci=(150, 260), r_g=(0.1, 0.3)), "x")
    assert fc["years"] == list(range(11)) and fc["n_runs"] == 1000
    for t in range(11):
        col = [fc[f"p{p}"][t] for p in PCTS]
        assert col == sorted(col), f"percentiles out of order in year {t}: {col}"
    assert 0 <= fc["p_below_threshold"] <= 1


def test_deterministic_for_same_seed():
    inp = replace(BASE, sigma=0.2, n0_ci=(150, 260))
    assert run_forecast(inp, "a") == run_forecast(inp, "a")
    assert run_forecast(inp, "a") != run_forecast(inp, "b")


def test_no_noise_grows_to_capacity():
    path = p50(replace(BASE, n0=60, k_factor=(2.0, 2.0)))          # K = 120
    assert path[0] == 60 and all(a <= b for a, b in zip(path, path[1:]))
    assert path[-1] == pytest.approx(120, abs=6)


def test_stays_at_capacity_without_losses():
    assert set(p50(BASE)) == {200}                                  # starts at K, nothing pushes it


def test_losses_bigger_than_growth_drive_decline_and_risk():
    safe = run_forecast(replace(BASE, n0=120, sigma=0.1), "s")
    doomed = run_forecast(replace(BASE, n0=120, sigma=0.1, r_g=(0.05, 0.05), loss_rate=0.15), "s")
    assert doomed["p50"][-1] < 60 < safe["p50"][-1]
    assert doomed["p_below_threshold"] > 0.9 and safe["p_below_threshold"] < 0.05


def test_more_starting_uncertainty_widens_the_fan():
    narrow = run_forecast(replace(BASE, n0_ci=(190, 210), sigma=0.05), "s")
    wide = run_forecast(replace(BASE, n0_ci=(100, 400), sigma=0.05), "s")
    assert (wide["p95"][0] - wide["p5"][0]) > 3 * (narrow["p95"][0] - narrow["p5"][0])
    assert narrow["p5"][0] >= 185 and narrow["p95"][0] <= 215        # year 0 matches the interval


def test_small_population_starting_below_threshold():
    dens = {"zone_id": "zone-c", "species": "elephant", "n0": 30, "n0_ci": [20, 45]}
    r = forecast_result(dens)
    assert r["starts_below_threshold"] is True and r["p_below_threshold"] > 0.9


def test_zone_threat_scales_losses():
    a = forecast_result({"zone_id": "zone-a", "species": "elephant", "n0": 100, "n0_ci": [80, 120]})
    c = forecast_result({"zone_id": "zone-c", "species": "elephant", "n0": 100, "n0_ci": [80, 120]})
    assert c["params"]["loss_rate"] == pytest.approx(a["params"]["loss_rate"] * 1.5)
    assert c["p_below_threshold"] >= a["p_below_threshold"] and c["p50"][-1] < a["p50"][-1]


def test_not_forecastable_cases():
    bird = forecast_result({"zone_id": "zone-a", "species": "bird", "n0": None, "not_applicable": True,
                            "note": "not valid for birds"})
    assert bird["not_applicable"] and bird["p50"] == [] and bird["note"] == "not valid for birds"
    thin = forecast_result({"zone_id": "zone-a", "species": "zebra", "n0": None, "insufficient_data": True})
    assert thin["insufficient_data"] and thin["p_below_threshold"] is None


def test_labels_are_honest():
    r = forecast_result({"zone_id": "zone-a", "species": "zebra", "n0": 250, "n0_ci": [220, 280]})
    assert r["illustrative"] is True and r["params"]["source"]["n0"] == "pod"
    assert all(v != "pod" for k, v in r["params"]["source"].items() if k != "n0")


# ---- through the API ----

NOW = 1795290000000


def _load(client, days=14):
    def post(path, body):
        r = client.post(path, json=body)
        assert r.status_code == 200, r.text
        return r.json()
    backfill([FakePod("pod-01", {"zebra": 6.0, "elephant": 2.0, "bird": 10.0}, {}, seed=3)],
             days, post, now_ms=NOW, hb_every_min=29)


def test_simulation_endpoint(client):
    _load(client)
    dens = {(d["zone_id"], d["species"]): d for d in client.get("/api/density").json()["results"]}
    res = client.get("/api/simulation", params={"zone_id": "zone-a"}).json()["results"]
    by = {r["species"]: r for r in res}
    z = by["zebra"]
    assert z["params"]["n0"] == dens[("zone-a", "zebra")]["n0"]          # forecast starts from density
    assert len(z["p50"]) == params.FORECAST_YEARS + 1 and z["threshold"] == params.QUASI_EXTINCTION
    assert by["bird"]["not_applicable"] is True
    assert by["elephant"]["p_below_threshold"] >= z["p_below_threshold"]   # slow breeder, fewer animals
    t = client.get("/api/simulation", params={"zone_id": "zone-a", "species": "zebra", "threshold": 400}).json()
    assert t["results"][0]["threshold"] == 400 and t["results"][0]["p_below_threshold"] >= z["p_below_threshold"]
    assert client.get("/api/simulation", params={"threshold": 0}).status_code == 422


def test_simulation_with_no_data(client):
    assert client.get("/api/simulation").json()["results"] == []
