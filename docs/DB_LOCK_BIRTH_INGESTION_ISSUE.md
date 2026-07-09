# DB Lock / Birth Ingestion Issue — 2026-06-20

## Symptom

PumpPortal CONNECTED, PumpSwap CONNECTED, Birth Queue 0 pending — but births not persisting. Health page shows "Births: STALE (11m+ ago)".

## What's Actually Happening

Births ARE arriving at the listener (`[PUMPPORTAL] 🟢 Birth: ...`). Every write attempt immediately fails:

```
[BIRTH] ⚠ Failed to insert bonding-curve token HBAzyJaxjVKzYZGu...: database is locked
[BIRTH] ⚠ Failed to persist metadata for 8hXtso4k95uvSDEK...: database is locked
[PREDICTION] ⚠ score_single BIRTH: database is locked
[SYMBOL_FETCH] ⚠️  ...: database is locked
[RPC_METRICS] batch write failed: database is locked
[FUNDING_QUEUE] ⚠ Queue processor error: database is locked
```

## Root Cause

**Connection leak in the listener process (PID 41977): 23 open file descriptors on the same DB.**

Three processes hold the DB:
- PID 41977 — listener (23 FDs — the leak)
- PID 9886 — helius_cli_monitor (running 20h+)
- PID 53759 — gunicorn worker

The leak comes from background queue threads each opening their own SQLite connections and not releasing them:

- **FUNDING_QUEUE** — `LISTENER_CREATOR_FUNDING_QUEUE_ENABLED=0` is set, but on restart the queue recovered 1 stale running job from the previous session and that job is still running, holding a write lock
- **CREATOR_RESOLUTION_QUEUE** — 154 pending, 2 workers running, each with their own connection
- **RPC_METRICS** — batch write thread opening separate connections

These background writers compete with the birth write path. Birth writes time out waiting for the lock and are dropped (not queued — birth queue stays at 0 because the enqueue itself fails).

## Why Restart Helps Temporarily

Restart clears:
- The stale FUNDING_QUEUE job recovered from the previous session
- All leaked connections (FD count resets)
- WAL pressure accumulated from failed checkpoints

But doesn't permanently fix it — the connection leak recurs as background threads spin up again.

## Recurring Pattern

This is the same lock storm root cause documented in `docs/DB_LOCK_STORM_DIAGNOSIS.md`. The serializer (`DB_WRITE_SERIALIZE`) was fixed for the main write path, but background queue threads (FUNDING_QUEUE, CREATOR_RESOLUTION, SECOND_HOP) bypass it by opening their own connections directly.

## Current Mitigations Active

| Flag | Value | Effect |
|------|-------|--------|
| `LISTENER_CREATOR_FUNDING_QUEUE_ENABLED` | 0 | Stops new funding queue jobs |
| `LISTENER_CREATOR_ACTIVITY_ENABLED` | 0 | Stops creator activity block |
| `LISTENER_LIVE_PRICE_UPDATER_ENABLED` | 0 | Stops price updater |
| `LISTENER_PRICE_WORKER_ENABLED` | 0 | Stops price worker |
| `FLEX_UI_RECOVERY_MODE` | 1 | Gates non-GET routes in UI |

## Immediate Fix (approved, works temporarily)

```bash
supervisorctl -c config/supervisor/supervisord.conf restart watchtower_listener
```

Clears the stale job and leaked connections. Buys ~10-60 minutes of clean ingestion before contention rebuilds.

## Permanent Fix (not yet implemented)

Two options, in order of preference:

**Option A — Park CREATOR_RESOLUTION_QUEUE in listener env**
Add `LISTENER_CREATOR_RESOLUTION_QUEUE_ENABLED=0` to the listener's run environment. This is the highest-volume background writer (154 pending, 2 workers). Parking it eliminates the main source of contention. Creator resolution can be run as a separate offline script when needed.

**Option B — Fix connection leak in queue workers**
Ensure all queue worker threads acquire connections through the write serializer and release them in `finally` blocks. The `db_locking.py` serializer already exists but queue threads bypass it.

## Second Hop Queue

Queue Status shows **12,826 pending** second-hop jobs. This is a large backlog that will generate sustained write pressure once the lock clears. Consider whether second-hop processing should also be gated.

## Detection

Health page now surfaces this accurately:
- `Births: STALE (Nm ago)` in Overall Status ingestion row
- `PumpPortal: CONNECTED` — confirms it's not a connection issue
- Birth Queue: 0 pending — confirms births aren't being queued on failure (writes dropped, not retried)

The combination of CONNECTED + STALE + queue=0 is the signature of this issue.
