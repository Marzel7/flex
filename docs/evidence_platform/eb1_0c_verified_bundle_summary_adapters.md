# EB1.0C Verified-Bundle Summary Adapters

EB1.0C maps exact frozen document projections from verified EB0.1J, EB0.2H, EB0.3G and EB0.4H bundles into EB1.0A. Callers must verify bundle integrity before supplying the canonical documents and hashes summary.

EB0.1J does not embed an engineering revision, so its adapter requires an explicit caller-bound revision and rejects omission. EB0.3 remains scoped to its exact request mint and time window; it is never treated as a broader missing-population denominator. Authority lanes, coverage, missingness, conflicts, completeness and provenance remain separate and deterministic.

The adapter performs no file access, cross-entity linkage, rates, profiles, ranking, scoring, policy, profitability/cashflow, identity, attribution or activation.
