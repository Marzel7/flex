# PSI0B-A Run-Specific Shadow Extraction Preflight

PSI0B-A defines a pure immutable preflight object for a later, separately authorized production shadow query. It requires a caller-supplied cohort artifact with explicit membership and source digest; the contract does not select a cohort. It binds the closed PSI0A-H through PSI0A-D identities, exact C16 inclusive rowid ceilings, corrected creator/evidence/main/ops path-binding digests, all five query identities, 5,000-row ceilings, one run ID and a fingerprint of a caller-selected output path that must not yet exist.

The evidence query requires an explicit caller fact family. The other four queries bind the cohort digest. Three health checkpoints remain explicit `REQUIRED_AT_EXECUTION` placeholders: PSI0B-A never fabricates fresh telemetry or converts fixture observations into production authority. Accounting fields and PSI0A-G stop semantics are frozen before execution.

Replay rejects cohort, lineage, path, boundary, parameter, health-placeholder or authority drift; duplicate/empty/oversized cohorts; existing output; retries, pagination, failover and widening. Qualification uses frozen/ephemeral fixtures only and grants no extraction, integration or activation authority.

Before any production query, a separate authorization must name the real immutable cohort artifact/digest, fact family, run ID and isolated output directory and must satisfy fresh PSI0A-F health gates.
