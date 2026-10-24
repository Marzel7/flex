# DEV004 Final Production Cutover Preflight

DEV004_FINAL_CUTOVER_PREFLIGHT_STATUS: **HOLD — production transition prerequisites are incomplete.** The successful disposable rehearsal remains PASS; it was not repeated. Observation: 2026-10-24T15:42:03.722028+00:00 through 2026-10-24T15:42:18.377796+00:00 UTC; supplemental bounded live-state probes are slightly later snapshots.

## IMMUTABLE_SOURCE_AND_CONFIG_AUTHORITY

Candidate **`bf0da567634a040c96cc955c9afe165466d94beb`** contains all accepted DEV004 corrections and the successful importer. Runtime trees and accepted ancestry are verified; runtime bytes remain unchanged. Rehearsal manifest/oracle hashes verify, including the29,633-row restart/rollback evidence. Current Supervisor configuration SHA256 remains `0f1d9f3c03c422727a8e7bdb2ffe623f1c77e817910c0fbe063a4c954356623e`.

A complete **production** configuration cannot be certified. production_manifest.json freezes the candidate and observed V1 bindings but explicitly marks missing production authorities; it is not a golden baseline or activation manifest. Both persistent V2 paths/generation, pair-authority paths, stable initialized birth/migration journals, persistent compact-history/freshness artifacts, state coverage and matched production rollback bindings are unassigned. No arbitrary production paths or new parent authorities are invented. The original disposable pair is retired; its /private/tmp paths are not production destinations.

Installed listener/resolver/Walkback packages lack `paired_binding_guard.py`; listener also lacks `birth_admission_journal.py`, `migration_signature_journal.py` and the qualified durable admission/drain functions. Existing installed db.py/write-owner/db_locking hashes differ from the candidate. Thus the tested retirement/paired-write/journal controls cannot currently be assumed to protect a production transition. This is a concrete deployment/configuration gap, not an allegation that normal V1 operation is failing. The actual per-file preimages are frozen for a subsequent bounded deployment assessment.

## LIVE_V1_STATE

Core `/Users/kevinkeaveney/Dev/claude/flex/database/flex_complete_database.db`, inode549513179; OPS sibling `wt_ops_v2.db`, inode421043107. Both remain V1 authority, WAL mode. At observation Core WAL absent, OPS WAL0bytes/SHM32,768bytes. No production pair seals or retirement markers. Read-only queries completed; absence of owner sidecars does not prove quiescence, and no lock/flock/checkpoint probe was run. Secondary creator repository remains `/Users/kevinkeaveney/Dev/claude/flex/pumpswap_tokens.db` and must retain its own authority.

Supervisor: listener94632, resolver3874, Walkback94577 **RUNNING**; all other listed services, including API, monitor, bridge, Funding and Cascade **STOPPED**. No restart or process action. Legacy DB-relative fallback journals were absent at the sample; absence is not proof that no admitted work exists. No stable DEV004 journal authority is explicitly configured, and no production journal is initialized here.

## MIGRATION_DELTA

**28,447 V1 rows compared by indexed PK/digest:0changed,0missing.** The15 separately accepted external records retain their existing authority. All1,171 rehearsed memberships remain present. Current known-operation roots contain **one additional Watchtower assignment**: `3WUfWQADbhWwogJk7Y37o5PUUVEy7KK1xsxB3YTvpump`, with canonical HIGH outcome and a PENDING outbox admission. It uses the already-qualified membership-commit namespace; this is final-state reconciliation, not a reopened reference investigation. Ten operation identities remain unchanged.

The fixed selected cohort is not the whole live operational-state population:

-Current creator-funding queue: **3,566pending,59retry,3running** (bounded indexed counts). Rehearsed selection contains136pending/59retry and none of the3running records. Therefore3,430currentpending records and3running records lie outside that selection. All3running lease deadlines are expired while Funding is stopped; do not copy them as active ownership or clear them here.
-Webhooks: the first129bounded records are all unconsumed;113are quarantined. Selection contains0webhook rows. The probe is truncated; no global total is inferred.
-Lineage quarantine: **at least2,049current records**, versus129selected; the bounded probe is truncated. It is not established that all additional records are required, but their dependencies are not proven absent, so they cannot be silently dropped.

These are state-coverage gaps, not evidence that historical research must restart. Existing queue/quarantine owners must specify preservation, reconciliation and lease treatment. Exact **global** births/migrations since rehearsal are unavailable because the rehearsal retained no complete source admission watermark/atomic generation. The exact selected-cohort and known-root delta above is established; no missing event is invented and no provider gap recovery is assumed.

## DURABLE_INTAKE_READINESS

Accepted DEV evidence for durable birth/signature admission, replay/ACK, disk/capacity refusal and writer retirement remains valid. Production is on its previous accepted listener path: birth retry remains tied to writable V1 and migration fallback is DB-relative. The new independent journal/fence paths are neither installed nor bound. A controlled transition must establish that authority before its final V1 fence; stopping a WebSocket listener is not assumed lossless. No durable state is reset or new buffering subsystem proposed.

## PAIRED_BINDING_AND_ROLLBACK

Retained matched-pair guard, stale-writer rejection, restart and fresh compact rollback proofs PASS. They cover disposable generations, not the current live queues or production package/config rollback. A production plan must bind the exact two target identities together, fence/drain all relevant writers, reconcile final canonical/queue/handoff state, close old connections and define fresh matched rollback destinations plus post-switch admitted-work handling. Never clear retirement markers or replace files under open connections. The original V1 pair stays preserved; no full V1 duplicate is required or feasible here.

## FUNCTIONAL_ACCEPTANCE

Reuse accepted100creator/616Watchtower/26Deep/11frozen qualifications; freshness/non-P3R review; creator resolution/Walkback normal, projection and outage recovery; Birdeye acquisition/budget/window/checkpoint/finalization/provenance and0.1%ATH tolerance. Rehearsal proof retained every29,633row,11reader outputs,10conflicting outcomes and5pending admissions. No old test suite or full rehearsal rerun.

All three installed Birdeye credential helpers match the accepted candidate: **BIRDEYE exclusively**, no legacy fallback or credential values inspected. Existing budgets20global/4token/60seconds remain unchanged. Dormant monitor queue root has a1,217-byte version1ledger with10retained debit entries, SHA256 `d5f7f952e5865c7c7b56a7f233031d0449011400c04ef4d392061cccf00bfca1`. This is not reset or interpreted as fresh allowance. Price queue/checkpoint/ledger authority must be bound through cutover/rollback independently of the primary pair.

The previously missing API/monitor/bridge roots remain dormant configuration references, not newly discovered regressions. They must stay STOPPED; this preflight does not require restoring or activating them. Preserve accepted source/projection contracts and qualify any future activation separately. Compact creator-history and freshness files still match accepted hashes, but their persistent production paths/feature bindings are missing; ignoring those bindings could discard accepted behaviour.

## STORAGE_AND_DISK_SAFETY

Available **3,690,426,368bytes (~3.69GB)**: floor2GB **PASS**, and256MiBrehearsal-style reserve fits. Qualified compact pair17,616,896bytes; sampled rehearsal fixture peak41,721,856bytes. V1 remains allocated in place (Core35,652,194,304bytes, OPS16,194,613,248allocated bytes); no duplicate, deletion or compaction.

Receipt authority observed at the existing wrapper's configured fallback:102,131files, **229/256full buckets**, **269remaining shard slots**. The installed rule is384files/bucket; a new identity in a full bucket fails closed even when disk space is adequate. This is accepted capacity refusal, not a new implementation defect. The production manifest still needs an explicit capacity/admission and dedup-preservation policy; no receipt is pruned or authority moved. Inherited environment was not inspected for credentials and is not claimed fully attested.

Production new-file/WAL growth is **not qualified below500,000,000bytes**. The64MiBOSlimit in rehearsal belongs to its test processes, not the running Supervisor services. No equivalent production bound is evidenced in their configured launch/write paths. A production transition must carry an enforceable budget before reaching the hard limit. The proposed5GiBcombined target is not enforced. Existing maintenance source includes seven-day rpc_metrics pruning, but its presence does not prove bounded active retention,30day/lifetime semantics or physical shrink; latest retained maintenance row is recorded separately, not executed. No new retention/deletion engineering is undertaken.

## REMAINING_MANDATORY_BLOCKERS

1. **Production-bound runtime/authority bundle incomplete:** tested durable journals/retirement/paired guard are not installed or bound; persistent V2/auxiliary/feature/rollback configuration is incomplete.
2. **Live-state reconciliation not qualified:** one new operation admission, pending/running jobs and unclassified quarantine outside the rehearsed selection, plus no complete admission watermark. Unknown dependencies cannot be silently excluded.
3. **Production admission/storage budget incomplete:** current receipt bucket capacity is heavily constrained; an explicit preservation/capacity policy and enforceable sub500MBfile/WAL limits are not qualified. This is not HOLD_DISK_HEADROOM.

No new historical-reference defect or regression of the successful disposable import is demonstrated. None of these gaps is resolved by repointing the three services at the retired disposable files.

## GO_HOLD_DECISION

**HOLD.** Do not deploy, switch databases or delete V1. Missing production authorization is not itself the reason: the concrete prerequisites above are incomplete.

## NEXT_ACTION

Prepare **one bounded production-integration deployment preflight** for the existing candidate: exact active-trio payload/preimages and persistent journal/paired-authority/compact-history bindings, with explicit live-state reconciliation, receipt capacity and file-budget gates. Keep V1 authoritative and perform no cutover during that preparation. Do not rerun the successful full seed rehearsal or begin historical research.

RUNTIME_DEPLOYMENT_REQUIRED=false: read-only evidence/configuration assessment only. Production writes/source changes, service actions, provider requests, V2 imports and database copies: **0**. Diagnostic files are bounded below8MB; no file near500MB created.
