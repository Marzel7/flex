# PSI0A-D5 durable migration transaction index runner

PSI0A-D5 adapts the proven PSI0A-C13 durable-attempt pattern into a separate
fixture-only runner that accepts exactly the replay-verified PSI0A-D4 contract
for `idx_psi0a_token_analysis_migration_tx`.

Each run requires a caller-supplied new empty directory and writes an append-only
canonical JSONL ledger. Every phase is flushed and fsynced: start and identity,
schema/precondition validation, lock acquisition, DDL start/outcome, deadline or
exception, rollback, progress-handler removal, close, row-preserving indexed-plan
postcondition, and terminal replay digest. A missing terminal record is reported
as an incomplete external termination.

The runner executes at most one deterministic statement with a 250 ms lock
timeout, caller-supplied positive monotonic deadline, and no retry. It rejects
schema drift, conflicting index definitions, reused output directories and any
non-fixture authorization.

This qualification grants no production access, DDL, deployment, extraction,
Evidence Mirror/Cohort Mode, or activation authority. Any production attempt is
a separate milestone.
