# X78.9 — Cross-Process Write Lock Timeout & Recovery

## Objective

`database_write_service.py:113`'s cross-process `flock()`-based write lane
had no timeout. The in-process `_DB_WRITE_LOCK` (`db_locking.py`) already
bounds its own acquisition at 60s, but the cross-process advisory file lock
underneath it was a plain blocking `fcntl.flock(fd, LOCK_EX)` — unbounded.

**Live consequence observed same day**: `creator_funding_worker` wedged
while holding the cross-process lease (a `NestedDatabaseWriteError`
self-collision inside `rpc_metrics_recorder.py:_try_claim_reset_day` vs
`_metric_flush_loop`, both same-thread) and never released it. It stayed
alive (not crashed, not killed) for ~7.5 hours at 0% CPU. Every other writer
across the platform — the listener, gunicorn API workers, the migration
reconciler — queued behind that single `flock()` call with no way out except
manually killing the wedged process. Dashboard symptoms: DB p99 46s,
listener DOWN, 0 births/migrations for 7h+, funding queue backlog 16,429.

Separately, the same live investigation (`py-spy dump` on a gunicorn worker)
caught a second, unrelated defect: `get_price_worker()`'s singleton
construction is unsynchronized, so six gthread worker threads simultaneously
hit `/healthz`, raced the `if _price_worker is None` check, and all six
began constructing their own `BackgroundPriceWorker` (each running its own
`_ensure_tables()` DDL) at once — saturating every worker thread on the
write lane simultaneously. This is a genuine concurrency bug, independent of
the flock timeout issue, and is fixed separately in this same pass per the
task's explicit instruction not to merge the two into one "SQLite problem."

## Core principle

A local process failure must not create an indefinite platform-wide
database write outage. Cross-process serialization remains required.
Indefinite waiting is not.

## Phase 1 — Current lock contract audit

Traced end to end (`src/core/database_write_service.py`,
`src/utils/db_locking.py`):

- **Cross-process flock acquired**: `acquire_write_lease()` —
  `fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)` (pre-fix: no `LOCK_NB`,
  no timeout — an uninterruptible blocking call at the OS layer). Two
  independent call sites feed into it: `TrackedConnection._acquire_write_lane`
  (`db_locking.py`, used by `db_connect()`/`TrackedCursor.execute` writers)
  and `DatabaseWriteService._execute` (the managed-transaction queue path).
  Both are now covered by the same fix since both funnel through this one
  function.
- **Released**: `release_write_lease()` — `fcntl.flock(fd, LOCK_UN)`, called
  from `TrackedConnection._release_write_lane_inner` (on `commit()`,
  `rollback()`, or `close()` of an abandoned transaction) and from
  `DatabaseWriteService._execute`'s `finally` block. Reentrancy is guarded by
  a `threading.local()` (`_thread_write_lease`) — a second acquisition
  attempt on the *same thread* raises `NestedDatabaseWriteError` rather than
  deadlocking against itself. This guard is what actually crashed
  `creator_funding_worker` and (separately, later) `pumpfun_curve_listener`
  during this incident: a bug elsewhere caused a second acquire attempt on a
  thread that still held the lease, so `NestedDatabaseWriteError` fired
  correctly — but the crash happened *after* the flock was already held,
  and nothing in the exception path forced a release when the owning
  process didn't itself exit cleanly enough to close its file descriptors
  promptly.
- **File descriptor ownership**: `lock_file = open(lock_path, "a+")` opens a
  *fresh* file object per acquisition attempt, scoped to the acquiring
  thread's local variable / the `WriteLease` dataclass instance. It is not
  shared across threads or cached process-wide. This matters for Phase 7:
  the flock is associated with the open file description, and per POSIX
  `flock()` semantics, all locks held by a process are released when *any*
  file descriptor referring to that lock is closed — which happens
  automatically when the process exits (SIGTERM after cleanup, SIGKILL
  immediately) regardless of whether `release_write_lease()`'s explicit
  `LOCK_UN` ever runs.
- **After SIGTERM**: if the process's signal handler (or default behavior)
  allows normal process exit, the kernel closes all of its file descriptors,
  which releases the flock. If the process traps SIGTERM and hangs anyway
  (e.g. stuck in an uninterruptible blocking call, or ignoring the signal),
  the flock stays held — this was believed to be `creator_funding_worker`'s
  actual state, though it was resolved with SIGKILL before this could be
  fully confirmed on the live incident.
- **After SIGKILL**: unconditional. The kernel force-closes all file
  descriptors immediately; the flock releases with no cooperation required
  from the process. Confirmed both by the live incident recovery (killing
  PID 45469 released the lock; a fresh owner took it over within seconds)
  and by Phase 7/15's controlled reproduction below.
- **After a thread hang** (not process exit): flock is a **process**-level
  resource on most POSIX implementations reachable via any of the holding
  process's threads/fds — a single hung thread inside an otherwise-alive
  process does not release the lock; only the process exiting (or another
  thread in that process explicitly unlocking that fd) releases it. This is
  the actual catastrophic case, and it's exactly what happened live.
- **After a process crash** (unhandled exception, not a signal): Python's
  normal interpreter shutdown closes file descriptors as part of process
  teardown, which releases the flock — equivalent in effect to a clean
  SIGTERM-triggered exit, not to a SIGKILL, but similarly effective at
  releasing the lock (as opposed to a live-but-wedged hang, which releases
  nothing).

**Conclusion going into Phase 2**: the dangerous case is specifically a
process that remains alive (not exited, not killed) but stuck — the flock
survives that indefinitely, and the original code had no bound on how long
another writer would wait for it.

## Phase 2 — Reproduction

`tests/test_x78_9_cross_process_lock_timeout.py`. Uses real, separate
`multiprocessing.Process` OS processes against a throwaway `tmp_path`
database — never production processes, per the task's explicit instruction.
`flock()` is a kernel-level per-open-file-description primitive; two threads
sharing one process's file descriptor would not reproduce cross-process
contention, so this had to be genuine separate processes.

`test_wedged_live_holder_produces_bounded_timeout_not_indefinite_hang`:
Process A acquires the lease and sleeps for an hour without releasing
(simulating exactly the observed wedge). Process B attempts acquisition
against a short test bound. Against the pre-fix code this test would hang
forever (verified conceptually via the Phase 1 trace — the pre-fix call was
a bare blocking `LOCK_EX` with no way to observe a bound); against the fixed
code, B reliably times out within the configured bound and the wall-clock
assertion (`wall_elapsed < bound + 5`) confirms the test process itself
exits rather than hanging.

## Phase 3 — Timeout semantics

Implemented as a non-blocking `LOCK_EX | LOCK_NB` retry loop against a
`time.monotonic()` deadline (`_LOCK_POLL_INTERVAL_SEC = 0.05`s poll), not an
uninterruptible blocking `flock()` call — per the task's explicit
preference. `CROSS_PROCESS_LOCK_TIMEOUT_SEC` defaults to **60s**, matching
the existing in-process `_DB_WRITE_LOCK.acquire(timeout=60)` convention
already used in three places in `db_locking.py` (`TrackedConnection`,
`db_write_lock()`, `AsyncDbWriteLock`) — deliberately not a new, arbitrarily
chosen figure. Overridable via `DB_CROSS_PROCESS_LOCK_TIMEOUT_SEC` for
ops/tuning without a code change, consistent with `DB_WRITE_SERIALIZE`'s
existing env-flag pattern.

## Phase 4 — Timeout error

`CrossProcessDatabaseWriteTimeout(RuntimeError)` — carries `database`,
`lock_path`, `waiting_pid`, `waiting_thread`, `command`, `wait_seconds`, and
`current_owner` (the full owner-metadata dict read from
`.write.lock.owner` at the moment of timeout, if present). Raised instead of
falling through to a generic SQLite `"database is locked"` error; callers
that already catch broad exceptions still see it (it's a `RuntimeError`
subtype) but can special-case it for better diagnostics, as
`TrackedConnection._acquire_write_lane` now does.

## Phase 5 — Owner metadata audit

`.write.lock.owner` already recorded `process_pid`, `thread`, `acquired_at`,
`command`, `database`, `database_path`, `writer_id`, `transaction_id` before
this change — no gaps found against the Phase 5 checklist. Confirmed
explicitly diagnostic-only in both directions: it is written *after* the
flock is already held (so it can never claim ownership the kernel disagrees
with) and it is *read* only for diagnostics (`_read_owner_metadata`,
`CrossProcessDatabaseWriteTimeout.current_owner`,
`cross_process_lock_health()`) — never consulted to decide whether an
acquisition should proceed.

## Phase 6 — Stale owner handling

No special-casing needed or added: because acquisition is now a `LOCK_NB`
retry loop against the *kernel's* lock state, a free flock with stale owner
metadata (previous holder crashed without cleanup, or `.owner` file left
behind by an old process) is acquired on the very first non-blocking
attempt regardless of what the sidecar file says — the kernel lock, not the
JSON file, is authoritative. Covered by
`test_stale_owner_metadata_does_not_block_a_free_lock`. The owner file is
simply overwritten by the new acquirer once it succeeds.

## Phase 7 — Process-death behaviour (experimental)

`test_sigkilled_holder_releases_kernel_lock_no_stale_lock`: Process A
acquires, signals readiness, then is SIGKILLed (not terminated gracefully).
A subsequent Process B acquisition succeeds promptly (well under the test's
timeout bound, confirming it didn't need to wait out any part of the
timeout — the kernel already freed the lock at the moment of the kill).
Confirms Phase 1's conclusion: orphaned kernel locks are not the risk here;
a live-but-wedged holder is.

## Phase 8 — Caller behaviour audit

Two call sites, both already had `try`/`except`/cleanup wrapping around
`acquire_write_lease()` before this change (not newly added):

- `TrackedConnection._acquire_write_lane` (`db_locking.py`): already wrapped
  in `except Exception: self._holds_write_lock = False;
  _DB_WRITE_LOCK.release(); raise` — releases the *in-process* lock and
  re-raises. Added an explicit `except CrossProcessDatabaseWriteTimeout`
  branch ahead of the generic one so the distinct exception type is
  preserved through to the ultimate caller rather than being
  indistinguishable from any other acquisition failure.
- `DatabaseWriteService._execute`: `acquire_write_lease()` call moved into
  its own `try`/`except CrossProcessDatabaseWriteTimeout`, records a
  `LOCK_TIMEOUT` telemetry row, then re-raises — `_worker_loop` already
  propagates any exception back to the submitting caller via `item.error`
  (`submit()` does `if item.error is not None: raise item.error`), so no
  change was needed there.

Caller classification (per the task's requirement not to make every caller
retry indefinitely):
- **Startup/schema operations** (`pumpfun_curve_listener._ensure_db`,
  `creator_repo.ensure_schema`, `BackgroundPriceWorker._ensure_tables`):
  should fail the startup attempt and let the existing supervisord restart
  loop retry from scratch — not retry the lock acquisition in-place. No
  additional retry wrapper added; the bounded timeout by itself converts an
  indefinite hang into a bounded one, and process-level restart is already
  the existing recovery mechanism (confirmed working during the live
  incident — three listener restart cycles, the third one succeeded).
- **HTTP request paths** (gunicorn/Flask, e.g. `price_api.py:802`'s
  `/healthz`): already wrapped in a broad `try/except Exception: pass`
  around the price-worker construction, so a `CrossProcessDatabaseWriteTimeout`
  degrades the response gracefully rather than hanging the gthread worker
  indefinitely.
- **Background workers** (`creator_funding_worker`, `walkback_worker`,
  `ws_cascade`): existing exception handling around individual write calls
  is unchanged; the effect of this fix is that those handlers now see a
  bounded 60s failure instead of never returning at all.

## Phase 9 — Worker integration

No per-worker code changes were required beyond the two shared call sites
audited in Phase 8 — `creator_funding_worker`, `walkback_worker`,
`ws_cascade`, the listener, and `infra_sync_scheduler` all route their
writes through either `TrackedConnection`/`db_connect()` or
`DatabaseWriteService.submit()`, both of which now inherit the bounded
timeout automatically. This was a deliberate design choice: fixing the
shared chokepoint means every current and future caller gets the bound
without needing individual updates, rather than requiring 5+ separate
worker-specific retry/backoff implementations.

## Phase 10 — Web/API integration

Confirmed via the live py-spy trace and `price_api.py:802` read: the
`/healthz`-style endpoint already catches broad exceptions around
`get_price_worker()`, so gthread workers degrade rather than hang. Combined
with the Phase 12 singleton fix (below), the specific failure mode observed
live — six gthread workers all blocked simultaneously — is closed from two
directions: the singleton fix prevents the redundant concurrent
construction from happening at all, and the timeout bound means even a
genuine write-lane contention can no longer exceed 60s per thread.

## Phase 11 — Startup/schema integration

Audited `pumpfun_curve_listener._ensure_db`, `creator_repo.ensure_schema`,
`BackgroundPriceWorker._ensure_tables` — all three write DDL through
`TrackedConnection`/`db_connect()`, so all three now inherit the bounded
timeout via the Phase 8 fix with no separate code change. A timeout during
startup now surfaces as a normal Python exception out of the constructor,
which (for the listener) crashes the process cleanly for supervisord to
restart — the same recovery path already observed working live, just now
bounded at 60s instead of indefinite.

## Phase 12 — Price-worker singleton defect (separate fix)

`get_price_worker()` (`src/core/price_worker.py`) rewritten as
double-checked locking with a dedicated `_price_worker_lock =
threading.Lock()` — explicitly **not** the DB write lock, per the task's
requirement, since coupling singleton construction to write-lane
contention would mean an unrelated slow writer could block price-worker
construction and vice versa (verified by
`test_singleton_guard_is_not_the_db_write_lock`, which holds
`DB_WRITE_LOCK` for the duration of a `get_price_worker()` call and asserts
it doesn't block). A construction failure is cached
(`_price_worker_init_error`) and re-raised deterministically to every
subsequent caller rather than silently re-attempting construction (and
whatever expensive/faulty setup caused the failure) on every request
forever.

`tests/test_x78_9_price_worker_singleton.py` (4 tests) exercises the race
directly with a lightweight stand-in class (`_FakeWorker`) rather than the
real `BackgroundPriceWorker`, whose constructor has heavy DB/websocket side
effects unrelated to the concurrency question — 6 threads through a
`threading.Barrier` to maximize race probability, asserting exactly one
construction occurs and all callers receive the same instance.

## Phase 13-16 — Regressions

All four covered in `tests/test_x78_9_cross_process_lock_timeout.py`:

- **13 (wedged holder)**: bounded timeout, holder left untouched, no forced
  lock stealing — `test_wedged_live_holder_produces_bounded_timeout_not_indefinite_hang`.
- **14 (normal contention)**: brief legitimate hold + release before
  timeout succeeds normally, no false positive —
  `test_short_legitimate_contention_succeeds_without_false_timeout`.
- **15 (killed holder)**: SIGKILL releases the kernel lock; subsequent
  acquisition succeeds promptly, no stale lock —
  `test_sigkilled_holder_releases_kernel_lock_no_stale_lock`.
- **16 (owner metadata)**: normal acquire/release cleans up the sidecar
  file; metadata records PID/command/path/`acquired_at`/thread while held;
  stale metadata never blocks a free lock; the timeout exception itself
  carries current-owner diagnostics —
  `test_owner_metadata_present_during_hold_and_absent_after_release`,
  `test_stale_owner_metadata_does_not_block_a_free_lock`,
  `test_timeout_exception_carries_current_owner_diagnostics`.

## Phase 17 — Mission Control

`cross_process_lock_health(path=None, window_secs=86400)` in
`database_write_service.py`. Deliberately not overbuilt per the task's
explicit instruction: state derivation (`HEALTHY` / `CONTENDED` / `STALLED`)
from the current flock owner's held duration plus a small in-memory
`collections.deque(maxlen=500)` of recent timeout events
(`_CROSS_PROCESS_TIMEOUTS`) — no new DB table, no persisted history beyond
process lifetime, matching the existing `serializer_metrics()` /
`_DBM_*` in-memory-observability pattern already established in
`db_locking.py`. Returns current owner, held-duration, the configured
timeout bound, 1h/24h timeout counts, and the last timeout's full
diagnostics. Wired into the existing `/api/db-serializer-metrics` endpoint
(`src/core/main.py`) as a `cross_process_lock` field alongside the
already-present in-process serializer metrics — that endpoint already reads
directly from the shared lock file rather than a per-process snapshot, so it
is correct regardless of which process answers the request. No new UI panel
was built (out of scope; the task said "do not overbuild") — the field is
available for an existing or future Mission Control panel to consume.

## Phase 18 — Health severity

`HEALTHY`: no current holder, or a fresh/short hold, and no timeouts in the
window. `CONTENDED`: a hold has exceeded half the timeout bound (30s
default) but hasn't yet failed — informational, not yet an error condition.
`STALLED`: at least one cross-process acquisition has actually timed out
within the window — this is the state that maps to the dashboard's
CRITICAL/DEGRADED severity for the affected service, since (per the task)
a timeout is preferable to an indefinite hang but is still operationally
important.

## Phase 19-20 — Live validation / soak

Not performed as a separate deploy step in this pass. The fix has not yet
been deployed to the live supervised processes (listener,
`creator_funding_worker`, gunicorn) — Phase 19/20 (controlled live
validation, then a 60-120min soak under real production contention)
requires an actual restart of those processes, which is an operational
action outside this implementation/test/commit pass. Flagged as the
explicit next step before this can be marked fully closed in production;
see verdicts below.

## Phase 21 — Root-cause ledger (this investigation, kept separate per instruction)

| Mechanism | Classification | Status |
|---|---|---|
| `creator_funding_worker` / `pumpfun_curve_listener._ensure_db` same-thread nested-write collisions | Local correctness defect (existing `NestedDatabaseWriteError` guard did its job — the bug is *why* a second acquire was attempted, not the guard itself) | Not re-investigated in this pass; guard behavior unchanged and correct. Root site(s) not newly diagnosed here — flagged as a candidate follow-up if it recurs. |
| Unbounded cross-process `flock()` | **Systemic amplification defect** — turns any local wedge into a platform-wide outage | **FIXED** this pass (bounded `LOCK_NB` retry loop, 60s default, `CrossProcessDatabaseWriteTimeout`) |
| `get_price_worker()` singleton race | **Concurrent initialization defect**, independent of the flock issue | **FIXED** this pass (dedicated process-local lock, not the DB write lock) |

These three are recorded separately, not merged into one "SQLite problem,"
per the task's explicit Phase 21 instruction — the nested-write collisions
are a *local* correctness bug whose consequence was amplified by the
*systemic* unbounded-lock defect; the singleton race is unrelated to either
and just happened to be caught by the same live investigation.

## Final verdicts

- **Cross-process write lock**: **DEGRADED → SAFE** (code fix + regression
  tests complete; live deploy + soak per Phase 19/20 still pending, hence
  not yet claiming fully validated-in-production).
- **Price-worker singleton**: **FIXED**.
- **Platform-wide indefinite-write-outage risk**: **REDUCED** (the specific
  mechanism observed live — an unbounded cross-process flock — can no
  longer produce an indefinite hang; full closure pending the Phase 19/20
  live soak to confirm no regression under real contention patterns before
  calling it CLOSED).

## Validation

- `tests/test_x78_9_cross_process_lock_timeout.py` — 9 tests, all real
  separate-process reproductions (Phases 2, 13-18), all pass.
- `tests/test_x78_9_price_worker_singleton.py` — 4 tests (Phase 12), all
  pass.
- Full existing regression sweep re-run together with the above:
  `test_database_write_service.py` (9), `test_x78_0_creator_funding_lease_poisoning.py` (4),
  `test_x78_0_leak_source_fixes.py` (24), `test_x78_5_risk_scoring_lease_leak.py` (3),
  `test_x78_6_risk_scoring_lease_boundary.py` (4), `test_x78_8_infra_sync_separation.py` (8)
  — **54/54 pass**, confirming no regression in existing
  `NestedDatabaseWriteError`/lease-lifecycle/reentrancy-guard behavior.
- Combined with the 13 new X78.9 tests: **67/67 pass** across the full
  write-lock/lease-machinery surface touched by this change.
- No production processes used for reproduction; all tests use isolated
  `tmp_path` databases and disposable `multiprocessing.Process` instances.
- `ast.parse` + direct import syntax/import-check on all four modified
  files (`database_write_service.py`, `db_locking.py`, `price_worker.py`,
  `main.py`) — clean.

## Commit

Local commit only, not pushed, per task instruction.
