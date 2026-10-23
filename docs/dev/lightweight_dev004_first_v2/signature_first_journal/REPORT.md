# DEV004 Signature-First Migration Journal

DEV004_SIGNATURE_FIRST_JOURNAL_STATUS: QUALIFIED_NOT_DEPLOYED — provider-free isolated DEV acceptance PASS. No production deployment or database switch.

CANDIDATE_SHA: a89cf9b6c899060d02a4fd88fc630241f01df746

## VERSIONED_RECORD_CONTRACT

Version2 extends the existing migration fallback JSONL file. One deterministic SHA256 event identity per transaction signature; first notification source/identity and reception time; optional observed mint/slot; PENDING/RESOLVED/ACKED/QUARANTINED/RETRY_EXHAUSTED state; durable attempt count; bounded error/conflict fields; per-record acknowledgement identity and Core/OPS path-pair digest. No mint is derived from signature text. First provenance is preserved across duplicate notifications. Conflicting mint/slot evidence is retained without overwriting the original and stops dispatch. No new database, queue service, acquisition engine or raw transaction/log payload store.

## SIGNATURE_ONLY_ADMISSION

Explicit opt-in DEV004_SIGNATURE_FIRST_JOURNAL_ENABLED=1 requires absolute MIGRATION_JOURNAL_PATH. Existing PumpSwap and PumpPortal notification entrypoints now call synchronous durable admission before creating the existing migration task. Missing path/file, capacity, sync or publication failure raises; no downstream dispatch is acknowledged. The existing handle_migration transaction acquisition/extraction and provider controls remain the replay interface. Initial classification still uses the accepted receive predicates; NO_MATCH/new discovery is irrelevant here. Default flag-off behavior delegates to the existing handler.

## DURABLE_PUBLICATION

OS flock on one stable sidecar serializes mutations across processes; no process-local lock is treated as durable authority. A bounded complete rewrite goes to one fixed O_EXCL staging file, is written fully, fsynced, atomically replaced and followed by parent-directory fsync before success returns. Lock creation is also synchronized. Actual macOS file/directory sync calls succeeded in fixtures. Short writes, fsync/rename failure or ambiguous leftover staging state fail closed. Leftover staging files require explicit review; there is no automatic deletion/recreation of ambiguous state. Tests simulate process exit and syscall failures, not physical power interruption. No exactly-once external HTTP execution claim.

## INDIVIDUAL_RECORD_LIFECYCLE

Replay reads at most20 pending records in deterministic retained order and uses existing handler controls sequentially. A durable attempt reservation precedes dispatch; restart/duplicates do not reset it. Maximum3 journal dispatch attempts; exhaustion is retained and not silently retried or deleted. Each record remains in the file even after ACK or quarantine; no pruning/retirement introduced.

Acknowledgement requires exact Core mint→migration signature, COMPLETE Core migration handoff for that signature and existing OPS queue readback by mint, matching the accepted handoff contract. Missing schema/evidence/readback leaves READBACK_UNVERIFIED. If Core committed before handoff, replay does not reacquire the transaction: the existing handoff owner must converge, then journal readback can ACK. Journal replay does not recreate attribution or membership. A pair-bound ACK cannot be reassigned to another pair. These are per-event verification markers, not a global cutover-completion watermark.

## LEGACY_COMPATIBILITY

Resolved version1 records load without inventing timestamps or identities, retaining exact original fields as bounded legacy evidence. Identical duplicate entries coalesce; differing same-mint retry metadata is retained in a bounded alternatives list. Conflicting legacy identity, unsupported state/version, unexpected raw fields or records exceeding the qualified cap fail closed with the original file retained. Existing large/ambiguous legacy files are not auto-converted, scanned beyond2MB, relocated or discarded. The flag-off legacy importer now refuses v2 or unresolved/malformed mixed input before its old whole-file cleanup.

## MIXED_JOURNAL_REPLAY

Resolved legacy and unresolved version2 events coexist. ACK of the resolved event never removes the unresolved record or whole journal. This closes the26f8a8c7 mixed-file omission counterexample for the new mode. Existing accepted producer/receipt corrections are composed from the already67-control-qualified prototype (installed65da0e22 plus exact DEV004 startup function); unchanged receipt/maintenance source bytes and all unrelated listener function ASTs are checked. This candidate now includes the previously missing accepted receipt/maintenance files. No new receipt semantics or credential fallback.

## CRASH_RESTART_RECOVERY

Qualification includes admission/reservation before dispatch, resolution, actual child-process exits and reopen, repeated restart, partial publication, missing/truncated/malformed state, failed file/directory sync, interrupted rename, duplicate scheduling, Core commit before OPS, OPS handoff before acknowledgement and durable ACK restart. Actual new scheduling/resolution/readback methods are exercised with synthetic notifications and disposable SQLite; existing acquisition/normal investigation bodies are not re-run against providers. Canonical/handoff crash fixtures model their commits and use the actual readback adapter; they do not claim a complete daemon or V1/V2 cutover rehearsal.

## QUARANTINE_AND_CONFLICT_HANDLING

Quarantined records remain durable and excluded from pending replay/scheduling/ACK. Conflicting mint/slot values are retained within fixed bounds; further overflow fails admission rather than overwriting evidence. Existing Core/OPS quarantine, retry ceilings and Walkback projection recovery are unchanged. No live leases copied, canonical rows deleted, uncertain evidence promoted or provider allowance reset. Journal dispatch counts are not a substitute for existing physical-provider accounting.

## STORAGE_BOUNDS

Maximum encoded record4096bytes, journal2000000bytes, records512, replay batch20, attempts3. At most one2000000-byte staging file plus the2000000-byte published file and a fixed empty lock sidecar: bounded journal transient allocation about4MB before filesystem overhead. Admission preserves a2000000000-byte disk floor plus staging headroom. Capacity exhaustion retains all old records and refuses new admission; it does not silently evict ACKs or quarantine. In new mode the legacy unbounded risk-journal spill is explicitly disabled so capacity cannot be bypassed through it. No new unbounded growth paths.

Maximum observed fixture file20480bytes; combined retained test fixtures128941bytes. Qualification process RLIMIT_FSIZE16000000bytes and fixture SQLite max_page_count256. No production DB clones, source journal relocation or files near500000000bytes. Journal work is bounded by its byte/record caps; no absolute fsync/lock-duration guarantee or new production WAL hard cap is claimed. No new schema or SQL writer; existing canonical transaction budgets remain their accepted owners.

## PROVIDER_FREE_TEST_RESULTS

63 unique controls pass: final release suite62/62 plus the capacity-spill guard (the second targeted run also repeats the affected composition check). Network connect calls blocked, SQLite paths restricted to each disposable fixture, environment credentials not inspected. Python3.11.5. Pytest reports only its existing asyncio_mode configuration warning because external plugins are deliberately disabled; tests use asyncio.run directly.

Early qualification found a binding-length defect for long fixture paths, corrected with a fixed-size pair digest. One fsync fault-injection initially hit the directory instead of the staging file; corrected before final verification. No application test failures remain. Prior100 creator/616 Watchtower/26 Deep/11 frozen, price/BIRDEYE and Walkback evidence reused; unchanged consumers are not repeatedly tested. Full source hashes and JUnit results accompany this report.

## V1/V2 authority-transfer scope

Explicit stable path is independent of active DB_PATH; pending records remain readable after rebind. No automatic production journal movement, deletion or empty initialization. The OS file lock protects journal mutation, NOT exclusive V1/V2 canonical ownership. Writer fencing, generation activation, transfer completion, leases and reverse reconciliation remain separate required gates. Old ACKs prove their recorded pair only. Rollback must retain the v2-aware importer guard or keep the versioned journal outside an older importer's reach; rolling arbitrary old code over the same v2 journal is not qualified. No full cutover or deployment readiness is claimed.

NEXT_ACTION: Qualify the controlled writer fence and paired Core/OPS authority transfer using this candidate's stable admission journal. Keep V1 authoritative; do not deploy, activate feature flags, initialize production state or switch databases.

RUNTIME_DEPLOYMENT_GATE=QUALIFIED_NOT_DEPLOYED. Executable code was qualified at the isolated DEV runtime paths in manifest.json; no daemon deployment/reload/restart or DEV_COMPLETE deployment claim. Production listener, resolver, Walkback, receipts, database/configuration/credentials and services unchanged.
