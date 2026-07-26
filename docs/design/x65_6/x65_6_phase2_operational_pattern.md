# X65.6 — Phase 2: Add Campaign To Discovery

## New dimension

**`campaign`** — added to `TOPO_SELECTION` alongside the
existing keys:
```js
TOPO_SELECTION = {
  behaviour: null, creator_identity: null, campaign: null,
  topology: null, funding: null, operation: null, mechanism: null
}
```

## Values

- `WATCHTOWER` — the launch satisfies the mandatory
  membership criteria (Phase 3)
- `OTHER_CAMPAIGN` — the launch has a resolved creator/funding lineage
  but does not match the WATCHTOWER provisioning fingerprint (e.g. no
  wrap-close evidence at all, even though other lineage exists)
- `UNCLASSIFIED` — insufficient evidence to determine either way

These three values are mutually exclusive (every launch gets exactly
one), following the same discipline X65.0 established for Behaviour
Cohort — Campaign is a new, independently-computed,
exhaustive classification, not an additive tag.

## Placement in the existing numbered cascade

**Inserted as a new stage after Creator Identity, before Topology**:

```
1. Behaviour Cohort
2. Creator Identity
3. Campaign   ← new
4. Topology
5. Funding Origin
6. Operation Attribution
```

### Why this position, not earlier or later

- **Not before Creator Identity**: membership's first mandatory
  criterion (Phase 3) is `creator_identity == FRESH_CREATOR` — placing
  Campaign before Creator Identity would mean evaluating it
  against a criterion the analyst hasn't yet seen or selected,
  breaking the existing "each stage narrows what the next stage
  analyses" contract (X65.0's own design principle, reused here).
- **Not after Topology**: Topology (X65.4) is known to be an
  incomplete description of the same underlying fan-out Operational
  Pattern checks more directly — placing Campaign after
  Topology would visually suggest Campaign is a refinement
  *of* Topology's own (known-incomplete) evidence, when in fact it is
  computed independently and is not gated by Topology's result at all
  (Phase 3/4). Placing it immediately after Creator Identity keeps it
  visibly independent of Topology.
- **Not after Funding/Operation**: Phase 4's core requirement (never
  gate on treasury/operation resolution) would be undermined by
  visually implying Campaign is a downstream refinement of
  Treasury/Operation status — placing it early, right after Creator
  Identity, reinforces that Campaign is computed from
  *provisioning-mechanism* evidence, structurally prior to and
  independent of treasury/operation resolution.

## Reuses existing mechanics exactly (no new interaction pattern)

- Rendered via a new mount (`dw-x65-6-operational-pattern-mount`)
  inside `renderX58Mounts()`, following the identical pattern as the
  five existing stage mounts.
- Cards use the existing `x58Card('campaign', key, label,
  count, note)` helper — the exact function already used by every
  other stage — so the generic `data-x56-dimension` click-binding in
  `bindTopoLevelClicks()` requires **zero new click-handling code**;
  the existing `document.querySelectorAll('[data-x56-dimension]')`
  binding already covers any element carrying this attribute,
  regardless of dimension name.
- A new `x60CampaignRows()` function is added to the
  existing `x60...Rows()` cascade chain
  (`x60CreatorIdentityRows()` → **`x60CampaignRows()`** →
  `x60TopologyRows()` → ...), following the identical filter-the-prior-
  stage's-array convention every other stage already uses.
- Selecting an Campaign card clears `topology`, `funding`,
  and `operation` (everything downstream in the new cascade order) —
  exactly mirroring the existing clear-on-select rule
  (`bindTopoLevelClicks()`'s per-dimension clear list), extended by one
  entry.
- The breadcrumb (`topoBreadcrumb()`) gains one additional row
  (`['Campaign', ...]`), following the exact same array
  format as the five existing rows.

## This becomes a Discovery filter/grouping mechanism, not a separate page

Consistent with the task's objective: no new route, no new page, no
new top-level navigation item — Campaign behaves exactly
like Creator Identity or Topology do today, as one more stage in the
same cascade, on the same `http://localhost:5002/discovery` page.
