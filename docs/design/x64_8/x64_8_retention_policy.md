# X64.8 — Phase 5: Retention Policy Recommendations

Recommendations only — nothing in this document is implemented. All
periods are starting proposals for discussion, not final policy.

| Category | Tables | Suggested retention | Reasoning |
|---|---|---|---|
| Dead/orphaned hot-DB copies | `funder_networks` (hot copy) | **Remove entirely — already superseded, archive copy is a verified superset** | Zero readers, zero writers, frozen since 2026-03-08; archive DB already holds a newer, larger copy (42,314 vs 41,734 rows) |
| Operational telemetry | `wss_metrics` | 30-90 days | Per-subscription WS event telemetry; reporting-only, not decision-path; high row-count growth (2.6M rows already) makes this the best early candidate for a rolling window |
| Operational retry/queue state | `wt_subprov_sig_retry`, `wt_candidate_websocket_watches`, `wt_active_subprov_sessions` | Purge on completion/expiry, not time-boxed | These are queues by design — the right retention rule is state-based ("completed"/"expired"/"terminal" status), not a calendar window; their current size suggests completed rows are not being purged today (see Phase 8) |
| Launch/token history | `token_analysis`, `token_prediction_scores`, `token_liquidity_snapshots` | Configurable, default: retain indefinitely for now, revisit at a defined size threshold (e.g. 5M rows or 5GB) | Core historical record of the system's detection output; the project's own "Launch audit / actionability" work already depends on being able to look back at historical launches, so no blanket time-box is safe without checking that dependency first |
| Creator/treasury/infra intelligence | `infra_wallets`, `creator_receivers`, `creator_risk_scores`, `creator_service_history`, `coordinated_creator_edges` | **Retain** — do not time-box | This is the accumulated attribution knowledge base (treasury/subprov/creator identity) that the entire WATCHTOWER detection model depends on; per this project's own persistent operating history, re-deriving this from scratch is exactly the kind of expensive, error-prone RPC-heavy work the system exists to avoid repeating |
| Funding-lineage graph | `transfer_index`, `funder_incoming_transfers`, `funder_outgoing_transfers` | Retain hot data for active investigation window (e.g. 6-12 months), archive older | High operational value while an investigation is live, declining value once a launch/operator has been fully attributed and confirmed; a good archive candidate on a rolling basis (see Phase 6), not a deletion candidate |
| Completed/reporting-only queues | `wt_attribution_outcomes`, `trade_simulations`, `wt_subprov_evidence` | Purge or archive after completion + a grace period (e.g. 90 days) | Reporting-only, not read on the live detection path; low risk to move off hot storage once a grace window for review has passed |
| Historical investigations (broad) | `flex_investigation_archive.db` contents (already-archived data) | Archive tier — long-term retain, no active purge | This is already the intended cold-storage tier; its purpose is explicitly long-term forensic retention |
| Prediction/decision snapshots | `prediction_decision_context`, `token_prediction_events` | 6-12 months hot, then archive | High per-row size and volume (276K/37K rows respectively) with declining marginal value once a token's outcome is long since determined |

## What NOT to retention-limit without further work

- `wt_walkback_queue`, `wt_ops_v2_edges`, `wt_operation_activity`,
  `wt_operation_candidates` — these are live operational state for the
  currently-running detection pipeline (including this session's own
  X64.5-X64.7A CREATE-ledger work). Any retention policy here needs to
  be strictly "completed/terminal state only," never calendar-based,
  or it risks purging in-flight reconciliation work.
- `metadata_cache`, `rpc_response_cache` — these are performance caches,
  not historical records; a retention/eviction policy is appropriate
  but should be designed for cache-hit-rate impact, not storage
  reduction alone (a separate, narrower piece of work from this audit's
  scope).

## General principle

Every retention recommendation above defaults to the **more
conservative** option where evidence was incomplete — per the explicit
constraint on this task, nothing here should be read as a green light to
implement without a follow-up design + review pass per table family.
