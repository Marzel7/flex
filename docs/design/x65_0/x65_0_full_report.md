# X65.0 — Exclusive Behaviour Classification (Full Report)

Consolidates all 7 phases of the X65.0 redesign — Discovery's Behaviour
Cohort is now an exclusive partition (every launch belongs to exactly
one canonical behaviour), replacing the prior additive model where a
launch could be discoverable through multiple overlapping behaviour
cohorts. All figures measured live against the production database,
2026-07-21.

---

## Phase 1 — Behaviour Audit

Read-only audit of every behaviour currently produced by the Discovery
Launch Intelligence pipeline. Live counts measured against the `7d`
window (4,126 launches, `wt_attribution_outcomes` population).

### Behaviour sources — two independent modules feed the Discovery UI

1. **`src/ops/operational_behaviour_tags.py`** — `build_behaviour_classification()`,
   consumed by `/api/ops-v2/operational-intelligence` (the endpoint the
   Discovery Cohort Report calls). Produces a `behaviours` list (0-3
   tags) per launch, additively.
2. **`src/ops/operational_intelligence.py`** — `classify_quick_birth_migration()`,
   which computes a *separate* boolean, `is_quick_birth_migration`, not
   part of the `behaviours` list at all, but rendered as its own
   Behaviour Cohort card (`QUICK_BIRTH_MIGRATION`) via a special case in
   `x60MatchesBehaviour()` (`templates/discovery.html:1303-1305`).

`QUICK_BIRTH_MIGRATION` is the task's own explicit first overlap example
("Quick Birth ≤5s → Migration"), so both sources are in scope.

### Per-behaviour detail

**`RAPID_BIRTH_LAUNCH`** ("Rapid Birth→Migration")
- Detection: `birth_to_launch_seconds <= 5`, from
  `wt_watchtower_launches.create_time`/`birth_to_launch_seconds`
  (on-chain-sourced).
- SQL: `SELECT mint, birth_to_launch_seconds FROM wt_watchtower_launches
  WHERE create_time IS NOT NULL AND birth_to_launch_seconds IS NOT NULL`
  (`behaviour_queue.py:118-121`).
- Classifier: `rapid_birth_launch_lookup()`.
- Priority (as coded): first tag checked, but every later check still
  runs and appends independently — "checked first" ≠ "wins exclusively."
- Overlaps: BURST_LAUNCH, CREATOR_RECYCLING, any migration-timing tag,
  QUICK_BIRTH_MIGRATION.
- Current count (7d): **0** — `wt_watchtower_launches` is scoped to only
  43 rows system-wide ("live-cascade-scoped"), none in this sample.

**`BURST_LAUNCH`** ("Burst Launcher")
- Detection: `cluster_size >= 3` (migrations within ±60s of this
  launch's own `migrated_at`).
- SQL: `SELECT mint, CAST(migrated_at AS REAL) AS m FROM token_analysis
  WHERE migrated_at IS NOT NULL AND CAST(migrated_at AS REAL) >= ?
  ORDER BY m` (`behaviour_queue.py:151-156`), sliding-window neighbour
  count via `bisect`.
- Classifier: `burst_launch_lookup()`.
- Overlaps: heavily with RAPID_MIGRATION (93.6% of BURST_LAUNCH) and
  CREATOR_RECYCLING (59.0%) — expected, since fast-migrating/recycling
  creators cluster together.
- Current count (7d): **918** (22.2%).

**`RAPID_MIGRATION` / `MIGRATION_5_TO_15M` / `DELAYED_MIGRATION`** (X43.0 migration-timing tags)
- Detection: buckets `token_analysis.migrated_at - created_at` into
  `<300s` / `300-899s` / `>=900s`.
- Classifier: `_migration_behaviour_tag()`, an if/elif chain —
  **already mutually exclusive by construction** among these three
  (confirmed 0% pairwise overlap). This sub-design already does what
  X65.0 asks, just not across the *other* tags.
- Overlaps with others: RAPID_MIGRATION overlaps with BURST_LAUNCH
  (93.6% of BURST_LAUNCH) and CREATOR_RECYCLING (96.6% of
  CREATOR_RECYCLING).
- Current counts (7d): RAPID_MIGRATION **3,859** (93.5%),
  MIGRATION_5_TO_15M **22** (0.5%), DELAYED_MIGRATION **22** (0.5%).

**`CREATOR_RECYCLING`** ("Creator Recycling")
- Detection: creator wallet appears on >1 distinct mint in the
  population. Exact wallet-address reuse only.
- Classifier: inline in `build_behaviour_classification`'s loop.
- Overlaps: single largest overlap source — 96.6% of RAPID_MIGRATION
  and 59.0% of BURST_LAUNCH launches are also CREATOR_RECYCLING.
- Current count (7d): **2,462** (59.7%).

**`REPEAT_CREATOR`** (defined, never populated)
- The constant/label exist but the classify loop never appends it —
  confirmed dead code, consistent with the existing test
  `test_repeat_creator_moved_out_of_behaviour_stage`. A same-named
  constant in `src/ops/creator_identity.py` is an unrelated coincidence.
  Out of scope for X65.0 (produces zero launches); flagged for a future
  cleanup task, not addressed here.
- Current count: **0**.

**`QUICK_BIRTH_MIGRATION`** ("Quick Birth ≤5s → Migration") — separate from `behaviours`
- Detection: `classify_quick_birth_migration()` —
  `creator_age_at_create_seconds <= 5` AND `create_to_migration_seconds
  <= 900`, from a distinct timestamp triple than the migration-timing
  tags.
- Not part of the `behaviours` priority chain at all — a standalone
  flag surfaced as its own UI cohort card via a special case.
- Overlaps: by definition likely to overlap RAPID_MIGRATION and/or
  RAPID_BIRTH_LAUNCH — the task's own named example.

### Summary table

| Behaviour | Source module | Count (7d) | Coverage | Exclusive w/ siblings? |
|---|---|---|---|---|
| RAPID_BIRTH_LAUNCH | behaviour_queue.py | 0 | 0% | No |
| BURST_LAUNCH | behaviour_queue.py | 918 | 22.2% | No |
| RAPID_MIGRATION | operational_behaviour_tags.py | 3,859 | 93.5% | Yes, vs. other 2 migration-timing tags only |
| MIGRATION_5_TO_15M | operational_behaviour_tags.py | 22 | 0.5% | Yes, same |
| DELAYED_MIGRATION | operational_behaviour_tags.py | 22 | 0.5% | Yes, same |
| CREATOR_RECYCLING | operational_behaviour_tags.py | 2,462 | 59.7% | No |
| REPEAT_CREATOR | operational_behaviour_tags.py | 0 (dead) | 0% | N/A |
| QUICK_BIRTH_MIGRATION | operational_intelligence.py | not yet measured | — | No |

### Tag-count distribution (evidence the overlap is systemic)

| Number of behaviours on a launch | Launch count |
|---|---|
| 0 | 137 |
| 1 | 1,224 |
| 2 | 2,238 |
| 3 | 528 |

**Only 29.7% of launches (1,224 of 4,126) have exactly one behaviour tag
today.** The majority (67.0%) carry 2 or 3 tags simultaneously.

---

## Phase 2 — Overlap Analysis

### Pairwise overlaps within `behaviours`

| Pair | Overlap | % of first | % of second | Intentional? |
|---|---|---|---|---|
| BURST_LAUNCH & RAPID_MIGRATION | 859 | 93.6% of BURST (918) | 22.3% of RAPID_MIGRATION (3,860) | Yes — clustering vs. speed, correlated but independent facts |
| BURST_LAUNCH & MIGRATION_5_TO_15M | 11 | 1.2% | 50.0% of MIGRATION_5_TO_15M (22) | Same, smaller population |
| BURST_LAUNCH & DELAYED_MIGRATION | 5 | 0.5% | 22.7% of DELAYED (22) | Same |
| BURST_LAUNCH & CREATOR_RECYCLING | 542 | 59.0% | 22.0% of CREATOR_RECYCLING (2,462) | Yes — serial deployers also cluster |
| RAPID_MIGRATION & CREATOR_RECYCLING | 2,378 | 61.6% | 96.6% | Yes — the single largest overlap in the system |
| MIGRATION_5_TO_15M & CREATOR_RECYCLING | 16 | 72.7% | 0.6% | Same relationship, smaller population |
| DELAYED_MIGRATION & CREATOR_RECYCLING | 11 | 50.0% | 0.4% | Same relationship |
| RAPID_MIGRATION & MIGRATION_5_TO_15M | 0 | 0% | 0% | Already exclusive by construction |
| RAPID_MIGRATION & DELAYED_MIGRATION | 0 | 0% | 0% | Already exclusive |
| MIGRATION_5_TO_15M & DELAYED_MIGRATION | 0 | 0% | 0% | Already exclusive |
| RAPID_BIRTH_LAUNCH & anything | 0 (this window) | — | — | Not evaluable — 0 launches this sample |

None of these overlaps are bugs — each tag answers a genuinely
independent question, and the module's own docstring says as much
("a launch CAN legitimately exhibit more than one archetype at once").
**The problem X65.0 addresses is not that these facts co-occur — it's
that the Behaviour Cohort UI treats each fact as an independent,
separately-clickable discovery path**, so the same launch is
discoverable through multiple cohort buttons.

### `QUICK_BIRTH_MIGRATION` overlap (the task's own named example)

| Overlap | Count | % of QUICK_BIRTH_MIGRATION (67) | % of other |
|---|---|---|---|
| & RAPID_BIRTH_LAUNCH | 0 | 0.0% | 0.0% (0 in window) |
| & BURST_LAUNCH | 6 | 9.0% | 0.7% of BURST_LAUNCH |
| **& RAPID_MIGRATION** | **67** | **100.0%** | 1.7% of RAPID_MIGRATION |
| & MIGRATION_5_TO_15M | 0 | 0.0% | 0.0% |
| & DELAYED_MIGRATION | 0 | 0.0% | 0.0% |
| & CREATOR_RECYCLING | 17 | 25.4% | 0.7% of CREATOR_RECYCLING |

**Every single QUICK_BIRTH_MIGRATION launch is also RAPID_MIGRATION** —
exactly the task's named scenario. This is a **strict subset
relationship**, not coincidental overlap: QUICK_BIRTH_MIGRATION requires
two conditions (fast birth AND fast migration) where RAPID_MIGRATION
requires only one (fast migration alone) — QUICK_BIRTH_MIGRATION must
therefore outrank RAPID_MIGRATION in the precedence tree.

### What is NOT an overlap problem

The three migration-timing tags are already perfectly mutually
exclusive by construction (0% overlap confirmed among all pairs) — this
sub-design is the template for how the redesigned single canonical
classifier should behave across all behaviours.

---

## Phase 3 — Specificity Ordering

Precedence is derived from **how many independent conditions a rule
requires** and how narrow/data-scarce its evidence source is — **not**
popularity. RAPID_MIGRATION (93.5% coverage, the most "popular" tag) is
actually the *least* specific rule — one broad condition matching
nearly the entire population. Popularity and specificity are inversely
related here.

### Specificity scoring

| Behaviour | Conditions required | Evidence scarcity | Rationale |
|---|---|---|---|
| RAPID_BIRTH_LAUNCH | 1 (`birth_to_launch_seconds <= 5`) | Scarcest — only 43 rows system-wide, on-chain | Most specific by evidence scarcity |
| QUICK_BIRTH_MIGRATION | 2 (`creator_age <=5s` AND `migration_delay <=900s`) | Narrow — 67/4,128 (1.6%) | Two conditions; strict subset of RAPID_MIGRATION — must outrank it |
| BURST_LAUNCH | 1, but relational (`cluster_size >=3`, depends on other launches) | Narrow — 918/4,126 (22.2%) | A population pattern beats a single-launch threshold |
| CREATOR_RECYCLING | 1, cross-referencing whole population | Broad — 2,462/4,126 (59.7%) | Weaker filter than BURST_LAUNCH's 3-within-60s |
| RAPID_MIGRATION | 1 (`migration_delay <300s`) | Broadest — 3,859/4,126 (93.5%) | Least specific timing rule |
| MIGRATION_5_TO_15M | 1 (`300-899s`) | Residual bucket, 22/4,126 (0.5%) | Same tier as the other timing buckets |
| DELAYED_MIGRATION | 1 (`>=900s`) | Residual bucket | Same tier |

### Canonical precedence tree

```
1. RAPID_BIRTH_LAUNCH
   (scarcest evidence source; on-chain-verified; highest-trust when present)
        │
        ▼
2. QUICK_BIRTH_MIGRATION
   (two independent conditions; strict subset of RAPID_MIGRATION)
        │
        ▼
3. BURST_LAUNCH
   (relational, population-level pattern)
        │
        ▼
4. CREATOR_RECYCLING
   (population-level fact, weaker filter than BURST_LAUNCH)
        │
        ▼
5. RAPID_MIGRATION / MIGRATION_5_TO_15M / DELAYED_MIGRATION
   (already mutually exclusive trio; broadest single-condition facts)
        │
        ▼
6. UNKNOWN_BEHAVIOUR
   (no rule's required evidence was available or satisfied)
```

### Why this ordering, not the task's own illustrative example

The task's example places migration-timing tags and Burst Launcher
higher, and Creator Recycling lower. This audit's measured evidence
supports CREATOR_RECYCLING ranking *above* the migration-timing trio
(not below it), and BURST_LAUNCH above CREATOR_RECYCLING, because:
1. The migration-timing tags are the *least* specific rules measured
   (RAPID_MIGRATION alone matches 93.5% of the population) — ranking
   them first would mean the least-specific rule wins, backwards from
   "most specific matching rule wins."
2. CREATOR_RECYCLING and BURST_LAUNCH both require reasoning about the
   launch in a broader population context — a more specific class of
   evidence than a single launch's own two timestamps.

The task's example was explicitly labeled "derive from actual rules" —
this ordering follows that instruction using measured specificity
rather than reproducing the illustrative tree verbatim.

### Boundary rules

- A rule whose required evidence is unavailable is treated as "did not
  match" (per every existing classifier's governing principle: absence
  of a match is not evidence of absence) — the chain falls through.
- Each tier is checked in strict order; the first match wins.

---

## Phase 4 — Exclusive Behaviour Assignment (Implementation)

### `src/ops/operational_behaviour_tags.py`

Added `QUICK_BIRTH_MIGRATION`, `UNKNOWN_BEHAVIOUR` constants and
`CANONICAL_BEHAVIOUR_ORDER` (the precedence tuple from Phase 3). Added
`canonical_behaviour_for(behaviours, *, is_quick_birth_migration=False)`
— a pure function that folds `is_quick_birth_migration` into the tag
set, then returns the first `CANONICAL_BEHAVIOUR_ORDER` entry present,
defaulting to `UNKNOWN_BEHAVIOUR`. Does **not** mutate the input list or
replace the existing additive `behaviours`/`behaviour_summary` fields,
which remain fully intact for filtering/cross-dimensional-query use
(`oi_query()`'s explicit "no hierarchy should prevent cross-dimensional
searching" requirement).

### `src/ops/operational_intelligence.py`

In `build_operational_intelligence()`, after `is_quick_birth_migration`
becomes available on each record, computes
`r["canonical_behaviour"] = canonical_behaviour_for(...)` for every
record. Added `canonical_behaviour_summary` (mutually-exclusive counts,
parallel to the existing additive `behaviour_summary`) and
`canonical_behaviour_conserved` (a persistent, checkable invariant:
`sum(counts) == total_launches`) to the response.

Added a new `canonical_behaviour` kwarg to `query()` (`oi_query`),
independent of the existing additive `behaviour` kwarg — filters on the
single exclusive value rather than list membership.

### `src/core/operation_dashboard_routes.py`

The API's `behaviour=` request parameter (what the Discovery UI's
cohort cards send when clicked) now routes to
`oi_query(canonical_behaviour=...)` instead of the old
`behaviour=`/`quick_birth_migration=True` special-case combination —
`canonical_behaviour_for()` already folds QUICK_BIRTH_MIGRATION into the
same exclusive value ahead of RAPID_MIGRATION, so the special case is
no longer needed.

### `templates/discovery.html`

- `x60MatchesBehaviour()`: changed from additive-list membership
  (`(row.behaviours||[]).indexOf(value)>=0`) to exclusive equality
  (`row.canonical_behaviour===value`).
- `renderBehaviourCohorts()` (the "1. Behaviour Cohort" section — the
  entry point in the Discovery Cohort Report): now reads
  `canonical_behaviour_summary` instead of reconstructing additive
  per-tag counts.
- **Left unchanged, by design**: `renderObservedPatterns()` (a separate
  section, explicitly labeled "Behaviour tags are additive; selecting a
  card narrows the cohort") still reads the additive `behaviours` list
  — this is the filtering use case the task explicitly preserves.

---

## Phase 5 — Pipeline Validation

Verifies Creator Identity, Topology, Funding Origin, Operation
Attribution, and Launch Results all continue to operate exactly as
before, with **zero logic changes** to any of those stages.

| Stage | Changed? | Detail |
|---|---|---|
| Behaviour Cohort | **Yes** | New `canonical_behaviour`/`canonical_behaviour_summary`; existing additive fields untouched |
| Creator Identity | No | Unmodified; receives a different (exclusive) upstream population |
| Topology | No | Unmodified |
| Funding Origin | No | `x60MatchesFunding()` unmodified |
| Operation Attribution | No | `operation_id`/`is_watchtower` unmodified |
| Launch Results | No | `x60OperationRows()`/`renderX60LaunchResults()` unmodified |
| Mechanism classification | No | Unmodified |

The UI's filter chain (`x60BehaviourRows()` → `x60CreatorIdentityRows()`
→ `x60TopologyRows()` → `x60FundingRows()` → `x60OperationRows()`) has
every downstream function body completely unmodified — only the match
predicate at the top of the chain changed, so exclusivity propagates
automatically with zero downstream code changes.

### Live verification: `oi_query()` matches manual filtering exactly

| Behaviour | `oi_query()` count | Manual filter count | Match |
|---|---|---|---|
| BURST_LAUNCH | 915 | 915 | ✅ |
| CREATOR_RECYCLING | 1,908 | 1,908 | ✅ |
| RAPID_MIGRATION | 1,095 | 1,095 | ✅ |
| QUICK_BIRTH_MIGRATION | 67 | 67 | ✅ |

### Live verification: downstream stages conserve population exactly

Selected `CREATOR_RECYCLING` (1,908 launches):
- Creator Identity distribution: REPEAT_CREATOR 1,740,
  UNKNOWN_CREATOR_IDENTITY 151, RETURNING_CREATOR 12,
  DORMANT_REACTIVATED 5 — **sums to 1,908**.
- Topology distribution: UNKNOWN 1,304, LINEAR 317, FAN_OUT 244,
  MULTI_LEVEL_FAN_OUT 43 — **sums to 1,908**.

Both distributions summing exactly to the cohort size confirms no
launch was silently dropped or double-counted passing from the new
exclusive Behaviour Cohort into the unmodified downstream stages.
Funding Origin and Operation Attribution were not independently
re-derived (their modules have zero line changes, confirmed by
inspection); transitive correctness follows from Creator
Identity/Topology's direct verification plus the identical
"filter-whatever-the-previous-stage-returned" pattern every later stage
uses.

---

## Phase 6 — UI Consistency

Live-tested against the running `watchtower_api` process after restart.

### Behaviour totals no longer overlap

| Cohort | Mints (24h) | All returned launches have this exact `canonical_behaviour`? |
|---|---|---|
| BURST_LAUNCH | 201 | ✅ 100% — only one distinct value present |
| QUICK_BIRTH_MIGRATION | 9 | ✅ |
| RAPID_MIGRATION | 223 | ✅ |

**QUICK_BIRTH_MIGRATION and RAPID_MIGRATION mint sets are disjoint** —
confirmed by direct set-intersection (`len(qbm_mints & rm_mints) == 0`).
Before X65.0, all 9 QUICK_BIRTH_MIGRATION launches also appeared under
RAPID_MIGRATION (100% overlap, per Phase 2). This is now zero.

### A WATCHTOWER launch appears under exactly one behaviour

Not independently spot-checked with a live WATCHTOWER sample in this
pass, but follows directly from `canonical_behaviour_for()`'s return
type (always exactly one string, no WATCHTOWER-specific carve-out),
verified exhaustively by the property test
`test_every_combination_yields_exactly_one_behaviour`.

### Counts reconcile cleanly

| Window | total_launches | Sum of canonical_behaviour_summary | Match |
|---|---|---|---|
| 24h | 687 | 687 | ✅ |
| 7d | 4,132 | 4,132 | ✅ |

`canonical_behaviour_conserved` is `True` in both cases — a persistent,
server-computed, checkable invariant on every request, not just an
eyeballed one-off measurement.

### Breadcrumbs represent a unique discovery path

The breadcrumb-rendering code (`templates/discovery.html:1461`) reads
`TOPO_SELECTION.behaviour`, set to whichever single cohort card was
clicked — since cohort cards now each correspond to exactly one
`canonical_behaviour` value, the breadcrumb trail is, by construction,
always exactly one behaviour value per launch's discovery path.

### Not changed, confirmed intentionally

"Observed Patterns" (additive, unchanged, explicitly filtering-oriented)
and every downstream stage (Creator Identity through Launch Results) —
zero code changes, per Phase 5.

---

## Phase 7 — Regression Testing

### Test suite results

46 pre-existing tests (`test_x27_4_behaviour_queue.py`,
`test_x64_8_creator_identity.py`,
`test_x26_11_unified_terminal_infrastructure_outcomes.py`) plus 11 new
tests (`test_x65_0_exclusive_behaviour.py`) — **all 57 pass**, zero
regressions.

### No launches lost / no duplicates / totals preserved

Live-measured (`7d`): `total_launches: 4,133`, `conserved: True`,
`canonical_behaviour_conserved: True`. Every mint appears exactly once
in `records` (structurally guaranteed — a Python dict keyed by mint).
`oi_query(canonical_behaviour='BURST_LAUNCH')` and a manual Python-side
filter produce byte-identical mint sets (915 mints).

| Window | total_launches | Sum of canonical_behaviour_summary | Match |
|---|---|---|---|
| 24h | 687 | 687 | ✅ |
| 7d | 4,132-4,133 (natural drift between successive live measurements against the actively-written DB) | matches exactly each time | ✅ |

### Each launch assigned exactly one behaviour

Proven two ways: (1) the exhaustive property test over every subset of
the 6 known tags × both quick-birth-migration states — always exactly
one string returned, never a list, never `None`; (2) live data — zero
`records` entries with missing/malformed `canonical_behaviour`.

### WATCHTOWER detections and operation attribution unchanged

`operation_summary.watchtower` and `quick_birth_migration_summary.
watchtower_overlap_count` both read `3` post-deployment — computed by
functions **not touched** by X65.0. `operation_id` assignment is not
read, written, or referenced anywhere in the new code.

### Explicit non-goals confirmed untouched

Zero lines changed in: Creator Identity classification
(`src/ops/creator_identity.py`, unrelated to X65.0 itself), Topology
classification (`src/ops/funding_topology.py`), Funding Origin logic
(`x60MatchesFunding()`), Operation Attribution logic, Launch Result
logic (`x60OperationRows()`/`renderX60LaunchResults()`).

### Success criteria — final status

| Criterion | Status |
|---|---|
| Every launch belongs to exactly one Behaviour Cohort | ✅ verified live and by property test |
| Behaviour cohorts are mutually exclusive | ✅ QUICK_BIRTH_MIGRATION ∩ RAPID_MIGRATION = ∅ (was 100% overlap) |
| Discovery paths are unique | ✅ verified via breadcrumb/match-predicate analysis |
| No launch discoverable through multiple cohorts solely because broader behaviours include narrower ones | ✅ QUICK_BIRTH_MIGRATION/RAPID_MIGRATION subset resolved by precedence |
| All downstream classification stages unchanged except receiving a unique canonical behaviour | ✅ 0 lines changed in Creator Identity, Topology, Funding Origin, Operation Attribution, Launch Results |

---

## Provenance note

This report consolidates the 7 original per-phase documents
(`x65_0_behaviour_audit.md` through `x65_0_regression.md`) into one
file. All measurements were taken live against the production database
and the running `watchtower_api` process; the implementation is
deployed (process restarted, pid confirmed) and all 57 relevant tests
pass. The per-phase files remain available individually if needed.
