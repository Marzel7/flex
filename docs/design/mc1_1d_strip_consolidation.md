# MC1.1D — Strip Consolidation (Duplicate "Live Capabilities" Fix)

Bug-fix follow-on to MC1.1C, reported directly from a live dashboard
view. UI-only, zero backend diff.

## The bug

The rendered page showed two near-identical "Live Capabilities" lists
back to back:

```
Live Capabilities
Live Ingestion
Creator Funding
Operational Intelligence
WATCHTOWER
Infrastructure
Price Tracking

Live Ingestion
Creator Funding
Operational Intelligence
Infrastructure
Price Tracking
```

Root cause: MC1.1B introduced `mc-summary-strip` (a "Live Capabilities"
list, 6 rows including WATCHTOWER) as its fix for the banner-authority
bug. MC1.1C then introduced `mc-cap-strip` (Phase B's terse capability
strip, 5 rows, WATCHTOWER excluded per Phase F) as a *separate* new
element, without ever removing or merging the old one — both rendered
on every page load, immediately adjacent, showing overlapping
information with a visible row-count mismatch (6 vs 5) that made the
duplication obviously wrong rather than just redundant.

## The fix

Removed `mc-summary-strip` entirely — its HTML element, its
`mcUpdateSummaryStrip()` render function, its now-unused
`MC_STATUS_ICON`/`MC_STATUS_COLOR_CLASS` constants, its CSS rules, and
its call site in `updateDashboard()`. `mc-cap-strip` (MC1.1C's Phase B
strip) is now the sole "Live Capabilities" glance — it is strictly a
superset of what the old strip offered (same hierarchy-ordered list,
same status icon per capability) plus click-to-jump behavior and
correct WATCHTOWER exclusion, so nothing was lost by keeping only it.

`mcUpdateBanner()` no longer returns a `worst` value to a caller that
needs it (the removed strip was `worst`'s only consumer) — the call site
in `updateDashboard()` was simplified accordingly; `mcUpdateBanner()`
itself is otherwise unchanged.

## What did NOT change

- `mc-cap-strip` / `mcRenderCapStrip()` (MC1.1C) — unchanged.
- The capability engine, incident engine, evidence engine, severity
  model, rate engine — zero diff, confirmed via `git diff --stat`.
- `src/core/main.py` / the `/api/health/full` API contract — zero diff.
- Incident cards, capability grid, banner content/logic — unchanged.

## Validation

- Confirmed via Flask test client: `id="mc-summary-strip"` no longer
  present in rendered output; `id="mc-cap-strip"` still present;
  `function mcUpdateSummaryStrip` no longer present in the page source.
- Full MC1.1 backend suite (13 tests) re-run, still passing.
- `git diff --stat` confirms zero change to the two backend files.
- Embedded JavaScript passes `node --check`.
