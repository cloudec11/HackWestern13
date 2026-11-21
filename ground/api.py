"""Ground station ingest API (agent-3, task P1-3).

Receives visit events and heartbeats from Pods (shared/event_schema.json v1),
stores them in SQLite, and serves a summary for the dashboard.

Run from the repo root:
    ground/.venv/Scripts/python -m uvicorn ground.api:app --host 0.0.0.0 --port 8000

Environment:
    POD_DB   path to the SQLite file (default ground/db/pod.sqlite3)

Endpoints:
    POST /api/events     body: event_batch     -> event_ack
    POST /api/heartbeat  body: heartbeat       -> heartbeat_ack
    GET  /api/summary    dashboard overview (pods, species counts, recent visits)
    GET  /api/events     raw visits, filterable (for density.py and the dashboard)
    GET  /health         liveness check
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "shared" / "event_schema.json"
SQL_PATH = Path(__file__).resolve().parent / "db" / "schema.sql"
DEFAULT_DB = Path(__file__).resolve().parent / "db" / "pod.sqlite3"

EVENT_COLS = ["pod_id", "seq", "t_start_ms", "t_end_ms", "class_id", "species", "max_score",
              "track_id", "n_frames", "thumb_path", "lat", "lon", "loc_source"]
HEARTBEAT_COLS = ["pod_id", "seq", "t_wall_ms", "infer_p50_ms", "infer_p99_ms", "e2e_p50_ms",
                  "e2e_p99_ms", "temp_c", "disk_free_mb", "restarts", "queue_depth", "last_ack_seq"]


# ---- Schema validation (shared/event_schema.json is the single source of truth) ----

def _load_validators() -> dict[str, Draft202012Validator]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    registry = Registry().with_resource(schema["$id"], Resource.from_contents(schema))
    return {
        name: Draft202012Validator({"$ref": f"{schema['$id']}#/$defs/{name}"}, registry=registry)
        for name in ("event_batch", "heartbeat")
    }


VALIDATORS = _load_validators()


def _validate(kind: str, body: Any) -> None:
    errors = sorted(VALIDATORS[kind].iter_errors(body), key=lambda e: list(e.absolute_path))
    if errors:
        detail = [{"path": "/".join(str(p) for p in e.absolute_path) or "(root)", "error": e.message}
                  for e in errors[:20]]
        raise HTTPException(status_code=422, detail=detail)


def now_ms() -> int:
    return int(time.time() * 1000)


# ---- Storage ----

class Store:
    def __init__(self, db_path: str | os.PathLike):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self.conn() as c:
            c.executescript(SQL_PATH.read_text(encoding="utf-8"))

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.db_path, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    @staticmethod
    def _touch_pod(c: sqlite3.Connection, pod_id: str, t: int) -> None:
        c.execute(
            "INSERT INTO pods (pod_id, first_seen_ms, last_seen_ms) VALUES (?, ?, ?) "
            "ON CONFLICT(pod_id) DO UPDATE SET last_seen_ms = excluded.last_seen_ms",
            (pod_id, t, t),
        )

    @staticmethod
    def _advance_ack(c: sqlite3.Connection, pod_id: str) -> int:
        """Move acked_seq forward over every contiguous stored seq. Returns the new value."""
        acked = c.execute("SELECT acked_seq FROM pods WHERE pod_id = ?", (pod_id,)).fetchone()[0]
        for (seq,) in c.execute(
            "SELECT seq FROM events WHERE pod_id = ? AND seq > ? ORDER BY seq", (pod_id, acked)
        ):
            if seq != acked + 1:
                break
            acked = seq
        c.execute("UPDATE pods SET acked_seq = ? WHERE pod_id = ?", (acked, pod_id))
        return acked

    def add_events(self, pod_id: str, events: list[dict]) -> dict:
        t = now_ms()
        stored = 0
        placeholders = ", ".join("?" for _ in EVENT_COLS)
        with self.conn() as c:  # one transaction: the whole batch or nothing
            self._touch_pod(c, pod_id, t)
            for ev in events:
                cur = c.execute(
                    f"INSERT OR IGNORE INTO events ({', '.join(EVENT_COLS)}, received_ms) "
                    f"VALUES ({placeholders}, ?)",
                    [ev.get(k) for k in EVENT_COLS] + [t],
                )
                stored += cur.rowcount
            acked = self._advance_ack(c, pod_id)
        return {"pod_id": pod_id, "acked_seq": acked, "stored": stored,
                "duplicates": len(events) - stored}

    def add_heartbeat(self, hb: dict) -> dict:
        t = now_ms()
        placeholders = ", ".join("?" for _ in HEARTBEAT_COLS)
        with self.conn() as c:
            self._touch_pod(c, hb["pod_id"], t)
            c.execute(
                f"INSERT OR IGNORE INTO heartbeats ({', '.join(HEARTBEAT_COLS)}, received_ms) "
                f"VALUES ({placeholders}, ?)",
                [hb[k] for k in HEARTBEAT_COLS] + [t],
            )
            acked = c.execute("SELECT acked_seq FROM pods WHERE pod_id = ?",
                              (hb["pod_id"],)).fetchone()[0]
        return {"pod_id": hb["pod_id"], "acked_seq": acked}

    def events(self, pod_id: str | None, species: str | None, since_ms: int | None,
               limit: int) -> list[dict]:
        where, args = [], []
        if pod_id:
            where.append("pod_id = ?"); args.append(pod_id)
        if species:
            where.append("species = ?"); args.append(species)
        if since_ms is not None:
            where.append("t_start_ms >= ?"); args.append(since_ms)
        sql = f"SELECT {', '.join(EVENT_COLS)}, received_ms FROM events"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY t_start_ms DESC, seq DESC LIMIT ?"
        args.append(limit)
        with self.conn() as c:
            return [_clean(dict(r)) for r in c.execute(sql, args)]

    def summary(self, recent: int) -> dict:
        with self.conn() as c:
            pods = []
            for p in c.execute("SELECT * FROM pods ORDER BY pod_id"):
                p = dict(p)
                p["n_events"] = c.execute("SELECT COUNT(*) FROM events WHERE pod_id = ?",
                                          (p["pod_id"],)).fetchone()[0]
                hb = c.execute(
                    f"SELECT {', '.join(HEARTBEAT_COLS)}, received_ms FROM heartbeats "
                    "WHERE pod_id = ? ORDER BY t_wall_ms DESC LIMIT 1", (p["pod_id"],)
                ).fetchone()
                p["last_heartbeat"] = dict(hb) if hb else None
                pods.append(p)
            species = [dict(r) for r in c.execute(
                "SELECT species, COUNT(*) AS visits, MIN(t_start_ms) AS first_seen_ms, "
                "MAX(t_end_ms) AS last_seen_ms, MAX(max_score) AS best_score "
                "FROM events GROUP BY species ORDER BY visits DESC, species"
            )]
            total = c.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        return {
            "generated_ms": now_ms(),
            "total_events": total,
            "pods": pods,
            "species": species,
            "recent_events": self.events(None, None, None, recent),
        }


def _clean(row: dict) -> dict:
    """Drop NULL optional fields so rows look like the JSON events that came in."""
    return {k: v for k, v in row.items() if v is not None}


# ---- App ----

def create_app(db_path: str | os.PathLike | None = None) -> FastAPI:
    store = Store(db_path or os.environ.get("POD_DB", DEFAULT_DB))
    app = FastAPI(title="The Pod ground station", version="1")
    app.state.store = store
    # Hackathon LAN only: let the dashboard (any local origin) call the API.
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                       allow_headers=["*"])

    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "time_ms": now_ms()}

    @app.post("/api/events")
    def post_events(body: Any = Body(...)) -> dict:
        _validate("event_batch", body)
        pod_id = body["pod_id"]
        problems = []
        for i, ev in enumerate(body["events"]):
            if ev["pod_id"] != pod_id:
                problems.append({"path": f"events/{i}/pod_id",
                                 "error": f"{ev['pod_id']!r} does not match batch pod_id {pod_id!r}"})
            if ev["t_end_ms"] < ev["t_start_ms"]:
                problems.append({"path": f"events/{i}/t_end_ms", "error": "t_end_ms < t_start_ms"})
        if problems:
            raise HTTPException(status_code=422, detail=problems)
        return store.add_events(pod_id, body["events"])

    @app.post("/api/heartbeat")
    def post_heartbeat(body: Any = Body(...)) -> dict:
        _validate("heartbeat", body)
        return store.add_heartbeat(body)

    @app.get("/api/summary")
    def get_summary(recent: int = Query(20, ge=0, le=500)) -> dict:
        return store.summary(recent)

    @app.get("/api/events")
    def get_events(pod_id: str | None = None, species: str | None = None,
                   since_ms: int | None = None,
                   limit: int = Query(200, ge=1, le=10000)) -> list[dict]:
        return store.events(pod_id, species, since_ms, limit)

    return app


app = create_app()
