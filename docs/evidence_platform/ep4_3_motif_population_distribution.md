# EP4.3 — Motif Population Distribution & Concentration Analysis

## Outcome

EP4.3 adds a deterministic analytical projection over frozen EP4.2 motif
intelligence profiles. It does not alter Discovery, motifs, canonicalization,
ranking, Evidence, Primitives, Runtime, or Operation Contracts.

The primary frozen corpus contains both a concentrated head and a substantial
long tail:

- 14,203 candidate occurrences across 1,379 motifs;
- largest motif: 961 occurrences;
- median motif: 1 occurrence;
- 972 singleton motifs;
- 69 motifs account for 80.13% of occurrences;
- the top 10 account for 43.00%;
- the top 100 account for 84.63%.

## Statistical contract

The analysis uses:

- nearest-rank percentiles;
- population standard deviation, recorded in thousandths;
- integer ppm shares and basis-point percentages;
- fixed occurrence bands: `1`, `2`, `3–5`, `6–10`, `11–25`, `26–100`,
  `101–500`, and `501+`;
- deterministic ordering by occurrence count descending and motif ID ascending.

No sampling, model, score, or adaptive threshold is used.

## Primary distribution

| Measurement | Result |
| --- | ---: |
| Motifs | 1,379 |
| Candidate occurrences | 14,203 |
| Minimum | 1 |
| Maximum | 961 |
| Mean | 10.299 |
| Median / p50 | 1 |
| p75 | 2 |
| p90 | 7 |
| p95 | 28 |
| p99 | 236 |
| Population standard deviation | 58.803 |

## Concentration

| Population head | Occurrences | Cumulative share |
| --- | ---: | ---: |
| Top 1 | 961 | 6.77% |
| Top 5 | 3,802 | 26.77% |
| Top 10 | 6,107 | 43.00% |
| Top 25 | 9,110 | 64.14% |
| Top 50 | 10,736 | 75.59% |
| Top 100 | 12,020 | 84.63% |

The measured Pareto threshold is 69 motifs for at least 80% of activity. Those
motifs contain 11,381 occurrences (80.13%); the remaining 1,310 motifs contain
2,822 occurrences (19.87%).

## Long tail

| Occurrences per motif | Motifs |
| --- | ---: |
| 1 | 972 |
| 2 | 134 |
| 3–5 | 110 |
| 6–10 | 51 |
| 11–25 | 39 |
| 26–100 | 44 |
| 101–500 | 21 |
| 501+ | 8 |

Singleton and two-occurrence motifs comprise 80.20% of motif identities. This
is a measured population property, not a recommendation to merge or discard
them.

## Completeness

Primary-corpus evidence completeness is 9,560 of 14,203 candidate occurrences
(67.31%). Primitive completeness is 64,076 of 70,465 referenced observations
(90.93%). The report retains the unavailable counts—4,643 occurrence-level
Evidence gaps and 6,389 incomplete Primitive observations—rather than
normalizing them away.

## Objective descriptors

Descriptors are deterministic predicates, not identity or governance states:

- `HIGH_VOLUME`: at least 101 occurrences;
- `SINGLETON`: exactly one occurrence;
- `STABLE` / `GROWING`: the frozen EP4.2 measured growth state;
- `DORMANT`: last observation precedes the corpus boundary;
- `FRAGMENTED`: more than one observed candidate-topology digest inside the motif;
- `EVIDENCE_LIMITED`: Evidence completeness below 1,000,000 ppm;
- `PRIMITIVE_LIMITED`: Primitive completeness below 1,000,000 ppm.

The primary corpus contains 29 high-volume, 972 singleton, 145 growing, 125
stable, 61 fragmented, 501 Evidence-limited, and 501 Primitive-limited motifs.
Descriptors may overlap.

## Replay stability

Independent replay comparison verified that the following were identical for
all three validation populations:

- largest motif;
- ranking;
- complete distribution;
- occurrence assignment identities;
- motif IDs;
- final analysis ID and tables.

The analysis reports `NOT_MEASURED` instead of stability when no replay
population is supplied.

## Other validation populations

Known corpus B contains 94 occurrences across 15 motifs. Its largest motif has
26 occurrences, its median is one, and five motifs account for 86.17% of
occurrences. The generic unlabelled population contains one motif with two
occurrences.

The machine-readable report contains every motif-level timeline, completeness,
diversity, growth, current-activity, and descriptor row:
`docs/evidence_platform/ep4_3_motif_population_analysis.json`.

Validation performed zero RPC calls, zero production reads or writes, and no
identity, governance, classification, or Operation Contract execution.
