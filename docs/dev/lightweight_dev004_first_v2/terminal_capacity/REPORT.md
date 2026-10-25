# FLEX V2 — Protected migration journal capacity

**FLEX_V2_TERMINAL_CAPACITY_STATUS:** PASS — QUALIFIED_NOT_DEPLOYED

**CANDIDATE_SHA:** 303f73f77f74d307b0aef18dc81f35fd3bc94892

**EVENT_513_RESULT:** PASS; full 512-record fixture admitted event 513 after 20 exact verified transfers (493 active entries). All 512 originals then verified independently in OPS.

**DEDUPLICATION_AND_RETRY_INTEGRITY:** Archived signatures return their exact protected record; no new journal slot, reset, attempt or replay. Conflicting supplied mint is refused, original evidence retained. No different-signature equivalence or canonical migration_tx rewrite. Existing known completion/retry rules retained.

**CRASH_RESTART_RECOVERY:** PASS before OPS commit, after OPS commit, after VERIFIED state, journal replacement and post-replacement crash. Interrupted event-513 publication recovers both new pending admission and protected originals. Corrupt destination blocks reclamation and interrupted-publication recovery. Fresh compact matched-pair rollback with actual paired guard preserves terminal deduplication, original transfer generations, canonical fixture and pending post-cutover admission; stale source pair rejected.

**READY_FOR_DEPLOYMENT_PREFLIGHT:** YES — not authorized or deployed. Requires exact three-file bundle, explicit OPS schema initialization, matched generation and stable journal authority, opt-in flag, and terminal-aware compatible fallback/fresh-pair rollback binding.

**NEXT_ACTION:** Perform a separately requested read-only deployment preflight binding the versioned OPS owner, three-file bundle, DEV004_MIGRATION_TERMINAL_EVIDENCE_ENABLED=1 and terminal-aware fallback. Do not activate or transfer live records under this qualification.

**EXACT_SCHEMA_AND_SOURCE_DELTA:** One versioned OPS owner: `migration_terminal_owner_v1` holds the contract and aggregate quotas; `migration_terminal_evidence_v1` holds immutable complete journal evidence, unique signature/identity, source-journal provenance, transfer generation, recovery rights and transfer status. Four triggers enforce admission accounting, quota, immutability and no deletion. Exact SQL, contract hash and file hashes are in qualification_summary.json. Runtime payload: new terminal owner, migration journal integration and listener factory opt-in. No existing canonical schema, foreign key or attribution definition changed.

**PROTECTED_RECORD_TRANSFER:** All 512 exact original records (274,881 bytes, SHA-256 `82cb20d631a4b8c16b54787cc333afdeb4d556fe55496f693146d2e42c6bb92c`) were preserved in disposable OPS with exact independent readback. Full original fields, including conflicts, error, source, reception identity/time, attempts and unknowns, remain intact. No historical source data was enriched or invented. Source-supplied notification_mint remains unverified; legacy mint is retained journal resolution, not canonical transaction proof. Missing legacy supplied-mint evidence remains unknown.

**TRANSFER ORDER:** Under the existing journal lock, select at most 20 protected records by reception time/identity; insert PERSISTED envelopes in one FULL-synchronous OPS transaction; read back through separate connections; commit VERIFIED transfer markers; independently reverify; only then publish the journal without those entries. VERIFIED authorizes journal capacity reuse only, not canonical migration acknowledgement. Restart reuses the same identities; terminal signature lookup precedes any new retry reservation. No cross-store atomic commit is assumed.

**STORAGE_BOUNDS:** Protected owner quota: 8,192 records / 64,000,000 logical envelope bytes. Payload max 4,096 bytes; allocated envelope max 8,192 bytes; transfer batch max 20. Journal and publication temp remain capped at 2,000,000 bytes each. OPS page cap is 480,000,000 bytes; the owner requires an already-enforced kernel single-file limit of at most 499,000,000 bytes. Disk refusal preserves the existing 2 GB floor plus a 2 MB transaction reserve. WAL admission threshold is 8 MB with SQLite automatic checkpoints every 32 pages. This threshold is not an absolute WAL size or lock-duration guarantee under concurrent readers/writers; the kernel file cap remains mandatory. Stalled checkpoints/storage pressure cause refusal with original records preserved.

**CAPACITY RESULT:** Event 513 succeeds after verified transfer. Another 2,000 sequential admissions reuse active journal capacity while retaining 2,512 distinct protected signatures. Largest sampled/final fixture file: 2,494,464 bytes. No new unbounded growth path, provider payload retention, archive or infrastructure service. The protected quota is finite: exhaustion fails closed and requires a separately authorized disposition, never automatic deletion or retry reset.

**PROVIDER_FREE_TEST_RESULTS:** 33 passed / zero failures in 8.96 seconds: 24 terminal-owner controls and nine directly affected protected-journal controls. Includes exact fixed 512, 513, thousands of admissions, duplicates/conflicts, crashes, corrupt readback, disk/quota/WAL/kernel/version/generation refusal, old-path rejection, indexed lookup, repeated startup, actual listener factory and actual paired-generation fresh compact rollback. Prior 562 completion/retry controls were reused, not rerun. Tests use disposable databases/journals, actual kernel file limits and blocked network. No full database copies or V1 access.

**ROLLBACK REQUIREMENT:** Fresh compact OPS restoration must retain both new owner tables, their triggers/counters, original transfer generations and stable journal path; the actual matched-pair test verifies this alongside canonical state and pending admission. After the first reclamation, an older terminal-unaware writer is not a safe source fallback: it could re-admit exhausted signatures. Deployment preflight must bind a compatible terminal-aware fallback, not silently restore abb72f57/194cf66d as a complete old journal implementation.

**PRODUCTION STATE:** No schema, database, journal, source, configuration or service change. Live generation remains DEV004-V2-20261024-dfb76562 / ACTIVE; installed preimage hashes and live journal bytes remain unchanged. No provider request, retry replay, historical deletion or BIRDEYE/budget change. Live capacity is not yet restored because this candidate is not deployed.

**RUNTIME_DEPLOYMENT_GATE:** PASS for isolated DEV interfaces; production QUALIFIED_NOT_DEPLOYED.
