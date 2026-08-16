# PSI0D-H immutable descriptive-projection publication contract

PSI0D-H qualifies a fixture-only filesystem publication boundary for the
non-authoritative descriptive projection produced by PSI0D-D. It does not
publish the real PSI0D-F projection and grants no integration or activation
authority.

The publisher accepts caller-injected canonical projection bytes and a new,
absent output path. Validation is fail closed over the PSI0D-B contract and
lineage identities, production-derived summary provenance, exact five-surface
schema, aggregate accounting, `ABSENT_NOT_NEGATIVE` semantics, reason codes,
interpretation flags, and false policy/ranking/integration/deployment/activation
authority. Unknown or source-value-bearing fields are rejected by the exact
schema.

## Immutable bundle

A successful fixture publication contains exactly:

- `projection.json`: the unchanged canonical descriptive projection;
- `contract.json`: the canonical PSI0D-H publication contract and lineage
  manifest;
- `hashes.json`: SHA-256 digests for the projection and contract plus one
  canonical bundle digest.

Files are created exclusively in a sibling staging directory, flushed and
fsynced, and the staging directory is atomically renamed to the caller's absent
target. The parent directory is then fsynced and the published bundle is replay
verified. Any validation, write, fsync, rename, or replay exception removes the
staging or renamed output. Existing targets are never overwritten and there is
no retry.

## Authority boundary

This contract is qualified only with injected frozen/ephemeral fixtures. It
does not authorize opening the real PSI0C assessment, reconstructing or
publishing the real PSI0D-F projection, integrating or deploying a consumer,
ranking entities, activating Evidence Mirror or Cohort Mode, production
activation, or EB2.
