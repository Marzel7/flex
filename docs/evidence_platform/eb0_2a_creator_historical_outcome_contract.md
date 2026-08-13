# EB0.2A canonical creator historical outcome evidence contract

EB0.2A is a pure, deterministic local projection over explicitly supplied,
qualified creator identity and outcome facts. It does not discover creators,
query databases, call providers, aggregate scores, rank creators, or infer an
operator.

Each immutable fact binds one creator, one mint, one outcome kind, and one fixed
observation horizon. `denominator_eligible` is true only when evidence covers the
entire horizon. Partial or absent coverage can never become `OBSERVED_FALSE`;
missing evidence remains `UNKNOWN` with `PARTIAL` or `NOT_OBSERVED` completeness.
An observed positive must occur after the cohort event, within the horizon, and
no later than the evidence cutoff. These rules prevent future leakage and
survivorship or missingness from being silently converted into performance.

The initial outcome kinds are deliberately narrow:

- `MIGRATION_BY_HORIZON`
- `MARKET_CAP_AT_LEAST_BY_HORIZON`

Market-cap thresholds are explicit decimal strings. They do not represent birth
market cap, profitability, cash flow, or realised return. EB0.1 event identity,
provenance, quality, completeness, conflicts, and missingness remain upstream
facts and are not reinterpreted here.

Conflicting qualified sources remain separate facts. Projection identity is
content-derived, exact-replay idempotent, and independent of input order.
Any aggregation, historical profile, policy, ranking, GMGN supplement, live
source adapter, or production activation requires a later separately authorized
milestone.
