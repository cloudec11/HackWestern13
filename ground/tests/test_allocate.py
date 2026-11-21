"""Tests for ground/allocate.py (P3-4): actions, greedy budget split, API caching."""
import pytest

from ground import params
from ground.allocate import ACTIONS, allocate, apply_actions, options, scenario_table
from ground.fake_pod import FakePod, backfill
from ground.simulate import Inputs

INP = Inputs(n0=100, n0_ci=(60, 160), r_g=(0.05, 0.05), sigma=0.1, loss_rate=0.1)


def dens(zone, species, n0, ci):
    return {"zone_id": zone, "species": species, "n0": n0, "n0_ci": ci}


# a zone in trouble (slow breeders, high threat) and one that's fine
DENS = [dens("zone-c", "elephant", 48, [34, 59]), dens("zone-c", "horse", 42, [26, 57]),
        dens("zone-b", "zebra", 360, [290, 430]), dens("zone-b", "giraffe", 310, [240, 380])]


@pytest.fixture(scope="module")
def table():
    return scenario_table(DENS)


def test_actions_change_the_right_input():
    assert apply_actions(INP, frozenset({"patrols"})).loss_rate == pytest.approx(0.05)
    hab = apply_actions(INP, frozenset({"habitat"}))
    assert hab.k_factor == pytest.approx((0.9 * 1.3, 1.6 * 1.3)) and hab.loss_rate == INP.loss_rate
    pods = apply_actions(INP, frozenset({"more_pods"}))
    lo, hi = pods.n0_ci
    assert 60 < lo < 100 < hi < 160                          # interval tightened around n0
    assert apply_actions(INP, frozenset()) == INP


def test_scenarios_cover_every_combination(table):
    assert set(table) == {"zone-c", "zone-b"}
    assert len(table["zone-c"]["risk"]) == 2 ** len(ACTIONS)


def test_patrols_and_habitat_reduce_risk_where_it_matters(table):
    z = table["zone-c"]
    base = sum(p for p, _ in z["risk"][frozenset()].values())
    for a in ("patrols", "habitat"):
        assert sum(p for p, _ in z["risk"][frozenset({a})].values()) < base


def test_greedy_spends_on_the_zone_in_trouble_first(table):
    out = allocate(table, 50_000)
    assert out["rows"] and out["rows"][0]["zone_id"] == "zone-c"
    assert out["spent"] + out["unallocated"] == 50_000 and out["spent"] <= 50_000
    assert out["species_at_risk_after"] < out["species_at_risk_before"]
    scores = [r["risk_reduction_per_1k"] for r in out["rows"]]
    assert all(r["risk_after"] < r["risk_before"] for r in out["rows"])
    assert scores[0] == max(scores)


def test_no_action_bought_twice_in_a_zone(table):
    out = allocate(table, 10_000_000)
    pairs = [(r["zone_id"], r["intervention"]) for r in out["rows"]]
    assert len(pairs) == len(set(pairs))


def test_budget_limits(table):
    assert allocate(table, 0)["rows"] == []
    small = allocate(table, 5_999)                       # cheaper than every action
    assert small["rows"] == [] and small["unallocated"] == 5_999
    more = allocate(table, 100_000)
    less = allocate(table, 20_000)
    assert more["species_at_risk_after"] <= less["species_at_risk_after"]


def test_more_pods_narrow_the_forecast(table):
    pods = [o for o in options(table) if o["intervention"] == "more_pods"]
    assert pods and all(o["band_width_change"] < 0 for o in pods)


def test_output_is_labelled(table):
    out = allocate(table, 50_000)
    assert out["illustrative"] is True and "illustrative" in out["note"]
    assert all(v["source"] == "illustrative" for v in out["actions"].values())


# ---- API ----

NOW = 1795290000000


def _load(client):
    def post(path, body):
        r = client.post(path, json=body)
        assert r.status_code == 200, r.text
        return r.json()
    for i, rates in ((1, {"elephant": 2.0, "zebra": 6.0}), (3, {"elephant": 1.5, "horse": 1.5})):
        backfill([FakePod(f"pod-0{i}", rates, {}, seed=i)], 10, post, now_ms=NOW, hb_every_min=29)


def test_allocate_endpoint_and_cache(client):
    _load(client)
    a = client.get("/api/allocate", params={"budget": 40000}).json()
    assert a["cached"] is False and a["budget"] == 40000 and a["threshold"] == params.QUASI_EXTINCTION
    assert a["rows"] and {"zone_id", "intervention", "cost", "risk_before", "risk_after"} <= set(a["rows"][0])
    b = client.get("/api/allocate", params={"budget": 80000}).json()
    assert b["cached"] is True and b["spent"] >= a["spent"]           # slider reuses the scenarios
    # one heartbeat must NOT force a 2 s rebuild (agent-1 review, finding 2)
    hb = client.post("/api/heartbeat", json={
        "pod_id": "pod-01", "seq": 99999, "t_wall_ms": NOW + 1, "infer_p50_ms": 1, "infer_p99_ms": 2,
        "e2e_p50_ms": 1, "e2e_p99_ms": 2, "temp_c": 50, "disk_free_mb": 1, "restarts": 0,
        "queue_depth": 0, "last_ack_seq": 0})
    assert hb.status_code == 200
    assert client.get("/api/allocate", params={"budget": 80000}).json()["cached"] is True
    # a new visit does (tests run with max_stale_s=0)
    ev = {"pod_id": "pod-01", "seq": 999, "t_start_ms": NOW - 5000, "t_end_ms": NOW - 1000, "class_id": 7,
          "species": "elephant", "max_score": 0.8, "track_id": 999, "n_frames": 10}
    assert client.post("/api/events", json={"pod_id": "pod-01", "events": [ev]}).status_code == 200
    assert client.get("/api/allocate", params={"budget": 80000}).json()["cached"] is False
    assert client.get("/api/allocate", params={"budget": -1}).status_code == 422


def test_cached_results_reused_briefly_while_data_streams_in(tmp_path):
    """With the production setting, a burst of new visits doesn't make every call rebuild."""
    from ground.api import create_app
    from ground.tests.conftest import Client
    c = Client(create_app(tmp_path / "x.sqlite3", max_stale_s=60, durable=False))
    _load(c)
    first = c.get("/api/density").json()
    assert first["cached"] is False
    ev = {"pod_id": "pod-01", "seq": 999, "t_start_ms": NOW - 5000, "t_end_ms": NOW - 1000, "class_id": 7,
          "species": "elephant", "max_score": 0.8, "track_id": 999, "n_frames": 10}
    c.post("/api/events", json={"pod_id": "pod-01", "events": [ev]})
    again = c.get("/api/density").json()
    assert again["cached"] is True and again["data_age_s"] < 60 and again["results"] == first["results"]


def test_allocate_with_no_data(client):
    out = client.get("/api/allocate").json()
    assert out["rows"] == [] and out["budget"] == params.DEFAULT_BUDGET and out["unallocated"] == out["budget"]


def test_never_buys_gains_below_the_noise_floor(table):
    out = allocate(table, 10_000_000)
    assert all(r["risk_before"] - r["risk_after"] >= params.MIN_GAIN - 1e-3 for r in out["rows"])
