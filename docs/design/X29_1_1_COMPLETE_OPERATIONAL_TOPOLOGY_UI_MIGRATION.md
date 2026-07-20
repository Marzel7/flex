# X29.1.1 — Complete Operational Topology UI Migration

**Status: UI migration sprint, complete.** Builds on
[X29.1](X29_1_HIERARCHICAL_OPERATIONAL_CLASSIFICATION.md), which explicitly
flagged (X29.0 Part 6, recommendation 4) that promoting the new Operational
Intelligence view over the legacy Investigation Queue "needs explicit
sign-off before removal." This sprint's brief is that sign-off for
**de-prioritisation**, not deletion — the legacy panel is retained, collapsed,
and relabelled, exactly as instructed ("Do not remove it yet").

**No detection, classification, replay, or API logic was changed.** Confirmed
by `git diff --stat`: `src/ops/funding_topology.py`,
`src/ops/funding_mechanism.py`, `src/ops/operational_behaviour_tags.py`,
`src/ops/operational_intelligence.py`, `src/ops/investigation_pipeline.py`,
and `src/ops/behaviour_queue.py` all show **zero diff** from this sprint.
The only files touched are `templates/discovery.html` (the UI) and one new
test file.

## What changed

### 1. Operational Intelligence promoted to primary workflow

`discovery.html`'s `landing()` render now emits the Operational Intelligence
panel (`operationalIntelligencePanel()`) **immediately after** the top
scorecard row and **before** the main Intelligence feed / legacy panel —
literally the first substantive content block on the page, matching the
brief's "first thing analysts see" success criterion.

### 2. Progressive drill-down (replacing X29.1's always-expanded tree)

X29.1 shipped a **fully-expanded** tree (every topology, every behaviour
under it, every mechanism under that, all rendered at once) — functional,
but not what this brief calls for. X29.1.1 replaces it with genuine
progressive navigation:

- `TOPO_SELECTION = {topology, behaviour, mechanism}` — in-memory JS state
  only, never persisted (matching the storage-model requirement: the
  hierarchy itself is never stored, and neither is the current selection).
- `renderTopoLevel()` shows **only** the current level's rows: topologies
  at the root, behaviours once a topology is picked, mechanisms once a
  behaviour is picked — "Only child items for the current selection should
  be displayed," per the brief's UI Layout example.
- A breadcrumb (`topoBreadcrumb()`) shows the selection path
  (`Operational Intelligence → Fan-Out → Rapid Birth→Migration`) and lets
  an analyst click back up to any prior level, resetting deeper selections.
- Clicking a row at any level advances `TOPO_SELECTION` and re-renders —
  no page navigation, no new HTTP request for the tree itself (the full
  hierarchy is fetched once on page load and cached client-side in
  `TOPO_TREE_CACHE`; drill-down re-slices that same cached tree).

**Verified before building on it, not assumed**: `build_hierarchy()`'s
existing output (unchanged since X29.1) is already correctly scoped
per-parent — a behaviour node's `count` and `children` are already computed
only within that specific topology, and a mechanism node's `count` only
within that topology+behaviour combination. This was confirmed directly
against a live `build_hierarchy()` call before writing any UI code (see the
`test_behaviour_level_is_scoped_to_the_selected_topology_only` test), and is
the reason this sprint required zero backend changes — the correctly-scoped
data was already there, only the rendering was flat/always-expanded rather
than progressive.

### 3. Cumulative filtering of the launch table

A new "Matching Launches" section beneath the drill-down calls
`updateLaunchTableFilter()` on every selection change, hitting the **same**
`/api/ops-v2/operational-intelligence` endpoint X29.1 already built with
whichever combination of `topology=`/`behaviour=`/`mechanism=` params are
currently selected — exactly the brief's Filtering Rules:

```
Fan-Out only:                      ?topology=FAN_OUT
Fan-Out + Rapid Birth→Migration:   ?topology=FAN_OUT&behaviour=RAPID_BIRTH_LAUNCH
Fan-Out + Rapid Birth + Wrap-Close: ?topology=FAN_OUT&behaviour=RAPID_BIRTH_LAUNCH&mechanism=WSOL_WRAP_CLOSE
```

Verified live against the running server (gunicorn reloaded via `SIGHUP`
to pick up the template change):

```
GET /api/ops-v2/operational-intelligence?window=24h&topology=FAN_OUT&behaviour=REPEAT_CREATOR
-> 27 matching mints
```

— matching the number seen under Fan-Out → Repeat Creator in the same-day
hierarchy view, confirming the drill-down count and the launch-table filter
agree with each other (no double-counting or drift between the two).

### 4. Legacy Investigation Queue: collapsed, relabelled, not removed

`healthPanel()` now renders a `<details>` element (no `open` attribute, so
collapsed by default) with:
- Heading changed from "Investigation Queue" to **"Legacy Investigation
  Queue"**.
- A visible note: **"Superseded by Operational Intelligence hierarchy."**
- Identical underlying data and click-through behaviour — `bucket=` links
  to `/discovery?bucket=...` are unchanged, since `investigation_pipeline.py`
  itself was not touched.
- Moved to render **after** the Operational Intelligence panel in the page
  layout, rather than before it.

## Validation

**Count consistency** (per the brief's explicit Validation requirements):
- `test_topology_level_counts_are_exclusive_and_sum_to_total` — topology
  remains exclusive; the top-level counts sum to the total record count.
- `test_behaviour_level_is_scoped_to_the_selected_topology_only` /
  `test_mechanism_level_is_scoped_to_topology_and_behaviour_selection` —
  parent-scoped counts are correct at every level, not global counts
  mislabeled as scoped ones.
- `test_multi_tag_mint_appears_under_every_matching_branch_without_inflating_topology`
  — a launch with 2 behaviour tags and 2 mechanism tags appears under every
  matching branch (additive tags working correctly) while the exclusive
  topology count still counts it exactly once.
- `test_narrowing_a_filter_never_increases_the_result_set` — the
  cumulative-filter result set is provably monotonically non-increasing as
  more levels are selected (topology ⊇ topology+behaviour ⊇
  topology+behaviour+mechanism), the core navigation guarantee "progressively
  narrower" requires.

**API/contract unchanged**:
- `test_hierarchy_response_shape_matches_x29_1_contract` — pins the exact
  JSON key shape `build_hierarchy()` produces, so a future accidental
  restructuring is caught immediately.
- `test_build_hierarchy_still_pure_no_mutation` — re-confirms the
  already-established purity guarantee still holds now that the UI calls
  this function on every single navigation click (not just once per page
  load as in X29.1), since a mutation bug would now surface far more
  frequently in practice.

Live server verification (gunicorn reloaded, port 5002): `/discovery`
returns 200; `/api/ops-v2/operational-intelligence?window=24h` returns the
same summary numbers as X29.1's replay; the hierarchy view and the
cumulative-filter endpoint agree with each other on a real drill-down path
(Fan-Out → Repeat Creator = 27 in both).

10 new tests, all passing. Combined with X29.1's 24 tests, 34/34 pass.

## Success criteria — status

- First thing analysts see is the Operational Intelligence hierarchy — ✅ moved to the top of `landing()`'s render.
- Investigation begins with Funding Topology, not legacy buckets — ✅ root level of the drill-down is Topology; legacy buckets are now below, collapsed.
- Behaviour and Mechanism explored through drill-down, not competing classifications — ✅ progressive one-level-at-a-time rendering replaces the always-expanded tree.
- Cumulative filtering works correctly across all three levels — ✅ verified live against the running server and by 4 dedicated tests.
- Legacy Investigation Queue remains available for comparison only — ✅ `<details>` collapsed by default, relabelled, superseded note added, data/logic untouched.
- No changes to detection, replay, or classification logic — ✅ zero diff in every classifier module and `investigation_pipeline.py`; only `discovery.html` (+ one new test file) changed.
