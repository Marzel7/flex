# X64.8 — Phase 3: Storage Analysis

All byte figures from `dbstat` (exact on-disk page usage, not estimated).
Growth estimates are rough, derived from row count vs. observed date
ranges where a timestamp column was available; noted as "no timestamp
sampled" where not checked in this pass.

## `database/flex_complete_database.db` — ranked largest to smallest (≥4MB)

| Rank | Table | Bytes | % of 9.9GB DB | Growth estimate |
|---|---|---|---|---|
| 1 | `funder_networks` | 2,864,263,168 | 27.0% | **Zero — frozen since 2026-03-08.** Not growing; this is 100% recoverable dead weight. |
| 2 | `token_analysis` | 568,496,128 | 5.4% | Continuous — one row per token, grows with launch volume (thousands/day based on `1.34M` total rows) |
| 3 | `transfer_index` | 544,272,384 | 5.1% | Continuous, tied to funding-lineage extraction volume (2.3M rows) |
| 4 | `prediction_decision_context` | 417,492,992 | 3.9% | Continuous, one row per prediction event (276K rows) |
| 5 | `watchtower_infra_events` | 296,345,600 | 2.8% | Continuous but lower volume (66.7K rows — infra-wallet events are rarer than per-token events) |
| 6 | `token_prediction_scores` | 180,887,552 | 1.7% | Continuous, tracks `token_analysis` roughly 1:1 (277K rows) |
| 7 | `wss_metrics` | 163,295,232 | 1.5% | **Fastest per-row table** — 2.6M rows, one per WS subscription event; likely the single fastest-growing table by row-count velocity even though its per-row size is small |
| 8 | `metadata_cache` | 122,208,256 | 1.2% | Grows with unique token/creator count, partially self-limiting (cache reuse reduces marginal growth) |
| 9 | `token_liquidity_snapshots` | 108,437,504 | 1.0% | Continuous, periodic per-token snapshots (1.3M rows) |
| 10 | `creator_receivers` | 63,819,776 | 0.6% | Continuous, tied to funding-extraction volume (476K rows) |
| 11 | `infra_wallets` | 63,537,152 | 0.6% | Continuous but slower — registry-style growth (665K rows, likely includes historical + current) |
| 12 | `coordinated_creator_edges` | 58,691,584 | 0.6% | Continuous, tied to coordination-detection volume (328K rows) |
| 13-20 | (`funder_outgoing_transfers` through `trade_simulations`) | ~40MB down to ~17MB each | 0.2-0.4% each | Continuous, all tied to funding/prediction extraction volume |

**Largest indexes** (all on high-write hot tables — expected, not
bloat): `idx_transfer_source_dest` (255.8MB), `idx_ta_bonding_curve_refresh`
(181.1MB), `idx_transfer_source_amount_time` (157.9MB),
`idx_transfer_destination_time` (155.3MB), `idx_transfer_source_time`
(149.0MB) — the `transfer_index`/`token_analysis` tables alone carry
~900MB of index overhead across their 5 largest indexes combined,
roughly 1.6x the size of `transfer_index`'s own table data. This is a
consequence of the many funding-lineage query patterns (by source, by
destination, by time, by amount) needed for real-time attribution, not
an indexing mistake.

## `database/wt_ops_v2.db` — ranked largest to smallest (≥1MB)

| Rank | Table | Bytes | % of 2.4GB DB | Growth estimate |
|---|---|---|---|---|
| 1 | `wt_subprov_sig_retry` | 392,859,648 | 16.2% | Should be self-limiting (a retry queue) — its size relative to the whole DB suggests either high queue churn or a pruning gap (see Phase 8) |
| 2 | `wt_candidate_websocket_watches` | 319,057,920 | 13.2% | Continuous, tied to WS-candidate volume; same pruning-gap concern as above |
| 3 | `watchtower_events` | 191,348,736 | 7.9% | Continuous, general event log |
| 4 | `wt_cdc_outbound_events` | 86,806,528 | 3.6% | Continuous, CDC pipeline volume |
| 5 | `wt_active_subprov_sessions` | 54,013,952 | 2.2% | Should track *active* sessions only — if this table is large relative to plausible concurrent-session counts, it likely holds completed/stale sessions never purged (see Phase 8) |
| 6 | `wt_subprov_evidence` | 17,928,192 | 0.7% | Continuous, tied to classification volume |
| 7-12 | (`wt_operation_activity` through `wt_ops_v2_edges`) | ~11.7MB down to ~3.3MB | <0.5% each | Continuous, operation-centric store volume |

**Combined**: the top 5 tables in `wt_ops_v2.db` account for
**~1.04GB of the 2.4GB total (~43%)** — a meaningfully more concentrated
distribution than the hot DB, where the single `funder_networks` outlier
alone dominates.

## Overall ranking (both databases combined, top 10)

| Rank | Table | DB | Bytes |
|---|---|---|---|
| 1 | `funder_networks` | flex_complete | 2,864,263,168 |
| 2 | `token_analysis` | flex_complete | 568,496,128 |
| 3 | `transfer_index` | flex_complete | 544,272,384 |
| 4 | `prediction_decision_context` | flex_complete | 417,492,992 |
| 5 | `wt_subprov_sig_retry` | wt_ops_v2 | 392,859,648 |
| 6 | `wt_candidate_websocket_watches` | wt_ops_v2 | 319,057,920 |
| 7 | `watchtower_infra_events` | flex_complete | 296,345,600 |
| 8 | `watchtower_events` | wt_ops_v2 | 191,348,736 |
| 9 | `token_prediction_scores` | flex_complete | 180,887,552 |
| 10 | `wss_metrics` | flex_complete | 163,295,232 |

**Fastest-growing by row-count velocity** (not just byte size):
`wss_metrics` (2.6M rows) and `transfer_index` (2.3M rows) have the
highest row counts of any table in either database, meaning they are
likely accumulating the fastest in absolute row terms even though their
per-row footprint keeps total bytes moderate relative to `funder_networks`.
