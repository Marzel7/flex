# PSI0B-D Production-Specific Shadow Binding

PSI0B-D qualifies the immutable authorization and path-binding boundary required before one production query-only shadow run can be separately approved. It performs no database access and does not consume an authorization.

The record binds the committed PSI0B-C runner and PSI0B-A/B lineage, the exact creator/evidence/main/ops absolute paths and logical path fingerprints, the five byte-frozen query identities, C16 rowid boundaries, PSI0A-E ceiling identity, a new run ID and output fingerprint, and one-attempt authority. URI `mode=ro`, verified `PRAGMA query_only`, 250 ms lock timeout, sequential connections, active deadlines, health gates and unconditional rollback/handler removal/close remain mandatory.

Only `HUMAN_APPROVED_ONE_RUN_QUERY_ONLY_PRODUCTION_SHADOW` is accepted. Fixture or unknown tokens, changed paths or fingerprints, reused output, retry/pagination/failover/widening, integration authority and activation authority fail closed. Building and replaying the record does not open a source, observe production health, execute a query or authorize activation.

The next milestone must separately authorize consumption of one exact record by the committed runner. That authorization must supply the final authorization ID, run ID, new empty output directory and fresh PSI0A-F health evidence, and must retain every PSI0A-G abort condition.

The production entry point verifies the complete record, exact bound PSI0B-A preflight and cohort before opening any source. It then invokes the same ceiling-, health-, deadline- and cleanup-enforced execution core as PSI0B-C while emitting production-shadow authority and authorization provenance. An unbound preflight is proven to stop before source access.
