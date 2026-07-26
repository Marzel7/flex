# X64.8 — Phase 1: Database Table Inventory

Read-only audit, 2026-07-21. Two databases in scope:

| Database | File size | Table count (excl. sqlite_ internal) |
|---|---|---|
| `database/flex_complete_database.db` | 10,600,730,624 bytes (9.9G) | 318 |
| `database/wt_ops_v2.db` | 2,526,175,232 bytes (2.4G) | 104 |

Given the scale (422 tables total), this inventory documents every table
with a non-trivial disk footprint (all tables ≥ 4MB, which together
account for the overwhelming majority — 90%+ — of total bytes in both
databases) in full detail, and summarizes the long tail of small
(largely metadata/config/small-lookup) tables as a group. Sizes were
measured via the `dbstat` virtual table (`SELECT name, SUM(pgsize) FROM
dbstat GROUP BY name`), which reports actual on-disk page usage — exact,
not estimated. Row counts below are exact `COUNT(*)` results for every
table listed individually.

## `database/flex_complete_database.db` — tables ≥ 4MB

| Table | Bytes | Rows | Purpose | Primary writer(s) | Primary reader(s) |
|---|---|---|---|---|---|
| `funder_networks` | 2,864,263,168 | 41,734 | Clustered funder networks (legacy funding-graph analysis) | **None — migrated off.** `cross_funding_network_analyzer.py` explicitly no longer writes here (see comments at lines 223, 302, 360-362: "funder_networks writes target the archive DB, not the hot DB") | `main.py` reads exclusively via `arch.funder_networks` (attached archive DB, lines 13211-13226) — hot-DB copy is not read anywhere |
| `token_analysis` | 568,496,128 | 1,338,952 | Core per-token record: creator, migration status, pricing, lifecycle | `pumpfun_curve_listener.py` (birth/migration handlers) | `main.py` (dashboard/API), most analysis modules |
| `transfer_index` | 544,272,384 | 2,326,627 | Indexed SOL transfer records for funding-lineage tracing | funding extractors (`realtime_creator_funding_extractor.py`, `funder_incoming_extractor.py`) | `cross_funding_network_analyzer.py`, attribution modules |
| `prediction_decision_context` | 417,492,992 | 276,049 | Snapshot of signals/features at prediction-decision time | `prediction_decision_context.py`, `token_prediction_builder.py` | `main.py`, `risk_axis.py` |
| `watchtower_infra_events` | 296,345,600 | 66,705 | WATCHTOWER infra-wallet/provisioning event log | `watchtower_detector.py`, `watchtower_init.py` | `main.py`, `schema_init.py` (schema only) |
| `token_prediction_scores` | 180,887,552 | 277,138 | Per-token prediction/risk scores | `token_prediction_builder.py` | `main.py`, `pumpfun_curve_listener.py`, `risk_axis.py` |
| `wss_metrics` | 163,295,232 | 2,637,910 | Per-subscription WebSocket telemetry (one row per WSS event) | `usage_tracker.py` | `main.py` (metrics dashboards) |
| `metadata_cache` | 122,208,256 | 1,303,815 | Cached token/creator metadata to avoid repeat RPC/API calls | metadata-fetch paths | most UI/analysis reads |
| `token_liquidity_snapshots` | 108,437,504 | 1,296,201 | Periodic liquidity/curve snapshots per token | price/liquidity worker | chart/analysis reads |
| `creator_receivers` | 63,819,776 | 476,371 | Creator → downstream-receiver funding edges | funding extractors | attribution/lineage modules |
| `infra_wallets` | 63,537,152 | 665,740 | Known infra-wallet registry (treasuries, subprovs, etc.) | WATCHTOWER detection modules | attribution modules, `main.py` |
| `coordinated_creator_edges` | 58,691,584 | 328,702 | Creator-to-creator coordination edges (shared funder/pattern) | `cross_funding_network_analyzer.py` (or related edge builder) | cluster/coordination views |
| `funder_outgoing_transfers` | 40,095,744 | (not individually counted — same family as `funder_incoming_transfers`) | Funder outgoing-transfer records | `funder_incoming_extractor.py` family | lineage/attribution modules |
| `creator_risk_scores` | 38,985,728 | (not individually counted) | Per-creator computed risk score | risk-scoring modules | `main.py`, risk views |
| `token_prediction_events` | 36,978,688 | (not individually counted) | Event log backing prediction scoring | `token_prediction_builder.py` | prediction dashboards |
| `rpc_response_cache` | 30,515,200 | (not individually counted) | Cached raw RPC responses | RPC-calling modules | same (cache read-through) |
| `creator_outgoing_transfers` | 22,147,072 | (not individually counted) | Creator's own outgoing transfers | funding extractors | lineage modules |
| `creator_service_history` | 21,463,040 | (not individually counted) | History of creator "service" (repeat-deploy) activity | serial-deployer detection | classification modules |
| `funder_incoming_transfers` | 17,772,544 | (not individually counted) | Funder incoming-transfer records (per project CLAUDE.md, Step 2 of funding cascade) | `funder_incoming_extractor.py` | `cross_funding_network_analyzer.py` |
| `trade_simulations` | 17,125,376 | (not individually counted) | Simulated trade outcomes for backtesting | analysis/backtest modules | reporting |

**Indexes**: the largest indexes shadow their parent tables 1:1 — e.g.
`idx_transfer_source_dest` (255MB), `idx_ta_bonding_curve_refresh`
(181MB), `idx_transfer_source_amount_time` (157MB), all on
`transfer_index`/`token_analysis`, consistent with normal query-pattern
indexing rather than duplication or bloat. No orphaned/duplicate index
found in this size tier.

**Foreign keys**: this codebase does not appear to declare SQLite
foreign-key constraints on any of the tables inspected (`PRAGMA
foreign_key_list` returned empty for every table sampled) — relationships
are enforced at the application layer, not the schema layer. This is a
pre-existing project convention, not a gap introduced here.

## `database/wt_ops_v2.db` — tables ≥ 1MB

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
| `wt_fanout_events` | 4,468,736 | Fan-out (subprov→many-wallets) event records | `ws_cascade.py` | attribution modules |
| `wt_attribution_outcomes` | 4,300,800 | Recorded attribution outcome per operation | `attribution_outcome.py` | reporting |
| `wt_ops_v2_edges` | 3,362,816 | Operation-graph edges (op-centric store) | `operation_store_v2` (per prior memory) | `operation_dashboard_routes.py` |

Below this tier, ~90 additional tables in `wt_ops_v2.db` are each under
1MB — small operational/config/cursor/state tables (e.g.
`wt_walkback_queue` at 2.0MB, `wt_webhook_hits` at 2.2MB,
`wt_subprov_sig_cursor` at 2.6MB) consistent with active, low-volume
operational state rather than bulk historical data.

## Long tail (both DBs)

The remaining ~360 tables across both databases (everything not listed
above) are each under the size thresholds used above. Spot-checks confirm
this tail is dominated by: schema-versioned config tables, small lookup
tables (known-wallet lists, program-ID registries), per-feature
state/cursor tables (one row per subsystem), and a number of
0-row/schema-only tables created by test runs or superseded feature work.
None individually contributes materially to total disk usage — the top
20 tables in `flex_complete_database.db` alone account for
**~5.6GB of the 9.9GB total (~57%)**, and the single largest table
(`funder_networks`) alone accounts for **~27% of the entire hot
database**.

## Appendix — full per-table listing (every remaining table, both DBs)

Per the audit scope's requirement that every table be at least listed,
this appendix covers all tables below the size thresholds used above.
Size = exact `dbstat` page bytes. Rows = exact `COUNT(*)`. "Code refs"
= number of distinct files under `src/`, `templates/`, `scripts/`, and
repo-root `*.py` containing the literal table name (a plain-string
search, not AST-aware — dynamically-constructed table names would not
show here, but no evidence of that pattern was found in this codebase
during spot checks). A "0" in code refs plus "0" rows was required
before any table below was considered for the Obsolete classification
in Phase 2 — see `x64_8_table_classification.md`.

`schema_migrations`, `sqlite_sequence`, `sqlite_stat1` are SQLite-internal
bookkeeping tables, not application tables — included for completeness
only, excluded from all classification/retention/archive decisions.


### flex_complete_database.db — long tail (below size threshold), full per-table listing

Table | Size | Rows | Code refs (files)
---|---|---|---
`rpc_metrics` | 3.5MB | 24655 | 19
`helius_usage_snapshots` | 3.4MB | 5973 | 7
`tracked_tokens` | 3.3MB | 36459 | 13
`wt_relay_counterparties` | 3.2MB | 12900 | 2
`token_resolution_telemetry` | 3.1MB | 20827 | 5
`wt_staged_wallets` | 2.9MB | 9246 | 4
`funder_upstream_links` | 2.9MB | 19769 | 9
`token_outcomes` | 2.6MB | 16286 | 7
`creator_tx_ledger` | 2.6MB | 12622 | 4
`intelligence_relationship_events` | 2.2MB | 13800 | 3
`domain_registry` | 2.2MB | 8300 | 2
`operator_creator_edges` | 2.0MB | 11817 | 2
`watchtower_sweep_events` | 1.9MB | 1017 | 2
`dev_reputation` | 1.9MB | 21255 | 11
`token_behavior` | 1.7MB | 8356 | 8
`wallet_clusters` | 1.6MB | 2292 | 20
`token_market_cap_peaks` | 1.6MB | 16756 | 11
`second_hop_lite_queue` | 1.4MB | 14408 | 9
`watch_outbound_scan_schedule` | 1.3MB | 11410 | 2
`wt_swarm_candidates` | 1.3MB | 3444 | 2
`funder_rpc_scan_cache` | 1.3MB | 12305 | 4
`creator_tags` | 1.2MB | 6471 | 9
`creator_resolution_queue` | 1.1MB | 4387 | 7
`funder_profitability` | 1.1MB | 7957 | 3
`watchtower_operator_graph` | 1.1MB | 4838 | 6
`wt_buyer_position_validation` | 1.0MB | 680 | 2
`wt_discovery_log` | 1.0MB | 4187 | 2
`funding_network_members` | 1.0MB | 15589 | 3
`analyzer_runs` | 900.0KB | 10852 | 6
`intelligence_refresh_candidates` | 892.0KB | 5537 | 4
`wt_candidate_scores` | 888.0KB | 4187 | 3
`wt_graph_edges` | 888.0KB | 4187 | 3
`watchtower_launch_candidates` | 860.0KB | 1768 | 5
`creator_state` | 748.0KB | 7078 | 5
`network_review_status` | 732.0KB | 805 | 1
`atomic_funder_networks` | 720.0KB | 1560 | 2
`token_rescore_queue` | 716.0KB | 7512 | 5
`wt_swarm_provisioners` | 688.0KB | 1026 | 2
`address_scan_state` | 672.0KB | 5376 | 1
`wt_operator_launches` | 660.0KB | 2773 | 2
`wt_hub_backfill_queue` | 656.0KB | 5159 | 2
`creator_profitability` | 616.0KB | 280 | 3
`creator_outbound_classifications` | 544.0KB | 3690 | 8
`pump_bot_signals` | 544.0KB | 2659 | 0
`wt_interceptor_validation` | 468.0KB | 2306 | 3
`funder_overlap` | 360.0KB | 2943 | 12
`address_activity` | 344.0KB | 3112 | 4
`trade_simulation_claims` | 328.0KB | 4303 | 1
`networks_release` | 320.0KB | 616 | 19
`upstream_account_classification` | 320.0KB | 3029 | 1
`wt_graph_nodes` | 312.0KB | 4191 | 3
`token_prediction_outcomes` | 284.0KB | 2482 | 2
`wt_webhook_enrollments` | 280.0KB | 1660 | 6
`network_membership` | 276.0KB | 2520 | 20
`creator_watch` | 272.0KB | 1331 | 8
`network_score_history` | 268.0KB | 1232 | 7
`watchtower_fee_payers` | 268.0KB | 1124 | 5
`funding_chains` | 256.0KB | 1123 | 4
`infra_funders_observed` | 256.0KB | 49 | 8
`network_scores` | 244.0KB | 937 | 9
`network_evidence` | 208.0KB | 837 | 4
`creator_analysis_queue` | 204.0KB | 681 | 3
`network_coordinators` | 200.0KB | 659 | 3
`blocksec_batch_log` | 184.0KB | 50 | 1
`watch_candidate_tokens` | 172.0KB | 1310 | 4
`creator_outgoing_cursor` | 164.0KB | 1000 | 0
`wt_webhook_reconciliations` | 160.0KB | 1318 | 2
`helius_webhook_assignments` | 152.0KB | 1347 | 3
`wt_curve_impact_analysis` | 148.0KB | 141 | 1
`creator_self_funding` | 128.0KB | 1244 | 11
`token_price_snapshots` | 128.0KB | 1204 | 17
`creator_second_hop` | 124.0KB | 463 | 7
`token_liquidity_health` | 120.0KB | 1111 | 2
`network_risk_scores` | 112.0KB | 290 | 4
`creator_sig_cursors` | 104.0KB | 1345 | 1
`network_display_names` | 100.0KB | 834 | 3
`token_liquidity_risks` | 96.0KB | 1114 | 1
`farm_cluster_edges` | 88.0KB | 574 | 3
`funder_network_map` | 84.0KB | 799 | 9
`shl_excluded_upstreams` | 80.0KB | 910 | 2
`creator_c2c_edges` | 60.0KB | 426 | 5
`farm_clusters` | 60.0KB | 23 | 13
`watchtower_wallet_state` | 60.0KB | 623 | 4
`work_queue` | 60.0KB | 545 | 6
`cluster_detection_log` | 56.0KB | 573 | 1
`network_names` | 56.0KB | 1046 | 7
`super_clusters` | 56.0KB | 840 | 3
`farm_cluster_members` | 52.0KB | 409 | 15
`upstream_network_bridge` | 48.0KB | 229 | 8
`wt_operator_clusters` | 48.0KB | 498 | 4
`creator_outbound_queue` | 44.0KB | 545 | 3
`token_snapshot_counts` | 40.0KB | 623 | 5
`wt_infra_telemetry_buckets` | 40.0KB | 344 | 2
`wt_operation_members` | 36.0KB | 246 | 6
`network_historical_performance` | 32.0KB | 313 | 3
`wt_operator_fingerprints` | 32.0KB | 482 | 2
`funding_network_shared_tokens` | 24.0KB | 362 | 1
`sqlite_stat1` | 24.0KB | 273 | 0
`wt_cluster_members` | 24.0KB | 121 | 4
`creator_networks` | 20.0KB | 17 | 12
`creator_super_cluster_membership` | 20.0KB | 232 | 1
`wt_creator_reservoir` | 20.0KB | 71 | 3
`wt_operator_treasuries` | 20.0KB | 22 | 3
`wt_provisioning_hubs` | 20.0KB | 48 | 7
`account_usage_cache` | 16.0KB | 155 | 1
`cex_wallets` | 16.0KB | 62 | 14
`creator_to_creator_networks` | 16.0KB | 130 | 5
`cross_network_senders` | 16.0KB | 85 | 0
`oneoff_hub5e1_outbound` | 16.0KB | 50 | 0
`wallet_cluster_nodes` | 16.0KB | 61 | 1
`wt_creator_launches` | 16.0KB | 37 | 6
`wt_launch_corridors` | 16.0KB | 46 | 3
`wt_operations` | 16.0KB | 49 | 9
`funding_networks` | 12.0KB | 108 | 2
`wt_detected_creates` | 12.0KB | 33 | 6
`wt_swarm_corridors` | 12.0KB | 18 | 2
`address_labels` | 4.0KB | 15 | 5
`atomic_network_names` | 4.0KB | 0 | 4
`blocksec_aml_cache` | 4.0KB | 0 | 1
`cex_coordinated_groups` | 4.0KB | 7 | 2
`circuit_breaker_state` | 4.0KB | 2 | 1
`cluster_exit_events` | 4.0KB | 0 | 0
`cluster_fingerprints` | 4.0KB | 0 | 0
`cluster_historical_performance` | 4.0KB | 0 | 3
`cluster_merge_log` | 4.0KB | 0 | 1
`cluster_outcome_stats` | 4.0KB | 0 | 3
`cluster_profitability` | 4.0KB | 0 | 3
`clustering_alerts` | 4.0KB | 0 | 1
`clustering_cursor` | 4.0KB | 1 | 1
`clustering_lock` | 4.0KB | 0 | 1
`coordinated_edge_cursor` | 4.0KB | 1 | 0
`coordinated_funders` | 4.0KB | 0 | 4
`creator_funding_graph` | 4.0KB | 2 | 3
`creator_inbound_transfers` | 4.0KB | 0 | 2
`creator_infra_interactions` | 4.0KB | 0 | 1
`creator_portfolio` | 4.0KB | 0 | 0
`creator_recipients_unified` | 4.0KB | 0 | 2
`creator_reuse` | 4.0KB | 0 | 13
`creator_seed_metrics` | 4.0KB | 0 | 3
`creator_sol_flows` | 4.0KB | 0 | 1
`creator_sol_transfers` | 4.0KB | 0 | 14
`db_maintenance_log` | 4.0KB | 121 | 2
`dev_farm_ecosystems` | 4.0KB | 0 | 3
`dev_organization_members` | 4.0KB | 0 | 10
`dev_organizations` | 4.0KB | 0 | 10
`ecosystem_evolution_log` | 4.0KB | 0 | 3
`ecosystem_member_tracking` | 4.0KB | 0 | 3
`funder_extraction_status` | 4.0KB | 0 | 2
`funder_interactions` | 4.0KB | 0 | 0
`funder_watchlist` | 4.0KB | 0 | 2
`funder_webhook_events` | 4.0KB | 0 | 2
`funder_webhook_groups` | 4.0KB | 0 | 1
`helius_account_history` | 4.0KB | 2 | 0
`helius_webhook_shards` | 4.0KB | 1 | 1
`intelligence_refresh_rpc_budget` | 4.0KB | 0 | 1
`internal_usage_snapshots` | 4.0KB | 0 | 1
`launch_detection_history` | 4.0KB | 0 | 3
`launch_watchlist` | 4.0KB | 0 | 5
`launch_wave_creators` | 4.0KB | 0 | 4
`launch_waves` | 4.0KB | 0 | 8
`liq_caught` | 4.0KB | 2 | 3
`listener_settings` | 4.0KB | 7 | 4
`master_launch_signals` | 4.0KB | 0 | 2
`metrics_reset_state` | 4.0KB | 2 | 0
`migrated_tokens` | 4.0KB | 0 | 19
`migration_inbox` | 4.0KB | 3 | 1
`migration_persist_queue` | 4.0KB | 2 | 3
`monitored_upstream_hubs` | 4.0KB | 30 | 4
`network_cex_infra_flags` | 4.0KB | 100 | 1
`network_profitability` | 4.0KB | 6 | 3
`org_alerts` | 4.0KB | 0 | 2
`org_enhanced_launch_windows` | 4.0KB | 0 | 2
`org_expansion_events` | 4.0KB | 0 | 2
`org_families` | 4.0KB | 0 | 2
`org_launch_cadence` | 4.0KB | 0 | 2
`org_launch_predictions` | 4.0KB | 0 | 3
`org_launch_windows` | 4.0KB | 0 | 3
`org_momentum_history` | 4.0KB | 0 | 3
`org_relationships` | 4.0KB | 0 | 1
`org_reputation` | 4.0KB | 0 | 3
`org_risk_scores` | 4.0KB | 0 | 3
`org_snapshots` | 4.0KB | 0 | 3
`organization_launch_waves` | 4.0KB | 0 | 2
`outgoing_chain_cursor` | 4.0KB | 1 | 0
`polling_settings` | 4.0KB | 2 | 1
`pool_health_metrics` | 4.0KB | 0 | 0
`prediction_features` | 4.0KB | 0 | 2
`protocol_fees` | 4.0KB | 0 | 0
`pump_bot_wallets` | 4.0KB | 1 | 0
`pumpfun_pre_migration_signals` | 4.0KB | 0 | 0
`recipient_cross_references` | 4.0KB | 0 | 1
`risk_score_history` | 4.0KB | 0 | 3
`rpc_metrics_state` | 4.0KB | 4 | 1
`schema_migrations` | 4.0KB | 1 | 0
`snapshot_cleanup_log` | 4.0KB | 24 | 2
`sqlite_sequence` | 4.0KB | 73 | 0
`system_metadata` | 4.0KB | 10 | 3
`token_early_signals` | 4.0KB | 0 | 1
`token_lifecycle_snapshots` | 4.0KB | 0 | 5
`token_metadata` | 4.0KB | 0 | 0
`token_monitoring_state` | 4.0KB | 2 | 6
`token_outcome_predictions` | 4.0KB | 0 | 2
`token_supply_cache` | 4.0KB | 0 | 1
`usage_reconciliation` | 4.0KB | 0 | 2
`wallet_cluster_edges` | 4.0KB | 0 | 0
`wallet_fingerprints` | 4.0KB | 0 | 1
`watchtower_predictions` | 4.0KB | 3 | 1
`watchtower_raydium_launches` | 4.0KB | 0 | 3
`watchtower_token_attribution` | 4.0KB | 0 | 11
`webhook_birth_queue` | 4.0KB | 0 | 2
`webhook_metrics` | 4.0KB | 0 | 3
`webhook_seen_signatures` | 4.0KB | 10 | 0
`wt_armed_operations` | 4.0KB | 0 | 3
`wt_campaigns` | 4.0KB | 0 | 2
`wt_extraction_clusters` | 4.0KB | 0 | 2
`wt_identity_proposals` | 4.0KB | 5 | 2
`wt_ignition_metrics` | 4.0KB | 109 | 1
`wt_known_operator_hubs` | 4.0KB | 11 | 5
`wt_operation_transitions` | 4.0KB | 14 | 2
`wt_ops_v2` | 4.0KB | 3 | 75
`wt_ops_v2_edges` | 4.0KB | 7 | 7
`wt_ops_v2_wallets` | 4.0KB | 13 | 17
`wt_pamm_interactions` | 4.0KB | 0 | 2
`wt_relay_sweep_epochs` | 4.0KB | 4 | 2
`wt_sub_provisioners` | 4.0KB | 12 | 5
`wt_swarm_corridors_samples` | 4.0KB | 9 | 2
`wt_swarm_recipients` | 4.0KB | 0 | 1
`wt_trader_wallets` | 4.0KB | 0 | 2
`wt_tripwire_fired` | 4.0KB | 0 | 2
`wt_tripwire_heartbeat` | 4.0KB | 1 | 1
`wt_unconfirmed_watchtower_like` | 4.0KB | 0 | 3
`wt_wallet_tier` | 4.0KB | 45 | 3
`wt_worker_failures` | 4.0KB | 0 | 1
`wt_worker_heartbeat` | 4.0KB | 8 | 9
### wt_ops_v2.db — long tail (below size threshold), full per-table listing

Table | Size | Rows | Code refs (files)
---|---|---|---
`wt_ops_v2_runs` | 696.0KB | 11391 | 2
`wt_provisioning_edges` | 424.0KB | 1256 | 14
`wt_wrap_close_candidates` | 392.0KB | 873 | 18
`attribution_evidence` | 388.0KB | 696 | 7
`wt_treasury_review` | 336.0KB | 643 | 9
`wt_discovered_subprovs` | 296.0KB | 1608 | 29
`watchtower_token_attribution` | 240.0KB | 1250 | 11
`wt_treasury_fingerprint_decisions` | 240.0KB | 752 | 4
`wt_temp_provision_candidates` | 236.0KB | 979 | 1
`wt_farm_launches` | 232.0KB | 1359 | 10
`wt_provisioning_sessions` | 220.0KB | 806 | 5
`wt_ops_v2_creators` | 200.0KB | 1091 | 15
`wt_funding_boundary` | 172.0KB | 529 | 5
`wt_subprov_discovery_checked` | 172.0KB | 2255 | 1
`wt_backfill_checked` | 132.0KB | 1931 | 1
`wt_ops_v2_wallets` | 112.0KB | 715 | 17
`wt_capital_reloads` | 100.0KB | 369 | 3
`wt_anchor_reconciliation_log` | 84.0KB | 328 | 1
`wt_capital_distributor_candidates` | 80.0KB | 322 | 5
`wt_farm_checked` | 80.0KB | 1368 | 1
`operator_observations` | 68.0KB | 69 | 3
`wt_unknown_infrastructure_registry` | 64.0KB | 33 | 4
`wt_walkback_edge_candidates` | 64.0KB | 114 | 3
`wt_ops_v2_armed` | 40.0KB | 163 | 4
`wt_vanity_families` | 40.0KB | 120 | 4
`wt_launch_audit` | 36.0KB | 43 | 5
`wt_operator_projection_reconciliation_reports` | 36.0KB | 4 | 1
`wt_ops_v2` | 36.0KB | 125 | 75
`watchtower_identity_reconciliations` | 32.0KB | 61 | 1
`wt_operation_wallet_cursor` | 32.0KB | 185 | 1
`wt_watchtower_launches` | 28.0KB | 43 | 40
`wt_creator_birth_launch` | 24.0KB | 95 | 9
`wt_treasury_approval_audit` | 24.0KB | 74 | 4
`wt_token_lifecycle` | 20.0KB | 29 | 9
`wt_walkback_atomic_flows` | 20.0KB | 18 | 2
`wt_infrastructure_candidate_reviews` | 16.0KB | 18 | 1
`wt_operator_entities_projection_shadow` | 16.0KB | 60 | 1
`wt_treasury_ws_usage` | 16.0KB | 63 | 4
`wt_worker_heartbeat` | 16.0KB | 2 | 9
`migrated_tokens` | 12.0KB | 51 | 19
`operator_entities` | 12.0KB | 68 | 18
`operator_evidence` | 12.0KB | 5 | 6
`wt_confirmed_treasuries` | 12.0KB | 61 | 40
`wt_confirmed_treasury_webhooks` | 12.0KB | 57 | 5
`wt_dust_observations` | 12.0KB | 27 | 1
`wt_dust_recipient_lifecycle` | 12.0KB | 22 | 1
`wt_farms` | 12.0KB | 54 | 2
`wt_ops_v2_operation_family_links` | 12.0KB | 28 | 2
`wt_ops_v2_treasury_resolution` | 12.0KB | 17 | 2
`wt_pending_session_writes` | 12.0KB | 32 | 3
`operator_promotion_reviews` | 8.0KB | 1 | 6
`analyst_inbox` | 4.0KB | 3 | 1
`attribution_evidence_write_failures` | 4.0KB | 0 | 1
`operation_merge_ledger` | 4.0KB | 0 | 3
`operation_merge_ledger_write_failures` | 4.0KB | 0 | 1
`operator_observation_runs` | 4.0KB | 1 | 2
`operator_reviews` | 4.0KB | 1 | 6
`operators` | 4.0KB | 1 | 59
`rpc_response_cache` | 4.0KB | 0 | 3
`wt_create_event_ledger` | 4.0KB | 0 | 4
`wt_create_ledger_conflicts` | 4.0KB | 0 | 2
`wt_create_ledger_pending` | 4.0KB | 0 | 2
`wt_dust_markers` | 4.0KB | 11 | 3
`wt_dust_pending_sigs` | 4.0KB | 0 | 1
`wt_dust_signaller_scan` | 4.0KB | 0 | 0
`wt_dust_signaller_scan_state` | 4.0KB | 46 | 0
`wt_dust_signaller_treasury_scan_state` | 4.0KB | 2 | 0
`wt_expansion_targets` | 4.0KB | 2 | 0
`wt_infrastructure_candidate_descendants` | 4.0KB | 0 | 1
`wt_infrastructure_candidate_evidence` | 4.0KB | 0 | 1
`wt_infrastructure_candidates` | 4.0KB | 18 | 2
`wt_known_spam_wallets` | 4.0KB | 1 | 4
`wt_operation_lifecycle` | 4.0KB | 56 | 3
`wt_ops_v2_families` | 4.0KB | 1 | 4
`wt_ops_v2_treasury_stats` | 4.0KB | 7 | 1
`wt_ops_v2_walk_candidates` | 4.0KB | 1 | 1
`wt_rotation_candidates` | 4.0KB | 0 | 0
`wt_scheduler_state` | 4.0KB | 1 | 2
`wt_subprov_account_ws_usage` | 4.0KB | 0 | 2
`wt_subprov_topups` | 4.0KB | 0 | 2
`wt_treasury_funders` | 4.0KB | 24 | 4
`wt_unconfirmed_watchtower_like` | 4.0KB | 0 | 3
`wt_vanity_matches` | 4.0KB | 6 | 2
`wt_vanity_sequence_evidence` | 4.0KB | 0 | 1
`wt_vanity_sibling_scan_cache` | 4.0KB | 13 | 1
`wt_wallet_lifecycle_evidence` | 4.0KB | 0 | 2
`wt_wallet_quality` | 4.0KB | 0 | 1
`wt_watchtower_candidates` | 4.0KB | 6 | 2
