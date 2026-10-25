# FLEX V2 CREATE intent crash-safety correction

FLEX_V2_CREATE_INTENT_CRASH_SAFETY_STATUS: PASS — isolated DEV qualified; production not deployed.

## ORIGINAL_CRASH_COUNTEREXAMPLE

Base candidate `ad2dfa7a3ca077410a9f9ba22b9fe4149fbad45a`; preflight handoff `7c558d04`.
The regression test executes the original candidate's receipt-confirmation method with the real receipt/journal owners. Its receipt reaches COMMITTED after the old terminal birth-journal ACK, then a process-death fault prevents CREATE-intent staging. Restart has no pending journal work or CREATE intent and OPS remains unanchored. The earlier106-test qualification did not cover this boundary.

## DURABLE_INTENT_BEFORE_ACK

Only the authoritative listener runtime source changes: `runtime/listener/src/core/pumpfun_curve_listener.py`. No schema, writer-owner implementation, provider, flag, queue, attribution, price or credential changes. The root legacy source copy is not the accepted production listener payload and is unchanged.

`_persist_durable_birth` now routes canonical readback, newly committed births and duplicate ACKED admissions through `_complete_durable_birth` before returning success. It recreates or reads only the exact admitted compact receipt identity, using the journal's original reception timestamp, then calls the existing receipt validator and CREATE producer. Existing receipts and their timestamps are preserved. `_record_pp_birth_outcome` returns explicit completion success rather than swallowing a staging failure as success. Disabled, unqualified, conflicting or unavailable staging cannot terminally acknowledge a requiring birth.

For a migrated, qualified birth, terminal journal ACK requires an existing idempotent intent (`PENDING`/`DUPLICATE`). For a birth not migrated yet, receipt qualification completes before journal ACK; a later migration must stage its intent in the migration transaction before returning QUALIFIED_PUMPPORTAL. Both race orderings are tested. Missing CREATE slot/block time remains unknown.

COMMITTED receipt still means canonical Core readback, not cross-store delivery completion. It can precede the intent, but the birth journal now remains recoverably pending through that interval. This eliminates the unrecoverable acknowledgement window without changing the receipt's accepted meaning or assuming filesystem/Core/OPS commits are atomic.

## RESTART_RECOVERY

The existing bounded listener birth drainer already invokes `_persist_durable_birth` for pending journal records. Canonical readback avoids reinsertion/acquisition on restart; receipt and intent identities deduplicate repeat work. Producer failure retains pending work. Already ACKED exact duplicate notifications also verify completion before returning success. This does not scan or replay historical ACKED records, reset exhausted jobs, promote held cases or introduce a new queue.

## OPS_ANCHOR_DELIVERY

Existing dispatcher unchanged. Core intent is durable before terminal journal acknowledgement; OPS commit and subsequent Core delivery acknowledgement remain independent. Crash after OPS commit is recovered idempotently by the accepted dispatcher. Conflicts, protected OPS status, exclusions and incomplete canonical evidence remain fail-closed. Walkback attribution predicates are untouched.

## PROVIDER_FREE_TEST_RESULTS

122 tests PASS:23 new focused controls plus99 directly affected retained producer/dispatcher controls. The seven previously accepted legacy birth-frame controls are reused because their source path is unchanged; new journal-completion integration is exercised directly. No broad historical audit or absent-receipt investigation was repeated.

Controls cover the original failure, crashes before/after receipt confirmation, after intent before journal ACK, after journal ACK before OPS, after OPS commit before delivery ACK, early receipt-creation crash, newly committed canonical birth, duplicates, repeated restart, both birth/migration orderings, missing/conflicting creator/signature, journal/intent/receipt capacity or storage refusal, bounded byte-limit refusal and paired-generation rejection before acknowledgement. Actual receipt/journal owners and CREATE producer/dispatcher are used; synthetic pair validation explicitly injects rejection in the mixed-generation test. Existing paired-binding owner is unchanged.

All265 retained qualified cases PASS again on disposable stores:182 earlier plus83 subsecond pre-handoff cases. Frozen baseline121/559 (21.6458%); achievable offline386/559 (69.0519%). Core fields and nonprojection OPS fields unchanged; unknown timestamps/slots remain NULL; three restoration cases and one unconfirmed receipt remain guarded. This is offline qualification, not a deployed coverage result or measured HTTP saving.

Commands:
```
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -q -p no:cacheprovider tests/test_v2_create_intent_crash_safety.py tests/test_v2_birth_availability.py tests/test_dev090_prospective_enqueue.py tests/test_dev090_handoff.py
```
The existing explicit-input `scripts/dev/qualify_v2_birth_availability.py` reruns the265 retained cases without live database access. Private case metadata and databases remain local; only aggregate results are committed.

## Storage and runtime authority

Current accepted production generation remains `DEV004-V2-20261024-dfb76562`, ACTIVE. Frozen config SHA256 `5931b38f82d4ba730cb63668550f33cd7030b3ea9490602ab0e4c238777149e8`; durable birth journal enabled. Installed listener preimage remains `9781a5f6adb300210d7d5363998d1be08eff48afc275caef39d2e5f3ef967314`. No production stores, sources or services changed; no V1 access, recovery replay or provider requests. BIRDEYE authority and all accepted budgets unchanged.

No new persistent storage path or raw-provider retention. Existing bounds retained:4096-byte receipt/record,512-record/2000000-byte journal,2000000-byte journal publication temporary, bounded20-record drain, existing receipt bucket ceilings and disk-floor checks, intent budgets and existing499000000-byte runtime file-size enforcement. Zero new unbounded growth paths. Refusals retain pending authority rather than acknowledging incomplete work. Largest focused-test fixture229376bytes; largest265-case fixture311296bytes, sampled dispatch WAL24752bytes. These measurements are not absolute future WAL guarantees.

RUNTIME_DEPLOYMENT_GATE=PASS_ISOLATED_DEV_FIXTURE_RUNTIME. Production=QUALIFIED_NOT_DEPLOYED; production activation is outside this milestone.

## CANDIDATE_SHA

The immutable engineering commit containing this report, runtime payload and regression tests is recorded in the published agent handoff and final response. Payload SHA256 `4aedc4360883ad323ed2d727d321c6b290e7e9472f624ed31e39b20dcfd82778`.

READY_FOR_DEPLOYMENT_PREFLIGHT: YES.

NEXT_ACTION: Run a fresh bounded deployment preflight against the corrected immutable candidate. Do not deploy without separate production authorization.
