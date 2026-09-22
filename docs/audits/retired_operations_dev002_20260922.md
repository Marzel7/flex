# DEV-002 retired-operation boundary

Leviathan, Sentinel, and Harbinger were retired from active DEV behaviour on
2026-09-22. This document is a retirement record, not an active operation
registry or attribution source.

The active reader denies the three historical operator identifiers, so retained
database rows cannot appear in active registry, detail, summary, or navigation
projections. Their former runtime matchers, projectors, operation-specific
maintenance scripts, UI projections, and tests were removed from DEV.

Reference classification:

- `HISTORICAL_PROVENANCE`: `docs/audits/**` and archived handoff evidence.
- `ISOLATED_RUNTIME_MIRROR`: `.runtime_api_deep_a1f4/**` and
  `.runtime_recovery_c06d/**`; retained without execution or import from DEV.
- `GENERIC_NON_OPERATION_USE`: technical uses of the lower-case word
  “sentinel” (for example, schema guards, placeholder values, and cache state).
- `UNEXPECTED_ACTIVE_REFERENCE`: none at completion.

Completion results:

- 107 legacy operation-specific source, script, template, and test files were
  removed from the DEV change set.
- `ACTIVE_UI_REFERENCES = 0`, `ACTIVE_RUNTIME_REFERENCES = 0`, and
  `ACTIVE_OPERATION_REGISTRY_REFERENCES = 0` for Leviathan, Sentinel, and
  Harbinger.
- The 25 remaining active-tree matches for the lower-case word `sentinel` are
  generic technical uses (schema guards, placeholders, cache state, or test
  fixtures), not the retired Sentinel operation.
- `HISTORICAL_AUDITS_PRESERVED = true` and
  `ISOLATED_RUNTIME_MIRROR_PRESERVED = true`.

This retirement does not alter historical evidence, production source,
production configuration, services, databases, queues, or release manifests.
