# X64.8 — Phase 8: Cleanup Candidates

No deletion is performed or authorized by this document — every item
below requires a separate, explicitly-scoped approval before any action,
per this task's own constraints and this project's established backup/
deletion discipline (see X64.7B/X64.7C precedent: read-only audit first,
execution as a separate explicitly-scoped task naming exact targets).

## Obsolete tables

| Item | Evidence | Estimated disk saving | Operational risk | Confidence |
|---|---|---|---|---|
| `funder_networks` (hot-DB copy, `flex_complete_database.db`) | Zero code writers (redirected to archive DB per `cross_funding_network_analyzer.py:223,302,360-362`), zero code readers (`main.py` reads only `arch.funder_networks`), frozen at 41,734 rows since 2026-03-08 while the archive copy has grown to 42,314 — proof current writes never reach this copy. An independent re-verification pass confirmed a matching fingerprint against the archive copy, and confirmed `scripts/reclaim_funder_networks_space.py` already exists, fully gated behind `--i-am-in-a-maintenance-window`, and has never been run | **~2.86GB** — the single largest cleanup opportunity found in this entire audit | **Low** — archive copy already verified as a superset (more rows, more recent); confirmed via direct row-count comparison | **High** |

## Expired queue / stale session rows (not whole-table candidates — row-level within otherwise-Operational tables)

**Update**: an independent re-verification pass ran the status/state
breakdown queries this document originally recommended as a follow-up.
Both are now **confirmed**, not just suspected:

| Item | Evidence | Estimated disk saving | Operational risk | Confidence |
|---|---|---|---|---|
| `wt_subprov_sig_retry` completed/terminal rows | **Confirmed**: 2.31M total rows, **99.99% at `status=DONE`**, with no code found reading DONE rows back — this is a pure write-once/check-once-then-abandon pattern with no existing purge job | Nearly all of 392.9MB (~99% of the table, since DONE rows dominate) | **Medium** — deleting rows still mid-retry (the <0.01% non-DONE) would break in-flight reconciliation; a status-scoped purge (`WHERE status='DONE'`) is safe, a blanket truncate is not | **High** (upgraded from Medium — row-level status distribution now confirmed, not inferred from table size alone) |
| `wt_candidate_websocket_watches` stale/expired watches | **Confirmed**: 3.05M total rows, **>99.3% at `state=EXPIRED`**, no code found reading EXPIRED rows back | Nearly all of 319.1MB | **Medium** — same caveat, a state-scoped purge (`WHERE state='EXPIRED'`) is safe, a blanket truncate is not | **High** (upgraded from Medium — same reasoning) |
| `wt_active_subprov_sessions` non-active sessions | Named "active" but sized (54MB) larger than plausible real-time concurrent-session counts would suggest, hinting completed sessions aren't being cleared | Modest (54MB) relative to the two above | **Low-Medium** — smaller blast radius if wrong | **Low-Medium** — naming convention is suggestive; not yet confirmed by a direct status-column query the way the two rows above were |
| `coordinated_creator_edges` staleness | **New finding** (independent re-verification pass): every one of its 328,702 rows shares one identical `created_at` timestamp (2026-06-06 07:08:03), consistent with a DELETE+bulk-INSERT full-rebuild pattern (`graph_analyzer_api.py`) that has not run in ~6 weeks as of this audit — worth checking whether its analyzer job is still scheduled, since a currently-Operational table may actually be stale/Historical in practice | 58.7MB if this proves to be an abandoned rebuild job (not a storage-saving purge candidate as such — the concern here is a possibly-broken scheduled job, not excess data) | **Low** — read-only, informational finding; no deletion implied | **Medium** — timestamp uniformity is strong circumstantial evidence of a stopped rebuild job, but this audit did not confirm whether the analyzer is still scheduled to run |
| `sol_transfers` legacy table | **New finding** (independent re-verification pass): small table (40,581 rows), stale since 2026-03-09 (~4.5 months), superseded by the much larger and current `transfer_index` table (2.33M rows, live through audit time) | Small (table not sized in the main pass — well under the ≥4MB threshold used for detailed inventory) | **Low** — small blast radius | **Medium** — plausible legacy/superseded table, low priority given its size |

## Stale caches

| Item | Evidence | Estimated disk saving | Operational risk | Confidence |
|---|---|---|---|---|
| `rpc_response_cache` | Confirmed cache-shaped table (30.5MB) with no confirmed active eviction/TTL logic found in this pass — recommend a follow-up check of whether stale entries are ever removed, or whether this only ever grows | Modest today (30.5MB) but compounds if genuinely unbounded | **Low** — a cache by definition can be safely trimmed if a proper eviction policy is designed (not blind deletion) | **Low** — this audit did not locate or rule out an eviction path, so this is a "worth checking," not a confirmed finding |

## Orphaned records

No specific orphaned-record pattern (e.g. rows referencing a
since-deleted parent) was confirmed in this pass — this project does not
use SQLite foreign-key constraints (`PRAGMA foreign_key_list` returned
empty across all sampled tables), so orphan-detection would require a
dedicated cross-table consistency pass, out of scope for this audit's
time budget. **Recommend as a distinct follow-up task**, not claimed
here as a finding.

## Zero/low-reference small tables (new, from independent re-verification pass)

A separate independent re-verification pass found, in the hot DB:
**9 tables with zero rows AND zero code references**, and **6 tables
with non-zero but stale data AND zero code references** — e.g.
`cross_network_senders` (last written 2026-02-16, ~5 months stale) and
`oneoff_hub5e1_outbound` (last written 2026-05-28, ~2 months stale).
These meet the same two-axis evidence bar used for `funder_networks`
(no writers, no readers), but are flagged **Medium confidence only**,
not High, per this project's own documented history of naive-grep-based
"looks unused" checks later proving wrong once deeper dependency tracing
was done (see the "Hot DB retention plan" precedent, where
`risk_score_history` and `wss_metrics` were both initially — and
incorrectly — flagged this way). Individually small (none were sized in
the ≥4MB detailed pass), so the storage benefit of removing them is
minor; the more valuable outcome of investigating them is confirming
whether they represent genuinely dead one-off/experimental tables
(`oneoff_hub5e1_outbound`'s name is itself suggestive of a one-off
investigation artifact) or schema debt worth cleaning up as a batch
alongside the `funder_networks` removal, rather than a meaningful
standalone storage-recovery target.

## Unused indexes

No unused index was confirmed in this pass — the largest indexes found
(on `transfer_index`/`token_analysis`) all correspond to query patterns
actually used by attribution/lineage code (by source, destination, time,
amount), and no index was found on the now-dead `funder_networks` hot
copy that wouldn't already be removed automatically by removing that
table itself. A dedicated `sqlite_stat`/query-log-based unused-index
audit would be needed to make a positive claim here — not attempted in
this pass.

## Duplicate structures

`atomic_funder_networks` (referenced in `cross_funding_network_analyzer.py`
as the newer table alongside the now-archived `funder_networks`) was
noted in code comments as the intended replacement structure
("one row per multi-target funder... regardless of clustering") — this
is not a duplicate needing cleanup, it's the active successor table, and
should be left alone. No other duplicate table pattern (e.g. two tables
with near-identical schemas both actively written) was found in this
pass.

## Summary of confidence levels

| Confidence | Items |
|---|---|
| **High** | `funder_networks` hot-DB copy removal (~2.86GB) |
| **Medium** | `wt_subprov_sig_retry` and `wt_candidate_websocket_watches` terminal-row pruning (size unconfirmed, but strong circumstantial evidence) |
| **Low-Medium** | `wt_active_subprov_sessions` stale-session pruning |
| **Low** | `rpc_response_cache` eviction gap |
| **Not evaluated / recommend follow-up** | orphaned records, unused indexes |
