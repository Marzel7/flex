# PSI0A-D6D production-specific migration transaction index runner

PSI0A-D6D establishes a production-specific durable execution boundary for
exactly one previously qualified PSI0A-D4 index statement. It does not deploy
the index and grants no extraction or activation authority.

The runner accepts only an immutable, replay-valid deployment authorization
record bound to all of the following:

- PSI0A-D4 contract digest
  `a160398c3130dbacf2314bb4046b9bac4865cd9dd421311f2fd999058c0ab9f2`;
- database `main`, relation `token_analysis`, `migration_tx TEXT`;
- index `idx_psi0a_token_analysis_migration_tx` and its exact single
  `CREATE INDEX` statement;
- engineering revision, authorization identity, run identity;
- fingerprints of the resolved production database path and new empty output
  directory;
- a positive monotonic deadline of at most 60 seconds, a 250 ms lock timeout,
  one statement, and zero retries.

Fixture authorization identities, changed paths, altered statements, authority
changes, reused outputs, schema/type drift and conflicting index definitions
fail closed before mutation. A future execution must supply a separately
authorized production record; D6D itself creates only fixture databases.

Every attempt writes an append-only canonical JSONL ledger. Each transition is
flushed and fsynced, including authorization identity, precondition verification,
lock acquisition, DDL start/outcome, deadline or exception, rollback, progress
handler removal, connection closure, row-preserving indexed-plan postcondition,
and terminal replay digest. A ledger without a valid terminal record is reported
as an incomplete external termination.

Qualification proves successful and already-compatible paths, lock failure,
deadline interruption, injected DDL failure and rollback, cleanup, row
preservation, indexed reconciler plan, exact authorization/path binding, fixture
token rejection, output reuse rejection, tamper detection and exact replay.

PSI0A-D6D performs no production/runtime access, production DDL, service stop or
restart, configuration change, high-water capture, evidence extraction, shadow
output, provider call, Evidence Mirror/Cohort Mode action, activation or EB2 work.
