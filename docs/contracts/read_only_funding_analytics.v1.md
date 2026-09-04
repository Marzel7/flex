# Read-only funding analytics contract v1

`src.ops.funding_analytics.FundingAnalytics` is a derived, read-only,
non-authoritative analytical surface. It creates no tables, keeps no cache, and
does not participate in ingestion, Walkback selection, attribution, topology,
P3R, Operations, or Potential Operation decisions.

## Canonical fact precedence

| Analytical fact | Source and semantics |
| --- | --- |
| Direct creator funding | `creator_funders`: retained creator-to-funder fact, amount, acquisition timestamp, source type and stored CEX classification. |
| Launch count/time | `token_analysis`: mint/creator and `created_at`; this is launch time, not funding time. |
| Selected funding trace | `wt_walkback_queue`, then selected `wt_walkback_edge_candidates`: funder, signature, slot/block time, amount and mechanism. |
| Transaction role/atomic interpretation | `wt_walkback_transaction_roles` and `wt_walkback_atomic_flows`; used only when a query needs instruction-level semantics. |
| Treasury/subprov/creator chain | `wt_provisioning_sessions` and `wt_provisioning_edges`; each leg retains its own time, amount and mechanism. |
| CEX/exchange identity | `creator_funders.is_cex`/`cex_exchange`, cross-checked with active `cex_wallets`; no heuristic classification. |
| Established Operation relation | `operator_launch_membership` joined by mint to retained Walkback funding evidence. |
| Potential Operation relation | `potential_operation_evidence_association` joined by retained evidence key. |

## Time semantics

- Funding-window helpers use normalized `creator_funders.first_detected_at`.
- Launch-window helpers use `token_analysis.created_at`.
- Technical evidence uses its explicit slot/block-time field.
- Provisioning timing uses the corresponding leg field; it is not substituted
  for funding or launch time.

Historical `first_detected_at` values have mixed SQLite representations. The
module normalizes numeric epochs and ISO timestamps before applying a window.

## Deliberate non-claims

- A returned Walkback edge is evidence-backed, not a generic account-history
  reconstruction.
- `funding_chain(mint)` returns only currently retained, ordered selected-edge
  and provisioning-session facts. It does not silently recreate a legacy
  `funding_chains` row where a historical leg is missing.
- Legacy network IDs and labels are not analytical authority.
