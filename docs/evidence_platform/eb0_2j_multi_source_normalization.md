# EB0.2J multi-source normalization

EB0.2J qualifies a deterministic bridge from three split, frozen SQLite schema
replicas into the exact normalized EB0.2G input schema. Every source path,
immutable mint cohort, source rowid high-water, and observation cutoff is
caller-supplied. Sources are opened with `mode=ro`, verified `query_only`,
schema subsets, selected-mint queries, rowid ceilings, and active deadlines.

Canonical create evidence is accepted only from a single coherent launch row
carrying a signature, slot, timestamps, and `VERIFIED` confidence. Market-first
evidence remains `MARKET_FIRST_OBSERVED`, never birth valuation. Creator identity
requires agreement between non-mismatched `pf_ws_creator`, a verified
PumpPortal-derived launch identity, and any legacy creator-membership rows.
Ambiguity or disagreement produces no identity fact.

Every selected mint receives a provenance-bound observation-window fact, but
`full_horizon_complete` is always false: source high-waters prove a bounded read,
not continuous outcome coverage. Missing outcomes therefore remain `UNKNOWN` or
`NOT_OBSERVED` and are ineligible for negative denominators. Legacy historical
performance aggregates are never read.

The optional materializer writes the exact EB0.2G schema only to a caller-
supplied nonexistent path. Qualification uses ephemeral fixtures only and makes
no production compatibility, live extraction, activation, ranking, scoring,
profitability, cashflow, or operator-attribution claim.
