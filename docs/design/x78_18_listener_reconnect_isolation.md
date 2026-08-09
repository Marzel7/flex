# X78.18 — Listener Reconnect Isolation

Objective: break the dependency `Reconnect → Database write acquisition`
established by X78.17 as the dominant cause of the Live Ingestion rate
collapse. No architecture redesign, no Mission Control changes, no
ingestion redesign — reconnect-path isolation only.

Full dependency inventory: `docs/audits/x78_18_reconnect_dependency_audit.md`
(Phases A–C). This document covers Phases D–H: implementation, deferred
initialisation, failure behavior, and validation.

---

## Phase D/E — What Changed

Three couplings were found and isolated. All three preserve existing
behavior exactly — only *timing* and *failure isolation* changed, not
what any of this code does when it succeeds.

### 1. Seed-subscription DB read (`src/core/pumpfun_curve_listener.py`)

**Before**: `listen_pumpportal_websocket()` ran a blocking DB read
(`SELECT mint FROM token_analysis WHERE lifecycle_stage='bonding_curve'
LIMIT 200`, via `managed_db_connect`) inline, between subscribing to
`newToken`/`migration` and entering the `ws.recv()` loop — on **every**
reconnect. A stall acquiring this connection delayed first-message
receipt on every single reconnect attempt, even though this read only
affects trade-event coverage for already-active tokens, never
birth/migration flow.

**After**: extracted into `_seed_trade_subscriptions(ws,
tracked_trade_mints)`, launched via `asyncio.create_task(...)`
immediately after `subscribeNewToken`/`subscribeMigration` are sent.
The `ws.recv()` loop begins immediately, without waiting for the DB read
to complete. The seeding task keeps its original try/except (now logging
"deferred, non-fatal" instead of just "failed") — its failure mode was
already safe; what changed is that it can no longer delay message
receipt, and as an independent asyncio task, an unhandled exception in
it is caught by asyncio's default task-exception logging and can never
propagate into `listen_pumpportal_websocket()`'s own `except Exception
as e:` (which counts reconnect failures).

### 2. `usage_tracker._ensure_started()` (`src/metrics/usage_tracker.py`)

**Before**: `_ensure_started()` called `ensure_schema()` unguarded.
`record_wss()`/`record_webhook()` — called synchronously from inside the
PumpPortal message handler, for every message — call `_ensure_started()`
on every invocation (a no-op after the first successful call, via the
`_started` flag). If the process's first-ever `record_wss()` call
happened to coincide with database write-lane contention, the resulting
`CrossProcessDatabaseWriteTimeout` (or any other exception) would raise
straight out of `record_wss()`, out of the message-handling code, into
`listen_pumpportal_websocket()`'s reconnect-counting exception handler —
a genuine instance of the frozen causal chain, confirmed by direct code
read (not merely inferred).

**After**: `ensure_schema()` is wrapped in try/except inside
`_ensure_started()`. `_started` is still set to `True` before the
attempt (unchanged), so a failure does not cause retries on every
subsequent call — matching the pre-existing once-per-process intent.
Only the failure mode changed: a schema-bootstrap failure is now
swallowed (metrics schema is best-effort, as the rest of this module
already treats it — `_flush()`'s own write loop already swallows all
exceptions at line 91-92 of the original file).

### 3. `walkback_queue.ensure_schema()` startup call (`main()` in
`src/core/pumpfun_curve_listener.py`)

**Before**: `main()` called `database_write_service.submit(...)`
directly, before `listen()`/`asyncio.gather(...)` — confirmed by reading
`database_write_service.submit()` (`src/core/database_write_service.py`
lines 478-516) that this call **blocks the calling thread**, waiting on
a `threading.Event` until the submitted write actually executes and
completes on the service's worker thread. Under write-lane contention,
this delayed the entire listener's readiness — including every
FATAL-triggered restart — before PumpPortal connection could even begin.

**After**: the same logic (unchanged: register database, submit DDL,
log outcome) now runs inside a daemon thread
(`walkback-schema-startup`), started via `threading.Thread(...).start()`
without waiting for it. `main()` proceeds immediately to `listen()`. The
DDL itself is idempotent (`CREATE TABLE IF NOT EXISTS`-style), so running
it once per process start in the background carries the same schema
guarantee as before — only its blocking relationship to listener startup
changed.

---

## Phase F — Failure Behaviour

For all three couplings above: a failure in the deferred/isolated work
now results in — per the charter's required outcome —

- Listener remains connected (or, for the walkback case, listener
  startup proceeds to `listen()` regardless of whether the background
  schema thread has finished).
- Birth flow continues uninterrupted.
- The deferred work can be retried independently of the connection
  lifecycle (the seed read naturally retries on the next reconnect; the
  walkback schema write logs a "background, non-fatal" failure but
  nothing in the reconnect loop observes or depends on it).
- No reconnect failure is counted, and no supervisor restart is
  triggered, by any of these three dependencies failing.

None of the three changes altered what happens when the underlying
operation **succeeds** — same DB query, same DDL, same subscription
messages sent to PumpPortal, same log content (only "(background)" /
"(deferred, non-fatal)" annotations added to distinguish the new timing
in the logs for future debugging).

---

## Phase G — Regression

`tests/test_x78_18_reconnect_isolation.py` (new, 4 tests):

- `test_ensure_started_swallows_ensure_schema_failure` — a raised
  exception from `ensure_schema()` does not propagate out of
  `_ensure_started()`.
- `test_ensure_started_only_attempts_schema_once` — confirms the
  once-per-process guarantee is unchanged even when the single attempt
  fails.
- `test_record_wss_does_not_raise_when_schema_bootstrap_fails` —
  end-to-end through the exact call the reconnect loop's message handler
  makes.
- `test_seed_trade_subscriptions_failure_does_not_propagate` — the
  extracted `_seed_trade_subscriptions()` method swallows a simulated DB
  failure and returns normally rather than raising.

Full related suite, run together: `test_x78_18_reconnect_isolation.py`
+ `test_usage_tracker_connection_lifecycle.py` +
`test_x78_10_listener_ensure_db_retry.py` — **13/13 passing**.
`test_pumpswap_listener.py`'s 4 failures (missing `analyze_creator_wallet`
module) are pre-existing and unrelated — confirmed identical on the
unmodified tree via `git stash`.

Both modified files verified to still parse (`ast.parse`).

---

## Live Validation

**Not performed as part of this deliverable.** The charter's own "Live
Validation" section calls for a narrow production deploy and measurement
of reconnect latency, restart frequency, birth receipt rate, listener
uptime, and Mission Control ingestion status over time — this requires
actually running the changed listener process against live PumpPortal
traffic and real database contention for a meaningful observation
window, which was not done in this session. This is an explicit gap,
not a silent omission: per the charter's own git-workflow section
("Push after: Regression passes. Live validation passes. Birth flow
improves."), this change should be committed locally now, deployed
narrowly, and observed before being considered fully validated or
pushed.

---

## Acceptance Gates — Status

| Gate | Status |
|---|---|
| Reconnect path contains no optional database writes | Met — the only DB operation remaining inline in the connect block is the two `ws.send()` subscribe calls (network, not DB); the seed read (a DB read, not write) is now off the hot path entirely |
| Reconnect no longer depends on successful `usage_tracker.ensure_schema()` execution | Met — failure is now swallowed inside `_ensure_started()` |
| Listener survives transient database contention | Implemented, not yet live-validated |
| Birth flow resumes immediately after reconnect | Implemented (message loop no longer waits on the seed read) — not yet live-validated |
| No regression | Met — 13/13 targeted tests pass, pre-existing unrelated failures confirmed unrelated |
| No production behaviour changes | Met by design — same queries, same DDL, same subscribe messages; only timing/failure-isolation changed |

---

## Explicitly Not Done (Out of Scope, Per Charter)

- PumpPortal protocol, birth parsing, listener filtering, Mission
  Control, OIP, and Runtime were not modified.
- PERIOD A (`intelligence_refresh.py:55 in _db`) was not fixed — it
  remains a separate, already-flagged issue (see X78.17's
  recommendation section); this milestone only ensures the *reconnect
  path itself* can no longer be taken down by contention like it, not
  that contention stops occurring.
- Database and RPC were not optimised.
- No architecture redesign — the write-lane/lease mechanism itself
  (`db_locking.py`, `database_write_service.py`) is unchanged.
