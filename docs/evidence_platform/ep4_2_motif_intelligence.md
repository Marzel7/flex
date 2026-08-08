# EP4.2 — Motif Intelligence & Prioritization

## Outcome

EP4.2 adds deterministic, objective intelligence profiles downstream of EP4.1
canonical motifs. It measures significance without identifying, classifying, or
promoting any motif.

Evidence, Primitives, Operation Runtime, Operation Contracts, Discovery, and
Motif Canonicalization are unchanged.

## Measurements

Each profile records:

- occurrence and observed-population counts;
- distinct launches, creators, explicit activation-controller roles, explicit
  funding roles, direct counterparties, and infrastructure participants;
- canonical node, edge, primitive, relationship, and topology distributions;
- evidence and primitive completeness with explicit numerators and denominators;
- first/last observation, active duration, dormancy at the corpus boundary, and
  spacing distribution;
- launch cadence, one-hour burst-gap count, creator/funding/infrastructure reuse,
  wallet churn, and primitive-sequence stability;
- supporting Evidence and Primitive IDs.

Roles are counted only where a Primitive explicitly records the role. For
example, a creator comes from `LAUNCH_SIGNER.wallet` or
`LAUNCH_ACTIVATION.creator`; an arbitrary wallet is not inferred to be a
creator.

## Exact numeric contract

Immutable profile identity contains no floating-point values. Ratios and
completeness are stored as integer parts per million (`*_ppm`). Rates and
averages use explicitly named scaled integer units (`*_milli`). Every
completeness value retains its numerator and denominator.

## Growth and stability

Growth compares occurrence counts in the first and second halves of the motif's
observed time window:

- positive delta: `GROWING`;
- zero delta: `STABLE`;
- negative delta: `COLLAPSING`;
- absent or insufficient time span: `NOT_COMPARABLE`.

The output includes both half-window counts, absolute delta, and scaled rates.
Fragmentation and cross-version stability are reported as not measured/not
comparable when the input has no prior assignment or version to compare. The
engine does not invent either conclusion.

## Deterministic prioritization

Ranking is lexicographic, not a confidence or composite score:

1. occurrence count descending;
2. measured growth delta descending;
3. evidence completeness descending;
4. primitive completeness descending;
5. graph complexity descending;
6. motif ID ascending as the stable tie-breaker.

The ordered criteria are included with every output ranking. Ranking snapshots
are append-only and content-addressed separately from immutable motif
intelligence, so later population changes never rewrite a profile.

## Persistence

`MotifIntelligenceStore` persists:

- immutable intelligence profiles;
- immutable Evidence and Primitive references;
- immutable ordered ranking snapshots.

Repeated persistence is idempotent. Profile identity excludes rank because rank
is a population-level projection rather than a property of one motif.

## Shadow-corpus validation

| Dataset | Motifs measured | Evidence completeness | Primitive completeness | Replay/ranking |
| --- | ---: | ---: | ---: | --- |
| Known corpus A | 1,379 | 63.67% average | 89.19% average | Stable |
| Known corpus B | 15 | 100% | 100% | Stable |
| Generic unlabelled | 1 | 100% | 100% | Stable |

Known corpus A growth measurements were 145 growing, 125 stable, 96 collapsing,
and 1,013 not comparable. Known corpus B produced 1 growing, 1 stable, 5
collapsing, and 8 not comparable.

The validation performed zero RPC calls, zero production reads/writes, loaded
no Operation Contracts, and performed no identity promotion or governance. The
machine-readable output is
`docs/evidence_platform/ep4_2_motif_intelligence_validation.json`.

## Authority boundary

Motif intelligence measures observed structure and activity only. It contains
no known-Operation lookup, identity conclusion, confidence score,
classification, promotion, or governance action.
