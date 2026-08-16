# PSI0D-B assessment-summary consumer

PSI0D-B qualifies a pure fixture-only, default-off consumer contract for synthetic PSI0C assessment summaries. Its contract digest is `a9e368cceede689736ca234891394551bda098df3b51ebfebf2e72f32aeb51f6`.

The contract accepts exact aggregate identities and accounting only: cohort denominator, per-query row and unique-mint counts, coverage numerators and denominators, duplicate and unmatched-row counts, aggregate unresolved-conflict and orphan/unmatched counts, approved missingness reason codes, and false authority flags. It rejects source rows, entity identifiers, payload values, unknown fields, stale lineage, inconsistent accounting, policy or ranking content, and authority drift.

The output is a canonical in-memory descriptive projection. Coverage fractions are copied without thresholds. `ABSENT_NOT_NEGATIVE` is preserved without negative inference. Duplicate, conflict, and unmatched counts remain unresolved descriptive facts. The contract performs no file, database, network, service, or configuration I/O and grants no policy, ranking, integration, deployment, or activation authority.

PSI0D-B is not an adapter for the real PSI0C assessment bundle. Any qualification closure, local application, consumer deployment, Evidence Mirror or Cohort Mode activation, production activation, or EB2 work remains separately authorized.
