# Birth evidence availability at migration

BIRTH_AVAILABILITY_STATUS: PASS — isolated DEV correction and provider-free qualification; production NOT deployed.

## EXACT_SOURCE_DELTA

Changed only listener behaviour in the root source and its production-composition copy (`runtime/listener`), plus focused tests and a disposable qualification runner:

- Stage the existing source-qualified PumpPortal CREATE delivery before returning QUALIFIED_PUMPPORTAL. Intent and canonical migration share the existing Core transaction; no internal commit or direct OPS writer is introduced.
- Validate the exact committed receipt even when memory contains a birth tuple. Canonical creator/signature are rechecked by the producer; invalid/unconfirmed receipts cannot create an accepted anchor.
- Close both receipt-confirmation/migration ordering races: one bounded exact receipt recheck under the migration transaction, and the existing producer after confirmed birth acknowledgement, serialized through the existing Core writer. No new service or queue.
- Keep matching cached-but-unconfirmed observations pending without new RPC work; do not falsely label them qualified. Existing OPS/queue terminal guards and caller commit/rollback behaviour remain intact.

No schema, discovery, classification, attribution, provider endpoint/budget, credential, retention or existing CREATE-handoff implementation changes. Resolver and Walkback source untouched. Unknown CREATE slot/block time remainNULL. Canonical token fields and nonprojection OPS fields compare unchanged in qualification.

Base source86fbf6b8ecc1ee6b70ad7986ae7954145cc90fc0. CANDIDATE_SHA is the immutable commit containing this report; exact SHA is recorded in the published terminal handoff/final response (`git rev-parse HEAD`). The qualification summary binds the tested runtime source hash. Production retains its previous source/configuration.

## BEFORE_AFTER_USABLE_COVERAGE

Fixed559migration denominator and retained audit artifacts reused. No new population scan or production database writes.

| Retained evidence / state | Count | Percent of559 |
|---|---:|---:|
| Any birth receipt found by complete bounded retained-store lookup |274|49.0161%|
| COMMITTED birth receipt, including three lacking V2 canonical fields |273|48.8372%|
| Exact canonical-matching birth receipts committed before precise OPS handoff |265|47.4061%|
| Earlier-than-whole-second-migration receipt group |182|32.5581%|
| Additional subsecond receipts before precise handoff |83|14.8479%|
| Existing OPS VALIDsignature anchors in frozen cohort |121|21.6458%|
| Additional projection-only cases qualified offline |265|47.4061%|
| Offline achievable OPS VALIDsignature anchors after those projections |386|69.0519%|
| Missing-canonical compatibility population |3|0.5367%|
| Unconfirmed receipt |1|0.1789%|
| No retained receipt; cause unknown |285|50.9839%|

These populations are overlapping views, not rows to sum. The265projection-only cases were waiting and disjoint from the121existing valid-anchor rows. The five cutover-delayed receipt acknowledgements are already in the existing completed/anchored population; they add no new coverage here. Two of the three missing-canonical cases have protected completed OPS rows already counted in121. No repair of these three or unconfirmed receipt is counted in386.

This improves **usable source-qualified signature evidence**, not raw birth capture or independently verified on-chain time/slot coverage. Exact full CREATE-at-processing field history remains unqualified. Deployed improvement is zero because deployment/production repair is not authorized.

## PROJECTION_ONLY_CASES

All265receipts pass the actual accepted reader against matching Core mint/creator/signature. Corrected admission stages265intents and no resolver jobs. Existing dispatcher batch16acknowledges all265in17rounds. Crash after OPS commit/before Core ACK recovered via ALREADY_COMMITTED; all other cases activated once. Repeated restart has no remaining work; counters pending0,ack265,admitted265. No confirmed operation membership/promotion is created.

## CANONICAL_COMPATIBILITY_CASES

All four exceptional cases were materialized only in disposable fixtures and evaluated through the actual committed-receipt reader and admission/protection gates:

- Three pre-cutover receipts are valid COMMITTEDsource evidence, but current V2 canonical creator/signature fields are absent. They do not satisfy the corrected projection's canonical-match precondition. One is held by existing QUEUE_TERMINAL protection; two have completed OPS outcomes and are held by OPS_PROTECTED. No canonical fields were invented/restored and no terminal jobs/states were reopened.
- The UNCONFIRMEDreceipt fails the accepted reader. Its matching current fields do not authorize receipt promotion; protected OPS state stays unchanged.

Exact authority still required for a future canonical-restoration repair: accept the historical receipt as the restoration source, qualify the existing Core writer's missing-field restoration and protected-state reconciliation, and preserve original receipt identity/acknowledgement/provenance plus current canonical timestamps and outcomes. This milestone does not implement or recommend automatic restoration from an unconfirmed receipt. The three remain separate HOLDs, not a reason to withhold the qualified265-case projection correction.

## ABSENT_RECEIPT_CAUSES

Reused the previous completed bounded lookup/log diagnosis; did not repeat117,643receipt reads. For all285cases, no independent retained evidence establishes outside-observation-window birth, receipt retirement, notification received but admission failed, durable admission without receipt completion, or demonstrated non-delivery. Therefore **UNKNOWN285**, not zero missed births, zero failures or a claim of provider loss. Global error counts and missing receipt files are not token-level causal proof. Stop at this retained-evidence boundary; no historical reconstruction or provider calls.

## PROVIDER_FREE_TEST_RESULTS

-99focused tests PASS: changed root/runtime composition functions; producer/receipt qualification, atomic intent rollback, duplicates, capacity refusal, protected OPS/queue states, conflicting creator/signature, receipt confirmation on either side of migration and transaction recheck; existing directly affected prospective/handoff controls.
-7existing birth-frame regressions PASS with transport blocked, synthetic provider environment, supported read-only qualification flag and disposable database bindings. An initial ordinary full-import collection was blocked by import-time file logging; corrected test isolation, not application infrastructure, resolved it.
-Fixed265case qualification PASS with actual DEV receipt reader/admission/producer/dispatcher. No network attempts, no provider calls; Core values and nonprojection OPS fields unchanged. Four exceptional compatibility cases remain guarded as above.
-Previous17diagnosis controls reused where unchanged; no unrelated creator/Watchtower/Deep/price matrices rerun. Accepted non-provider contracts remain unchanged by this patch.

Reproduce focused suite:
```
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -q -p no:cacheprovider tests/test_v2_birth_availability.py tests/test_dev090_prospective_enqueue.py tests/test_dev090_handoff.py
```

Fixed-case runner `scripts/dev/qualify_v2_birth_availability.py` takes explicit local cohort, receipt-lookup and schema-authority artifacts and a **new /private/tmp output directory**. It never opens live databases. Private input evidence remains local, outside Git. Only aggregate results are versioned.

## Storage and safety

Largest final fixture311,296bytes; OPSfixture118,784bytes; sampled dispatch WAL peak24,752bytes. The WAL figure is measured, not a universal hard ceiling. Existing owner limits remain: one<=1024-byte encoded proof per admitted token, dispatch batch16, pending10000,ACK70000,lifetime admitted100000,tries5,30-day activation and existing storage checks. Existing runtime499,000,000-byte per-file kernel cap retained; qualification subprocess enforces it. The exact receipt recheck reads at most4096bytes and performs no population scan. No new raw provider retention or unbounded growth path introduced. No file>=500MB. Any capacity/storage refusal remains fail-closed and rolls back the migration transaction rather than returning QUALIFIED.

## POTENTIAL_RECOVERY_REDUCTION

265cases can gain usable OPS anchors with zero new acquisition in the qualified fixtures. None of these265had a retained recovery job in the frozen cohort; do not label265as already-spent requests saved or replay the separate exhausted population. Matching unconfirmed cached evidence remains pending without newly introduced RPC demand. Actual HTTP request savings and future capture-rate improvement are not measured.

## REMAINING_BLOCKERS / NEXT_ACTION

No blocker to the bounded DEV correction's qualification. Production remains QUALIFIED_NOT_DEPLOYED and unchanged. Three canonical-restoration cases, one unconfirmed receipt and285unknown receipt absences retain their separate limitations; no additional seed, schema or recovery work started.

NEXT_ACTION: Review this immutable DEV candidate and, if separately authorized, prepare only the listener correction plus a fresh exact-receipt/canonical revalidation plan for eligible waiting rows. Do not deploy or repair production under this milestone.

RUNTIME_DEPLOYMENT_REQUIRED=true; RUNTIME_DEPLOYMENT_GATE=PASS for the intended isolated DEV fixture runtime (both source-composition copies exercised). No live listener/resolver/Walkback activation, service restart, production SQL/source/config/receipt/journal mutation, provider call, V1 access or migration rerun.
