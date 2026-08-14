# PSI0A-D4 migration transaction index repair

PSI0A-D4 qualifies one fixture-only additive index:

`idx_psi0a_token_analysis_migration_tx ON token_analysis(migration_tx)`

It addresses the recurring `_reconciler_unseen_sigs` membership scan identified
by PSI0A-D3. The contract accepts only a `token_analysis` table with a `TEXT`
`migration_tx` column, rejects a conflicting named index, and executes exactly
one deterministic `CREATE INDEX IF NOT EXISTS` statement only when no compatible
leading prefix already exists.

Fixture qualification proves that rows are preserved and the exact bounded
`migration_tx IN (...)` query changes from a relation scan to an indexed search.
Contract and result digests provide exact replay.

The contract is fixture-only. It grants no production database access, DDL,
deployment, extraction, Evidence Mirror/Cohort Mode, or activation authority.
Creating the production index and retrying PSI0A-D require separate approvals.
