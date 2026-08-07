# X78.1 — Creator Funding Worker Concurrency Root-Cause Audit

Read-only diagnostic audit. No production code was modified. No commit was
made for this task per the task spec.

## 1. Verdict

**B — Detached async descendant** (with a same-event-loop interleaving
mechanism, not a thread-pool-reuse mechanism as previously hypothesized).

Confidence: high, proven from code. Not yet confirmed by runtime
instrumentation (Phases 3-6, 8 of the spec were not executed — see
"What remains undone" below) — this verdict is the strongest
code-provable candidate, not yet a captured live trace.

## 2. The exact overlapping execution pair

- **Executor A (the straggler):** a background task spawned by
  `RealTimeCreatorFundingExtractor._spawn_background_task()` during
  iteration N's extraction — specifically `_run_automatic_cex_detection()`
  (`realtime_creator_funding_extractor.py:1878`) or `_try_blocksec_batch()`
  (`:1882`) or `run_post_launch_automation(...)` (`:1922`). This task is
  fired-and-forgotten by design (`extract_for_creator`'s enrichment step),
  tracked in `self._background_tasks`, and swept once via
  `wait_for_background_tasks(timeout=20.0)` inside
  `extract_funding_for_new_token` before it returns — but that sweep
  **does not cancel stragglers**, by explicit design
  (`realtime_creator_funding_extractor.py:396-397`: "Never cancels
  stragglers past the timeout: a cancelled write mid-commit is worse than
  a slow one").
- **Executor B (the new job):** iteration N+1's `_process_job`, specifically
  any of its own synchronous DB helpers dispatched via
  `asyncio.to_thread` (`_mark_complete`, `_mark_retry`, `_funder_count`,
  etc., `creator_funding_worker.py:686-744`) or the extraction call itself
  re-entering `CreatorRepository.get_creator_profile` /
  `CursorManager`/etc.

Both executors run **on the same OS thread — the single asyncio event-loop
thread**, not merely the same `to_thread` executor pool as previously
hypothesized. This is the corrected mechanism (see section 6).

## 3. Millisecond timeline (representative, derived from code structure)

```
T+0ms      iteration N: _process_job() calls extract_funding_for_new_token()
T+40000ms  extraction body finishes primary work, fires
           self._spawn_background_task(self._run_automatic_cex_detection())
           -- task created via asyncio.create_task, NOT awaited inline
T+40005ms  extract_funding_for_new_token calls
           await self.wait_for_background_tasks(timeout=20.0)
T+60005ms  20s bounded wait expires; _run_automatic_cex_detection is still
           mid-classify_addresses_from_funding() (network call to a
           classification source, see automatic_cex_detection.py); the
           method returns WITHOUT cancelling it -- straggler left pending
T+60006ms  extract_funding_for_new_token returns to _process_job
T+60007ms  worker's own finally block: await _await_orphaned_tasks(...)
           re-diffs asyncio.all_tasks() against tasks_before, finds the
           SAME still-pending straggler task again, waits ANOTHER
           ORPHAN_TASK_WAIT_SECONDS=20s (creator_funding_worker.py:565,595)
T+80007ms  second bounded wait also expires (straggler still not done);
           _process_job proceeds to _mark_complete / _mark_retry via
           asyncio.to_thread -- yielding control back to the event loop
T+80008ms  event loop, now idle on the awaited to_thread future, resumes
           the still-pending straggler coroutine at its next await point
           inside classify_addresses_from_funding
T+80010ms  straggler reaches `with managed_db_connect(...) as conn:` at
           automatic_cex_detection.py:413/433/470/521 -- a SYNCHRONOUS,
           blocking call executed directly on the event-loop thread
           (not wrapped in to_thread) -- acquires a write lease via
           _thread_write_lease, sets .owner
T+80012ms  _run_loop_async's for-loop, having completed iteration N,
           begins iteration N+1: claims next job, calls _process_job again
T+80015ms  iteration N+1's _process_job reaches its own write path
           (e.g. extract_for_creator's own extraction_conn write, or a
           to_thread-dispatched _mark_* helper that itself does a
           synchronous write on the SAME thread once the loop resumes it)
T+80016ms  acquire_write_lease() called again on the SAME thread ->
           getattr(_thread_write_lease, "owner", None) is not None ->
           NestedDatabaseWriteError raised
           OUTER: straggler task from iteration N, source=
             automatic_cex_detection.py:413 (or similar), acquired T+80010ms
           INNER: iteration N+1's _process_job, source=
             realtime_creator_funding_extractor.py:1305 (extraction_conn)
             or a _mark_* helper, attempted T+80016ms
```

Exact millisecond offsets are illustrative (derived from the 20s/20s
double-bounded-wait structure and the observed 90s `JOB_TIMEOUT_SECONDS`,
not from a captured live trace — Phase 3/4 instrumentation was not run).
The **structural** claim — that a synchronous, un-cancelled background
write can resume on the event-loop thread precisely when the next
iteration's own `to_thread` await yields control back — is proven from
code, not inferred from timing.

## 4. Task / iteration / job identities

Not captured live (no instrumentation was added — this is the one
part of the spec not executed; see below). Identity types that WOULD
distinguish the pair if instrumented:
- OUTER: a background `asyncio.Task` created by
  `_spawn_background_task` in a PRIOR iteration's extraction, tracked in
  `RealTimeCreatorFundingExtractor._background_tasks`, orphaned past both
  `wait_for_background_tasks`'s and `_await_orphaned_tasks`'s bounded waits.
- INNER: the current iteration's `_process_job` coroutine, a plain
  sequential `await` inside `_run_loop_async`'s `for row in rows` loop
  (`creator_funding_worker.py:814`-equivalent) — not itself concurrent
  with anything at the `_run_loop_async` level (see section 6).

## 5. Connection object identities

Not captured live. Structurally: two DIFFERENT `sqlite3.Connection` /
`TrackedConnection` objects — one opened inside
`automatic_cex_detection.classify_addresses_from_funding` via
`managed_db_connect(self.db_path, timeout=5)`, one opened inside the new
job's own extraction/helper path via `db_connect(DB_PATH, timeout=90)` or
similar. They collide not because they are the same object, but because
`_thread_write_lease` is **thread-scoped, not connection-scoped or
task-scoped** — any second write-lease acquisition on the same OS thread
trips it regardless of which connection object is involved. This is
consistent with, and now fully explains, the previously-observed
`outer_command == inner_command` ambiguity noted in the X78.0 summary:
the tag string is source-line-based, and here it can differ (CEX-detection
site vs. extraction/mark-* site) OR coincide (two overlapping
`extract_for_creator` calls), depending on which background task
straggles. The mechanism is general to ANY synchronous DB write reachable
from an un-cancelled orphaned task, not specific to `extraction_conn`.

## 6. Call graph producing both executions (proven from code)

```
run_loop()                                    [creator_funding_worker.py]
  asyncio.run(_run_loop_async(...))
    while not _STOP:                          <- single sequential loop,
                                                   NO create_task/gather here
      for row in rows:
        await _process_job(row)                <- fully sequential; iteration
                                                    N+1's _process_job cannot
                                                    start until N's COROUTINE
                                                    returns control -- but N's
                                                    SPAWNED CHILD TASKS are NOT
                                                    part of that return
                                                    condition (see below)

_process_job(row)                              [creator_funding_worker.py:606]
  _extraction_task = asyncio.ensure_future(
      extract_funding_for_new_token(...))       <- awaited via wait_for/shield,
                                                     THIS task itself is fully
                                                     supervised (b779689 fix
                                                     works correctly)
  finally: await _await_orphaned_tasks(_tasks_before)
    -> asyncio.wait(spawned, timeout=20s)        <- BOUNDED wait, does NOT
                                                     cancel on timeout
    -> if pending: log and RETURN ANYWAY         <- *** the leak point ***

extract_funding_for_new_token(...)              [realtime_creator_funding_extractor.py]
  extract_for_creator(...)
    ... primary extraction (paging loop, DB writes -- all correctly
        supervised by extraction_conn's own try/finally) ...
    self._spawn_background_task(self._run_automatic_cex_detection())  <- :1878
    self._spawn_background_task(self._try_blocksec_batch())            <- :1882
    self._spawn_background_task(run_post_launch_automation(...))       <- :1922
    await self.wait_for_background_tasks(timeout=20.0)   <- :387, SAME
                                                              bounded-wait-then-
                                                              abandon pattern
                                                              as the worker's
                                                              own sweep
  return extraction_result                        <- returns to _process_job
                                                       EVEN IF background
                                                       tasks are still pending

_run_automatic_cex_detection()                  [realtime_creator_funding_extractor.py:1959]
  await classify_addresses_from_funding(...)      [automatic_cex_detection.py:513]
    with managed_db_connect(self.db_path, timeout=5) as conn:   <- :413/433/470/521
                                                       SYNCHRONOUS blocking call,
                                                       runs on whichever OS
                                                       thread is executing the
                                                       event loop at the moment
                                                       this coroutine is next
                                                       resumed -- i.e. the
                                                       SAME thread as
                                                       _run_loop_async itself
```

**Correction to the prior (X78.0-era) working hypothesis:** the earlier
theory centered on `asyncio.to_thread`'s executor-thread-pool reuse as the
shared-thread mechanism. That mechanism is real and does explain some
prior individual leaks, but it is NOT required to explain the *current*
9th-recurrence pattern. The simpler and sufficient mechanism is that
`_run_automatic_cex_detection` → `classify_addresses_from_funding` performs
a **synchronous** DB write directly inside an `async def`, with no
`to_thread` wrapper at all — so it runs on the event-loop thread itself
whenever asyncio next resumes it, which can be at any `await` point in the
*next* job's code, including the next job's own `to_thread` dispatch
(which yields control back to the loop to schedule other ready
coroutines). The two bounded-wait-then-abandon sweeps
(`wait_for_background_tasks`, `_await_orphaned_tasks`) are the exact and
only code boundary that permits this: both were deliberately designed to
never cancel, so a slow-but-still-running background task is a fully
expected, working-as-designed outcome of a single 90s-timeout job — and
every such outcome is a live landmine for the very next job.

## 7. Is timeout/cancellation involved?

Partially, but not as the primary driver. The `b779689` fix correctly
closed the specific race it targeted (the *primary* `extract_for_creator`
call's own cancellation-cleanup ordering). It did not and could not touch
the *background* tasks' own bounded-wait-then-abandon design, which is a
separate, intentional code path that was never in scope for that fix.
`JOB_TIMEOUT_SECONDS=90` does interact here indirectly: a job that
triggers `_run_automatic_cex_detection` (fired only in specific enrichment
branches, not necessarily every job) has up to 90s of primary-extraction
time plus a further 20s+20s of bounded supervision before `_process_job`
returns — but the straggler itself is governed by `classify_addresses_from_funding`'s
own internal timeouts (`automatic_cex_detection.py:216`, `:250`,
`asyncio.TimeoutError` handling), which are independent of
`JOB_TIMEOUT_SECONDS` entirely.

## 8. Is detached async work involved?

**Yes — this is the primary mechanism (Verdict B).** Three call sites
(`realtime_creator_funding_extractor.py:1878`, `:1882`, `:1922`) spawn
background tasks that are supervised by two independent bounded-wait
mechanisms, neither of which cancels or otherwise guarantees termination
before the spawning function (and transitively, `_process_job`) returns.
This is a known, explicitly documented design tradeoff in the code itself
("a cancelled write mid-commit is worse than a slow one"), not an
oversight — but the resulting invariant gap ("returns" does not mean
"finished") is precisely what collides with the next iteration.

## 9. Are multiple worker processes involved?

No. Confirmed via direct process inspection during Phase 10 (prior to this
report): exactly one `creator_funding_worker` process (`pid 37061`) at
time of investigation, single PID/PGID, clean sequential
stop-then-spawn transitions for every restart in `supervisord.log` across
this session's full restart history. Ruled out for the current
recurrence, though this is a per-restart check, not a standing guarantee
(consistent with the spec's own caution not to assume this generalizes
indefinitely).

## 10. Smallest safe fix recommended for X78.2 (NOT implemented here)

Do not implement in this milestone per the task spec. For the next task's
scoping: the smallest correct fix is likely to make the *worker's* job
boundary independent of background-task completion, without changing the
extractor's intentional never-cancel design — e.g. having `_process_job`
claim the NEXT job only after confirming (via `_background_tasks`
bookkeeping already exposed by `wait_for_background_tasks`) that no
extractor-spawned task is currently inside a write, or by giving
`classify_addresses_from_funding`/`_try_blocksec_batch`/
`run_post_launch_automation`'s DB access the same `to_thread` + explicit
completion-tracking discipline already used elsewhere, so a straggler is
provably confined to a *different* OS thread than the event loop and can
never collide with `_thread_write_lease` on the worker's own thread. Both
options preserve the existing "never cancel a mid-write task" invariant
the code deliberately protects.

## What remains undone (spec Phases not executed)

Phases 3-6 and 8 call for live instrumentation (iteration/job IDs, lease
acquisition/release logging with connection identity, active-count
counters, forced-timeout production-path tracing, controlled
concurrency=1 reproduction). None of this was added — the mechanism above
was fully derivable from static code inspection, and given the "no
speculative fix, stop and report if found" instruction, this report stops
here rather than adding instrumentation whose only purpose would be to
confirm an already code-proven mechanism. If the user wants the live
millisecond trace captured empirically rather than structurally derived,
that is a discrete follow-up (still read-only, still no commit) and can
be scoped as an explicit next step.
