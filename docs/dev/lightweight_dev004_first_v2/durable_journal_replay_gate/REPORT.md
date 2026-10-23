# DEV004 Durable Journal and Authority Transfer — Replay Gate

DEV004_DURABLE_JOURNAL_STATUS: HOLD — stopped before runtime implementation under the explicit insufficient-replay-record rule.

Frozen authority: evidence based28e683780ce49a53f1925d3b2d06253ee3e9cd3; FLEX_STABLE_V2=2d825bbcf27f802bd0bfcef90cedeba43cc968eb; freshly verified installed accepted65da0e22 listener SHA256751d40d14150b112513cfbed44329cf1c9f0e3e58f34369dd8f29550a1cd3a5f. Current executable DEV004 candidate506fe25c remains unchanged. Prior installed-correction/receipt reconciliation reused; no canonical schema, historical reference, price, discovery or P3R work reopened. Production bindings and BIRDEYE-only authority/provider budgets unchanged.

BIRTH_ADMISSION_DURABILITY: Previous accepted helper/fence evidence reused: current file append lacks fsync; compact birth retry lives in Core and has no file fallback after its own failure. Birth replay can carry its compact CREATE fields, but adding fsync alone would not resolve the migration replay/ownership blocker. No isolated durability change is presented as a qualified lossless transfer.

MIGRATION_ADMISSION_DURABILITY: Existing _migration_fallback_append is explicitly minimal data to recreate migration_persist_queue after resolved migration persistence failure. PumpSwap pre-dispatch supplies signature/logs; handle_migration subsequently fetches transaction data and extracts the mint. Existing journal has no notification stage/logs or unresolved-admission replay branch. Existing importer filters out records without mint/signature. Therefore merely moving this call before task dispatch is unsafe for the unresolved-mint case.

Four fresh actual-helper controls use accepted queue DDL and disposable SQLite:
1. Resolved mint/signature journal imports to one PENDING row successfully.
2. A signature-only/null-mint record cannot import; alone it remains in the file.
3. Appending a resolved record to that same file causes the importer to import the resolved record and remove the entire file, including the untransferred unresolved record.
4. Truncated JSON blocks import and retains the file.

The unresolved record is a proposed early-admission input, outside the currently accepted post-resolution journal use. This is NOT a claim that production journals contain such records or that actual production evidence was lost. It demonstrates a concrete unsafe composition if the existing format/importer is reused unchanged for the requested earlier admission boundary. Valid signature-only replay may use the existing handle_migration acquisition interface in a separately defined contract; this test does not claim signatures are intrinsically unreplayable or that a new acquisition system is needed.

STABLE_JOURNAL_AUTHORITY: Not implemented. Existing DB_PATH-derived namespace issue remains documented; production journals were not read, moved or deleted. Stable explicit paths alone do not repair the replay-stage mismatch.

EXCLUSIVE_TRANSFER_OWNER: Not implemented/qualified after replay gate failed. No process-local or filesystem lock is represented as a complete durable owner fence. Source-owner epochs, restart-safe phase transitions and stale V1 refusal remain necessary before transfer can pass.

DURABLE_WATERMARK: Not implemented. Existing importer removes a whole journal after filtered queue insertion; that cleanup is not a per-event canonical+handoff verification watermark. It must not acknowledge omitted/unresolved events. No real source journal cleanup was executed.

CORE_OPS_TRANSFER_EQUIVALENCE: NOT QUALIFIED. Disposable resolved retry insertion passes only its existing scope. No canonical CREATE/migration, OPS handoff/membership, retry ceilings, lease state or quarantine was copied or changed. Prior passing behaviour remains closed; successful insertion is not paired-state equivalence.

CRASH_RESTART_RECOVERY: The requested full interruption matrix is NOT qualified. Only malformed-record fail-closed behaviour and existing replay preconditions were tested here. Atomic publication, failed fsync, crash after Core commit/before OPS, completion watermark, repeated owner restart and stale V1 refusal cannot receive PASS against an undefined admission envelope/owner. Stop rule applied before implementing them; no new journal engine or test-only transfer owner substituted.

ROLLBACK_SAFETY: No transition occurred; V1 remains authoritative and unchanged. No post-V2-write rollback authority is claimed. Matched-pair rollback requires the previously specified exclusive-owner and reverse-state reconciliation evidence.

STORAGE_BOUNDS: Maximum disposable DB16384bytes; process file ceiling16000000bytes; SQLite max_page_count256. No large copies, new provider payload retention or application growth paths. Current migration journal lacks a qualified total cap; no enforcement or absolute production WAL/lock bound is claimed. Existing floor/receipt/quarantine protections unchanged. No files approach500000000bytes.

PROVIDER_FREE_TEST_RESULTS: Four replay-gate controls PASS as qualification diagnostics, including the unsafe pre-admission composition counterexample. Actual installed AST file writer/importer and accepted queue DDL used. Standard fixture SQLite adapter removes only the production-specific priority keyword; all opens restricted to the disposable DB. No synthetic provider transport was needed. No daemon startup, provider calls, live WebSockets, production writes, service restarts or deployment.

CANDIDATE_SHA: Commit containing this evidence is documentation/test-only; executable candidate remains506fe25c941eadf379ca98e4f0d42c1a2486dc9c. RUNTIME_DEPLOYMENT_REQUIRED=false — no executable correction accepted or deployed.

GO_HOLD_DECISION: HOLD. Exact limitation: accepted journal/importer is post-resolution retry, not complete pre-dispatch admission; unresolved events cannot safely pass its current filter/whole-file cleanup. User explicitly instructed HOLD if compact records cannot support replay, so no broader owner/transfer implementation was attempted.

NEXT_ACTION: Specify and authorize a minimal versioned extension of the existing migration journal for unresolved signature-first admission and its existing handle_migration replay path, requiring every record to remain retained until individually resolved/transferred/quarantined. Preserve raw-retention limits, budgets and no-new-acquisition scope. Only after that input/replay contract is agreed should fsync, fixed paths and exclusive paired transfer be implemented together. Keep production and V1 unchanged.
