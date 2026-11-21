"""Tests for ground/api.py (P1-3). Run from repo root:
    ground/.venv/Scripts/python -m pytest ground/tests -q
"""
import pytest

T0 = 1795283000000


def ev(seq, pod="pod-01", **kw):
    e = {"pod_id": pod, "seq": seq, "t_start_ms": T0 + seq * 10000, "t_end_ms": T0 + seq * 10000 + 5000,
         "class_id": 19, "species": "sheep", "max_score": 0.8, "track_id": seq, "n_frames": 30}
    e.update(kw)
    return e


def batch(*events, pod="pod-01"):
    return {"pod_id": pod, "events": list(events)}


def hb(seq=1, pod="pod-01", **kw):
    h = {"pod_id": pod, "seq": seq, "t_wall_ms": T0 + seq * 10000, "infer_p50_ms": 90.0,
         "infer_p99_ms": 140.0, "e2e_p50_ms": 100.0, "e2e_p99_ms": 150.0, "temp_c": 60.0,
         "disk_free_mb": 20000, "restarts": 0, "queue_depth": 0, "last_ack_seq": 0}
    h.update(kw)
    return h


def post(client, body):
    return client.post("/api/events", json=body)


def test_health(client):
    assert client.get("/health").json()["ok"] is True


def test_batch_stored_and_acked(client):
    r = post(client, batch(ev(1), ev(2), ev(3)))
    assert r.status_code == 200
    assert r.json() == {"pod_id": "pod-01", "acked_seq": 3, "stored": 3, "duplicates": 0}


def test_resend_is_idempotent(client):
    post(client, batch(ev(1), ev(2)))
    r = post(client, batch(ev(1), ev(2), ev(3)))
    assert r.json() == {"pod_id": "pod-01", "acked_seq": 3, "stored": 1, "duplicates": 2}
    assert client.get("/api/summary").json()["total_events"] == 3


def test_ack_is_highest_contiguous(client):
    # seq 3 lost in transit: ack must stop at 2 so the Pod resends 3.
    assert post(client, batch(ev(1), ev(2), ev(4), ev(5))).json()["acked_seq"] == 2
    # gap filled later: ack jumps over everything already held.
    assert post(client, batch(ev(3))).json()["acked_seq"] == 5


def test_ack_zero_when_first_event_missing(client):
    assert post(client, batch(ev(2))).json()["acked_seq"] == 0


def test_pods_are_independent(client):
    post(client, batch(ev(1), ev(2)))
    r = post(client, batch(ev(1, pod="pod-02"), pod="pod-02"))
    assert r.json()["acked_seq"] == 1 and r.json()["stored"] == 1


@pytest.mark.parametrize("bad", [
    ev(1, species="person"),
    ev(1, colour="red"),
    ev(1, max_score=1.5),
    ev(0),
    ev(1, lat=43.0, lon=-81.0),            # location without loc_source
])
def test_invalid_event_rejects_whole_batch(client, bad):
    r = post(client, batch(ev(2), bad))
    assert r.status_code == 422
    assert client.get("/api/summary").json()["total_events"] == 0


def test_pod_id_mismatch_rejected(client):
    r = post(client, batch(ev(1, pod="pod-02")))
    assert r.status_code == 422
    assert "does not match" in r.json()["detail"][0]["error"]


def test_end_before_start_rejected(client):
    r = post(client, batch(ev(1, t_end_ms=T0)))
    assert r.status_code == 422


def test_batch_over_50_rejected(client):
    assert post(client, batch(*[ev(i) for i in range(1, 52)])).status_code == 422


def test_optional_location_roundtrip(client):
    post(client, batch(ev(1, lat=43.0, lon=-81.0, loc_source="simulated", thumb_path="a/1.jpg")))
    e = client.get("/api/events").json()[0]
    assert (e["lat"], e["lon"], e["loc_source"], e["thumb_path"]) == (43.0, -81.0, "simulated", "a/1.jpg")
    # absent optional fields are omitted, not null
    post(client, batch(ev(2)))
    e2 = client.get("/api/events", params={"limit": 1}).json()[0]
    assert e2["seq"] == 2 and "lat" not in e2 and "thumb_path" not in e2


def test_heartbeat_returns_ack_and_is_idempotent(client):
    post(client, batch(ev(1), ev(2)))
    r = client.post("/api/heartbeat", json=hb(1))
    assert r.json() == {"pod_id": "pod-01", "acked_seq": 2}
    client.post("/api/heartbeat", json=hb(1))  # duplicate is harmless
    assert client.post("/api/heartbeat", json=hb(2, temp_c=70.0)).status_code == 200


def test_heartbeat_invalid_rejected(client):
    h = hb(1)
    del h["temp_c"]
    assert client.post("/api/heartbeat", json=h).status_code == 422


def test_heartbeat_from_new_pod_acks_zero(client):
    assert client.post("/api/heartbeat", json=hb(1, pod="pod-09")).json()["acked_seq"] == 0


def test_summary_shape(client):
    post(client, batch(ev(1), ev(2, species="bird", class_id=15, max_score=0.9), ev(3)))
    client.post("/api/heartbeat", json=hb(1))
    client.post("/api/heartbeat", json=hb(2, temp_c=65.0))
    s = client.get("/api/summary", params={"recent": 2}).json()
    assert s["total_events"] == 3
    assert [(x["species"], x["visits"]) for x in s["species"]] == [("sheep", 2), ("bird", 1)]
    pod = s["pods"][0]
    assert pod["pod_id"] == "pod-01" and pod["acked_seq"] == 3 and pod["n_events"] == 3
    assert pod["last_heartbeat"]["temp_c"] == 65.0          # newest heartbeat
    assert [e["seq"] for e in s["recent_events"]] == [3, 2]  # newest first


def test_events_filters(client):
    post(client, batch(ev(1), ev(2, species="bird", class_id=15), ev(3)))
    assert [e["seq"] for e in client.get("/api/events", params={"species": "sheep"}).json()] == [3, 1]
    since = T0 + 20000
    assert [e["seq"] for e in client.get("/api/events", params={"since_ms": since}).json()] == [3, 2]


def test_non_json_object_rejected(client):
    assert client.post("/api/events", json=[1, 2]).status_code == 422
    assert client.post("/api/events", content=b"not json",
                       headers={"content-type": "application/json"}).status_code == 422


def test_unknown_path_and_wrong_method(client):
    r = client.get("/api/nope")
    assert r.status_code == 404 and r.json()["detail"][0]["error"] == "no such endpoint"
    assert client.get("/api/events/").status_code == 200          # trailing slash is fine
    assert client.request("DELETE", "/api/events").status_code == 405
    assert client.get("/api/heartbeat").status_code == 405


def test_cors_headers_and_preflight(client):
    assert client.get("/health").headers["Access-Control-Allow-Origin"] == "*"
    r = client.request("OPTIONS", "/api/summary")
    assert r.status_code == 204 and "GET" in r.headers["Access-Control-Allow-Methods"]


def test_body_too_large(client):
    r = client.post("/api/events", content=b"x" * 1_000_001)
    assert r.status_code == 413


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 10001}, {"limit": "abc"}, {"since_ms": "x"}])
def test_bad_query_params(client, params):
    r = client.get("/api/events", params=params)
    assert r.status_code == 422 and r.json()["detail"][0]["path"] in params


def test_summary_recent_bounds(client):
    assert client.get("/api/summary", params={"recent": 501}).status_code == 422
    assert client.get("/api/summary", params={"recent": 0}).json()["recent_events"] == []


def test_errors_are_json_with_path(client):
    r = post(client, batch(ev(1, species="person")))
    body = r.json()
    assert r.headers["Content-Type"] == "application/json"
    assert body["detail"][0]["path"] == "events/0/species"
