# X65.20 — Refactor the Discovery UI to Reflect the Canonical WATCHTOWER Topology

Implementation summary. UI/presentation refactor only — no attribution logic,
detection logic, classifier logic, or database schema was changed, per the task's
explicit constraint.

## What changed

All changes are in `templates/discovery.html`. No backend files were touched.

### Phase 1/2 — Canonical topology + hierarchy strip

Already correct from an earlier pass (X42.0): `operatorHierarchyStrip()`
(`discovery.html:2446-2452`) already reads
`['Operator','WATCHTOWER','Operations','Treasuries','Subproviders','Provisioning Wallets','Creators','Launches']`.
No change needed — verified directly, not assumed.

### Phase 3/7 — Lineage graph: synthetic Provisioning Wallet node + Operational vs Attribution distinction

`lineageChain()` (`discovery.html`) now inserts a synthetic, clearly-marked
"Provisioning Wallet — Inferred" card between any SUBPROVIDER node and the CREATOR
node it funded, whenever the persisted `wt_provisioning_edges`/`wt_watchtower_launches`-derived
chain jumps directly from one to the other (the exact collapsed hop X65.17/X65.19
proved). The card:

- Uses a dashed amber border (`.dw-lineage-node-inferred`), visually distinct from the
  solid-bordered, directly-observed nodes around it.
- Carries an "Inferred" badge and a tooltip citing the X65.19 proof (42/42
  ground-truth launches).
- Explicitly states the wallet "is not yet persisted individually" rather than
  fabricating an address.

The panel's own copy was updated to state plainly: *"This is the persisted attribution
graph... the proven operational topology (X65.19) still includes a distinct
Provisioning Wallet stage — shown above, marked 'Inferred'."*

Separately, a new static explanation block (`renderProvisioningWalletExplanation()`)
renders a two-column **Operational Topology vs Attribution Graph** comparison directly
beneath the Topology card, using the exact two diagrams from X65.19/X65.20's own task
spec, each labeled with a colored badge ("Proven, 42/42" vs "Currently persisted") so
the two concepts are never presented as interchangeable anywhere on the page.

### Phase 4 — Terminology correction

`lineageNodeCard()`'s `fan_out_count` property (sourced from
`operational_lineage.py::_fan_out_count()`, proven creator-only by X65.17) now renders
as **"Creators Funded — N confirmed creator(s)"**, replacing the previous
**"Fan-out — N downstream wallet(s)"** wording that X65.18 identified as the single
clearest mislabeling in the UI. No other "fan-out" label was touched — the Campaign
fingerprint's "Fan-out observed" stat (`wt_candidate_websocket_watches`-sourced,
genuine raw recipient evidence, per X65.18) and the Provisioning Activity card's
"Observed edges" wording were both already correct and are left unchanged.

### Phase 5 — Topology card evidence-source exposure

`renderTopologyDistribution()` now computes, per topology bucket, a breakdown of which
evidence source (`topology_derived_from`, already returned by the API, per
X65.10/X65.18) produced each launch's label — mapped through a small new
`x60TopologySourceLabel()` helper to four short, honest names: "Raw candidate
observations," "Confirmed creator history," "Sub-provider session lineage," and
"Walkback-resolved chain." Rendered as a compact chip row beneath the existing topology
count cards, directly closing the ambiguity X65.18 identified (the same "Fan-Out"
label could previously rest on either raw fan-out evidence or confirmed-creator-count
evidence with no visual distinction).

### Phase 6 — Provisioning Wallet explanation

Folded into the same new `renderProvisioningWalletExplanation()` panel (Phase 3/7,
above) rather than a separate tooltip, since the task's own two required explanatory
points ("one Provisioning Wallet per launch," "creator never receives funds directly
from the SubProvider," "current attribution graph may collapse this stage") map
directly onto the two-column comparison's own copy.

### Phase 8 — Documentation

This file. The hierarchy-strip's own pre-existing code comment
(`discovery.html:2443-2445`, referencing X39/X40/X41) already documents the canonical
model at the code level; this document is the task-level record of what changed and
why, per this session's established "single linkable" documentation pattern.

## Files modified

| File | Change |
|---|---|
| `templates/discovery.html` | `lineageNodeCard()` terminology fix; new `syntheticProvisioningWalletCard()`; `lineageChain()` insertion logic; new `x60TopologySourceLabel()`; `renderTopologyDistribution()` evidence-source breakdown; new `renderProvisioningWalletExplanation()`; new CSS rules for `.dw-lineage-node-inferred`, `.dw-topo-source-*`, `.dw-provwallet-*` |

## Verification performed

- `GET /discovery` returns HTTP 200 (271,856 bytes), no server error markers.
- Extracted the page's JS, stripped Jinja placeholders, and ran `node --check` —
  syntax valid.
- Simulated the updated `lineageChain()`/`lineageNodeCard()` logic in Node against a
  **real** `GET /api/ops-v2/lineage/<subprov_wallet>` response (Treasury → SubProvider
  → Creator, `fan_out_count: 1`) and confirmed the rendered HTML correctly shows
  "Creators Funded — 1 confirmed creator" and inserts the dashed "Provisioning Wallet
  — Inferred" card between the SubProvider and Creator nodes.
- Confirmed via `grep` that all four new CSS-class markers
  (`dw-provwallet-panel`, `dw-topo-source-block`, `dw-lineage-node-inferred`,
  `Creators Funded`) are present in the live-rendered `/discovery` HTML.
- No headless-browser tool was available in this environment to capture literal
  before/after screenshots; verification instead used direct HTML/JS inspection plus
  a real-data rendering simulation, as documented above.

## Confirmation: no attribution/detection/schema changes

- No file under `src/ops/`, `src/core/`, or any `.db` schema was modified.
- `git status`-equivalent scope of this task: `templates/discovery.html` only.
- `funding_topology.py`, `campaign_classification.py`, `operational_lineage.py`,
  `provisioning_edges.py`, and every classifier X65.17-X65.19 examined are byte-for-byte
  unchanged.

## What remains genuinely unresolved (by design, per X65.19)

The Provisioning Wallet's actual per-launch address is still not individually
persisted anywhere (X65.17/X65.19's own finding) — this refactor makes that gap
honest and visible in the UI rather than closing it, since closing it would require a
new persistence/detection change explicitly out of this task's scope.
