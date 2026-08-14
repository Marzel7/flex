# PSI0A-H Immutable Preflight Bundle and Closure

PSI0A-H assembles replay-verified summaries of the committed PSI0A capture manifest/read boundary, five query plans, resource ceilings, health gates and abort/isolation semantics. It writes canonical `run.json`, `lineage.json`, `preflight.json`, `closure.json` and `hashes.json` only through a new atomic fixture directory. Per-file hashes and one bundle digest bind the exact file set.

Verification rejects existing output, stale staging, missing, extra, altered or noncanonical files; identity, count, conflict, lineage, replay or authority drift; and any component that is not explicitly `PASS`. Faults before publication remove staging and never publish a partial bundle.

The only passing closure verdict is `PSI0A_PASS_PSI0B_MAY_BE_SEPARATELY_PROPOSED`. It means the frozen evidence machinery has a qualified bounded and stop-safe design for proposing one later shadow extraction. It does not authorize PSI0B, extraction, production integration, a consumer, Evidence Mirror, Cohort Mode or activation.

Qualification uses committed artifacts and frozen/ephemeral fixtures only. It performs no live health observation, production query, row read, provider call, write, DDL, deployment, restart or configuration change.
