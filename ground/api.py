"""Ground station ingest API (agent-3, task P1-3). Python standard library only.

Receives visit events and heartbeats from Pods (shared/event_schema.json v1),
stores them in SQLite, and serves a summary for the dashboard. It runs
unchanged on the laptop and on the QNX Pi (Python 3.10+, no pip packages).

Run from the repo root:
    python -m ground.api                       # 0.0.0.0:8000, db ground/db/pod.sqlite3
    python -m ground.api --port 8000 --db /path/pod.sqlite3 --quiet

Environment (used when the flags are absent):
    POD_DB   path to the SQLite file
    POD_PORT port (default 8000)

Endpoints:
    POST /api/events     body: event_batch     -> event_ack
    POST /api/heartbeat  body: heartbeat       -> heartbeat_ack
    GET  /api/summary    ?recent=20            dashboard overview
    GET  /api/events     ?pod_id=&species=&since_ms=&limit=200
    GET  /api/zones      zones, their area and Pods
    GET  /api/density    ?zone_id=&species=   Random Encounter Model estimates (ground/density.py)
    GET  /api/scene      ?pod_id=&hours=24    scene JSON for the habitat view (ground/dashboard/sim)
    GET  /api/simulation ?zone_id=&species=&threshold=50   10-year forecast fan chart (ground/simulate.py)
    GET  /api/allocate   ?budget=50000&threshold=50       budget split (ground/allocate.py), cached for the slider
    GET  /api/sightings  ?zone_id=&species=&window_days=7&baseline_days=28   expected vs actual sightings + alerts
    GET  /health         liveness check

Errors are JSON: {"detail": [{"path": ..., "error": ...}, ...]} with HTTP 422
for bad input, 404 for unknown paths, 405 for a wrong method, 413 for a body over 1 MB.

`create_app(db_path)` returns a plain WSGI app, for tests or another WSGI server.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Any, Callable, Iterator
from urllib.parse import parse_qs
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from ground import allocate, density, params, sightings, simulate
from ground.schema_check import load_validators

HERE = Path(__file__).resolve().parent
SQL_PATH = HERE / "db" / "schema.sql"
DEFAULT_DB = HERE / "db" / "pod.sqlite3"
MAX_BODY = 1_000_000

EVENT_COLS = ["pod_id", "seq", "t_start_ms", "t_end_ms", "class_id", "species", "max_score",
              "track_id", "n_frames", "thumb_path", "lat", "lon", "loc_source"]
HEARTBEAT_COLS = ["pod_id", "seq", "t_wall_ms", "infer_p50_ms", "infer_p99_ms", "e2e_p50_ms",
                  "e2e_p99_ms", "temp_c", "disk_free_mb", "restarts", "queue_depth", "last_ack_seq"]

VALIDATORS = load_validators()   # shared/event_schema.json is the single source of truth


def now_ms() -> int:
    return int(time.time() * 1000)


class HTTPError(Exception):
    def __init__(self, status: int, detail: Any):
        super().__init__(detail)
        self.status, self.detail = status, detail


def _problems(errors: list[tuple[str, str]]) -> list[dict]:
    return [{"path": p, "error": m} for p, m in errors[:20]]


def _validate(kind: str, body: Any) -> None:
    errors = VALIDATORS[kind].errors(body)
    if errors:
        raise HTTPError(422, _problems(errors))


# ---- Storage ----

class Store:
    def __init__(self, db_path: str | os.PathLike, durable: bool = True):
        self.db_path = str(db_path)
        self.durable = durable
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self.conn() as c:
            c.executescript(SQL_PATH.read_text(encoding="utf-8"))

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.db_path, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        if not self.durable:
            c.execute("PRAGMA synchronous = OFF")   # tests only (see create_app)
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
        ).fetchall():
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
            for p in c.execute("SELECT * FROM pods ORDER BY pod_id").fetchall():
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


# ---- Request helpers ----

def _int_param(q: dict, name: str, default: int | None, lo: int | None = None,
               hi: int | None = None) -> int | None:
    vals = q.get(name)
    if not vals or vals[-1] == "":
        return default
    try:
        n = int(vals[-1])
    except ValueError:
        raise HTTPError(422, [{"path": name, "error": f"{vals[-1]!r} is not an integer"}])
    if (lo is not None and n < lo) or (hi is not None and n > hi):
        raise HTTPError(422, [{"path": name, "error": f"must be between {lo} and {hi}"}])
    return n


def _str_param(q: dict, name: str) -> str | None:
    vals = q.get(name)
    return vals[-1] if vals and vals[-1] != "" else None


def _read_json(environ: dict) -> Any:
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except ValueError:
        length = 0
    if length > MAX_BODY:
        raise HTTPError(413, [{"path": "(body)", "error": f"body over {MAX_BODY} bytes"}])
    raw = environ["wsgi.input"].read(length) if length else b""
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise HTTPError(422, [{"path": "(body)", "error": f"body is not valid JSON: {e}"}])


# ---- App ----

CORS = [("Access-Control-Allow-Origin", "*"),
        ("Access-Control-Allow-Methods", "GET, POST, OPTIONS"),
        ("Access-Control-Allow-Headers", "Content-Type")]
REASONS = {200: "OK", 204: "No Content", 404: "Not Found", 405: "Method Not Allowed",
           413: "Payload Too Large", 422: "Unprocessable Entity", 500: "Internal Server Error"}


class App:
    """WSGI application. Routes: {(method, path): handler(environ, query) -> dict|list}."""

    def __init__(self, db_path: str | os.PathLike, max_stale_s: float | None = None, durable: bool = True):
        self.store = Store(db_path, durable=durable)
        # How long a cached science result may be reused after new data arrives (seconds).
        # None = defaults per result (budget 30 s, density/forecast 10 s); 0 = always fresh (tests).
        self.max_stale_s = max_stale_s
        self._memo: dict = {}
        self._memo_lock = threading.Lock()
        self.routes: dict[tuple[str, str], Callable[[dict, dict], Any]] = {
            ("GET", "/health"): lambda env, q: {"ok": True, "time_ms": now_ms()},
            ("POST", "/api/events"): self.post_events,
            ("POST", "/api/heartbeat"): self.post_heartbeat,
            ("GET", "/api/summary"): lambda env, q: self.store.summary(
                _int_param(q, "recent", 20, 0, 500)),
            ("GET", "/api/events"): lambda env, q: self.store.events(
                _str_param(q, "pod_id"), _str_param(q, "species"),
                _int_param(q, "since_ms", None), _int_param(q, "limit", 200, 1, 10000)),
            ("GET", "/api/zones"): self.get_zones,
            ("GET", "/api/density"): self.get_density,
            ("GET", "/api/scene"): self.get_scene,
            ("GET", "/api/simulation"): self.get_simulation,
            ("GET", "/api/allocate"): self.get_allocate,
            ("GET", "/api/params"): lambda env, q: params_report(),
            ("GET", "/api/sightings"): self.get_sightings,
        }
    HB_BUCKET = 60          # heartbeats per cache step: one heartbeat barely changes camera time

    def _data_version(self, c) -> tuple:
        """Changes when visits arrive, and every HB_BUCKET heartbeats (agent-1 review, finding 2:
        keying on every heartbeat rebuilt the 2.3 s budget scenarios every 10 s)."""
        ev = c.execute("SELECT COUNT(*), MAX(received_ms) FROM events").fetchone()
        hb = c.execute("SELECT COUNT(*) FROM heartbeats").fetchone()[0]
        return (ev[0], ev[1], hb // self.HB_BUCKET)

    def _cached(self, name: str, key: tuple, compute: Callable[[sqlite3.Connection], Any],
                max_stale_default: float) -> tuple[Any, bool, float]:
        """Memoise a science result on the data version. A result is also reused for up to
        max_stale seconds after the data changes, so a busy live feed can't make every
        dashboard poll or slider move wait for a rebuild (agent-1 review, findings 2 and 3).
        Returns (value, cache_hit, age_s)."""
        max_stale = self.max_stale_s if self.max_stale_s is not None else max_stale_default
        with self._memo_lock:              # one rebuild at a time; concurrent callers then hit the cache
            with self.store.conn() as c:
                version = self._data_version(c)
                hit = self._memo.get((name, key))
                now = time.time()
                if hit and (hit[0] == version or now - hit[1] < max_stale):
                    return hit[2], True, round(now - hit[1], 1)
                value = compute(c)
                self._memo[(name, key)] = (version, now, value)
                return value, False, 0.0

    def get_sightings(self, environ: dict, q: dict) -> dict:
        """Expected vs actual sightings for the last window_days, from the days before it
        (ground/sightings.py), plus the 10-year forecast in sightings and the chance of decline."""
        window = _int_param(q, "window_days", 7, 1, 60)
        baseline = _int_param(q, "baseline_days", 28, 3, 365)
        zone_id, species = _str_param(q, "zone_id"), _str_param(q, "species")

        def compute(c):
            sims = {(r["zone_id"], r["species"]): r
                    for r in simulate.simulation_results(density.density_results(c, zone_id, species))}
            out = []
            for z in density.zones(c):
                if zone_id and z["zone_id"] != zone_id:
                    continue
                for r in sightings.zone_sightings(c, z, window, baseline, species):
                    s = sims.get((r["zone_id"], r["species"]))
                    r["p_decline_10y"] = s.get("p_decline") if s else None
                    r["forecast_monthly"] = (sightings.forecast_in_sightings(s, z["area_km2"], len(z["pods"]))
                                             if s else None)
                    out.append(r)
            return out

        results, hit, age = self._cached("sightings", (zone_id, species, window, baseline), compute, 10)
        alerts = [{"kind": "sudden", "zone_id": r["zone_id"], "species": r["species"], "status": r["status"],
                   "actual": r["window"]["actual"], "expected": r["expected"], "change_pct": r["change_pct"]}
                  for r in results if r["status"] in ("below", "above", "new")]
        alerts += [{"kind": "trend", "zone_id": r["zone_id"], "species": r["species"],
                    "status": r["trend"]["status"], "weekly_change_pct": r["trend"]["weekly_change_pct"],
                    "ci90_pct": r["trend"]["ci90_pct"]}
                   for r in results if r.get("trend") and r["trend"]["status"] in ("declining", "increasing")]
        return {"generated_ms": now_ms(), "window_days": window, "baseline_days": baseline,
                "cached": hit, "data_age_s": age, "alerts": alerts, "results": results,
                "note": "Expected range is built only from the baseline days before the window. "
                        "'below' = fewer sightings than 95% of normal windows: worth a ranger's look."}

    def get_allocate(self, environ: dict, q: dict) -> dict:
        budget = _int_param(q, "budget", params.DEFAULT_BUDGET, 0, 100_000_000)
        threshold = _int_param(q, "threshold", None, 1, 100_000)
        table, hit, age = self._cached(
            "allocate", (threshold,),
            lambda c: allocate.scenario_table(density.density_results(c), threshold), 30)
        out = allocate.allocate(table, budget)
        out.update(threshold=threshold or params.QUASI_EXTINCTION, options=allocate.options(table),
                   cached=hit, data_age_s=age, generated_ms=now_ms())
        return out

    def get_simulation(self, environ: dict, q: dict) -> dict:
        threshold = _int_param(q, "threshold", None, 1, 100_000)
        zone_id, species = _str_param(q, "zone_id"), _str_param(q, "species")
        results, hit, age = self._cached(
            "simulation", (zone_id, species, threshold),
            lambda c: simulate.simulation_results(density.density_results(c, zone_id, species), threshold), 10)
        return {"generated_ms": now_ms(), "cached": hit, "data_age_s": age, "results": results}

    def get_scene(self, environ: dict, q: dict) -> dict:
        """Scene JSON for agent-4's habitat view (ground/dashboard/sim): what one Pod
        has seen recently. Animals are real visit counts; biome is the zone's
        configured setting; weather is a placeholder until someone owns weather."""
        hours = _int_param(q, "hours", 24, 1, 24 * 30)
        pod_id = _str_param(q, "pod_id")
        with self.store.conn() as c:
            if pod_id is None:
                row = c.execute("SELECT pod_id FROM pods ORDER BY last_seen_ms DESC LIMIT 1").fetchone()
                pod_id = row[0] if row else None
            animals = []
            if pod_id is not None:
                since = now_ms() - hours * 3_600_000
                animals = [{"species": sp, "count": n} for sp, n in c.execute(
                    "SELECT species, COUNT(*) AS n FROM events WHERE pod_id = ? AND t_end_ms >= ? "
                    "AND species != 'unknown' GROUP BY species ORDER BY n DESC, species", (pod_id, since))]
        zone = density.params.zone_info(density.params.zone_of(pod_id or ""))
        return {
            "_note": "From the ground API. animals = visit events in the last `hours`; "
                     "biome = zone setting in ground/params.py; weather = placeholder.",
            "pod_id": pod_id or "no-pod-yet",
            "zone_id": zone["zone_id"],
            "hours": hours,
            "biome": zone.get("biome", "savanna"),
            "biome_source": "zone setting",
            "weather": {"condition": "clear", "source": "placeholder (no weather feed yet)"},
            "animals": animals,
        }

    def get_zones(self, environ: dict, q: dict) -> dict:
        with self.store.conn() as c:
            return {"zones": density.zones(c)}

    def get_density(self, environ: dict, q: dict) -> dict:
        zone_id, species = _str_param(q, "zone_id"), _str_param(q, "species")
        results, hit, age = self._cached("density", (zone_id, species),
                                         lambda c: density.density_results(c, zone_id, species), 10)
        return {"generated_ms": now_ms(), "cached": hit, "data_age_s": age, "results": results}

    def post_events(self, environ: dict, q: dict) -> dict:
        body = _read_json(environ)
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
            raise HTTPError(422, problems)
        return self.store.add_events(pod_id, body["events"])

    def post_heartbeat(self, environ: dict, q: dict) -> dict:
        body = _read_json(environ)
        _validate("heartbeat", body)
        return self.store.add_heartbeat(body)

    def __call__(self, environ: dict, start_response: Callable) -> list[bytes]:
        method = environ.get("REQUEST_METHOD", "GET").upper()
        path = environ.get("PATH_INFO", "/").rstrip("/") or "/"
        if method == "OPTIONS":                       # CORS preflight from the dashboard
            start_response("204 No Content", CORS)
            return [b""]
        try:
            handler = self.routes.get((method, path))
            if handler is None:
                if any(p == path for _, p in self.routes):
                    raise HTTPError(405, [{"path": path, "error": f"{method} not allowed"}])
                raise HTTPError(404, [{"path": path, "error": "no such endpoint"}])
            status, payload = 200, handler(environ, parse_qs(environ.get("QUERY_STRING", "")))
        except HTTPError as e:
            status, payload = e.status, {"detail": e.detail}
        except Exception as e:                        # never crash the server on one request
            traceback.print_exc(file=sys.stderr)
            status, payload = 500, {"detail": [{"path": path, "error": f"{type(e).__name__}: {e}"}]}
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        start_response(f"{status} {REASONS.get(status, '')}",
                       [("Content-Type", "application/json"), ("Content-Length", str(len(data)))] + CORS)
        return [data]


def params_report() -> dict:
    """Every setting the science uses, straight from ground/params.py (nothing copied by hand)."""
    def lst(v):
        return list(v) if isinstance(v, tuple) else v
    return {
        "illustrative": True,
        "note": "All values are illustrative except NOISE_SCALE, which was calibrated on real moose counts.",
        "camera": dict(params.CAMERA),
        "species": {sp: {"v_km_per_day": d.get("v_km_per_day"), "rem_ok": d.get("rem_ok"),
                         **({k: lst(v) for k, v in params.GROWTH[sp].items()} if sp in params.GROWTH else {}),
                         **({"sigma_deployed": round(params.GROWTH[sp]["sigma"] * params.NOISE_SCALE, 3)}
                            if sp in params.GROWTH else {})}
                    for sp, d in params.SPECIES.items()},
        "zones": [{**{k: z[k] for k in ("zone_id", "name", "area_km2", "pods", "biome")},
                   "threat": params.ZONE_THREAT.get(z["zone_id"], 1.0)} for z in params.ZONES],
        "interventions": params.INTERVENTIONS,
        "forecast": {"years": params.FORECAST_YEARS, "runs": params.FORECAST_RUNS,
                     "threshold": params.QUASI_EXTINCTION, "k_factor": list(params.K_FACTOR),
                     "noise_scale": params.NOISE_SCALE},
        "density": {"min_visits": params.MIN_VISITS, "outage_gap_min": params.OUTAGE_GAP_MIN,
                    "bootstrap_resamples": params.BOOTSTRAP_RESAMPLES, "ci_level": params.CI_LEVEL},
        "allocation": {"min_gain": params.MIN_GAIN, "default_budget": params.DEFAULT_BUDGET},
    }


def create_app(db_path: str | os.PathLike | None = None, max_stale_s: float | None = None,
               durable: bool = True) -> App:
    """durable=False skips the per-commit disk sync: tests only. The real server keeps it
    on, because an ack must mean the event is safely on disk."""
    return App(db_path or os.environ.get("POD_DB") or DEFAULT_DB, max_stale_s=max_stale_s, durable=durable)


class _ThreadingServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True
    allow_reuse_address = True


class _QuietHandler(WSGIRequestHandler):
    def log_message(self, *args) -> None:
        pass


def serve(host: str, port: int, db_path: str | os.PathLike | None, quiet: bool = False) -> None:
    app = create_app(db_path)
    handler = _QuietHandler if quiet else WSGIRequestHandler
    with make_server(host, port, app, server_class=_ThreadingServer, handler_class=handler) as srv:
        print(f"ground API on http://{host}:{port}  db={app.store.db_path}", file=sys.stderr, flush=True)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="The Pod ground station API (stdlib only)")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=int(os.environ.get("POD_PORT", 8000)))
    p.add_argument("--db", default=None, help="SQLite path (default: $POD_DB or ground/db/pod.sqlite3)")
    p.add_argument("--quiet", action="store_true", help="don't log every request")
    a = p.parse_args(argv)
    serve(a.host, a.port, a.db, a.quiet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
