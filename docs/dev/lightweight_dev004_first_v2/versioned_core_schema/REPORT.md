# DEV004 versioned Core base schema

`DEV004_VERSIONED_CORE_SCHEMA_STATUS: PARTIAL — authorized three-gap bootstrap PASS; full fresh compact-reader compatibility HOLD at an existing missing index.`

## EXACT_SCHEMA_DELTA

Source candidate **`ac37ec070e40014b5e61d456b225712c357fafe1`**, continuing the existing DEV004 worktree/source composition. New executable file only: `runtime/creator_resolution/src/ops/dev004_core_base_schema.py`. No existing runtime owner or production initialization path is modified or automatically hooked.

Authoritative V1 metadata was obtained by read-only `sqlite_master`/PRAGMA inspection, with no data selected. Exact metadata and SQL are in `schema_authority.json`:

- `token_analysis.first_observed_at INTEGER`: nullable, no default, no added key/constraint.
- `token_analysis.create_tx_signature TEXT`: nullable, no default, no added key/constraint.
- `token_market_cap_peaks`: verbatim accepted CREATE SQL, eight columns with exact types/defaults, mint TEXT PRIMARY KEY and FK to `tracked_tokens(mint)`. Its only accepted index is SQLite's automatic primary-key index. No new explicit index is invented.

Existing token_analysis columns and user rows are preserved; no backfill, confidence change, timestamp substitution or historical data copy. Only the table and its automatic index appear as new SQLite objects; two missing columns are appended. Existing compatible objects are validated, not recreated. Incompatible column type/default/nullability/CHECK/UNIQUE semantics, peak SQL/columns/FK/PK/index/trigger definitions or completion identity fail closed.

## SCHEMA_VERSION_CONTRACT

Version `dev004.core-base.v1`. Explicit API/CLI opt-in, an existing regular single-link Core file, and an explicitly selected symlink-free `/private/tmp/dev004-*` disposable root are required. Main input is capped at32MiB. The function refuses a process without an OS single-file limit at or below64MiB; the standalone CLI imposes that limit. Production paths, aliases/hardlinks and undeclared roots are rejected. There is no default production path or ambient DB_PATH lookup.

The existing listener owner supplies token_analysis first; the existing `PriceWorkerRegistry` owner supplies `tracked_tokens`. The step checks that prerequisite and never creates a substitute parent. Metadata validation and nullable ALTERs/peak DDL use one `BEGIN IMMEDIATE` transaction. No SELECT of historical rows or full-table scan is performed by this initializer. Lock admission uses the existing SQLite busy timeout (1second); progress cancellation is best effort, not a claimed absolute filesystem/lock-duration bound.

Successful completion is recorded **after commit** in a contract-digest/file-identity sidecar (maximum4096bytes), written via fsync and atomic replace. A fixed temporary sidecar has the same bound. No fourth schema object/version table is introduced; `PRAGMA user_version` is preserved, including an explicit849 test. A crash before commit rolls back all DDL. A crash after schema commit but before marker publication is repaired by revalidating the compatible schema and publishing the marker. A valid repeat performs no DDL or marker rewrite. Bad/stale markers fail closed.

## FRESH_CORE_INITIALIZATION

A genuinely empty Core was prepared using the already-qualified existing base/price/queue owners, then the existing price registry owner initialized the FK parent without starting a price worker. All tables were empty. The new versioned step creates exactly the missing columns and peak table/automatic PK index, with no historical token rows required.

Source/definitions preserve FLEX_STABLE_V2, accepted price-worker authority, compact-history and deployed BIRDEYE contracts. The new module is opt-in DEV-only and has no provider imports, worker startup or production hook.

The standalone CLI was exercised twice against a disposable pair: INITIALIZED then ALREADY_INITIALIZED. It requires `--core`, `--disposable-root`, and `--allow-disposable-v2`; it is not a production deployment command.

## REPEAT_AND_RESTART_SAFETY

Twenty focused groups pass: exact fresh delta/repeat, compatible unmarked adoption without DDL, compatible partial completion, wrong column type/default/CHECK/UNIQUE and peak definition rejection, missing-parent rejection, explicit opt-in, preservation of existing rows/NULLs, physical process crashes after a column/before commit/after commit/before marker replacement, failed COMMIT rollback/retry, existing user_version preservation, marker mismatch and hardlink rejection, and canonical/price SQL/FK checks.

Actual process exit71 was injected in four transaction/marker windows. Each restart converges to one exact table/PK and one marker; subsequent repeats are inert. Pre-existing synthetic rows remain unchanged and new nullable fields remain NULL. No history is copied or reconstructed.

## CREATOR_HISTORY_COMPATIBILITY

The prior `COMPACT_HISTORY_TIMESTAMP_SCHEMA` failure is resolved: all required timestamp columns now exist, and canonical/history SQL prepares correctly. The accepted lightweight reference fixture is adopted with **byte-identical Core content**, and its actual compact-history reader passes through ordinary Walkback packaging. No 100-creator matrix is repeated; existing semantic qualification is reused.

**Fresh fixture HOLD:** the actual compact reader now reaches its next mandatory guard and raises `COMPACT_HISTORY_UNINDEXED_DETAIL`. The fresh existing base-owner chain has no usable pf_ws_creator index; its detail plan is `SCAN token_analysis`. The accepted reference fixture has `CREATE INDEX idx_ta_pf_ws_creator ON token_analysis(pf_ws_creator)`. This index is outside the explicitly authorized three gaps, so it was not added. Classifier logic and bounds were not relaxed and no scan was executed to bypass the guard. This is a concrete remaining bootstrap dependency, not a regression caused by changing classification semantics.

## CANONICAL_CREATE_COMPATIBILITY

Canonical CREATE query preparation passes on fresh initialized Core, followed by synthetic named-column persistence/readback of creator and CREATE signature. No real identity/provenance was invented. Existing creator-resolution and migration/handoff schemas remain intact. The separately qualified resolver and Walkback startup boundaries were rerun only for this affected schema pair: both reach ready and stop before their first processing cycle. No subscriptions, live workers or provider requests were activated. Full listener runtime isolation remains its separately recorded gate.

## PRICE_LIFECYCLE_COMPATIBILITY

Price upsert SQL prepares with foreign keys enabled and the accepted tracked_tokens parent. The actual accepted `TokenPriceService._store_snapshot` was exercised with two synthetic Birdeye-labelled valuations: first observation remains locked at250 and timestamp1792747001, while a later200 observation cannot lower the peak or replace its timestamp. FK check passes. No Birdeye request or credential value was used.

That existing price consumer retains its own already-accepted lazy first-observation metadata behaviour; those consumer-owned fields are not new initializer DDL. Exact initializer object delta was checked before consumer tests. OPS lifecycle facts, finalization identity, opening provenance and historical ATH evidence remain untouched; existing accepted provenance/0.1% tolerance evidence is reused. No raw/dense price history is restored.

## PROVIDER_FREE_TEST_RESULTS

All20 focused bootstrap groups, standalone CLI repeat, affected resolver/Walkback startup boundaries, canonical persistence and actual price persistence PASS. Accepted lightweight reference adoption/compact read PASS with Core byte-identical. Fresh compact read HOLD at the exact missing creator lookup index above. See `result.json`, `extra.json`, `startup_cli.json`, reader results and `consumer_result.json`.

Original reference Core/OPS/history and executable runtime corrections remain unchanged. No broad creator/route/Deep/freshness/recovery/price equivalence matrix was repeated. No production database writes, source changes, services, provider calls, live WebSockets, historical deletion or cutover.

Maximum new-file design bound64MiB (OS enforced), input Core32MiB, marker/temp4096bytes each. Qualification uses16,000,000-byte OS file caps and small fixtures, never a production clone. Nullable metadata ALTERs and one empty eight-column table introduce no historical retention. Raw retention=false; new unbounded growth paths=0. Existing downstream runtime database growth remains the separate G5 qualification, not a guarantee supplied by this bootstrap. No absolute WAL or writer-lock-duration claim is made.

## REMAINING_STARTUP_BLOCKERS

The three authorized schema gaps are implemented and qualified. Fresh compact history still requires the accepted pf_ws_creator lookup index. No unrelated schema/index, lineage change, quarantine packaging repair or infrastructure work was introduced. Existing broader V2 cutover gates remain separate and unchanged.

## CANDIDATE_SHA

`ac37ec070e40014b5e61d456b225712c357fafe1` — new explicit DEV module plus exact V1 schema authority. Qualified but not deployed; no production activation authorized. The report-containing commit retains this exact executable source.

## NEXT_ACTION

Qualify the accepted `idx_ta_pf_ws_creator` bootstrap dependency as a separately authorized narrow follow-up. Do not change classifier semantics or expand the schema scope here. Keep V1 authoritative. **Scoped three-gap implementation PASS; full requested fresh-reader startup remains HOLD.**
