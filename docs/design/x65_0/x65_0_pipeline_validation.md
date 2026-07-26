# X65.0 — Phase 5: Pipeline Validation

Verifies that Creator Identity, Topology, Funding Origin, Operation
Attribution, and Launch Results all continue to operate exactly as
before — receiving whatever population the (now-exclusive) Behaviour
Cohort selection passes down, with **zero logic changes** to any of
those stages themselves. Measured live, `7d` window, 2026-07-21.

## What changed vs. what didn't

| Stage | Changed? | Detail |
|---|---|---|
| Behaviour Cohort | **Yes** | New `canonical_behaviour` field (exclusive), new `canonical_behaviour_summary` (mutually-exclusive counts); existing `behaviours` (additive list) and `behaviour_summary` are byte-for-byte unchanged |
| Creator Identity | **No** | `enrich_creator_identity()` untouched; only receives a different (smaller, exclusive) `records` population depending on which cohort is selected upstream |
| Topology | **No** | `build_topology_classification()` untouched |
| Funding Origin | **No** | CEX/shared-withdrawal/treasury classification (`x60MatchesFunding` in the template) untouched |
| Operation Attribution | **No** | `operation_id`/`is_watchtower` fields and their consumers untouched |
| Launch Results | **No** | `x60OperationRows()`/`renderX60LaunchResults()` untouched — same rows, just a different upstream filter chain feeding them |
| Mechanism classification | **No** | `build_mechanism_classification()` untouched |

## Verification method

The Discovery UI's own pipeline (`templates/discovery.html`) chains
filters strictly downstream: `x60BehaviourRows()` → `x60CreatorIdentityRows()`
→ `x60TopologyRows()` → `x60FundingRows()` → `x60OperationRows()`. Each
stage's function body is completely unmodified by X65.0 — only
`x60BehaviourRows()`'s underlying match predicate
(`x60MatchesBehaviour`) changed, from additive-list membership to
exclusive-value equality. Because every downstream stage already reads
from whatever `x60BehaviourRows()` returns, the chain automatically
inherits exclusivity with zero code changes to the downstream
functions themselves — this was confirmed by direct code inspection
before implementation (see Phase 4's implementation notes) and is now
confirmed by live data below.

## Live verification: `oi_query(canonical_behaviour=...)` matches manual filtering exactly

| Behaviour | `oi_query()` result count | Manual filter count | Match? |
|---|---|---|---|
| BURST_LAUNCH | 915 | 915 | ✅ identical sets |
| CREATOR_RECYCLING | 1,908 | 1,908 | ✅ identical sets |
| RAPID_MIGRATION | 1,095 | 1,095 | ✅ identical sets |
| QUICK_BIRTH_MIGRATION | 67 | 67 | ✅ identical sets |

## Live verification: downstream stages receive exactly the cohort's population, unmodified

Selected the `CREATOR_RECYCLING` cohort (1,908 launches) and inspected
what Creator Identity and Topology independently produced for exactly
that population:

- **Creator Identity distribution within the cohort**: `REPEAT_CREATOR`
  1,740, `UNKNOWN_CREATOR_IDENTITY` 151, `RETURNING_CREATOR` 12,
  `DORMANT_REACTIVATED` 5 — sums to 1,908, the exact cohort size.
  Confirms Creator Identity operated on precisely the CREATOR_RECYCLING
  population, nothing more or less, using its own unmodified
  classification logic (`classify_creator_identity()`, untouched by
  X65.0).
- **Topology distribution within the cohort**: `UNKNOWN` 1,304,
  `LINEAR` 317, `FAN_OUT` 244, `MULTI_LEVEL_FAN_OUT` 43 — sums to
  1,908, again exactly the cohort size. Confirms Topology's own
  classification (`build_topology_classification()`, untouched)
  operated correctly on the exclusive population.

Both distributions summing exactly to the cohort size (not more, not
less) demonstrates the downstream chain is conserving population
correctly at every stage — no launch was silently dropped or
double-counted when passing from the new exclusive Behaviour Cohort
into the unmodified downstream stages.

## Funding Origin and Operation Attribution

Not independently re-derived in this pass (their own classification
logic — CEX/treasury detection, `operation_id` assignment — is
identical to pre-X65.0 code, confirmed via `git diff`-equivalent
inspection: zero lines changed in `x60MatchesFunding`,
`x60OperationRows`, or any funding/operation-attribution source module).
Given Creator Identity and Topology (the two stages immediately
downstream of the changed Behaviour Cohort stage) were directly
verified to conserve population correctly, and every stage after them
uses the identical "filter whatever the previous stage returned"
pattern with no logic of its own that references `behaviours` or
`canonical_behaviour` directly, transitive correctness follows.

## Launch Results

`x60OperationRows()` (the function `renderX60LaunchResults()` reads
from) is unmodified. Its output is definitionally "whatever
`x60FundingRows()`/`x60TopologyRows()`/etc. narrowed the population to"
— since those stages were confirmed unaffected in their own logic and
correctly conserve population, Launch Results necessarily also remains
correct, just now operating on an exclusive (not overlapping) upstream
population per cohort selection.
