"""Fake Pod: sends realistic visit events and heartbeats to the ground API (agent-3, P1-4).

Behaves like the real pod-uplink: it keeps a queue of unsent events, sends
batches of up to 50, and only forgets an event once the server's acked_seq
covers it. So it also exercises the server's duplicate and gap handling.
Uses only the standard library, so it runs anywhere Python 3.10+ does.

Examples (from the repo root, server already running on :8000):
    # live demo feed: about 6 visits a minute from one Pod, forever
    python -m ground.fake_pod
    # three Pods, faster, flaky network, stop after 2 minutes
    python -m ground.fake_pod --pods 3 --live-rate 20 --drop 0.2 --loss 0.1 --duration 120
    # 30 days of history in a few seconds (for density / simulation work),
    # with elephants declining 5% a week
    python -m ground.fake_pod --backfill-days 30 --trend elephant=-5
    # print the JSON instead of sending it
    python -m ground.fake_pod --dry-run --duration 5

Class ids come from agent-2's label table, models/labels.csv.
All numbers are made up: never show them as real measurements.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional

LABELS_CSV = Path(__file__).resolve().parent.parent / "models" / "labels.csv"


def load_species_ids(path: Path = LABELS_CSV) -> dict[str, int]:
    """species -> class_id from agent-2's label table (models/labels.csv).
    Falls back to the same ids if the file is missing (it matched them when written)."""
    fallback = {"bird": 1, "cat": 2, "dog": 3, "horse": 4, "sheep": 5, "cow": 6,
                "elephant": 7, "bear": 8, "zebra": 9, "giraffe": 10, "teddy_bear": 11}
    try:
        lines = [l for l in path.read_text(encoding="utf-8").splitlines()
                 if l.strip() and not l.startswith("#")]
    except OSError:
        return fallback
    ids = {row["species"]: int(row["class_id"]) for row in csv.DictReader(lines)}
    ids.pop("unknown", None)
    return ids or fallback


SPECIES_IDS = load_species_ids()

# Default "habitat": expected visits per camera-day for each species (illustrative).
DEFAULT_RATES = {"zebra": 6.0, "giraffe": 3.0, "elephant": 2.0, "bird": 10.0, "horse": 1.5}

# Typical dwell time in front of the camera, seconds (median of a log-normal).
DWELL_S = {"bird": 4, "elephant": 25, "giraffe": 18, "zebra": 12}

BATCH_MAX = 50
DAY_MS = 86_400_000
START_LAT, START_LON = 43.0096, -81.2737   # arbitrary start point for --mobile

PostFn = Callable[[str, dict], Optional[dict]]  # returns parsed JSON, or None if unreachable


def http_post(base_url: str, timeout: float = 3.0) -> PostFn:
    def post(path: str, body: dict) -> Optional[dict]:
        req = urllib.request.Request(base_url.rstrip("/") + path, data=json.dumps(body).encode(),
                                     headers={"content-type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:   # server reached but rejected: a bug, show it
            print(f"[{path}] HTTP {e.code}: {e.read().decode(errors='replace')[:500]}", file=sys.stderr)
            return None
        except (urllib.error.URLError, OSError) as e:   # network down: keep events queued
            print(f"[{path}] unreachable ({e}); keeping events queued", file=sys.stderr)
            return None
    return post


def dry_run_post(path: str, body: dict) -> Optional[dict]:
    print(json.dumps({"POST": path, "body": body}))
    if path == "/api/events":
        return {"pod_id": body["pod_id"], "acked_seq": body["events"][-1]["seq"],
                "stored": len(body["events"]), "duplicates": 0}
    return {"pod_id": body["pod_id"], "acked_seq": None}


class FakePod:
    def __init__(self, pod_id: str, rates: dict[str, float], trend: dict[str, float],
                 seed: int, mobile: bool = False, drop: float = 0.0, loss: float = 0.0,
                 dup: float = 0.0):
        self.pod_id = pod_id
        self.rates = rates
        self.trend = trend              # % change in visit rate per week, per species
        self.rng = random.Random(seed)
        self.mobile = mobile
        self.drop, self.loss, self.dup = drop, loss, dup
        self.seq = 0                    # last event seq created
        self.acked = 0                  # server's acked_seq
        self.queue: list[dict] = []     # created but not yet acked, ascending seq
        self.recent_acked: list[dict] = []  # last few acked events, for --dup resends
        self.track_id = 0
        self.hb_seq = 0
        self.restarts = 0
        self.temp = 55.0 + self.rng.random() * 5
        self.disk_mb = 24_000
        self.lat, self.lon = START_LAT, START_LON

    # ---- event generation ----

    def rate_at(self, species: str, t_ms: int, t0_ms: int) -> float:
        """Visits per day for a species at time t, with an optional weekly trend."""
        weeks = (t_ms - t0_ms) / (7 * DAY_MS)
        return self.rates[species] * (1 + self.trend.get(species, 0) / 100) ** weeks

    def make_visit(self, species: str, t_ms: float, ended: bool = False) -> dict:
        """One visit starting at t_ms (or, with ended=True, finishing at t_ms, the way
        the real tracker only emits a visit once the animal has left)."""
        r = self.rng
        self.seq += 1
        self.track_id += 1
        dwell_s = max(1.0, r.lognormvariate(math.log(DWELL_S.get(species, 8)), 0.6))
        t_start_ms = t_ms - dwell_s * 1000 if ended else t_ms
        fps_seen = r.uniform(4, 9)       # detections per second while visible
        ev = {
            "pod_id": self.pod_id, "seq": self.seq,
            "t_start_ms": int(t_start_ms), "t_end_ms": int(t_start_ms + dwell_s * 1000),
            "class_id": SPECIES_IDS.get(species, 0), "species": species,
            "max_score": round(min(0.99, 0.5 + r.betavariate(4, 2) * 0.5), 3),
            "track_id": self.track_id,
            "n_frames": max(3, int(dwell_s * fps_seen)),
        }
        if self.mobile:
            self.lat += r.gauss(0, 0.0005)
            self.lon += r.gauss(0, 0.0005)
            ev.update(lat=round(self.lat, 6), lon=round(self.lon, 6), loc_source="simulated")
        self.queue.append(ev)
        return ev

    def pick_species(self) -> str:
        names = list(self.rates)
        return self.rng.choices(names, weights=[self.rates[n] for n in names])[0]

    # ---- uplink (store-and-forward, like pod-uplink) ----

    def sync(self, post: PostFn, t_ms: int) -> None:
        """Like a Pod after reboot: ask the server how far it already has us, and
        continue numbering from there. Without this, a second run with the same
        pod id would restart at seq 1 and every event would be ignored as a duplicate."""
        self.heartbeat(post, t_ms)
        self.seq = max(self.seq, self.acked)

    def send(self, post: PostFn) -> int:
        """Send queued events in batches. Returns how many batches went out."""
        sent, stalled = 0, 0
        while self.queue and stalled < 3:
            if self.rng.random() < self.drop:            # simulated network outage
                return sent
            batch = self.queue[:BATCH_MAX]
            if self.loss and len(batch) > 1 and self.rng.random() < self.loss:
                lost = self.rng.randrange(len(batch))    # this one never arrives; resent later
                batch = batch[:lost] + batch[lost + 1:]
            if self.dup and self.recent_acked and self.rng.random() < self.dup:
                # re-send a couple of already-acked events: the server must ignore them
                batch = (self.recent_acked[-2:] + batch)[:BATCH_MAX]
            ack = post("/api/events", {"pod_id": self.pod_id, "events": batch})
            if ack is None:
                return sent
            sent += 1
            before = self.acked
            self._apply_ack(ack.get("acked_seq"), batch)
            stalled = stalled + 1 if self.acked == before else 0
        return sent

    def _apply_ack(self, acked_seq: Optional[int], batch: list[dict]) -> None:
        if acked_seq is None:                            # dry run: treat batch as acked
            acked_seq = max((e["seq"] for e in batch), default=self.acked)
        self.acked = max(self.acked, acked_seq)
        done = [e for e in self.queue if e["seq"] <= self.acked]
        self.recent_acked = (self.recent_acked + done)[-10:]
        self.queue = [e for e in self.queue if e["seq"] > self.acked]

    def heartbeat(self, post: PostFn, t_ms: int) -> None:
        r = self.rng
        self.hb_seq += 1
        self.temp = min(80.0, max(45.0, self.temp + r.gauss(0, 0.4)))
        self.disk_mb = max(0, self.disk_mb - r.randint(0, 2))
        p50 = r.gauss(92, 3)
        p99 = p50 + abs(r.gauss(48, 6))
        hb = {
            "pod_id": self.pod_id, "seq": self.hb_seq, "t_wall_ms": int(t_ms),
            "infer_p50_ms": round(p50, 1), "infer_p99_ms": round(p99, 1),
            "e2e_p50_ms": round(p50 + r.gauss(9, 1), 1), "e2e_p99_ms": round(p99 + r.gauss(11, 2), 1),
            "temp_c": round(self.temp, 1), "disk_free_mb": self.disk_mb,
            "restarts": self.restarts, "queue_depth": len(self.queue),
            "last_ack_seq": self.acked,
        }
        ack = post("/api/heartbeat", hb)
        if ack and ack.get("acked_seq") is not None and ack["acked_seq"] > self.acked:
            self._apply_ack(ack["acked_seq"], [])  # resync after a restart


# ---- modes ----

def backfill(pods: list[FakePod], days: float, post: PostFn, now_ms: Optional[int] = None) -> int:
    """Generate `days` of history per Pod (Poisson visits, hour by hour), then send it all."""
    now_ms = now_ms or int(time.time() * 1000)
    t0 = int(now_ms - days * DAY_MS)
    hour = 3_600_000
    total = 0
    for pod in pods:
        pod.sync(post, now_ms)
        starts = []                                       # (t_start_ms, species)
        for h in range(int(math.ceil(days * 24))):
            t = t0 + h * hour
            for sp in pod.rates:
                lam = pod.rate_at(sp, t, t0) / 24         # expected visits this hour
                starts += [(t + pod.rng.random() * hour, sp) for _ in range(_poisson(pod.rng, lam))]
        for t, sp in sorted(starts):                      # seq follows time order
            if t < now_ms:
                pod.make_visit(sp, t)
                total += 1
        for _ in range(1000):                             # retries cover --drop / --loss
            if not pod.queue:
                break
            pod.send(post)
        if pod.queue:
            print(f"{pod.pod_id}: {len(pod.queue)} events still unacked", file=sys.stderr)
        pod.heartbeat(post, now_ms)
    return total


def live(pods: list[FakePod], live_rate: float, duration: Optional[float], post: PostFn,
         tick_s: float = 1.0, sleep: Callable[[float], None] = time.sleep,
         clock: Callable[[], float] = time.time) -> int:
    """Real-time feed: `live_rate` visits per minute per Pod, heartbeats every 10 s."""
    start = clock()
    total, ticks = 0, 0
    for pod in pods:
        pod.sync(post, int(start * 1000))
    while duration is None or clock() - start < duration:
        now_ms = int(clock() * 1000)
        for pod in pods:
            for _ in range(_poisson(pod.rng, live_rate / 60 * tick_s)):
                pod.make_visit(pod.pick_species(), now_ms - pod.rng.random() * 1000, ended=True)
                total += 1
            if ticks % 2 == 0:
                pod.send(post)
            if ticks % 10 == 0:
                pod.heartbeat(post, now_ms)
        ticks += 1
        sleep(tick_s)
    for pod in pods:   # flush what is left; retry past simulated outages (a real Pod keeps them on disk)
        for _ in range(50):
            if not pod.queue:
                break
            pod.send(post)
        if pod.queue:
            print(f"{pod.pod_id}: {len(pod.queue)} events still unacked at exit", file=sys.stderr)
    return total


def _poisson(rng: random.Random, lam: float) -> int:
    if lam <= 0:
        return 0
    l, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= l:
            return k
        k += 1


def _parse_map(s: str, what: str) -> dict[str, float]:
    out = {}
    for part in filter(None, (p.strip() for p in s.split(","))):
        name, _, val = part.partition("=")
        if name not in SPECIES_IDS or not val:
            raise SystemExit(f"bad {what} entry {part!r}; species must be one of {sorted(SPECIES_IDS)}")
        out[name] = float(val)
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    # 127.0.0.1, not localhost: on Windows "localhost" tries IPv6 first and costs ~2 s per request.
    p.add_argument("--url", default="http://127.0.0.1:8000", help="ground API base URL")
    p.add_argument("--pods", type=int, default=1, help="number of fake Pods (pod-01, pod-02, ...)")
    p.add_argument("--first-pod", type=int, default=1, help="number of the first Pod id")
    p.add_argument("--rates", default="", help="visits per camera-day, e.g. zebra=6,bird=10 (backfill)")
    p.add_argument("--trend", default="", help="%% change per week, e.g. elephant=-5")
    p.add_argument("--live-rate", type=float, default=6.0, help="visits per minute per Pod (live mode)")
    p.add_argument("--duration", type=float, default=None, help="live mode: stop after N seconds")
    p.add_argument("--backfill-days", type=float, default=0, help="send N days of history, then exit")
    p.add_argument("--mobile", action="store_true", help="add simulated lat/lon (drone/rover mode)")
    p.add_argument("--drop", type=float, default=0.0, help="probability a send attempt hits a network outage")
    p.add_argument("--loss", type=float, default=0.0, help="probability one event is lost from a batch")
    p.add_argument("--dup", type=float, default=0.0, help="probability a batch re-sends acked events")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--dry-run", action="store_true", help="print JSON instead of sending")
    a = p.parse_args(argv)

    rates = _parse_map(a.rates, "rate") or dict(DEFAULT_RATES)
    trend = _parse_map(a.trend, "trend")
    unknown = set(trend) - set(rates)
    if unknown:
        raise SystemExit(f"--trend species not in --rates: {sorted(unknown)}")
    seed = a.seed if a.seed is not None else random.randrange(1 << 30)
    pods = [FakePod(f"pod-{n:02d}", rates, trend, seed + n, a.mobile, a.drop, a.loss, a.dup)
            for n in range(a.first_pod, a.first_pod + a.pods)]
    post = dry_run_post if a.dry_run else http_post(a.url)

    if a.backfill_days:
        n = backfill(pods, a.backfill_days, post)
        print(f"backfill: {n} visits over {a.backfill_days:g} days from {len(pods)} pod(s); "
              f"acked: {', '.join(f'{x.pod_id}={x.acked}' for x in pods)}", file=sys.stderr)
        return 0
    print(f"live: {a.live_rate:g} visits/min per pod, {len(pods)} pod(s) -> "
          f"{'stdout' if a.dry_run else a.url} (Ctrl+C to stop)", file=sys.stderr)
    try:
        n = live(pods, a.live_rate, a.duration, post)
    except KeyboardInterrupt:
        n = sum(x.seq for x in pods)
    print(f"live: {n} visits sent; acked: {', '.join(f'{x.pod_id}={x.acked}' for x in pods)}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
