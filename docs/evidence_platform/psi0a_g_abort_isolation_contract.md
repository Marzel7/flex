# PSI0A-G Stop/Abort and Production-Isolation Proof

PSI0A-G is a pure deterministic fixture evaluator over immutable stop traces. It binds the PSI0A-F health gate, PSI0A-E resource ceilings, replay-verified PSI0A-D plans, canonical capture manifest and C16 boundary. It performs no production operation and grants no extraction or activation authority.

The contract recognizes only health-gate failure, deadline/resource breach, schema/path/boundary/contract drift, SQLite interruption/exception, replay failure and observer insufficiency. Before start, every trigger produces `DO_NOT_START` and proves that no connection, transaction, progress handler or temporary artifact was opened. During a hypothetical active operation, every trigger produces `ABORTED` only when each resource that was opened is deterministically cleaned: progress handler removed, rollback attempted, transaction resolved, connection closed and temporary artifact removed.

Any production write/DDL, service/configuration/lock/metric mutation, partial bundle publication, retry, pagination, failover, degraded bypass, adaptive widening or incomplete cleanup produces `ISOLATION_FAILURE`. Conditional cleanup is required only for resources proven opened; the contract never fabricates cleanup evidence. Traces and decisions are canonical-digest bound and replay verified.

Qualification uses frozen and ephemeral fixtures plus fault injection only. PSI0A-G does not authorize production access, a live health observation, extraction, shadow artifacts, PSI0A-H, PSI0B or activation.
