# FLEX V2 — Terminal capacity deployment preflight

**FLEX_V2_TERMINAL_CAPACITY_PREFLIGHT:** HOLD — ONE CRITICAL ROLLBACK CONFIGURATION BLOCKER

**GO_HOLD_DECISION:** HOLD_SINGLE_CRITICAL_BLOCKER — TERMINAL_UNAWARE_RECOVERY_CONFIGURATION

**EXACT CRITICAL BLOCKER:** The authoritative fresh-pair recovery configuration is terminal-unaware: dev004_fresh_pair_recovery.py calls dev004_production_control.write_config(), which rebuilds from frozen source-preimages/supervisor.conf and frozen-runtime-plan.json. A bounded in-memory render produces no DEV004_MIGRATION_TERMINAL_EVIDENCE_ENABLED for watchtower_listener, so the qualified factory defaults to 0 after recovery. The retained qualification does not bind a compatible fallback that corrects this. Post-reclamation rollback would bypass OPS signature deduplication.

**CANDIDATE_INTEGRITY:** PASS. Candidate `95b65a2e56dcba4be1da0b2bc4f5acc692e040d4` includes `abb72f57`; all three runtime files match immutable Git bytes. Installed listener/journal preimages match accepted 194cf66d. Paired guard and hard-file-limit entrypoint are unchanged. Retained 33 terminal/affected controls and 562 completion controls reused; no suites rerun.

**EXACT_DEPLOYMENT_PAYLOAD:** Listener-only package `/Users/kevinkeaveney/Production/flex-listener/src/core/`. Exact file identities below; no installation performed.

- `pumpfun_curve_listener.py`: candidate SHA-256 `9b09f8e9bd6888302298f94138965d0ff4457254695e5adc244cd9ee29e6f433`; installed preimage `4aedc4360883ad323ed2d727d321c6b290e7e9472f624ed31e39b20dcfd82778`.
- `migration_signature_journal.py`: candidate SHA-256 `65e012a0eac3e1aefc48b870ecd878d2eee870d6f91c459e789118c0062e3668`; installed preimage `d29c1fc033d918dd24795952dc68d9d13072f17611c5fc0901503d33c348a7ed`.
- `migration_terminal_evidence.py`: candidate SHA-256 `0262a5bdf3d4cb179d8c4ef9f1fccd96cac306a6f5dc80074258443c0c090ecf`; installed preimage `ABSENT (new file)`.

**OPS_SCHEMA_COMPATIBILITY:** PASS for explicit future preparation. OPS is 151,535,616 bytes, DELETE journal mode, schema version 135; all terminal owner names are absent with no collisions. Retained owner tests cover idempotency, exact DDL/trigger accounting, indexed signature lookup and canonical/FK preservation. Production schema was not initialized.

**FEATURE_FLAG_AUTHORITY:** Currently absent/default 0 as expected before deployment. Future activation must set only listener `DEV004_MIGRATION_TERMINAL_EVIDENCE_ENABLED=1` after schema and source readback. Current configuration SHA-256 `5931b38f82d4ba730cb63668550f33cd7030b3ea9490602ab0e4c238777149e8` is unchanged.

**TERMINAL_AWARE_FALLBACK:** FAIL. Read-only AST extraction and in-memory rendering of the authoritative configuration generator, with atomic/save/Supervisor actions stubbed, proves listener recovery output omits the flag. The recovery tool calls this renderer. Its existing data backup copies compact OPS tables, but correct data alone does not preserve active terminal ownership. No terminal-unaware old source is acceptable after reclamation. No generator or runtime configuration was changed.

**LIVE_JOURNAL_OCCUPANCY:** Migration 512/512, all RETRY_EXHAUSTED; 274,881 bytes; no temporary publication. Original hash remains `82cb20d631a4b8c16b54787cc333afdeb4d556fe55496f693146d2e42c6bb92c`. Birth snapshot 499/512 (368 ACKED, 131 quarantined); existing qualified completed-record reuse remains intact and was not invoked.

**STORAGE_AND_DISK_HEALTH:** Available 5,015,519,232 bytes, above 2 GB floor plus 2 MB transaction reserve. Core 45,285,376 bytes; OPS 151,535,616 bytes. No WAL/rollback-journal allocation observed at the file sample; read-only snapshot acquisition succeeded. No checkpoint or maintenance issued. Same-process immutable entry wrapper enforces 499,000,000-byte soft/hard file limits before the running components. Owner limits remain 8,192 records/64 MB logical allocation, OPS 480 MB and 8 MB WAL admission threshold (not an absolute WAL ceiling). Receipts: 0 full buckets; 117,577 occupied entries; 275,639 remaining entry slots; busiest 481/1,536; no temp entries. Counts are bounded point-in-time observations.

**ACTIVE RUNTIME:** Matched DEV004-V2-20261024-dfb76562 / ACTIVE; physical identities/seals and owner hash verified. Listener 83937, resolver 44903 and Walkback 44919 remain RUNNING under Supervisor 1131 and accepted package paths. Current process environments bind the same Core/OPS paths and stable journals. Frozen provider authority remains BIRDEYE only, 20 global and 4 per token per 60 seconds, no legacy fallback; retained ledger SHA is unchanged. No provider validation request or dormant service activation.

**DEPLOYMENT_SEQUENCE (NOT EXECUTED):**

1. DO NOT EXECUTE while rollback gate is HOLD. Freeze terminal-aware fallback/configuration and matching three-file hashes first.
2. Refresh exact installed preimages, current pair authority, disk/receipt/journal prerequisites; stage approved payload and verified terminal-aware fallback without copying V1.
3. Using current Supervisor authority, stop only watchtower_listener and confirm stopped; preserve recoverable birth/migration journals. Resolver and Walkback remain untouched.
4. With exact active-pair environment and kernel FSIZE limit, explicitly initialize the qualified OPS terminal owner in bounded transaction; independently verify version, contract, triggers, unique lookup and quota counters. No journal transfer yet.
5. Atomically install only the three approved files with preserved ownership/permissions; verify all hashes.
6. Update only listener environment to add DEV004_MIGRATION_TERMINAL_EVIDENCE_ENABLED=1 after schema and source verification. Preserve accepted current configuration and provider budgets; do not invoke old broad configuration renderer.
7. Supervisor reread; require only listener changed; update/start only listener as required and verify new PID, matched pair, stable journal and flag=1.
8. Transfer original protected identities in at most 20-record batches (512 originals require at most 26 batches); exact independent OPS readback/VERIFIED marker precedes journal removal. Refuse conflicts, corruption or pressure. No exhausted replay/reset.
9. Verify natural signature admission and migration processing, current receipt/CREATE/queue integrity, unchanged resolver/Walkback PIDs, and storage. No artificial provider traffic or fabricated natural coverage.
10. On critical failure preserve all terminal evidence, journal and pending admissions. Use only frozen terminal-aware fallback/fresh compact matched-pair recovery; never restore a terminal-unaware writer or disable the flag after reclamation.

**NEXT_ACTION:** Qualify and freeze a terminal-aware fallback/recovery configuration that retains the exact three-file payload, opt-in flag=1, stable journal path and terminal tables across fresh matched-pair recovery; prove archived-signature deduplication and pending-admission preservation. Then repeat only this failed preflight gate. No production change is authorized.

**SAFETY:** Production changes 0; service restarts 0; transfers/replays 0; provider calls 0; V1 access 0; full database copies 0; historical deletion 0. New outputs are small local/DEV report files. RUNTIME_DEPLOYMENT_REQUIRED=false: this is read-only assessment/documentation.
