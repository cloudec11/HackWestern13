"""Tests for pod-uplink against agent-3's real ground API (ground/api.py), run in-process.
Owner: agent-1 (P2-3). Standard library only:  python -m unittest pod/uplink/test_uplink.py -v
(run from the repo root; works on the laptop and on the Pi)."""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from wsgiref.simple_server import WSGIRequestHandler, make_server

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ground.api import create_app  # noqa: E402  (agent-3's server, used as-is)
import uplink  # noqa: E402

SPECIES = uplink.load_species(REPO / "models" / "labels.csv")
T0 = 1795283000000


class Quiet(WSGIRequestHandler):
    def log_message(self, *args):
        pass


class Server:
    """Ground API on 127.0.0.1:<free port>, backed by a given SQLite file."""
    def __init__(self, db: Path):
        self.httpd = make_server("127.0.0.1", 0, create_app(db), handler_class=Quiet)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def summary(self) -> dict:
        with urllib.request.urlopen(self.url + "/api/summary", timeout=5) as r:
            return json.loads(r.read())


def event(seq: int, class_id: int = 5, pod: str = "pod-01", **extra) -> dict:
    e = {"pod_id": pod, "seq": seq, "t_start_ms": T0 + seq * 10000, "t_end_ms": T0 + seq * 10000 + 4000,
         "class_id": class_id, "max_score": 0.8, "track_id": seq, "n_frames": 20, "thumb_path": ""}
    e.update(extra)
    return e


def write_events(path: Path, events: list[dict], trailing_partial: str = "") -> None:
    with open(path, "a", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
        f.write(trailing_partial)


def pod_row(server: Server, pod: str = "pod-01") -> dict:
    pods = server.summary()["pods"]
    rows = pods if isinstance(pods, list) else [dict(v, pod_id=k) for k, v in pods.items()]
    return next(p for p in rows if p["pod_id"] == pod)


class UplinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db = root / "pod.sqlite3"
        self.events = root / "events"
        self.events.mkdir()
        self.state = root / "uplink"
        self.server = Server(self.db)
        self.logs: list[str] = []

    def tearDown(self):
        self.server.stop()
        self.tmp.cleanup()

    def make(self) -> uplink.Uplink:
        return uplink.Uplink(self.server.url, self.events, self.state, SPECIES, timeout=3, log=self.logs.append)

    def drain(self, up: uplink.Uplink) -> list[str]:
        results = []
        for _ in range(20):
            r = up.step()
            results.append(r)
            if r != "sent":
                break
        return results

    def test_sends_everything_in_batches_and_saves_cursor(self):
        write_events(self.events / "events-0001.jsonl", [event(s) for s in range(1, 121)])
        up = self.make()
        results = self.drain(up)
        self.assertEqual(results, ["sent", "sent", "sent", "idle"])   # 50 + 50 + 20
        self.assertEqual(up.cursor, 120)
        self.assertEqual((self.state / "cursor").read_text().strip(), "120")
        row = pod_row(self.server)
        self.assertEqual(row["acked_seq"], 120)
        self.assertEqual(row["n_events"], 120)

    def test_restart_resumes_without_resending(self):
        write_events(self.events / "events-0001.jsonl", [event(s) for s in range(1, 31)])
        self.drain(self.make())
        write_events(self.events / "events-0002.jsonl", [event(s) for s in range(31, 41)])
        up2 = self.make()                                  # a new process: cursor comes from disk
        self.assertEqual(up2.cursor, 30)
        self.drain(up2)
        self.assertEqual(up2.cursor, 40)
        self.assertEqual(up2.duplicates, 0)
        self.assertEqual(up2.stored, 10)

    def test_network_down_keeps_everything_then_catches_up(self):
        write_events(self.events / "events-0001.jsonl", [event(s) for s in range(1, 11)])
        up = self.make()
        self.drain(up)
        self.server.stop()                                 # ground station goes away
        write_events(self.events / "events-0001.jsonl", [event(s) for s in range(11, 81)])
        self.assertEqual(up.step(), "net_error")
        self.assertEqual(up.cursor, 10)                    # nothing assumed delivered
        self.server = Server(self.db)                      # back, same database, new port
        up.url = self.server.url
        self.drain(up)
        self.assertEqual(up.cursor, 80)
        self.assertEqual(pod_row(self.server)["n_events"], 80)

    def test_partial_last_line_is_not_sent_until_complete(self):
        path = self.events / "events-0001.jsonl"
        half = json.dumps(event(2))[:25]
        write_events(path, [event(1)], trailing_partial=half)
        up = self.make()
        self.drain(up)
        self.assertEqual(up.cursor, 1)
        with open(path, "a", encoding="utf-8") as f:       # pod-log finishes the line
            f.write(json.dumps(event(2))[25:] + "\n")
        self.drain(up)
        self.assertEqual(up.cursor, 2)

    def test_species_added_and_empty_thumb_dropped(self):
        wire = uplink.to_wire(event(1, class_id=5), SPECIES)
        self.assertEqual(wire["species"], SPECIES[5])
        self.assertNotIn("thumb_path", wire)
        self.assertEqual(uplink.to_wire(event(1, class_id=0), SPECIES)["species"], "unknown")

    def test_rejected_event_is_reported_not_looped(self):
        write_events(self.events / "events-0001.jsonl", [event(1, max_score=2.0)])  # schema: max 1
        up = self.make()
        self.assertEqual(up.step(), "rejected")
        self.assertEqual(up.cursor, 0)
        self.assertIn("422", up.problem)
        self.assertTrue((self.state / "rejected.jsonl").exists())

    def test_gap_on_server_is_detected(self):
        write_events(self.events / "events-0001.jsonl", [event(s) for s in range(5, 11)])  # 1..4 missing
        up = self.make()
        self.assertEqual(up.step(), "gap")
        self.assertEqual(up.cursor, 0)
        self.assertIn("gap", up.problem)

    def test_heartbeat_only_with_health_file(self):
        write_events(self.events / "events-0001.jsonl", [event(1)])
        up = self.make()
        self.drain(up)
        self.assertEqual(up.heartbeat_once(), "no_source")
        (self.state / "heartbeat.json").write_text(json.dumps({
            "infer_p50_ms": 90.0, "infer_p99_ms": 140.0, "e2e_p50_ms": 100.0, "e2e_p99_ms": 150.0,
            "temp_c": 55.0, "disk_free_mb": 1000, "restarts": 0}))
        self.assertEqual(up.heartbeat_once(), "sent")
        self.assertEqual(up.hb_seq, 1)


if __name__ == "__main__":
    unittest.main()
