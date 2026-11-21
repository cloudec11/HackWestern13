# Agent 3 log

Append-only. Written by agent-3 only.

## 2026-11-21 14:30:22 | agent-3 | P0-3 | DOING
**What:** Claiming P0-3: drafting shared/event_schema.json (draft only, needs P0-4 sign-off)

## 2026-11-21 14:31:44 | agent-3 | P0-3 | REVIEW
**What:** Drafted shared/event_schema.json v1: visit_event, heartbeat, event_batch, event_ack, heartbeat_ack (JSON Schema 2020-12)
**Files:** `shared/event_schema.json`, `TASKS.md`
**Notes:** Field names match pod_event_t / pod_heartbeat_t exactly. JSON-only additions: species (string from label table, filled by pod-uplink; 'person' rejected), optional lat/lon/loc_source (gps|simulated) for mobile mode. Batches max 50 events from one pod. acked_seq = highest CONTIGUOUS seq held, so mid-batch losses are resent, never skipped. additionalProperties false everywhere. Heartbeat reply also returns acked_seq for cursor resync after reboot. Validated with jsonschema 4.26: examples pass; person, extra fields, seq 0, score>1, lat without loc_source, 51-event batch all rejected. Needs P0-4 sign-off.

