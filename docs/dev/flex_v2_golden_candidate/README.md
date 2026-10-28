# Flex V2 Golden candidate

Reconstruct this exact Git commit in a fresh checkout. Use the single SHA-bound
FROZEN_RUNTIME_MANIFEST.json supplied with the qualification handoff. Verify it
with scripts/flex_v2_golden_verify.py --root CHECKOUT --manifest MANIFEST --sha256 DIGEST.
The manifest identifies external matched V2 stores, paired authority, journals,
receipt/custody owners, durable Helius accounting/publication state, retained
monitor queues/selections and existing credential-file authority. None is
recreated, reset, replayed or embedded in source. Secrets are environment-file
references only. Runtime source paths use ${SOURCE_ROOT}; no historical Codex
worktree is a runtime dependency. The live installation remains unchanged.

The three runtime role trees match every installed Python module. Ten previously
different source files are reconciled to installed authority. Optional undeployed
CREATE audit helpers remain packaged but inactive; their previously qualified
callback versions are not substituted for installed processing. Existing root
API, templates/static, monitor and bridge source are preserved unchanged.

Existing entrypoints remain authoritative: lightweight_runtime_entry for active
roles, flex_v2_dormant.py for dormant packages, flex_v2_fresh_pair_recovery.py for
explicit fresh compact-pair recovery, and src.ops.v2_storage_lifecycle for bounded
seven-day cache/WSS maintenance. Dormant activation and recovery execution need
separate authorization. Existing dormant profiles intentionally reject a changed
pair until explicitly rebound to its identity/generation, preserving their queue,
selection and budget state. A changed pair also requires explicit lifecycle
manifest rebinding; stale cleanup authority fails closed.

This candidate is not a Golden tag or a production deployment. Browser-visible
UI acceptance and exact historical at-handoff CREATE timing remain unverified.
The protected CREATE-waiting backlog, blocked deliveries, finite custody quotas
and narrow birth-journal headroom remain operational limitations, not permission
to delete evidence or replay exhausted work. No V1 contents are required.
