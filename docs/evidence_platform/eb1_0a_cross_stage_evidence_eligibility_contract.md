# EB1.0A Cross-Stage Evidence Eligibility Contract

EB1.0A is a pure deterministic contract over caller-supplied immutable EB0.1–EB0.4 bundle summaries. It binds each upstream bundle schema version, digest, cohort or window identity, engineering revision, authority lane, coverage counts, completeness and provenance. It performs no file, database, service, network, provider or clock access.

The four authority lanes remain separate:

- EB0.1 canonical birth and valuation evidence;
- EB0.2 canonical creator-outcome evidence;
- EB0.3 supplemental non-authoritative market evidence;
- EB0.4 non-authoritative operational-family nomination evidence.

Each lane receives exactly one deterministic readiness state: `ELIGIBLE`, `INELIGIBLE_MISSING`, `INELIGIBLE_CONFLICTING`, or `NOT_APPLICABLE`. Missing or incomplete evidence is never converted into a negative outcome. Conflicts take precedence and remain explicit. Exact four-stage membership, counts, authority, versions, digests and replay identity fail closed on drift or mutation.

This contract does not join mints, creators, wallets, operations or families. It emits no rates, profiles, ranks, scores, policy, profitability/cashflow, operator identity, attribution or activation decision. Qualification uses frozen synthetic summaries only and makes no production compatibility claim.
