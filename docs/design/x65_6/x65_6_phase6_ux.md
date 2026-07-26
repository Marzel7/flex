# X65.6 — Phase 6: Discovery UX

## Viewing "by" each dimension

The task asks that analysts can "view by" Creator Identity, Topology,
Treasury, or Campaign. In the existing cascade model
(Phase 1), "viewing by" a dimension means selecting one of its cards,
which narrows the launch table and reveals the next stage's own cards
computed over that narrowed population — this is already exactly how
Creator Identity and Topology behave today, and Campaign
(Phase 2) is designed to behave identically:

- **View by Creator Identity**: click a Creator Identity card (stage 2,
  unchanged) — narrows to that identity, reveals Campaign
  cards (stage 3) computed over that population.
- **View by Campaign**: click an Campaign card
  (stage 3, new) — narrows to that pattern, reveals Topology cards
  (stage 4) computed over that population.
- **View by Topology**: click a Topology card (stage 4, unchanged,
  renumbered) — narrows further, reveals Funding Origin (stage 5).
- **View by Treasury**: Treasury is not its own cascade stage (Phase
  4) — "viewing by Treasury" means either (a) clicking a Treasury-tier
  segment inside an already-selected Campaign card (a
  same-stage refinement), or (b) sorting/scanning the Treasury column
  directly in the launch table, which requires no selection at all.
  This is a deliberate design choice, not an oversight: Phase 4 already
  established Treasury must never gate membership in any dimension, so
  giving it its own cascade stage would risk exactly the fragmentation
  this task exists to remove — the table-column + sub-grouping
  presentation lets an analyst still "view by Treasury" without a
  parallel drill-down.

## Switching dimensions never loses evidence

Because every stage's underlying rows come from the same single
`X58_FILTERED_ROWS` array (already the case today — Phase 1's review
confirms `renderX58Mounts()` computes this once via
`x60CurrentRows()` and reuses it across every mount), and because
adding `campaign` to `TOPO_SELECTION` only ever narrows
(never replaces) that array, no evidence is ever hidden by navigating
between dimensions — a launch's Creator Identity, Topology, Treasury,
Funding Origin, and Operation Attribution values remain visible in its
table row regardless of which dimension is currently selected as the
active filter. This is the same non-destructive-cascade property the
existing five stages already guarantee; Campaign inherits
it by construction rather than requiring new code to enforce it.

## Campaign behaves exactly like existing Discovery dimensions

Concretely, per Phase 2's mechanics:
- Same card component (`x58Card`)
- Same click-binding (`data-x56-dimension`, no new JS event listener
  needed)
- Same breadcrumb integration (`topoBreadcrumb()`, one new row)
- Same URL-sync behavior (`x56SyncUrl()` already serializes whatever
  keys exist on `TOPO_SELECTION`, needing no new logic — this was
  confirmed as a generic function during Phase 1's review, not
  specific to any one dimension's name)
- Same reset-via-breadcrumb-crumb behavior (`.dw-topo-crumb`, one new
  `resetLevel` case: `'campaign'`, clearing `topology`,
  `funding`, `operation` — consistent with the existing per-level
  crumb resets)

No new interaction vocabulary is introduced anywhere in this design —
an analyst who already knows how to use Discovery's existing five
dimensions needs to learn nothing new to use Campaign.
