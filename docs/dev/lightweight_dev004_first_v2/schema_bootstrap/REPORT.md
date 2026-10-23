# DEV004 fresh V2 schema bootstrap qualification

`DEV004_SCHEMA_BOOTSTRAP_STATUS: PARTIAL — existing OPS startup schema owners PASS; complete fresh Core/OPS bootstrap HOLD.`

Executable source remains `712f18400508a7681e3cea78e79cf84c0be8b666`; no runtime source changed. FLEX_STABLE_V2 and accepted corrections/partial composition remain intact. Existing reference Core/OPS/history hashes are unchanged. Tests use newly empty databases and actual accepted owner functions, not copied production databases, extracted SQL or hand-written tables. V1 remains authoritative.

## MISSING_STARTUP_OBJECTS

| Missing contract | Existing owner and consumer | Result |
|---|---|---|
| `wt_watchtower_candidates`, `ix_wt_candidates_status_created` | `watchtower_candidates.ensure_schema`; Walkback startup/candidate readers | Created; startup validator PASS |
| `wt_unknown_infrastructure_registry`, `ix_wuir_eligible` | `attribution_outcome.ensure_schema`; Walkback startup/outcome readers | Created; startup validator PASS |
| `wt_confirmed_treasury_webhooks` plus treasury tables/indexes | `treasury_bank.ensure_schema`; startup/treasury authority | Created; treasury validator PASS |
| Anchor columns/log | `anchor_reconciliation.ensure_schema`; creator/CREATE anchor recovery | Created; validator PASS |
| `wt_create_event_ledger`, conflict/pending tables/indexes | `create_event_ledger.ensure_schema`; canonical CREATE/creator recovery | Created; validator PASS |
| Fingerprint evidence/health/cluster schema and unique indexes | `operation_fingerprint_drift.ensure_schema`; completion/fingerprint consumers | Created; validator PASS |
| `token_analysis.first_observed_at` | Not supplied by executed accepted Core owners; required by immutable compact creator-history timestamp schema | Actual reader fails `COMPACT_HISTORY_TIMESTAMP_SCHEMA`; HOLD |
| `token_market_cap_peaks` | Listener/price/lifecycle/API readers require it; `schema_init` assumes the base table already exists and only migrates it | Absent after bootstrap; complete base-schema owner contract not established; HOLD |
| `wt_sub_provisioners`, `watchtower_infra_events` | Core `watchtower_init.ensure_watchtower_tables` is an existing owner; `schema_init` assumes these tables exist | Missing in selected minimal Core bootstrap. Not declared obsolete. Their owner includes broader static seeding/updates and was not invoked to bypass the demonstrated Core reader blocker. Active requirement beyond the migration notes is not inferred. |
| Assessment lineage/bindings | `generic_living_lineage_metadata.ensure_lineage_schema`, explicitly a disposable qualification helper | First pass creates objects; second pass raises `table potential_operation_assessment_lineage already exists`. No repeat-safe complete runtime bootstrap contract established. |
| Birth quarantine schema | Installed `Production/flex-listener/src/core/birth_retry_quarantine.py:ensure_schema` | Installed owner exists; absent from frozen `runtime/listener` package. Not loaded/copied or replaced with DDL; HOLD |

The original missing OPS objects were reproduced from the prior partial-manifest failure and recreated on empty databases through their owners. They are operational contracts, not obsolete tables. No provider evidence was used or acquired.

## SCHEMA_OWNER_AUTHORITY

`bootstrap_manifest.json` pins all invoked owner hashes and the immutable source. Core ownership is explicit: listener `_ensure_db_with_connection` creates its base tables, `schema_init.ensure_schema` applies its existing migrations, creator queue initializer owns resolution state, and migration handoff retry owns its Core intent table. OPS ownership remains queue/candidate/treasury/outcome/anchor/CREATE/fingerprint/Deep/lineage owners. Operator-model and Potential Operations initialization were tested separately through existing APIs, including the actual shared-writer operator initializer; no replacement writer or background service was introduced.

The listener schema method has no self-state/provider dependency and was called directly with the disposable connection, without constructing a live listener. Every schema statement came from an owner procedure. Core schema migrations log missing dependencies and return; a successful function return is not accepted as proof of complete schema readiness.

## BOOTSTRAP_ORDER

Tested minimal Core order: listener base owner → existing Core migration helper → creator-resolution initializer → migration handoff retry owner. This initializes the tested queues but exposes a missing **base Core contract**, not merely a last-stage index order issue. The migration helper's `pf_ws_creator`/`creator_mismatch` sentinel makes its second call skip despite remaining absent base tables. No sentinel rewrite or ad hoc table/column repair was attempted.

Tested OPS order: Walkback queue → Watchtower candidates → treasury → attribution outcome → anchor reconciliation → CREATE ledger → fingerprint drift → Deep evidence → lineage quarantine; explicit commits follow each owner. All six direct Walkback startup validators then return VALID. Schema definitions are unchanged on a second invocation of this sequence.

Supplemental operator/Potential order: operator model via `OperatorWriter.initialize_schema` → Living schema → qualification lineage schema. First pass succeeds; repeated lineage helper fails. A one-shot disposable helper is not silently promoted to an idempotent runtime initializer. Qualification stopped at this contract; no IF NOT EXISTS patch, table dropping or replacement bootstrap was added.

## CORE_V2_SCHEMA_RESULT

Owner calls return and the selected minimal schema is idempotent by object-definition comparison, with zero user rows before startup checks. Nevertheless **Core readiness HOLD**: fresh schema lacks `first_observed_at`, and the actual accepted compact reader rejects it. The immutable history requires `[first_observed_at, analyzed_at, created_at]`. No observation timestamp or historical evidence was invented. The price-peak base table remains absent; migration notes also identify the two Core Watchtower tables above.

An initial isolated probe used the recorder's relative default and created an extra 40,960-byte telemetry database inside that scratch directory. Final qualification explicitly sets the existing `RPC_METRICS_DB` binding to Core, producing only the declared Core/OPS databases. This is a required configuration binding, not a source correction. No V1 database was accessed.

## OPS_V2_SCHEMA_RESULT

**PASS for the six active Walkback startup schema contracts** on truly empty OPS: queue, treasury, canonical outcome, anchor, CREATE ledger and fingerprint validators all pass. Deep and lineage-quarantine schemas also initialize. This closes the previously recorded missing OPS startup-object gap.

The broader operator/Potential first pass creates its owned schema and six immutable audit/history/review triggers. Required object lists are in the owner results/final metadata. Complete repeat-safe Potential bootstrap remains HOLD at the one-shot lineage helper; global discovery acceptance is not tested or claimed. Schema presence does not replace the existing operational reference seed.

## LISTENER_STARTUP

Actual listener schema initialization PASS on the empty Core. Full listener readiness HOLD because its accepted compact-history/price dependencies are incomplete. The previously recorded process-global listener lock and separate creator-database authority remain unchanged; no live entrypoint, lock or WebSocket was activated. This task did not repair those unrelated runtime-composition boundaries.

## RESOLVER_STARTUP

Actual `creator_resolution_worker.run_loop(once=True)` reaches startup-ready and exits with the stop flag set before its first processing cycle. No acquisition, enqueue sweep or live queue work occurred. Existing Core creator funding/resolution/retry and handoff tables are present.

## WALKBACK_STARTUP

Actual `walkback_worker.run_loop()` passes all startup validators and maintenance with zero pending jobs, then is stopped at its first `cycle_started` trace boundary before heartbeat/investigation processing. The test does not mock schema validation or invoke provider acquisition. The accepted projection-recovery startup creates its fixed cursor through its own code. Full daemon activity is not activated or claimed.

## UNEXPECTED_SCHEMA_OBJECTS

Final guarded qualification opens only declared Core/OPS paths; a SQLite audit hook independently enforces this even for captured connector aliases. No denied external DB access, production path, provider request, subprocess or unexpected thread succeeded. Existing reaper/watchdog threads and the one expected shared-writer initialization thread are isolated in child processes. urllib3's local IPv6 capability probe is blocked; it is not provider acquisition.

No unrelated history tables or acquisition rows were generated. Telemetry/cache schema created during listener dependency import belongs to the existing recorder/cache owners and is zero-row. OPS operator/governance/manual-registry objects belong to the explicit operator initializer. The object inventories make this dependency-owned schema overhead visible; no schemas were synthesized from table names.

Foreign-key checks on Core/OPS return no violations. Final metadata includes six immutable OPS triggers and one OPS view. These checks cover empty/qualification state, not seed relationship equivalence. Core birth/migration retry and migration handoff schema exist; installed birth-quarantine packaging is still missing. No activation epoch, retry state or quarantine provenance was invented.

## STORAGE_AND_WAL_BOUNDS

| Phase | Core bytes | OPS bytes |
|---|---:|---:|
| Empty guarded primary owner bootstrap, before startup records |225,280|348,160|
| Additional operator/Potential first-pass schema |225,280|634,880|
| After bounded startup checks |233,472|655,360|

Before startup, all user tables are empty: these allocations are schema/index overhead, not historical bulk. Startup adds exactly one Core worker-heartbeat row and one OPS projection-cursor row; no attribution or operation membership is invented. Final allocation equals logical file size, 4,096-byte pages, freelist zero. Peak sampled primary-bootstrap WAL: Core 605,672 bytes; OPS 1,100,072 bytes. Final WAL files are zero. These are observations, not guaranteed production WAL bounds; OS file cap 16,000,000 bytes and fixture SQLite page cap 3,840 guard the qualification. No VACUUM or explicit checkpoint ran. No file approaches 500,000,000 bytes; no new unbounded growth path/raw retention was introduced.

## PROVIDER_FREE_TEST_RESULTS

- Primary Core/OPS owner-call sequences and repeated schema comparison complete; all six OPS startup validators VALID.
- Empty DB foreign-key checks pass; required owner-created indexes/triggers/views are recorded.
- Resolver and Walkback actual startup boundaries pass, stopped before processing; no live workers/listeners activated.
- Actual compact reader reproduces a fresh-Core compatibility failure: missing `first_observed_at`.
- Potential lineage second invocation reproduces a one-shot/non-idempotent initializer boundary.
- Installed birth-quarantine owner exists but is not packaged at the immutable candidate root; no substitute owner was used.
- No production database access/writes, provider calls, source changes, services, live WebSockets, historical deletion or cutover.
- 100 creator, 616 Watchtower, 11 frozen, 26 Deep, freshness, recovery and price qualifications are reused. Runtime source and accepted seed/history hashes remain unchanged. The new failure concerns a freshly generated schema; it does not invalidate the accepted reference-fixture results.

## REMAINING_RUNTIME_BLOCKERS

1. Accepted Core **base-schema bootstrap authority** must supply the compact timestamp schema, including `first_observed_at`, and canonical price-peak schema; the tested initializer chain does not.
2. Complete Potential Operations schema bootstrap needs an existing repeat-safe contract; the invoked qualification-only lineage helper is one-shot.
3. Installed birth-quarantine owner is missing from the frozen listener package.
4. Previously documented listener lock/secondary DB and dormant component composition remain separate. No infrastructure restoration or reference expansion was attempted.

## NEXT_ACTION

**HOLD. Identify and qualify the existing accepted Core base-schema owner or versioned bootstrap artifact that supplies `first_observed_at` and the canonical price table.** Do not manually add the column/table or alter classifier semantics. Preserve the exact lineage and quarantine packaging holds above. OPS startup-object qualification is complete and should not be repeated. Runtime deployment is not authorized; V1 remains unchanged.

`RUNTIME_DEPLOYMENT_REQUIRED=false`: provider-free schema-owner qualification and documentation only, no executable correction or deployment. Runtime startup verdicts are bounded to the stated test boundaries.
