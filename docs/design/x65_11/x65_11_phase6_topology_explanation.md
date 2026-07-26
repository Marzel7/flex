# X65.11 — Phase 6: Explain Topology Classification

For every launch, the exact evidence that drove `funding_topology.py`'s
`classify_topology_for_launch()` decision — read directly from the
live API's `topology_derived_from` field, which names the precise rule
and evidence value that fired (X65.10's implementation).

## Per-launch explanation

| Mint | Topology | Evidence explanation |
|---|---|---|
| 5KNDHuNZZc… | MULTI_LEVEL_FAN_OUT | `derived_from=wt_active_subprov_sessions_sub_subprov_lineage` — this SubProv is itself recorded as a child of another, further-upstream SubProv session (a genuine multi-tier chain, not a sibling-count measure) |
| 2HBTVUsaor… | LINEAR | `derived_from=wt_provisioning_edges_sibling_count=1` — the SubProv (`8DWH19uhVTaz…`) has exactly 1 recorded creator edge in `wt_provisioning_edges` (consistent with Phase 3's direct measurement of this same SubProv) |
| ExL7K9dVVa… | MULTI_LEVEL_FAN_OUT | `derived_from=selected_walkback_depth=4;upstream_fanout=4` — the walkback process's own selected hop-chain for this specific mint reached depth 4 with an upstream parent that itself fans out to 4 children |
| GtpUa2zbVc… | LINEAR | `derived_from=selected_walkback_depth=1;no_observed_branch` — walkback resolved only 1 hop with no branching observed at that hop |
| 4cVTL5RNa9… | MULTI_LEVEL_FAN_OUT | `derived_from=selected_walkback_depth=3;upstream_fanout=6` |
| 3aNojTm74D… | MULTI_LEVEL_FAN_OUT | `derived_from=selected_walkback_depth=5;upstream_fanout=32` — the deepest walkback chain in this cohort |
| 5KtNnnPt7x… | MULTI_LEVEL_FAN_OUT | `derived_from=selected_walkback_depth=2;upstream_fanout=2` |
| 9wvwgFa2Ni… | MULTI_LEVEL_FAN_OUT | `derived_from=selected_walkback_depth=3;upstream_fanout=32` |
| 7ri93jDVvo… | MULTI_LEVEL_FAN_OUT | `derived_from=selected_walkback_depth=3;upstream_fanout=6` |
| 4FWfPWMRX5… | MULTI_LEVEL_FAN_OUT | `derived_from=selected_walkback_depth=6;upstream_fanout=32` |
| 2bFc6R3Wr8… | LINEAR | `derived_from=wt_provisioning_edges_sibling_count=1` — SubProv (`2EpHmj6CLGQJ…`) has exactly 1 recorded creator edge |
| EnEgmM4Eb6… | MULTI_LEVEL_FAN_OUT | `derived_from=selected_walkback_depth=2;upstream_fanout=2` |
| 5ejRBHFabF… | MULTI_LEVEL_FAN_OUT | `derived_from=selected_walkback_depth=4;upstream_fanout=6` |
| 3zUqCv6rsq… | LINEAR | `derived_from=selected_walkback_depth=1;no_observed_branch` |
| 5TW8ARthng… | MULTI_LEVEL_FAN_OUT | `derived_from=wt_active_subprov_sessions_sub_subprov_lineage` — same sub-subprov-lineage rule as 5KNDHuNZZc (same SubProv, `Dv34prGm2BT7…`) |
| 7LxAGkCSxf… | MULTI_LEVEL_FAN_OUT | `derived_from=selected_walkback_depth=4;upstream_fanout=32` |
| Ar3vVpZt2x… | LINEAR | `derived_from=selected_walkback_depth=1;no_observed_branch` |
| 7CFsJrkPSb… | MULTI_LEVEL_FAN_OUT | `derived_from=selected_walkback_depth=3;upstream_fanout=32` |
| CnEgM3tCug… | MULTI_LEVEL_FAN_OUT | `derived_from=wt_active_subprov_sessions_sub_subprov_lineage` |

## Pattern: Topology classification tracks funding mechanism exactly

A direct, verifiable correlation is present in this cohort:

- **Every one of the 13 WSOL_WRAP_CLOSE launches → `MULTI_LEVEL_FAN_OUT`** (13/13).
- **Every one of the 6 PLAIN_TRANSFER launches → `LINEAR`** (6/6).

This is not a coincidence of this specific evidence set — it follows
directly from how the walkback process's own selected-hop-chain
evidence (`wt_walkback_edge_candidates`/`wt_walkback_queue`, the
dominant evidence source for 16 of 19 launches in this cohort, per the
`derived_from` values above) tends to resolve deeper, more-branching
chains specifically for wrap-close-mediated funding (which this
project's own prior investigations, e.g. X65.4, established as
routinely involving multi-hop treasury→subprov→sub-subprov chains),
while plain-transfer funding in this cohort resolved to shallower,
single-hop chains with no observed branch. **This is an observation
about this specific 24-hour cohort's evidence, not a general claim
about the two mechanisms always producing these topologies** — it is
reported because it is directly measurable in this data, not because
the underlying causal mechanism was investigated further (out of
scope for this read-only audit).

## Note: the candidate-watch-based rule (X65.10) never fired in this cohort

Per Phase 2/3, none of this cohort's 12 sub-providers have any
`wt_candidate_websocket_watches` coverage — so X65.10's newly-added
evidence-priority rule (`wt_candidate_websocket_watches_count=<n>`,
consulted first) never had data to act on for any of these 19
launches. Every classification in this cohort instead came from the
**fallback** paths: either the pre-existing `wt_provisioning_edges`
sibling-count rule (3 launches: `2HBTVUsaor…`, `2bFc6R3Wr8…`, and — via
the sub-subprov-lineage rule rather than sibling-count —
`5KNDHuNZZc…`/`5TW8ARthng…`/`CnEgM3tCug…`) or the walkback-based
fallback (the remaining majority). This is consistent with, not
contradictory to, X65.8/X65.10's own finding that the new evidence
source's coverage is concentrated in the live-cascade-confirmed
population, which this walkback-resolved 24-hour cohort is not part of.
