"""Tests for ground/fake_pod.py (P1-4), run against the real API in-process."""
import pytest
from fastapi.testclient import TestClient

from ground.api import VALIDATORS, create_app
from ground.fake_pod import DEFAULT_RATES, FakePod, backfill, live

NOW = 1795290000000


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(tmp_path / "fake.sqlite3"))


def poster(client, sent=None):
    def post(path, body):
        if sent is not None:
            sent.append((path, body))
        r = client.post(path, json=body)
        assert r.status_code == 200, r.text   # generator output must always be valid
        return r.json()
    return post


def pod(n=1, **kw):
    kw.setdefault("rates", dict(DEFAULT_RATES))
    kw.setdefault("trend", {})
    return FakePod(f"pod-{n:02d}", seed=n, **kw)


def summary(client):
    return client.get("/api/summary", params={"recent": 0}).json()


def test_generated_payloads_match_schema():
    p = pod(mobile=True)
    events = [p.make_visit(p.pick_species(), NOW + i * 1000) for i in range(200)]
    for i in range(0, 200, 50):
        assert VALIDATORS["event_batch"].is_valid({"pod_id": p.pod_id, "events": events[i:i + 50]})
    sent = []
    p.heartbeat(lambda path, body: sent.append(body) or {"pod_id": p.pod_id, "acked_seq": 0}, NOW)
    assert VALIDATORS["heartbeat"].is_valid(sent[0])


def test_backfill_stores_everything(client):
    pods = [pod(1), pod(2)]
    n = backfill(pods, 3, poster(client), now_ms=NOW)
    s = summary(client)
    assert n > 0 and s["total_events"] == n
    assert all(p.queue == [] for p in pods)
    assert {x["pod_id"]: x["acked_seq"] for x in s["pods"]} == {p.pod_id: p.seq for p in pods}


def test_backfill_seq_follows_time(client):
    backfill([pod()], 2, poster(client), now_ms=NOW)
    evs = sorted(client.get("/api/events", params={"limit": 10000}).json(), key=lambda e: e["seq"])
    assert [e["seq"] for e in evs] == list(range(1, len(evs) + 1))
    assert all(a["t_start_ms"] <= b["t_start_ms"] for a, b in zip(evs, evs[1:]))


def test_flaky_network_loses_nothing(client):
    p = pod(drop=0.3, loss=0.3, dup=0.3)
    n = backfill([p], 5, poster(client), now_ms=NOW)
    s = summary(client)
    assert s["total_events"] == n and s["pods"][0]["acked_seq"] == n and p.queue == []


def test_second_run_continues_numbering(client):
    n1 = backfill([pod()], 1, poster(client), now_ms=NOW)
    n2 = backfill([pod()], 1, poster(client), now_ms=NOW + 86_400_000)   # fresh process, same id
    assert summary(client)["total_events"] == n1 + n2


def test_trend_reduces_visits():
    flat, falling = pod(rates={"elephant": 50.0}), pod(rates={"elephant": 50.0}, trend={"elephant": -50})
    sink = lambda path, body: {"pod_id": body["pod_id"], "acked_seq": body["events"][-1]["seq"]} \
        if path == "/api/events" else {"pod_id": body["pod_id"], "acked_seq": 0}
    a = backfill([flat], 28, sink, now_ms=NOW)
    b = backfill([falling], 28, sink, now_ms=NOW)
    assert b < a * 0.6


def test_live_mode(client):
    t = [NOW / 1000]
    def clock():
        return t[0]
    def sleep(s):
        t[0] += s
    sent = []
    n = live([pod()], live_rate=60, duration=30, post=poster(client, sent), sleep=sleep, clock=clock)
    s = summary(client)
    assert n > 0 and s["total_events"] == n
    assert any(path == "/api/heartbeat" for path, _ in sent)
    evs = client.get("/api/events", params={"limit": 1000}).json()
    assert all(e["t_end_ms"] <= NOW + 31_000 for e in evs)      # visits are emitted after they end


def test_rebooted_pod_heartbeats_still_recorded(client):
    post = poster(client)
    pod().heartbeat(post, NOW)
    pod().heartbeat(post, NOW + 10_000)   # new process, counter back at 1
    assert summary(client)["pods"][0]["last_heartbeat"]["t_wall_ms"] == NOW + 10_000
