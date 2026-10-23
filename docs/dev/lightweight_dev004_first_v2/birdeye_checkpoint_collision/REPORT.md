# DEV004 Birdeye Checkpoint Collision Correction

DEV004_BIRDEYE_CHECKPOINT_COLLISION_STATUS: PASS — isolated DEV qualification. Conflicting or ambiguous windows correctly return CheckpointHold and receive no completion identity. Production remains QUALIFIED_NOT_DEPLOYED; overall V2 cutover is not authorized.

## ORIGINAL_COLLISION_COUNTEREXAMPLE

The frozen5b72bb26 finalizer was rerun with an existing valid BIRDEYE_OHLCV observation at1800000000. Its INSERT OR IGNORE checkpoint committed without recording the new chunk identity. After crash, window[1800000000,1800000900) was requested twice: four synthetic requests for three windows. The corrected path makes three requests, skips the completed window on restart and preserves the original row through finalization.

Observation uniqueness remains(operation_id,mint,observation_timestamp,resolution). Window identity remains the existing digest of version/mint/resolution/time_from/time_to. These are different identities; one bucket may legitimately support more than one acquisition identity.

## OBSERVATION_EQUIVALENCE_RULE

Exact operation/mint/timestamp/resolution key, finite complete OHLC, valid OHLC invariants, and exact numerical equality of mc_usd/close plus open/high/low/close. Source must be an accepted Birdeye candle source and existing request identity/provenance must be present. Distinct acquisition IDs are compatible when that evidence is equivalent. Existing source, request identity, provenance, created_at and every value remain untouched.

No0.1% tolerance applies to observation equivalence. The approved tolerance remains solely an ATH valuation comparison. Missing OHLC, unknown provenance, conflicting values or conflicting duplicate bars are not upgraded or guessed: the window is held.

## CHECKPOINT_PERSISTENCE

Minimum representation: one bounded operation_monitor_window_completion relation in the existing disposable OPS database, owned by src.ops.dev004_checkpoint_completion. Its composite primary key is(operation_id,mint,chunk_id). It stores request boundaries/resolution, completion time, normalized response digest and compact canonical-row key/fingerprint references. It stores no raw HTTP response or duplicate price history. No separate database, service, scheduler or general checkpoint system is introduced.

The existing shared DatabaseWriteService owns one transaction: validate compatible existing schema, compare occupied observations or insert missing ones, then insert completion identity. No INSERT OR REPLACE or observation UPDATE is used. If any comparison fails, all inserts and the completion marker roll back together. Repeated identical completion is idempotent. Readback checks exact plan identity/boundaries and fingerprints of still-present canonical evidence before skipping. Complete verified legacy chunk rows remain readable; incomplete legacy coverage is held rather than treated as completion.

Finalization now verifies/preserves existing observations and inserts only missing ones in the same shared transaction as the accepted ATH/lifecycle fact update. It no longer changes occupied observation source/provenance during finalization. The reducer and request-window construction are unchanged. Existing finalized facts still short-circuit without recalculation.

## CONFLICT_HANDLING

Typed CheckpointHold is raised for conflicting prices, incomplete evidence, unknown provenance or changed/missing checkpoint evidence. No completed marker is added for rejected evidence. Existing rows remain unchanged. A mixed window test inserts one new row before encountering a conflict; rollback removes that tentative insertion and publishes no completion. Existing bounded queue error/retry policy is not redesigned by this correction.

## RESTART_SKIP_RESULT

Retained-bucket collision PASS: after checkpoint commit/crash, restart skips the equivalent occupied bucket and acquires only outstanding windows. Identical-row and distinct-acquisition-ID controls PASS with exactly one completion record and unchanged canonical row. Duplicate processing and repeated fresh-process replay add no duplicate observation, no extra completed-window request and no repeated finalization. Fingerprints preserve the original source/provenance authority rather than relabelling it as the later request.

## BUDGET_CONTINUITY

The durable owner is unchanged (SHA256461a8cf99f0e1ca33bdb540fa1d60d54e08890bf8c031af7db36e8f741d69d65), retaining20global/4per-token/60seconds. Normal/committed-checkpoint recovery consumes3 reservations for3 requests. Pre-dispatch crash keeps an unused reservation charged (4 reservations/3 requests); lost response or pre-commit crash consumes4 reservations/4 requests. No budget is reset or refunded. No exactly-once external HTTP execution is claimed.

## FIVE_INTERRUPTION_RESULTS

All five previously accepted scenarios PASS again through actual shared-writer transactions: before dispatch; after synthetic response/before persistence; after checkpoint commit; between completed windows; after finalization commit/before ACK. An additional physical crash inside the checkpoint transaction before commit also PASSes: no partial completion survives and retry is charged.

Each flow converges to3 unique observations and1 committed finalization. Duplicate finalized admission/reopen leaves facts/observations/request/debit counts unchanged. Stable finalization request identity remains07406ad713104276aa4446149cde49754a199418abe9ed6e87724e9834356982 across the synthetic plan and all crash cases.

## PROVIDER_FREE_TEST_RESULTS

15 groups PASS: normal; five existing crash scenarios; before-checkpoint-commit crash; retained overlap; identical existing; compatible distinct identity; conflicting value; missing OHLC; unknown provenance; atomic mixed conflict; original defect reproduction. Negative controls PASS by returning HOLD without completion, not by treating contradictory evidence as success. Actual module imports and DatabaseWriteService commits are used; fault hooks surround real operations. Only the explicit old-source counterexample substitutes the frozen starting finalizer.

Price checks: locked opening100/FIRST_FULL_POST_MIGRATION_SECOND_MC/ADAPTER_FROZEN provenance is preserved; ATH1000/multiple10/drawdown99% exactly match the frozen synthetic oracle (0% relative ATH difference). Historical facts in retained fixtures remain equal. Previously accepted canonical historical ATH/provenance/0.1% evidence is reused, with no historical recalculation. BIRDEYE-exclusive credential helper, endpoints, provider controls, opening policy and reducer source are unchanged. No actual credential values or provider requests.

Baseline/correction ancestry, qualified budget hash and frozen Supervisor configuration hash verified. No100creator/616Watchtower/freshness/Walkback/11frozen/26Deep matrices rerun. No production source/config/DB writes, service restart, live WebSocket, cutover or V1/historical-source deletion.

Storage is explicit and fail-closed: only MONITOR_RUNTIME=dev, symlink-free/private/tmp/dev004-* disposable database profiles with an already-enforced OS file limit<=64MiB may use the new write helper. Qualification uses16000000bytes and guarded SQLite page counts. Completion metadata is capped at4096rows,65536reference bytes/window,4MiB total reference bytes and512candles/window; capacity is checked in the transaction and failure rolls back. No automatic retirement. No owner-produced file can reach500000000bytes under the required OS limit. Raw retention=false; new unbounded growth paths=0. Larger/unqualified profiles HOLD; this does not certify future production storage limits.

Only newly generated superseded test DB copies were cleaned after qualification; their immutable source fixtures and test evidence were preserved. Latest normal/retained-overlap pairs remain reusable. Targeted free space rose to3096133632bytes after cleanup. No broad filesystem cleanup or historical retirement occurred.

DEV runtime gate PASS: actual changed finalizer and new completion owner execute from the isolated DEV package with recorded source hashes and actual transactions. No daemon restart is required. Production gate remains QUALIFIED_NOT_DEPLOYED. The new completion schema/profile must be included in the existing final V2 manifest/schema gate before any separately authorized deployment.

## CANDIDATE_SHA

`506fe25c941eadf379ca98e4f0d42c1a2486dc9c` — correction and provider-free evidence. Report-containing descendant retains identical executable source.

## REMAINING_CUTOVER_GATES

Checkpoint collision blocker is closed in DEV; budget durability remains PASS. Wider retry/backoff/exhaustion acceptance not exercised by this collision task retains its prior status. Global discovery/publication, final manifest/dormant-capability preservation, sustained production growth and cutover/rollback reconciliation remain the existing independent gates. No new broad investigation is introduced.

## NEXT_ACTION

Collision correction: NONE — COMPLETE. Continue the existing bounded integrated acquisition retry/backoff/exhaustion qualification before closing the full Birdeye recovery gate; reuse these completed collision and crash results. Do not deploy or switch databases.
