# X78.0 — Creator Funding Concurrency & Production Readiness

## Objective

Restore `creator_funding_worker` to sustained healthy production operation.
Determine the real cause of its continuous `NestedDatabaseWriteError`
self-nesting — do not guess. Repair only `creator_funding_worker.py` and
`realtime_creator_funding_extractor.py` (plus files directly in their
reachable write-call graph). Do not weaken `TrackedConnection`,
`DatabaseWriteService`, the write-lane guard, or nested-write detection.

## Phase 1 — Architecture audit

Execution path traced end to end:

```
creator_funding_worker._run_loop_async()          [event-loop thread]
  await asyncio.to_thread(_pending_count)          [executor thread pool]
  await asyncio.to_thread(_recover_stale_and_claim) [executor thread pool]
  for row in rows:
    _write_heartbeat(...)                          [event-loop thread, SYNC call]
    await _process_job(row)                        [event-loop thread]
      _tasks_before = asyncio.all_tasks(...)
      await asyncio.wait_for(
          extract_funding_for_new_token(...),       [event-loop thread]
          timeout=90,
      )
        └── extractor.process_new_token(...)
              └── extractor.extract_for_creator(...) [event-loop thread]
                    extraction_conn = db_connect(...)  [ACQUIRES write lease
                                                          lazily, on event-loop
                                                          thread]
                    ... paging loop, many awaits ...
                    CREATE TABLE IF NOT EXISTS (creator_service_history,
                                                 creator_receivers)
                    _spawn_background_task(_run_automatic_cex_detection())
                    _spawn_background_task(_try_blocksec_batch())
                    extraction_conn.close()          [releases lease, if held]
                    _spawn_background_task(run_post_launch_automation(...))
                    finally: extraction_conn.close() [no-op if already closed]
        └── asyncio.gather(_jitotip, check_transfers_for_debridge,
                            check_transfers_for_axiom, _outgoing,
                            return_exceptions=True)   [event-loop thread,
                                                        each uses its own
                                                        managed_db_connect]
              └── extract_outgoing_transfers(...)
                    await asyncio.to_thread(_save_outgoing_transfer, ...)
                                                       [executor thread pool --
                                                        SAME pool as the
                                                        worker's own
                                                        to_thread calls]
        └── await extractor.wait_for_background_tasks()  [event-loop thread,
                                                            awaits every task
                                                            _spawn_background_task
                                                            registered, bounded
                                                            20s timeout]
      finally: await _await_orphaned_tasks(_tasks_before)  [event-loop
                                                              thread, worker's
                                                              OWN older,
                                                              redundant
                                                              all_tasks()-diff
                                                              heuristic]
      await asyncio.to_thread(_funder_count, creator)  [executor thread pool]
      await asyncio.to_thread(_mark_complete/_mark_retry/_mark_failed, ...)
                                                        [executor thread pool]
      await asyncio.to_thread(_enqueue_second_hop_lite, ...)  [executor pool]
      await asyncio.to_thread(RiskScoringBuilder(...).score_creator_now, ...)
      await asyncio.to_thread(_rescore)                [executor pool]
      await asyncio.to_thread(assign_live_network_for_creator, ...)
      await asyncio.to_thread(_post_extraction_intelligence_refresh, ...)
```

**Two distinct OS-thread pools are in play**, and both matter:

1. **The asyncio event-loop's own thread** — `extract_for_creator`'s
   `extraction_conn`, its `CREATE TABLE IF NOT EXISTS` block, and the four
   `asyncio.gather`-ed enrichment coroutines (`check_create_tx_for_jitotip`,
   `check_transfers_for_debridge`, `check_transfers_for_axiom`, the
   `_outgoing` closure) all execute here, directly, never via
   `asyncio.to_thread`.
2. **`asyncio.to_thread`'s default executor thread pool** — every
   `_mark_complete`/`_mark_retry`/`_mark_failed`/`_write_heartbeat`-adjacent
   call, `_funder_count`, `_enqueue_second_hop_lite`,
   `RiskScoringBuilder.score_creator_now`, `_rescore`,
   `assign_live_network_for_creator`, `_post_extraction_intelligence_refresh`,
   and (critically) `_save_outgoing_transfer` (dispatched from inside the
   event-loop-thread `extract_outgoing_transfers`) all land here.

`TrackedConnection`'s write-lease reentrancy guard
(`_thread_write_lease = threading.local()` in `database_write_service.py`)
is scoped per OS thread, not per coroutine/task. **Any single connection on
either pool that acquires the write lease and is never `commit()`ted,
`rollback()`ed, or `close()`d on that same thread poisons every future
write dispatched to that same thread, permanently, for the life of the
process.**

Cancellation/timeout: `_process_job` wraps the extraction call in
`asyncio.wait_for(..., timeout=JOB_TIMEOUT_SECONDS)`. No timeout messages
were observed in production logs during this investigation, so timeout
cancellation was not the trigger for the specific incident traced live —
but it remains a theoretical risk worth naming: cancellation delivered
mid-blocking-C-call (a synchronous SQLite statement) can only take effect
at the coroutine's next actual `await` point, not instantaneously.

## Phase 2 — Thread ownership

**Directly verified**: `asyncio.to_thread`'s default executor reuses the
same OS worker thread across sequential calls in a single event loop.

```python
async def main():
    for i in range(5):
        await asyncio.to_thread(lambda: print(threading.get_ident()))
asyncio.run(main())
# → same thread ID printed all 5 times
```

**Directly verified**: this reuse means thread-local write-lease ownership
survives across unrelated calls dispatched to the same reused thread. A
minimal fixture (two sequential `asyncio.to_thread` calls, the first
leaking a lease, the second attempting a clean, unrelated write) reproduces
`NestedDatabaseWriteError` on the second call — and on a third call too,
proving the poisoning is permanent for that thread, not a one-time
collision. See `tests/test_x78_0_creator_funding_lease_poisoning.py`.

**The connection reaper cannot heal this.** `db_locking.py`'s
`_reap_stale_connections()` (intended as the safety net for exactly this
class of leak) runs on its own dedicated thread (`db-conn-reaper`).
Python's `sqlite3` module defaults every connection to
`check_same_thread=True` — never overridden anywhere in this codebase — so
calling `.close()` on a connection from a different thread than the one
that created it raises `sqlite3.ProgrammingError`, which the reaper's own
generic `except Exception: pass` silently swallows. **The reaper has never
successfully force-closed a connection across a thread boundary.** This
was directly verified (`test_reaper_cannot_close_a_connection_from_a_different_thread`)
and cross-checked against production logs: zero `[DB_REAPER] force-closed`
or `[DB_REAPER] closed idle connection` lines exist anywhere in
`creator_funding_worker`'s log history.

**Explicitly out of this milestone's scope** (per the user's own framing
during this investigation): the reaper's cross-thread gap is a genuine
platform-level architectural finding, but it exists to catch leaks that
should not happen in the first place — fixing the producer (the leak
sources themselves) is the correct order, not patching the safety net
first, which would mask rather than eliminate the defect. This is recorded
as future backlog, not fixed in X78.0.

## Phase 3 — Reproduction

Deterministic, non-timing-dependent regression in
`tests/test_x78_0_creator_funding_lease_poisoning.py` (4 tests):

1. `test_asyncio_to_thread_reuses_the_same_os_thread_across_sequential_calls`
   — the foundational premise, directly verified.
2. `test_a_single_leaked_lease_poisons_every_subsequent_write_same_thread`
   — one thread, one leaked connection, two subsequent unrelated write
   attempts on the same thread both fail with `NestedDatabaseWriteError`
   (old behaviour, still present after the fix — this is intentionally
   proving the underlying mechanism is real and the guard itself must never
   be weakened, not that leaks stop being fatal once they happen).
3. `test_reaper_cannot_close_a_connection_from_a_different_thread` —
   direct proof the safety net cannot engage across threads.
4. `test_create_table_if_not_exists_still_acquires_and_can_leak_the_lease`
   — the exact real-world shape: `extract_for_creator`'s own
   `CREATE TABLE IF NOT EXISTS` block, reproduced against a poisoned
   thread, proving the pre-fix code's `except: pass` swallows the
   resulting `NestedDatabaseWriteError` and continues with a connection
   that never actually held the lease it thought it acquired — the
   self-perpetuating mechanism.

## Phase 4 — Root cause

**Why `creator_funding_worker` continuously self-nests, in order of
causal depth:**

1. **Proximate**: every single logged failure shares the identical
   `outer_command=realtime_creator_funding_extractor.py:1226 in
   extract_for_creator` — meaning ONE specific `extraction_conn` (tagged at
   `db_connect()` call time via `inspect.stack()`, permanently, for that
   connection object) has held the write lease, unreleased, since before
   the log window began. Every subsequent extraction's own attempt to
   acquire the write lease for its own `CREATE TABLE IF NOT EXISTS` check
   (`extract_for_creator`'s own "ensure tables exist" block) fails
   immediately with `NestedDatabaseWriteError`, silently swallowed by a
   blanket `except: pass`, so the function continues, eventually reaches
   its own `close()` — which is a no-op, since THIS connection's own
   `_holds_write_lock` was never set `True` (its acquisition failed) — and
   the ORIGINAL leaked owner remains held. This repeats identically on
   every cycle, forever.
2. **Systemic**: `asyncio.to_thread`'s thread-pool reuse means a leak from
   ANY write path dispatched to that pool (not just `extraction_conn`
   itself) can poison every future write on that same reused thread — this
   was directly proven as the general mechanism, independent of which
   specific call first triggered it.
3. **Why it became permanent instead of self-healing**: the connection
   reaper — the platform's own designed safety net for exactly this
   failure class — cannot cross a thread boundary to force-close a leaked
   connection, due to SQLite's `check_same_thread=True` default never being
   overridden. Confirmed via zero successful reaper log lines across this
   worker's entire log history.
4. **Contributing leak sources** (three found and fixed, in the reachable
   write-call graph of `extract_for_creator`):
   - `realtime_creator_funding_extractor.py`'s own "ensure tables exist"
     block (`extraction_conn`'s CREATE TABLE, wrapped in a swallowing
     `except: pass`, `commit()` only reached on success).
   - `solscan_address_tagger.tag_creator_with_services` — called
     synchronously from inside `extract_for_creator`'s paging loop,
     `conn.close()` only on the success path, no `finally`.
   - `blocksec_aml_batcher.BlockSecAMLBatcher._ensure_tables` — runs on
     **every single extraction cycle** (a fresh `BlockSecAMLBatcher()` is
     instantiated per call in `auto_batch_new_addresses()`), `conn.close()`
     only on the success path, no `finally`.

   All three share the exact contract violation this session's X77.x
   milestones already established as the platform's recurring failure
   pattern: **`TrackedConnection`'s write lease requires an explicit
   `commit()`, `rollback()`, or `close()` on every code path, or it leaks
   for the rest of that thread's life.**

**The exact original trigger** (which of these three, or some other cause
entirely, fired first on the specific thread that has been poisoned since
before this investigation's log window began) could not be pinpointed with
certainty — the process has been running continuously for 19+ hours and
the log window does not extend back to the true first occurrence. This
does not weaken the fix: all three confirmed leak sources are eliminated
regardless of which one fired first, and the underlying mechanism (any one
of them, on a reused thread, poisons that thread permanently) is fully
proven and now closed off at every reachable point.

**Why the self-kill guard never fired**: `_check_self_kill(pending)` only
exits on `uptime_h >= MAX_UPTIME_HOURS and pending == 0` (idle-only clean
restart) or `handles > MAX_OPEN_HANDLES` (a raw open-file-descriptor
count). A permanently write-poisoned thread does not leak file descriptors
(the connections that fail to acquire the lease are still `close()`d, just
with nothing to release) and does not make the queue idle (jobs keep
failing back to `retry`/`failed`, never draining to zero) — so neither
guard condition was ever met. This gap is recorded as technical debt, not
fixed in X78.0 (see below).

## Phase 5 — Repair

**Repaired, in the reachable write-call graph of `extract_for_creator`
(3 files, all directly called from `extract_for_creator`'s hot path):**

1. **`src/extractors/realtime_creator_funding_extractor.py`** —
   `extract_for_creator`'s "ensure tables exist" block now checks
   `sqlite_master` for `creator_service_history`/`creator_receivers`
   before attempting any `CREATE TABLE`, so a fully-migrated database (true
   after the very first extraction ever) issues zero write statements
   here — eliminating the specific block whose swallowed
   `NestedDatabaseWriteError` was the proximate, directly-observed cause of
   every single production failure trace.
2. **`src/utils/solscan_address_tagger.py`** — `tag_creator_with_services`
   now declares `conn = None` before its `try`, closes it in a `finally`
   regardless of which statement raised. Called synchronously from inside
   `extract_for_creator`'s own paging loop, on the event-loop thread.
3. **`src/monitoring/blocksec_aml_batcher.py`** —
   `BlockSecAMLBatcher._ensure_tables` (runs on every single extraction
   cycle, not just once) now both checks `sqlite_master` first (matching
   fix #1's shape) and closes its connection in a `finally`. Reached via
   `_try_blocksec_batch`'s fire-and-forget background task, spawned inside
   `extract_for_creator`.

**Not weakened**: `TrackedConnection`, `DatabaseWriteService`, the
write-lane guard, and nested-write detection are all untouched. No
threshold was raised. No protection was disabled. The fix is exclusively:
never attempt a write statement that would fail on an already-migrated
schema, and guarantee `close()` on every remaining code path in the two
newly-hardened functions.

**Confirmed unreachable, not fixed (dead code, no live risk)**:
`check_transactions_for_meteora_programs` in
`realtime_creator_funding_extractor.py` shares the same
no-`finally`-on-early-exception shape but is never called from
`extract_for_creator`'s or `extract_funding_for_new_token`'s call graph
(verified via `grep`). `_tag_creator_from_funding_patterns` in
`post_launch_automation.py` is permanently unreachable (`return False` as
its first statement, explicitly labeled "PERMANENTLY DISABLED... LEGACY
CODE BELOW - UNREACHABLE" in its own docstring). Neither was touched.

**Confirmed already safe (no fix needed)**: `_save_outgoing_transfer`,
`check_create_tx_for_jitotip`, `check_transfers_for_debridge`,
`check_transfers_for_axiom`, and every write path in
`post_launch_automation.py` already use `managed_db_connect` (a
context-manager guaranteeing `close()` on every exit), hardened by X76.3
earlier this year. `creator_funding_worker.py` itself was independently
audited line-by-line — every one of its own `db_connect`/`_db_connect`
calls already has a matching `finally` block; no leak originates in the
worker's own code.

## Phase 6 — Transaction integrity

Verified across all three fixed functions (and re-confirmed for the four
already-safe `managed_db_connect` call sites, read during this audit):

| Property | Status |
|---|---|
| Every successful write → commit | ✅ (all three fixes: `conn.commit()` only reached when a write actually happened, immediately after) |
| Every failed write → the connection is still closed | ✅ (all three fixes: `finally: conn.close()`, or in fix #1's case, no write is ever attempted against an already-migrated schema, so there's nothing to fail) |
| Every connection → closed | ✅ (proven directly: `test_tag_creator_with_services_closes_connection_on_exception`, `test_blocksec_ensure_tables_closes_connection_on_exception`) |
| Every task → cleanup | ✅ unchanged — `_spawn_background_task`'s `add_done_callback(self._background_tasks.discard)` and `wait_for_background_tasks()`'s bounded wait were already correct (X76.3), not touched |

## Regression

9 new tests, all passing:
`tests/test_x78_0_creator_funding_lease_poisoning.py` (4) — deterministic
reproduction of the full mechanism.
`tests/test_x78_0_leak_source_fixes.py` (4) — proves each of the three
fixes closes its specific leak.

Existing suite (unchanged behaviour confirmed):
`tests/test_incremental_extraction.py`, `tests/test_phase1_monitoring.py`,
`tests/test_phase1_with_env.py`, `tests/test_x76_3_extractor_concurrency.py`
— all passing (see this document's own commit for the exact run).

No attribution, reconciliation, or resolver logic changed. No behavior
changed for a healthy (non-poisoned) run — the three fixes are pure
transaction-hygiene corrections; a database that already has the relevant
tables (true for every production database except a brand-new empty one)
sees byte-identical outcomes, only fewer (zero, in the common case) write
statements attempted.

## Phase 7/8/9 — Runtime validation, platform validation, readiness reassessment

Pending: requires deploying the fix and running the required 60-minute
(preferably 2-hour) soak per the X78.0 spec, alongside `walkback_worker`,
`ws_cascade`, Mission Control, Discovery, and Treasury Review, before the
platform-wide readiness verdict can be updated. This document will be
updated (or a follow-up X78.0 addendum committed) once that soak completes.

## Technical debt identified this milestone (not fixed — explicitly out of scope)

1. **Connection reaper cannot close cross-thread leaks** — `db_locking.py`'s
   `_reap_stale_connections()` runs on a dedicated thread; every SQLite
   connection in this codebase defaults to `check_same_thread=True`
   (never overridden), so the reaper's force-close attempts always raise
   `sqlite3.ProgrammingError`, silently swallowed. The reaper has never
   successfully reaped a connection across a thread boundary in this
   worker's history. A genuine platform-level architectural gap — fixing
   it (e.g. `check_same_thread=False` plus appropriate locking, or a
   redesigned thread-safe connection lifecycle) is future work, deliberately
   deferred until it's known whether anything still leaks after this
   milestone's producer-side fixes.
2. **`creator_funding_worker`'s self-kill guard has no path for "stuck on
   every cycle but not idle and not leaking handles"** — `_check_self_kill`
   only guards handle-count leaks and idle-uptime clean restarts. A
   permanently write-poisoned thread (jobs continuously failing back to
   retry/failed, never idle, no handle leak since failed connections still
   close cleanly) was invisible to both guard conditions for this worker's
   entire 19+ hour incident. Worth a dedicated guard (e.g. a
   nested-write-error rate threshold) in a future milestone if leaks are
   still possible after this one.
3. **`check_transactions_for_meteora_programs`** (dead code,
   `realtime_creator_funding_extractor.py`) and
   **`_tag_creator_from_funding_patterns`** (dead code,
   `post_launch_automation.py`) both share the same no-`finally` leak shape
   but are unreachable — noted for cleanup if either is ever
   re-activated, not fixed now (no live risk).

[x78_0_creator_funding_concurrency.md](docs/audits/x78_0_creator_funding_concurrency.md)
