# DEV004 Bounded Paired-Transfer Qualification

RESULT: HOLD — matched-pair transfer succeeds, but the incomplete-pair startup acceptance case fails. Stop at this demonstrated authority gap; no application correction or coordinator added.

SOURCE_REVISION:91d3975e77236f601586b1bd8af86544cc97b9de, unchanged executable candidate. Frozen FLEX_STABLE_V2 remains2d825bbcf27f802bd0bfcef90cedeba43cc968eb. Relevant current source bytes match that candidate. The accepted birth admission/retirement and signature-first journal implementations remain unchanged; previous148passing controls are reused. This task adds only a bounded qualification test and artifacts.

## Authority and configuration

Production remains authoritative at Core /Users/kevinkeaveney/Dev/claude/flex/database/flex_complete_database.db and OPS sibling wt_ops_v2.db (its name does not mean the lightweight target). Neither production path has a retirement marker. No production database was opened. Installed Supervisor configuration hash remains0f1d9f3c03c422727a8e7bdb2ffe623f1c77e817910c0fbe063a4c954356623e; no service control, credential or configuration changes.

The experiment uses four small disposable stores, existing qualified fixture table/index definitions, explicit stable journal paths and frozen fixture-launch-config.json. The JSON is test orchestration evidence, not a newly accepted runtime coordinator or generation owner. Core/OPS selection uses the actual existing core/db.py DB_PATH and resolve_ops_db_path interfaces. Source and configuration hashes are in manifest.json. Existing BIRDEYE/provider-budget contracts remain unchanged; no acquisition is invoked.

## Exact sequence exercised

1. Construct disposable V1/V2 schemas from retained non-production accepted schema definitions; no production clone or reference-seed expansion.
2. Seed one completed birth, one synthetic frozen operation/membership/attribution outcome, pending creator/Walkback state and a quarantined unconsumed birth retry.
3. Acquire the actual V1 Core write lease; durably admit one pending birth and one signature-only migration. Duplicate notifications coalesce. Release the lease.
4. Quiesce the test persistence interfaces. Retire V1 Core and OPS through the accepted owner, including SQLite writer drain and durable markers.
5. Read and reconcile final rows across ten declared tables into V2, at most20rows/table transaction. Existing rows must match exactly; conflicting rows or active leases fail closed. No INSERT OR REPLACE or old-file substitution.
6. Verify all selected source/target rows match before replay, close transaction/read connections, and publish the complete bounded frozen fixture launch mapping with file/directory sync.
7. Instantiate fresh source namespaces/interfaces with both V2 paths. Drain the pending birth through the actual canonical INSERT and durable readback; persist the fixture-resolved migration through the accepted migration SQL/intent/completion/readback functions and a bounded OPS queue persistence adapter.
8. Verify two canonical birth rows, one new migration handoff/queue row, two ACKed birth identities and one ACKed migration identity. Duplicate delivery adds no canonical row. Existing creator attempts/status, quarantine, operation identity/confidence/review/membership and attribution evidence remain unchanged.

Source callback bodies for symbol lookup, metadata/SSE broadcasting are disabled as noncanonical/provider-capable side effects. Prospective CREATE delivery remains the accepted bootstrap default disabled in this fixture. Migration mint resolution and queue classification are frozen synthetic inputs, not recomputed classifications or provider acquisition. This is not a full daemon/creator-resolution/Walkback investigation rehearsal; their retained qualifications are not reopened.

## Data continuity and ownership evidence

The seven retained fixture rows across the declared populations compare exactly after transfer. Three new target rows represent pending birth/migration/handoff progress, not historical replay. data-continuity.json records exact durable identities, ACK states and counts. Pending creator attempts2 and retry status remain unchanged; pending Walkback attempts1 remain; quarantined birth retry stays unconsumed. There are no active leases in the positive source image; the bounded copier rejects such rows rather than copying ownership.

Matched-pair restart tests cover interruption after Core retirement, interruption after Core reconciliation, and a crash after target Core migration commit before OPS handoff. Replay is idempotent and admissions remain pending until exact required state exists. Two real child processes independently re-import the actual path resolver from the frozen matched launch configuration after process exit and recover identical bindings. Persistence restarts otherwise use fresh interface namespaces and explicit crash injection, not production/Supervisor restarts.

Stale V1 Core and OPS write attempts after target binding are rejected by the accepted retirement fence. No old SQLite file is renamed or replaced under an open connection. A deliberately retained stale witness is used only in its negative test and closed after refusal; it is not claimed as a drained normal worker.

## Mandatory failure: partial paired rebind

Fault configuration declares Core V2 but still points OPS at retired V1. The current resolver accepts this combination. The actual durable birth persistence path commits the new birth in Core V2 and ACKs its journal record; no complete-pair startup rejection occurs. The old OPS retirement fence works for OPS writes, but it does not reject independently writable Core V2. A separate real-process restart confirms that the mixed paths remain accepted after restart.

mixed-pair-counterexample.json shows core_committed_under_mixed_pair=true, birth_ack_states=[ACKED,ACKED], launch_rejected=false. The record itself is durable; this is an unresolved paired-authority/activation condition, not loss of that birth, a failure of single-database retirement, or evidence of a production event loss.

The fixture's complete configuration can be applied correctly, but current runtime consumers do not enforce that it is complete before permitting canonical writes. Adding a test-only validator and calling the result safe would hide this missing runtime contract. No such gate or database coordinator was invented during qualification.

## Tests and storage

Final portable suite:7PASS,1FAIL. The failure is the mandatory fail-closed mixed-generation acceptance assertion, not marked xfail or silently excluded. Initial assembly lacked the extracted writer's managed_db_connect/priority globals; those test-only fixture errors were corrected before the final run. No schema or application changes were made to obtain the matched-pair result. JUnit retained.

Maximum fixture file98304bytes; unique logical fixture bytes2863073; measured allocated bytes3260416. Measurements deduplicate pytest symlink aliases. Process/child RLIMIT_FSIZE16000000bytes; bounded per-table copying and row encoding; fixture launch manifest below4096bytes. Existing journal record/file/batch/floor limits remain enforced. No new raw provider retention or runtime growth path, large DB copies, production WAL/checkpoint or absolute lock/WAL-cap claims.

## Remaining blocker / next action

HOLD: no enforced complete paired-generation startup/binding gate before Core or OPS canonical writes. The single-database guards and durable admission remain qualified; they do not close this distinct global startup condition.

NEXT_ACTION: Qualify the smallest validation at the existing runtime startup/configuration boundary that rejects incomplete or mismatched Core/OPS mappings before any canonical write, including restart from a partial rebind. Then rerun this exact bounded suite. Do not add a distributed transaction system, promote the fixture, deploy or change production authority.

RUNTIME_DEPLOYMENT_REQUIRED=false — test/documentation only. Actual application source remains91d3975e. No production source/database writes, provider calls, live WebSockets, service restarts, historical deletion, promotion or cutover. Quiescing test interfaces does not prove lossless upstream delivery while a real listener is stopped; no historical provider completeness claim is introduced.
