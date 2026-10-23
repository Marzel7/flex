# DEV004 Birdeye Multi-Window Recovery Integration

DEV004_BIRDEYE_RECOVERY_INTEGRATION_STATUS: HOLD — minimal DEV composition implemented; normal flow and all five requested crash cases pass for new buckets. A retained-price-bucket counterexample prevents general completed-window preservation. Stop rule applied; no deployment.

## EXACT_SOURCE_DELTA

Two files changed under runtime/creator_resolution/src/ops. operation_monitor_worker imports the qualified budget owner, exposes the pre-dispatch queue admission callback and passes it to the finalizer without budget-less constructor fallback. watchtower_terminal_ath_finalizer receives that callback and ports accepted completed-chunk readers/remaining-window selection from71b6e338. Readback is filtered to the current frozen chunk identities/resolution, avoiding unrelated acquisitions. Finalization identity/provenance request plan remains the full original plan rather than changing with the remaining subset. Exact endpoint/window construction, reducer, opening policy and lifecycle/ATH calculation are unchanged.

No new schema/checkpoint table/service or acquisition scheduler was introduced. The existing chunk_seconds parameter supplies the synthetic three-window test plan; no production chunking policy was added. Prior accepted owner remains byte-identical to aabf24c1. Frozen baseline/correction ancestry and Supervisor configuration hash were verified. Only these two runtime files changed.

## DURABLE_BUDGET_BINDING

MonitorQueue.admit_provider_dispatch calls the qualified DevProviderBudget immediately before the selected finalizer binding. It requires explicit MONITOR_RUNTIME=dev and an independently provisioned valid ledger in the explicit queue root; other runtime scope or missing/ambiguous ledger fails closed. There is no fallback that dispatches without a reservation. Defaults20global/4per-token/60seconds are unchanged; no higher environment override was added. Provider backoff is checked separately, not treated as allowance.

The exact qualified owner/source hash and30-group durability evidence are reused. Synthetic zero-use ledgers were independently provisioned only for fresh disposable test queues; no live allowance was invented or reset. Budget debits survive physical child exits. An unused pre-dispatch reservation remains charged; a response lost before persistence may require another charged attempt. No exactly-once external HTTP claim is made.

## COMPLETED_WINDOW_READBACK

For unoccupied buckets, committed chunk request identities and OHLC rows are read from operation_monitor_observations; the selected remaining-window list skips those exact plan identities. Crash/reopen after one or two checkpoint commits makes only the outstanding requests.

Concrete HOLD: the accepted checkpoint SQL is INSERT OR IGNORE against primary key(operation_id,mint,observation_timestamp,resolution). If a valid retained Monitor row already occupies a bucket, the transaction can commit successfully while inserting zero chunk rows. Source/request_identity are not part of this primary key. Thus the new completed-chunk identity is not durably represented and the accepted reader cannot find it.

A synthetic existing15m BIRDEYE_OHLCV observation at1800000000 was preserved while the same window[1800000000,1800000900) was acquired/checkpointed. After an immediate process crash, restart requested that window again, followed by the other two. Four requests occurred rather than three. This is a demonstrated checkpoint collision, not historical missing-candle uncertainty or a budget reset. INSERT OR IGNORE suppresses duplicate rows but does not prove acquired-window completion.

Resolving this needs an evidence-preserving completion-authority decision when the canonical observation key is occupied. Blindly overwriting retained request identity/source/provenance, inferring a new request boundary from an unrelated observation, or adding an ad hoc completion store is not justified by the accepted checkpoint contract. None was done.

## FIVE_INTERRUPTION_RESULTS

All use disposable V2 Core/OPS copies of the accepted DEV reference fixtures (never production), ordinary package imports, actual commit_write_and_wait/shared writer, synthetic responses and physical os._exit71. One new synthetic Watchtower-assigned token has a frozen3×900second plan.

| Case | Result for new buckets | Durable budget debits | Synthetic requests |
|---|---|---:|---:|
| Normal reference | PASS |3|3|
| Crash after reservation, before dispatch | PASS |4|3|
| Crash after response, before persistence | PASS; lost response explicitly uncertain |4|4|
| Crash after first checkpoint commit | PASS; first window skipped |3|3|
| Crash between two completed windows and third | PASS; both skipped |3|3|
| Crash after finalization commit, before ACK | PASS; ALREADY_FINALIZED on restart |3|3|

Every new-bucket case converges to three unique observation rows and one committed finalization, with identical request identity07406ad713104276aa4446149cde49754a199418abe9ed6e87724e9834356982. A duplicate finalized admission adds no request, debit, observation or finalization. The extra retained-bucket case is HOLD because the first window is reacquired after its checkpoint transaction committed. It cannot be excluded from overall acceptance merely because the five empty-bucket cases pass.

## RESTART_AND_RETRY_INTEGRITY

Original queue processing identity survives crash; existing recover() restores the interrupted synthetic claim without clearing attempts. No custom catch-up loop or retry scheduler was added. General queue max_attempts3, same-window retry helper8 and existing backoff source are unchanged. The existing budget ledger is not reset by job recovery. Full outage/backoff/exhaustion integration was not additionally certified after the checkpoint stop boundary; no uniform8-retry ceiling is inferred for every queue path.

## FINALIZATION_IDEMPOTENCY

One actual shared-writer finalization transaction is recorded per new-bucket scenario; post-commit restart and duplicate admissions return ALREADY_FINALIZED and ACK without acquisition. These are single-worker crash/replay observations, not a claim of arbitrary concurrent finalization or exactly-once external HTTP execution. The retained-bucket replay still reaches one finalization and three rows, but incurs an unnecessary charged acquisition; idempotent finalization alone does not cure missing progress authority.

## PRICE_LIFECYCLE_EQUIVALENCE

For the synthetic plan, locked opening100 with FIRST_FULL_POST_MIGRATION_SECOND_MC/ADAPTER_FROZEN provenance remains unchanged; ATH1000, multiple10 and drawdown99% match the frozen numeric oracle exactly (0% ATH difference, within approved0.1%). All historical price facts in each retained fixture are read back equal before/after; original reference fixtures are untouched. No canonical historical ATH, timestamp, provenance or finalization identity is recalculated. Opening-policy/reducer source and BIRDEYE-exclusive helper are unchanged; their previous provider-free acceptance is reused. No legacy credential path was introduced or actual credential values inspected.

## PROVIDER_FREE_TEST_RESULTS

Six passing flows (normal plus five interruption cases), repeated finalized admission/reopen controls, and one failing retained-bucket collision control. Actual database shared-writer commits are used, not fake persistence receipts. SQLite opens are audited to the disposable Core/OPS pair; socket connections, subprocesses inside workers and unrelated threads are blocked. The only allowed additional worker thread is the existing shared writer for that exact disposable OPS path. No provider transport or live WebSocket is used; synthetic response counts are not real provider calls. Source bindings/hashes and outcomes are retained in qualification.json/result.json.

Each test process has16000000byte RLIMIT_FSIZE and guarded SQLite page limits; reference DBs start below14MB. All created files remain below500000000bytes. Raw provider retention=false; finite test fixtures/requests only; no new unbounded storage path. This does not qualify sustained production DB/WAL growth. Existing100creator/616route/freshness/Walkback/11frozen/26Deep matrices were not rerun.

DEV runtime gate is PARTIAL/HOLD: exact changed modules execute from the isolated DEV package, five crash boundaries pass, but retained-bucket progress fails. Production remains NOT_DEPLOYED/NOT_AUTHORIZED. No production source/config/DB writes, service restarts, historical deletion, V2 cutover or unrelated restoration occurred.

## CANDIDATE_SHA

`5b72bb26c8eed08a19bf77a061caceefe80d959e` — explicitly unqualified DEV integration candidate with reproducible tests. The report-containing descendant retains this executable source. This is not deployment-ready.

## REMAINING_CUTOVER_GATES

M2 remains HOLD specifically for checkpoint-key collision and unqualified wider retry integration. The qualified durable budget prerequisite remains PASS. Other discovery/publication, final manifest/dormant contracts, sustained storage and cutover/rollback gates are unchanged. No closed prior classification, attribution, startup or price qualification is reopened.

## NEXT_ACTION

One bounded DEV004 collision-safe checkpoint correction: establish durable completed-window authority when an existing observation occupies the canonical bucket key, preserving that row's values/source/provenance and exact request boundaries. Qualify the demonstrated overlap/crash case before rerunning directly affected recovery controls. Do not silently overwrite evidence, infer unsupported completion or add a new checkpoint schema within this stopped integration.
