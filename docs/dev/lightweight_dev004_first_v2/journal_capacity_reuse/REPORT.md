# DEV004_JOURNAL_CAPACITY_STATUS

PASS offline qualification; production cutover HOLD at the current quarantine-state transfer bound.

## RECLAMATION_OR_ROLLOVER

Existing MigrationJournal (also inherited by BirthJournal) reclaims at most20 ACKED records when capacity is needed, after independent current-generation canonical/handoff verification. No legacy-original, unresolved, pending, failed, conflicting, quarantined or retry-exhausted record is reclaimed. Reclamation is an atomic, fsynced journal replacement. Original identity authority remains in canonical CREATE/migration fields and handoff/queue evidence; accepted receipt storage and seven-day retention are unchanged. No new table, archive, queue or background service.

Listener wires its existing exact completion readbacks into both journal owners. Duplicate signature-only migration admission recovers the canonical mint using the accepted migration_tx index before dispatch, never by a historical scan or new acquisition. Conflicting canonical identities fail closed. A replayed completion is durably admitted and independently verified before ACK.

## EVENT_513_RESULT

PASS for birth and migration. After512 canonical+ACKed records, independently reverified completion reclamation permits513. 2500 sequential completions per stream passed; active records remained <=512 and journal allocation below2MB. No larger limit was substituted.

## RESTART_AND_DEDUPLICATION

Duplicate delivery after reclamation retains exact canonical identity and does not acquire new evidence. Pending/quarantine/exhausted records survive. Interrupted capacity-only replacements are validated on restart: no protected identity may disappear or change; missing completions must verify again. Complete safe candidates can publish; truncated, changed-authority or ambiguous candidates refuse. Crash after successful ACK, before replacement rename, and after restart covered.

## STORAGE_BOUNDS

Single journal2,000,000bytes, transient publication2,000,000bytes, record4096bytes, active population512, reclaim batch20. No completed-record archive or new unbounded growth path. Tests enforce actual64-byte child kernel refusal and source-journal preservation; disk-floor, short-write and generation-change refusal pass. Existing499,000,000-byte V2runtime kernel policy and64MiB bootstrap policy unchanged. Compact staging used64MiB kernel limits; no full V1copy.

## PROVIDER_FREE_TEST_RESULTS

192 distinct accepted controls:27 capacity/recovery controls and165 affected signature, birth-admission/retirement and paired-guard controls. Final recovery23/23 PASS; the four event513/long-sequence controls passed earlier with their algorithm paths unchanged by recovery-only edits. Existing run had164 PASS plus one AST-only fixture lacking the newly wired callback; that fixture was corrected and rerun PASS. Earlier capacity exception spelling and stale pre-P0 receipt-baseline assertions were corrected; receipt implementation remains byte-identical to the accepted cec3494d. XML records retain all runs, including intermediate failures, rather than hiding them. No broad historical classifier/price matrices rerun.

## FINAL_CANDIDATE_SHA

34ec79d8a97d08cd808c48c7eb0a55b2437b98ed. Only executable changes versus cec3494d are existing migration_signature_journal.py and listener journal callback/duplicate-identity wiring. BirthJournal inherits the capacity mechanism unchanged. Frozen runtime_plan.json updates the exact source/payload hashes and declares the exact existing V1 migration_tx index required on compact Core.

## PRODUCTION_CUTOVER_STATUS

HOLD before source installation, service actions or V1 retirement. The previously authorized fresh compact matched-pair rollback model is accepted; the former rollback-authorization mismatch is resolved.

Live preflight:65/65 installed source preimages match, listener wrapper hash matches, three accepted Supervisor workers RUNNING from expected roots, receipt buckets0full/256 and291966available slots, persisted Birdeye budget hash matches, free disk about4.85GB. No dormant activation.

The actual compact seed stage passed with foreign keys enabled:28,444 frozen selected records plus exact current Byzantine closure; Core4,833,280bytes, OPS12,783,616bytes, sampled allocation17,620,992bytes. Final operational-state staging then refused `ROW_LIMIT:wt_lineage_quarantine`: the source returned12,001records under a bounded LIMIT12001, beyond the accepted live reconciliation limit12000; the selected seed has129 quarantine rows. The additional operational-staging transaction rolled back. This proves the current qualified transfer boundary cannot certify complete required quarantine preservation; no wholesale historical scan or unsupported exclusion was substituted. Exact total population is not claimed. Unknown historical source token absences were not manufactured into parent records; V1 remained unchanged.

## ACTIVE_CORE_OPS_PATHS

Core `/Users/kevinkeaveney/Dev/claude/flex/database/flex_complete_database.db`; OPS `/Users/kevinkeaveney/Dev/claude/flex/database/wt_ops_v2.db`. No V2 authority activated.

## SERVICE_HEALTH

Listener97696, resolver92522, Walkback94577 observed RUNNING under Supervisor1131. No service restart/stop/configuration change. No V2 natural-intake acceptance claimed.

## ROLLBACK_READINESS

Accepted fresh matched compact rollback unchanged; not invoked. No V1 retirement, deletion or compaction. Original V1 sources and configuration remain authoritative and intact.

## NEXT_ACTION

Qualify bounded deterministic quarantine continuation and total storage bounds, preserving all required identities, exclusions, provenance and protected state. Then resume the already-authorized production cutover after immediate live checks. Do not redo the journal correction or reopen historical research.
