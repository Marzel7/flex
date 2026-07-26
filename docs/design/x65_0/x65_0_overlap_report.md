# X65.0 — Phase 2: Overlap Analysis

Measured live, 2026-07-21, `7d` window, ~4,126-4,128 launches (the exact
population differs by a couple of rows between the pure behaviour-tag
measurement and the full `build_operational_intelligence` measurement
due to normal population drift between the two measurement calls, both
against the live, actively-written database).

## Pairwise overlaps within `behaviours` (RAPID_BIRTH_LAUNCH, BURST_LAUNCH, migration-timing trio, CREATOR_RECYCLING)

| Pair | Overlap count | % of first | % of second | Intentional? |
|---|---|---|---|---|
| BURST_LAUNCH & RAPID_MIGRATION | 859 | 93.6% of BURST_LAUNCH (918) | 22.3% of RAPID_MIGRATION (3,860) | **Yes, by design** — these are independent facts (clustering vs. individual speed) that happen to co-occur often because fast-migrating launches are more likely to cluster together in absolute time |
| BURST_LAUNCH & MIGRATION_5_TO_15M | 11 | 1.2% of BURST_LAUNCH | 50.0% of MIGRATION_5_TO_15M (22) | Same as above, smaller population |
| BURST_LAUNCH & DELAYED_MIGRATION | 5 | 0.5% of BURST_LAUNCH | 22.7% of DELAYED_MIGRATION (22) | Same as above |
| BURST_LAUNCH & CREATOR_RECYCLING | 542 | 59.0% of BURST_LAUNCH | 22.0% of CREATOR_RECYCLING (2,462) | **Yes, by design** — a creator running a serial-deployer operation is likely to also produce clustered (burst) migrations |
| RAPID_MIGRATION & CREATOR_RECYCLING | 2,378 | 61.6% of RAPID_MIGRATION | 96.6% of CREATOR_RECYCLING | **Yes, by design** — the single largest overlap in the system; serial/recycling creators overwhelmingly also migrate fast |
| MIGRATION_5_TO_15M & CREATOR_RECYCLING | 16 | 72.7% of MIGRATION_5_TO_15M | 0.6% of CREATOR_RECYCLING | Same relationship, smaller population |
| DELAYED_MIGRATION & CREATOR_RECYCLING | 11 | 50.0% of DELAYED_MIGRATION | 0.4% of CREATOR_RECYCLING | Same relationship, smaller population |
| RAPID_MIGRATION & MIGRATION_5_TO_15M | 0 | 0% | 0% | N/A — **already exclusive by construction** (single if/elif chain) |
| RAPID_MIGRATION & DELAYED_MIGRATION | 0 | 0% | 0% | N/A — already exclusive |
| MIGRATION_5_TO_15M & DELAYED_MIGRATION | 0 | 0% | 0% | N/A — already exclusive |
| RAPID_BIRTH_LAUNCH & anything | 0 (in this window) | 0% | — | Not evaluable in this sample — 0 launches; RAPID_BIRTH_LAUNCH is real but scoped to only 43 total historical rows across the whole system |

**Why these overlaps exist**: none of them are bugs. Each tag answers a
genuinely independent question about the launch (its birth timing, its
migration clustering, its migration speed, its creator's launch
history) — the module's own docstring is explicit that this is
deliberate ("a launch CAN legitimately exhibit more than one archetype
at once"). The overlap is real signal correlation, not double-counting
error. **The problem X65.0 addresses is not that these facts co-occur —
it's that Discovery's Behaviour Cohort UI currently treats each fact as
an independent, separately-clickable discovery path**, so the SAME
launch is discoverable through multiple cohort buttons, which is
exactly what the task says is inappropriate for attribution (though
fine, and explicitly preserved, for filtering).

## `QUICK_BIRTH_MIGRATION` overlap (the task's own named example)

`QUICK_BIRTH_MIGRATION` is not part of the `behaviours` list at all — it
is a separate boolean (`is_quick_birth_migration`) computed by a
different module (`operational_intelligence.py`) from a different
timestamp triple, but rendered as its own Behaviour Cohort card in the
Discovery UI.

| Overlap | Count | % of QUICK_BIRTH_MIGRATION (67) | % of other |
|---|---|---|---|
| QUICK_BIRTH_MIGRATION & RAPID_BIRTH_LAUNCH | 0 | 0.0% | 0.0% of RAPID_BIRTH_LAUNCH (0 in this window) |
| QUICK_BIRTH_MIGRATION & BURST_LAUNCH | 6 | 9.0% | 0.7% of BURST_LAUNCH |
| **QUICK_BIRTH_MIGRATION & RAPID_MIGRATION** | **67** | **100.0%** | 1.7% of RAPID_MIGRATION |
| QUICK_BIRTH_MIGRATION & MIGRATION_5_TO_15M | 0 | 0.0% | 0.0% |
| QUICK_BIRTH_MIGRATION & DELAYED_MIGRATION | 0 | 0.0% | 0.0% |
| QUICK_BIRTH_MIGRATION & CREATOR_RECYCLING | 17 | 25.4% | 0.7% of CREATOR_RECYCLING |

**Every single QUICK_BIRTH_MIGRATION launch is also RAPID_MIGRATION** —
this is the exact scenario the task names: "a launch classified as
Quick Birth ≤5s → Migration also appears under Rapid Migration." This
is not coincidental overlap like the pairs above — it is a **strict
subset relationship**: QUICK_BIRTH_MIGRATION's own definition
(`creator_age <= 5s AND migration_delay <= 900s`) requires
`migration_delay`, and 100% of the launches that satisfy this
additional creator-age constraint happen to also fall under
RAPID_MIGRATION's broader `<300s` migration-speed threshold in this
sample. QUICK_BIRTH_MIGRATION is the MORE SPECIFIC signal (it requires
two conditions: fast birth AND fast migration) — RAPID_MIGRATION only
requires one (fast migration alone). This directly informs the
specificity ordering in Phase 3: QUICK_BIRTH_MIGRATION must outrank
RAPID_MIGRATION in the precedence tree, exactly as the task's own
example precedence tree places "Quick Birth ≤5s → Migration" above
"Rapid Migration."

## Tag-count distribution (systemic overlap, not an edge case)

| Number of behaviours (`behaviours` list only, excluding QUICK_BIRTH_MIGRATION) | Launch count | % of population |
|---|---|---|
| 0 | 137 | 3.3% |
| 1 | 1,224 | 29.7% |
| 2 | 2,238 | 54.2% |
| 3 | 528 | 12.8% |

Only 29.7% of launches have a single unambiguous behaviour today. The
majority (67.0%) carry 2 or 3 simultaneous tags. Adding
QUICK_BIRTH_MIGRATION as an independent, separately-clickable cohort
(as the current UI does) means a launch in the "3 tags" bucket that is
also QUICK_BIRTH_MIGRATION is discoverable through as many as 4
different Behaviour Cohort entry points for the exact same underlying
launch — the precise failure mode X65.0 exists to close.

## What is NOT an overlap problem (already correctly exclusive)

The three migration-timing tags (RAPID_MIGRATION, MIGRATION_5_TO_15M,
DELAYED_MIGRATION) are already perfectly mutually exclusive by
construction (`_migration_behaviour_tag`'s if/elif chain) — confirmed
0% overlap among all three pairs. This sub-design does not need to
change; it is a template for how the redesigned single canonical
classifier should behave across ALL behaviours, not just this trio.
