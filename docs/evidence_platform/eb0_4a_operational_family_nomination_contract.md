# EB0.4A operational-family nomination evidence contract

EB0.4A is a pure deterministic contract over frozen platform-owned operation
observations. Per-operation facts keep role as the primary classification axis
and preserve edge topology as relationship metadata alongside mechanism and
temporal behaviour, source/version/provenance, quality, completeness and
conflict state.

Cross-operation output is permanently `NOMINATION_NON_AUTHORITATIVE` and may be
only `PROPOSED` or `SUPPORTED`. Every nomination records its member operations,
primary role, supporting immutable fact IDs, explicitly shared edge/mechanism/
temporal features, sources, quality, completeness and conflicts. Facts and
nominations have deterministic content identities and preserve conflicts.

Topology alone never supports a family. `SUPPORTED` requires a common role,
shared mechanism and temporal evidence, at least two versioned sources, and
complete non-conflicting inputs. Even then it is only a family nomination:
`operator_identity_asserted` is always false. Operator/owner identity,
attribution, confirmation, confidence, ranking, scoring, profitability,
cashflow and policy fields fail closed. The contract has no I/O, provider,
runtime, deployment or activation path.
