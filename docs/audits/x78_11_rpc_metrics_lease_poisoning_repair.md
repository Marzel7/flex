# X78.11 — RPC Metrics Recorder Lease-Poisoning Repair

## Objective

X78.10 root-caused and reproduced twice, but deliberately did not fix, a
permanent write-lease poisoning defect in
`src/metrics/rpc_metrics_recorder.py`. X78.11 fixes that defect and any
sibling functions sharing its exact lifecycle shape, then re-validates
`creator_resolution_worker` in production to confirm sustained, repeated
progress across multiple RPC-metrics-flush cycles — the specific condition
that poisoned both prior reproductions.

## Core invariant

Every connection opened by RPC metrics terminates through exactly one safe
lifecycle: success → commit → close, or failure → (rollback where
semantically required) → close, on every code path, with no exception
escaping the function while a tracked connection remains open. In
particular, `CrossProcessDatabaseWriteTimeout` must never leave
`_thread_write_lease.owner` set on the calling thread afterward.

## Part A — Freeze the reproduction (Phase 1-2)

**Phase 1**: `creator_resolution_worker` PID `82909`, uptime confirmed
still running at the start of this milestone. Frozen state: 1 successful
cycle, 1,670 error lines since restart, last heartbeat stuck at `cycles: 1`
(`wt_worker_heartbeat` row timestamp `1786188209`, unchanged for over an
hour — every heartbeat write since has failed). `creator_resolution_queue`
pending: 1,818. Kernel `.write.lock.owner` confirmed **free** at freeze
time — explicitly distinguishing the poisoned thread-local state from live
kernel flock ownership, per X78.10's own established distinction.

**Phase 2**: Exact initiating transaction captured precisely from the log:
`_try_claim_reset_day` acquired the write lease on `MainThread` at
`acquired_at: 1786188298.6165671`. 55.4 seconds later, the
`rpc-metrics-flusher` thread (same process, PID `82909`) attempted its own
acquisition via `_metric_flush_loop`, timed out at exactly `wait_seconds:
60.0` waiting on that same still-held `MainThread` lease, and logged
`CrossProcessDatabaseWriteTimeout` with `current_owner.process_pid ==
waiting_pid == 82909` — the process waiting on itself. The very next write
attempt on `MainThread` (`creator_resolution_queue.py:51 in connect`)
immediately self-collided as `NestedDatabaseWriteError` with
`outer_command=rpc_metrics_recorder.py:427 in _try_claim_reset_day`,
confirming `_try_claim_reset_day` never released its lease.

## Part B — Connection census (Phase 4-6)

Full census of every connection-owning function in
`src/metrics/rpc_metrics_recorder.py` (12 functions/methods total):

| Function | Line | Write SQL | Cleanup (pre-fix) | Classification |
|---|---|---|---|---|
| `_rpc_metrics_schema_ready` | 151 | No | `finally: close` | SAFE |
| `_ensure_rpc_metrics_table` | 192 | Yes (CREATE/ALTER) | success-path only | **SAME_DEFECT** |
| `_metric_flush_loop` | 301 | Yes (INSERT) | `try/finally: close` | SAFE |
| `_set_state` | 380 | Yes (INSERT/UPDATE) | success-path only | **SAME_DEFECT** |
| `_get_earliest_metric_timestamp` | 398 | No | success-path only | READ_ONLY (never acquires the write lane) |
| `_get_state` | 409 | No | success-path only | READ_ONLY |
| `_try_claim_reset_day` | 424 | Yes (INSERT/UPDATE) | success-path only | **SAME_DEFECT** (confirmed root cause) |
| `_get_actual_helius_usage` | 607 | No | success-path only | READ_ONLY |
| `get_summary` (inline) | ~1074 | No | success-path only | READ_ONLY |
| `get_top_methods` | ~1304 | No | success-path only | READ_ONLY |
| (credits-saved breakdown) | ~1500 | No | success-path only | READ_ONLY |
| (component breakdown) | ~1619 | No | success-path only | READ_ONLY |

`_metric_flush_loop` was already correct (`try/finally: conn.close()`
inside its per-attempt retry loop) — this is why the flusher thread that
timed out waiting on the poisoned `MainThread` correctly logged the error
and moved on without itself becoming a new poison source; it's the
established correct pattern already present in this same file.

Three functions share the exact same defect shape and were fixed in this
milestone: `_ensure_rpc_metrics_table`, `_set_state`, `_try_claim_reset_day`.
The remaining eight `READ_ONLY` functions never acquire the write lane at
all (per `TrackedConnection`'s design, only `INSERT`/`UPDATE`/`DELETE`/
`CREATE`/`ALTER` statements trigger `_acquire_write_lane`), so they cannot
exhibit this defect regardless of their own cleanup shape — not touched,
per the explicit instruction not to broaden the sweep beyond proven
identical defects.

## Part C — Repair (Phase 7-11)

All three fixed functions now declare `conn = None` before their `try`
block and add a `finally: if conn is not None: conn.close()` (wrapped in
its own `except Exception: pass`, since a failure during cleanup itself
must never raise past the function boundary). `_try_claim_reset_day`
additionally attempts `conn.rollback()` in its `except` branch before the
`finally`'s close, since it performs a genuine `INSERT ... ON CONFLICT`
write whose partial-failure state is worth explicitly unwinding (the other
two are DDL/idempotent-upsert operations where this is less load-bearing,
but `close()` alone still guarantees no lease leak regardless).

Function semantics are unchanged: return values, log/print message
strings, day-boundary/deduplication logic, and existing
expected-error-swallowing behavior are all preserved exactly — only the
connection lifecycle changed. Confirmed no external caller elsewhere in
the codebase depends on any different behavior.

`CrossProcessDatabaseWriteTimeout` itself remains fully bounded and
diagnosable — this fix does not retry indefinitely, does not touch the
60-second cross-process timeout, does not catch-and-ignore the timeout at
the write-lane layer, and does not bypass the global `sqlite3.connect`
monkeypatch. The metrics operation may still fail (exactly as before); the
calling thread now remains healthy afterward, which is the entire fix.

## Part D — Regression (Phase 12-16)

`tests/test_x78_11_rpc_metrics_lease_poisoning.py` — 5 tests, all directly
proving actual `_thread_write_lease.owner` state (not merely that
`close()` was called), using a real `TrackedConnection` (via
`db_locking.db_connect()`, not a mock) with only `commit()` wrapped to
raise — so the tests exercise the genuine
`_acquire_write_lane`/`_release_write_lane` machinery end to end:

- Primary poison regression for each of the three fixed functions
  (`_try_claim_reset_day`, `_ensure_rpc_metrics_table`, `_set_state`):
  forced `CrossProcessDatabaseWriteTimeout` from `commit()`, asserts the
  lease clears, then proves a same-thread follow-up write succeeds.
- Rollback-failure regression: `commit()` and `rollback()` both raise;
  `close()` still executes, lease still clears.
- Repeated stress: 200 iterations of `_try_claim_reset_day` cycling
  through forced timeouts (every 3rd call) and successes, asserting zero
  stale thread-local owners after **every single iteration**, not just at
  the end — the deterministic equivalent of the 1,342-cycle production
  failure, compressed to run in under a second.

All 5 initially failed cleanly against the pre-fix code with precise,
readable assertion messages naming the exact production bug (confirmed
before implementing the fix, per Phase 3's explicit requirement); all 5
pass against the fixed code.

**Test mechanics note**: the global `sqlite3.connect` monkeypatch
(`db_locking._patched_connect`) only intercepts the one real, configured
flex DB path — it does not intercept arbitrary `tmp_path` test databases.
Tests instead call `db_locking.db_connect()` directly (which constructs a
genuine `TrackedConnection` for any path) and monkeypatch
`rpc_metrics_recorder.sqlite3.connect` to return that real connection with
only `commit()` overridden — this was necessary to make the tests
mechanically prove the fix rather than merely asserting a mock's `close()`
was invoked; an earlier draft using a plain wrapper mock passed even
against pre-fix code because it never exercised the real release
machinery, and was corrected before being relied upon.

## Part E — Full X78.9/X78.10 regression (Phase 18-21)

Combined regression sweep, zero failures:

- `test_x78_9_cross_process_lock_timeout.py` — 9/9 pass (bounded timeout,
  short contention, SIGKILL release, owner metadata, health reporting).
- `test_x78_9_price_worker_singleton.py` — 4/4 pass.
- `test_x78_10_price_service_singleton.py` — 4/4 pass.
- `test_x78_10_release_unlocked_lock.py` — 4/4 pass (the guarded-release
  fix from X78.10 is untouched and still correct).
- `test_x78_10_listener_ensure_db_retry.py` — 4/4 pass (listener startup
  retry wrapper untouched and still correct).
- `test_x78_11_rpc_metrics_lease_poisoning.py` — 5/5 pass (new, this
  milestone).
- `test_database_write_service.py` — 9/9 pass.
- Broader pre-existing X78.0/X78.5/X78.6/X78.8 regression sweep — 45/45
  pass.

**84/84 tests pass** across the full accumulated write-lock/lease surface.
None of X78.2 (detached descendants), X78.3 (RPC cache), X78.4 (retry
containment), X78.6 (risk-scoring transaction boundaries), or X78.8
(infra-sync ownership) were reopened — no regression evidence against any
of them was found, per the explicit scope instruction.

## Part G — Creator resolution live recovery

[Filled in after production deployment and soak — see below.]

---

*(Document continues after live deployment; see the final report appended
below once the production soak completes.)*
