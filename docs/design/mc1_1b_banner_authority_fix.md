# MC1.1B — Banner/Strip Single Source of Truth Fix

Bug-fix follow-on to MC1.1A, reported directly from a live screenshot
review. UI-only, zero backend diff.

## The bugs

**1. Banner/incident-card severity disagreement.** The top-of-page
banner (`mcUpdateBanner`) showed `"Critical — Platform — database
AT_RISK"` while every incident card below it read `WARNING`. Root
cause: the banner was never actually re-sourced when MC1.1 added the
capability/incident layer — it continued deriving its own `worst`/message
text from `renderPlatformGroup`/`renderIngestionGroup`/
`renderIntelligenceGroup`/`renderInfraGroup` (the four pre-existing
"Phase 9" legacy groups), an entirely separate, independent computation
over the *same* raw `/api/health/full` subsystem data that the new
capability engine also classifies. Two classifiers looking at the same
data can disagree — the fix is to have exactly one classifier the whole
page defers to, not to reconcile two.

**2. Live Ingestion could be silently missing from the top.** Because
the banner never read `fullHealth.incidents` at all, it was structurally
impossible for it to ever say "Live Ingestion unavailable" as its
headline, no matter how severe that specific capability was — the whole
point of MC1.0's capability-first model was undermined at the one place
(the banner) an operator looks first.

## The fix

`mcUpdateBanner` and `mcUpdateSummaryStrip` are now driven **exclusively**
by `fullHealth.incidents` / `fullHealth.capabilities` — the identical
data the incident cards and capability grid render from. They can no
longer disagree, because there is only one source of truth left.

- `mcUpdateBanner(incidents)`: sorts incidents by capability hierarchy
  order (`Object.keys(MC_CAPABILITY_LABELS)`, unchanged from MC1.1 —
  Live Ingestion → Creator Funding → Operational Intelligence →
  WATCHTOWER, with Infrastructure/Price Tracking alongside), leads with
  the most-upstream CRITICAL (or WARNING) incident's own title, and lists
  any other concurrent incidents in the sub-line. `worst` is computed
  from the same incidents array and returned so the caller can pass it
  straight to the summary strip — no second independent computation.
- `mcUpdateSummaryStrip(worst, capabilities)`: replaced the old
  "Healthy / Attention Required" two-column layout (driven by the four
  legacy groups) with a single "Live Capabilities" list, one line per
  capability in hierarchy order, ✓/⚠/✗ icon matching `cap.status` —
  directly implementing the "Platform Health / Live capabilities" view
  requested in review.
- `mcRenderIncidents` (added in MC1.1) now also sorts by the same
  hierarchy order via a new shared `mcSortByHierarchy()` helper, so the
  incident *cards* and the banner always agree on ordering too — Live
  Ingestion's incident card, when present, is always first.

The four legacy group-render functions (`renderPlatformGroup` etc.)
still run and still populate their own detail sections further down the
page — nothing about their content changed, they simply no longer feed
the top banner/strip.

## What did NOT change

- The capability engine, incident engine, evidence engine, severity
  model, and rate engine (`src/ops/mission_control_capabilities.py`) —
  zero diff, confirmed via `git diff --stat`.
- `src/core/main.py` / the `/api/health/full` API contract — zero diff.
- The capability card layout from MC1.1A (metrics → status → evidence →
  diagnostics) — unchanged.
- The legacy per-group detail sections (Platform/Ingestion/Intelligence/
  Infrastructure bodies) — unchanged content, still rendered.

## Validation

- Reproduced the exact reported scenario (`database: AT_RISK` +
  `ingestion: DOWN`) against the real capability engine
  (`compute_capabilities`/`compute_incidents`/`compute_platform_status`)
  and confirmed the backend already correctly classified `live_ingestion`
  as CRITICAL — the bug was entirely in the banner's data source, not in
  MC1.1's computation layer.
- Extracted and ran the new banner/strip sort-and-lead logic under Node
  against that exact scenario's incident/capability shape: banner
  correctly produces `"Critical — Live ingestion unavailable"` with
  `"Infrastructure WARNING"` in the sub-line; summary strip correctly
  produces the six-capability ✓/⚠/✗ list in hierarchy order with Live
  Ingestion first.
- Full MC1.1 backend suite (13 tests) re-run, still passing.
- Template renders via Flask test client (200 status).
- Embedded JavaScript passes `node --check`.
