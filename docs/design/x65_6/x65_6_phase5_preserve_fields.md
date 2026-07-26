# X65.6 — Phase 5: Preserve Existing Discovery Fields

## Principle

Campaign is inserted as stage 3 of the numbered cascade
(Phase 2); it does not remove, rename, or recompute any of the five
existing stages or their underlying data sources.

## Fields every launch continues to expose, unchanged

| Field | Source (unmodified) | Status after this integration |
|---|---|---|
| Behaviour Cohort | `canonical_behaviour_for()` (X65.0) | Unchanged — stage 1, unaffected |
| Creator Identity | `enrich_creator_identity()` (X64.8/X64.9) | Unchanged — stage 2, unaffected |
| Topology | `classify_topology_for_launch()` (X29.1; known X65.4 gap tracked separately) | Unchanged — remains stage 4 (renumbered from 3 to make room for Campaign, label/position shift only, no logic change) |
| Funding Origin | Same source as Topology (X65.1 Phase 1) | Unchanged — stage 5 (renumbered from 4) |
| Treasury | `resolve_treasury_for_cohort()` (X65.1) | Unchanged in computation; presentation role changes to a table column + Campaign sub-grouping (Phase 4) — the underlying values and statuses are identical |
| Operation Attribution | `wt_ops_v2_wallets` join (X65.1) | Unchanged — stage 6 (renumbered from 5) |
| Confidence | Existing per-dimension confidence values (e.g. Treasury Resolution's `0.95`) | Unchanged; the new Campaign confidence tier (Phase 3) is displayed as an **additional**, separately-labeled field, never merged into or replacing an existing confidence value |

## Explicit non-goals (identical in spirit to X65.5 Phase 5, restated for this concrete integration)

- No existing `x60...Rows()` function's filtering logic changes —
  `x60CampaignRows()` is a new function inserted into the
  chain; `x60TopologyRows()`, `x60FundingRows()`, and
  `x60OperationRows()` continue to run exactly as they do today, just
  fed by one additional upstream filter stage.
- No existing API response field is removed from
  `/api/ops-v2/operational-intelligence`. A new `campaign`
  field (and its confidence sub-fields) would be **added** to each
  launch record, alongside every currently-returned field — consistent
  with X65.5 Phase 5's same non-goal, restated here because this task
  makes the integration concrete enough that an implementer could
  otherwise be tempted to restructure the response; this document
  states explicitly that they should not.
- The Treasury Resolution panel (X65.1, `renderTreasuryResolution()`)
  continues to render under its existing condition
  (`TOPO_SELECTION.funding === 'UNKNOWN'`) exactly as today — it is not
  replaced by the new Campaign Treasury sub-grouping (Phase
  4), which serves a different purpose (an at-a-glance breakdown
  within the campaign view) than the existing panel (a focused
  resolution-attempt table for launches specifically stuck at
  `funding=UNKNOWN`). Both can coexist without conflict, since they
  mount to different, independent locations in the page.
