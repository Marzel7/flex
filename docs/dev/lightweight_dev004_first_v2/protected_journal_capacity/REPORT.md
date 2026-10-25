# FLEX V2 protected migration-journal capacity

FLEX_V2_JOURNAL_CAPACITY_STATUS: HOLD_MISSING_PROTECTED_TERMINAL_AUTHORITY.

## EXACT_SATURATION_CAUSE

Installed runtime remains194cf66da5cbb8bdf9cfcf48ecac99593b286bb3 with all accepted CREATE and other corrections. Installed listener SHA2564aedc4360883ad323ed2d727d321c6b290e7e9472f624ed31e39b20dcfd82778; existing signature journal owner SHA256d29c1fc033d918dd24795952dc68d9d13072f17611c5fc0901503d33c348a7ed. Accepted V2 generationACTIVE and frozen configuration unchanged.

At2026-10-25T18:26:40Z the journal had512/512RETRY_EXHAUSTED records,274881bytes; the prior511exhausted+oneACKED population has advanced. These records are excluded from automatic pending replay. MAX_ATTEMPTS remains3. `_reclaimable` permits only nonlegacy,nonconflicting,independently reverified ACKED completions; a completion callback cannot make RETRY_EXHAUSTED disposable. The fixed512active population is saturated by protected terminal records, not disk bytes.

The prior34ec79d8 capacity qualification proved event513 and2500sequential **completed/ACKed** admissions per stream. It explicitly retained pending,quarantined,exhausted andlegacy records. That accepted result remains valid; it did not prove capacity reuse for an all-protected terminal population. A verified ordinary ACK can still free one slot; an unverified/protected ACK cannot. The new tests distinguish these cases rather than treating the511+1count alone as proof of saturation.

## PROTECTED_TERMINAL_AUTHORITY

Bounded read-only checks used the accepted migration_tx index and unique queue-signature index for all512current exhausted signatures:

-0canonical migration signature matches.
-0matching migration_persist_queue records.
-No accepted journal-to-quarantine identity/disposition owner is established. No unindexed quarantine/history scan was substituted for missing authority.

Existing stores do not currently supply the required contract:

| Existing owner | Why it cannot be treated as protected signature-terminal authority |
|---|---|
| birth_retry_quarantine | Proven compact birth/CREATE authority conflicts only; not provider-exhausted unresolved migrations |
| wt_lineage_quarantine | Lineage source-row/evidence/exclusion semantics; no accepted signature-first journal identity or recovery mapping |
| wt_walkback_recovery_events | Worker lease/self-kill/manual-restart audit, explicitly separate from evidence/governance; not migration retry disposition |
| migration_persist_queue | Existing resolved persistence queue lacks complete original journal identity/error/conflict envelope; legacy upsert overwrites last_error and returns non-PROCESSED rows toPENDING |
| dev004_retained_reference_evidence | Existing typed-reference snapshot schema, not an accepted terminal-transfer/dedup/recovery owner. Merely fitting JSON in a text column does not establish retained recovery rights |

Canonical presence alone would not preserve original notification identity,source,reception,attempt count,failure reason,conflicts orrecovery rights; here even canonical/queue counterparts are absent. RETRY_EXHAUSTED is not a deletion or quarantine decision.

## SAFE_CAPACITY_REUSE

NOT_QUALIFIED. There is no existing independently verified durable destination/reader for these records. Deleting them,calling them completed,relabeling them as lineage/birth conflicts,resetting attempts,or merely raising512would violate the requested contract. No such change was made. No new table/archive/queue/service was introduced merely to create an appearance of success.

The exact missing capability is a bounded,indexed,versioned **signature-terminal evidence owner** with full immutable journal envelope,explicit protected disposition,readback verification,durable duplicate lookup before another provider attempt,and restart/fresh-pair recovery transfer authority. Its commit must precede active-slot retirement, and interrupted two-owner transfer must be idempotent. Existing V2 retained evidence could be a destination only after that typed ownership,storage and recovery contract is independently established; it is not already established by its generic schema.

## EVENT_513_RESULT

Exact disposable counterexamples preserved:

1.511exhausted+one unverifiedACK → event513refused JOURNAL_CAPACITY; byte-identical journal retained.
2.511exhausted+one protected legacyACK,even with positive completion verifier → same safe refusal.
3.512exhausted,even with positive completion verifier → same safe refusal.
4.Control:511exhausted+one ordinary independently verifiedACK → event513admitted,all511protected records unchanged. This proves the prior completed-record correction remains intact.

No production event was replayed,retired oracknowledged by this work. Rejected notifications are not claimed durably admitted or historically recoverable.

## CRASH_RESTART_RECOVERY

Three repeated disposable restarts preserve every protected record and continue to refuse unsafe reuse. Duplicate delivery retains the exhausted attempt ceiling and failure state; it does not reset provider allowance. Truncated/missing publication cannot grant fresh capacity. There is no protected transfer implementation to crash-test; transfer crash/recovery,canonical/quarantine destination equivalence,andthousands of protected sequential admissions remain NOT_QUALIFIED,not passing assertions.

## STORAGE_BOUNDS

Existing512record,4096-byte record,2000000-byte journal andsame-size atomic temporary limits unchanged. Existing disk-floor refusal preserves the original journal. Compact synthetic fixtures only; no production DB orjournal copy,rawprovider retention,unbounded archive ornewinfrastructure. The runtime499000000-byte cap remains unchanged. No generated file approaches500000000bytes. Production signature lookups were read-only,indexed andbounded to512; no V1 access,maintenance,checkpoint orhistorical scan.

## PROVIDER_FREE_TEST_RESULTS

9tests PASS in0.40seconds: saturation variants,verified-completion control,duplicate exhaustion preservation,repeated restart,refusal of forged completion eligibility,disk-floor refusal andtruncated-state refusal. These are regression/safety tests,not a qualified capacity recovery. The existing code passes them by safely refusing admission when no valid destination exists.

```
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -q -p no:cacheprovider tests/test_v2_protected_journal_capacity.py
```

No broad operation,price,CREATE ormigration test matrices repeated because their runtime code was not changed.

## CANDIDATE_SHA

NONE — no deployable correction. Runtime remains194cf66da5cbb8bdf9cfcf48ecac99593b286bb3. The regression/evidence-only engineering revision is recorded in the published handoff; it must not be deployed as a capacity fix.

READY_FOR_DEPLOYMENT_PREFLIGHT: NO.

NEXT_ACTION: Establish the narrowly scoped bounded signature-terminal ownership/transfer/dedup/recovery contract described above, using an existing V2 store only if those semantics can be qualified. Then allow the existing journal to reclaim only exact records with durable verified destination readback. Preserve all protected history and fresh-pair recovery; do not reset/replay exhausted work or simply increase512.

Production source/database/journal mutations0;service restarts0;provider calls0. Walkback,Potential Operations,known operations,CREATE delivery,BIRDEYE/budgets,seven-day receipts andV1retirement unchanged. RUNTIME_DEPLOYMENT_REQUIRED=false: regression/evidence-only work; no qualified runtime correction.
