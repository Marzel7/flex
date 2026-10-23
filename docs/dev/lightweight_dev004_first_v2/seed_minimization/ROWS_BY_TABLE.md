# DEV004 — ROWS_BY_TABLE

Every one of the28,454 rows is assigned once. EO=essential operational; EI=essential operation intelligence; ED=essential discovery; EP=essential price; C=conditional compactable; H=synthetic historical/test-only; U=unknown.

Logical bytes below use the same canonical full-row JSON encoding as the prior inventory. This includes field-name/null/escaping overhead, not SQLite payload or physical recovery. Per-table allocation is unavailable (`dbstat` absent). Core2190pages/OPS3355pages,4096bytes/page; freelists0.

| Database | Table | Rows | Encoded logical bytes | Rows by class | Table pages |
|---|---|---:|---:|---|---|
| core | birth_persist_queue | 0 | 0 |  | unavailable |
| core | creator_funders | 3543 | 1394236 | U:3543 | unavailable |
| core | creator_funding_queue | 1467 | 989467 | EO:195, C:1038, U:234 | unavailable |
| core | creator_resolution_queue | 28 | 16818 | H:1, U:27 | unavailable |
| core | metadata_cache | 1143 | 170337 | U:1143 | unavailable |
| core | migration_walkback_handoff | 343 | 154440 | C:343 | unavailable |
| core | token_analysis | 1154 | 2909235 | H:1, U:1153 | unavailable |
| core | token_market_cap_peaks | 216 | 54427 | EP:216 | unavailable |
| core | token_pool_accounts | 1138 | 1318936 | U:1138 | unavailable |
| core | tracked_tokens | 1129 | 401360 | EP:1050, U:79 | unavailable |
| core | transfer_index | 2326 | 890623 | U:2326 | unavailable |
| core | wb_create_control | 1 | 185 | H:1 | unavailable |
| core | wb_create_delivery | 1 | 533 | H:1 | unavailable |
| core | webhook_birth_queue | 1 | 561 | H:1 | unavailable |
| ops | confirmed_operation_matches | 318 | 353528 | U:318 | unavailable |
| ops | dev004_retained_reference_evidence | 15 | 16998 | C:15 | unavailable |
| ops | monitor_admission_outbox | 104 | 45778 | U:104 | unavailable |
| ops | operation_behavioural_profiles | 6 | 55762 | EI:6 | unavailable |
| ops | operation_monitor_entry_provenance | 71 | 89035 | EP:71 | unavailable |
| ops | operation_monitor_facts | 24 | 40583 | EP:18, H:6 | unavailable |
| ops | operation_monitor_mode_transitions | 2 | 839 | EP:2 | unavailable |
| ops | operation_monitor_observations | 47 | 22496 | EP:11, C:22, H:14 | unavailable |
| ops | operation_qualification_contracts | 6 | 6207 | EI:6 | unavailable |
| ops | operation_registry_dispositions | 10 | 2779 | EI:10 | unavailable |
| ops | operator_entities | 153 | 38200 | EI:153 | unavailable |
| ops | operator_evidence | 5 | 6156 | EI:5 | unavailable |
| ops | operator_identity_assets | 122 | 40509 | EI:122 | unavailable |
| ops | operator_identity_events | 136 | 64055 | EI:136 | unavailable |
| ops | operator_identity_merges | 1 | 199 | EI:1 | unavailable |
| ops | operator_identity_state | 5 | 2061 | EI:5 | unavailable |
| ops | operator_launch_assignment_history | 27 | 11180 | C:27 | unavailable |
| ops | operator_launch_membership | 1165 | 266779 | EI:1165 | unavailable |
| ops | operator_promotion_reviews | 1 | 8297 | EI:1 | unavailable |
| ops | operator_reviews | 2 | 610 | EI:2 | unavailable |
| ops | operators | 10 | 3601 | EI:10 | unavailable |
| ops | p3r_v2_candidate_membership | 1332 | 826820 | ED:98, U:1234 | unavailable |
| ops | potential_operation_assessment_association_binding | 183 | 31293 | ED:183 | unavailable |
| ops | potential_operation_assessment_lineage | 5 | 1741 | ED:2, C:3 | unavailable |
| ops | potential_operation_assessment_version | 5 | 9813 | ED:2, C:3 | unavailable |
| ops | potential_operation_child_candidates | 2 | 2890 | ED:2 | unavailable |
| ops | potential_operation_current | 2 | 421 | ED:2 | unavailable |
| ops | potential_operation_evidence_association | 183 | 63405 | ED:183 | unavailable |
| ops | potential_operation_identity | 2 | 712 | ED:2 | unavailable |
| ops | potential_operation_workflows | 63 | 143035 | ED:63 | unavailable |
| ops | watchtower_token_attribution | 975 | 437932 | U:975 | unavailable |
| ops | wt_attribution_outcomes | 1164 | 3385264 | EI:1, C:1163 | unavailable |
| ops | wt_candidate_websocket_watches | 19 | 19097 | U:19 | unavailable |
| ops | wt_confirmed_treasuries | 115 | 29708 | EI:115 | unavailable |
| ops | wt_creator_birth_launch | 0 | 0 |  | unavailable |
| ops | wt_discovered_subprovs | 0 | 0 |  | unavailable |
| ops | wt_infrastructure_candidate_descendants | 0 | 0 |  | unavailable |
| ops | wt_infrastructure_candidate_evidence | 0 | 0 |  | unavailable |
| ops | wt_infrastructure_candidate_reviews | 0 | 0 |  | unavailable |
| ops | wt_infrastructure_candidates | 0 | 0 |  | unavailable |
| ops | wt_launch_audit | 600 | 1513158 | C:600 | unavailable |
| ops | wt_lineage_quarantine | 129 | 92306 | U:129 | unavailable |
| ops | wt_lineage_root_policies | 1 | 287 | U:1 | unavailable |
| ops | wt_lineage_verified_session_edges | 129 | 45676 | U:129 | unavailable |
| ops | wt_ops_v2 | 0 | 0 |  | unavailable |
| ops | wt_ops_v2_wallets | 0 | 0 |  | unavailable |
| ops | wt_provisioning_sessions | 1001 | 714900 | EI:1, U:1000 | unavailable |
| ops | wt_treasury_review | 50 | 61130 | U:50 | unavailable |
| ops | wt_walkback_atomic_flows | 784 | 781888 | ED:71, U:713 | unavailable |
| ops | wt_walkback_edge_candidates | 4172 | 4044311 | EI:88, ED:834, H:3, U:3247 | unavailable |
| ops | wt_walkback_queue | 1195 | 1741416 | EO:1, ED:98, C:1092, H:1, U:3 | unavailable |
| ops | wt_walkback_transaction_roles | 73 | 91947 | EI:73 | unavailable |
| ops | wt_wallet_lifecycle_evidence | 0 | 0 |  | unavailable |
| ops | wt_watchtower_launches | 605 | 571100 | U:605 | unavailable |
| ops | wt_wrap_close_candidates | 945 | 599069 | U:945 | unavailable |

Total: **28,454 rows;24,586,129 encoded logical bytes**. Field-value-only representation estimate12,324,307bytes excludes field names and nulls; it is not SQLite payload. Full per-table source population, inclusion reason, installed reader/writer SQL references (absolute path/line), evidence relationship, future-decision status and conditional byte targets are in inventory.json. No activated-path or exhaustive-call-graph claim is made from static SQL references.
