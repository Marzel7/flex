# EB0.2J multi-source normalization

EB0.2J qualifies a deterministic bridge from three split, frozen SQLite schema
replicas into the exact normalized EB0.2G input schema. Every source path,
immutable mint cohort, source rowid high-water, and observation cutoff is
caller-supplied. Sources are opened with `mode=ro`, verified `query_only`,
schema subsets, selected-mint queries, rowid ceilings, and active deadlines.

Canonical create evidence and creator identity are accepted only from the
audited live-cascade pairing `creator_extraction_method=CLOSE_ACCOUNT_DESTINATION`
and `confidence=STRICT`, with non-null creator, signature, slot, coherent chain
and recorded times, agreement with non-mismatched `pf_ws_creator` when present,
and agreement with any creator-membership rows. This maps to
`CANONICAL_CREATE_PROOF`, never `PF_WS_CREATOR_VERIFIED`. Walkback, backfill,
manual-attestation, ambiguous, and mismatched rows produce no identity fact.
Market-first evidence remains `MARKET_FIRST_OBSERVED`, never birth valuation.

Every selected mint receives a provenance-bound observation-window fact, but
`full_horizon_complete` is always false: source high-waters prove a bounded read,
not continuous outcome coverage. Missing outcomes therefore remain `UNKNOWN` or
`NOT_OBSERVED` and are ineligible for negative denominators. Legacy historical
performance aggregates are never read.

The optional materializer writes the exact EB0.2G schema only to a caller-
supplied nonexistent path. Qualification uses ephemeral fixtures only and makes
no production compatibility, live extraction, activation, ranking, scoring,
profitability, cashflow, or operator-attribution claim.

The source schema initializers include mint-leading indexes on
`wt_watchtower_launches(mint)` and `creator_tokens(mint)`. EB0.2M qualifies
their selected-mint query plans only on ephemeral databases; creating those
indexes in production is a separate explicitly authorized mutation milestone.
