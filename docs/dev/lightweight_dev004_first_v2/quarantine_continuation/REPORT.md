# DEV004_QUARANTINE_CONTINUATION_STATUS

**PASS for bounded quarantine continuation. Production cutover HOLD; safely aborted before V1 retirement.**

## QUARANTINE_SOURCE_COUNT

207,116 records at the captured writer-fenced V1 boundary. No unrestricted COUNT or full V1 clone: indexed keyset batches reached source EOF. V1 has since resumed, so this is a historical fenced capture, not a newly certified current live boundary.

## QUARANTINE_TRANSFERRED_COUNT

207,116 in the compact staging OPS; all independently verified. The inactive persistent V2 destination currently has74,585 quarantine rows and is not complete or authoritative. No claim those partial rows are an accepted production generation.

## QUARANTINE_RECONCILIATION

All12 source columns, identities, reasons, provenance, exclusion bits and original evidence_json bytes are preserved. Every staged target row was checked against its indexed source identity. Ordered source/target digests and counts match; EOF completion fenced publication. Digest `b51806989c5e639db940cbd6f7d93728026483d5d918769e60e8ff03882b936b`. No conflicting row was overwritten, protected evidence discarded or V1 quarantine changed by the transfer tool.

## BATCH_AND_RESTART_SAFETY

128rows per transaction;64KiB per-row encoding;16KiB checkpoint; one bounded file lock per checkpoint;480MBmain budget;499MBkernel per-file maximum;2GBfree-space floor plus64MiBreserve. No fixed population ceiling, new table, daemon, archive or unbounded intermediate population. Source physical/WAL/schema generation changes invalidate progress. Target identity changes, missing records, mismatches and incomplete coverage fail closed. A commit before checkpoint publication replays idempotently. Interrupted checkpoint publication is retained and refused, never silently reset.

The initial live observational capture correctly refused source mutation after74,496 committed rows. The later fenced run completed and verified207,116rows in67.09seconds; compact OPS145,014,784bytes. Source authority was fenced using separate existing per-database lease-owner processes; the existing nested-write prohibition was not bypassed.

## PROVIDER_FREE_TEST_RESULTS

16/16continuation controls PASS, including13,017rows, multiple batches, duplicate target identity, restart, commit-before-checkpoint crash, missing/changed source, target conflict, complete reconciliation, FK failure and storage/disk refusal. An additional fresh matched rollback with108,609,536-byte synthetic OPS passed: bootstrap empty identities first, then restore compact data under the larger runtime file budget without replacing database inodes; active generation and fresh canonical write verified. Separate original write-lane owner exclusivity/release control passed.

**Critical related acceptance:1FAIL.** Actual `_migration_completed_mint` raises PAIR_TRANSFER_ONLY under the transfer fence. `MigrationJournal.admit` calls that optional resolver before appending/publishing the input, so the synthetic signature is absent afterward (journal0records). The failing regression is retained in tests/dev004/test_fenced_migration_admission_regression.py. No provider or production DB was opened for this counterexample.

## FINAL_CANDIDATE_SHA

`dfb76562efc0aa598c6750ddb36723b9e5e7b06e` includes the prior journal correction and the new quarantine continuation. Only the quarantine tool was added to runtime source this milestone; classifier, pricing, schema and discovery definitions were not changed. Production packages contain the65-file approved payload from this composition; every installed payload hash matches. The migration-only quarantine tool ran from the immutable DEV source rather than being activated as an application service.

## PRODUCTION_CUTOVER_STATUS

**HOLD — failed signature-first durable admission during the transfer fence.**

The approved source/configuration bundle was installed on the three existing packages and run against the same matched V1 pair with stable journals. Natural V1 admission verified27births initially. Resolver/Walkback were paused, V1writes fenced, and quarantine staging completed. Final non-quarantine state was staged, including pending queues, handoff/reference deltas, canonical admissions and three stopped funding leases normalized to retry with unchanged attempts/backoff. No V1retirement or V2activation occurred.

The completion-lookup ordering defect was identified before irreversible retirement. The owner was restored to ACTIVE on the **same unretired V1 pair**, and resolver/Walkback restarted. This is an abort before retirement, not reactivation of retired stores. The qualified source remains installed on V1; the original preimages/configuration remain preserved privately. Do not retry the fence with this lookup ordering.

The recovery sample verifies152ACKed birth records against canonical CREATE evidence. Migration journal retains4ACKed and6RETRY_EXHAUSTED records; the exhausted records are protected, not marked complete or discarded. A bounded retained-log scan did not supply independently recoverable unmatched full signatures; that does not certify zero unjournaled external-event exposure. No missing event, mint or timestamp was manufactured and no historical provider recovery was attempted.

## ACTIVE_CORE_OPS_PATHS

Core: `/Users/kevinkeaveney/Dev/claude/flex/database/flex_complete_database.db`

OPS: `/Users/kevinkeaveney/Dev/claude/flex/database/wt_ops_v2.db`

Active generation: `DEV004-V1-20261024`. Pair manifest `/Users/kevinkeaveney/Dev/claude/flex/.runtime/dev004-v2/authority/v1-pair.json`; Supervisor configuration SHA256 `a6dcc96e3ef0f107226d1a488f00813e51eb3a504a50ea0dca19bb754617c858`. Staged V2 is not active.

## SERVICE_HEALTH

Listener44753, resolver47203, Walkback47244 are RUNNING under the existing Supervisor. All other services retain their stopped state. Canonical birth recovery verified; failed/pending migration state retained. Existing Birdeye credential helper/budget authority, seven-day receipts and dormant components were preserved. No qualification-only provider calls; ordinary runtime provider work is not claimed to be zero.

## ROLLBACK_STATUS

V1 was not retired or deleted; no retired marker was removed. No post-V2 admissions exist to reverse because V2 never activated. Fresh compact matched-pair rollback ordering passed the108.6MBfixture; it was not invoked on production. Original V1 databases and source/configuration preimages remain preserved.

Disk free at evidence: 4294234112bytes. Largest created database observed145,014,784bytes; all transfer processes enforced a sub500MBper-file kernel cap. Legacy-size V1 preparation used the existing V1 entrypoints; the V2 kernel runtime entry was not activated. No full V1copy, deletion, VACUUM, compaction, receipt retirement, retention shortening or dormant activation.

## NEXT_ACTION

Move signature persistence before optional completion lookup, retaining the pending record on TRANSFER/unavailable/conflicting authority and preventing dispatch until lookup/authority qualifies. Pass the retained failing regression, then resume the same authorized cutover using a newly frozen live boundary. Quarantine continuation is complete and should not be reopened as research.
