# EB0.1G/EB0.1I query-only canonical census extractor

EB0.1G adds a dependency-injected SQLite extractor for the EB0.1A-E canonical birth and valuation baseline. EB0.1I amends it for the observed split-source layout: callers independently supply the primary token database and Evidence database, plus an immutable migration high-water. It has no production path or default database.

The extractor opens SQLite with `mode=ro`, enables and verifies `PRAGMA query_only=ON`, validates an exact source-column allow-list, uses a 250 ms connection timeout, caps every query at 30 seconds, and requires the exact 5,000-mint programme ceiling. EB0.1K counts eligible distinct mints at the immutable high-water, selects only the latest 5,000 by descending `migrated_at` then mint, and records the older eligible remainder as excluded by the cohort bound without loading its canonical evidence.

Only explicit evidence is adapted. Verified `LaunchFact` rows from the separately injected Evidence database may establish `CHAIN_BIRTH`; optional bounded explicit receive-record inputs establish `PLATFORM_FIRST_SEEN` and observed `MIGRATION`; write-once first-observed values establish market observations. The extractor does not require or create synthetic receive tables. Generic `created_at`/`analyzed_at`, later valuations, and migration timestamps without an explicit receive boundary are never promoted. Missing event kinds and wholly uncovered mints are counted explicitly rather than synthesized or allowed to fail the whole cohort.

EB0.1L restricts returned `LaunchFact` rows to the selected mint set with a JSON predicate. Deterministic ranking returns at most a two-row allowance per selected mint and 10,000 rows globally, plus one overflow sentinel; overflow or malformed JSON stops the run. Launch facts for unselected mints are not materialized into the extractor.

EB0.1M makes the query deadline active rather than retrospective. Every bounded statement installs a monotonic SQLite progress handler that interrupts execution at the configured deadline (never above 30 seconds), classifies its own interruption as `EB0_1G_QUERY_TIMEOUT`, preserves unrelated SQLite errors, and clears the handler on success, timeout, and exception paths.

EB0.1O canonicalizes `token_analysis` REAL-affinity first-observed market-cap and price values through the same positive finite Decimal representation used for snapshot evidence before EB0.1C adaptation. Integral REAL values lose trailing `.0`, exponent forms expand deterministically, and zero, negative, or non-finite values stop with `EB0_1O_INVALID_MARKET_VALUE`.

The result contains source/high-water fingerprints, deterministic EB0.1D manifests, EB0.1E corpora, observation/exclusion/missingness accounting, and a replay digest. EB0.1I performs no writes or output publication itself. Qualification uses only ephemeral SQLite fixtures. Production census execution remains a separate explicitly authorized milestone.
