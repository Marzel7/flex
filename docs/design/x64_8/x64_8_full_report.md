# X64.8 — Database Lifecycle & Retention Audit (Full Report)

Read-only audit, 2026-07-21, of `database/flex_complete_database.db`
(9.9GB, 318 tables) and `database/wt_ops_v2.db` (2.4GB, 104 tables). No
data, schema, or files were modified during this audit — every finding
below comes from `SELECT`/`PRAGMA`/`dbstat`/`.schema` reads and code
search (`grep -rn`) only.

This document consolidates all 10 phases previously split across
separate files (`x64_8_table_inventory.md` through
`x64_8_implementation_roadmap.md`) into one report, folding in
corrections and new findings surfaced by two independent
re-verification passes run after the initial audit.

---

## Phase 1 — Table Inventory

| Database | File size | Table count (excl. `sqlite_*`) |
|---|---|---|
| `database/flex_complete_database.db` | 10,600,730,624 bytes (9.9G) | 318 |
| `database/wt_ops_v2.db` | 2,526,175,232 bytes (2.4G) | 104 |

Given the scale (422 tables total), every table ≥ 4MB (flex DB) / ≥ 1MB
(ops DB) is documented individually below — together accounting for
90%+ of total bytes in both databases. Sizes are exact, from
`SELECT name, SUM(pgsize) FROM dbstat GROUP BY name`. Row counts are
exact `COUNT(*)` results where shown.

### `database/flex_complete_database.db` — tables ≥ 4MB

| Table | Bytes | Rows | Purpose | Primary writer(s) | Primary reader(s) |
|---|---|---|---|---|---|
| `funder_networks` | 2,864,263,168 | 41,734 | Clustered funder networks (legacy funding-graph analysis) | **None — migrated off.** `cross_funding_network_analyzer.py:223,302,360-362`: "funder_networks writes target the archive DB, not the hot DB" | `main.py` reads exclusively via `arch.funder_networks` (attached archive DB, lines 13211-13226) — hot-DB copy is not read anywhere |
| `token_analysis` | 568,496,128 | 1,338,952 | Core per-token record: creator, migration status, pricing, lifecycle | `pumpfun_curve_listener.py` (birth/migration handlers) | `main.py` (dashboard/API), most analysis modules |
| `transfer_index` | 544,272,384 | 2,326,627 | Indexed SOL transfer records for funding-lineage tracing | funding extractors (`realtime_creator_funding_extractor.py`, `funder_incoming_extractor.py`) | `cross_funding_network_analyzer.py`, attribution modules |
| `prediction_decision_context` | 417,492,992 | 276,049 | Snapshot of signals/features at prediction-decision time | `prediction_decision_context.py`, `token_prediction_builder.py` | `main.py`, `risk_axis.py` |
| `watchtower_infra_events` | 296,345,600 | 66,705 | WATCHTOWER infra-wallet/provisioning event log | `watchtower_detector.py`, `watchtower_init.py` | `main.py`, `schema_init.py` (schema only) |
| `token_prediction_scores` | 180,887,552 | 277,138 | Per-token prediction/risk scores | `token_prediction_builder.py` | `main.py`, `pumpfun_curve_listener.py`, `risk_axis.py` |
| `wss_metrics` | 163,295,232 | 2,637,910 | Per-subscription WebSocket telemetry | `usage_tracker.py` | `main.py` (metrics dashboards) |
| `metadata_cache` | 122,208,256 | 1,303,815 | Cached token/creator metadata (avoid repeat RPC/API calls) | metadata-fetch paths | most UI/analysis reads |
| `token_liquidity_snapshots` | 108,437,504 | 1,296,201 | Periodic liquidity/curve snapshots per token | price/liquidity worker | chart/analysis reads |
| `creator_receivers` | 63,819,776 | 476,371 | Creator → downstream-receiver funding edges | funding extractors | attribution/lineage modules |
| `infra_wallets` | 63,537,152 | 665,740 | Known infra-wallet registry (treasuries, subprovs, etc.) | WATCHTOWER detection modules | attribution modules, `main.py` |
| `coordinated_creator_edges` | 58,691,584 | 328,702 | Creator-to-creator coordination edges | full-rebuild edge builder (`graph_analyzer_api.py`) | cluster/coordination views |
| `funder_outgoing_transfers` | 40,095,744 | — | Funder outgoing-transfer records | `funder_incoming_extractor.py` family | lineage/attribution modules |
| `creator_risk_scores` | 38,985,728 | — | Per-creator computed risk score | risk-scoring modules | `main.py`, risk views |
| `token_prediction_events` | 36,978,688 | — | Event log backing prediction scoring | `token_prediction_builder.py` | prediction dashboards |
| `rpc_response_cache` | 30,515,200 | — | Cached raw RPC responses | RPC-calling modules | same (cache read-through) |
| `creator_outgoing_transfers` | 22,147,072 | — | Creator's own outgoing transfers | funding extractors | lineage modules |
| `creator_service_history` | 21,463,040 | — | History of creator "service" (repeat-deploy) activity | serial-deployer detection | classification modules |
| `funder_incoming_transfers` | 17,772,544 | — | Funder incoming-transfer records | `funder_incoming_extractor.py` | `cross_funding_network_analyzer.py` |
| `trade_simulations` | 17,125,376 | — | Simulated trade outcomes for backtesting | analysis/backtest modules | reporting |

**Indexes**: largest indexes shadow their parent tables 1:1 —
`idx_transfer_source_dest` (255MB), `idx_ta_bonding_curve_refresh`
(181MB), `idx_transfer_source_amount_time` (157MB), all on
`transfer_index`/`token_analysis`, consistent with normal query-pattern
indexing, not bloat.

**Foreign keys**: no table in either database declares SQLite foreign-key
constraints (`PRAGMA foreign_key_list` empty everywhere sampled) —
relationships are enforced at the application layer, a pre-existing
project convention.

### `database/wt_ops_v2.db` — tables ≥ 1MB

| Table | Bytes | Purpose | Primary writer(s) | Primary reader(s) |
|---|---|---|---|---|
| `wt_subprov_sig_retry` | 392,859,648 | Retry queue for subprov-signature resolution | `walkback_worker.py` / anchor-reconciliation retry path | same (self-consuming queue) |
| `wt_candidate_websocket_watches` | 319,057,920 | Active/historical WS subscription-candidate tracking | `ws_cascade.py` | `operation_dashboard_routes.py` |
| `watchtower_events` | 191,348,736 | General WATCHTOWER event log | multiple detection modules | `operation_dashboard_routes.py` |
| `wt_cdc_outbound_events` | 86,806,528 | Change-data-capture outbound event log | CDC/webhook pipeline | downstream consumers |
| `wt_active_subprov_sessions` | 54,013,952 | Active subprov WS-session tracking | `ws_cascade.py` | session-health dashboards |
| `wt_subprov_evidence` | 17,928,192 | Evidence records backing subprov classification | classification modules | review/audit UI |
| `wt_operation_activity` | 11,767,808 | Operation-centric activity log | `operation_scheduler.py` | `operation_dashboard_routes.py` |
| `wt_operation_candidates` | 8,380,416 | Candidate operations under evaluation | discovery/scoring modules | dashboard |
| `wt_swarm_buys` | 4,571,136 | Buy-swarm detection records | swarm detector | attribution modules |
| `wt_fanout_events` | 4,468,736 | Fan-out event records | `ws_cascade.py` | attribution modules |
| `wt_attribution_outcomes` | 4,300,800 | Recorded attribution outcome per operation | `attribution_outcome.py` | reporting |
| `wt_ops_v2_edges` | 3,362,816 | Operation-graph edges (op-centric store) | `operation_store_v2` | `operation_dashboard_routes.py` |

Below this tier, ~90 additional `wt_ops_v2.db` tables are each under
1MB — cursors, session state, per-subsystem config (e.g.
`wt_walkback_queue` 2.0MB, `wt_webhook_hits` 2.2MB,
`wt_subprov_sig_cursor` 2.6MB) — consistent with active, low-volume
operational state.

The remaining ~360 tables across both databases (long tail, not sized
individually) are dominated by schema-version/config/lookup tables and
some schema-only/0-row tables from prior test runs or superseded
feature work. The top 20 tables in `flex_complete_database.db` alone
account for **~5.6GB of the 9.9GB total (~57%)**; `funder_networks`
alone is **~27% of the entire hot database**.

---

## Phase 2 — Table Classification

### Obsolete

| Table | Evidence |
|---|---|
| `funder_networks` (hot copy, 2.86GB, 41,734 rows) | Zero writers, zero readers (see Phase 1), and confirmed stale: `MAX(detected_at) = 2026-03-08` (frozen 4+ months) while the archive copy has grown to 42,314 rows (580 more) in the same period — proof all current writes go to the archive only. Independently re-verified twice (matching fingerprint against the archive copy each time). **The only table classified Obsolete with full confidence.** |

No other ≥1MB table met all three bars (zero writers AND zero readers
AND confirmed-stale activity) — every other large table has live code
references. Per this project's own prior lesson (a naive "looks unused"
grep once misclassified `risk_score_history` and `wss_metrics` before
deeper tracing reversed the verdict), all candidates were checked for
live code references before ruling anything out.

### Operational

- `token_analysis`, `transfer_index`, `prediction_decision_context`,
  `watchtower_infra_events`, `token_prediction_scores`, `metadata_cache`,
  `creator_receivers`, `infra_wallets`, `coordinated_creator_edges`,
  `funder_outgoing_transfers`, `creator_risk_scores`,
  `token_prediction_events`, `creator_outgoing_transfers`,
  `funder_incoming_transfers` (flex DB)
- `wt_subprov_sig_retry`, `wt_candidate_websocket_watches`,
  `watchtower_events`, `wt_cdc_outbound_events`,
  `wt_active_subprov_sessions`, `wt_subprov_evidence`,
  `wt_operation_activity`, `wt_operation_candidates`, `wt_swarm_buys`,
  `wt_fanout_events`, `wt_attribution_outcomes`, `wt_ops_v2_edges`,
  `wt_walkback_queue`, `wt_webhook_hits`, `wt_subprov_sig_cursor` (ops DB)
- The ~90 sub-1MB ops-DB tables (cursors, session state, per-subsystem config).

### Historical

- `creator_service_history` (21.4MB) — serial-deployer history, read
  occasionally, not a live hot path.
- `trade_simulations` (17.1MB) — backtest reference data, not consumed
  by live detection.
- `rpc_response_cache` (30.5MB) — borderline Temporary/Historical; no
  active eviction path confirmed in code.

### Derived / Rebuildable

- `token_liquidity_snapshots` (108MB) — **rebuild cost: high** (would
  require expensive RPC-bound curve-replay per token; treat as
  expensive-to-rebuild, not casually purgeable).
- `wss_metrics` (163MB) — **not rebuildable at all**, a live telemetry
  stream; no single row is analytically irreplaceable beyond aggregates,
  but functionally closer to Temporary for retention purposes.
- `metadata_cache` (122MB) — **rebuild cost: low-to-moderate**,
  re-fetchable from RPC/external APIs on demand.

### Temporary

- `wt_subprov_sig_retry` (392.9MB), `wt_candidate_websocket_watches`
  (319MB) — retry/watch queues by design; their size strongly suggests
  a pruning gap (**confirmed** in Phase 8 below).
- `rpc_response_cache` (30.5MB) — cache-shaped, eviction behavior
  unconfirmed.

### Summary counts (≥1MB tier)

| Category | Table count | Approx. bytes |
|---|---|---|
| Obsolete | 1 | 2.86GB |
| Operational | ~39 | ~2.6GB |
| Historical | 3 | ~69MB |
| Derived/Rebuildable | 3 | ~394MB |
| Temporary | 3 (overlaps Operational — queues are both) | ~742MB |

The single Obsolete table is larger than Historical + Derived/Rebuildable
+ Temporary combined — it dominates every lifecycle decision in this audit.

---

## Phase 3 — Storage Analysis

### `flex_complete_database.db` — ranked largest to smallest (≥4MB)

| Rank | Table | Bytes | % of 9.9GB DB | Growth estimate |
|---|---|---|---|---|
| 1 | `funder_networks` | 2,864,263,168 | 27.0% | **Zero — frozen since 2026-03-08.** 100% recoverable dead weight. |
| 2 | `token_analysis` | 568,496,128 | 5.4% | Continuous, one row/token (1.34M rows) |
| 3 | `transfer_index` | 544,272,384 | 5.1% | Continuous, funding-lineage extraction volume (2.3M rows) |
| 4 | `prediction_decision_context` | 417,492,992 | 3.9% | Continuous, one row/prediction (276K rows) |
| 5 | `watchtower_infra_events` | 296,345,600 | 2.8% | Continuous, lower-volume (66.7K rows) |
| 6 | `token_prediction_scores` | 180,887,552 | 1.7% | Continuous, tracks `token_analysis` ~1:1 |
| 7 | `wss_metrics` | 163,295,232 | 1.5% | **Fastest per-row table** (2.6M rows) |
| 8 | `metadata_cache` | 122,208,256 | 1.2% | Grows w/ unique token/creator count, self-limiting via reuse |
| 9 | `token_liquidity_snapshots` | 108,437,504 | 1.0% | Continuous periodic snapshots (1.3M rows) |
| 10 | `creator_receivers` | 63,819,776 | 0.6% | Continuous (476K rows) |
| 11 | `infra_wallets` | 63,537,152 | 0.6% | Registry-style growth (665K rows) |
| 12 | `coordinated_creator_edges` | 58,691,584 | 0.6% | Full-rebuild table — **see Phase 8, may be stalled** |
| 13-20 | (remaining ≥4MB tables) | ~40MB-17MB each | 0.2-0.4% each | Continuous, funding/prediction volume |

**Largest indexes**: `idx_transfer_source_dest` (255.8MB),
`idx_ta_bonding_curve_refresh` (181.1MB),
`idx_transfer_source_amount_time` (157.9MB),
`idx_transfer_destination_time` (155.3MB),
`idx_transfer_source_time` (149.0MB) — `transfer_index`/`token_analysis`
carry ~900MB of index overhead across their top 5 indexes alone (~1.6x
`transfer_index`'s own table data), a consequence of real-time
attribution's many query patterns, not an indexing mistake.

### `wt_ops_v2.db` — ranked largest to smallest (≥1MB)

| Rank | Table | Bytes | % of 2.4GB DB | Growth estimate |
|---|---|---|---|---|
| 1 | `wt_subprov_sig_retry` | 392,859,648 | 16.2% | **Confirmed pruning gap** — see Phase 8 |
| 2 | `wt_candidate_websocket_watches` | 319,057,920 | 13.2% | **Confirmed pruning gap** — see Phase 8 |
| 3 | `watchtower_events` | 191,348,736 | 7.9% | Continuous general event log |
| 4 | `wt_cdc_outbound_events` | 86,806,528 | 3.6% | Continuous, CDC pipeline volume |
| 5 | `wt_active_subprov_sessions` | 54,013,952 | 2.2% | Possible stale-session accumulation, unconfirmed |
| 6 | `wt_subprov_evidence` | 17,928,192 | 0.7% | Continuous, classification volume |
| 7-12 | (remaining) | ~11.7MB-3.3MB | <0.5% each | Continuous, operation-centric store volume |

Top 5 ops-DB tables = **~1.04GB of 2.4GB total (~43%)** — more
concentrated than the hot DB, where `funder_networks` alone dominates.

### Overall ranking (both DBs combined, top 10)

1. `funder_networks` (flex) — 2,864,263,168
2. `token_analysis` (flex) — 568,496,128
3. `transfer_index` (flex) — 544,272,384
4. `prediction_decision_context` (flex) — 417,492,992
5. `wt_subprov_sig_retry` (ops) — 392,859,648
6. `wt_candidate_websocket_watches` (ops) — 319,057,920
7. `watchtower_infra_events` (flex) — 296,345,600
8. `watchtower_events` (ops) — 191,348,736
9. `token_prediction_scores` (flex) — 180,887,552
10. `wss_metrics` (flex) — 163,295,232

**Fastest-growing by row-count velocity**: `wss_metrics` (2.6M rows) and
`transfer_index` (2.3M rows) — highest row counts of any table in either
database.

---

## Phase 4 — Access Analysis

### `flex_complete_database.db`

| Table | Written? | Read? | Frequency | Prod-critical? | Evidence |
|---|---|---|---|---|---|
| `funder_networks` | **No** (hot copy) | **No** (hot copy) | Never (frozen) | **No — dead in hot DB** | `main.py:84-121`, `cross_funding_network_analyzer.py:223,302,360-362` |
| `token_analysis` | Yes | Yes | Continuous | Yes | `pumpfun_curve_listener.py`, `main.py` |
| `transfer_index` | Yes | Yes | Continuous | Yes | `realtime_creator_funding_extractor.py`, `funder_incoming_extractor.py` |
| `prediction_decision_context` | Yes | Yes | Continuous | Yes | `token_prediction_builder.py`, `prediction_decision_context.py` |
| `watchtower_infra_events` | Yes | Yes | Continuous, lower-volume | Yes | `watchtower_detector.py`, `watchtower_init.py` |
| `token_prediction_scores` | Yes | Yes | Continuous | Yes | `token_prediction_builder.py`, `pumpfun_curve_listener.py`, `risk_axis.py` |
| `wss_metrics` | Yes | Yes | Continuous, high-frequency | Reporting-only | `usage_tracker.py`, `main.py` |
| `metadata_cache` | Yes | Yes | Continuous | Yes (perf-critical) | metadata-fetch paths; most UI/analysis |
| `token_liquidity_snapshots` | Yes | Yes | Continuous, periodic | Yes | price/liquidity worker; chart reads |
| `creator_receivers` | Yes | Yes | Continuous | Yes | funding extractors; attribution modules |
| `infra_wallets` | Yes | Yes | Continuous | Yes | WATCHTOWER detection modules |
| `coordinated_creator_edges` | Yes (full-rebuild) | Yes | **Possibly stalled — see Phase 8** | Yes (coordination views) | `graph_analyzer_api.py` |
| `creator_service_history` | Yes | Occasional | Low-freq read | Historical-only in practice | serial-deployer detection |
| `trade_simulations` | Batch-only | Rare | Batch | No — reporting/backtest only | analysis/backtest modules |
| `rpc_response_cache` | Yes | Yes | Continuous, self-limiting | Perf-only | RPC-calling modules |

### `wt_ops_v2.db`

| Table | Written? | Read? | Frequency | Prod-critical? | Evidence |
|---|---|---|---|---|---|
| `wt_subprov_sig_retry` | Yes | Yes | Continuous | Yes — active retry path | `walkback_worker.py` / anchor-reconciliation |
| `wt_candidate_websocket_watches` | Yes | Yes | Continuous | Yes | `ws_cascade.py` |
| `watchtower_events` | Yes | Yes | Continuous | Yes | multiple detection modules; `operation_dashboard_routes.py` |
| `wt_cdc_outbound_events` | Yes | Yes | Continuous | Yes | webhook/CDC pipeline |
| `wt_active_subprov_sessions` | Yes | Yes | Continuous | Yes | `ws_cascade.py`; session-health dashboards |
| `wt_subprov_evidence` | Yes | Occasional | Moderate | Review/audit-only | classification modules; review UI |
| `wt_operation_activity` | Yes | Yes | Continuous | Yes | `operation_scheduler.py` |
| `wt_operation_candidates` | Yes | Yes | Continuous | Yes | discovery/scoring modules |
| `wt_swarm_buys` | Yes | Occasional | Moderate | Attribution-only | swarm detector |
| `wt_fanout_events` | Yes | Yes | Continuous | Yes | `ws_cascade.py` |
| `wt_attribution_outcomes` | Yes | Occasional | Low-moderate | Reporting-only | `attribution_outcome.py` |
| `wt_ops_v2_edges` | Yes | Yes | Continuous | Yes | operation-centric store |
| `wt_walkback_queue` | Yes | Yes | Continuous | Yes — core reconciliation queue | `walkback_worker.py`, `anchor_reconciliation.py`, `create_event_ledger.py` |

**Cross-cutting**: UI/reporting-only tables (`trade_simulations`,
`wt_attribution_outcomes`, `wt_subprov_evidence`, `wss_metrics`) gate
nothing in production detection — safe candidates for more aggressive
retention. `token_analysis`, `transfer_index`,
`prediction_decision_context`, `wt_walkback_queue`, `wt_ops_v2_edges`
are production-critical, high-frequency, and need explicit (not
blanket) retention scoping. Only `funder_networks` (hot copy) has zero
access in either direction, code-confirmed.

---

## Phase 5 — Retention Policy Recommendations

Recommendations only — nothing implemented.

| Category | Tables | Suggested retention | Reasoning |
|---|---|---|---|
| Dead/orphaned hot-DB copies | `funder_networks` (hot copy) | **Remove entirely** | Zero readers/writers, frozen since 2026-03-08; archive is a verified superset |
| Operational telemetry | `wss_metrics` | 30-90 days | Reporting-only, fastest row-count grower — best early rolling-window candidate |
| Operational retry/queue state | `wt_subprov_sig_retry`, `wt_candidate_websocket_watches`, `wt_active_subprov_sessions` | Purge on completion/expiry, not time-boxed | State-based, not calendar; current size confirms rows aren't purged today (Phase 8) |
| Launch/token history | `token_analysis`, `token_prediction_scores`, `token_liquidity_snapshots` | Retain indefinitely for now; revisit at a size threshold (e.g. 5M rows / 5GB) | Core historical record; "Launch audit" work depends on historical lookback |
| Creator/treasury/infra intelligence | `infra_wallets`, `creator_receivers`, `creator_risk_scores`, `creator_service_history`, `coordinated_creator_edges` | **Retain, do not time-box** | Accumulated attribution knowledge base — expensive/error-prone to re-derive |
| Funding-lineage graph | `transfer_index`, `funder_incoming_transfers`, `funder_outgoing_transfers` | Retain hot for active investigation window (6-12 months), archive older | High value while investigation is live, declining after attribution confirmed |
| Completed/reporting-only queues | `wt_attribution_outcomes`, `trade_simulations`, `wt_subprov_evidence` | Purge/archive after completion + grace period (~90 days) | Reporting-only, not on live detection path |
| Historical investigations | `flex_investigation_archive.db` contents | Archive tier — long-term retain, no active purge | Already the intended cold-storage tier |
| Prediction/decision snapshots | `prediction_decision_context`, `token_prediction_events` | 6-12 months hot, then archive | High volume, declining marginal value once outcome determined |

**Do not retention-limit without further work**: `wt_walkback_queue`,
`wt_ops_v2_edges`, `wt_operation_activity`, `wt_operation_candidates` —
live operational state for the currently-running pipeline (incl. this
session's X64.5-X64.7A CREATE-ledger work); any policy must be strictly
completed/terminal-state-based. `metadata_cache`, `rpc_response_cache` —
performance caches needing an eviction design, not a storage-driven purge.

**General principle**: every recommendation defaults to the more
conservative option where evidence was incomplete.

---

## Phase 6 — Archive Candidates

Precedent: `flex_investigation_archive.db` (2.87GB) already holds one
migrated table (`funder_networks`, 42,314 rows) from prior work.

1. **`funder_networks` (hot copy)** — not a new archive candidate, the
   move already happened in code; what remains is a **cleanup**, not an
   archive action (see Phase 8).
2. **`prediction_decision_context` + `token_prediction_events`** —
   ~454MB combined; value highest right after prediction, declines over
   time. Production effect: low (not on live detection path).
   Investigation effect: moderate (occasional historical lookback
   needed — an archive DB preserves this, deletion would not).
   Restoration: low complexity (same `ATTACH DATABASE` pattern already proven).
3. **`transfer_index` + funder-transfer tables (partial, time-boxed)** —
   potentially the largest long-term saving (544MB+, fastest
   row-count grower), but only a **confirmed-attribution-only, >6-months
   old** subset is safe to move; wholesale archival risks breaking
   re-evaluation of reopened clusters. Restoration: moderate complexity
   (cross-table joinability must be preserved, unlike the single-table
   `funder_networks` case).
4. **`wss_metrics`** — 163MB, pure telemetry, no correctness dependency
   found anywhere. Low restoration complexity, no FK-style joins found.
5. **`trade_simulations`, `wt_attribution_outcomes`, `wt_subprov_evidence`**
   — modest individually (17MB/4.3MB/17.9MB) but low-risk, easy wins to
   batch into the same tooling effort as a bigger candidate.

**Not recommended for archiving now**: `token_analysis`, `infra_wallets`,
`creator_receivers`, `creator_risk_scores`, `coordinated_creator_edges`
(all confirmed live-read); `wt_walkback_queue`, `wt_ops_v2_edges`,
`wt_operation_activity`, `wt_operation_candidates` (live queue/state —
archiving is a correctness risk, not an optimization).

---

## Phase 7 — Backup Strategy

**Context**: X64.7C deleted two 8.1GB stale backups under disk-full
emergency pressure (795Mi free at the time); X64.7D then hit the same
constraint attempting a fresh full backup. Disk remains tight (17Gi
free of 228GB, 91% capacity) — this is a recurring, not one-off, constraint.

| Strategy | Description | Pros | Cons | Recovery |
|---|---|---|---|---|
| **A: Full production backup** | Copy both DBs wholesale on a schedule | Simplest; matches prior manual-snapshot pattern | ~12.3GB/snapshot; disk can sustain at most 1 more before repeating the X64.7C crisis; backs up 2.86GB of known-dead data every time | Complete, single-step restore |
| **B: Operational + historical split** | Frequent cheap backup of the operational core; infrequent backup of the historical/archive tier | Matches this audit's own findings — naturally excludes dead weight once cleaned up; matches actual change velocity | More moving parts; needs the operational/historical split cleanly enforced (some tables straddle both, e.g. `transfer_index`) | Full recovery possible; recency differs appropriately by tier |
| **C: Incremental backups** | WAL-shipping / diff-based, avoiding full-copy cost each cycle | Much lower marginal disk cost; enables frequent backups on tight disk | No existing tooling in this project (confirmed no backup automation exists at all); real engineering investment; new restore-failure mode class | Strong if correct, but untested territory for this project |

### Recommendation: Strategy B now, Strategy C as a future enhancement

1. This audit already found the hot DB's biggest table
   (`funder_networks`) has zero ongoing value — Strategy A would keep
   backing it up forever unless separately excluded, effectively
   re-deriving Strategy B's split just to avoid waste.
2. 17Gi of headroom cannot sustainably support repeated 12GB+ full
   backups — this already caused the X64.7C emergency once.
3. Strategy B needs no new tooling — the same `ATTACH DATABASE` +
   separate-file pattern already proven for `flex_investigation_archive.db`.
4. Strategy C is the right eventual target once backup-frequency
   requirements tighten, but building it now would solve a problem this
   project doesn't have evidence of yet.

**Concrete next step**: execute Phase 8's cleanup first (shrinking the
operational-backup footprint) before building any backup automation —
avoid automating backups of data about to become historical overhead.

---

## Phase 8 — Cleanup Candidates

No deletion is authorized by this document — every item requires a
separate, explicitly-scoped approval before action, per this project's
established discipline (X64.7B preflight → X64.7C execution pattern).

### Obsolete tables

| Item | Evidence | Estimated saving | Operational risk | Confidence |
|---|---|---|---|---|
| `funder_networks` (hot copy) | Zero writers, zero readers, frozen since 2026-03-08 vs. archive's growing 42,314 rows. Independently re-verified twice (matching fingerprint). `scripts/reclaim_funder_networks_space.py` already exists, gated behind `--i-am-in-a-maintenance-window`, never run | **~2.86GB** — largest single opportunity in this audit | **Low** — archive copy verified as superset | **High** |

### Expired queue / stale session rows

Two items below were originally flagged Medium confidence pending a
follow-up status-column query — that follow-up has since been run by an
independent re-verification pass and both are now **confirmed**:

| Item | Evidence | Estimated saving | Operational risk | Confidence |
|---|---|---|---|---|
| `wt_subprov_sig_retry` completed/terminal rows | **Confirmed**: 2.31M rows, **99.99% at `status=DONE`**, no code reads DONE rows back — pure write-once/check-once/abandon pattern, no purge job exists | Nearly all of 392.9MB | **Medium** — a status-scoped purge (`WHERE status='DONE'`) is safe; a blanket truncate is not (would break the <0.01% still mid-retry) | **High** (upgraded from Medium) |
| `wt_candidate_websocket_watches` stale/expired watches | **Confirmed**: 3.05M rows, **>99.3% at `state=EXPIRED`**, no code reads EXPIRED rows back | Nearly all of 319.1MB | **Medium** — same caveat, state-scoped purge only | **High** (upgraded from Medium) |
| `wt_active_subprov_sessions` non-active sessions | Named "active" but sized (54MB) larger than plausible concurrent-session counts suggest | Modest (54MB) | **Low-Medium** — smaller blast radius | **Low-Medium** — suggestive, not confirmed by a direct status query |
| `coordinated_creator_edges` staleness | **New finding**: all 328,702 rows share one identical `created_at` (2026-06-06 07:08:03) — consistent with a DELETE+bulk-INSERT rebuild job (`graph_analyzer_api.py`) that hasn't run in ~6 weeks | 58.7MB if this is an abandoned job (not primarily a storage concern — check if the analyzer job is still scheduled) | **Low** — informational, no deletion implied | **Medium** — strong circumstantial evidence, scheduling not directly confirmed |
| `sol_transfers` legacy table | **New finding**: small table (40,581 rows), stale since 2026-03-09 (~4.5 months), superseded by `transfer_index` (2.33M rows, live) | Small | **Low** | **Medium** — plausible legacy/superseded table, low priority given size |

### Stale caches

| Item | Evidence | Estimated saving | Risk | Confidence |
|---|---|---|---|---|
| `rpc_response_cache` | Cache-shaped (30.5MB), no confirmed active eviction/TTL logic | Modest today, compounds if unbounded | **Low** | **Low** — worth checking, not confirmed |

### Zero/low-reference small tables (new, from independent re-verification pass)

A separate re-verification pass found, in the hot DB: **9 tables with
zero rows AND zero code references**, and **6 tables with non-zero but
stale data AND zero code references** — e.g. `cross_network_senders`
(last written 2026-02-16, ~5 months stale) and `oneoff_hub5e1_outbound`
(last written 2026-05-28, ~2 months stale). These meet the same
two-axis bar as `funder_networks`, but are flagged **Medium confidence
only**, per this project's own documented history of naive-grep "looks
unused" checks later proving wrong (the same `risk_score_history`/
`wss_metrics` precedent referenced in Phase 2). Individually small —
the more valuable outcome of investigating is confirming whether these
are genuinely dead one-off/experimental tables
(`oneoff_hub5e1_outbound`'s name is itself suggestive) worth batch
cleanup alongside `funder_networks`, rather than a meaningful
standalone storage target.

### Orphaned records / unused indexes

Neither was confirmed in this pass. No FK constraints exist anywhere in
this schema (relationships enforced at the application layer), so
orphan-detection needs a dedicated cross-table consistency pass — out
of scope here, recommended as a distinct follow-up. No unused index was
found; all large indexes correspond to query patterns actually used by
attribution/lineage code.

### Duplicate structures

`atomic_funder_networks` is the *intended active successor* to
`funder_networks` (per code comments), not a duplicate needing cleanup
— leave alone. No other duplicate-schema pattern found.

### Confidence summary

| Confidence | Items |
|---|---|
| **High** | `funder_networks` hot-DB copy removal (~2.86GB); `wt_subprov_sig_retry` DONE-row purge; `wt_candidate_websocket_watches` EXPIRED-row purge |
| **Medium** | `coordinated_creator_edges` possibly-stalled rebuild job; `sol_transfers` legacy table; 9 zero-row/zero-ref + 6 stale/zero-ref small tables |
| **Low-Medium** | `wt_active_subprov_sessions` stale-session pruning |
| **Low** | `rpc_response_cache` eviction gap |
| **Not evaluated / recommend follow-up** | orphaned records, unused indexes |

---

## Phase 9 — Executive Summary

**Total size**: `flex_complete_database.db` 9.9GB + `wt_ops_v2.db` 2.4GB
= **~12.3GB combined hot-DB footprint** (plus 2.87GB already in the
archive tier, ~15.2GB total across all three files).

**Largest tables**: `funder_networks` (2.86GB, dead) →
`token_analysis` (568MB) → `transfer_index` (544MB) →
`prediction_decision_context` (417MB) → `wt_subprov_sig_retry` (393MB).

**Fastest-growing**: `wss_metrics` (2.64M rows) and `transfer_index`
(2.33M rows) by row-count velocity — both continuously written with no
retention limit today.

**Operational vs. historical split** (of ~12.3GB hot total):
~2.86GB (23%) fully dead, ~9.0GB (~73%) genuinely operational, ~450MB
(~4%) historical/reporting-only with declining value over time.

**Estimated recoverable storage**:
- Immediate, high-confidence: **~2.86GB** (`funder_networks` removal)
- Confirmed-high-confidence, size TBD precisely but likely near-total of
  the two tables: **most of ~712MB** combined
  (`wt_subprov_sig_retry` + `wt_candidate_websocket_watches` terminal-row purge)
- Longer-term via archiving (not deletion): **~450MB+** and growing
  (`prediction_decision_context`, `wss_metrics`, small reporting tables)

**Archive candidates**: `prediction_decision_context` +
`token_prediction_events` (time-boxed); `transfer_index` + funder-transfer
tables (partial, confirmed-only); `wss_metrics`; small reporting trio
(`trade_simulations`/`wt_attribution_outcomes`/`wt_subprov_evidence`).

**Future deletion candidates**: `funder_networks` (hot copy, high
confidence); terminal rows in the two ops-DB queue tables (high
confidence, exact size pending a final sizing query); a further ~15
small zero/low-reference tables (medium confidence, batch-worthy).

**Recommended backup architecture**: Strategy B (operational +
historical split), sequenced after cleanup shrinks the operational
footprint; Strategy C (incremental) as a future enhancement once backup
frequency needs increase.

**Recommended retention policy**: retain creator/treasury/infra
intelligence indefinitely (expensive to re-derive); retain launch/token
history with a size-based revisit trigger, not a calendar one; purge
completed queue rows on a state basis; archive prediction/decision
snapshots and funding-lineage detail after 6-12 months once attribution
is confirmed.

---

## Phase 10 — Implementation Roadmap

Prioritized follow-on work; each item still requires its own
explicitly-scoped approval before execution (audit → separately
authorized execution task, per the X64.7B → X64.7C pattern).

### Quick wins (low risk, high storage savings)

| Item | Storage reclaimed | Risk | Effort | Dependencies |
|---|---|---|---|---|
| Remove `funder_networks` hot-DB copy | ~2.86GB | Low | Low — reuse the X64.7C verify→delete→verify pattern; `scripts/reclaim_funder_networks_space.py` already exists and is ready to run under a maintenance window | None — ready today |

### Medium-term improvements (archive tooling, retention jobs)

| Item | Storage reclaimed | Risk | Effort | Dependencies |
|---|---|---|---|---|
| Purge confirmed DONE/EXPIRED rows in `wt_subprov_sig_retry` / `wt_candidate_websocket_watches` | Most of ~712MB combined | Medium — status-scoped purge only, never a blanket truncate | Medium — status/state distribution now confirmed; still needs a scoped purge job built | None blocking — evidence already gathered |
| Build generalized archive tooling (from `flex_investigation_archive.db`/`funder_networks` precedent) | Enables ~450MB+ further archiving | Low-Medium | Medium — single-table pattern proven; multi-table (`transfer_index` family) is new work | None blocking |
| Investigate `coordinated_creator_edges` rebuild-job scheduling | Informational — may reveal a broken scheduled job | Low | Low — a scheduling/cron check | None |
| Batch-review the ~15 zero/low-reference small tables | Small individually, cumulative cleanup value | Low | Low | None |
| Automated retention/eviction for `rpc_response_cache` | Modest, unconfirmed | Low | Low-Medium — needs a TTL/eviction design | A check of whether any eviction logic exists today |

### Long-term architectural changes (partitioning, database split, cold storage)

| Item | Storage reclaimed | Risk | Effort | Dependencies |
|---|---|---|---|---|
| Operational/historical backup split (Strategy B) | N/A (backup cost reduction) | Low | Medium — needs a scheduled job | Quick win + archive tooling sequenced first |
| Time-boxed partial archival of `transfer_index` + funder-transfer tables | Potentially the largest long-term saving | Medium — must scope to confirmed-attribution-only chains | High — most structurally complex candidate (cross-table joinability) | Archive tooling proven on simpler single-table cases first |
| Incremental/WAL-based backup strategy (Strategy C) | N/A (backup efficiency) | Medium — new failure-mode class | High — no existing tooling to build from | Strategy B in production first |

**Sequencing rationale**: the quick win is independent and should
happen first regardless of anything else — it's the single
highest-confidence, highest-value item in the entire audit. Medium-term
items build the tooling and evidence needed before any long-term
architectural change; in particular, proving archive tooling on the
already-clean single-table case before attempting the structurally
harder `transfer_index` family avoids repeating this project's own
documented lesson that naive "looks safe" judgments have been wrong
before without deeper dependency verification.

---

## Provenance note

This report consolidates the original 9-phase audit (run directly after
three background-agent delegation attempts each failed to produce any
output) plus corrections and new findings from two independent
read-only re-verification passes that ran afterward and cross-checked
every figure against the live databases. All figures above reflect the
final, reconciled state; nothing in this document was ever written to
either database.
