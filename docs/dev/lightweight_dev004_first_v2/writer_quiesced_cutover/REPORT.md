# DEV004 Writer-Quiesced Cutover Feasibility

DEV004_WRITER_QUIESCED_CUTOVER_STATUS: HOLD — quiescence can establish a fixed database boundary in principle, but a safe evidence-preserving maintenance window is not currently qualified.

GO_HOLD_DECISION: HOLD. Concrete blockers: no accepted lossless intake-gap recovery contract; dangerously inadequate current disk headroom; installed listener/receipt corrections not matched by the current DEV004 source composition. No stop, restart, cutover, provider acquisition, cleanup or schema/database write was executed. Do not reopen online cross-database coordination.

## Frozen authority

Inspection f94a8e59e8f7a9a2d5b95f19bf3f59dff58d465d; runtime candidate506fe25c941eadf379ca98e4f0d42c1a2486dc9c remains ancestral. FLEX_STABLE_V2 remains2d825bbcf27f802bd0bfcef90cedeba43cc968eb. Prior15 continuation,14 capture,8 writer-owner and19 review controls reused; no tests repeated for planning.

Current installed listener and pumpportal_birth_audit differ from DEV004 runtime/listener; hashes in evidence.json. AST comparison identifies birth-queue outcome/quarantine/drain and compact receipt sharding/capacity/provenance functions among the differences. The installed receipt writer includes sharded targets; its MAX_RECEIPT_FILES4096 is explicitly legacy flat-store compatibility, not an asserted total sharded-store limit. Differences are not classified as a production defect or reverted. They must be reconciled with accepted installed authority before any candidate deployment; immutable source composition is not newly certified by this report. Credential deployment remains closed, BIRDEYE exclusively authoritative.

## WRITER_INVENTORY

Current selective Supervisor status was read, not modified. Configuration hash matches accepted authority. Relevant scope:

| Program / owner | Current state | Writable authority relevant to pause |
|---|---|---|
| watchtower_listener, Production/flex-listener | RUNNING PID94632 | Core births/migrations/retry/handoff; OPS enqueue; receipt filesystem; creator secondary store; webhook/persistence drainers and diagnostics. Its asynchronous tasks and writer threads belong to this process; stopping only the DB workers does not freeze intake writes. |
| creator_resolution_worker, Production/flex-main-workers | RUNNING PID52248 | Core creator/CREATE resolution, queues/heartbeat and associated handoff/work state. |
| walkback_worker, Production/flex-walkback-dev002 | RUNNING PID94577 | OPS investigations/leases/roles/edges/attribution/membership/projection recovery/review; Core reads and any configured derived telemetry/files must be included. |
| watchtower_api, watchtower_ngrok, webhook_worker | STOPPED | API/manual review/governance endpoints and webhook intake are potential writers; must remain unavailable during reconciliation. ngrok itself is transport, not a DB writer. |
| operation_scheduler, infra_sync_scheduler, intelligence_snapshot_scheduler | STOPPED | Investigation/infra work, source caches and discovery snapshot/provenance publication. Must remain stopped. |
| creator_funding_worker, ws_cascade | STOPPED | Funding/queue/cascade state and provider-backed work. No activation authorized. |
| evidence_writer, operation_monitor_worker, operation_monitor_bridge, alert_evaluator | STOPPED | Evidence, price/lifecycle/budget/checkpoint, monitoring/admission/alerts and associated files. Preserve state; keep stopped. |
| Manual/DEV/CLI/sqlite clients, external webhook senders | Not exhaustively certifiable from one process snapshot | No ad hoc source writes or other controllers may act during a future window. Confirm read/write handles, scheduler ownership and external admission boundary at execution time. |

Read-only process inventory finds these three Flex Python workers and two Supervisor processes: PID1131 uses the selective config; PID27361 has no explicit configuration argument. No additional matching Flex Python writer was observed. The second controller's authority is unresolved; do not stop it blindly or claim a complete global writer fence. One targeted lsof instant showed Walkback holding relevant DB files; transient opens from listener/resolver are not disproved by their absence. No broad filesystem audit.

## QUIESCE_FEASIBILITY

Existing supported control is Supervisor stop/start for named programs, not a proven intake-preserving pause API. Listener wrapper execs Python; Supervisor stopasgroup/killasgroup=true and stopwaitsecs15. Resolver/Walkback use TERM, stopwaitsecs15 and default group settings. Resolver TERM requests shutdown after the current cycle; that cycle is not guaranteed to finish within15s. No comparable graceful Walkback loop-stop handler was established in the inspected source. Supervisor may escalate if a process fails to exit; no graceful-drain or forced-kill zero-loss guarantee follows from STOPPED status alone.

After all relevant writers and admitted manual work are stopped, both stores can remain stable without inventing an online dual-database transaction. Existing in-flight SQLite transactions commit or roll back atomically per database when the process completes/exits, but cross-database handoffs can remain between commits. Provider execution outside a transaction may be ambiguous. Verify all owned children/threads exit, handles/locks are released and queue/handoff intent is accounted before accepting the frozen boundary. Do not clear attempts or copy active leases as valid V2 ownership. A zero-byte WAL at one live instant is not evidence of drain/quiescence. This task performed no lock acquisition, checkpoint or database health scan.

## WEBSOCKET_INTERRUPTION_RISK

The listener source subscribes to PumpPortal subscribeNewToken and subscribeMigration without a resumable sequence/cursor parameter. It explicitly documents migration events lost during reconnect gaps. PumpSwap/curve subscriptions are also in this process. Stopping it therefore interrupts upstream acquisition, not only database writes. Receipts describe messages already received; they cannot buffer events emitted while the listener is absent. In-memory tasks/queues may also remain before durable receipt/canonical persistence. Current wrapper disables HELIUS_BIRTH_WS_ENABLED and PUMPFUN_BIRTH_RECONCILER_ENABLED; no accepted complete birth+migration gap recovery is established. Webhook backup is not proven complete and its receiver path is currently stopped. No provider documentation was fetched and no provider requests or replay were attempted; this is a code-boundary finding, not a new external provider guarantee.

Accordingly, zero data loss cannot be claimed. A deliberate evidence-loss exception has not been authorized, and missing migrations can affect attribution. Keeping the existing listener running would keep Core/OPS/receipts mutable, defeating this quiesced plan. A second new listener/capture service is outside scope. This independently triggers HOLD under the user's stop rule.

## CORE_OPS_CONSISTENCY / FINAL_STATE_RECONCILIATION

Conditional sequence only, NOT execution authorization:

1. Before any pause, resolve intake-gap evidence protection, current disk floor, source-composition differences, all writer controllers, final package/config/secondary-store bindings and recovery budgets. Abort the window if any prerequisite fails.
2. Keep dormant services stopped and inhibit manual/API/webhook/scheduled mutations. Under separate authorization stop intake only with the approved interruption/recovery contract, then let accepted downstream work finish or stop resolver/Walkback with explicit abandoned-claim accounting. Verify process/child exit and actual lock/transaction release.
3. Establish read-only Core/OPS/file boundary manifests: physical identities, schema/source hashes, frozen source identity enumeration and pending receipts/temporary files, handoffs, secondary CreatorRepository, queues/quarantine and price/budget state. All writers remain stopped; content comparison across bounded passes is then meaningful. No long-held live writer lock is needed. WAL/SHM contents belong to their database state; never omit a live WAL or manufacture a checkpoint. Production VACUUM/checkpoint is not authorized here.
4. Reconcile final canonical CREATE/migration and accepted operation/reference deltas, compact creator history, review/confidence/exclusions, queue retry attempts/eligibility, committed-attribution projection state and Core→OPS intents/admissions/ACKs. Preserve completed work/dedup and quarantine; reclaim only claims whose old owner is proven stopped under existing recovery rules. Do not promote uncertain roles or invent provenance. Existing reference fixtures are not a final live queue image.
5. Materialize final bounded V2 generation through accepted schema/seed owners; validate exact source coverage and continuation completion before defining candidate identities/ranks as final. Freeze generation/artifact manifest plus completion identity and verification records. Preserve last-good until this finishes; overflow/failure means no replacement.
6. With all writers still stopped, update the complete Core/OPS/secondary-store and filesystem bindings as one controlled configuration authority change; verify every consumer resolves the paired generation before any process resumes. File/config edits need not be atomic across files while no consumer runs, but restart after an interrupted switch must refuse mixed/incomplete bindings. That durable completion/recovery check is a required rehearsal, not existing certified implementation.
7. Under separate deployment authorization start only accepted services in the rehearsed dependency order (queue consumers/bindings ready, intake resumed promptly), keeping all dormant components stopped. Bound the interruption duration; do not claim natural intake can be tested offline.
8. Verify genuine new births/migrations, creator resolution, normal Walkback/projection/retry/quarantine and no duplicate acquisition, plus receipt capacity and relevant retained price/review projections. Live verification is future authorized acceptance, not performed here.
9. Preserve V1. Before new V2 writes, abort can revert verified bindings. After V2 writes, returning to V1 requires verified reverse reconciliation of canonical/queue/receipt/reference and budget changes; a bare path flip loses new evidence. If reverse reconciliation is unavailable, remain quiesced for an explicit recovery decision, never silently discard V2 work.

## DISCOVERY_PUBLICATION_FEASIBILITY

A verified writer-quiesced immutable source boundary removes the live-source mutation/publication race in principle. The15-control capped continuation remains reusable: complete sorted profiles and cross-batch groups, accepted merge/rank, frozen evaluation clock, explicit generation and coverage/completion markers. No partial ranking may replace last-good. Source closure still must include resolver/governance/confirmation/reconciliation inputs and relevant file state. Restart is safe only if all source writers remain stopped, identities/bindings match the manifest, and completion is durably verified; otherwise invalidate rather than resume. Restarting writers before publication/fencing completes reopens the rejected online epoch problem.

Full bounded source capture, complete public projection/finalization, durable mixed-binding refusal, storage enforcement and maintenance duration remain unrehearsed. Existing normalized-input feasibility is not acceptance of those parts. No discovery snapshot or fixture was generated in this planning task.

## STORAGE_AND_ROLLBACK

Current available bytes observed319201280,327204864,323252224 (approximately0.30GiB, volatile). Installed receipt MIN_FREE_BYTES=2000000000; final observation is1676747776bytes below that floor BEFORE new V2/temp/WAL/swap allocation. This is an immediate cutover HOLD, not an instruction to delete anything. Swap total9728MiB, used9094.44MiB, free633.56MiB; swap can further consume filesystem headroom. No swap or storage changes were made.

V1 Core logical/allocated35652194304bytes; OPS logical16194387968bytes, allocated16194613248bytes. Preserve both, about48.3GiB total, in place; a full rollback clone is neither required nor feasible from current space and exceeds the single-file restriction. OPS WAL observed0, SHM32768; these are one-time observations, not stable cutover evidence. Source DBs were not opened through SQLite.

Accepted reference fixture pair previously occupied22724608bytes (~21.7MiB), but final generation/queue deltas and long-term growth are not sized by that sample. Required additional free capacity must cover new V2 allocation, staged capture, old+new snapshot/transient overlap, bounded WAL/log/receipt growth, rollback delta authority and a swap reserve. Reuse already-allocated V1 without double-counting it. Minimum is max(effective receipt/publisher free-space floors) PLUS peak net additional allocation/reserve. The existing snapshot publisher defaults to10GiB free unless final configuration explicitly provides another accepted value;2GB alone is not a qualified maintenance budget. Exact total cannot be honestly fixed before final state/delta bounds are qualified.

Every new individual file must stay below500000000bytes. An aggregate5GiB aspiration is not enforced by current Core/OPS writers and cannot justify two monolithic files growing to that size. Current sharded receipt/retention semantics must come from installed corrections, not the older DEV flat-store interpretation. No historical receipt retirement, V1 deletion, full clone, compaction or cleanup is authorized. Failure before restart leaves V1 authoritative and last-good visible; failure after V2 writes invokes the rehearsed state-preserving rollback/reconciliation path, not deletion.

## NEXT_ACTION

Resolve the intake-interruption contract before scheduling any maintenance: independently qualify an existing complete birth+migration gap-recovery path, or obtain an explicit decision accepting bounded evidence loss. Do not create a new capture service under this task. Disk headroom and exact installed-source reconciliation remain mandatory prerequisites even if that decision is resolved. Stop this feasibility task here; no cutover execution.

RUNTIME_DEPLOYMENT_REQUIRED=false — read-only planning and documentation. No tests repeated, production writes, service controls, providers, live WebSockets, new infrastructure or large artifacts.
