# DEV004 Birdeye Budget Durability Correction

DEV004_BIRDEYE_BUDGET_DURABILITY_STATUS: PASS — isolated DEV budget-owner qualification. Production: QUALIFIED_NOT_DEPLOYED. Multi-window recovery is not integrated or qualified by this milestone.

## ORIGINAL_COUNTEREXAMPLE

The exact accepted owner from71b6e338 was rerun: four consumed synthetic admissions, malformed ledger, then another admission grants remaining_token3. The corrected owner instead denies malformed/truncated/missing/incompatible established authority without recreating or rewriting its consumption. The original reproduction and corrected controls are included in the30 passing test groups.

## FAIL_CLOSED_RECOVERY

`runtime/creator_resolution/src/ops/dev_provider_budget.py` is the accepted owner ported and hardened in the existing DEV004 package. It retains the version1 ledger shape and public admission API. Ledger presence and exact compatible version/schema are mandatory; read errors, duplicate JSON keys, invalid field types, oversized/nonregular input and clock regression fail closed with non-sensitive constant errors.

Admission never seeds zero usage, recreates a missing ledger, infers consumption or repairs state. Even first use requires an independently provisioned/verified version1 ledger. Interrupted publication leaves the existing fixed temporary file and/or a PENDING_V1 marker in the existing lock file; later admissions block for independently verified external recovery. No automated recovery/reset is provided. Compatible legacy empty lock files and valid ledgers are accepted; consumption remains authoritative. The qualified owner must be the sole owner implementation for its configured ledger; mixed legacy writers are not authorized by this DEV qualification.

## DURABLE_PUBLICATION

Under the existing exclusive flock: validate guard/ledger → compute the unchanged rolling-window reservation → write and fsync pending guard → fsync parent directory → write bounded exclusive temporary ledger → fsync ledger data → atomic replace → fsync parent directory → write/fsync ready guard → return permission. Caller dispatch may occur only after successful return.

Any I/O/sync/publication error denies that invocation. No unsupported directory-sync fallback silently grants permission. Ambiguous pending state stays blocked. A failure clearing the ready marker cannot erase the already durable debit; the invocation is denied and that reservation remains conservatively charged. No provider is called by the owner itself.

## FSYNC_AND_RENAME_BEHAVIOUR

Actual file and parent-directory fsync calls succeeded on the disposable local filesystem. The traced ordering matches the sequence above before the synthetic dispatch marker. Tests inject partial writes, write errors, exits before/after rename, rename errors, temporary-file fsync failure, both guard-sync failures, post-replace directory-sync failure and unsupported directory-sync behaviour. Each fails closed. Physical process exits release flock without clearing pending evidence.

These are real synchronization API calls plus software/process fault injection, not a claim of a hardware power-cut experiment or guarantees beyond the filesystem/OS synchronization contract. An unsupported/failing filesystem denies admission.

## CONCURRENT_RESERVATION_SAFETY

Across32 fresh-process contenders for one token, exactly4 succeed and28 are denied. Across32 distinct-token contenders, exactly20 succeed and12 are denied. Persisted call counts equal successful reservations. Exclusive locking spans validation through durable publication; no lost update or unearned allowance was observed.

## CRASH_RESTART_RESULTS

Valid state survives fresh-interpreter restart with counts1,2,3,4 and then exhaustion. A crash after durable ledger publication but before ready acknowledgement leaves one reservation and blocks ambiguous restart. A crash after admission returns but immediately before dispatch leaves its reservation charged; the next admissible call is count2, not count1. Failures before publication preserve the old ledger and deny restart while pending evidence exists. No ambiguous/failed dispatch is refunded.

Malformed, truncated, missing, unsupported-version, unreadable, duplicate-key, wrong-shape and oversized state all deny dispatch. Missing state is never silently initialized. Normal60-second expiry still works unchanged; this legitimate policy expiry is distinct from a reset caused by lost authority.

## PROVIDER_FREE_TEST_RESULTS

30/30 focused groups PASS; exact cases and outcomes in result.json. The tests load the actual corrected DEV module in fresh interpreters with synthetic token/class/time/ledgers and blocked network connect. Tests reproduce the old defect using the byte-verified accepted source. No acquisition transport, price worker, service or live queue was started. No real credentials, provider budgets or production databases were inspected or modified.

Frozen authority verified: FLEX_STABLE_V2 peeled2d825bbcf27f802bd0bfcef90cedeba43cc968eb, immutable manifest SHA256f0d3d933109f3572023af677729feef4db6e432657c7b7095b5bae05f2821dc7, and all ten accepted correction ancestors of889f2399. Existing runtime files are unchanged; closed100creator/616route/freshness/Walkback/11frozen/26Deep/price qualifications are reused. No broad matrix rerun.

Maximum owner-produced ledger/temp file65536bytes, guard64bytes maximum accepted input (emitted marker11bytes or fewer); fixed three paths, no accumulating archives or automatic cleanup. Oversized input blocks before parsing. Qualification OS file cap16000000; largest adversarial fixture65537bytes, deliberately rejected. Raw provider retention=false; new unbounded growth paths=0. No file can grow toward500000000bytes through this owner. This is not a bound on the rest of the future Flex runtime.

## EXACT_SOURCE_DELTA

One executable module added in runtime/creator_resolution/src/ops: hardened existing DevProviderBudget from71b6e338. Defaults remain GLOBAL_LIMIT20, TOKEN_LIMIT4, WINDOW60; no provider-budget or scheduling increase. No retry loop is added. Public result fields remain unchanged. No endpoint, BIRDEYE authority, price/ATH/lifecycle, Watchtower/Walkback, schema or production source/configuration changes. No multi-window callbacks or imports are connected.

DEV runtime deployment gate PASS: actual target is the module above in the isolated DEV worktree; tests record its exact SHA256461a8cf99f0e1ca33bdb540fa1d60d54e08890bf8c031af7db36e8f741d69d65 and exercise its real API with30 passing groups. Preimage was absent from this candidate package; accepted original owner remains retained in Git/test evidence. Fresh interpreters require no daemon restart. Production remains not deployed and unauthorized.

## CANDIDATE_SHA

`aabf24c137ab764a574909c6d097af167b21cb84` — corrected owner plus provider-free tests/results. Report-containing descendant retains identical executable source.

## READY_FOR_RECOVERY_INTEGRATION

YES — this budget-owner prerequisite is qualified for DEV integration using explicit independently verified state. The wider Birdeye multi-window gate remains incomplete until accepted checkpoint/budget callbacks are connected and the five acquisition interruption cases pass. No first-use allowance, consumed counts or live cutover ledger is invented here.

## NEXT_ACTION

Continue the existing bounded DEV004 recovery composition: connect accepted completed-window selection and this qualified pre-dispatch budget owner, preserving its state authority and existing controls; then run the five provider-free multi-window interruption cases. Do not deploy or activate services.

Production/V1 remain unchanged: no writes, source/config changes, service restarts, providers/live WebSockets, historical deletion or cutover. This budget-owner milestone is complete.
