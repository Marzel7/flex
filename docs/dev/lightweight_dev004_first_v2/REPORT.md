# DEV004 first V2 prototype

DEV004_FIRST_V2_PROTOTYPE_STATUS: HOLD — bounded partial prototype built; evidence-complete acceptance is not established. V1 remains authoritative. No production writes, source changes, restarts, providers or cutover.

## Frozen authority

FLEX_STABLE_V2=2d825bbcf27f802bd0bfcef90cedeba43cc968eb; immutable runtime manifest SHA256=f0d3d933109f3572023af677729feef4db6e432657c7b7095b5bae05f2821dc7. The nine deployed dd481183 credential files were rechecked unchanged; their completed deployment was not reopened. Existing DEV004 design and both dependency manifests are hashed in result.json. Installed Walkback and main-worker consumer identities are pinned there. No classifier or provider implementation was changed.

## SELECTED_OPERATION_ROUTE

WATCHTOWER assignment for HXpDLpoH6Vx6MtqybpmsC6gFFoZpeb6bYTTpkrrypump. Treasury 88WRB31nEcBb5PedLzuyZMTd1r2AYD2FJUQSwNugQUqe → SubProvider/direct funding wallet D1fMNtUkuXvxMawc2W7xHJr99ATeC9oZRsYwurLYuQ4o → creator Ad5iNR9HYVzsHhf98NuSXUyx7h19tAPTDVUGCrQF7G4v → launch. Direct funder and SubProvider are the same retained identity; no separate Funding Account was invented. Upstream amount=1000 SOL at1789573678; creator funding=2.122039 SOL at1789574064. Both funding signatures, CREATE signature and migration signature/time are retained in fixture records.

## EVIDENCE_CLOSURE

37 distinct source rows: Core6/OPS31. Included original canonical assignment with null event_id, completed Walkback business state, source attribution/confidence, provisioning session, two selected parent edges and one LOWER_RANKED_BUT_RETAINED alternative, launch/anchor records, original identity/evidence/review state, one final price fact and11 observations. Rows were selected with indexed predicates, LIMIT129, 200000-VM-operation/one-second per-selector guards and a2048-row pass cap. Unindexed admission-outbox selection was refused. No historical RPC cache or unrelated launch bulk copied. This is a selected partial closure, not a certified minimum seed.

## V2_CORE_OPS_FIXTURE_SIZES

Core294912bytes; OPS479232bytes. Separate completed-queue test clone544768bytes. Fixtures are at /private/tmp/dev004-first-v2-build/reproducibility-run. Two independent builds produced identical Core/OPS hashes. Per-database construction limit2048pages at4096bytes/page; process file limit16000000bytes, including transient files. Raw RPC/provider payload retention=false; per-run unbounded growth paths=0. Fixtures reopen read-only; no live database clone/backup API used. Production rows are held in separate read-only transactions; this is not a cross-database cutover snapshot.

## WATCHTOWER_EQUIVALENCE

Unmodified installed function bodies were extracted by AST to avoid runtime bootstrap/provider imports. Eleven frozen V1/V2 outputs match exactly, with both semantic digests retained. Role-normalized route predicate returns eligible=true/ROLE_NORMALIZED_ROUTE_VERIFIED. Creator classification=LINK_ONLY through wt_watchtower_launches. Parent alternatives and retained rows match exactly. Full behavioural discovery/attribution replay remains HOLD because original transaction-role and broader historical populations are not closed.

V1 itself returns known_treasury=false and known_subprovider=false for these route wallets, while retained Watchtower membership and confirmed queue classification remain present. Materialized attribution outcome=UNKNOWN_INFRASTRUCTURE. These are different retained authority populations, not silently reconciled or promoted. Positive known-dictionary recognition is therefore not qualified by this case.

## WALKBACK_EQUIVALENCE

Installed classify_creator and retained outcome match exactly. Installed claim_with_lease/ensure_schema executed only on the disposable recovery clone: two connection restarts both reject claiming the completed row, with original row unchanged. Full provider-free Walkback reconstruction is HOLD: canonical signatures exist, but complete CREATE/atomic instruction-role payload closure is not established. Stale historical process/lease fields are inert evidence in a fixture and must not be adopted as cutover authority.

## POTENTIAL_OPERATIONS_COMPATIBILITY

HOLD. potential_operations_contract.json records14 installed source identities and nine relevant schema populations. Required closure includes identity, typed evidence associations (INCLUDED/EXCLUDED/UNRESOLVED/CONTRADICTORY), immutable assessment versions/current pointer, lineage and association bindings; workflow/child-candidate review, promotion and rejection states; qualified candidate specifications, membership/ranking artifacts; candidate-wide selected/alternative/atomic populations; canonical review/identity references. Original rowids/high-water/freshness semantics must survive: new compact INSERT rowids cannot substitute for source generations.

Installed _notify_living_after_walkback_commit dispatches after source commit and isolates publisher failure. LIVING_GENERIC_BOUNDED_DISPATCH_ENABLED, reverse resolution, persisted-source reader and atomic publisher remain required. They were inspected, not activated or bypassed. One known-token subset and empty schema cannot certify emerging discovery or future promotion; no global discovery/publication was run.

## BIRDEYE_PRICE_EQUIVALENCE

Original baseline=FIRST_FULL_POST_MIGRATION_SECOND_MC, entry124188.60986679113USD; accepted ATH1015607.2718995338USD, multiple8.17794218800668; lifecycle PRICE_MONITOR_COMPLETE_COLLAPSED. Final facts, timestamps, provenance and11 observations are byte/field equivalent in V2. However the unchanged reducer using the retained OHLC subset yields peak124188.60986679113, not the accepted ATH. Historical calculation equivalence is HOLD; final labels are not a substitute for the missing input window. No historical values were overwritten or estimated.

Nine synthetic credential assertions passed across the three installed helpers, using mocked dotenv and synthetic environment only; actual credentials were not read. Existing monitor/rate-limit sources are hash-pinned and unchanged. The prototype makes zero provider requests and adds no request path. Existing full credential deployment qualification remains closed. End-to-end acquisition-plan/budget recovery equivalence is not claimed without the checkpoint.

## QUEUE_AND_RESTART_RECOVERY

Completed-state restart checks pass; pending/retry/in-flight recovery is unqualified. No acquisition checkpoint or restartable acquisition envelope for the selected mint was located in the retained manifest. last_observation_at/provider_call_count and finalization markers are preserved but are not relabelled as resumable acquisition checkpoints. No live queue file was changed. No claim of exhaustive checkpoint absence is made.

## UNRESOLVED_DEPENDENCIES

Missing accepted ATH input window and baseline-provider inputs; acquisition checkpoint/retry envelope; complete canonical CREATE/atomic role evidence; source namespace/review closure for null assignment event; safely indexed admission-outbox linkage; candidate-wide Potential Operations populations and original high-water identity; genuine pending/retry restart case. Unknown dependencies stay protected in V1. Dictionary-versus-membership authority differences remain explicit.

## GO_HOLD_DECISION

HOLD for evidence-complete V2 acceptance. Partial read equivalence and small fixtures are proven, not whole-system equivalence. No production remediation, provider reacquisition or broader schema audit is justified by this result.

## NEXT_ACTION

Perform one bounded retained-artifact lookup for this mint's accepted ATH finalization request70ca8541f0e928653eaec5fb4a721fa8196df94b0a78cae0a04eb552999227b4 and its acquisition checkpoint/envelope; add only recovered evidence to this same prototype and rerun the price/recovery comparisons. If those artifacts cannot be recovered, keep this case HOLD rather than inventing inputs or weakening acceptance.

RUNTIME_DEPLOYMENT_REQUIRED=false — DEV-only fixtures and tests; no executable production behaviour changed.
