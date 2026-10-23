# DEV004 Writer-Coordinated Discovery Epoch

DEV004_DISCOVERY_EPOCH_STATUS: HOLD — existing owners do not expose a supported durable paired Core/OPS capture/publication epoch.

GO_HOLD_DECISION: HOLD. Feasibility decision reached; stop here. Do not implement a multi-thread/multi-database coordinator, distributed transaction protocol, new mutation journal or changed nested-lock semantics under this task.

Frozen authority: inspection d8ff8f9d2a4604018b3e997b556ff5a9e46506a9; unchanged runtime candidate 506fe25c941eadf379ca98e4f0d42c1a2486dc9c; FLEX_STABLE_V2=2d825bbcf27f802bd0bfcef90cedeba43cc968eb. Relevant accepted correction ancestry reused. Exercised database_write_service.py, db_locking.py and intelligence_snapshots.py are byte-identical to installed Walkback package copies; hashes recorded. Existing15 continuation controls and14 capture diagnostics reused without rerunning. No P3R or broad discovery investigation.

## EXISTING_WRITER_OWNER_COMPATIBILITY

DatabaseWriteService owns one queue/connection/transaction and one path-specific advisory write lane per database. acquire_write_lease rejects any nested same-thread acquisition, explicitly including another database, because that would create independently committed inner work. submit rejects cross-database nesting for the same reason. An OPS acquisition from a separate diagnostic thread succeeds while Core's lease remains held: a Core lease is not an OPS fence. This is intended owner scope, not a defect to repair.

SQLite-backed tracked writers participate through these path-specific advisory leases. A native SQLite connection can still commit while an advisory lease is held unless it participates; the disposable test demonstrates that scope only. This is not an allegation that a particular active production writer bypasses its owner. A universal epoch would require verified participation of every relevant writer or SQLite-level exclusion, not an assumption based on one advisory lock.

There is no accepted paired-acquisition/release/abort owner in the inspected contract. Using two worker threads and inventing lock ordering, coupled completion and crash recovery would be a new coordination protocol, not existing-owner reuse. Holding independent BEGIN IMMEDIATE locks and claiming cross-store/filesystem atomicity would also exceed the contract and block both writer lanes.

## DURABLE_GENERATION_IDENTITY

Lease owner records carry diagnostic transaction IDs, PID/thread/path, timestamps and state. The authoritative lock is kernel flock, not the .owner sidecar. The in-memory token is explicitly process-local and never persisted; reacquisition creates a different token. Those records are not immutable source generation manifests or durable paired completion identities. Prior capture diagnostics demonstrate why connection-local data_version cannot supply that authority after restart. No generation identity with accepted cross-owner completion semantics was established.

## MUTATION_FENCING

Core lease acquisition passes; same-thread Core→OPS acquisition raises NestedDatabaseWriteError. A separate OPS owner acquires/releases independently. A same-Core waiter times out with CrossProcessDatabaseWriteTimeout. These tests qualify per-database mutual exclusion, not a joint freeze. No supported epoch prevents all incompatible Core and OPS changes during validation/publication. Priority changes waiter scheduling; it does not preempt or cap the current holder.

The owner's acquisition timeout does not bound callback execution or lease occupancy. The diagnostic acquired Core with timeout5ms and observed its lease still held after about58.9ms, including a deliberately contending50ms waiter. This is an observation, not a proposed acceptable live pause or a performance guarantee. The service invokes transaction callbacks synchronously, then commits/rolls back; no safe absolute holder-duration cutoff was found. Changing those semantics is outside scope.

## FINAL_PUBLICATION_ATOMICITY

Existing intelligence snapshot persistence serializes publishers with its own in-process _PERSIST_LOCK, checks disk headroom, fsyncs a sibling temporary and atomically replaces the final file. It does not acquire Core/OPS writer ownership or commit a source generation/completion record with both stores. Atomic file replacement protects bytes; it does not close the last-validation/source-mutation gap. No source lease was held across a global snapshot write and no global publication was touched. Incomplete/conflicting generations therefore remain ineligible; duplicate complete generation publication cannot be certified by these existing owners alone.

## CRASH_RESTART_RECOVERY

Ordinary per-database leases can be released and reacquired; process exit also releases kernel ownership. That property does not preserve an epoch across process restart, connection replacement, interrupted capture/finalization or partial source/publication completion. Sidecar metadata is explicitly diagnostic, so stale metadata cannot authorize resumed publication. Complete-epoch crash/partial-publication/repeated-restart matrix was stopped before inventing an unsupported paired owner. No such cases are counted as passing. Previous15/14 last-good/incomplete-generation results remain accepted in their stated scopes. All existing publications remain unchanged.

## RESOURCE_BOUNDS

Only disposable Core/OPS stores in /private/tmp/dev004-epoch-owner-pez6jy0h were used. Each contains one synthetic row in an accepted schema definition from preceding DEV fixtures; each main database12288bytes. RLIMIT_FSIZE16000000 and SQLite max_page_count3840 guard test files. Largest retained lock metadata file447bytes; no large temporary files. No new raw retention or unbounded application path.

Measured separate OPS lease round-trip1.123ms; contending Core waiter56.687ms; observed Core lease occupancy58.871ms. Measurements are not hard deadlines and are not live throughput or acceptable-interruption qualifications. No multi-step capture, full finalization or SQLite epoch transaction was performed. Closed test connections left no WAL artifact in the final file listing; that is not a runtime WAL ceiling. The callback duration, aggregateV2 ceiling, complete generation checkpoint and restart/rollback state bounds remain unqualified for an epoch that does not exist.

## PROVIDER_FREE_TEST_RESULTS

8/8 direct owner diagnostics pass: existing Core acquisition, supported cross-database nesting rejection, independent OPS acquisition, same-Core contention timeout, acquisition-timeout versus holder-duration distinction, native SQLite/advisory-fence distinction, successful next Core acquisition after release, fresh process-local token on reacquisition. Actual accepted owner functions were imported from runtime/walkback under retained isolation guards. Named diagnostic threads only; all SQLite opens restricted to disposable paths. Network, subprocess, credential-file and unmanaged-thread guards remained active; blocked activity=[]; no providers, services, live WebSockets, production source/database writes or cutover.

## NEXT_ACTION

Do not pursue another existing-owner-only online epoch qualification. If generation capture remains required, separately scope a writer-quiesced capture boundary using the existing controlled cutover maintenance contract, or explicitly authorize a new coordination capability. Neither alternative is authorized or implemented here. Preserve current functional qualifications and last-good publication; do not solve this by changing attribution predicates, holding unbounded live locks or silently weakening mutation consistency.

RUNTIME_DEPLOYMENT_REQUIRED=false — feasibility tests/report only; no runtime correction, deployment or production action. Runtime candidate remains 506fe25c941eadf379ca98e4f0d42c1a2486dc9c.
