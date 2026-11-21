# AGENTS.md — Multi-agent framework for The Pod

You are one of four Claude agents building **The Pod** (QNX wildlife sensor on a Raspberry Pi + a Python ground station) at a hackathon. Each agent works for a different human, in parallel, on the same repo. These rules exist so you do not collide. Follow them exactly.

Full design: the "The Pod: Architecture & Hackathon Plan" doc. Task assignments: `TASKS.md`.

## 1. Know who you are

Your human tells you which agent you are (agent-1 … agent-4) at the start of a session. If they have not, **ask before doing anything**. Do not guess.

| Agent | Role | Lane |
|-------|------|------|
| agent-1 | Embedded lead | `pod/capture/`, `pod/supervisor/`, `pod/log/`, `pod/uplink/`, `pod/ipc/`, `pod/scripts/` |
| agent-2 | AI lead | `pod/infer/`, `pod/track/`, `models/` |
| agent-3 | Backend / simulation | `ground/api.py`, `ground/density.py`, `ground/simulate.py`, `ground/allocate.py`, `ground/db/`, `ground/tests/` |
| agent-4 | Frontend / demo | `ground/dashboard.py`, `ground/dashboard/`, `docs/`, `demo/` |

(`TASKS.md` is authoritative if it differs from this table.)

## 2. Start-of-session checklist

1. `git pull --rebase` to get everyone's latest work.
2. Read `TASKS.md`. Find your tasks and check their dependencies.
3. Run `python scripts/log.py show --limit 15` (or read `LOG.md`) to see what others did recently.
4. Pick the next task assigned to **you** whose dependencies are `DONE`. Set it to `DOING` in `TASKS.md`, log it, commit and push **before** starting.

## 3. The core rules

1. **Stay in your lane.** Only create or edit files in directories you own (section 1). Reading anything is fine.
2. **Never edit another agent's files**, even for a one-line fix. Use a cross-lane request (section 5).
3. **Contract files are shared and frozen.** `shared/pod_msgs.h` and `shared/event_schema.json` change only through the contract change process (section 6).
4. **Edit only your own rows in `TASKS.md`**: your status cells and the Blockers table. Never reassign tasks; that is a human decision.
5. **Log every change** (section 4). No log entry means it did not happen.
6. **Small, frequent commits.** Commit after each task step, `git pull --rebase`, then push. Do not sit on a big diff.
7. **Never force-push, rewrite history, or reset another agent's work.** If a rebase conflicts in a file you do not own, stop and ask your human.
8. **Do not install global dependencies or change shared tooling** (build files, `requirements.txt` at the repo root, CI) without a contract change. Put lane-specific dependencies in your lane (e.g. `ground/requirements.txt` for agent-3/4 coordination, `pod/<proc>/CMakeLists.txt` for agent-1/2).
9. **Honour the plan's constraints:** capture and inference are C++ on the Pod; Python only for logging, uplink and the ground station; no OpenCV on the Pod; person detections are dropped on-device and never stored; claim "bounded, measured latency", never "no delay".
10. **Respect the gates.** If a phase gate is not met, take the fallback in the plan and move on. Do not sink hours.

## 4. Logging system

Every change is logged so any agent (or human) can see "agent 1 did this, agent 2 did that".

- Per-agent files: `logs/agent-N.md`. Only agent N appends. Append-only; never edit or delete old entries.
- `LOG.md`: combined newest-first view, **generated** by `python scripts/log.py merge`. Never edit by hand.

### When to log

Log after each meaningful unit of work: a task started, a file or feature added/changed/removed, a task finished, a blocker hit, a contract proposal. Also log a measurement you took (e.g. "inference p50 = 92 ms, p99 = 140 ms on Pi 4") because teammates need those numbers.

### How to log

```
python scripts/log.py add --agent 1 --task P1-1 --status DONE \
    --summary "pod-capture writes frames into 4-slot shm ring buffer, pulses pod-infer" \
    --files pod/capture/main.cpp pod/ipc/ring.h \
    --notes "Frame header matches shared/pod_msgs.h v1. Tested with file camera."
```

| Flag | Meaning |
|------|---------|
| `--agent` | Your number, 1–4 (required) |
| `--task` | Task ID from `TASKS.md` (e.g. `P1-1`); omit for ad-hoc work |
| `--summary` | One line, what changed and why it matters (required) |
| `--files` | Files you touched |
| `--status` | `DOING`, `DONE` (default), `BLOCKED`, `REVIEW`, `CONTRACT` |
| `--notes` | Anything others need: interfaces, gotchas, numbers |

Entry format in `logs/agent-N.md`:

```
## 2026-11-21 14:05:09 | agent-1 | P1-1 | DONE
**What:** pod-capture writes frames into 4-slot shm ring buffer, pulses pod-infer
**Files:** `pod/capture/main.cpp`, `pod/ipc/ring.h`
**Notes:** Frame header matches shared/pod_msgs.h v1.
```

Rules for good entries: write for a teammate who has not seen your screen; name interfaces others depend on; keep it to a few lines.

### Commit with your log

Log first, then commit code **and** your `logs/agent-N.md` together:

```
git add <your lane files> logs/agent-N.md TASKS.md
git commit -m "agent-N P1-1: pod-capture ring buffer"
git pull --rebase && git push
```

Prefix every commit message with `agent-N`. Do **not** commit `LOG.md` from routine work (it would conflict constantly). Rebuild it locally with `python scripts/log.py merge` when you want the combined view; a human commits it occasionally. If `LOG.md` ever conflicts, regenerate it with `merge` and keep that result.

## 5. Cross-lane requests

Need something from another lane (a function, a field, a bug fix, an endpoint)?

1. Add a row to the **Blockers and handoffs** table in `TASKS.md`: your agent, the target agent, exactly what you need.
2. Log it: `--status BLOCKED --summary "need X from agent-3"`.
3. Tell your human, who relays it. Meanwhile work on something unblocked, or stub against the contract with a fake.
4. When the other agent delivers, they remove the row and log it.

Do not "just fix it" in their files.

## 6. Contract change process (`shared/`)

Contract files are what let four people work without talking constantly. Changing them silently breaks everyone.

1. Never edit `shared/*` directly in the middle of other work.
2. Log a `CONTRACT` entry describing the proposed change and why, and add it to the Blockers table addressed to **all**.
3. Wait for your human to confirm that the other three humans agreed.
4. Make the change in its own commit, touching only `shared/` and your log. Add a version bump line at the top of the file (`v1` → `v2`).
5. Every agent re-pulls and adapts. Additive changes (new optional field) are fine; renames and removals need extra care.

During Phase 0 the contracts are drafted by agent-1 (`pod_msgs.h`) and agent-3 (`event_schema.json`) and signed off by all four.

## 7. Repo layout

```
pod/                 Pi side (QNX)
  capture/ infer/ track/ log/ uplink/ supervisor/ ipc/ scripts/
ground/              laptop side (Python)
  api.py density.py simulate.py allocate.py dashboard.py db/ tests/
shared/              contract files (frozen, see section 6)
  pod_msgs.h  event_schema.json
models/              TFLite/ONNX models (agent-2)
docs/  demo/         slides, scripts, submission (agent-4)
logs/                per-agent logs (append-only)
scripts/log.py       logging helper
TASKS.md             who does what, status, blockers
LOG.md               generated combined log
AGENTS.md            this file
```

If a directory you own does not exist yet, create it. Do not create files in other lanes' directories.

## 8. When in doubt

- Unsure whether a file is yours? It is not. Ask.
- Unsure whether a change touches a contract? Treat it as if it does.
- Task is unclear or two tasks seem to overlap? Log `BLOCKED`, tell your human, and move to something else.
- Something broke after a pull and it is not in your lane? Do not fix it. Add a Blockers row and tell your human.

Keep going until your assigned tasks are `DONE` or `BLOCKED`, and always leave the log and `TASKS.md` accurate for the next agent.
