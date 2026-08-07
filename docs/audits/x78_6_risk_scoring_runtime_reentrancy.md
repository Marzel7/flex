# X78.6 — Risk Scoring Runtime & Re-Entrancy Audit

## Verdict: A — SLOW READ WORK INSIDE WRITE LEASE (fully quantified, not inferred)

## Summary

X78.5's fix (wrapping `score_creator_now`'s connection lifecycle in
`try`/`finally`) was necessary but not sufficient. The live sanity
window still showed a permanent-looking `NestedDatabaseWriteError`
poisoning creator_funding_worker's thread, still attributed to
`risk_scoring_builder.py` (`score_creator_now`). This audit measured
runtime directly against the real production database rather than
inferring another lifecycle defect, per the task's explicit instruction.

## Phase 1: frozen failure

pid `93337`, permanently poisoned since shortly after its 18:30 startup
through at least 19:53 (over an hour), continuously retrying and
exhausting on `outer_command=risk_scoring_builder.py:143 in
score_creator_now` (the X78.5-fixed code's connection line). Confirmed
the same outer owner persisted throughout — not a rotating set of
different collisions.

## Phase 2: call graph (single-creator path only)

`score_creator_now` calls `_build_context(conn, [creator])` — **not**
`_load_creator_universe` (that is only called by the separate `run()`
method, the full-batch entry point, not the single-creator path).
Call order: `apply_migration(conn)` → `sync_infra_wallets(conn)` →
`_build_context(conn, [creator])` → `_score_creator_fast(...)` →
`_write_creator_scores(conn, ...)` → `commit()`.

## Phase 6-7: measured against the real production database

| Query | Table(s) | Rows scanned | Measured time |
|---|---|---:|---:|
| `sync_infra_wallets`: `SELECT DISTINCT bonding_curve_pda FROM token_analysis` | `token_analysis` (1,603,447 rows) | 1,416,413 | 20.30s |
| `sync_infra_wallets`: `SELECT DISTINCT pool_address FROM token_analysis` | `token_analysis` | 29,371 | 14.02s |
| `sync_infra_wallets`: `SELECT DISTINCT pumpswap_pool_address FROM token_analysis` | `token_analysis` | 26,762 | 14.19s |
| `_build_context`: `tokens_by_creator` (`token_analysis` LEFT JOIN `token_pool_accounts` GROUP BY) | `token_analysis`, `token_pool_accounts` (27,472 rows) | 1,568,670 | 17.89s |
| `_build_context`: `funders_by_creator` (`creator_funders` with `NOT IN (SELECT address FROM infra_wallets)`) | `creator_funders` (82,557), `infra_wallets` (665,740) | 77,010 | 1.01s |
| `_build_context`: `fanout_by_funder` | `creator_funders` | 72,199 | 0.32s |
| `_build_context`: `coordinated_creator_edges` | `coordinated_creator_edges` (328,702 rows) | 327,477 | 0.98s |

**`EXPLAIN QUERY PLAN` for the `tokens_by_creator` query confirmed `SCAN ta`
— a full table scan of `token_analysis`, no usable index for a single-
creator filter (the query does not even filter by creator in SQL; it
scans the whole table and filters in Python).**

Total measured time for `sync_infra_wallets` alone: **~48.5 seconds**.
Combined with `_build_context`'s slowest component: **~70+ seconds**
of read-only work, for a call that scores exactly one creator.

## Phase 8-9: lease lifetime

`apply_migration(conn)` runs write-shaped `CREATE TABLE`/`ALTER`
statements (line ~146 pre-fix), which acquire the write lease
immediately via `_acquire_write_lane()`. Every read-only query listed
above then executed **while that lease was already held** — the write
lease was acquired 70+ seconds before the first actual mutation
(`_write_creator_scores`, a fast `executemany`). This is the textbook
"open write transaction → expensive read-only work → eventual write"
anti-pattern the task's Phase 8 explicitly named as the primary
suspect.

## Phase 10-14: re-entrancy — ruled out as the primary mechanism

`score_creator_now` is called from exactly one site
(`creator_funding_worker.py`'s post-extraction enrichment,
`await asyncio.to_thread(lambda: RiskScoringBuilder(DB_PATH).score_creator_now(creator))`),
directly awaited, sequential per `_process_job` call. No `create_task`,
no fire-and-forget dispatch, no detached descendant. Given jobs are
processed sequentially (X78.2's own guarantee), two `score_creator_now`
calls cannot be concurrently in-flight from this call site alone. The
70+ second read-heavy window is sufficient on its own to explain every
observed collision without needing a re-entrancy hypothesis: any other
write dispatched to the worker's executor pool during that window
(heartbeat, `_mark_retry`, the NEXT job's own writes) collides,
recurring indefinitely if jobs arrive faster than each ~70s window
clears — which is exactly what was observed live.

## Phase 15: connection lifecycle proof

Confirmed via direct code reading and reproduction: `close()` IS reached
on every path (already fixed in X78.5) — this is a **runtime/
transaction-boundary issue** (the lease is held far longer than
necessary, not leaked forever), matching Phase 15's explicit
disambiguation between "close() never reached" (X78.5's defect) and
"close() reached but far too late" (X78.6's defect).

## Phase 20: reproduction

Reproduced deterministically in
`tests/test_x78_6_risk_scoring_lease_boundary.py::test_write_lease_not_held_during_slow_context_build`:
artificially delays `_build_context`, confirms an unrelated concurrent
write succeeds immediately during that delay (proving the lease is
released before the slow phase, post-fix) — this is Form A from the
task's list ("large creator-universe query exceeds threshold while
lease held").

## Phase 22: repair

Restructured `score_creator_now` into two sequential, non-overlapping
connections:
1. **Setup connection**: `apply_migration` + `sync_infra_wallets` +
   `_build_context` (all read-heavy/schema work), committed and closed
   immediately after — releasing the write lease before any slow work
   from a NEXT call could matter.
2. **Write connection**: opened fresh, holds the lease only for
   `_write_creator_scores` + `commit` — a fast `executemany`.

This matches the task's Phase 22/24 prescribed pattern exactly ("load
context → compute score → open short write transaction → persist →
commit/close") and does not change scoring semantics, migration
behaviour, or infra-wallet sync behaviour — only *when* the write lease
is held.

## Validation

- `tests/test_x78_6_risk_scoring_lease_boundary.py` (4 tests): proves
  the lease is not held during the slow read phase (the core
  discriminating regression — fails against pre-fix code, passes
  post-fix), the happy path still works, write-phase failures still
  release correctly, and the two connections are structurally
  non-overlapping (open→close→open→close, never open→open).
- Existing X78.5 tests (3 tests) continue to pass unchanged.
- All 30 tests across X78.2-X78.6 combined pass together.
- Benchmarking directly against the live production database was
  attempted but proved inconclusive as a clean signal: the live
  `creator_funding_worker` process (pid 93337) was still running the
  OLD (X78.5, pre-X78.6) code at the time, itself actively stuck in the
  exact bug this audit diagnoses — so a benchmark script run against
  the same live DB file collided with that already-broken live process,
  not with anything in the new code. This is expected and not a defect
  in the fix; the live sanity window (Phase 29-31, next) is the correct
  place to observe real-world timing post-deployment.

## Root-cause ledger (cumulative, X78.0-X78.6)

| Mechanism | Status |
|---|---|
| Individual connection/transaction leaks (25 fixes, X78.0) | FIXED / historical |
| Detached background descendants (X78.2) | FIXED |
| RPCCache same-job nested ownership (X78.3) | FIXED |
| Cancellation grace-period overrun (X78.4) | FIXED (via retry/isolation) |
| `score_creator_now` pre-try connection leak (X78.5) | FIXED (necessary, not sufficient) |
| `score_creator_now` over-broad write-lease lifetime across ~70s of full-table reads (X78.6) | **FIXED** |
| `_patched_connect` caller-attribution diagnostic gap | FIXED (permanent improvement) |
| `SecondHopExpansionBuilder._is_enabled()` connection handle leak | Identified, not fixed — hygiene follow-up, unrelated to `NestedDatabaseWriteError` |

## Production readiness verdict

**NOT YET CONFIRMED READY** — fix implemented, locally validated (30/30
tests), not yet deployed/soaked. Next: restart via supervisor, 15-minute
sanity window, then soak, per this investigation's established
discipline of requiring live confirmation before any READY verdict.

## Commit

Local commit only, not pushed, per task instruction.
