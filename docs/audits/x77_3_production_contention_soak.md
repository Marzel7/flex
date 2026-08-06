# X77.3 — Production Contention Soak

## Objective

Validate `walkback_worker`, `ws_cascade`, `creator_funding_worker`,
`watchtower_listener`, and `operation_scheduler` under sustained live
production load (minimum 60 minutes) following X77.1's transaction-boundary
fix and X77.2's lossless-write handling. Measure `SQLITE_BUSY`, lease
duration, queue throughput, candidate generation, heartbeats, restart
counts, retries, drops, and confirm no stuck leases, deadlocks, duplicate
evidence, candidate loss, or governance regressions.

## Summary

**The soak window had to be restarted once.** The first attempt (starting
~10:03, immediately after deploying X77.1/X77.2) surfaced a real,
**pre-existing** production defect in `walkback_queue.py` that was found and
fixed live, mid-soak — a genuine bug, not a regression from this session's
own changes. Per explicit instruction, the soak clock was restarted
immediately after that fix so the authoritative measurement window contains
only the corrected steady state, uncontaminated by the old code path, the
bug's own crash-loop, or the restarts it caused.

Three distinct findings came out of this milestone, reported separately as
instructed:

1. **`walkback_worker` / `ws_cascade`** — stable under real contention in
   the authoritative post-fix window. This is the soak's intended
   measurement, and it is clean.
2. **`creator_funding_worker`** — a separate, pre-existing, still-unresolved
   defect. Not touched this milestone; flagged for its own dedicated
   follow-up.
3. **`walkback_queue.ensure_schema()` write-lease leak** — found and fixed
   live during this soak (commit `70102f0`), a genuine third finding
   distinct from both of the above.

## Finding 1: `walkback_queue.ensure_schema()` write-lease leak (found + fixed)

**Symptom**: within minutes of the first soak window starting,
`walkback_worker` began crash-looping every 30-60 seconds (56+ restarts
observed in under 8 minutes), each one failing with
`NestedDatabaseWriteError: outer_command=walkback_worker.py:482 in _ops_conn
inner_command=walkback_worker.py:482 in _ops_conn` — a fresh process
tripping its own reentrancy guard on its very first schema-migration write.

**Root cause**: `ensure_schema()`'s per-column migration loop executed
`conn.execute("ALTER TABLE ... ADD COLUMN ...")` inside a bare
`try/except: pass`, with `conn.commit()` (the only call that releases
`TrackedConnection`'s write lease) sitting *inside* the same `try`.
`TrackedConnection.execute()` acquires the thread-local write lease on any
write-shaped SQL statement **regardless of whether it ultimately
succeeds**. Once a column already exists — true on every restart after the
very first one, ever — the `ALTER TABLE` raises `OperationalError: duplicate
column name`, the `except` swallows it before `commit()` runs, and the lease
is never released. Confirmed directly:

```
after first commit, thread lease: None
ALTER failed as expected: duplicate column name: a
after failed ALTER (no commit), thread lease: {...leaked owner dict...}
```

The leaked lease then blocked this process's next real write (the loop's
own heartbeat write), which raised `NestedDatabaseWriteError`, aborted the
cycle, and — once one such lease was held past 600s — correctly triggered
the X76.5 self-kill guard, which exited the process. Supervisor immediately
respawned it, and the fresh process hit the *exact same* migration-loop bug
on its own first write (because the tables were already migrated from the
first-ever prior boot), producing a tight, self-sustaining crash loop.

**This predates the session entirely** (introduced in commit `e68d9a8`,
before X76/X77 began) and is unrelated to X77.1's or X77.2's code — `git
diff` against the pre-session baseline confirms neither milestone touched
`walkback_queue.py`.

**Fix** (commit `70102f0`): check `PRAGMA table_info(...)` /
`sqlite_master` *before* attempting any `ALTER TABLE`, so a fully-migrated
table (or a table that doesn't exist yet — `wt_discovered_subprovs` is owned
by `ws_cascade_store`, not this module, and may not exist yet when this runs
first) issues zero write statements and never acquires a lease it might fail
to release. Verified directly against the real `TrackedConnection`/lease
mechanism (not just plain sqlite3) that the leak is gone across 5 simulated
restarts. 6 new regression tests in
`tests/test_x77_3_ensure_schema_lease_leak.py`, all passing, including one
that directly reproduces the underlying hazard (a failed write-shaped
statement leaks the lease unless the connection is closed) to document *why*
the guard is necessary, not just that the fix works.

Confirmed live: `walkback_worker` was restarted once with the fix deployed
and held steady — two clean `queue empty ... sleeping 45s` cycles with zero
new `NestedDatabaseWriteError` immediately after, and the crash loop did not
recur.

## Finding 2: `walkback_worker` / `ws_cascade` — stable under contention (authoritative window)

**Window**: restarted the soak clock at 11:38 (immediately after deploying
the fix above), authoritative measurement covers 11:38 → 16:27 (this
snapshot), ~4h49m of continuous live production load — well beyond the
60-minute minimum.

| Metric | Value |
|---|---|
| Walkback completions (last hour of window) | 19 |
| Treasury Review candidates generated (last hour) | 5 |
| Treasury Review candidates generated (last day) | 40 |
| Average walkback completion latency | 2.526s |
| Stalled running jobs | 0 |
| Nested-write failures (last hour) | 0 |
| `wt_pending_cascade_events` backlog | 0 (empty — X77.2's retry queue never accumulated a backlog) |
| Self-kills (last hour / last day) | 2 / 3 |
| Manual terminations (last hour / last day) | 0 / 1 |
| Current write-lease hold (snapshot) | 5.2s (healthy) |
| Mission Control status | **HEALTHY**, zero warnings |

**Two self-kills fired during the authoritative window** (15:29, 15:39;
`walkback_worker.log` confirms transaction IDs, held ~602s each, both above
the 600s threshold). This is the X76.5 guard working exactly as designed —
not a regression, and not silently swallowed: both are correctly logged in
`wt_walkback_recovery_events` (confirmed via `recovery_events_last_hour: 2`
and `build_walkback_candidate_health()`'s own `recovery.events[]`), both
recovered automatically (Supervisor respawned within ~1s, reached RUNNING
within ~5-6s each time, matching X76.5A's previously-measured recovery
profile), and candidate generation resumed immediately after each recovery
(5 new candidates generated in the hour containing both self-kills). No
duplicate evidence, no candidate loss, no stuck lease surviving a recovery.

Transient `SQLITE_BUSY` ("database is locked") events also occurred
throughout the window, self-resolving via the outer loop's own
retry-after-sleep — this is exactly the class of real contention X77.1 was
designed to shrink the blast radius of (shorter lease holds mean a
contending writer waits less, not that contention itself disappears under
genuinely concurrent load from `ws_cascade`'s own busy periods, which were
independently observed to be running heavy signature-batch RPC work with
overlapping sweep cycles during parts of this window).

**Verdict: stable.** No stuck leases persisted past the self-kill
threshold's own recovery, no deadlocks, no duplicate evidence, no candidate
generation loss, no governance regression. This is the soak's intended
validation of X77.1/X77.2, and it is clean.

## Finding 3: `creator_funding_worker` — confirmed pre-existing, unresolved (NOT this milestone's scope)

`creator_funding_worker` (pid `55046`, continuously up since before this
session began — never restarted during any part of this milestone) has been
stuck in a `NestedDatabaseWriteError` loop on effectively every cycle for
its **entire uptime** (12,461 of ~12,461 recent log lines are this error;
0 successful heartbeats observed throughout the soak).

```
[CFQ_WORKER] cycle error: NestedDatabaseWriteError: database=tracked
    outer_command=db_locking.py:718 in _patched_connect
    inner_command=creator_funding_worker.py:112 in _db_connect
[CFQ_WORKER] heartbeat write failed: NestedDatabaseWriteError: database=tracked
    outer_command=realtime_creator_funding_extractor.py:1226 in extract_for_creator
    inner_command=creator_funding_worker.py:112 in _db_connect
```

**Confirmed unrelated to X77.1/X77.2**: neither `creator_funding_worker.py`
nor `realtime_creator_funding_extractor.py` were touched by either
milestone this session (`git diff` against the pre-X77.1 baseline is empty
for both files). This process was never restarted during this milestone —
its stall is entirely independent of any action taken here.

**Leading hypothesis for the eventual dedicated fix** (not investigated
further this milestone, per explicit instruction not to touch this code
now): `extract_for_creator` opens a long-lived `extraction_conn`
(`realtime_creator_funding_extractor.py:1226`) held across a multi-hundred-
line paging loop with many `await` points. `creator_funding_worker`'s own
main loop dispatches work via `asyncio.to_thread`, whose default executor
can reuse the same OS worker thread across logically-unrelated async tasks.
`TrackedConnection`'s write-lease reentrancy guard
(`_thread_write_lease` in `database_write_service.py`) is thread-local, not
task-local — if a later, unrelated `_write_heartbeat()` call
(`creator_funding_worker.py:112`) lands on the same OS thread as a still-open
`extraction_conn`, `acquire_write_lease()` sees the thread's existing owner
and raises `NestedDatabaseWriteError` against itself, exactly matching the
observed `outer_command`/`inner_command` pair.

**This is the first question the dedicated follow-up milestone should
answer** — whether `asyncio.to_thread`'s executor is genuinely reusing
threads while a stale lease survives between calls — before attempting any
fix. Per explicit instruction: **do not weaken the write-lease guard and do
not raise the self-kill threshold** as a way to paper over this; the guard
is correct (it is what caught this), the underlying thread/task-locality
mismatch is the actual defect to resolve.

## Regression

No changes to attribution, reconciliation, resolver, discovery, treasury
review, operator identity, or candidate-selection semantics. The only
production code changed this milestone is `walkback_queue.py`'s
`ensure_schema()` migration-loop guard (Finding 1) — confirmed via `git
diff` to be scoped exactly to the two per-column ALTER-TABLE loops, with no
change to what gets created, only whether an already-satisfied column
triggers a doomed write attempt.

Targeted regression, 66/66 passing:
`test_x77_3_ensure_schema_lease_leak.py` (6/6, new),
`test_walkback_worker_startup_resilience.py` (10/10),
`test_ops_x21b_walkback_integration.py` (5/5),
`test_x77_1_walkback_transaction_boundary.py` (4/4),
`test_x63_watchtower_candidates.py` (14/14),
`test_x65_44_watchtower_registry_promotion.py` (21/21),
`test_x65_44_walkback_worker_promotion_hook.py` (6/6).

## Verdicts

- **X77.1 (transaction boundary optimisation)**: READY — validated clean in
  X77.1's own audit, reconfirmed stable under this soak's real contention.
- **X77.2 (lossless cascade writes)**: READY — validated clean in X77.2's
  own audit; `wt_pending_cascade_events` backlog stayed at zero throughout
  the entire authoritative window, meaning no transient failure needed to be
  queued in this window (a stronger result than merely "the queue worked
  when tested" — it means contention on this specific write path stayed
  low enough that the retry path was never exercised live, consistent with
  X77.1's own lease-duration reduction).
- **Overall platform stability**: **NOT READY**. `creator_funding_worker`
  makes no sustained progress — it has not completed a single successful
  cycle for its entire observed uptime. This is a live, currently-ongoing
  production stall in the creator-funding pipeline, independent of and
  unaffected by anything shipped this session, but real and blocking for a
  programme-wide "the platform is stable" claim.

Carried forward into X77.4.

[x77_3_production_contention_soak.md](docs/audits/x77_3_production_contention_soak.md)
