# PSI0A-A Production Boundary Contract

PSI0A-A is a pure deterministic contract defining the permitted production-shadow preflight boundary. It binds an engineering revision and explicit logical database/relation allow-list while fixing SQLite URI `mode=ro`, verified `PRAGMA query_only`, bounded autocommit reads, non-mutating metadata/query-plan statement classes, and deterministic stop conditions.

The contract prohibits writes, DDL, temporary production objects, multi-statement input, provider/RPC access, evidence extraction, shadow evidence output, Evidence Mirror, Cohort Mode and activation. It emits `NON_EXECUTABLE_PRODUCTION_SHADOW_PREFLIGHT` with both extraction and activation authority false. Unknown surfaces, write-capable statements, replay mutation and authority expansion fail closed.

Qualification uses frozen fixtures only. PSI0A-A performs no production access or evidence extraction and does not authorize PSI0A-B, PSI0B or production activation.
