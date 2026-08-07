# X78.2 — Creator Funding Detached-Task Lifecycle Repair

## Summary

X78.1 (static, code-derived) identified detached background descendants
(Verdict B) as the likely surviving cause of creator_funding_worker's
recurring `NestedDatabaseWriteError`, after 25 individual leak/hygiene
fixes across 9 commits (X78.0) had closed every other reachable source.

X78.2 reproduced the mechanism deterministically (Phase 1), confirmed
Verdict A = **DETACHED DESCENDANT CONFIRMED** (Phase 4), inventoried and
classified every spawned descendant (Phase 5-6), and implemented the
smallest fix that closes the collision without weakening
`TrackedConnection`, `_thread_write_lease`, or changing any extraction/
attribution/reconciliation semantics (Phase 7-17).

## Root cause (confirmed)

`RealTimeCreatorFundingExtractor._spawn_background_task()` fires three
fire-and-forget enrichment tasks per extraction:

| Call site | Function | DB access |
|---|---|---|
| `realtime_creator_funding_extractor.py:1878` | `_run_automatic_cex_detection()` → `classify_addresses_from_funding()` | synchronous `managed_db_connect` writes at `automatic_cex_detection.py:413/433/470/521`, directly inside `async def`, interleaved with a real network `await` |
| `realtime_creator_funding_extractor.py:1882` | `_try_blocksec_batch()` → `auto_batch_new_addresses()`/`BlockSecAMLBatcher.submit_batch()` | same pattern, `blocksec_aml_batcher.py:384/451`, interleaved with `await resp.json()`/`await resp.text()` |
| `realtime_creator_funding_extractor.py:1922` | `run_post_launch_automation(...)` | same pattern, `post_launch_automation.py:409/468/536` |

All three are **WRITE_CAPABLE**, none confine their DB access to a
dedicated thread (no `to_thread` wrapper around the synchronous
`sqlite3.connect`/`managed_db_connect` calls) — they run directly on
whichever thread is executing the event loop at the moment asyncio
resumes them, which for `creator_funding_worker` is always the same
single event-loop thread `_run_loop_async` itself runs on.

Two independent supervision layers exist to await these tasks —
`RealTimeCreatorFundingExtractor.wait_for_background_tasks(timeout=20)`
and the worker's own `_await_orphaned_tasks(timeout=20)` — and **both
intentionally give up and return once their bounded wait expires,
without cancelling the straggler** ("a cancelled write mid-commit is
worse than a slow one"). Prior to this fix, that meant `_process_job`
could return with a background task still holding, or about to acquire, a
write lease — and because `_thread_write_lease`
(`src/core/database_write_service.py:85`) is a `threading.local()`
reentrancy guard keyed on the OS thread (not the task or connection), the
very next `_process_job` call's own first write on that same thread
raised `NestedDatabaseWriteError`.

This was reproduced deterministically, with no sleeps/timing races, in
`tests/test_x78_2_detached_descendant_reproduction.py` using the real
`acquire_write_lease`/`release_write_lease` primitives.

## The fix

`src/core/creator_funding_worker.py` (single file, ~70 lines added, no
deletions of existing logic):

- A new module-level `_STRAGGLER_TASKS: set` that survives across
  `_process_job` calls (unlike the existing `_tasks_before`/`spawned`
  locals, which are per-call).
- `_await_orphaned_tasks`, on its existing bounded-wait timeout, now hands
  off whatever is still `pending` into `_STRAGGLER_TASKS` instead of
  silently discarding the reference — the wait itself, its timeout value,
  and its refusal to cancel are all unchanged.
- A new `_await_stragglers_before_next_write()`, called at the very top of
  `_process_job` (before extraction, before any write this job makes),
  that waits **unboundedly** — never cancels — for every tracked straggler
  from a prior job to finish before allowing this job's own writes to
  begin.

This closes the collision at the one boundary where it can actually
occur (the moment a new job is about to write) without touching the
extractor's fire-and-forget design, without cancelling anything mid-write,
and without changing `_thread_write_lease`'s thread-scoped semantics —
the guard was correctly exposing an invalid lifecycle; the lifecycle is
what changed.

## Validation

- `tests/test_x78_2_detached_descendant_reproduction.py` (2 tests) —
  Phase 1 deterministic reproduction + negative control.
- `tests/test_x78_2_job_boundary_regression.py` (5 tests) — Phase 13-14:
  handoff into `_STRAGGLER_TASKS`, the core "next job waits, doesn't race"
  regression, no-op-when-empty, never-cancels invariant, and set pruning
  after successful wait.
- `tests/test_x78_2_sequential_stress.py` (1 test) — Phase 15: 100
  sequential simulated jobs with randomized fast/slow/very-slow background
  enrichment. Result: 0 `NestedDatabaseWriteError`, 100/100 writes
  completed, 0 lost stragglers.
- All 8 new tests pass together in one run.
- `git diff` scoped to a single file; no changes to `TrackedConnection`,
  `database_write_service.py`, or any attribution/reconciliation/resolver/
  walkback/CEX/BlockSec classification logic (Phase 16-17 verified by
  diff inspection).

**Pre-existing, unrelated finding:** `tests/test_x78_0_leak_source_fixes.py`
and `tests/test_x78_0_creator_funding_lease_poisoning.py` hang partway
through their suite when run back-to-back or in combination with other
test files. Confirmed via `git stash` that this hang reproduces
identically on the pre-X78.2 codebase — it is not caused by this change.
Root cause not yet investigated (out of scope for X78.2); likely a
leaked `_thread_write_lease`/`_DB_WRITE_LOCK` from one of those files'
own intentional lease-poisoning test scenarios bleeding into a later
test in the same process. Each new X78.2 test file, and the X78.0 suites
individually, pass in isolation.

## Live deployment / soak status

Per the task's Phase 18-22, restart-and-soak was intended to follow local
validation. Given session constraints, live deployment and the
15-minute/60-minute soak windows were **not executed as part of this
turn** — see Production Readiness verdict below.

## Root-cause reconciliation (Phase 23)

| Mechanism | Status |
|---|---|
| Individual connection/transaction leaks (25 fixes, X78.0, 9 commits) | Fixed, historical |
| `asyncio.to_thread` executor-thread-pool reuse amplification | Real mechanism, but not required to explain the 9th recurrence; superseded as primary explanation |
| Primary extraction timeout/cancellation ordering (`b779689`) | Fixed, correctly closed its specific target; not the surviving cause |
| Detached background descendants outliving `_process_job` | **Was the surviving cause — fixed in X78.2** |
| Event-loop synchronous DB writes inside async enrichment functions | Root mechanism enabling the above; not itself changed (writes still synchronous) — the *lifecycle* around them changed instead, per the task's explicit preference to fix ownership over touching those three call sites |

## Production readiness verdict (Phase 24)

**NOT READY** — pending live deployment and soak (Phase 18-22), which
were not run in this turn. All local validation (deterministic
reproduction, regression, 100-job stress test) passes. Remaining blocker
before a READY verdict: a real supervised restart plus the 15-minute
sanity window and 60-minute/2-hour soak, observing heartbeat, queue
progress, and confirming zero live `NestedDatabaseWriteError` recurrence
under real production contention (walkback_worker, ws_cascade running
concurrently, per Phase 21).

## Commit

Local commit only, not pushed, per task instruction.
