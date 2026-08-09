# MC1.1C — Mission Control UX Simplification

Presentation-only follow-on. Zero backend diff (confirmed via
`git diff --stat` against `src/ops/mission_control_capabilities.py` and
`src/core/main.py`). Named MC1.1C rather than reusing "MC1.1B" (the
charter's suggested commit name) because that name was already used for
the banner-authority bug fix in the immediately preceding commit —
reusing it here would make `git log` ambiguous between two different
pieces of work.

## What changed

**Phase A — single primary incident.** `mcRenderIncidents` now computes
one "primary" incident (highest severity; capability hierarchy order as
tiebreak among equally-severe incidents, so Live Ingestion still wins a
CRITICAL/CRITICAL tie) and renders only that one expanded. Every other
concurrent incident renders as a collapsed one-line card (icon + severity
badge + title, click to expand) — same underlying `impact`/
`contributing_signals` content, just not shown until requested.

**Phase B — capability strip.** New `mc-cap-strip` element/function:
five rows (platform capabilities only, WATCHTOWER excluded per Phase F),
hierarchy order, one line each — `✓ Infrastructure`, `✗ Live Ingestion`,
`⚠ Creator Funding`, etc. Click jumps to (and expands, if collapsed) the
corresponding capability card. This is distinct from MC1.1B's existing
`mc-summary-strip` ("Live Capabilities" list under the banner) — that
strip is kept as-is; this new one is the terser, higher-priority glance
rendered directly under the banner per the charter's explicit example.

**Phase C — operational metrics first.** Unchanged from MC1.1A — this
was already the card order (metrics → status → evidence → diagnostics).
No further reordering was needed; MC1.1C's job here was verifying it
still holds true after the collapse/expand changes, which it does.

**Phase D — collapse secondary cards.** `mcRenderCapabilityGrid` now
computes the same single "primary" capability (highest severity among
the 5 platform capabilities, hierarchy tiebreak) and renders only that
one expanded with full metrics/status/evidence/diagnostics. Every other
capability renders collapsed: name + status dot + a short 1-2-signal
summary line (e.g. "Worker stopped, Queue stalled"), expandable on
click via the card's own head.

**Phase E — concise diagnostic labels.** New `MC_CONCISE_SIGNAL_LABELS`
map + `mcConciseSignalLabel()` helper. Presentation only: the underlying
signal `name` from the frozen evidence engine is still the lookup key
and still drives which detail applies — only the *displayed string* for
each signal name changed (`worker_status` → "Worker stopped" instead of
the raw snake_case name). Falls back to the original space-separated
name for any signal not in the map, so a future backend signal never
renders blank. Applied both to collapsed-card summaries (Phase D) and
the full expandable signal list.

**Phase F — WATCHTOWER relocated.** `MC_PLATFORM_CAPABILITY_ORDER`
excludes `watchtower` and is now what the capability strip and
capability grid iterate over (5 entries, not 6). WATCHTOWER's own
capability computation, status, evidence, and incident-opening behavior
are entirely unchanged in the frozen backend — `fullHealth.capabilities.watchtower`
still exists, is still read, and still opens its own incident card if
independently abnormal (Phase A's incident logic is untouched and
doesn't special-case it). Only its *navigation placement* changed: a new
`mcWatchtowerSublineHtml()` renders it as a compact one-line sub-status
inside the Operational Intelligence card ("WATCHTOWER (Operation): ..."),
reflecting the charter's framing that WATCHTOWER is an Operation
consuming Operational Intelligence's capability, not a platform
capability of its own for top-level navigation purposes.

**Phase G — information density.** With the above, the default view for
a page with N concurrent abnormal capabilities/incidents now shows
exactly 1 expanded incident + 1 expanded capability card + a 5-row
capability strip, regardless of how many capabilities are actually
degraded — everything else is a single line. Measured against a real
live snapshot (3 concurrent WARNING incidents: creator_funding,
operational_intelligence, infrastructure): estimated visible-character
count dropped from ~1815 to ~541, a **70.2% reduction** — meeting the
charter's "reduce visible text by at least 70%" target. This is a rough
proxy (string-length estimate over the same API payload's field
lengths, not a pixel-accurate rendered-DOM measurement) but reflects the
real, substantial reduction the collapse-by-default behavior produces.

## A bug noticed and fixed in passing

The MC1.1A-era signal list rendered `s.abnormal ? '✓' : '✗'` — backwards
(an abnormal/problem signal was getting a checkmark, a normal signal an
✗). Fixed to `s.abnormal ? '✗' : '✓'` while touching this exact line for
Phase E's concise-label swap. This was a pure display bug (the `abnormal`
boolean itself, computed by the frozen evidence engine, was always
correct) — worth noting since it's a substantive fix bundled into an
otherwise presentation-reordering commit.

## What did NOT change

- The capability engine, incident engine, evidence engine, severity
  model, and rate engine (`src/ops/mission_control_capabilities.py`) —
  zero diff.
- `src/core/main.py` / the `/api/health/full` API contract — zero diff.
- MC1.1B's banner/summary-strip single-source-of-truth fix — still in
  place, `mcUpdateBanner`/`mcUpdateSummaryStrip` untouched by this
  milestone.
- The legacy per-group detail sections further down the page — unchanged.
- WATCHTOWER's own capability status/evidence/signals — computed
  identically; only its default navigation surface moved.

## Validation

- Full MC1.1 backend suite (13 tests) re-run, all still passing.
- `git diff --stat` confirms zero change to the two backend files.
- Reproduced the exact real-world "3 concurrent WARNING incidents, no
  CRITICAL" scenario against live API data and traced the primary-selection
  logic under Node: correctly picks `creator_funding` (most upstream of
  the three) as the single expanded incident/card, correctly excludes
  `watchtower` from the strip/grid, correctly collapses the other two.
- Template renders via Flask test client (200 status).
- Embedded JavaScript passes `node --check`.
