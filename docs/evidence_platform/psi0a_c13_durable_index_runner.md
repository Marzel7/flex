# PSI0A-C13 durable single-index runner

PSI0A-C13 qualifies a fixture-only runner for one exact repair from the
PSI0A-C10 contract. It requires a caller-supplied new empty output directory and
writes canonical `attempt.jsonl` records with `flush` and `fsync` at every
phase. A terminal record binds the digest of all prior events; replay verifies
canonical bytes, contiguous sequence, terminal status and final ledger digest.

The ledger distinguishes normal success, compatible no-op, lock failure,
deadline interruption, DDL exception and incomplete external termination. It
records schema verification, lock acquisition, DDL start/outcome, exact
exception, rollback, progress-handler removal, connection closure and the final
index postcondition. The runner executes at most one deterministic statement,
uses a 250 ms SQLite lock timeout, accepts one positive monotonic deadline and
never retries.

This qualification uses frozen or ephemeral SQLite fixtures only. It grants no
production access, production DDL, evidence extraction, shadow output,
activation, PSI0A-D or PSI0B authority. A production retry of the remaining
`token_analysis(migrated_at, mint)` index requires separate authorization.
