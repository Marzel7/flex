# EB0.1J immutable census output bundle

EB0.1J serializes an already bounded EB0.1I `CensusResult`; it never reads a database, service, provider response, or configuration. The caller supplies a safe run identifier, source-schema fingerprints, and an empty isolated output directory. All files use exclusive creation and a nonempty directory is rejected.

The bundle contains exactly `run.json`, `aggregate.json`, `corpora.json`, and `hashes.json`. Run metadata binds the EB0.1I schema, high-water, 5,000-mint ceiling, input fingerprint, result digest, and source-schema fingerprints. Aggregate output records coverage, quality, completeness, conflicts, missing valuation, missing event kinds, exclusions, and uncovered mints. Corpus output contains only canonical EB0.1D/EB0.1E projections and their provenance digests—not raw database rows or provider payloads.

`hashes.json` binds SHA-256 digests for every payload file and a deterministic bundle digest. Replay verification requires the exact file set, schema version, every file digest, and the bundle digest. Qualification uses ephemeral fixtures only; live census execution remains separately authorized.
