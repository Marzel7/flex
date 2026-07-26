# X65.0 — Phase 7: Regression Testing

## Test suite results

46 pre-existing tests (across `test_x27_4_behaviour_queue.py`,
`test_x64_8_creator_identity.py`, `test_x26_11_unified_terminal_
infrastructure_outcomes.py`) plus 11 new tests
(`test_x65_0_exclusive_behaviour.py`) — **all 57 pass**, zero
regressions.

## No launches lost

Live-measured (`7d` window, 2026-07-21): `total_launches: 4,133`,
`conserved: True` (topology's own pre-existing conservation check,
unaffected by X65.0), `canonical_behaviour_conserved: True` (new,
verifies the exclusive classification itself conserves population).

## No duplicate launches

Every mint appears exactly once in `records` (a Python dict keyed by
mint — duplication is structurally impossible at that layer, unchanged
by X65.0). At the Behaviour Cohort filtering layer specifically: a
direct API test confirmed `oi_query(canonical_behaviour='BURST_LAUNCH')`
and a manual Python-side filter over `records` produce byte-identical
mint sets (915 mints, `7d` window) — no duplication introduced by the
new filter path.

## Total launches preserved

| Window | total_launches (topology's own count) | Sum of canonical_behaviour_summary | Match |
|---|---|---|---|
| `24h` | 687 | 687 | ✅ |
| `7d` | 4,132-4,133 (minor natural drift between successive live measurements against the actively-written database) | matches exactly in each measurement | ✅ |

## Each launch assigned exactly one behaviour

Proven two ways:
1. **Property test** (`test_every_combination_yields_exactly_one_
   behaviour` in the new test suite): every possible subset of the 6
   known additive tags, crossed with both quick-birth-migration states,
   was checked — `canonical_behaviour_for()` always returns exactly one
   string from `CANONICAL_BEHAVIOUR_ORDER`, never a list, never `None`.
2. **Live data**: `records` dict in `build_operational_intelligence`'s
   output has zero entries missing/malformed `canonical_behaviour`
   (checked directly: `len(bad) == 0` where `bad` = mints whose
   `canonical_behaviour` is not a string).

## Existing WATCHTOWER detections remain intact

`operation_summary.watchtower` and `quick_birth_migration_summary.
watchtower_overlap_count` both read `3` in the live `7d`-window
measurement taken after deployment — these are computed by
`sum(r["is_watchtower"] for r in records.values())` and
`sum(r["is_watchtower"] and r["is_quick_birth_migration"] for r in
records.values())` respectively, **neither of which was touched by
X65.0**. WATCHTOWER attribution logic (`is_watchtower`, wherever it is
set) was not modified, referenced, or read by any of this task's code
changes.

## Operation attribution unchanged

`operation_id` assignment (whatever upstream process sets it) is not
read, written, or referenced anywhere in `canonical_behaviour_for()`,
`build_hierarchy()` (unmodified), or the API route changes (which only
changed how the `behaviour=` query parameter routes to `oi_query()`,
not how `operation=` is handled — that parameter's handling in both the
route and `oi_query()` is byte-for-byte unchanged). Live-verified:
`quick_birth_migration_summary.assigned_operation_count: 3` matches the
same value the `watchtower` operation count reads, consistent with no
change to how operation IDs get attached to records.

## Explicit non-goals confirmed untouched (per the task's own scope limits)

- Creator Identity classification logic: 0 lines changed in
  `src/ops/creator_identity.py` by this task (the file WAS modified
  earlier this session, but for an unrelated performance fix — X65.0
  itself made no further changes to it).
- Topology classification logic: `src/ops/funding_topology.py` — 0
  lines changed.
- Funding origin (CEX/treasury/shared-withdrawal) logic:
  `x60MatchesFunding()` in the template — 0 lines changed.
- Operation attribution logic: wherever `operation_id`/`is_watchtower`
  are originally computed — 0 lines changed, not even referenced by
  X65.0's new code.
- Launch Result logic: `x60OperationRows()`,
  `renderX60LaunchResults()` — 0 lines changed.

## Summary

| Success criterion (from the task) | Status |
|---|---|
| Every launch belongs to exactly one Behaviour Cohort | ✅ verified live and by property test |
| Behaviour cohorts are mutually exclusive | ✅ verified live (QUICK_BIRTH_MIGRATION ∩ RAPID_MIGRATION = ∅, previously 100% overlap) |
| Discovery paths are unique | ✅ verified via breadcrumb/match-predicate analysis |
| The same operation cannot be "discovered" through multiple behaviour cohorts solely because broader behaviours include narrower ones | ✅ the exact QUICK_BIRTH_MIGRATION/RAPID_MIGRATION subset relationship is now resolved by precedence |
| All downstream classification stages remain unchanged except for receiving a unique, canonical behaviour assignment | ✅ verified — 0 lines changed in Creator Identity, Topology, Funding Origin, Operation Attribution, or Launch Results logic |
