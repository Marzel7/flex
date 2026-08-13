# EB0.2H immutable creator outcome extraction bundles

EB0.2H serializes an already bounded EB0.2G result; it never reads a database,
service, provider, or configuration. The caller supplies a safe run ID, an
engineering revision, the exact fixed policies, and a new or empty isolated
directory. Files are created exclusively and overwrite is rejected.

The exact bundle is `run.json`, `accounting.json`, `manifests.json`,
`corpora.json`, and `hashes.json`. Canonical JSON binds the source fingerprint,
extraction digest, policies, engineering revision, selected/qualified/excluded
accounting, EB0.2D manifests, and EB0.2E corpora. Per-file hashes produce one
deterministic bundle digest.

Verification requires the exact file set and hashes, reconstructs and verifies
every manifest and corpus, recomputes accounting, and replays the EB0.2G result
digest from the bundled content. Missing, extra, altered, inconsistent, or
noncanonical content fails closed. Qualification uses ephemeral fixtures only;
live extraction, external publication, aggregation, ranking, and activation are
separate milestones.

A fully accounted extraction with no qualified mints is a valid measured result:
it contains no manifests or corpora, every selected mint has an explicit
exclusion reason, and exact replay still binds the policies, source fingerprint,
accounting, and result digest. Empty manifests with non-empty corpora, qualified
mints, missing exclusions, or otherwise inconsistent accounting fail closed.
