# DEV004 end-to-end operational qualification

DEV004_END_TO_END_STATUS: HOLD —20 provider-free functional checks pass;2 scoped checks hold.11 prior frozen V1/V2 outputs remain EXACT_MATCH;27 keyed known-operation reference rows match. These are separate counts, not a complete operational-equivalence claim. V1 remains authoritative.

Frozen authority: FLEX_STABLE_V2=2d825bbcf27f802bd0bfcef90cedeba43cc968eb; immutable runtime manifest SHA256=f0d3d933109f3572023af677729feef4db6e432657c7b7095b5bae05f2821dc7. Deployed nine-file BIRDEYE correction hashes unchanged. Installed source identities are recorded in phase1/phase2 results. Read-only configured Core/OPS authorities remain database/flex_complete_database.db and database/wt_ops_v2.db under /Users/kevinkeaveney/Dev/claude/flex. Exact configured package paths are in runtime_authority.json. No unrelated runtime/configuration check or repair was attempted.

## V2_CORE_OPS_SIZES

Working copies start from the existing Core294912bytes/OPS503808bytes fixtures. Qualification copies finish Core335872bytes and OPS671744bytes, combined1007616bytes. Original fixtures remain hash-identical. Files: /private/tmp/dev004-e2e/qualified/core_v2.sqlite and ops_v2.sqlite. Only targeted required queue schemas, ten known registry roots and their retained identity/profile/qualification records, plus explicitly synthetic work were added. No production backup/clone or historical table-wide copy was performed.

## FRESH_BIRTH_MIGRATION_PIPELINE

PASS for tested installed interfaces: PumpPortal CREATE handler plus actual birth persistence method writes canonical creator/CREATE fields and a562-byte COMMITTED compact receipt. Duplicate deliveries leave one canonical identity/receipt. Actual PumpSwap detector rejects Buy logs and accepts Migrate/CreatePool; actual nested migration-store closure persists signature/time/lifecycle. Installed prospective-migration helper admits creator-resolution work. Full WebSocket daemon, full migration transaction parsing and shared production writer are not qualified. AST extraction preserves installed function bodies while omitting service bootstrap; metadata/SSE/symbol/creator-RPC side effects are test doubles. No real socket opens.

## CREATOR_RESOLUTION

PARTIAL. Admission, source-qualified CREATE proof validation/staging and durable Core→OPS delivery pass. A transaction containing new Core authority and its proof rolls back entirely on injected pre-commit failure. After injected failure following OPS commit but preceding Core ACK, restart returns ALREADY_COMMITTED and converges to ACK without changing the anchor. Full strict RPC creator recovery was not executed against a complete retained transaction fixture; do not equate trusted synthetic proof construction with recovery qualification.

## WALKBACK_FUNCTIONALITY

PARTIAL. New waiting work becomes pending with the qualified anchor. Three synthetic selected edges retain Treasury→SubProvider→Funding Account→Creator→Launch, with distinct SubProvider/funding-account identities; installed deep_walkback persistence is used. Completed work is not reclaimed. This proves topology storage, not complete funding investigation, treasury recognition or full unseen-route attribution. Historical mechanism/statistical search populations and transaction-role closure remain unqualified. No synthetic candidate was promoted into a real operation.

## WATCHTOWER_EQUIVALENCE

EXACT_MATCH for the original retained case's role-normalized route, creator classification, stored outcome, selected/alternative parents, negative lookup states and price records. All11 prior comparisons remain unchanged after new synthetic work and reference-root extension. Full behavioural discovery remains INSUFFICIENT_EVIDENCE; the original distinct authority states are preserved.

## OTHER_KNOWN_OPERATIONS

Ten canonical registry identities are present: WATCHTOWER, WATCHTOWER_DEEP, Byzantine, Nexus, P3R, P3R_EC1,3SW2,WSOL_PROVISION_CLOSE_1_SOL_MINUS_15K,P3R_13A04,FOUR_STEP_30_SOL_14_479K_WSOL_LADDER. Keyed V1 reads added9 missing registry roots,4 identity states,6 behavioural profiles and6 qualification contracts.27 reference-row comparisons are EXACT_MATCH. Original ACTIVE/CONFIRMED/MERGED distinctions are retained. Registry presence is not classification equivalence: complete memberships, supporting routes, conflicts, exclusions and relevant historical denominators for all operations are not yet seeded. Overall all-operation attribution is HOLD.

## POTENTIAL_OPERATIONS

HOLD. Installed pure matcher controls pass for unique exact signature, insufficient input and cross-candidate collision. Actual fixture lookup fails because potential_operation_identity is absent. Required candidate associations, assessment versions/current pointers, lineage/bindings, review/promotion/rejection state and candidate-wide evidence populations are not seeded. New candidate creation, evidence accumulation and conditional post-Walkback publication therefore cannot pass. Existing schedulers remain untouched; no replacement discovery logic was introduced.

## BIRDEYE_PRICE_PIPELINE

PASS for synthetic target-second opening, new assignment admission/deduplication, installed worker candle persistence, market-cap/multiple/peak/lifecycle fields, terminal finalization and stable finalization identity after reopen. Tests use existing offline persistence injection with synchronous bounded SQLite transactions, not the production shared writer. Credential/header checks select synthetic BIRDEYE only and reject blank authority; no actual credentials are read. Existing429 policy proves8 retries/9 total attempts, capped60-second individual waits. Account-wide budget/counter cutover remains unqualified; no allowance was inferred/reset.

Concrete source-composition mismatch: for identical complete synthetic OHLC at migration+2 with migration+1 absent, retained accepted worker71b6e338cfb54057ec4984d41bfbe02477c90dba returns plus_one_absent=true and migration_absent=true. Installed main-worker MonitorBirdeyeTransport returns the same value/time without those flags. This is REGRESSION relative to the accepted fallback-provenance contract, not evidence that a labelled+2 fallback itself is forbidden. The installed source was not changed and no active-service regression is inferred from this dormant price copy. V2 must select a qualified complete price-runtime composition before acceptance.

Historical ATH numerical acceptance remains PASS under the user0.1% valuation-only tolerance (observed0.0025616522%). Canonical ATH, timestamps and provenance are not recalculated/replaced. Missing original candles do not block this numerical gate.

## QUEUE_RETRY_QUARANTINE

PASS for the tested synthetic transitions: duplicate proof admission; pending claim; completed-job protection; restart preserving queue identity/attempt count; retry exhaustion after3 queue attempts; conflicting Core→OPS handoff stops BLOCKED after5 tries without overwriting the conflicting OPS creator; permanent birth conflict quarantine is idempotent and leaves canonical CREATE/consumed state unchanged. No stale running lease was adopted from V1. Historical complete rows remain inert reference evidence.

External-request limitation: injected commit-before-ACK failure causes two mock transport executions after retry. Observation PKs deduplicate persisted candle rows, and terminal finalization identity is stable. Exactly-once external execution is not guaranteed; existing bounded retry/idempotency behaviour is preserved. This is not silently counted as an exactly-once provider guarantee. Live queue/receipt/admission/counter reconciliation remains HOLD.

## V1_V2_EQUIVALENCE_MATRIX

| Surface | Classification | Limit |
|---|---|---|
| Original11 frozen creator/route/outcome/price comparisons | EXACT_MATCH | One retained case |
| Known registry/reference roots | EXACT_MATCH |27 rows, not full memberships/detection |
| Historical ATH valuation | WITHIN_APPROVED_TOLERANCE | No change to canonical evidence |
| Accepted opening fallback provenance | REGRESSION | Accepted flags absent from installed main-worker copy |
| Full fresh-daemon intake | INSUFFICIENT_EVIDENCE | Installed interfaces tested; bootstrap/socket/parsing not exercised |
| Full RPC creator/funding investigation | INSUFFICIENT_EVIDENCE | Admission/proof/topology are narrower tests |
| All-operation classification | INSUFFICIENT_EVIDENCE | Supporting populations incomplete |
| Potential Operations | INSUFFICIENT_EVIDENCE | Required identity/candidate state absent |
| Live queue cutover reconciliation | INSUFFICIENT_EVIDENCE | Synthetic transitions are not live reconciliation |
| Full API/UI compatibility | INSUFFICIENT_EVIDENCE | Stored fields tested; accepted complete API/runtime not assembled |
| Runtime-wide storage retention | INSUFFICIENT_EVIDENCE | Test file bounds are not production retention |

No EXPECTED_FRESH_START_DIFFERENCE is counted as equivalent without an approved contract. Detailed results and source identities are in summary.json and phase result files.

## STORAGE_GROWTH

Original reference fixtures total798720bytes. Added required Core queue schemas increase Core by40960bytes; the one synthetic birth and migration fit allocated pages with zero additional file allocation, plus a562-byte receipt. Walkback evidence/schema step increases OPS by65536bytes; this combines schema and evidence and is not a per-edge estimate. Final source-reference extensions plus price/recovery data yield OPS671744bytes. Observed synthetic price WAL=119512bytes at the recorded checkpoint, with37112bytes at the normal-worker checkpoint; this is observed growth, not a maximum bound. WAL auto-checkpoints on connection closure.

Schema-only allocation: Core335872bytes, OPS569344bytes. Reference seed allocation remains the original798720bytes; it is not a certified minimum. Allocated-page subtraction is not a logical payload estimate. One birth/one migration and fixed schema/page allocation are insufficient to estimate growth per1000 births/100 migrations; no linear extrapolation is claimed.

Every test process enforces a16000000-byte single-file limit; writable fixture connections cap SQLite at2048pages×4096bytes. Raw provider response retention=false; test-run unbounded growth paths=0. Runtime-wide V2 retention/caps remain unqualified: at least token_analysis, wt_walkback_edge_candidates and operation_monitor_observations retain ongoing growth surfaces. Exact runtime-wide count was not audited; no unrelated retention infrastructure was built. No new file approaches500000000bytes.

## REMAINING_BLOCKERS

1. Qualified price runtime composition must preserve accepted opening fallback provenance; current installed main-worker copy differs.
2. Complete operational memberships, behavioural/statistical populations, CREATE/atomic evidence and all-operation reference closure.
3. Potential Operations identity/association/assessment/review state and discovery evidence populations.
4. Full creator/funding investigation and accepted API/UI composition qualification.
5. Consistent live intake/queue/handoff/quarantine/account-budget reconciliation, controlled cutover and rollback acceptance.
6. Production-capable bounded storage/retention; fixture limits alone do not qualify long-term runtime growth.

## CUTOVER_READINESS

NOT_READY / HOLD. Existing source-only production deployment and V1 authority are unchanged. No source deployment, restart, live queue write, provider request, socket connection, historical deletion, compaction or cutover occurred. Small fixtures and passing component tests cannot replace these missing acceptance requirements.

## NEXT_ACTION

First qualify the existing DEV004 price-runtime source composition against accepted71b6e338, preserving its fallback provenance and provider controls, then rerun the affected price tests. Keep the work in DEV004; do not repair/deploy production or restore dormant services. The remaining reference/Potential Operations/queue/storage gates stay separately HOLD.

RUNTIME_DEPLOYMENT_REQUIRED=false — isolated provider-free fixture/test qualification only; no production executable change.
