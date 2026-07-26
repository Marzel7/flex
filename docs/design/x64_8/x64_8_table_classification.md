# X64.8 — Phase 2: Table Classification

Every table sized ≥ 1MB (the set accounting for ~95%+ of total disk
usage) is classified below into exactly one category. The long tail of
sub-1MB tables is classified as a group at the end (overwhelmingly
Operational — small state/config/cursor tables — with a minority
Obsolete, noted below).

## Obsolete

| Table | Evidence |
|---|---|
| `funder_networks` (hot DB copy, 2.86GB, 41,734 rows) | **Positive evidence on both required dimensions.** (1) No code writes to it anymore — `cross_funding_network_analyzer.py` lines 223/302/360-362 explicitly state "funder_networks writes target the archive DB, not the hot DB" and "funder_networks is no longer created here — it has been moved." (2) No code reads it from the hot DB — `main.py` attaches `flex_investigation_archive.db` as `arch` and reads exclusively `arch.funder_networks` (lines 13211-13226). (3) Row activity confirms staleness: `MAX(detected_at) = 2026-03-08`, over 4 months with zero new rows, while the archive DB's copy has grown to 42,314 rows (580 more) in the same period — proof all current writes go to the archive, none to the hot copy. This is the single largest Obsolete table found, and the only one classified with full confidence. |

No other table in the ≥1MB tier met both bars (zero writers AND zero
readers AND confirmed stale row activity). Per the project's own prior
lesson (a naive "looks unused" grep previously misclassified
`risk_score_history` and `wss_metrics` before deeper tracing reversed
the verdict), every other large table was checked for live code
references before being ruled out as a classification candidate, and
all of them have live writers or readers found directly in `src/`.

## Operational

Tables with active writers and readers, required for day-to-day
detection/attribution/dashboard function:

- `token_analysis`, `transfer_index`, `prediction_decision_context`,
  `watchtower_infra_events`, `token_prediction_scores`, `metadata_cache`,
  `creator_receivers`, `infra_wallets`, `coordinated_creator_edges`,
  `funder_outgoing_transfers`, `creator_risk_scores`,
  `token_prediction_events`, `creator_outgoing_transfers`,
  `funder_incoming_transfers` (`flex_complete_database.db`)
- `wt_subprov_sig_retry`, `wt_candidate_websocket_watches`,
  `watchtower_events`, `wt_cdc_outbound_events`,
  `wt_active_subprov_sessions`, `wt_subprov_evidence`,
  `wt_operation_activity`, `wt_operation_candidates`, `wt_swarm_buys`,
  `wt_fanout_events`, `wt_attribution_outcomes`, `wt_ops_v2_edges`,
  `wt_walkback_queue`, `wt_webhook_hits`, `wt_subprov_sig_cursor`
  (`wt_ops_v2.db`)
- The ~90 sub-1MB tables in `wt_ops_v2.db` (cursors, session state,
  per-subsystem config) — small by nature, actively read/written each
  detection cycle.

## Historical

Data with investigative/forensic value but not required for the system
to keep operating day-to-day:

- `creator_service_history` (21.4MB) — serial-deployer history; useful
  for classification lookups but not a live operational hot path (read
  occasionally, not on every event).
- `trade_simulations` (17.1MB) — backtest results; reference data for
  analysis, not consumed by live detection.
- `rpc_response_cache` (30.5MB) — arguably borderline Temporary/Historical;
  classified Historical here because unlike a true TTL cache it does not
  appear to have an active eviction path in the code sampled — see
  cleanup candidates for the distinction.

## Derived / Rebuildable

- `token_liquidity_snapshots` (108MB, 1,296,201 rows) — periodic
  snapshots computed from on-chain curve state. **Rebuild cost: high** —
  would require re-replaying curve history per token from RPC/on-chain
  data (this project's own "Launch audit" work already documents
  curve-replay as an expensive, RPC-bound operation), so despite being
  technically "derived," treat as expensive-to-rebuild and do not
  casually purge.
- `wss_metrics` (163MB, 2,637,910 rows) — per-subscription WS telemetry.
  **Rebuild cost: not rebuildable at all** — it is a live telemetry
  stream, not derived from any other stored source; once purged, that
  window of telemetry is permanently gone. Classified here only in the
  sense that no single row has irreplaceable *analytical* value beyond
  aggregate metrics — but note this is closer to Temporary in practical
  retention terms (see Phase 5).
- `metadata_cache` (122MB, 1,303,815 rows) — cached token/creator
  metadata. **Rebuild cost: low-to-moderate** — re-fetchable from
  RPC/external metadata APIs on demand, at the cost of API calls and
  request latency, not unbounded time.

## Temporary

- `wt_subprov_sig_retry` (392.9MB) — a retry queue by design; rows
  should complete and clear. Its size (largest table in the ops DB)
  suggests either very high queue volume or completed rows not being
  pruned — flagged in Phase 8 (Cleanup Candidates) as worth checking for
  a stale/completed-row backlog rather than assumed healthy.
- `wt_candidate_websocket_watches` (319MB) — active + historical WS
  candidate-watch state; same shape as above, likely accumulating
  completed/expired watches without being pruned.
- `rpc_response_cache` (30.5MB) — see note above; behaves like a cache
  but retention/eviction behavior wasn't confirmed in this pass.

## Summary counts (≥1MB tier)

| Category | Table count | Approx. bytes |
|---|---|---|
| Obsolete | 1 | 2.86GB |
| Operational | ~39 | ~2.6GB |
| Historical | 3 | ~69MB |
| Derived/Rebuildable | 3 | ~394MB |
| Temporary | 3 (overlapping with Operational above — queues are operational *and* temporary in nature) | ~742MB |

The single Obsolete table (`funder_networks`) is larger than the entire
rest of the Historical + Derived/Rebuildable + Temporary categories
combined — it dominates every lifecycle decision in this audit.
