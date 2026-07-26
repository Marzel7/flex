# X65.6 — Phase 1: Review Current Discovery UI

Read-only review of `templates/discovery.html`'s actual implementation
(not a re-description of X65.5's abstract dimension list — this phase
documents the concrete DOM/state mechanics X65.6 must integrate with).

## Navigation

Discovery is a single page (`/discovery?window=<range>`), no
sub-navigation or tabs today. Window selection (`7d`, etc.) is the only
top-level control outside the drill-down itself.

## State model

A single client-side JS object, `TOPO_SELECTION`, holds the current
selection per dimension:
```js
TOPO_SELECTION = {
  behaviour: null, creator_identity: null, topology: null,
  funding: null, operation: null, mechanism: null
}
```
Selecting a value at one dimension clears every dimension *after* it
in the fixed cascade order (`bindTopoLevelClicks()`,
`templates/discovery.html:1652-1697`) — e.g. setting `topology` clears
`funding` and `operation`, but leaves `behaviour`/`creator_identity`
alone. `x56SyncUrl()` mirrors `TOPO_SELECTION` into the URL query
string after every change (confirmed by call sites immediately
following every `TOPO_SELECTION.<x>=` assignment).

## Cards / drill-down sections (`renderX58Mounts()`, line 1622-1642)

Rendered in fixed numbered order, each into its own mount `<div>`:
1. **Behaviour Cohort** (`renderBehaviourCohorts()`) — mutually
   exclusive cards, clicking sets `TOPO_SELECTION.behaviour`
2. **Creator Identity** (`renderCreatorIdentity()`) — cards scoped to
   the current behaviour selection
3. **Topology** (`renderTopologyDistribution()`) — cards scoped to
   creator identity
4. **Funding Origin** (`renderFundingOrigin()`) — scoped to topology
5. **Operation Attribution** (`renderOperationAttribution()`) — scoped
   to funding
6. **Treasury Resolution** (`renderTreasuryResolution()`, X65.1,
   conditional mount `dw-x65-1-treasury-resolution-mount`) — fires
   only when `TOPO_SELECTION.funding === 'UNKNOWN'`, i.e. it is not a
   numbered top-level stage but a **conditional supplementary panel**
   that appears inside the existing flow when relevant

Every card is rendered via a shared helper (`x58Card(dimension, key,
label, count, note)`) producing a `data-x56-dimension`/`data-x56-value`
button — the single, generic click-binding in `bindTopoLevelClicks()`
(`document.querySelectorAll('[data-x56-dimension]')`) handles all of
them uniformly. This is the exact mechanism X65.6 Phase 2 will reuse.

## Filters / grouping logic

Filtering is purely additive/cumulative via `x60...Rows()` functions
(`x60BehaviourRows()` → `x60CreatorIdentityRows()` →
`x60TopologyRows()` → `x60FundingRows()` → `x60OperationRows()`),
each filtering the previous stage's already-narrowed array. There is no
independent "grouping" concept distinct from this cascade — every
dimension is both a filter and a grouping lens simultaneously (the
cards show counts of the *current* population grouped by that
dimension's own values).

## Sorting / search

No sort or search UI exists on this page today (confirmed: no
`<input type="search">` or sort-control markup found in the reviewed
sections). The launch results table (`renderLaunchResultsHeader()`
mount `dw-x58-results-head-mount`) is populated in the order the
backing API returns rows.

## Breadcrumb / reset

`topoBreadcrumb()` (line ~1393) renders the "Current Selection" panel
showing every dimension's current value (or "All X") — clicking a
`.dw-topo-crumb` resets that dimension and everything cascading from
it (line 1683-1692), mirroring the forward-cascade's own clear-on-select
behavior in reverse.

## Where an Campaign lens fits without disrupting existing workflow

Two structurally sound options exist, both reusing existing mechanics
with zero new interaction patterns:

**Option chosen for this design (see Phase 6/7): a new stage inserted
into the SAME numbered cascade, positioned after Creator Identity and
before Topology** — reusing the identical `x58Card`/`data-x56-dimension`
mechanism already used for all five existing stages, and the identical
`TOPO_SELECTION`/`x60...Rows()` cascade-clearing convention. This is
deliberately **not** a new page, not a new interaction model, and not
a conditional-only panel (unlike Treasury Resolution, which only
appears under one specific prior condition) — Campaign is
proposed to always be visible and selectable, exactly like Creator
Identity or Topology are today, because Phase 4's constraint (never
gated on treasury) means it should never be conditionally hidden the
way the Treasury Resolution panel currently is.

The exact insertion point (after Creator Identity, before Topology) is
justified in Phase 2.
