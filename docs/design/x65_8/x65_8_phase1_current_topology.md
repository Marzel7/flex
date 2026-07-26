# X65.8 — Phase 1: Review Current Topology Classifier

Read-only review of `src/ops/funding_topology.py` (unchanged since
X65.4's own audit — re-confirmed current as of this task).

## Where classification is performed

`classify_topology_for_launch()` (per-launch, pure function) called
from `build_topology_classification()` (batch entry point, run once
per Discovery load). This is the sole topology classifier — nothing
else in the codebase computes a `topology` value.

## Full decision tree (unchanged from X65.4, re-verified)

```
For a given launch (mint):

1. Is there a selected walkback edge chain with depth ≥ 2 AND a parent
   wallet that fans out to >1 child (among SELECTED walkback edges only)?
   → MULTI_LEVEL_FAN_OUT

2. Else, is there no subprov AND no treasury evidence at all?
   → UNKNOWN

3. Else, is the involved subprov itself a recorded child of another
   subprov session (SUBPROV_SESSION_OPENED_WS, via=subprov_plain_xfer)?
   → MULTI_LEVEL_FAN_OUT

4. Else, is the involved treasury also recorded as a subprov elsewhere
   (wt_active_subprov_sessions treasury∩subprov overlap)?
   → MESH

5. Else, if a subprov is known:
   a. Does wt_provisioning_edges record >1 DISTINCT CREATOR for this
      subprov (SUBPROV_TO_CREATOR edges, across ALL mints)?
      → FAN_OUT (n_siblings > 1)
   b. Does it record exactly 1 creator?
      → LINEAR (n_siblings == 1)
   c. No SUBPROV_TO_CREATOR edge recorded for this subprov at all —
      fall back to selected-walkback parent fan-out at depth ≥ 1:
      does the immediate walkback parent fan out to >1 child?
      → FAN_OUT (walkback fallback)
      else → LINEAR (walkback fallback, no observed branch)
      else (no walkback evidence either) → UNKNOWN

6. Else, if only a treasury is known (no subprov at all):
   → LINEAR ("treasury_direct_no_subprov")

7. Else → UNKNOWN
```

## Evidence sources consulted, per classification value

| Value | Table(s) read | What is actually counted |
|---|---|---|
| `MULTI_LEVEL_FAN_OUT` (walkback variant) | `wt_walkback_edge_candidates`, `wt_walkback_queue.termination_reason_json` | Selected walkback hop chains for THIS mint's own resolution, not general fan-out |
| `MULTI_LEVEL_FAN_OUT` (session-lineage variant) | `watchtower_events` (`SUBPROV_SESSION_OPENED_WS`) | Whether the subprov is itself a recorded child of another subprov |
| `MESH` | `wt_active_subprov_sessions` (treasury∩subprov overlap) | Structural mesh signal, currently matches 0 launches live |
| `FAN_OUT`/`LINEAR` (primary) | `wt_provisioning_edges` (`SUBPROV_TO_CREATOR` only) | **Distinct creators**, not distinct recipients — see Phase 2/4 |
| `FAN_OUT`/`LINEAR` (walkback fallback) | `wt_walkback_edge_candidates`/`wt_walkback_queue` | Same creator-ancestry counting, one hop shallower |
| `UNKNOWN` | (absence of the above) | No lineage evidence at all |

## Traversal depth, thresholds, confidence model

| Parameter | Current value |
|---|---|
| Graph traversal depth | 1 hop (subprov→creator) for primary Fan-Out/Linear; up to 2 hops for Multi-Level walkback variant |
| Branching criteria | `COUNT(DISTINCT to_wallet)` on `SUBPROV_TO_CREATOR` edges — creators only |
| Fan-out threshold | `> 1` distinct creator |
| Temporal window | None — no windowing concept anywhere in this classifier |
| Amount similarity | None — `funding_amount_sol` stored but never compared |
| Confidence model | **None at all** — `classify_topology_for_launch()` returns only `{topology, label, derived_from}`; there is no confidence score, tier, or gradation anywhere in the existing Topology classifier. This is a material contrast with Campaign (X65.7), which has a three-tier confidence model (High/Medium/Baseline) built in from the start |
| RPC/DB sources | Exclusively local SQLite reads against `wt_ops_v2.db`; zero RPC |

## Exact assignment locations (line references, `src/ops/funding_topology.py`)

- `LINEAR`: lines 276-280 (`n_siblings == 1`), line 296-298 (walkback fallback, no branch), line 303-304 (`treasury_direct_no_subprov`)
- `FAN_OUT`/what the task's background calls "WATCHTOWER Provisioning Fan-Out" (terminology renamed in X65.5/X65.6, underlying value unchanged): lines 270-275 (`n_siblings > 1`), lines 290-294 (walkback fallback, branch observed)
- `UNKNOWN`: line 246 (no lineage evidence at all), line 299 (subprov present, no sibling evidence, no walkback), line 306 (final fallback)

This is the exact, unmodified state Phase 2 onward builds on.
