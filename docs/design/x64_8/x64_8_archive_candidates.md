# X64.8 — Phase 6: Archive Candidates

Precedent: `database/flex_investigation_archive.db` (2.87GB) already
exists and holds exactly one migrated table today — `funder_networks`
(42,314 rows, current as of this audit) — moved there in a prior body of
work specifically to relieve hot-DB pressure while preserving
investigative value. This audit's candidates are evaluated against that
same pattern: does the data still have forensic/investigative value, but
no longer need to be in the hot, actively-serialized-write database?

## Candidate 1: `funder_networks` (hot-DB copy) — already effectively archived, cleanup not archive

This is **not a new archive candidate** — the archive move already
happened (code already reads/writes `arch.funder_networks` exclusively).
What remains is a **cleanup** action (removing the now-fully-redundant
2.86GB hot-DB copy), not an archive action. See
[x64_8_cleanup_candidates.md](x64_8_cleanup_candidates.md) — this is
listed here only to avoid double-flagging it as if it were still
undecided.

## Candidate 2: `prediction_decision_context` + `token_prediction_events`

- **Why archivable**: both are prediction/decision snapshots whose value
  is highest in the days immediately following a prediction (evaluating
  accuracy, tuning models) and declines steadily afterward. 276K + 37K
  rows respectively, ~454MB combined.
- **Expected storage saving**: ~454MB if moved wholesale; more likely a
  rolling archive (e.g. older than 6 months) would move a majority of
  current rows given the system's operating history stretches back to
  at least March 2026.
- **Effect on production**: low — these are read for prediction-scoring
  UI and post-hoc analysis, not on the live detection critical path.
- **Effect on investigations**: moderate — investigators occasionally
  need historical prediction context to evaluate model drift or review
  a specific past call; an archive DB (queryable, just not in the hot
  write path) preserves this, a deletion would not.
- **Restoration complexity**: low — same `ATTACH DATABASE` pattern
  already proven with `funder_networks`.

## Candidate 3: `transfer_index` + `funder_incoming_transfers` + `funder_outgoing_transfers` (partial, time-boxed)

- **Why archivable**: these are the funding-lineage graph's raw edge
  data. Once an operator/treasury/creator chain has been fully
  attributed and confirmed (a state this project already tracks via its
  attribution/classification tables), the raw transfer rows backing that
  conclusion have much lower ongoing operational value — they're
  forensic backing evidence, not live decision inputs, for anything
  already resolved.
- **Expected storage saving**: potentially large over time (`transfer_index`
  alone is 544MB and growing at the fastest row-count rate of any table
  measured, 2.3M rows) but **only a fraction is safe to move today** —
  a partial/time-boxed archive (e.g. transfers backing operations
  confirmed >6 months ago) rather than a wholesale move.
- **Effect on production**: needs care — some attribution logic may
  re-query historical transfers when re-evaluating an operator (e.g. if
  new evidence reopens an old cluster). A wholesale move risks breaking
  that re-evaluation path; a time-boxed, confirmed-only move is safer.
- **Effect on investigations**: low, if scoped to only-already-confirmed
  chains — the whole point of confirming attribution is that it
  shouldn't need to change.
- **Restoration complexity**: moderate — unlike `funder_networks` (a
  single self-contained table), these three tables have cross-references
  used throughout attribution code; archiving needs to preserve joinable
  structure, not just move rows.

## Candidate 4: `wss_metrics`

- **Why archivable**: pure telemetry, reporting-only, no correctness
  dependency found in any detection/attribution code path. 2.6M rows,
  163MB.
- **Expected storage saving**: 163MB now, growing continuously (fastest
  row-count grower of any table sampled) — the saving compounds over
  time more than its current size suggests.
- **Effect on production**: none — confirmed reporting-only via code
  search (`usage_tracker.py` writes, `main.py` metrics dashboard reads).
- **Effect on investigations**: minimal — telemetry granularity is
  rarely needed for forensic attribution work; aggregate summaries would
  likely suffice if ever needed historically.
- **Restoration complexity**: low — no foreign-key-style joins found
  referencing this table from other tables.

## Candidate 5: `trade_simulations`, `wt_attribution_outcomes`, `wt_subprov_evidence`

- **Why archivable**: all three are reporting/backtest/evidence-review
  tables, not live-detection inputs, per Phase 4's access analysis.
- **Expected storage saving**: modest individually (17MB, 4.3MB, 17.9MB)
  but low-risk, easy wins if an archive-tooling pass is being built
  anyway (batch several small, clearly-safe tables into the same
  migration effort as a bigger candidate).
- **Effect on production**: none — confirmed reporting/review-only.
- **Effect on investigations**: low — these support post-hoc review, and
  an archive DB (still queryable) preserves that fully.
- **Restoration complexity**: low.

## Not recommended for archiving at this time

- `token_analysis`, `infra_wallets`, `creator_receivers`,
  `creator_risk_scores`, `coordinated_creator_edges` — all confirmed
  live-read on the operational path; archiving would require a much
  more careful "confirmed + inactive" scoping than this audit
  established evidence for.
- `wt_walkback_queue`, `wt_ops_v2_edges`, `wt_operation_activity`,
  `wt_operation_candidates` — active queue/state tables for the
  currently-live detection pipeline; archiving live state is a
  correctness risk, not a storage optimization.
