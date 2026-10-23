# DEV004 Bounded Discovery Generation Capture

DEV004_DISCOVERY_GENERATION_CAPTURE_STATUS: PARTIAL — bounded quiet two-store fixture capture/mutation diagnostics PASS; complete runtime generation authority HOLD.

GO_HOLD_DECISION: HOLD. No accepted durable cross-owner capture/publication fence or complete bounded source-change reconciliation owner was established. Live connection-local mutation watching can invalidate an in-progress capture, but cannot certify restart or atomically order all Core/OPS mutations against publication. The existing grouping continuation remains feasible and unchanged.

## Frozen authority

Inspection revision 2d530a6a9b6e1b3b92b718272d4b19f948d230e5; executable candidate 506fe25c941eadf379ca98e4f0d42c1a2486dc9c. FLEX_STABLE_V2=2d825bbcf27f802bd0bfcef90cedeba43cc968eb. Verified relevant startup, freshness, roles, creator graph, Walkback recovery and credential correction ancestry. Reused all15 passing normalized-input continuation controls and prior19 discovery controls; grouping/ranking predicates not modified or rerun. No runtime source changes.

## CORE_OPS_GENERATION_AUTHORITY

Current authoritative source paths remain Core database/flex_complete_database.db and OPS database/wt_ops_v2.db; they were NOT opened by this milestone. Their accepted owners commit independently. OperatorResolver.evaluate opens separate read-only OPS and Core connections. EmergingOperatorService reads discovery/canonical evidence, then operation_confirmation reads another OPS connection; reconciliation_metadata may return a time-cached result or reopen OPS and reconstruct profiles/populations. Publication is a separate filesystem atomic replace. Existing per-item handoff IDs/retries establish work linkage, not one universal database generation covering arbitrary source updates, exclusions, confirmations and cached evaluation results.

No shared generation ID, durable all-relevant-writer mutation sequence or writer-coordinated capture/publication barrier was established in these accepted source contracts. This is an exact scope finding, not a claim that consistent two-store capture is impossible with another authorized protocol.

## FROZEN_SOURCE_BOUNDARY

Fixture uses only the accepted retained DEV schema definitions for Core token_analysis and OPS wt_treasury_review, with eight synthetic rows/store. Source schema metadata is read from existing disposable DEV fixtures, never from production data. Generation manifest in the toy protocol binds captured PK identities and values, digest, explicit source-session authority and complete-enumeration flag. PK keyset retrieval is four rows/query; exact once-only identity coverage is tested against the frozen fixture manifest.

Controlled independent snapshots produce Core-old/OPS-new although actual write order was Core-new then OPS-new. That pair never existed as one committed state: independent snapshots cannot be advertised as an atomic generation. A single attached connection is not asserted as a cure; no ATTACH atomicity claim is made. Complete real closure includes all identity, provisioning, review/exclusion, confirmation and reconciliation inputs, configuration/evaluation clock and relevant caches, not only these two diagnostic tables.

## MUTATION_FENCING

Two surviving read-only watcher connections can bracket each bounded capture step with PRAGMA data_version checks. A concurrent writer thread updating only Core, or only OPS, changes the appropriate watcher token and invalidates capture. Updating an already-seen PK is detected; a rowid high-water would miss it. The toy capture never continues after a detected mutation and leaves last-good bytes unchanged; no retries loop automatically.

Observed restart counterexample: watcher connections report (2,2), close; OPS mutates; reopened connections again report (2,2). data_version numbers are meaningful on the same surviving connection, not durable cross-connection generation IDs. Persisting them in a checkpoint cannot authorize resume. Connection-loss/restart during capture must therefore invalidate the in-progress generation unless independently verified durable authority exists. The same limitation applies to restart during finalization; a completed input file alone cannot prove intervening live review/confirmation changes were reconciled. Full finalization restart acceptance is NOT claimed.

There is also an explicit check-to-publication race: a writer commits after the last successful version check and before the intended replacement. The diagnostic detects that interleaving and refuses publication. Rechecking alone moves the race; no finite polling loop creates an atomic fence. An honestly labelled as-of snapshot could use a different contract with durable follow-up/reconciliation authority; that alternative is not silently substituted for the required mutation reconciliation. No known source mutation is treated as free to skip.

## BOUNDED_CAPTURE

Diagnostic bounds: four selected rows/query; eight total/source (overflow HOLD); <=2048 encoded bytes/page; <=16384 bytes/generation; SQLite length limit4096; fixture main-database max_page_count128; process RLIMIT_FSIZE16000000. Actual quiet run: six queries including exhaustion pages,16rows,113byte largest page,428byte captured input. Stable continuation keys are declared PK mint/treasury and strict greater-than cursors. This is a test protocol for two known tables, not a replacement extractor for the full discovery owner.

The progress handler's two-second check is cooperative only. No absolute wall-clock, reader/writer lock occupancy or WAL-size guarantee is claimed. Mutation writers completed while the demonstration read transactions were open under WAL, but prolonged readers can retain WAL in general. No restart-spanning read transactions are proposed. Fixture main files12288bytes each; observed WALs12392/4152bytes and SHM32768each. These observed allocations and test limits do not enforce runtime WAL or the aggregate5GiB ceiling. No large files or production locks were created.

## CHECKPOINT_AND_RESTART

The fixture checkpoint records owner-session marker, source cursor, connection-local token vector and rows seen; it is explicitly incomplete. Largest checkpoint123bytes. It is a diagnostic, not a qualified durable checkpoint owner: the restart result requires invalidation, never acceptance of old tokens. A bounded fresh recapture can succeed during a quiet interval, but the full accepted source extraction lacks demonstrated bounded complete retrieval and source-coordination fencing. Missing identities and conflicting duplicate identities must fail closed; exact duplicates may deduplicate by source PK/content. This milestone tests same-identity dedup and missing-key rejection; conflicting duplicate input remains covered by the15 prior continuation controls.

## COMPLETION_RECONCILIATION

A quiet fixture generation serializes successfully after complete enumeration, deterministic digest and identity coverage. The existing qualified256-profile grouping/revision/rank oracle remains reusable once valid inputs exist. This milestone does NOT certify full identity resolver/confirmation/reconciliation closure, source mutation reconciliation after capture, or a joint final-publication epoch. A complete flag cannot create that authority. No incomplete or mismatched real discovery snapshot was published.

## PUBLICATION_SAFETY

Disposable last-good sentinel remained byte-identical on interrupted, mutated and invalidated capture. A separate complete-fixture serialization (589bytes) demonstrates bounded input persistence only, not application snapshot acceptance. Global snapshot directory and production last-good publication were untouched. Existing atomic file replace protects bytes but does not order source database mutations. The test has no authority to replace the accepted application publisher or claim current-generation completeness.

## PROVIDER_FREE_TEST_RESULTS

14/14 focused diagnostic checks pass: independent-snapshot skew; complete quiet bounded capture; Core-only invalidation; OPS-only invalidation; last-good preservation; existing-PK update detection; non-portable restart tokens; interrupted-capture invalidation; exact key coverage; duplicate identity control; missing-key rejection; validation/publication gap; gap refusal; quiet bounded input serialization. Concurrent mutations use real independent SQLite connections in controlled writer threads. They are synthetic writes ONLY to /private/tmp/dev004-generation-capture-8q2upq63. Existing15 continuation/256-profile equivalence controls are reused. Restart during finalization and complete application publication remain unqualified, not silently counted as passing. No provider requests, network transport, live services, historical data, production source/database writes, or raw retention.

## REMAINING_CUTOVER_GATES

M1/M4: actual bounded source closure, durable mutation/restart authority and final completion/publication fencing remain HOLD. Existing non-P3R discovery functionality and capped grouping continuation remain PASS in their qualified scopes. Final runtime bindings and paired state/cutover/rollback are separate unchanged gates. No P3R, general indexing, provider acquisition or new background infrastructure was introduced.

NEXT_ACTION: Qualify one narrowly scoped writer-coordinated generation-capture/publication epoch on disposable Core/OPS through the existing owners, with explicit restart invalidation and complete source closure. If those owners cannot provide that coordination without broader changes, retain HOLD; do not substitute data_version persistence, long-held live locks or unbounded rescans.

RUNTIME_DEPLOYMENT_REQUIRED=false — test-only capture/fencing diagnostics and documentation. Runtime candidate unchanged; no deployment or operational activation authorized.
