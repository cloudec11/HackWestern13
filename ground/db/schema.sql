-- Ground station SQLite schema (agent-3). Mirrors shared/event_schema.json v1.
-- Applied idempotently at server start-up.

PRAGMA journal_mode = WAL;

-- One row per Pod. acked_seq = highest seq S such that events 1..S are all stored.
CREATE TABLE IF NOT EXISTS pods (
    pod_id        TEXT PRIMARY KEY,
    zone_id       TEXT REFERENCES zones(zone_id),
    acked_seq     INTEGER NOT NULL DEFAULT 0,
    first_seen_ms INTEGER NOT NULL,
    last_seen_ms  INTEGER NOT NULL
);

-- Visit events. (pod_id, seq) is the idempotency key: repeats are ignored.
CREATE TABLE IF NOT EXISTS events (
    pod_id      TEXT    NOT NULL,
    seq         INTEGER NOT NULL,
    t_start_ms  INTEGER NOT NULL,
    t_end_ms    INTEGER NOT NULL,
    class_id    INTEGER NOT NULL,
    species     TEXT    NOT NULL,
    max_score   REAL    NOT NULL,
    track_id    INTEGER NOT NULL,
    n_frames    INTEGER NOT NULL,
    thumb_path  TEXT,
    lat         REAL,
    lon         REAL,
    loc_source  TEXT,
    received_ms INTEGER NOT NULL,
    PRIMARY KEY (pod_id, seq)
);
CREATE INDEX IF NOT EXISTS events_species_time ON events (species, t_start_ms);
CREATE INDEX IF NOT EXISTS events_time ON events (t_start_ms);

-- Heartbeats. Key includes t_wall_ms: an exact resend is ignored, but a Pod that
-- reboots and restarts its heartbeat counter at 1 is still recorded.
CREATE TABLE IF NOT EXISTS heartbeats (
    pod_id       TEXT    NOT NULL,
    seq          INTEGER NOT NULL,
    t_wall_ms    INTEGER NOT NULL,
    infer_p50_ms REAL    NOT NULL,
    infer_p99_ms REAL    NOT NULL,
    e2e_p50_ms   REAL    NOT NULL,
    e2e_p99_ms   REAL    NOT NULL,
    temp_c       REAL    NOT NULL,
    disk_free_mb INTEGER NOT NULL,
    restarts     INTEGER NOT NULL,
    queue_depth  INTEGER NOT NULL,
    last_ack_seq INTEGER NOT NULL,
    received_ms  INTEGER NOT NULL,
    PRIMARY KEY (pod_id, seq, t_wall_ms)
);
CREATE INDEX IF NOT EXISTS heartbeats_time ON heartbeats (pod_id, t_wall_ms);

-- Conservation zones for density / simulation / budget (filled by later tasks).
CREATE TABLE IF NOT EXISTS zones (
    zone_id  TEXT PRIMARY KEY,
    name     TEXT NOT NULL,
    area_km2 REAL NOT NULL CHECK (area_km2 > 0)
);
