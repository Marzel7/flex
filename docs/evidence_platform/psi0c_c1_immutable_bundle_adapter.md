# PSI0C-C1 immutable-bundle assessment adapter

PSI0C-C1 qualifies a provenance-preserving adapter between the replay-verified PSI0B production-shadow bundle format and the pure PSI0C-B assessment core. Its contract digest is `37ceac82d73152c4eba8d47f806a2d54d4131a074d1fc1120155e231755a31f9`.

The adapter consumes an injected in-memory mapping of exactly `accounting.json`, `hashes.json`, `results.json`, and `run.json`. It validates canonical bytes, per-file hashes, the expected bundle digest, production-run lineage, false integration and activation authority, the five frozen query identities, query and total accounting, and PSI0A-E row and byte ceilings before assessment.

Accepted rows retain the provenance class `PRODUCTION_DERIVED_IMMUTABLE_LOCAL_BUNDLE`; they are never relabelled as fixtures. The existing PSI0C-B fixture entry point remains unchanged and continues to reject `fixture_only=False`. Both paths use the same private deterministic assessment core.

The adapter grants no extraction, retry, integration, policy, ranking, activation, or EB2 authority. PSI0C-C remains a separate, one-attempt approval to read the actual immutable local bundle.
