# MC1.1E — Exclude WATCHTOWER from Incident/Banner Navigation

Bug-fix follow-on to MC1.1C/D, reported directly from a live dashboard
screenshot. UI-only, zero backend diff.

## The bug

The screenshot showed the top banner reading **"Critical — WATCHTOWER
degraded"** as the primary headline, and a 4th top-level incident card
titled "WATCHTOWER degraded" with its own "Jump to WATCHTOWER" link —
even though:

- The capability strip and grid (fixed correctly by MC1.1C's Phase F)
  already showed WATCHTOWER nested inside Operational Intelligence's
  card, not as a top-level tile.
- Infrastructure was independently CRITICAL and should have been the
  banner's lead (it's the more severe, platform-level problem — a
  database at 59993ms p99 write latency is a bigger operational concern
  than an Operation-level cascade subscription issue).
- Clicking "Jump to WATCHTOWER" would scroll to a card that no longer
  exists in the grid (MC1.1C removed it from there), landing nowhere
  useful.

Root cause: MC1.1C's Phase F only filtered WATCHTOWER out of
`mcRenderCapStrip()` and `mcRenderCapabilityGrid()`. `mcRenderIncidents()`
and `mcUpdateBanner()` still read the raw, unfiltered
`fullHealth.incidents` array — which still contains a WATCHTOWER entry
whenever that capability is independently abnormal, since the frozen
incident engine has no reason to know about MC1.1C's presentation-layer
navigation decision. When WATCHTOWER happened to be more severe (or tied)
with the other abnormal capabilities, it won the severity-first
primary-selection logic and became the banner's headline and its own
incident card — exactly contradicting Phase F's stated intent that
WATCHTOWER should not appear in platform-level capability navigation at
all.

## The fix

New shared filter, `mcPlatformIncidents(incidents)`, applied at both
consumption points:

- `mcRenderIncidents()` now filters `fullHealth.incidents` through it
  before sorting/selecting the primary incident — a WATCHTOWER incident
  can no longer become the primary or open its own top-level card.
- `mcUpdateBanner()`'s call site filters the same way before passing
  incidents in — the banner can no longer lead with WATCHTOWER.

WATCHTOWER's own status/evidence is **not discarded** — it remains fully
visible via the existing nested subline inside the Operational
Intelligence card (`mcWatchtowerSublineHtml()`, introduced in MC1.1C),
which reads `fullHealth.capabilities.watchtower` directly and was never
affected by this bug (that data path never went through the incidents
array in the first place). An operator investigating Operational
Intelligence will still see "WATCHTOWER (Operation): Cascade offline,
Cascade stale" exactly as before — only the *redundant, misleadingly
promoted* top-level incident card and banner headline are removed.

## What did NOT change

- The capability engine, incident engine, evidence engine, severity
  model, rate engine — zero diff, confirmed via `git diff --stat`.
- `src/core/main.py` / the `/api/health/full` API contract — zero diff.
  `fullHealth.incidents` still contains the WATCHTOWER entry exactly as
  the frozen incident engine computes it; only the dashboard's own
  navigation logic now filters it before display.
- `mcWatchtowerSublineHtml()` / the Operational Intelligence card's
  nested WATCHTOWER subline — unchanged, unaffected.
- MC1.1C's capability strip/grid WATCHTOWER exclusion — already correct,
  unchanged.

## Validation

- Reproduced the exact screenshot scenario (creator_funding WARNING,
  operational_intelligence WARNING, watchtower CRITICAL, infrastructure
  CRITICAL) and traced the filter + primary-selection logic under Node:
  confirmed the banner/primary now correctly leads with `infrastructure`
  (CRITICAL) instead of `watchtower`, and WATCHTOWER produces zero
  top-level incident cards.
- Full MC1.1 backend suite (13 tests) re-run, still passing.
- `git diff --stat` confirms zero change to the two backend files.
- Template renders via Flask test client (200 status), `mcPlatformIncidents`
  confirmed present in output.
- Embedded JavaScript passes `node --check`.
