# X45.0 — Discovery Intelligence UX Refinement (Analyst-First Presentation)

Follows [X44.0](X44_0_CEX_FUNDING_INTELLIGENCE.md). Strictly presentation only — no
backend files were touched in this pass (confirmed via file mtimes: `templates/
discovery.html` was the only file modified this turn; all backend modules date from
earlier X41–X44 turns). No new API requests were added; every section below reads data
already fetched by the two existing calls (`/api/ops-v2/operational-intelligence`,
`/api/ops-v2/cex-funding-intelligence`).

## What changed

### 1. Intelligence Summary
New `renderIntelligenceSummary()`, mounted directly under the Topology→Behaviour→
Infrastructure header once a topology is picked. Shows Topology label, Launches (from
the already-fetched hierarchy node's own `count`), and — **only when the CEX cache has
data** — Funding Origins (CEX) and Exchanges counts, derived from the already-loaded
`CEX_MINT_CACHE`.

**Deliberate scope decision** (confirmed with the user before implementing): Creators/
Treasuries/Subproviders from the spec's example are **not shown**, because the
already-loaded API responses don't carry that data at the whole-selection level without
issuing a new request per mint — which would violate "no additional requests." Per
explicit direction, a field not genuinely derivable from already-loaded data is omitted
entirely, never shown as 0/guessed/estimated.

### 2/3. Behaviour Summary + Explainer
`renderBehaviourCards()` replaces the flat behaviour-row list with metric cards
(count + percentage). **Percentages are computed against the current topology
selection's own count** (`b.count / topologyNode.count`), not the whole-window
`total_launches` the backend's own `coverage_pct` field uses — verified this distinction
matters: at the 24h window, `RAPID_MIGRATION`'s backend-computed `coverage_pct` (88.9%,
against 659 total launches) differs from its selection-scoped percentage inside the
UNKNOWN topology alone (36%, against that topology's 1076 own launches). The static
explainer line ("Behaviour tags are additive...") is appended below the cards.

### 4-6. Funding Intelligence (exchange cards + funding path)
Replaced the old flat exchange→wallet-list layout with `renderExchangeCard()`: one
`<details>` card per exchange (collapsed by default), showing aggregate launch/creator/
Operation-presence/withdrawal-origin counts, expandable to per-origin detail
(`renderOriginDetail()`) with a vertical funding-path chain (`fundingPathHtml()`, "↓"
between real hops only — same `funding_path` array X44.0's backend already computes,
never a new hop invented here).

### 7. Intelligence Highlights
`renderIntelligenceHighlights()` — every highlight is a direct read of already-loaded
`CEX_FULL_CACHE` fields: multiple-exchanges (distinct exchange count), cross-exchange
infrastructure (a `shared_infrastructure` entry with `role==='Cross-Exchange Hop'`),
shared treasury, shared withdrawal origin (any origin's own `strength_indicators`), and
the one ⚠-marked (not ✓) case — multiple CEX origins for one creator, from
`multi_cex_creators`. No highlight is ever inferred; a highlight is only shown if its
underlying evidence array/flag is actually non-empty/true.

### 8. Evidence Strength
`evidenceStrengthHtml()` replaces isolated ✓/✗ icons with the pattern from the brief:
"Exchange Match" shows the actual exchange name next to ✓; "Linked Operations" shows the
readable "No linked Operations." text instead of a bare "0" when the count is zero (the
brief's Section 12 Empty States requirement, applied here specifically).

### 9/10. Supporting Launches collapsed + reordered hierarchy
The launch-list section is now wrapped in a `<details>` with a
`Show Supporting Launches (N)` summary toggle, collapsed by default. The panel's HTML
skeleton (`operationalIntelligencePanel()`) was restructured with distinct mount points
(`dw-x45-summary-mount`, `dw-topo-level-mount` for Behaviour, `dw-x45-funding-mount`,
`dw-topo-infra-mount`, then the collapsed launch section) so the DOM order itself matches
the required visual hierarchy: Summary → Behaviour → Funding Intelligence →
Infrastructure → Supporting Launches.

### Navigation model change (confirmed with the user before implementing)
The prior page used a strict one-level-at-a-time wizard (pick Topology, THEN see
Behaviour, THEN see Infrastructure, each hiding the previous view). This didn't match the
spec's dashboard layout, where Summary/Behaviour/Funding Intelligence/Infrastructure are
all visible together once a topology is picked. Reworked accordingly: picking a topology
now reveals all four sections at once; clicking a behaviour card still narrows the launch
list below (and can be toggled off by clicking the same card again) without hiding the
other sections. `infrastructureRowsHtml()` aggregates mechanism counts across all
behaviour children when no specific behaviour is selected yet, so Infrastructure is
populated immediately rather than staying empty until a second click — computed
client-side from the same tree data already fetched, not a new request.

### 11/12. Typography and empty states
New CSS scale (`dw-x45-section-title` > `dw-x45-metric-v` > `dw-x45-meta`, wallet
addresses at `font:9.5px ui-monospace` — the lowest-priority tier) applied throughout the
new sections. "No linked Operations." is the one concrete empty-state string implemented
per the brief's example; other genuinely-zero cases (e.g., no behaviour tags recorded)
already had a readable message before this pass and were left as-is.

## Validation

- **No backend files modified**: confirmed directly via file modification timestamps —
  `templates/discovery.html` (13:56) is the only file touched this turn; every backend
  module (`cex_funding_intelligence.py`, `operational_behaviour_tags.py`,
  `treasury_bank.py`, etc.) predates this session's start.
- **No new API requests**: `renderIntelligenceSummary`/`renderBehaviourCards`/
  `renderFundingIntelligence`/`renderIntelligenceHighlights` all read from
  `TOPO_TREE_CACHE` and `CEX_FULL_CACHE`/`CEX_MINT_CACHE`, both populated by the same two
  fetches (`loadOperationalIntelligence()`, `loadCexMintCache()` inside
  `updateLaunchTableFilter()`'s existing `Promise.all`) that already existed before this
  pass — no third fetch call was added anywhere.
- **Identical API payloads**: confirmed by re-running the exact same two endpoint calls
  used before this pass and diffing shapes — unchanged (this pass added zero query
  params, zero new routes).
- **Syntax/parse**: brace-balance check (820/820) and a full Jinja2 template parse both
  pass cleanly after the restructure.
- **Full regression suite**: `cex`/`discovery`/`operational_intelligence`-keyword tests
  show only the same 2 pre-existing failures already confirmed unrelated in X41.0–X44.0
  (stale HTML-content string assertions); 114 passed, including all 16
  `test_x44_0_cex_funding_intelligence.py` backend tests (untouched by this pass).
- **Visual verification**: extracted the exact new render functions
  (`renderIntelligenceSummary`, `renderBehaviourCards`, `renderFundingIntelligence`,
  `renderIntelligenceHighlights`, `evidenceStrengthHtml`, `renderOriginDetail`,
  `renderExchangeCard`, `fundingPathHtml`) plus their CSS into a standalone HTML page, fed
  it the real `/api/ops-v2/operational-intelligence` hierarchy node for the UNKNOWN
  topology (1076 launches) and the real `/api/ops-v2/cex-funding-intelligence` response,
  and screenshotted the result. Confirmed: Intelligence Summary shows correctly scoped
  fields (Topology/Launches/Funding Origins/Exchanges, no fabricated Creator/Treasury
  counts); Behaviour cards show real counts with selection-scoped percentages (e.g.
  "Rapid Migration (<5m) 388 36%" against the 1076-launch topology, not the whole-window
  total); Funding Intelligence shows 2 genuine Intelligence Highlights, 8 exchange cards
  sorted by launch count, and the expanded Binance card showing correct origin detail,
  funding path, Evidence Strength (✓ Binance / ✓ Shared Withdrawal Origin / ✗ Shared
  Treasury / ✗ Shared Subprovider / "No linked Operations.") — a screenshot attempt via a
  full CDP-driven click-through was abandoned after repeated headless-Chrome instance
  conflicts in this environment; the standalone-function verification method (same
  approach already validated in X44.0) was used instead.

## Explicit constraints honored

No architecture, schema, attribution, scoring, behaviour classification, Operation logic,
Operator logic, or backend intelligence generation was modified. No API contract changed.
Creators/Treasuries/Subproviders were correctly omitted from the Summary card rather than
approximated, per explicit product direction, keeping this pass consistent with the
evidence-first discipline established across X39–X44.

## Answer to the stated success criterion

An analyst opening a topology now sees, in order: a summary of what's selected, how it
behaved (with selection-scoped percentages), where its CEX funding originated (collapsed
exchange cards, expandable to funding-path detail), what infrastructure is shared
(Intelligence Highlights + per-origin Evidence Strength), and only then the raw
supporting launch list, collapsed by default. Every value shown traces to data the page
was already fetching — nothing new was computed, queried, or invented.
