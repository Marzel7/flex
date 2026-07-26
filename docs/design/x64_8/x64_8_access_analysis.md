# X64.8 — Phase 4: Access Analysis

Per-table read/write status for the ≥1MB tier, based on direct code
search (`grep -rn` across `src/`) rather than assumption.

## `database/flex_complete_database.db`

| Table | Written? | Read? | Frequency | Prod-critical? | Notes (file:line evidence) |
|---|---|---|---|---|---|
| `funder_networks` | **No** (hot copy) | **No** (hot copy) | Never (frozen 2026-03-08) | **No — dead in hot DB** | `main.py:84-121` attaches archive DB and reads `arch.funder_networks` exclusively; `cross_funding_network_analyzer.py:223,302,360-362` confirms writes redirected to archive |
| `token_analysis` | Yes | Yes | Continuous (every launch/migration) | Yes | `pumpfun_curve_listener.py` (birth/migration handlers), read throughout `main.py` |
| `transfer_index` | Yes | Yes | Continuous (every funding extraction) | Yes | `realtime_creator_funding_extractor.py`, `funder_incoming_extractor.py` |
| `prediction_decision_context` | Yes | Yes | Continuous (every prediction) | Yes | `token_prediction_builder.py`, `prediction_decision_context.py` |
| `watchtower_infra_events` | Yes | Yes | Continuous but lower-volume | Yes | `watchtower_detector.py`, `watchtower_init.py`, `schema_init.py` (schema only, not a read/write path) |
| `token_prediction_scores` | Yes | Yes | Continuous | Yes | `token_prediction_builder.py`, `pumpfun_curve_listener.py`, `risk_axis.py` |
| `wss_metrics` | Yes | Yes | Continuous, high-frequency (one row/WS event) | Reporting-only — telemetry, not decision-path | `usage_tracker.py` (writer), `main.py` (metrics dashboard reader) |
| `metadata_cache` | Yes | Yes | Continuous | Yes (perf-critical cache) | metadata-fetch paths write-through; most UI/analysis modules read |
| `token_liquidity_snapshots` | Yes | Yes | Continuous, periodic | Yes (chart/analysis paths) | price/liquidity worker; chart reads |
| `creator_receivers` | Yes | Yes | Continuous | Yes | funding extractors; attribution modules |
| `infra_wallets` | Yes | Yes | Continuous | Yes | WATCHTOWER detection modules |
| `coordinated_creator_edges` | Yes | Yes | Continuous | Yes (coordination views) | cross-funding analyzer family |
| `creator_service_history` | Yes | Yes (occasional) | Low-frequency read, moderate write | Historical-only in practice | serial-deployer detection paths |
| `trade_simulations` | Yes (batch) | Rare | Batch-only | No — reporting/backtest only | analysis/backtest modules |
| `rpc_response_cache` | Yes | Yes | Continuous but self-limiting (cache) | Perf-only, not correctness-critical | RPC-calling modules |

## `database/wt_ops_v2.db`

| Table | Written? | Read? | Frequency | Prod-critical? | Notes |
|---|---|---|---|---|---|
| `wt_subprov_sig_retry` | Yes | Yes | Continuous (queue churn) | Yes — active retry path | `walkback_worker.py` / anchor-reconciliation retry logic |
| `wt_candidate_websocket_watches` | Yes | Yes | Continuous | Yes | `ws_cascade.py` |
| `watchtower_events` | Yes | Yes | Continuous | Yes | multiple detection modules; `operation_dashboard_routes.py` |
| `wt_cdc_outbound_events` | Yes | Yes | Continuous | Yes (CDC pipeline) | webhook/CDC pipeline |
| `wt_active_subprov_sessions` | Yes | Yes | Continuous | Yes | `ws_cascade.py`; session-health dashboards |
| `wt_subprov_evidence` | Yes | Yes (occasional) | Moderate | Review/audit-only | classification modules; review UI |
| `wt_operation_activity` | Yes | Yes | Continuous | Yes | `operation_scheduler.py`; `operation_dashboard_routes.py` |
| `wt_operation_candidates` | Yes | Yes | Continuous | Yes | discovery/scoring modules |
| `wt_swarm_buys` | Yes | Yes (occasional) | Moderate | Attribution-only | swarm detector; attribution modules |
| `wt_fanout_events` | Yes | Yes | Continuous | Yes | `ws_cascade.py`; attribution modules |
| `wt_attribution_outcomes` | Yes | Yes (occasional) | Low-moderate | Reporting-only | `attribution_outcome.py` |
| `wt_ops_v2_edges` | Yes | Yes | Continuous | Yes | operation-centric store |
| `wt_walkback_queue` | Yes | Yes | Continuous | Yes — core reconciliation queue | `walkback_worker.py`, `anchor_reconciliation.py`, `create_event_ledger.py` (this session's X64.5-X64.7A work) |

## Cross-cutting observations

- **UI-only / reporting-only tables** identified: `trade_simulations`,
  `wt_attribution_outcomes`, `wt_subprov_evidence` (review UI), `wss_metrics`
  (metrics dashboard). None of these gate production detection —
  they could tolerate more aggressive retention limits without breaking
  the live pipeline (see Phase 5).
- **Historical-only**: `creator_service_history` — written continuously
  but read only occasionally (lookup during classification, not on
  every detection cycle).
- **Confirmed production-critical, high-frequency, no retention
  headroom without a deliberate policy**: `token_analysis`,
  `transfer_index`, `prediction_decision_context`,
  `wt_walkback_queue`, `wt_ops_v2_edges` — any retention policy touching
  these needs explicit scoping (partial/time-boxed, not blanket).
- **Zero access, any direction**: only `funder_networks` (hot-DB copy)
  meets this bar with code-level confirmation.
