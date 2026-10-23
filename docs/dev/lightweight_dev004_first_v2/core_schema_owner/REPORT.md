# DEV004 Core base-schema owner qualification

`DEV004_CORE_SCHEMA_OWNER_STATUS: HOLD — accepted initialization procedures run repeatably, but do not produce the required complete fresh Core schema.`

Runtime source remains `712f18400508a7681e3cea78e79cf84c0be8b666`. No implementation, source deployment, new schema design or production database access occurred. Fresh disposable Core was initialized only through existing owner procedures; no schema was copied from V1. Existing accepted reference fixtures and all previous scoped acceptance remain unchanged.

## ACCEPTED_CORE_SCHEMA_AUTHORITY

Frozen FLEX_STABLE_V2 is `2d825bbcf27f802bd0bfcef90cedeba43cc968eb`; immutable manifest SHA256 remains `f0d3d933109f3572023af677729feef4db6e432657c7b7095b5bae05f2821dc7`. Candidate `712f1840`, the partial runtime manifest and prior bootstrap results are preserved. The accepted Supervisor configuration's current hash still matches `end_to_end/runtime_authority.json`. Its V1 bindings remain `/Users/kevinkeaveney/Dev/claude/flex/database/flex_complete_database.db` and `/Users/kevinkeaveney/Dev/claude/flex/database/wt_ops_v2.db`; only path existence/resolution and configuration identity were checked, not database contents.

| Required surface | Existing owner / authority | Finding |
|---|---|---|
| Core base token/funding/persistence tables | `PumpFunCurveListener._ensure_db_with_connection` | Creates its declared tables; not a complete fresh-install schema |
| Core incremental migrations | `schema_init.ensure_schema`; CLI `scripts/ensure_schema.py` delegates to it | Migrates pre-existing base objects; logs missing tables and can fast-return on its two-column sentinel |
| First observed valuation fields | `TokenPriceService._store_snapshot` | Lazy observation-writing path, not schema-only bootstrap |
| Price service support schema | `TokenPriceService._ensure_tables_with_connection` | Circuit-breaker and pool-account schema only; no first-observation columns or peak base table |
| Creator resolution | `creator_resolution_queue.initialize_schema/ensure_schema` | Queue columns/indexes repeat-safe; queue CREATE signature column is not a token_analysis column |
| Migration/birth persistence | Listener base owner | `migration_persist_queue`, `birth_persist_queue` and funding state created |
| Webhook birth retry | Listener `_ensure_webhook_birth_queue_schema` | Dedicated owner creates retry envelope; repeat-safe |
| Migration Core→OPS intent | `migration_walkback_handoff_retry.ensure_schema` | Core handoff schema repeat-safe |
| Canonical CREATE delivery control | `walkback_create_handoff.initialize` | Separate explicit one-time migration requiring transaction, epoch and authority budgets; not a generic idempotent Core bootstrap. Not invoked or assigned invented activation state |
| Accepted monitor lifecycle evidence | OPS `operator_lifecycle_projection.ensure_schema` | Owns `operation_monitor_facts/observations/mode_transitions`; do not create substitutes in Core |
| Acquisition request/retry checkpoint envelope | `MonitorQueue` → `EvidenceIntakeQueue`; `OPERATION_MONITOR_QUEUE_PATH` | Durable filesystem queue/ACK/retry/backoff authority, not a missing Core table. Unfinished account-budget/checkpoint acceptance remains separate |
| Birth quarantine | Installed `birth_retry_quarantine.ensure_schema` | Owner exists in installed listener but is missing from frozen listener package; independent packaging HOLD, not used to fabricate Core objects |

Owner file hashes and the retained source comparison are recorded in `qualification.json` and `source_comparison.json`.

## FIRST_OBSERVED_AT_CONTRACT

The actual schema creator is conditional logic **inside `_store_snapshot`**. It adds `first_observed_mc`, `first_observed_price`, `first_observed_at INTEGER`, source and confidence only when persisting a qualifying positive valuation. Its timestamp is the captured price timestamp, or current observation time for stale/cache input. It is a truthful valuation observation, not an interchangeable birth timestamp or default backfill value.

The schema-only price owner does not add these fields. This is true in all three inspected immutable authorities: FLEX_STABLE_V2, accepted price-worker `71b6e338cfb54057ec4984d41bfbe02477c90dba`, and current DEV004. It is a pre-existing fresh-install contract gap, not a newly demonstrated DEV004 code regression.

After executing all selected schema owners, the ordinary accepted compact-history reader still raises `COMPACT_HISTORY_TIMESTAMP_SCHEMA`: its immutable baseline requires `[first_observed_at, analyzed_at, created_at]`, while fresh Core lacks `first_observed_at`. No price observation was invented or persisted merely to force lazy DDL. No timestamp substitution or classifier change was attempted.

## CANONICAL_PRICE_SCHEMA

`TokenPriceService._ensure_tables_with_connection` successfully creates `circuit_breaker_state`, `token_pool_accounts` and their declared indexes/migrations. It does **not** create `token_market_cap_peaks`. `_store_snapshot` writes that table; `schema_init` only alters/updates an assumed existing table. The inspected baseline/accepted-price/current schema owners all lack its CREATE path.

No accepted schema-only peak-base initializer was established in the bounded retained-source lookup. This does not prove that no historical installer ever existed. It establishes that the tested accepted composition cannot initialize the table from empty. Restoring generic dense price history or constructing a table from V1 metadata would exceed authorization and is not a solution here. OPS monitor facts and filesystem acquisition state are separate authorities and remain untouched.

## CREATOR_AND_MIGRATION_SCHEMA

The existing base/migration/queue owners create `pf_ws_creator`, `earliest_tx_creator`, `creator_mismatch`, `created_at`, `migrated_at` and migration metadata. Funding/resolution/persistence queues include their own CREATE signatures and retry state.

However, fresh `token_analysis` itself lacks **`create_tx_signature`**. Preparing the existing canonical Core reader query (`SELECT pf_ws_creator, earliest_tx_creator, create_tx_signature, migrated_at FROM token_analysis WHERE mint=?`) fails with `no such column: create_tx_signature`. This is a third exact missing base-schema contract. A signature column in a queue does not supply canonical CREATE authority in the token table. No creator, signature, provenance or confidence was fabricated.

## BIRTH_RETRY_QUARANTINE_SCHEMA

The existing listener `_ensure_webhook_birth_queue_schema` is tested directly on the disposable Core and then repeated: same schema, zero rows, no provider work. Core birth/migration persistence and migration-handoff retry tables also remain present.

Quarantine packaging and Potential Operations lineage repeat-safety are independent: neither is invoked by the Core owner chain, and neither caused its missing first-observation/CREATE/peak schema. They remain separate existing HOLDs. The installed quarantine source was not copied or dynamically substituted, and no explicit CREATE-delivery activation epoch was invented.

## FRESH_CORE_BOOTSTRAP

Actual accepted procedure order tested:

1. Listener `_ensure_db_with_connection` on the disposable connection.
2. Price service `_ensure_tables_with_connection` on that connection.
3. Existing `schema_init.ensure_schema(explicit_path)`.
4. Creator-resolution `initialize_schema(explicit_path)`.
5. Migration-handoff retry `ensure_schema`.
6. Separate webhook-birth retry owner, also invoked twice.

Only these owner procedures supplied schema SQL. The schema method bodies are executed unchanged; no listener/price-service constructor, subscription, transport, observation writer or background monitor is activated. Explicit `DB_PATH`, `WT_OPS_DB_PATH` and `RPC_METRICS_DB` bindings stay in the disposable directory; SQLite audit guards reject any other database authority. No external database open was attempted in the final qualification.

The OPS companion is a copy of the previous small disposable startup fixture (including its fixed test cursor), not V1; OPS was not rebuilt and its completed startup validation was not repeated. Core remains zero-row after all schema checks. The migration helper logs assumed-but-missing peak/Core-Watchtower tables and later fast-skips because its sentinel columns exist; neither return code nor repeatability certifies completeness.

## REPEAT_STARTUP

Both full selected-owner passes produce identical SQLite object definitions; the separate birth-retry owner is also repeat-safe. Creator-resolution `schema_ready` is true, and foreign-key checks return no violations. These results qualify the tested owners' repeat behaviour, not a complete fresh application startup.

Listener Core readiness and compact creator-history readiness remain HOLD at the demonstrated missing contracts. Walkback's configured Core binding remains correct, but a canonical CREATE read cannot be prepared against this fresh Core. Live entrypoints/workers were not activated. Existing scoped resolver/Walkback startup evidence remains accepted; it does not certify the missing reader fields.

## PROVIDER_FREE_TEST_RESULTS

- Existing base, price-support, migration, creator queue and handoff owners execute without replacement SQL.
- Same owner sequence repeated: schema definitions identical; webhook retry repeat also exact.
- Creator-resolution schema readiness PASS; foreign-key check PASS; all user tables remain empty.
- Actual compact-history reader HOLD: missing `first_observed_at`.
- Actual canonical-reader statement preparation HOLD: missing `token_analysis.create_tx_signature`.
- Canonical Core peak table still absent after the accepted price schema initializer.
- Baseline/accepted-price/current source comparison confirms lazy first-observation DDL and absent peak creation in the tested owners.
- No provider requests, live sockets, service actions, observation persistence, production database access or source changes. urllib3's blocked local IPv6 capability probe is not provider acquisition.
- 100 creators, 616 Watchtower routes, freshness, projection/outage recovery, Birdeye provenance and ATH tolerance are reused from retained evidence; no large matrix repeated. Runtime source and original Core/OPS/history hashes remain exact.

Fresh Core size: **258,048 bytes** after the primary owner sequence; **266,240 bytes** after the dedicated birth retry schema. Peak sampled primary WAL: **712,792 bytes**. Existing qualification caps are 16,000,000 bytes per file and 3,840 SQLite pages; no production growth guarantee is inferred. Price-support/telemetry/cache schema belongs to the invoked existing owners/import dependencies; no unrelated history or raw acquisition data was generated. No ad hoc table, column, index or trigger was issued.

## REMAINING_STARTUP_BLOCKERS

The accepted current initialization chain lacks an explicit fresh Core base contract supplying:

1. `token_analysis.first_observed_at` and the accepted first-observation column group before any observation is acquired.
2. `token_analysis.create_tx_signature` before canonical CREATE readers run.
3. The canonical `token_market_cap_peaks` base schema before existing price writers/migrations run.

These cannot be certified by treating the live V1 schema as a fresh installer, invoking a data writer as a migration, or inferring DDL from existing data. Independent lineage, quarantine packaging, listener lock/secondary DB and dormant component gates remain unchanged. No unrelated repair was attempted.

## NEXT_ACTION

**HOLD under the requested stop rule.** Supply a versioned accepted base-schema initializer/artifact covering the three exact contracts, or separately authorize a minimal explicit Core bootstrap-owner correction. No such correction is implemented here. Preserve canonical timestamp/provenance semantics and the existing schema; do not manufacture observations or change classifier logic. V1 remains authoritative and unchanged.

`RUNTIME_DEPLOYMENT_REQUIRED=false` — schema-owner qualification/documentation only, no executable change or deployment.
