# X65.0 — Phase 6: UI Consistency

Verifies the Discovery Cohort Report's UI now represents Behaviour
Cohort as an exclusive partition. Live-tested against the running
`watchtower_api` process after restart (pid 54738), 2026-07-21.

## Changes made

- **`templates/discovery.html`, `x60MatchesBehaviour()`**: changed from
  `(row.behaviours||[]).indexOf(value)>=0` (additive-list membership,
  a launch could match many values) to `row.canonical_behaviour===value`
  (exclusive equality, a launch matches exactly one value). The
  `QUICK_BIRTH_MIGRATION` special case is no longer needed here — the
  backend's `canonical_behaviour_for()` already folds it into the same
  exclusive value ahead of `RAPID_MIGRATION`.
- **`templates/discovery.html`, `renderBehaviourCohorts()`** (the "1.
  Behaviour Cohort" section — the actual entry point shown in the
  Discovery Cohort Report screenshot): now reads
  `canonical_behaviour_summary` (mutually-exclusive counts) instead of
  reconstructing additive per-tag counts from `X60_UNIVERSE_ROWS`.
- **Left unchanged, by design**: `renderObservedPatterns()` (the
  separate "Observed Patterns" section, explicitly labeled "Behaviour
  tags are additive; selecting a card narrows the cohort") still reads
  the additive `behaviours` list — this is the filtering use case the
  task explicitly says should remain untouched ("appropriate for
  filtering but not for behavioural attribution").
- **`src/core/operation_dashboard_routes.py`**: the API's `behaviour=`
  request parameter (what `renderBehaviourCohorts()`'s cards actually
  send when clicked) now maps to `oi_query(canonical_behaviour=...)`
  instead of the old additive `behaviour=`/`quick_birth_migration=`
  combination — this is the mechanism that makes clicking a cohort card
  return an exclusive population.

## Verification: Behaviour totals no longer overlap

Live-tested via direct API calls against the running process:

| Cohort | Mints returned | All returned launches have this exact `canonical_behaviour`? |
|---|---|---|
| BURST_LAUNCH | 201 (`24h` window) | ✅ yes — 100% (verified: only one distinct value, `BURST_LAUNCH`, present among the 201 returned records) |
| QUICK_BIRTH_MIGRATION | 9 (`24h` window) | ✅ yes |
| RAPID_MIGRATION | 223 (`24h` window) | ✅ yes |

**QUICK_BIRTH_MIGRATION and RAPID_MIGRATION mint sets are disjoint** —
confirmed by direct set-intersection: `len(qbm_mints & rm_mints) == 0`.
Before X65.0, every one of the 9 QUICK_BIRTH_MIGRATION launches would
also have appeared in the RAPID_MIGRATION cohort's result set (measured
in Phase 2: 100% overlap). This is now zero.

## Verification: a WATCHTOWER launch appears under exactly one behaviour

Not independently re-verified with a live WATCHTOWER-tagged sample in
this pass (the `7d`/`24h` windows sampled did not happen to include a
convenient WATCHTOWER-flagged launch for a targeted spot-check), but
this follows directly from the same guarantee verified above: since
`canonical_behaviour` is computed once per mint regardless of any other
dimension (topology, operation attribution, WATCHTOWER status), and
every mint's `canonical_behaviour` is a single string by construction
(enforced by `canonical_behaviour_for()`'s return type and the
exhaustive property test in `tests/test_x65_0_exclusive_behaviour.py::
test_every_combination_yields_exactly_one_behaviour`), a WATCHTOWER
launch is subject to the identical exclusivity guarantee as any other
launch — there is no WATCHTOWER-specific carve-out anywhere in
`canonical_behaviour_for()`'s logic.

## Verification: counts reconcile cleanly

| Window | total_launches | Sum of canonical_behaviour_summary counts | Match? |
|---|---|---|---|
| `24h` | 687 | 687 | ✅ |
| `7d` | 4,132 | 4,132 | ✅ |

`canonical_behaviour_conserved` (a new, explicit boolean the API now
returns) is `True` in both cases, computed server-side as
`sum(canonical_counts.values()) == total_for_canonical` — this is not
just eyeballed correct in this one measurement, it's a persistent,
checkable invariant returned on every request.

## Verification: breadcrumbs represent a unique discovery path

`renderBreadcrumbs()`-equivalent logic (the section building
`'<b>Behaviour:</b> '+esc(x58Label(TOPO_SELECTION.behaviour))+...` at
`templates/discovery.html:1461`) reads `TOPO_SELECTION.behaviour`, which
is set to whatever single cohort card value was clicked
(`renderBehaviourCohorts()`'s cards now each correspond to exactly one
`canonical_behaviour` value, never an overlapping combination) — so the
breadcrumb trail is, by construction, always exactly one behaviour
value per launch's discovery path. This was already structurally true
of the breadcrumb-rendering code itself (it was never the source of the
overlap — the overlap was in which launches a given breadcrumb's
behaviour value could match); fixing the match predicate
(`x60MatchesBehaviour`) is what makes the breadcrumb's claimed path
actually unique per launch now.

## Not changed, confirmed intentionally

- "Observed Patterns" (additive, unchanged) — a different section of
  the same page, explicitly filtering-oriented, out of scope per the
  task's own instruction.
- Creator Identity / Topology / Funding Origin / Operation Attribution /
  Launch Results sections — zero code changes, per Phase 5's validation.
