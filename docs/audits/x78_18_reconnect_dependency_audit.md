# X78.18 — Reconnect Dependency Audit (Phases A–C)

Frozen from X78.17: PumpPortal reconnect can fail because of database
write-lane contention. This audit enumerates every operation on the
path from process start (and from each reconnect) to the first birth
message, to identify exactly what must be removed or deferred.

---

## Phase A — Reconnect Audit

Path: process start → `listen()` → `listen_pumpportal_websocket()` →
first successfully received birth.

`src/core/pumpfun_curve_listener.py`

| Step | Location | Required before first message? | Type |
|---|---|---|---|
| Import `rpc_metrics_recorder`, call `initialize_recorder(...)` | module level, L280-281 | No — fires at import regardless of connection state | CPU/DB (unconfirmed depth) |
| Import `usage_tracker.record_wss/record_webhook` | module level, L291 | No — import only, no I/O yet | none |
| Import `db_locking` (`db_connect`, `managed_db_connect`) | module level, L16 | Yes (needed for the seed-read) but side effects (reaper/watchdog threads, `sqlite3.connect` monkeypatch) run at import, not connect time | CPU/thread spawn |
| `walkback_queue.ensure_schema()` via `database_write_service.submit(...)` | `main()`, L11987-11997 | **No** — unconditional startup-only schema write against `OPS_DB_PATH`, runs once per process start, before `listen()` | **DB write** |
| `CreatorRepository.ensure_schema()` | `listen()`, L11602-11613 | No — **disabled in production** (`LISTENER_CREATOR_ACTIVITY_ENABLED=0` in `run_listener.sh`) | DB write (dead code path in prod) |
| `asyncio.gather(listen_pumpswap_websocket(), listen_pumpportal_websocket(), drain_webhook_birth_queue(), drain_migration_persist_queue(), _loop_lag_watchdog(), _db_fd_watchdog())` | `listen()`, L11661-11677 | Only `listen_pumpportal_websocket()` itself is required; the other 5 coroutines start concurrently and are NOT ordered before it, but they share process resources (DB connections, event loop) that can stall it | mixed |
| `websockets.connect(PUMPPORTAL_WS, ...)` | `listen_pumpportal_websocket()`, L10794-10800 | **Yes — the actual required operation** | Network |
| `ws.send(subscribeNewToken)`, `ws.send(subscribeMigration)` | L10806-10807 | **Yes — required** | Network |
| Seed-subscription DB read (`SELECT mint FROM token_analysis WHERE lifecycle_stage='bonding_curve' LIMIT 200`) via `managed_db_connect(DB_PATH, timeout=5)` | L10822-10844, inside `_seed_read()`, run via `asyncio.to_thread` | **No** — wrapped in its own try/except that only logs a warning on failure; does not block subscription to `newToken`/`migration`, only delays `subscribeTokenTrade` seeding | **DB read**, on the hot path, on **every reconnect** |
| `ws.recv()` loop begins | L10847+ | **Yes — this is "first message received"** | Network |
| First `record_wss(...)` call (inside message loop, after first message classified) | L10863 area | No — happens AFTER first message is already received, not before | DB write (lazy, once per process) |

**Conclusion (Phase A):** exactly two required operations gate the first
message: `websockets.connect` and the two `ws.send` subscribe calls.
Everything else on this list — including the one DB read that currently
sits inline in the hot path (seed-subscription) and the one DB write
that currently sits inline in the startup path (walkback schema) — is
deferrable or optional relative to receiving events.

---

## Phase B — Database Dependency Inventory

| Dependency | Purpose | Mandatory? | Idempotent? | Moveable? | Safe to defer? |
|---|---|---|---|---|---|
| Seed-subscription read (`SELECT mint FROM token_analysis ...`) | Pre-populate `subscribeTokenTrade` for already-active bonding-curve tokens so trade events for them aren't missed while the process was down | No — only affects trade-event coverage for pre-existing tokens, not new-token/migration events | Yes (pure SELECT, re-running it is harmless) | Yes — can run after `newToken`/`migration` subscriptions are confirmed, off the connect hot path | **Yes** |
| `walkback_queue.ensure_schema()` (via `database_write_service`) | Ensure `OPS_DB_PATH` schema exists for the walkback queue | Only once per deployment/schema version, not once per process start | Yes (`CREATE TABLE IF NOT EXISTS`-style DDL) | Yes — can run once at deploy/init time, or deferred to first actual walkback-queue use | **Yes** |
| `usage_tracker.ensure_schema()` (via lazy `_ensure_started()` inside `record_wss`) | Ensure `wss_metrics`/`webhook_metrics` tables exist | Only once per process lifetime (already lazy) | Yes | Already deferred to first `record_wss` call, which itself is already after first message — **not on the pre-message path today** | N/A — already correctly placed; see Phase C |
| `CreatorRepository.ensure_schema()` | Creator-activity tracking schema | Disabled in production | Yes | N/A while disabled | N/A |
| `record_wss(...)` per-message metrics write | Usage/WSS metrics accounting | No — purely observational | Yes | Already off the pre-first-message path; still inline in the per-message hot path post-connect, out of scope here (per-message, not reconnect-time) | Out of scope for X78.18 (not a reconnect-time dependency) |

---

## Phase C — `usage_tracker.ensure_schema()`

**Why it executes where it does:** `ensure_schema()` is called from
`_ensure_started()` (`usage_tracker.py` L94-101), which is itself called
unconditionally (but internally guarded by a `_started` module flag) at
the top of every `record_wss()`/`record_webhook()` call. The listener's
first `record_wss()` call happens inside the message-processing loop,
**after** a message has already been received and classified (L10863) —
not during `websockets.connect()`, not during subscribe, and not on
every reconnect. After the first call in a process's lifetime, `_started`
is `True` and `ensure_schema()` never executes again for that process.

**Determination:** schema verification is required **once per process
start** (not every reconnect, not every deployment cycle beyond that —
SQLite `CREATE TABLE IF NOT EXISTS` DDL is safe to re-run but wasteful
to run more than once per process). It is **already correctly scoped**
to "once per process, lazily" by the existing `_started` guard — X78.17's
frozen causal chain names `usage_tracker.ensure_schema()` specifically,
but this audit finds its *current* callsite is not actually on the
reconnect hot path today; it fires once, on the first message, in the
success path, not in the retry loop.

**However**, this only means `ensure_schema()` itself is not the
per-reconnect repeat offender. The frozen chain remains valid as a
description of a *possible* execution: if the very first `record_wss()`
call for a process happens to coincide with database write-lane
contention (e.g., immediately after a fresh process start following a
FATAL restart, when other processes are mid-write), that one-time
`ensure_schema()` call can still block for up to 60s on the in-process
write lane (`_DB_WRITE_LOCK.acquire(timeout=60)` in `db_locking.py`) or
raise `CrossProcessDatabaseWriteTimeout` from the cross-process lease —
and because it runs from inside the message-handling code path (not a
background thread), an unhandled exception there would propagate up
through the same `try/except` that wraps the whole connect block,
incrementing `_consecutive_failures` exactly as X78.17 described.

**Safe lifecycle ownership:** `ensure_schema()` should run exactly once,
off the hot path, with failures isolated so they cannot raise into the
reconnect loop's exception handler. It does not need "once per
deployment" tracking (no version field in its DDL) — "once per process
start, non-blocking with respect to message flow" is sufficient and
matches its already-lazy intent; the fix is isolating its *failure mode*
and its *timing relative to first message*, not its frequency.

---

## Summary — What Actually Sits on the Reconnect Hot Path Today

Contrary to a literal reading of the frozen chain (which names
`usage_tracker.ensure_schema()` as running during reconnect), this audit
finds the two real hot-path dependencies are:

1. **The seed-subscription DB read**, which runs on **every** successful
   reconnect (Phase A/B) and is wrapped in try/except today — but that
   try/except only guards its own body; if `managed_db_connect` itself
   blocks for up to 60s acquiring the in-process write lane before the
   SELECT can even run (SQLite connection opening can contend with the
   write lane depending on connection pooling/locking behavior), that
   delay still happens synchronously inside the connect block, before
   `ws.recv()` begins — a stall here, even one that's eventually caught,
   still delays first-message receipt and can push `_mins_since_connect`
   toward the 3-minute FATAL threshold across repeated reconnects.
2. **`usage_tracker.ensure_schema()`'s one-time first call**, which is
   already off the very-first-message path but still executes from
   inside message-handling code with no isolation from the reconnect
   loop's own exception handling, on whichever reconnect happens to be
   active when the first message of the process arrives.
3. **`walkback_queue.ensure_schema()`**, a blocking startup-time DB write
   that runs before `listen()`'s `asyncio.gather` even starts — not a
   reconnect-time dependency, but a process-start dependency that delays
   the whole listener's readiness on every restart, including every
   FATAL-triggered restart.

Phase D/E implementation targets all three: move the seed-subscription
read to run only after `ws.recv()` begins receiving messages (non-blocking,
best-effort, failure-isolated); isolate `usage_tracker.ensure_schema()`'s
one-time call so a failure there cannot raise into the reconnect
exception handler; and move `walkback_queue.ensure_schema()`'s startup
write off the pre-`gather()` critical path so a startup-time write stall
doesn't delay every restart's time-to-reconnect.
