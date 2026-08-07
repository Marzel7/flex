# X78.4 — Creator Funding Cancellation Grace-Period Repair

## Summary

X78.3's live sanity window (post-restart) surfaced a fourth, distinct
`NestedDatabaseWriteError` mechanism: a timed-out job's cancellation
handling exceeded `EXTRACTION_CANCEL_GRACE_SECONDS` and "proceeded
anyway" — exactly the condition X78.0's own code comment warned about —
stalling the live worker permanently (~27+ minutes of unbroken
collisions observed).

X78.4 reproduced the mechanism deterministically, **disproved two
detection-based fix designs** with direct evidence before implementing
either, and shipped the smallest fix that actually survives reproduction:
retry-with-backoff around every write this worker's own loop/job
processor makes, since detecting true completion of the cancelled
extraction's underlying work is provably impossible from outside that
work itself.

## Root cause (confirmed)

`asyncio.CancelledError` cannot interrupt code already running inside an
`asyncio.to_thread()` dispatch — cancellation only takes effect at the
awaiting coroutine's own `await` point. If `extract_for_creator`'s
coroutine is suspended on `await asyncio.to_thread(self._save_outgoing_transfer, ...)`
(a synchronous call that acquires a write lease internally, per its own
comment at `realtime_creator_funding_extractor.py:1193-1203`) at the
moment `_process_job` calls `_extraction_task.cancel()`, the underlying
thread-pool call keeps running to completion regardless — proven directly:

```python
task.cancel()
await task  # raises CancelledError
# ... yet the underlying to_thread call is STILL running here
```

If that call is slow (e.g. blocked on `DB_WRITE_LOCK` contention — the
live logs show `[FUNDING] Error saving outgoing transfer: database is
locked` at exactly this point) and outlives
`EXTRACTION_CANCEL_GRACE_SECONDS` (10s), `_process_job`'s pre-X78.4 code
logged a warning and proceeded to the next job/heartbeat/cycle anyway —
while the straggling thread could still be holding a write lease.

## Two detection-based designs attempted and disproven (in order)

Per the task's explicit instruction to reproduce before repairing, two
designs were built, tested, and found **provably wrong** before the
actual fix was written:

**Design 1 — reuse X78.2's `_STRAGGLER_TASKS` mechanism.** Track the
cancelled `_extraction_task` as a straggler (like X78.2 does for
detached background descendants) and gate the next write on
`asyncio.Task.done()`. **Disproven**: `Task.done()`/`.cancelled()`
report `True` within milliseconds of `.cancel()` being called — the
instant asyncio's own bookkeeping processes the cancellation — completely
independent of whether the underlying `to_thread` work has actually
stopped. A regression test asserting `not task.done()` while the real
thread was still provably running failed immediately, catching this
before it reached production code.

**Design 2 — a dedicated `threading.Event` set in the extraction
coroutine's own `finally` block**, reasoned to be more reliable than
`Task.done()` since it's explicitly under our control. **Also
disproven**, by the same mechanism one level deeper: `finally: _extraction_finished.set()`
runs the instant `CancelledError` propagates through
`await asyncio.to_thread(...)` — which happens as soon as the *coroutine*
is cancelled, not when the underlying thread genuinely completes. This
was caught via a live end-to-end test against the real `_process_job`,
which completed suspiciously fast; a minimal reproduction confirmed the
exact mechanism and is preserved as a permanent regression test
(`test_wrapper_finally_signal_is_unreliable`) documenting why this
approach cannot work with current Python asyncio semantics.

**Conclusion: there is no reliable way to observe true completion of a
cancelled `to_thread` call from the awaiting coroutine's side**, without
instrumenting the synchronous function itself (out of this fix's scope,
since it would mean touching every `to_thread` call site inside
`extract_for_creator`, not a small fix).

## The fix (isolation, not detection)

Since detection is provably impossible at this boundary, X78.4
implements the other half of the safe invariant the task specified:
"isolated such that its connection can no longer conflict."
`NestedDatabaseWriteError` was already proven, both structurally and in
the reproduction tests, to be **transient** — the straggler's own
`DB_WRITE_LOCK.acquire(timeout=60)` and every other SQLite timeout in
this codebase are bounded, so a genuine straggler MUST eventually release
its lease. `_retry_on_nested_write(fn, *args, **kwargs)` retries the
wrapped call with exponential backoff (0.5s base, capped at 30s/attempt,
8 attempts max — ~90s worst case) on `NestedDatabaseWriteError`.

An important corrected fact discovered during implementation:
`NestedDatabaseWriteError` is **not** raised by `_db_connect`/`db_connect`
(opening a connection or running `PRAGMA` never acquires the write
lease) — it is raised by the first write-shaped `.execute()`/`.executemany()`
call on a connection. The retry therefore wraps each write helper's full
open-write-close call, not connection-opening alone — confirmed directly
in `test_nested_write_error_is_raised_by_execute_not_by_connect`.

Applied at every write this worker's own execution path makes:
- `_mark_retry`, `_mark_complete`, `_mark_failed` (queue bookkeeping —
  the critical path; a permanent failure here means a job silently never
  progresses).
- `_recover_stale_and_claim` (cycle entry).
- `_write_heartbeat`, at all three call sites — also converted from a
  direct synchronous call to `asyncio.to_thread`-dispatched, since
  retrying with `time.sleep` directly on the event-loop thread would
  otherwise freeze the entire loop (including the very extraction task
  the retry exists to wait out).

Not wrapped: the post-extraction enrichment calls (risk scoring,
second-hop-lite, network assignment, intelligence refresh) — each already
has its own `try/except: log and continue`, so a single miss is already
tolerated and non-fatal; wrapping them would add complexity without
closing a real gap.

## Validation

- `tests/test_x78_4_cancellation_grace_period_reproduction.py` (2 tests)
  — Phase 1/2: proves cancellation cannot interrupt in-flight `to_thread`
  work, and proves the lease is still held at the exact moment
  `_process_job`'s pre-fix cancellation handling would have proceeded.
- `tests/test_x78_4_write_retry.py` (5 tests) — the permanent regression
  documenting why both detection designs fail
  (`test_wrapper_finally_signal_is_unreliable`), confirms exactly where
  `NestedDatabaseWriteError` is raised, and validates
  `_retry_on_nested_write`'s success/give-up/no-op-when-clean paths.
- `tests/test_x78_4_process_job_end_to_end.py` (1 test) — exercises the
  real `_process_job` end to end against the exact live-observed
  sequence (timeout → cancel → grace-period overrun → still-running
  straggler) and confirms the job completes (via retry) instead of
  stalling.
- All 21 tests across X78.2, X78.3, and X78.4 pass together in one run —
  no regression to either prior fix.
- `git diff` scoped to a single file; no changes to `TrackedConnection`,
  `_thread_write_lease`, `NestedDatabaseWriteError`, or any extraction/
  attribution semantics — `extract_for_creator` itself was not touched.

## Live deployment

Restarted via the normal supervised path
(`supervisorctl restart creator_funding_worker`) after local validation.
See the live sanity-window results recorded separately (Phase 18-20
observations appended below once available).

## Root-cause ledger (cumulative, X78.0-X78.4)

| Mechanism | Status |
|---|---|
| Individual connection/transaction leaks (25 fixes, X78.0) | FIXED / historical |
| `asyncio.to_thread` executor-thread-pool reuse amplification | HISTORICAL — real, but not the primary explanation for X78.2-X78.4 |
| Primary extraction timeout ordering (`b779689`, X78.0) | Superseded by X78.4 (the grace-period gap it left open is now closed by retry, not by a stronger wait) |
| Detached background descendants outliving `_process_job` (X78.2) | FIXED |
| RPCCache same-job nested ownership (X78.3) | FIXED |
| Cancellation grace-period overrun leaving lease held past `_process_job`'s boundary (X78.4) | **FIXED** — via retry/isolation, since detection is proven impossible |
| Other nested-write source | NONE currently identified; live soak is the next opportunity to surface one |

## Commit

Local commit only, not pushed, per task instruction.
