# X78.5 — Raw sqlite3.connect Lease-Leak Root-Cause Audit

## Status: root cause found and fixed.

## Summary

X78.4's live sanity window revealed a fifth permanent `NestedDatabaseWriteError`
source, initially only visible as `outer_command=db_locking.py:718 in
_patched_connect`. That tag identified the global `sqlite3.connect()`
monkeypatch (an interception point), not the real caller, making the
error unactionable. This audit shipped a diagnostic fix to
`db_connect()`'s caller-attribution logic, redeployed, and captured the
real caller on the next live recurrence: **`risk_scoring_builder.py:124
in score_creator_now`**.

## Phase 1-2: caller-attribution fix and live capture

`db_connect()`'s caller-detection (`inspect.stack()[1]`) always resolved
to `_patched_connect` when entered via the global monkeypatch, since that
function is literally the immediate caller one frame up. Fixed to walk
one frame further specifically when that's detected, without touching
`_patched_connect` itself or any locking semantics. Redeployed
(pid `87341`) and let the leak recur naturally.

Within the first cycle, the real caller was captured directly in the
error log:

```
outer_command=risk_scoring_builder.py:124 in score_creator_now
inner_command=creator_funding_worker.py:117 in _db_connect
```

241 occurrences accumulated in a single ~30 minute window, spanning
`prediction rescore failed`, `[INTEL_REFRESH] IRC error`,
`[INTEL_REFRESH] NetworksRelease error`, `heartbeat write failed`, and
—critically— `[FUNDING] Error saving outgoing transfer` failures during
an **entirely different, later job's own extraction**, proving the
lease had been acquired once and never released, permanently poisoning
that thread.

## Phase 9/11: root cause

`RiskScoringBuilder.score_creator_now()` (`risk_scoring_builder.py:122-140`,
pre-fix):

```python
def score_creator_now(self, creator: str) -> dict:
    conn = sqlite3.connect(self.db_path, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        self.apply_migration(conn)
        sync_infra_wallets(conn)
        ...
        conn.commit()
        return {"status": "success", ...}
    except Exception as e:
        conn.rollback()
        return {"status": "error", ...}
    finally:
        conn.close()
```

The connection is opened, and `row_factory`/`PRAGMA journal_mode=WAL`
are executed, **before** the `try` block begins. If either of those two
lines raised for any reason, the exception propagated straight out of
`score_creator_now` without ever reaching `finally: conn.close()` — the
only thing that calls `TrackedConnection._release_write_lane()` and
clears this thread's `_thread_write_lease.owner`. Since `score_creator_now`
is dispatched via `asyncio.to_thread(...)` inside `_process_job`'s
post-extraction enrichment (`creator_funding_worker.py:836`), on the
same reused executor pool as every other `to_thread`-dispatched write in
the worker, one such failure poisoned that thread permanently — every
subsequent write landing on it collided, matching the live signature
exactly, until the process was eventually restarted.

**Verdict: A — RAW CALLER MISSES CLOSE** (specifically: on a failure path
reachable before the function's own `try` block began).

## Phase 12: minimal repair

Moved `conn = sqlite3.connect(...)`, `conn.row_factory = sqlite3.Row`,
and `conn.execute("PRAGMA journal_mode=WAL")` inside the `try` block,
with `conn = None` declared beforehand and `conn.close()`/`conn.rollback()`
guarded with `if conn is not None:` in `finally`/`except` — matching the
exact pattern already used correctly by every other connection in this
file and across the codebase since X78.0. No other logic changed;
attribution/scoring semantics are untouched.

## Phase 13: forward invariant

`score_creator_now` now has exactly one terminal lifecycle on every
path: `SUCCESS → commit → close`, or `FAILURE (any point after connect)
→ rollback (best-effort) → close`. No third path remains — the
previously-uncovered pre-`try` window is closed.

## Validation

- `tests/test_x78_5_patched_connect_caller_attribution.py` (2 tests) —
  confirms the diagnostic fix correctly attributes the real caller and
  doesn't affect direct `db_connect()` calls.
- `tests/test_x78_5_risk_scoring_lease_leak.py` (3 tests) — confirms
  `score_creator_now` releases its lease on the happy path, on an early
  setup failure, and that a later unrelated write on the same thread
  does not collide afterward.
- **Honest limitation, documented in the test file itself**: forcing the
  exact original trigger (`PRAGMA journal_mode=WAL` or `row_factory`
  raising specifically before the pre-fix `try` began) could not be made
  deterministic in a unit test — a real concurrent lock is waited out
  within the connection's own `timeout=60`, and the `sqlite3.Connection`
  C type cannot be monkeypatched. The tests instead verify the general,
  equally load-bearing invariant (lease released regardless of where in
  the function a failure occurs) and the fix's correctness rests
  additionally on direct code review: the diff is a pure reordering into
  the same try/finally shape already proven correct elsewhere in this
  file (`run()`) and this codebase.
- All 26 tests across X78.2, X78.3, X78.4, and X78.5 combined pass in
  one run — no regression to any prior fix.
- `git diff` scoped to `risk_scoring_builder.py` (one function) plus
  `db_locking.py` (the caller-attribution diagnostic improvement) and
  new test files; no changes to `TrackedConnection`, `_thread_write_lease`,
  `NestedDatabaseWriteError`, `_patched_connect`, or any scoring/
  attribution semantics.

## Root-cause ledger (cumulative, X78.0-X78.5)

| Mechanism | Status |
|---|---|
| Individual connection/transaction leaks (25 fixes, X78.0) | FIXED / historical |
| Detached background descendants (X78.2) | FIXED |
| RPCCache same-job nested ownership (X78.3) | FIXED |
| Cancellation grace-period overrun (X78.4) | FIXED (via retry/isolation) |
| `RiskScoringBuilder.score_creator_now` pre-try connection leak (X78.5) | **FIXED** |
| `_patched_connect` caller-attribution diagnostic gap | FIXED (permanent improvement, benefits all future investigations) |
| `SecondHopExpansionBuilder._is_enabled()` connection handle leak (SELECT-only, contributes to `_open_handle_count()` but not `NestedDatabaseWriteError`) | Identified during the census, not fixed this pass — hygiene follow-up |
| Other nested-write source | NONE currently identified; next live soak is the opportunity to surface one |

## Production readiness verdict

**NOT YET CONFIRMED READY** — the identified root cause is fixed and
locally validated, but per this whole investigation's established
discipline, a live restart and sanity window are required before
declaring readiness. Given the pattern in this series (X78.2 through
X78.4 each required more than one live cycle to fully validate), the
next step is a supervised restart and observation window before any
READY verdict.

## Commit

Local commit only, not pushed, per task instruction.
