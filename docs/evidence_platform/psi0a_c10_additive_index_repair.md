# PSI0A-C10 additive-index repair qualification

PSI0A-C10 binds the two missing prefixes found by replay-verified PSI0A-C9 audit
`08642b6ca525a01f029e60029798afc16351162869ab80cc8831d7c212a55c31`:

- `evidence.normalized_evidence_records(fact_family)` via
  `idx_psi0a_normalized_evidence_fact_family`;
- `main.token_analysis(migrated_at, mint)` via
  `idx_psi0a_token_analysis_migrated_mint`.

The contract contains one deterministic, single-statement `CREATE INDEX IF NOT
EXISTS` definition for each prefix. Qualification is restricted to
caller-owned frozen or ephemeral SQLite fixtures. It validates the exact table,
column affinity and existing named-index definition before executing anything.
An existing compatible leading prefix performs no work; an incompatible index
using the deterministic name fails closed.

This milestone grants no production access or DDL authority. It does not create
either production index, capture a high-water, extract evidence, produce shadow
output, or authorize PSI0A-D, PSI0B or activation. Production deployment, if
later proposed, requires a separate bounded authorization and health gate.
