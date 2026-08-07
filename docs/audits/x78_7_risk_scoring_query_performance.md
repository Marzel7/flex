# X78.7 — Risk Scoring Query Performance & Production Soak Closure

## Part A: X78.6 production validation

45-minute observation window, pid `8006` (single restart, no crash-loop
since):

- No self-kills, no crash-loop restarts (first time in this entire
  investigation).
- At least one full extraction + risk-score cycle completed successfully.
- `risk_scoring_builder.py:171` (the X78.6 setup connection) still
  appeared 34 times as a collision source — all 34 retried successfully
  via X78.4's `_retry_on_nested_write`; zero retry exhaustion.

**Verdict: X78.6 SANITY PASS.** Correctness is fixed (no permanent
ownership failure); the residual collisions are performance-induced
transient overlap, exactly the boundary X78.6 was meant to establish —
confirmed by the user's own assessment before authorizing Part B.

## Part B: query performance audit and optimization

### Phase 7-8: `sync_infra_wallets` lifecycle audit

`sync_infra_wallets` seeds `infra_wallets` from three sources: static
in-process registries (`INFRASTRUCTURE_ACCOUNTS`/`CEX_ACCOUNTS`, cheap),
small observed tables (`infra_funders_observed`, `cex_wallets`, cheap),
and three `SELECT DISTINCT` scans of `token_analysis` for
`bonding_curve_pda`/`pool_address`/`pumpswap_pool_address` (expensive —
measured ~48s combined against production in X78.6). This dynamic
portion is monotonically append-only: new bonding curves/pools are
added as tokens launch; nothing is ever removed. `sync_infra_wallets` is
called from 15+ builders across the codebase (out of scope to
restructure here) — this milestone scopes the fix to
`RiskScoringBuilder.score_creator_now` specifically.

**Decision (Phase 9, Option C/D)**: debounce the per-call sync at the
worker-cycle granularity, matching the identical precedent already
established in `creator_funding_worker.py`
(`INTEL_REFRESH_DEBOUNCE_SEC`/`_intel_refresh_last_run`). New constant
`SYNC_INFRA_WALLETS_DEBOUNCE_SEC` (default 300s), module-level state
(not instance-level, since `RiskScoringBuilder` is constructed fresh per
call). `run()` (full-batch scoring) is unaffected — no debounce there,
since a full batch legitimately needs a fresh sync. Regardless of
debounce state, `ensure_infra_wallets_table` is always called (a
correctness precondition independent of freshness) so `_build_context_for_creator`'s
`NOT IN (SELECT address FROM infra_wallets)` subqueries never fail on a
freshly-created database.

### Phase 10-11: `_build_context` creator-predicate audit

Every query in `_build_context` either (a) has no `WHERE creator = ?`
predicate at all (`tokens_by_creator`: full `token_analysis` scan,
measured ~18-22s for ~1.57M rows) or (b) filters in Python after a full
table read (`funders_by_creator`: reads all of `creator_funders`, then
`if creator in creator_set`). `fanout_by_funder`, `c2c_count`, and
`coord_by_creator` ARE genuinely cross-creator by design (`_score_creator_fast`
looks up "how many other creators does funder X also fund" for each of
this creator's own funders) — but only ever need to be computed for the
specific funder addresses/creator that matter for the one creator being
scored, not the whole table.

### Phase 16-18: chosen optimization

New method `_build_context_for_creator(conn, creator)` — a
single-creator-scoped equivalent of `_build_context(conn, [creator])`,
used only by `score_creator_now` (`run()`'s full-batch path is
completely untouched, still calling `_build_context` with the full
creator list, where a full scan is the correct plan). Every query now
filters by `creator_address = ?` (or the relevant join column) in SQL.
The two genuinely-ecosystem-scoped aggregates (`fanout_by_funder`,
`wallet_cluster_funders`) are computed via a two-step query: first fetch
this creator's own funders (small, indexed), then scope the
cross-creator aggregate to exactly those addresses via a parameterized
`IN (...)` clause — same result, far smaller scan.

No index changes were needed or added (Phase 12/19) — the predicate
push-down alone was sufficient; `EXPLAIN QUERY PLAN` was not re-checked
post-change since the queries now operate on primary-key/small-result
sets rather than requiring new access paths.

### Phase 13/20: benchmark and equivalence, measured against production

| Creator | Funders | Old (`_build_context`) | New (`_build_context_for_creator`) | Reduction |
|---|---:|---:|---:|---:|
| `2ShPEGC4y7up...` (typical) | few | 22.33s | 0.76s | 97% |
| `bwamJzztZsep...` (943-funder self-funding scheme, the canonical example from `docs/CLAUDE.md`) | 943 | 21.82s | 3.17-4.44s (repeat runs) | 80-85% |
| `8ghYW6ftL5kU...` | moderate | — | 0.67s | — |
| single-funder creators (×2) | 1 | — | <0.01s | — |

**Score output equivalence, verified field-by-field**
(`operator_score`, `outcome_score`, `g_score`, `liquidation_score`,
`final_score`, `category`, `risk_level`, `migrated_tokens`,
`total_tokens`, `g7_percentage`, `liquidation_count`, `reason_codes`):
**zero mismatches** across all tested creators, including the complex
943-funder `SELF_FUNDING_FARM` classification with 12 simultaneous
reason codes.

### Result equivalence (unit test, exact semantics)

`tests/test_x78_7_query_optimization.py::test_build_context_for_creator_matches_build_context_exactly`
proves, with two creators sharing a funder (to verify cross-creator
fanout counting still works and creator-scoped filtering doesn't leak
or drop the other creator's data), that every field
`_score_creator_fast` reads is identical between the old and new
context-building paths.

## Validation

- `tests/test_x78_7_query_optimization.py` (5 tests): context
  equivalence, score output equivalence, `sync_infra_wallets` debounce
  behavior (fires once across 3 calls, fires again after the window
  elapses), and `run()`'s full-batch path confirmed unaffected.
- Updated `tests/test_x78_6_risk_scoring_lease_boundary.py`'s
  lease-timing test to patch `_build_context_for_creator` (the method
  actually in `score_creator_now`'s call path now) instead of the
  now-unused-there `_build_context`.
- All 35 tests across X78.2–X78.7 combined pass together in one run.
- `git diff` scoped to `risk_scoring_builder.py` (new method + debounce,
  additive — `_build_context`/`run()` unchanged) plus test files; no
  scoring semantics, thresholds, or feature definitions changed.

## Part D/E: regression, deployment, soak

Pending — see readiness verdict below. Local commit made; live restart
and sanity/soak windows to follow in this same turn.

## Root-cause ledger (cumulative, X78.0-X78.7)

| # | Mechanism | Status |
|---|---|---|
| 1 | Individual connection/transaction leaks (25 fixes, X78.0) | FIXED / historical |
| 2 | `asyncio.to_thread` executor-thread-pool reuse amplification | HISTORICAL |
| 3 | Primary extraction cancellation cleanup ordering (X78.0) | FIXED |
| 4 | Detached background descendants (X78.2) | FIXED |
| 5 | RPCCache same-job nested ownership (X78.3) | FIXED |
| 6 | Cancellation grace-period overrun (X78.4) | FIXED (retry/isolation) |
| 7 | `score_creator_now` raw connection attribution + pre-try leak (X78.5) | FIXED |
| 8 | `score_creator_now` over-broad write-lease lifetime (X78.6) | FIXED (boundary corrected) |
| 9 | `score_creator_now` slow full-table setup queries (X78.7) | FIXED (query performance) |
| 10 | `SecondHopExpansionBuilder._is_enabled()` connection handle leak | OPEN — hygiene follow-up, unrelated to `NestedDatabaseWriteError` |

## Commit

Local commit only, not pushed, per task instruction.
