# EP5.0 — Operational Change Intelligence

## Outcome

EP5.0 adds a deterministic, non-authoritative change engine that compares two
immutable operational-landscape snapshots. It detects and persists objective
motif, neighbourhood, relationship, population, ranking, and temporal changes.

The engine does not infer identity, execute governance, load Operation
Contracts, alter discovery, or read production databases. It performs no RPC.

## Comparison contract

Every evaluation has exactly two inputs:

```text
OperationalLandscapeSnapshot N
                  +
OperationalLandscapeSnapshot N+1
                  |
                  v
             ChangeSnapshot
```

There is no rolling mutable state. Repeating a comparison with the same
snapshots and change-engine version produces the same snapshot and observation
identities, regardless of input ordering.

Motif continuity is established only through exact overlap of immutable
candidate occurrence assignments. Addresses, similarity scores, known
Operations, and semantic labels do not establish continuity.

## Immutable outputs

- `ChangeSnapshot` records the comparison identity, concentration movement,
  Pareto movement, ranking movement, largest movers, and stable leaders.
- `MotifDelta` records new, disappeared, growing, declining, stable, dormant,
  reactivated, fragmenting, merging, retired, topology, primitive-composition,
  completeness, population-share, and rank changes.
- `NeighbourhoodDelta` records new/disappeared neighbourhoods, expansion,
  contraction, isolation, split/merge, size, relationship count, density, and
  external-connectivity changes.
- `RelationshipDelta` records exact relationship creation and removal.
- `TrendObservation` records measured growth, decay, rank movement, dormancy,
  reactivation, and positive acceleration without forecasting.

All records are append-only and retain references to both immutable source
snapshots. Identical appends are idempotent; updates and deletes are prohibited.

## Measurement semantics

- Structural diffs preserve both canonical graphs and report node, edge,
  primitive, relationship, and topology deltas.
- Population diffs report exact new/lost occurrences, share movement, and rank
  movement.
- Temporal measurements use first/latest observations and elapsed-time-normalized
  growth or decay. `EXPLODING` represents measured positive growth acceleration;
  it is not a prediction or composite score.
- Split and merge observations require exact occurrence-overlap components.
- Pareto and concentration movement are calculated directly from the two frozen
  motif populations.

## Validation corpus

The validator used the frozen known corpora and generic unlabelled corpus. For
each corpus it constructed an earlier immutable snapshot by filtering exact
existing occurrences at the median observation boundary. It did not rerun
discovery with different heuristics or acquire new data.

### Known corpus A

- Motifs: 767 → 1,379
- Candidate occurrences: 7,106 → 14,203
- New motifs: 612
- Growing motifs: 244
- Stable motifs: 523
- Dormant motifs: 765
- Reactivated motifs: 2
- Positively accelerating motifs: 113
- Became dominant: 8
- Became irrelevant: 10
- Exact relationship changes: 110 (62 created, 48 removed)
- Neighbourhood change observations: 30 across 26 changed neighbourhoods
- Dominant-population share: 81.5367% → 80.1310%
- Changes emitted: 2,553

The current Pareto measurements are:

| Band | Occurrences | Share |
| --- | ---: | ---: |
| Top 10 | 6,107 | 42.9980% |
| Top 25 | 9,110 | 64.1414% |
| Top 50 | 10,736 | 75.5897% |
| Top 69 | 11,381 | 80.1310% |
| Top 100 | 12,020 | 84.6300% |
| Top 250 | 12,894 | 90.7840% |

### Known corpus B

- Motifs: 8 → 15
- New: 7
- Growing: 5
- Stable: 3
- Dormant: 3
- Changes emitted: 106

### Generic unlabelled population

- Motifs: 1 → 1
- Growing: 1
- Reactivated: 1
- Changes emitted: 4

## Safety and reproducibility

- Replay and reversed-input-order validation produced identical outputs.
- Coverage was complete for every compared motif.
- Identity and governance remained disabled.
- No Operation Contract was loaded.
- No production database was read or written.
- No RPC call was issued.
- Existing Evidence, Primitive, Runtime, Discovery, canonicalization, and ranking
  implementations were not modified.

The full machine-readable comparison, including canonical graphs and immutable
record identities, is in
`docs/evidence_platform/ep5_0_operational_change_intelligence.json`.
