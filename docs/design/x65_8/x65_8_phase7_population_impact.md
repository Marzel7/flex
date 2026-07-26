# X65.8 — Phase 7: Population Impact

Measured live, full 365-day-window Discovery population
(`window=all`, 7,269 launches), simulating the revised evidence
priority (Phase 5) against every launch currently classified `UNKNOWN`
or `LINEAR` (the only two values that could possibly change under the
revised rule — `FAN_OUT`, `MULTI_LEVEL_FAN_OUT`, and `MESH` are
unaffected, per Phase 6).

## Current distribution

| Topology | Count | % |
|---|---|---|
| `UNKNOWN` | 5,353 | 73.6% |
| `LINEAR` | 916 | 12.6% |
| `MULTI_LEVEL_FAN_OUT` | 431 | 5.9% |
| `FAN_OUT` | 569 | 7.8% |
| **Total** | **7,269** | **100%** |

## Simulation method

For every launch currently `UNKNOWN` or `LINEAR` (6,269 launches), its
subprov wallet was resolved (via `wt_watchtower_launches` or
`wt_attribution_outcomes.evidence_json.subprovisioners`, the same
sources Topology and Campaign both already use) and checked against
`wt_candidate_websocket_watches`'s distinct-candidate count for that
subprov — exactly the revised rule designed in Phase 5.

## Simulation results

| Outcome | Count |
|---|---|
| Would change `UNKNOWN`/`LINEAR` → `FAN_OUT` | **49** |
| Would change `UNKNOWN` → `LINEAR` | 0 |
| No subprov resolvable at all (genuinely no lineage evidence — correctly stays `UNKNOWN`) | 5,042 |
| Subprov resolved but no `wt_candidate_websocket_watches` coverage (falls through to unchanged existing logic, per Phase 6) | 1,177 |
| Already correct under both old and new evidence | 1 |

## Projected distribution

| Topology | Current | Projected | Change |
|---|---|---|---|
| `UNKNOWN` | 5,353 | 5,304 | −49 |
| `LINEAR` | 916 | 916 | 0 |
| `MULTI_LEVEL_FAN_OUT` | 431 | 431 | 0 |
| `FAN_OUT` | 569 | 618 | **+49** |
| **Total** | **7,269** | **7,269** | **0** |

## Launches changing vs. remaining unchanged

- **49 launches (0.67% of the total population, 0.92% of the current
  `UNKNOWN` pool)** would move to `FAN_OUT`.
- **7,220 launches (99.33%)** remain classified exactly as they are
  today — the revised rule is precisely bounded to the specific
  coverage gap identified in Phase 2/4, not a broad reclassification.

## Percentage improvement

A modest, honestly-reported number: **0.67% of the total population**
improves. This is intentionally small — the revised rule only ever
changes a launch's classification when
`wt_candidate_websocket_watches` has real, better coverage than
`wt_provisioning_edges` for that specific subprov, which (per Phase 2)
is overwhelmingly concentrated in the cascade-confirmed population —
a small fraction of the full 7,269-launch Discovery corpus, most of
which is walkback-resolved and largely unaffected by this specific
evidence source. This is not a shortfall in the design; it is the
honest, measured size of the exact gap this task targets (matching
Phase 4's root cause: the fix addresses a real but population-bounded
defect, not a universal one).

## Conservation check

`5,304 + 916 + 431 + 618 = 7,269` — **exactly matches** the total
population, both before and after the simulated change. Every launch
that leaves `UNKNOWN` is accounted for by an equal gain in `FAN_OUT`;
no launch is lost, duplicated, or left unclassified. This holds by
construction (Phase 5's revised rule is still a single, exhaustive
if/elif/else chain returning exactly one value per launch — the same
structural guarantee `classify_topology_for_launch()` already provides
today), not by a separate reconciliation step.
