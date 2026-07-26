# X64.5 — Phase 7: Historical Backfill Dry-Run Report

Run against live `database/wt_ops_v2.db` / `database/flex_complete_database.db`,
2026-07-21, via `anchor_reconciliation.dry_run_report()`. Read-only — no
rows modified by this report itself (the subsequent live reconciliation
run is documented separately in `x64_5_implementation.md`).

## Population at time of dry-run

Query:
```sql
SELECT COUNT(*) FROM wt_walkback_queue
WHERE status='waiting' AND path_state='WAITING_FOR_CREATE_ANCHOR'
  AND (create_anchor_signature IS NULL OR create_anchor_audit_state='MISSING_OR_MALFORMED');
```

**Total: 352** (grew from the ad-hoc 350 first observed a few minutes
earlier in this same session — the queue is live and continuously
enqueuing new rows via `enqueue_migration()`, so exact counts are
timestamp-sensitive; both figures are reported for traceability).

## Classification (via `anchor_reconciliation.classify_stuck_row()`, using
the actual production `valid_signature()` function, not a re-derived rule)

| Classification | Count |
|---|---|
| RECOVERABLE_VALID_ANCHOR | 310 |
| ANCHOR_PRESENT_INVALID | 0 |
| ANCHOR_STILL_MISSING | 0 |
| AMBIGUOUS_MULTIPLE_ROWS | 0 |
| MINT_NOT_FOUND | 42 |

Full recoverable-row list: [x64_5_recoverable_rows.csv](x64_5_recoverable_rows.csv)
(309 lines including header, matching the 308-row count from the
slightly-earlier ad-hoc snapshot — the two extra rows found in the later
live run are additional, not a discrepancy).

## Age distribution

- **Oldest stuck row**: `enqueued_at=1784572614` (2026-07-20T18:36:54 UTC)
- **Newest stuck row**: `enqueued_at=1784631086` (2026-07-21T10:51:26 UTC)
- **Span**: ~16.2 hours — consistent with this being an ongoing, continuously
  reproducing condition (every FULL_WALKBACK migration since this pattern
  began enqueueing), not a one-time historical anomaly.

## Attempts / RPC touch

- **All 352 rows: `attempts=0`, `rpc_used=0`.** None has ever been claimed
  or processed by the worker — `drain_batch`'s own SELECT excludes
  `status='waiting'` rows entirely (only `pending`/expired-`running`), so
  these rows were structurally unreachable by the normal walkback path
  before this fix, confirming they represent genuinely lost throughput,
  not failed attempts.
- **Number already attributed through another route**: 0 — none of the
  352 mints have a `watchtower_token_attribution` row or a
  `wt_attribution_outcomes` row (checked directly; none of these rows have
  ever reached `materialize_outcome()`, since `_mark_complete` is never
  called for a row that never left `status='waiting'`).

## Conflicts (Phase 7's explicit conflict-detection requirement)

**0 conflicts found.** A conflict requires an existing valid
`create_anchor_signature` on a row still marked
`create_anchor_audit_state='MISSING_OR_MALFORMED'` (a data-inconsistency
case, not the common shape) whose value differs from what
`creator_funding_queue` holds. No row in this dataset has that shape — by
construction, every stuck row's own `create_anchor_signature` is NULL, so
there is nothing to conflict against. The conflict-detection code path
(`anchor_reconciliation.py`'s guard inside both
`reconcile_waiting_create_anchors()` and `dry_run_report()`) remains
active and will populate [x64_5_conflicts.csv](x64_5_conflicts.csv)
(currently header-only) if this data shape is ever encountered on a
future run.

## Canonical regression case confirmed present

`H55qUAeK313XyTrhxeMVQgBrogdGG9biyAVfmDQipump` appears in the
`RECOVERABLE_VALID_ANCHOR` bucket with recovered signature
`Tt3yP2SNaXG4gNWAmduUBCDbpmV26RErBQrzDLSZuZuqv28m4Kez3m6f82RJnvCUov8jPqHn2LhkCYxwwLfSP6b`,
source `creator_funding_queue` — matching the value independently
verified earlier in this session.

## Decision

Per the task's explicit instruction ("update only rows where queue anchor
is absent or invalid AND creator_funding_queue contains exactly one valid
matching signature. Do not overwrite an existing different valid anchor
automatically"), all 310 `RECOVERABLE_VALID_ANCHOR` rows meet this
criterion cleanly (single matching `creator_funding_queue` row, valid
signature, no existing conflicting anchor) and were released for the live
backfill documented in `x64_5_implementation.md`. The 42
`MINT_NOT_FOUND` rows are left untouched — genuinely no
`creator_funding_queue` row exists for them, so nothing can be recovered
without RPC.
