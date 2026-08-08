# EP4.4 — Dominant Motif Intelligence

## Outcome

EP4.4 provides deterministic operational intelligence profiles for the 69
motifs that EP4.3 measured as accounting for at least 80% of primary-corpus
activity. It does not alter or interpret the frozen population.

The selected motifs contain 11,381 of 14,203 occurrences (80.13%). Detailed
analysis is restricted to those motifs. Full-population access is used only to
calculate the explicitly requested Pareto cut-offs.

## Profile contract

Each dominant profile contains:

- the complete canonical graph;
- node/edge counts, maximum directed shortest-path depth, branching factor,
  structural symmetry, relationship distribution, and topology variability;
- launch cadence, burst and spacing measurements, creator/funding/
  infrastructure/counterparty reuse, wallet churn, and sequence stability;
- first/last observation, active duration, dormancy, measured inactivity gaps,
  growth, acceleration, decline, recurrence, and measurable seasonality;
- occurrences, objective role counts, diversity, completeness, and observation
  density;
- relationship count and deterministic replay state.

No profile contains an identity, Operation attribution, confidence score,
forecast, promotion state, or governance action.

## Structural definitions

- Topology depth is the maximum directed shortest-path distance in the
  canonical quotient graph.
- Branching factor is outgoing edge multiplicity per node class with outbound
  edges, stored in thousandths.
- Structural symmetry is the ppm share of nodes belonging to a canonical class
  with multiplicity greater than one.
- Structural variability is the already-measured count of distinct candidate
  topology digests inside the canonical motif.

Across the primary dominant population, canonical graphs contain 4–39 nodes.
Topology depth is one for 42 motifs, two for 18, three for eight, and four for
one.

## Temporal definitions

Internal dormancy periods are gaps greater than twice the motif's measured
median gap. Acceleration is the change between successive occurrence-count
deltas across three equal temporal partitions. Seasonality is reported only
with at least ten observations spanning fourteen days; otherwise it is
`NOT_MEASURABLE`.

No temporal measurement predicts future activity.

## Objective stability descriptors

Descriptors use transparent measured predicates:

- `PERSISTENT`: lifetime at or above the dominant-population median;
- `TRANSIENT`: lifetime below that median;
- `EXPLODING`: measured growth with a positive occurrence delta;
- `DECLINING`: the EP4.2 state is collapsing;
- `DORMANT`: last observation precedes the corpus boundary;
- `FRAGMENTING`: more than one observed topology digest.

Merging is always `NOT_MEASURED_WITHOUT_PRIOR_MOTIF_ASSIGNMENT` in this
milestone. The primary dominant population contains 37 persistent, 32
transient, 49 exploding, 20 declining, 68 dormant, and 27 fragmenting motifs.
Descriptors overlap.

## Relationship graph

Relationship edges require at least one exact, auditable condition:

- shared immutable Primitive ID;
- shared immutable Evidence ID;
- shared subject established by `REPEATED_COUNTERPARTY`;
- exact topology fingerprint;
- exact, fully observed behaviour fingerprint.

Window overlap is annotation only and cannot create an edge. Partial behaviour
vectors cannot match through shared unknowns. Ordinary `SHARED_TRANSACTION`
participants are not treated as infrastructure because that would incorrectly
connect motifs through ubiquitous transaction accounts.

The primary graph contains 69 nodes, 78 edges, and 26 connected neighbourhoods.
All 78 edges share Primitive and Evidence provenance. No additional edge was
created solely from topology, behaviour, timing, or infrastructure similarity.
The largest neighbourhoods contain 15, 11, and 10 motifs; isolated motifs remain
one-node neighbourhoods.

These are structural neighbourhoods, not Operations or ownership clusters.

## Pareto intelligence

| Cut-off | Occurrences | Total share | Marginal contribution |
| --- | ---: | ---: | ---: |
| Top 10 | 6,107 | 43.00% | 43.00% |
| Top 25 | 9,110 | 64.14% | 21.14% |
| Top 50 | 10,736 | 75.59% | 11.45% |
| Top 69 | 11,381 | 80.13% | 4.54% |
| Top 100 | 12,020 | 84.63% | 4.50% |
| Top 250 | 12,894 | 90.78% | 6.15% |

## Replay and safety

Independent reversed-order replay produced identical:

- dominant selection;
- profile payloads;
- relationship IDs and edges;
- neighbourhood IDs and membership;
- Pareto tables;
- final analysis ID.

Known corpus B produced 15 dominant profiles, 85 relationship edges, and one
connected neighbourhood. The generic unlabelled corpus produced one profile
and no edges.

The full machine-readable profiles and graphs are in
`docs/evidence_platform/ep4_4_dominant_motif_intelligence.json`.

Validation performed zero RPC calls, zero production reads or writes, loaded
no Operation Contracts, and changed no frozen algorithms.
