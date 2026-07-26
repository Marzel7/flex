# X64.8 — Executive Summary: Database Lifecycle & Retention Audit

Read-only audit, 2026-07-21, of `database/flex_complete_database.db`
(9.9GB, 318 tables) and `database/wt_ops_v2.db` (2.4GB, 104 tables).
No data, schema, or files were modified during this audit.

## Total database size

- `flex_complete_database.db`: 10,600,730,624 bytes (9.9G)
- `wt_ops_v2.db`: 2,526,175,232 bytes (2.4G)
- Combined hot-DB footprint: **~12.3GB**
- (For reference, the existing archive tier, `flex_investigation_archive.db`,
  adds a further 2.87GB, bringing total on-disk database storage across
  all three files to ~15.2GB)

## Largest tables (combined ranking)

1. `funder_networks` (flex_complete) — 2.86GB — **dead weight, see below**
2. `token_analysis` (flex_complete) — 568MB
3. `transfer_index` (flex_complete) — 544MB
4. `prediction_decision_context` (flex_complete) — 417MB
5. `wt_subprov_sig_retry` (wt_ops_v2) — 393MB

## Fastest-growing tables

By row count (the leading indicator of future size growth):
`wss_metrics` (2.64M rows, WS telemetry) and `transfer_index` (2.33M
rows, funding-lineage edges) — both are continuously written on every
detection/extraction cycle and have no retention limit today.

## Operational vs. historical storage split

Of the ~12.3GB hot-DB total, approximately:
- **~2.86GB (23%) is fully dead** (`funder_networks` hot-DB copy —
  already superseded by an archive copy, zero live readers or writers)
- **~9.0GB (~73%) is genuinely operational** — actively read/written on
  the live detection/attribution/dashboard path
- **~450MB (~4%) is historical/reporting-only** with declining
  operational value over time (`prediction_decision_context`,
  `token_prediction_events`, `trade_simulations`,
  `wt_attribution_outcomes`, `wt_subprov_evidence`)

## Estimated storage recoverable through lifecycle management

- **Immediate, high-confidence: ~2.86GB** — removing the dead
  `funder_networks` hot-DB copy (archive copy already safely holds a
  superset of this data).
- **Medium-confidence, needs a follow-up query to size precisely**: an
  unquantified but likely substantial fraction of `wt_subprov_sig_retry`
  (393MB) and `wt_candidate_websocket_watches` (319MB) — both show signs
  of retry/watch rows never being pruned after completion.
- **Longer-term, via archiving (not deletion) of aging
  historical/reporting tables**: up to ~450MB+ of `prediction_decision_context`
  / `wss_metrics` / small reporting tables, growing over time as more
  data ages into the "historical" bucket.

## Tables suitable for archiving

`prediction_decision_context` + `token_prediction_events` (time-boxed),
`transfer_index` + funder-transfer tables (partial, confirmed-attribution-only),
`wss_metrics`, and the small reporting trio
(`trade_simulations`/`wt_attribution_outcomes`/`wt_subprov_evidence`) —
see [x64_8_archive_candidates.md](x64_8_archive_candidates.md) for full
reasoning per table.

## Tables suitable for future deletion

Only `funder_networks` (hot-DB copy) met the evidentiary bar for
Obsolete classification in this pass — see
[x64_8_cleanup_candidates.md](x64_8_cleanup_candidates.md). Everything
else flagged is a pruning/archiving candidate, not a deletion candidate,
pending further row-level evidence.

## Recommended backup architecture

**Strategy B: split operational + historical backup**, not a continued
full-database backup approach — see
[x64_8_backup_strategy.md](x64_8_backup_strategy.md) for full
comparison. The disk-full incident that triggered X64.7C is a direct
consequence of the full-backup approach's cost profile on this host's
available disk (17Gi free, 91% capacity); Strategy B is recommended
specifically because it stops backing up known-dead data forever and
matches backup cadence to actual data-change velocity.

## Recommended retention policy

See [x64_8_retention_policy.md](x64_8_retention_policy.md) for the full
per-category table. Headline recommendations: retain creator/treasury/
infra intelligence indefinitely (it's the system's core knowledge base,
expensive to re-derive); retain launch/token history with a size-based
(not time-based) revisit trigger; purge completed queue rows on a
state basis, not a calendar basis; archive prediction/decision snapshots
and funding-lineage detail after 6-12 months once attribution is
confirmed.

## Recommended next implementation phases

1. **Immediate (low-risk, high-value)**: confirm and execute the
   `funder_networks` hot-DB copy removal — the largest, most
   evidence-backed cleanup opportunity in this audit, following the same
   rigorous verify-then-delete pattern used in X64.7C.
2. **Near-term**: a targeted follow-up query against
   `wt_subprov_sig_retry` and `wt_candidate_websocket_watches` to
   confirm the completed/terminal row split, sizing the true pruning
   opportunity before building automated pruning logic.
3. **Medium-term**: build archive tooling generalized from the
   `flex_investigation_archive.db` / `funder_networks` precedent, to
   support the additional archive candidates identified in Phase 6
   without one-off scripts per table family.
4. **Medium-term**: implement Strategy B's operational/historical split
   backup, sequenced *after* the cleanup above shrinks the operational
   backup footprint — avoids automating backups of data about to become
   historical overhead.
5. **Longer-term**: revisit Strategy C (incremental/WAL-based backups)
   once backup frequency requirements increase beyond what full-copy
   Strategy B can sustain.

See [x64_8_cleanup_candidates.md](x64_8_cleanup_candidates.md) Phase 10
roadmap-style prioritization for a more detailed breakdown of effort/risk/
dependency per item.
