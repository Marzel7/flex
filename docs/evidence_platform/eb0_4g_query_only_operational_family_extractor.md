# EB0.4G Query-Only Operational-Family Extractor

EB0.4G qualifies a dependency-injected SQLite extractor using only frozen or ephemeral fixtures. It has no default database path and makes no production compatibility claim.

It requires the exact EB0.4F three-table schema, opens it with `mode=ro`, verifies `PRAGMA query_only=ON`, rejects extra schema objects, enforces an active query deadline and hard cohort/evidence/group/membership ceilings, and queries only the immutable operation cohort. Explicit candidate membership is input evidence rather than clustering or identity inference.

Every operation is qualified or explicitly excluded. Candidate groups flow through EB0.4C adapters, EB0.4A nomination rules, EB0.4D manifests and EB0.4E corpora with deterministic accounting and digests. No profile, rate, rank, score, policy, profitability/cashflow or operator identity/attribution is emitted.
