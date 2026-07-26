# X65.22 — Simplify WATCHTOWER Discovery: Treat Topology as Canonical, Not a Classifier

Implementation summary. UI/presentation refactor only. **One file changed**:
`templates/discovery.html`. No backend, classifier, attribution, detection, or
database schema file was touched — confirmed via `git status` showing only this
one file modified for this task's scope.

## Phase 1 — Audit of current topology usage

| Location | Purpose | Still appropriate after X65.21? |
|---|---|---|
| `renderTopologyDistribution()` (bucket cards: Multi-Level Fan-Out/Fan-Out/Linear/Unknown) | Displays topology distribution for the current campaign selection | **No, for `campaign=WATCHTOWER` specifically** — X65.19/X65.21 established one canonical shape; bucketing implies multiple genuine WATCHTOWER topologies where none exist. **Yes, unchanged, for `OTHER_CAMPAIGN`/`UNCLASSIFIED`** — these populations' operational topology is still genuinely being classified. |
| `x60TopologyRows()` | Filters the cascade by `TOPO_SELECTION.topology` | **Unchanged, kept for all campaigns.** This is the underlying data mechanism, not a display choice — Funding Origin and Operation stages depend on it downstream regardless of which campaign is selected. |
| `renderFundingOrigin()` (stage 5) | Reads `x60TopologyRows()`'s output to show funding-origin evidence | **Unchanged.** Depends on the topology filter mechanism (preserved), not the bucket-card display (only display changed for WATCHTOWER). |
| `topoBreadcrumb()` / stage-nav / `x58HasFilters()` | Drill-down breadcrumb and active-filter tracking | **Unchanged.** These read `TOPO_SELECTION.topology` directly, which still exists and still works identically for every campaign — only the *card that lets a user click into a specific bucket* changed for WATCHTOWER. |
| `renderProvisioningWalletExplanation()` (X65.20) | Explains the Operational Topology vs Attribution Graph distinction | **Relocated, not removed** — moved from the generic topology-card function (reached only by non-WATCHTOWER campaigns after this change) into the new WATCHTOWER-specific canonical card, since its content is WATCHTOWER-specific (X65.19's own proof). |

## Phase 2/4/6 — Canonical WATCHTOWER topology card

New function `renderCanonicalWatchtowerTopology()`, called instead of the bucketed
view exactly when `TOPO_SELECTION.campaign === 'WATCHTOWER'`:

```
WATCHTOWER Topology
✓ Canonical Operational Topology
Treasury → SubProvider → Provisioning Wallet → Creator → Launch
```

Every confirmed WATCHTOWER launch shows the identical card — there is no per-launch
variation to click into, by design, since X65.19 (42/42 resolvable ground-truth
launches) and X65.21 (now persisted directly) established this is one shape, not
several. The card's copy explicitly states topology "describes how the WATCHTOWER
operation works, not a way to distinguish individual launches within it," directing
readers to Behaviour for per-launch differences — the exact distinction Phase 4
requires.

## Phase 3 — Behaviour remains additive (verified, not changed)

Audited `operationalBehaviour()` (the per-launch Level-2 disclosure) and confirmed it
already renders `behaviour_summary` as a plain list of rows with no exclusivity
implied — multiple tags (e.g. Burst Launch, Quick Birth ≤5s, Rapid Migration) already
display simultaneously whenever a launch satisfies more than one. **No change was
needed here** — this was already correct before this task.

The separate, genuinely-exclusive `canonical_behaviour` field (X65.0's own design,
used only for the Behaviour Cohort *filter* card, not per-launch display) is
untouched and left exactly as-is — it answers a different question ("which single
cohort entry point should this launch be discoverable through") from the per-launch
additive summary, and X65.0's own prior work already established this distinction
deliberately.

## Phase 5 — Topology classification preserved where still analytically useful

For `campaign=OTHER_CAMPAIGN` and `campaign=UNCLASSIFIED`, `renderTopologyDistribution()`
is **completely unchanged** — same bucket cards, same evidence-source breakdown
(X65.18), same click-to-filter behavior. Rationale: these populations have not been
proven to share one canonical topology the way the WATCHTOWER ground truth has (X65.19
scoped its 42-launch proof specifically to cascade-confirmed WATCHTOWER launches) —
for these launches, Multi-Level Fan-Out/Fan-Out/Linear/Unknown genuinely still
represent open, useful classification signal, exactly as Phase 5 requires this task
to preserve.

## Phase 7 — Validation

| Check | Result |
|---|---|
| Every confirmed WATCHTOWER launch displays the same topology | **By construction** — the canonical card has no per-launch parameterization; it renders identically for the entire `campaign=WATCHTOWER` selection (291 launches, confirmed live) |
| Behaviour tags continue to vary correctly | **Confirmed** — `operationalBehaviour()` untouched, already additive |
| No detection logic changed | **Confirmed** — zero files under `src/core/` or `src/ops/` touched |
| No attribution changed | **Confirmed** — `provisioning_edges.py`, `campaign_classification.py`, `attribution_outcome.py` untouched |
| No classification changed | **Confirmed** — `funding_topology.py` untouched; the underlying `topology`/`topology_derived_from` fields are unchanged, only which HTML renders them for WATCHTOWER changed |
| Topology buckets remain available only where still analytically useful | **Confirmed** — unchanged for OTHER_CAMPAIGN/UNCLASSIFIED, replaced only for WATCHTOWER |
| Discovery page still loads | **Confirmed** — `GET /discovery` returns HTTP 200; extracted JS passes `node --check`; live HTML contains the new canonical-topology markup |
| `campaign=WATCHTOWER` (291 launches) and `campaign=OTHER_CAMPAIGN` (6,468 launches) both fetched live | **Confirmed** — both API responses retrieved and inspected to verify the branching condition (`TOPO_SELECTION.campaign==='WATCHTOWER'`) has real data on both sides to exercise |

## Components removed or retained

| Component | Status |
|---|---|
| Multi-Level Fan-Out/Fan-Out/Linear/Unknown bucket cards | **Retained**, but only reachable when `campaign !== 'WATCHTOWER'` |
| `x58Card('topology', ...)` click-to-filter mechanism | **Retained**, unchanged, for non-WATCHTOWER campaigns |
| Evidence-source breakdown (X65.18/X65.20) | **Retained**, unchanged, for non-WATCHTOWER campaigns |
| `renderProvisioningWalletExplanation()` (X65.20) | **Retained, relocated** into the new canonical WATCHTOWER card |
| New: `renderCanonicalWatchtowerTopology()` | **Added** — single static card, WATCHTOWER-only |
| `x60TopologyRows()`, `TOPO_SELECTION.topology`, breadcrumb/stage-nav | **Unchanged** for every campaign — this task only changed what is *displayed*, never the underlying filter/cascade mechanism |

## Before / after (WATCHTOWER campaign selected)

**Before**: four clickable cards — "Multi-Level Fan-Out (177)", "Fan-Out (95)",
"Linear (14)", "Unknown (5)" — each implying a genuinely different WATCHTOWER
operational shape, plus a source-breakdown block beneath.

**After**: one static card — "✓ Canonical Operational Topology /
Treasury → SubProvider → Provisioning Wallet → Creator → Launch" — with the existing
Operational Topology vs. Attribution Graph explanation panel (X65.20) directly below
it.

No screenshots were captured (no headless-browser tool available in this environment,
consistent with X65.20's own note) — verification instead used direct HTML/JS
inspection and live API data fetches, as documented above.

## Confirmation: no backend logic changed

`git status --short templates/discovery.html` is the complete file-change list for
this task. No file under `src/`, no `.db` schema, and no test file was modified.
