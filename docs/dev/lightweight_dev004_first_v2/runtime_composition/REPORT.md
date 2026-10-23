# DEV004 reproducible runtime composition

`DEV004_RUNTIME_COMPOSITION_STATUS: PARTIAL — compact-history packaging PASS; full startup composition HOLD; QUALIFIED_NOT_DEPLOYED.`

## SOURCE_COMPOSITION

Source payload `712f18400508a7681e3cea78e79cf84c0be8b666` contains `54ece3eb` and all accepted corrections in one immutable Git commit. Three package trees and selected entrypoints are pinned in `runtime_manifest.json`; immutable FLEX_STABLE_V2, price-worker `71b6e338`, lock authority, original seed/history hashes and accepted freshness mapping are preserved. No V1 or installed package changed. No large functional matrix was rerun.

## WALKBACK_IMPORT_BOUNDARY

The ordinary Walkback worker itself imported before the correction; its evaluator silently lacked the compact-history path. A direct package import of `src.ops.dev004_compact_creator_history` reproduced ModuleNotFoundError. The full investigation harness previously injected the creator package evaluator through sys.modules. Cause: missing helper packaging and missing qualified evaluator hook, not a circular dependency or wrong Python path.

## MINIMUM_CORRECTION

Exactly two DEV executable files: copy the accepted helper byte-for-byte into Walkback; replace only `evaluate_launcher_profile` with the accepted creator-package function. The evaluator AST and helper bytes are exact. All other Walkback functions/classes, including its existing schema contract, remain unchanged. Existing flag-off behaviour and opt-in compact semantics are retained. One representative frozen creator profile and actual queue classification match V1 through ordinary package imports with no evaluator/module substitution; only the previously frozen observation clock is pinned. Full 100/616/freshness/price matrices remain accepted by source identity, not re-executed.

## FROZEN_RUNTIME_MANIFEST

`runtime_manifest.json` is a frozen **partial/HOLD** composition record, not a golden baseline or ready-to-run deployment specification. It pins the immutable corrected source, entrypoints/package trees, environment/lock authority, seed/history/mapping digests, shared disposable Core/OPS paths, explicit opt-ins, credential names only, source budget defaults, queue/handoff ownership, receipt/quarantine authority, expected stopped states and qualified versus unqualified storage limits. Incomplete fields are explicitly unresolved rather than invented. Full API/UI/monitor dependency closure, dormant bridge entrypoint, secondary creator DB authority and production storage/activation bindings remain unqualified. No historical runtime roots were restored.

## LISTENER_STARTUP

Import PASS using the actual listener package and existing log-path configuration directed into the disposable area. Full startup HOLD: `main()` uses the process-global `/tmp/pumpfun_curve_listener.lock`; the filesystem guard blocks its parent mutation before opening/truncating or locking anything. No live listener lock was touched. `CREATOR_DB_PATH` also defaults to a separate package-local `pumpswap_tokens.db`, not the Core/OPS pair; it was never opened. The required authority must be pinned or the accepted disabled-path contract demonstrated before full composition PASS. Neither lock nor database logic was changed in this compact packaging task.

## RESOLVER_STARTUP

Actual import and `run_loop(once=True)` startup PASS on a disposable pair, with `_STOP` requested before the first processing cycle. Existing schema initialization and startup-ready heartbeat execute; no queue work, transport or watchdog processing cycle is claimed. This proves that boundary, not a running production daemon or creator acquisition lifecycle. All child processes exited cleanly.

## WALKBACK_STARTUP

Import PASS and compact evaluator selection PASS without sys.modules injection. Actual `run_loop()` fails closed at its existing startup schema validator against the original copied reference fixture: missing `wt_unknown_infrastructure_registry`, `wt_watchtower_candidates`, `ix_wuir_eligible`, `ix_wt_candidates_status_created`.

One bounded diagnostic on a **separate disposable copy** invoked only the two existing schema owners (`watchtower_candidates.ensure_schema`, `attribution_outcome.ensure_schema`), with no new schema design or historical population import. The Walkback and outcome validators then pass, confirming that first boundary is a fixture bootstrap gap. Metadata-only validation identifies the remaining missing contracts:

- Treasury: `wt_confirmed_treasury_webhooks`.
- Anchor: `wt_anchor_reconciliation_log` (mint/recovered_signature/recovery_source/recovery_timestamp).
- CREATE: `wt_create_event_ledger` (creator/mint/signature/source).
- Fingerprint: `operation_fingerprint_drift_evidence`.

No further schema repair or investigation was attempted. Full Walkback startup/restart cannot be claimed until the accepted schema-owner bootstrap is qualified on the lightweight fixture. No reference seed or production schema was changed.

## POTENTIAL_OPERATIONS_IMPORTS

Actual `living_potential_operations`, generic pipeline and forward-cutover imports PASS. The accepted two-candidate freshness module and explicit `LIVING_V2_FRESHNESS_MAPPING_PATH` reference are pinned. No scheduler, discovery run, assessment publication or promotion was activated; the existing global-discovery acceptance gate remains separate.

## PRICE_AND_API_COMPONENTS

Actual imports PASS: price_service, operation_monitor_worker/service, accepted DEV opening policy, price_api and internal_api. Existing stopped services remain stopped. Monitor/bridge/API/UI full application launch, templates, routes and complete accepted dormant source composition remain HOLD; an import alone does not establish that functionality. Missing historical price roots remain absent and were not restored. Accepted Birdeye-only authority, price provenance and ATH 0.1% contract are referenced unchanged.

Three imports encountered a blocked bind in urllib3's `_has_ipv6` capability check. The traced call is an expected local IPv6 availability probe, not provider acquisition or a service listener. It is blocked and urllib3 falls back normally. No defect or additional infrastructure repair is inferred. Only known `db-conn-reaper` and `db-wal-watchdog` import threads were allowed; unexpected threads/subprocesses/transports were blocked. No provider request or live WebSocket was made.

## DATABASE_BINDINGS

Eighteen imports in two fresh-process rounds used the same copied Core/OPS pair under `/private/tmp/dev004-runtime-composition/shared-session`. Actual listener/resolver DB_PATH and Walkback LIVE_DB_PATH/OPS_DB_PATH agree with those bindings. SQLite opens outside the disposable root were rejected. A secondary creator-pipeline default is explicitly unresolved, never silently redirected into Core. Original seed Core/OPS/history hashes remain unchanged; their rows were not expanded or minimized. An independent schema-only diagnostic copy is not a new authoritative seed.

## PROVIDER_FREE_TEST_RESULTS

- Original missing-helper import reproduced; corrected direct helper/evaluator import passes.
- Accepted evaluator AST/helper bytes exact; only evaluator changed among existing Walkback functions/classes.
- One exact frozen profile and actual classification pass through ordinary Walkback packaging.
- Nine selected modules imported twice in fresh processes (18/18 successful imports), same database pair, clean process exits. Full daemon restart is not claimed where startup is blocked.
- Resolver startup-ready/stop boundary passes; actual listener and Walkback startup fail closed at the exact boundaries above.
- Potential Operations and available price/monitor/API imports pass; no production writes, provider calls, service actions or live sockets.
- Qualification fixtures use OS file cap 16,000,000 bytes and SQLite 3,840-page cap; no full production clone, raw provider retention or new file growth path. These are test bounds, not long-term production storage certification.

## REMAINING_CUTOVER_GATES

The compact import boundary is closed. G1 remains HOLD for exact fixture startup contracts, isolated listener launch/secondary creator DB binding and complete accepted dormant API/UI/bridge composition. G2 reference authority/four role cases, G3 global discovery, G4 unfinished Birdeye budget/checkpoint recovery, G5 sustained growth limits and G6 final state reconciliation/cutover/rollback remain unchanged. Prior accepted attribution, recovery and price milestones stay closed.

## NEXT_ACTION

Qualify only the **existing schema-owner startup bootstrap** on a disposable V2 pair for the four enumerated remaining contracts, preserving all reference rows. Keep the listener's global-lock/secondary-DB and dormant composition gaps separately explicit. Do not deploy, restore historical runtimes or treat this partial manifest as cutover authority. V1 remains authoritative.
