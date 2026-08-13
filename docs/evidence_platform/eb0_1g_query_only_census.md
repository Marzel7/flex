# EB0.1G query-only canonical census extractor

EB0.1G adds a dependency-injected SQLite extractor for the EB0.1A-E canonical birth and valuation baseline. It has no production path or default database. Callers must supply a file and an immutable migration high-water explicitly.

The extractor opens SQLite with `mode=ro`, enables and verifies `PRAGMA query_only=ON`, validates an exact source-column allow-list, uses a 250 ms connection timeout, caps every query at 30 seconds, and requires the exact 5,000-mint programme ceiling. Selection is deterministic by descending `migrated_at` then mint, and rows beyond the supplied high-water are excluded.

Only explicit evidence is adapted. Verified `LaunchFact` rows may establish `CHAIN_BIRTH`; explicit receive tables establish `PLATFORM_FIRST_SEEN` and observed `MIGRATION`; write-once first-observed values establish market observations. Generic `created_at`/`analyzed_at`, later valuations, and migration timestamps without an explicit receive boundary are never promoted. Missing valuation remains absent and is represented by the existing EB0.1A-E semantics.

The result contains source/high-water fingerprints, deterministic EB0.1D manifests, EB0.1E corpora, observation/exclusion accounting, and a replay digest. EB0.1G performs no writes or output publication itself. Qualification uses only ephemeral SQLite fixtures. Production census execution remains a separate explicitly authorized milestone.
