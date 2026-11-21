#!/usr/bin/env python3
"""pod-uplink: store-and-forward visit events from the Pod to the ground station.

Owner: agent-1 (P2-3). Python standard library only, so it runs on the QNX Pi.

Input (written by pod-log, P2-2): JSON Lines files  <events-dir>/events-*.jsonl.
One visit event per line, the JSON form of pod_event_t (shared/pod_msgs.h):
    {"pod_id": "pod-01", "seq": 42, "t_start_ms": ..., "t_end_ms": ..., "class_id": 5,
     "max_score": 0.87, "track_id": 12, "n_frames": 61, "thumb_path": "20261121/000042.jpg"}
Optional: lat, lon, loc_source (mobile mode). seq starts at 1, +1 per event, never reused.
A line without its trailing newline is still being written and is ignored until complete.

Output: POST /api/events and /api/heartbeat per shared/event_schema.json v1.
  - Adds "species" from models/labels.csv (agent-2) using class_id; drops an empty thumb_path.
  - Batches of up to 50 events from one pod, ascending seq.
  - The cursor (highest seq known to be held by the ground station) advances ONLY to the
    acked_seq the server returns, and is saved atomically, so a crash or network drop
    never loses an event; at worst some are resent, which the server ignores.
  - Network errors: retry with backoff 1, 2, 4 ... 30 s. Nothing is dropped.
  - Two "stuck forever" cases are detected and reported instead of looping:
      rejected  the server answered 422 (event fails the schema). The batch is written to
                <state-dir>/rejected.jsonl; uplink retries every 60 s in case the server changes.
      gap       the server's acked_seq is below this batch's first seq - 1, i.e. it is missing
                an event this Pod no longer has (pod-log bug). Reported, retried every 30 s.

Heartbeat: every 10 s, IF <state-dir>/heartbeat.json exists (the health fields:
infer_p50_ms, infer_p99_ms, e2e_p50_ms, e2e_p99_ms, temp_c, disk_free_mb, restarts,
written by the supervisor path, P3-1/P3-3). uplink fills pod_id, seq, t_wall_ms,
queue_depth and last_ack_seq itself. Without that file no heartbeat is sent, so the
dashboard never shows made-up health numbers.

Usage:
    python3 pod/uplink/uplink.py --url http://<laptop-ip>:8000 \
        --events-dir ~/pod-data/events --state-dir ~/pod-data/uplink
    POD_GROUND_URL sets the default URL. --once sends until caught up, then exits.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BATCH_MAX = 50
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LABELS = REPO_ROOT / "models" / "labels.csv"
WIRE_FIELDS = ("pod_id", "seq", "t_start_ms", "t_end_ms", "class_id", "species", "max_score",
               "track_id", "n_frames", "thumb_path", "lat", "lon", "loc_source")
HEALTH_FIELDS = ("infer_p50_ms", "infer_p99_ms", "e2e_p50_ms", "e2e_p99_ms", "temp_c",
                 "disk_free_mb", "restarts")
NET_ERRORS = (urllib.error.URLError, OSError, TimeoutError)


def load_species(path: Path) -> dict[int, str]:
    """class_id -> species from agent-2's labels.csv (lines starting with # are comments)."""
    species = {0: "unknown"}
    with open(path, newline="", encoding="utf-8") as f:
        rows = [line for line in f if line.strip() and not line.lstrip().startswith("#")]
    for row in csv.DictReader(rows):
        species[int(row["class_id"])] = row["species"].strip()
    return species


def read_events(events_dir: Path, after_seq: int, limit: int) -> tuple[list[dict], int, int]:
    """Events with seq > after_seq, ascending, at most `limit`.
    Returns (events, backlog = all such events, bad = complete lines that were not valid JSON)."""
    found: dict[int, dict] = {}
    bad = 0
    for path in sorted(events_dir.glob("events-*.jsonl")):
        try:
            data = path.read_bytes()
        except OSError:
            continue
        lines = data.split(b"\n")
        for raw in lines[:-1]:            # the last piece has no newline yet: still being written
            if not raw.strip():
                continue
            try:
                ev = json.loads(raw)
            except ValueError:
                bad += 1
                continue
            seq = ev.get("seq") if isinstance(ev, dict) else None
            if isinstance(seq, int) and seq > after_seq:
                found.setdefault(seq, ev)
    seqs = sorted(found)
    return [found[s] for s in seqs[:limit]], len(seqs), bad


def to_wire(ev: dict, species: dict[int, str]) -> dict:
    out = {k: ev[k] for k in WIRE_FIELDS if k in ev and ev[k] is not None}
    out["species"] = species.get(int(ev.get("class_id", 0)), "unknown")
    if out.get("thumb_path") == "":
        del out["thumb_path"]
    return out


def post_json(url: str, body: dict, timeout: float) -> tuple[int, object]:
    """Returns (HTTP status, parsed JSON body). Raises on network errors."""
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read() or b"null")
        except ValueError:
            detail = None
        return e.code, detail


def atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def read_int(path: Path) -> int:
    try:
        return int(path.read_text().strip() or 0)
    except (OSError, ValueError):
        return 0


class Uplink:
    def __init__(self, url: str, events_dir: Path, state_dir: Path, species: dict[int, str],
                 timeout: float = 5.0, log=print):
        self.url = url.rstrip("/")
        self.events_dir = Path(events_dir)
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.species = species
        self.timeout = timeout
        self.log = log
        self.cursor = read_int(self.state_dir / "cursor")
        self.hb_seq = read_int(self.state_dir / "heartbeat_seq")
        self.backlog = 0
        self.stored = 0
        self.duplicates = 0
        self.problem = ""                  # last stuck reason, "" when healthy
        self.pod_id = ""                   # learned from the events this Pod logs

    def _any_pod_id(self) -> str:
        for path in sorted(self.events_dir.glob("events-*.jsonl")):
            try:
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        try:
                            pid = json.loads(line).get("pod_id")
                        except (ValueError, AttributeError):
                            continue
                        if pid:
                            self.pod_id = pid
                            return pid
            except OSError:
                continue
        return ""

    def _set_cursor(self, acked: int) -> None:
        if acked > self.cursor:            # never moves backwards
            self.cursor = acked
            atomic_write(self.state_dir / "cursor", f"{acked}\n")

    def send_once(self) -> str:
        """Send one batch. Returns idle | sent | rejected | gap | server_error.
        Raises a network error if the ground station cannot be reached."""
        events, self.backlog, bad = read_events(self.events_dir, self.cursor, BATCH_MAX)
        if bad:
            self.log(f"pod-uplink: WARNING {bad} unreadable line(s) in {self.events_dir}")
        if not events:
            return "idle"
        pod_id = events[0]["pod_id"]
        self.pod_id = pod_id
        batch = []
        for ev in events:                  # one pod per batch (schema rule)
            if ev.get("pod_id") != pod_id:
                break
            batch.append(to_wire(ev, self.species))
        status, body = post_json(self.url + "/api/events", {"pod_id": pod_id, "events": batch}, self.timeout)
        if status == 200 and isinstance(body, dict) and isinstance(body.get("acked_seq"), int):
            self.stored += int(body.get("stored", 0))
            self.duplicates += int(body.get("duplicates", 0))
            acked = body["acked_seq"]
            self._set_cursor(acked)
            if acked < batch[0]["seq"] - 1:
                self.problem = (f"gap: ground station acked_seq={acked} but this batch starts at "
                                f"seq {batch[0]['seq']}; it lacks events this Pod no longer has")
                return "gap"
            self.problem = ""
            self.backlog = max(0, self.backlog - len(batch))
            return "sent"
        if status == 422:
            with open(self.state_dir / "rejected.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps({"t_ms": int(time.time() * 1000), "detail": body, "events": batch}) + "\n")
            self.problem = f"rejected: server returned 422 for seq {batch[0]['seq']}..{batch[-1]['seq']}: {body}"
            return "rejected"
        self.problem = f"server_error: HTTP {status} {body}"
        return "server_error"

    def heartbeat_once(self) -> str:
        """Send one heartbeat if a health file exists. Returns sent | no_source | rejected | server_error."""
        src = self.state_dir / "heartbeat.json"
        try:
            health = json.loads(src.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return "no_source"
        _, backlog, _ = read_events(self.events_dir, self.cursor, 1)
        pod_id = health.get("pod_id") or self.pod_id or self._any_pod_id()
        if not pod_id:
            return "no_source"
        hb = {k: health[k] for k in HEALTH_FIELDS if k in health}
        hb.update(pod_id=pod_id, seq=self.hb_seq + 1, t_wall_ms=int(time.time() * 1000),
                  queue_depth=backlog, last_ack_seq=self.cursor)
        status, body = post_json(self.url + "/api/heartbeat", hb, self.timeout)
        if status == 200:
            self.hb_seq += 1
            atomic_write(self.state_dir / "heartbeat_seq", f"{self.hb_seq}\n")
            if isinstance(body, dict) and isinstance(body.get("acked_seq"), int):
                self._set_cursor(body["acked_seq"])      # resync, e.g. after the state dir was lost
            return "sent"
        return "rejected" if status == 422 else "server_error"

    def step(self) -> str:
        """send_once, but a network failure becomes the result 'net_error'."""
        try:
            return self.send_once()
        except NET_ERRORS as e:
            self.problem = f"net_error: {e}"
            return "net_error"

    def run(self, once: bool = False, poll_s: float = 1.0, hb_every_s: float = 10.0,
            status_every_s: float = 10.0) -> int:
        backoff = 1.0
        next_hb = next_status = 0.0
        while True:
            result = self.step()
            now = time.monotonic()
            if now >= next_hb:
                try:
                    self.heartbeat_once()
                except NET_ERRORS:
                    pass
                next_hb = now + hb_every_s
            if now >= next_status or result in ("rejected", "gap"):
                self.log(f"pod-uplink: cursor={self.cursor} backlog={self.backlog} stored={self.stored} "
                         f"duplicates={self.duplicates} last={result}" + (f"  PROBLEM {self.problem}" if self.problem else ""))
                next_status = now + status_every_s
            if result == "sent":
                backoff = 1.0
                if self.backlog > 0:
                    continue                     # catch up without waiting
            if once and result in ("idle", "sent"):
                return 0
            if once:
                return 1
            if result in ("net_error", "server_error"):
                time.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
            elif result == "rejected":
                time.sleep(60)
            elif result == "gap":
                time.sleep(30)
            else:
                backoff = 1.0
                time.sleep(poll_s)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="pod-uplink: store-and-forward events to the ground station")
    p.add_argument("--url", default=os.environ.get("POD_GROUND_URL", "http://127.0.0.1:8000"))
    p.add_argument("--events-dir", default=os.environ.get("POD_EVENTS_DIR", "pod-data/events"))
    p.add_argument("--state-dir", default=os.environ.get("POD_UPLINK_STATE", "pod-data/uplink"))
    p.add_argument("--labels", default=str(DEFAULT_LABELS))
    p.add_argument("--once", action="store_true", help="send until caught up, then exit")
    a = p.parse_args(argv)
    up = Uplink(a.url, Path(a.events_dir), Path(a.state_dir), load_species(Path(a.labels)),
                log=lambda m: print(m, flush=True))
    up.log(f"pod-uplink: {a.events_dir} -> {up.url}, cursor={up.cursor}")
    try:
        return up.run(once=a.once)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
