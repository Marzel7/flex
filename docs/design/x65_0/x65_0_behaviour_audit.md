# X65.0 — Phase 1: Behaviour Audit

Read-only audit of every behaviour currently produced by the Discovery
Launch Intelligence pipeline. Live counts measured 2026-07-21 against
the `7d` window (4,126 launches, `wt_attribution_outcomes` population).

## Behaviour sources — two independent modules feed the Discovery UI

1. **`src/ops/operational_behaviour_tags.py`** — `build_behaviour_classification()`,
   the function actually consumed by `/api/ops-v2/operational-intelligence`
   (the endpoint the Discovery Cohort Report calls). Produces a `behaviours`
   list (0-3 tags) per launch, additively.
2. **`src/ops/operational_intelligence.py`** — `classify_quick_birth_migration()`,
   which computes a *separate* boolean, `is_quick_birth_migration`, not
   part of the `behaviours` list at all, but rendered as its own Behaviour
   Cohort card in the UI (`QUICK_BIRTH_MIGRATION`) via a special case in
   `x60MatchesBehaviour()` (`templates/discovery.html:1303-1305`).

Both sources are in scope for this redesign; `QUICK_BIRTH_MIGRATION` is the
task's own explicit first overlap example ("Quick Birth ≤5s → Migration").

## Per-behaviour detail

### `RAPID_BIRTH_LAUNCH` ("Rapid Birth→Migration")

- **Detection logic**: `birth_to_launch_seconds <= 5`, using
  `wt_watchtower_launches.create_time`/`birth_to_launch_seconds`
  (on-chain-sourced, per `src/ops/behaviour_queue.py`'s trust-basis
  docstring).
- **SQL**: `SELECT mint, birth_to_launch_seconds FROM
  wt_watchtower_launches WHERE create_time IS NOT NULL AND
  birth_to_launch_seconds IS NOT NULL` (`behaviour_queue.py:118-121`).
- **Classifier**: `rapid_birth_launch_lookup()`.
- **Priority (as currently coded)**: first tag checked in
  `build_behaviour_classification`'s per-mint loop
  (`operational_behaviour_tags.py:198-200`) — but "priority" here means
  only "checked first," not "wins exclusively," since every subsequent
  check still runs and appends independently.
- **Overlapping conditions**: can co-occur with `BURST_LAUNCH`,
  `CREATOR_RECYCLING`, and any migration-timing tag; can also overlap
  with `QUICK_BIRTH_MIGRATION` (different threshold basis — see Phase 2).
- **Current launch count (7d window)**: **0** — `wt_watchtower_launches`
  is scoped to only 43 rows total across the system's whole history
  (per this module's own docstring, "live-cascade-scoped"), none of
  which fall inside this particular 7-day sample. Real in other windows.

### `BURST_LAUNCH` ("Burst Launcher")

- **Detection logic**: `cluster_size >= 3`, where cluster_size counts
  migrations within ±60s of this launch's own `migrated_at`
  (`token_analysis.migrated_at`, confirmed reliable per X27.3.2's
  timing-integrity audit).
- **SQL**: `SELECT mint, CAST(migrated_at AS REAL) AS m FROM
  token_analysis WHERE migrated_at IS NOT NULL AND CAST(migrated_at AS
  REAL) >= ? ORDER BY m` (`behaviour_queue.py:151-156`), then a
  sliding-window neighbour count via `bisect`.
- **Classifier**: `burst_launch_lookup()`.
- **Priority (as currently coded)**: second tag checked.
- **Overlapping conditions**: heavily overlaps with `RAPID_MIGRATION`
  (93.6% of BURST_LAUNCH launches are also RAPID_MIGRATION — see Phase
  2) and `CREATOR_RECYCLING` (59.0%). This overlap is structurally
  expected: a burst of co-migrating launches and a fast individual
  migration are correlated but independent facts (a burst can happen
  slowly if all members happen to migrate around the same absolute
  time; a single fast migration says nothing about clustering).
- **Current launch count (7d window)**: **918** (22.2% coverage).

### `RAPID_MIGRATION` / `MIGRATION_5_TO_15M` / `DELAYED_MIGRATION` (X43.0 migration-timing tags)

- **Detection logic**: buckets `token_analysis.migrated_at -
  token_analysis.created_at` into three fixed, non-overlapping
  thresholds: `<300s` (RAPID_MIGRATION), `300-899s`
  (MIGRATION_5_TO_15M), `>=900s` (DELAYED_MIGRATION).
- **SQL**: reads `created_at`/`migrated_at` from `token_analysis` for
  every mint in the window (`operational_behaviour_tags.py:163-183`);
  `created_at` is parsed via `_parse_token_analysis_timestamp` (mixed
  ISO-8601/epoch-text format, defensively parsed, excluded if neither
  parses).
- **Classifier**: `_migration_behaviour_tag()`, an if/elif chain —
  **already mutually exclusive by construction** among these three tags
  specifically (confirmed live: 0 pairwise overlap between
  RAPID_MIGRATION/MIGRATION_5_TO_15M/DELAYED_MIGRATION in the
  measurement below). This part of the system already does exactly what
  X65.0 asks for, just not across the *other* behaviour tags.
- **Priority (as currently coded)**: this is the ONE already-exclusive
  sub-group; it's appended as the last tag in the per-mint loop, but
  since only one of the three is ever produced per launch, "priority"
  within this trio is moot.
- **Overlapping conditions with OTHER tags**: RAPID_MIGRATION overlaps
  with BURST_LAUNCH (93.6% of BURST_LAUNCH's population) and
  CREATOR_RECYCLING (96.6% of CREATOR_RECYCLING's population) — see
  Phase 2.
- **Current launch counts (7d window)**: RAPID_MIGRATION **3,859**
  (93.5%), MIGRATION_5_TO_15M **22** (0.5%), DELAYED_MIGRATION **22**
  (0.5%).

### `CREATOR_RECYCLING` ("Creator Recycling")

- **Detection logic**: the launch's creator (`pf_ws_creator` or
  `earliest_tx_creator`, same resolution as elsewhere) appears on more
  than 1 distinct mint within the current population. Exact
  wallet-address reuse only, no clustering or inference.
- **SQL**: no separate SQL — reuses the `creator_of` map already built
  for the (now-dead) `REPEAT_CREATOR` tag, then counts mints per
  creator in Python (`operational_behaviour_tags.py:185-192`).
- **Classifier**: inline in `build_behaviour_classification`'s loop.
- **Priority (as currently coded)**: third tag checked, appended
  independently of everything else.
- **Overlapping conditions**: the single largest overlap source in the
  system — 96.6% of RAPID_MIGRATION launches are ALSO CREATOR_RECYCLING,
  and 59.0% of BURST_LAUNCH launches are ALSO CREATOR_RECYCLING. This is
  expected given the population: creators launching many tokens quickly
  will naturally also migrate quickly and often cluster with each
  other's launches.
- **Current launch count (7d window)**: **2,462** (59.7%).

### `REPEAT_CREATOR` (defined, never populated)

- **Detection logic**: none — the constant and its label exist in
  `BEHAVIOUR_LABELS`/`BEHAVIOUR_ORDER`, but the classify loop
  (`operational_behaviour_tags.py:196-217`) never calls
  `tags.append(REPEAT_CREATOR)`. This is confirmed dead code in this
  module, consistent with the module's own docstring note and the
  existing test `test_repeat_creator_moved_out_of_behaviour_stage`
  (`tests/test_x64_8_creator_identity.py`), which asserts this constant
  does NOT appear in the behaviour-rendering section of the template.
- **Note**: a same-named `REPEAT_CREATOR` constant is separately defined
  in `src/ops/creator_identity.py` for the (unrelated) Creator Identity
  dimension — a naming coincidence, not shared logic. This audit treats
  `operational_behaviour_tags.py`'s `REPEAT_CREATOR` as out of scope for
  X65.0 (it produces zero launches, so it cannot contribute to the
  overlap problem), but flags it for a future cleanup task (removing
  dead code is outside X65.0's stated scope: "This task is limited to
  behaviour classification" concerns exclusivity, not dead-code removal).
- **Current launch count (7d window)**: **0** (never assigned).

### `QUICK_BIRTH_MIGRATION` ("Quick Birth ≤5s → Migration") — separate from `behaviours`, rendered as its own cohort

- **Detection logic**: `classify_quick_birth_migration()` in
  `src/ops/operational_intelligence.py` — `creator_age_at_create_seconds
  <= 5` AND `create_to_migration_seconds <= 900`, both computed from
  `creator_birth_at`/`create_at`/`migration_at` (a distinct timestamp
  triple from the migration-timing tags above, which use
  `token_analysis.created_at`/`migrated_at` directly).
- **SQL**: not a single query — assembled per-record inside
  `_enrich_discovery_records()` from multiple evidence sources, then
  passed through `classify_quick_birth_migration()`.
- **Classifier**: `classify_quick_birth_migration()`
  (`operational_intelligence.py:72-112`).
- **Priority (as currently coded)**: none — this is not part of the
  `behaviours` list's priority chain at all. It is a wholly separate
  boolean flag (`is_quick_birth_migration`) surfaced as an independent
  UI cohort card, filtered via a special case in
  `x60MatchesBehaviour()` rather than the generic `behaviours.indexOf()`
  check every other tag uses.
- **Overlapping conditions**: by definition, any launch matching
  QUICK_BIRTH_MIGRATION (creator_age ≤5s, migration ≤900s) is very
  likely to also match RAPID_MIGRATION (migration <300s, a subset of
  ≤900s) and/or RAPID_BIRTH_LAUNCH (a similar but NOT identical ≤5s
  threshold on a different timestamp pair — birth_to_launch_seconds
  from on-chain create_time vs. creator_age_at_create_seconds from a
  birth/create timestamp pair). This is the task's own named example
  overlap.
- **Current launch count**: not measured in this pass (requires the
  full `_enrich_discovery_records` pipeline, deferred to Phase 2's
  overlap report where it is measured directly).

## Summary table

| Behaviour | Source module | Current count (7d) | Coverage | Mutually exclusive with siblings? |
|---|---|---|---|---|
| RAPID_BIRTH_LAUNCH | behaviour_queue.py | 0 | 0% | No — additive with all others |
| BURST_LAUNCH | behaviour_queue.py | 918 | 22.2% | No — additive with all others |
| RAPID_MIGRATION | operational_behaviour_tags.py | 3,859 | 93.5% | Yes, vs. the other 2 migration-timing tags only |
| MIGRATION_5_TO_15M | operational_behaviour_tags.py | 22 | 0.5% | Yes, vs. the other 2 migration-timing tags only |
| DELAYED_MIGRATION | operational_behaviour_tags.py | 22 | 0.5% | Yes, vs. the other 2 migration-timing tags only |
| CREATOR_RECYCLING | operational_behaviour_tags.py | 2,462 | 59.7% | No — additive with all others |
| REPEAT_CREATOR | operational_behaviour_tags.py | 0 (dead) | 0% | N/A (never produced) |
| QUICK_BIRTH_MIGRATION | operational_intelligence.py | not yet measured | — | No — entirely separate from `behaviours`, own overlap surface |

## Tag-count distribution (evidence the overlap is systemic, not rare)

Measured live, 4,126 launches in the `7d` window:

| Number of behaviours on a launch | Launch count |
|---|---|
| 0 | 137 |
| 1 | 1,224 |
| 2 | 2,238 |
| 3 | 528 |

**Only 29.7% of launches (1,224 of 4,126) have exactly one behaviour
tag today.** The majority (2,766 of 4,126, 67.0%) carry 2 or 3 tags
simultaneously — confirming this is the dominant case, not an edge case.
