# X78.8 — Infrastructure Sync Hot-Path Separation & Creator Funding Closure

## Part A: ownership audit

### Phase 1-2: `sync_infra_wallets`'s exact contract

Reads static in-process registries (`INFRASTRUCTURE_ACCOUNTS`/`CEX_ACCOUNTS`,
instant) plus small observed tables (`infra_funders_observed`, `cex_wallets`,
cheap) plus three full `SELECT DISTINCT` scans of
`token_analysis.bonding_curve_pda`/`pool_address`/`pumpswap_pool_address`
(~1.4-1.6M rows each). Writes `INSERT ... ON CONFLICT DO UPDATE` (upsert,
idempotent) into `infra_wallets`, one row per address regardless of
whether the upsert changed anything. **Commits nothing itself** — the
caller commits. Source classification: the `token_analysis`-derived
portion is **APPEND_ONLY** (new bonding curves/pools appear as tokens
launch; existing values never change or get removed); the static/CEX
portions are effectively static per-process or small-table-mutable.

### Phase 4: complete caller census

15 call sites across the codebase (`second_hop_builder.py`,
`risk_scoring_builder.py` ×2, `graph_dev_farm_detection.py`,
`graph_analyzer_api.py`, `creator_outbound_builder.py` ×2,
`second_hop_lite_worker.py`, `token_prediction_builder.py`,
`intelligence_refresh.py`, `upstream_expansion_builder.py`,
`wallet_clustering.py`, `network_membership_builder.py` ×2,
`profitability_intelligence.py` ×3, `funder_overlap_analysis.py`).
**Every single caller** calls it synchronously, before reading
`infra_wallets` as a pure exclusion set, with no freshness check against
a timestamp and no rejection of stale data. Classification: **all 15+
callers are `CAN_USE_LAST_SUCCESSFUL_STATE`** — none require
transaction-current freshness.

### Phase 5-6: does scoring output actually depend on sync freshness?

`_score_creator_fast` only consumes `infra_wallets` indirectly, via
`_build_context_for_creator`'s `NOT IN (SELECT address FROM infra_wallets)`
exclusions. Since the dynamic portion is append-only, staleness means at
worst a brand-new infra wallet is briefly treated as a non-infra funder
until the next refresh — a bounded, self-correcting classification lag,
not a correctness break. This matches every other caller's tolerance
exactly.

### Phase 7: why the X78.7 debounce didn't solve the problem

X78.7's `SYNC_INFRA_WALLETS_DEBOUNCE_SEC=300` reduced how *often*
`sync_infra_wallets` ran inside `score_creator_now`, but not its
per-call *cost* when the window was cold. Given
`creator_funding_worker`'s real job cadence (~15-20 minutes per
completed job under RPC-bound extraction load, confirmed via live log
observation across X78.6-X78.8's soak windows), the 300s window
routinely elapsed between calls, so a "debounced" call was frequently
still a cold, full-cost call. Live evidence: `risk_scoring_builder.py`
remained a frequent `NestedDatabaseWriteError` outer owner even after
X78.7 (43+ occurrences in a 24-minute window, no material reduction from
pre-X78.7 levels).

### An investigative accident, disclosed directly

While benchmarking `sync_infra_wallets`' isolated cost, a benchmark
script run directly against the **live production database** became
blocked in uninterruptible sleep (`UN` state) on real lock contention
with the live `creator_funding_worker` process, and was left running
for several minutes before being noticed. In that window,
`creator_funding_worker` crash-looped twice (`exit status 1; not
expected`, pids `21116`→`21252`). Once the stuck script was killed, the
worker stabilized immediately and remained stable (17+ minutes, no
further crashes, confirmed before this doc was written). This was
almost certainly the benchmark script itself contributing to or causing
the crash-loop, not a new defect in the worker. No further blocking
benchmarks were run against the live DB for the remainder of this
investigation; all further measurement relies on the existing X78.6
isolated measurement (~48s combined) and passive live-log observation
only.

## Part C: ownership design

### Phase 12: options evaluated

- A (per-creator synchronous) — pre-X78.7, proven broken.
- B (longer debounce) — explicitly disallowed by this task's own
  instructions; also doesn't address root cause (per-call cost).
- C (once per worker cycle) — improvement, but `infra_wallets` is
  consumed by 14 OTHER callers across the codebase, not just
  `creator_funding_worker`; coupling ownership to one specific worker's
  loop doesn't fix the systemic pattern.
- D (worker startup + periodic refresh) — same objection as C.
- **E (existing scheduler / supervised periodic job) — chosen.** This
  codebase already has a proven precedent for exactly this shape:
  `intelligence_snapshot_scheduler` (X67.28) — "a slow, shared,
  periodic-refresh build extracted into its own supervised process,
  independent of any single worker's request/job lifecycle" — and
  `operation_scheduler`, both using an identical PID-liveness-checked
  single-flight lock file pattern. `infra_wallets` is genuinely global,
  shared, slowly-changing state; it belongs in this same architectural
  category, not owned by any one of its 15 consumers.
- F (incremental refresh) — not required; Phase 11 did not need to be
  exercised since the append-only nature already makes a full periodic
  rescan cheap enough at a 10-minute cadence, and Phase 26 (further
  query optimization) is explicitly out of scope unless still necessary
  after the lifecycle separation.

### Phase 14: chosen owner

A new standalone process, `src.core.infra_sync_scheduler`, following
`intelligence_snapshot_scheduler`'s exact architecture (not reusing
`operation_scheduler`/`intelligence_snapshot_scheduler` directly, since
both are scoped to different databases — `wt_ops_v2.db` and snapshot
files respectively — while `infra_wallets` lives in the main flex DB
that every one of the 15 callers shares).

### Phase 15: single-flight

Reused the identical `acquire_lock`/`release_lock` pattern from
`operation_scheduler.py` (PID-liveness check via `os.kill(pid, 0)`,
stale-lock reclamation) as a local copy — matching this codebase's own
established convention (per `intelligence_snapshot_scheduler`'s own
comment) of small, independent, single-purpose standalone processes
with no cross-import coupling between schedulers.

### Phase 17-18: failure and staleness contract

New `infra_wallets_sync_status` table (`last_attempt_at`,
`last_success_at`, `last_duration_ms`, `last_status`, `last_error`,
`rows_processed`), a single row (`id=1`), upserted by the scheduler
after every attempt. A failed refresh is caught, logged, and recorded —
**never re-raised** — so a broken refresh cannot crash or crash-loop any
consumer; the last successful state remains untouched
(`ON CONFLICT` preserves `last_success_at`/`rows_processed` from the
prior success on a failed attempt, verified in
`test_infra_sync_scheduler_status_reflects_failure`). `get_status()`
classifies health as `healthy`/`stale`/`failed`/`never_succeeded` for
external observability (Phase 37's Mission Control hook — not
implemented this pass, deferred as an operational nice-to-have; the
status table itself is the minimum viable observability surface Phase
18 required).

### Phase 21: cadence

600 seconds (10 minutes), derived from evidence, not carried over from
X78.7's 300s: the source data is continuously but not violently
changing (new launches, not a bursty batch), every consumer already
tolerates eventual consistency with no stated tighter requirement, and
the full scan itself is expensive enough (~48s+) that running it more
than ~6x/hour has no freshness benefit proportional to its cost.
`MAX_ACCEPTABLE_AGE_SEC=1800` (30 min) before staleness classification,
looser than the refresh interval per the same relationship
`intelligence_snapshot_scheduler` uses between its own
`REFRESH_INTERVAL_SEC` and `MAX_ACCEPTABLE_AGE_SEC`.

## Part D: implementation

### Phase 19-20: hot-path removal

`RiskScoringBuilder.score_creator_now` no longer calls
`sync_infra_wallets` under any condition (removing X78.7's debounce
entirely, per Phase 22's explicit instruction not to leave confusing
duplicate ownership). It still calls `ensure_infra_wallets_table` — a
correctness precondition (so `_build_context_for_creator`'s `NOT IN`
subqueries never fail on a freshly-created database), not a freshness
operation. `run()` (full-batch scoring) is completely unaffected — it
still calls `sync_infra_wallets` directly, since a full batch
legitimately benefits from a fresh sync and is not on the same
per-creator hot path.

## Validation

- `tests/test_x78_8_infra_sync_separation.py` (8 tests): confirms
  `score_creator_now` never calls `sync_infra_wallets` (0 calls across 3
  consecutive invocations), still succeeds on a table-less fresh
  database, correctly reads whatever state a prior sync persisted, the
  scheduler's `run_once`/`get_status` correctly persist and report
  success/failure, a failure never raises and never clobbers the prior
  success's state, and the single-flight lock correctly reclaims a dead
  owner while blocking a genuinely live one.
- Removed two now-obsolete X78.7 debounce tests
  (`test_sync_infra_wallets_debounced_across_score_creator_now_calls`,
  `test_sync_infra_wallets_runs_again_after_debounce_window_expires`)
  since the mechanism they tested no longer exists; the equivalent
  current behavior is covered by the new X78.8 test file.
- All 41 tests across X78.2-X78.8 combined pass together in one run.
- **All tests use isolated `tmp_path` databases** — none touch the live
  production DB, per the lesson from this investigation's own
  benchmarking accident.
- `git diff` scoped to `risk_scoring_builder.py` (hot-path removal),
  new `infra_sync_scheduler.py`, `supervisord.conf` (new program entry,
  `autostart=false`), and test files. No scoring semantics, thresholds,
  or feature definitions changed.

## Deployment status

Pending — see readiness verdict below. The new scheduler is
`autostart=false` (matching `intelligence_snapshot_scheduler`'s own
precedent of not auto-launching a new supervised process without an
explicit, deliberate start after config validation). `creator_funding_worker`
itself needs a restart to pick up the `score_creator_now` change.

## Root-cause ledger (cumulative, X78.0-X78.8)

| # | Mechanism | Status |
|---|---|---|
| 1 | Individual connection/transaction leaks (X78.0) | FIXED |
| 2 | `asyncio.to_thread` executor-thread-pool reuse amplification | HISTORICAL |
| 3 | Primary extraction cancellation cleanup ordering (X78.0) | FIXED |
| 4 | Detached background descendants (X78.2) | FIXED |
| 5 | RPCCache same-job nested ownership (X78.3) | FIXED |
| 6 | Cancellation grace-period overrun (X78.4) | FIXED (retry/isolation) |
| 7 | `score_creator_now` raw connection attribution + pre-try leak (X78.5) | FIXED |
| 8 | `score_creator_now` over-broad write-lease lifetime (X78.6) | FIXED (boundary corrected) |
| 9 | `score_creator_now` slow full-table setup queries (X78.7) | FIXED (query performance, partial win) |
| 10 | `score_creator_now` performing ecosystem-wide infra sync in the per-creator hot path (X78.8) | **FIXED** (ownership moved to standalone scheduler) |
| 11 | `SecondHopExpansionBuilder._is_enabled()` connection handle leak | OPEN — hygiene follow-up, unrelated to `NestedDatabaseWriteError` |

## Commit

Local commit only, not pushed, per task instruction.
