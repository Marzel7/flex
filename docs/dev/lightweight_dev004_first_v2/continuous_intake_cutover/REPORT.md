# DEV004 Continuous-Intake Cutover Feasibility

DEV004_CONTINUOUS_INTAKE_CUTOVER_STATUS: HOLD — preparation with continuous V1 intake is feasible; complete replacement using only existing accepted mechanisms is not qualified. No implementation or production action.

Frozen authority: evidence base63f93b4ce383556e01a9da77ba5699fdd88ed25b; FLEX_STABLE_V2=2d825bbcf27f802bd0bfcef90cedeba43cc968eb; executable DEV004 candidate506fe25c remains unchanged. Current installed listener/receipt hashes still match accepted65da0e22 and prior67-control offline reconciliation. Hashes for the listener, receipt owner, writer, handoff, resolver and Walkback are in authority.json. Existing source integration is evidence-only, not silently certified as installed DEV004. Prior passing matrices and gap counterexample reused.

## EXISTING_DURABLE_INTAKE_AUTHORITY

Installed listener: /Users/kevinkeaveney/Production/flex-listener. Resolver: /Users/kevinkeaveney/Production/flex-main-workers. Walkback: /Users/kevinkeaveney/Production/flex-walkback-dev002. Selective Supervisor configuration remains the retained DEV090 operational-restoration authority; no current PID/state acceptance or service action is claimed by this feasibility check.

Core V1 authority is /Users/kevinkeaveney/Dev/claude/flex/database/flex_complete_database.db; OPS V1 authority is the sibling wt_ops_v2.db (the filename does not make it the lightweight replacement). No production DB was opened. Listener binds DB_PATH at module initialization, starts its writer/watchdog with that path, and resolves OPS separately. Secondary CreatorRepository and receipt/journal bindings must also remain explicit.

Compact receipts retain supplied mint/creator/signature, local reception/acceptance and persistence outcome. Their strict allowlist excludes full CREATE transaction, logs, funding history and complete source payload. A migration receipt records BEFORE_OPS_SUBMIT/ATTEMPTED; it is not proof of queue commit or complete migration replay. These are useful identity/provenance proofs, not a complete ordered event log.

Core canonical rows preserve successful writes. birth_persist_queue, migration_persist_queue, webhook birth retry and failure journals retain specific pending/failed work. The migration fallback importer commits queue rows before removing its journal and deduplicates signatures. They do not journal every successful event or supply a complete cross-database change stream. Core migration_walkback_handoff records intent, OPS enqueue/readback follows, then Core completion is recorded. Quarantine preserves excluded/conflicting work; it must not be treated as ordinary retryable admission. Existing acquisition checkpoints concern already admitted jobs, not all incoming WebSocket events.

## OPTION_A_PAUSED_CUTOVER

HOLD for lossless intake. A writer pause could create a stable source pair after transactions drain, but stopping the listener also stops PumpPortal/PumpSwap subscriptions. Accepted subscriptions lack replay cursors; receipts cannot contain never-received messages. Existing latest100 recovery remains incomplete for the150-event case. No zero-loss pause duration is established.

## OPTION_B_CONTINUOUS_INTAKE

GO for preparation only: build/qualify small V2 fixtures while the listener remains authoritative on V1. HOLD for final transition. A final copy is already stale if V1 writes after its boundary. There is no accepted complete cross-store delta/change journal or listener DB-only fence/drain/rebind protocol in the inspected path. Separate source snapshots or MAX(rowid) values would not capture all updates/deletes/ACKs or establish a paired boundary.

Changing environment/configuration does not rebind existing module constants, writer threads and database handles. A live filename/symlink replacement is not a substitute: the disposable SQLite control proves an existing connection reads V1 while a fresh connection sees V2. Independent Core/OPS replacements expose CoreV2/OPSV1. The test used rollback-journal fixtures; do not attempt live WAL file replacement, whose sidecar/handle hazards are additional reasons to reject that operation.

## OPTION_C_DURABLE_HANDOFF

HOLD as a full transition bridge. Existing intents, queues and journals can recover their own acknowledged/unacknowledged work within an established authority. They cannot cover all successful source mutations and every incoming event during a binding change. No accepted switch mode directs every birth/migration to a complete durable replay boundary while database writers are drained. Deliberately causing DB errors to divert traffic to fallback files is not a supported capture mode and has no demonstrated complete/bounded guarantee. Retaining V1 permanently as the ingestion database could keep intake continuous, but is not the requested V1 replacement or a qualified lightweight cutover.

## CORE_OPS_CONSISTENCY / INTAKE_GAP_RISK

SQLite atomicity is per connection/database transaction here. Core intent, OPS queue and Core ACK can straddle a crash; accepted idempotent convergence helps only when both authorities and source facts are retained. There is no online paired-generation switch. No option currently proves continuous intake plus complete final synchronization using existing mechanisms alone. No provider completeness assumption is introduced.

## QUEUE_AND_QUARANTINE_RECONCILIATION

A future transition must carry final canonical births/CREATE/migrations, pending receipt outcomes, retry attempts and deadlines, quarantine/conflicts/exclusions, handoff intents/OPS admissions/ACKs, completed-job dedup and projection-recovery state. Preserve original identities and evidence; reconcile leases only after their prior owners are fenced. Preserve acquisition checkpoint/finalization and consumed/reserved provider budgets in place or through verified authority transfer; do not reset allowance. Existing Birdeye BIRDEYE-only authority and all provider budgets remain unchanged. This report neither copies live leases nor establishes a final live-state image.

## ROLLBACK_FEASIBILITY

Before V2 accepts writes, aborting preparation leaves V1 untouched. After V2 accepts writes, a path flip back to V1 is insufficient: canonical/queue/reference/receipt and budget deltas must first converge without dual authority. Preserve V1 and its actual WAL state; do not delete or copy only the main file from a live store. No reverse-reconciliation owner is newly qualified.

## Storage measurement

Available filesystem bytes at observation:5,131,051,008 (4.779GiB), above the2,000,000,000-byte receipt floor; this is not reserved capacity. Existing disposable Core fixture8,970,240bytes and OPS13,754,368bytes, combined22,724,608bytes; both allocations equal their lengths. This is one fixture, not a final full-state size estimate.

The small100-row WAL semantics probe measured WAL12,392bytes, main4,096bytes and SHM32,768bytes before close. Maximum new fixture file32,768bytes; process single-file ceiling16,000,000bytes. These are observed test costs, not production WAL or transition bounds. Final canonical/queue delta volume, capture duration, WAL pinned by snapshots and rollback catch-up storage remain UNKNOWN without an accepted transition protocol. No full DB copy was made.

The5GiB combined V2 target is a planning objective, not an enforceable cap; a single5GiB SQLite file would violate the500,000,000-byte individual-file requirement. At observed free space, adding5GiB is not feasible even before preserving the receipt floor and transient growth. A22.7MB fixture does not prove the final cutover budget. Existing V1 remains in place.

## MINIMUM_SAFE_CUTOVER_DESIGN

No executable design is GO within the existing-mechanisms-only constraint. The smallest missing capability is a supported listener-local database-write fence/drain and paired-generation rebind, with every received birth/migration durably accounted while the fence is held, restart fencing and bounded replay/delta acknowledgement. Existing failure receipts/queues do not establish that contract. This identifies a requirement, not a proposed new buffering subsystem or implementation authorization. Discovery publication also needs its previously specified complete source generation; continuous mutable V1 intake does not resolve that gate.

GO_HOLD_DECISION: HOLD. Concrete blocker: no accepted intake-preserving durable write fence and paired Core/OPS authority transfer. Stop under the user's rule; no new architecture built.

NEXT_ACTION: Obtain authorization for one bounded DEV feasibility contract for a listener-local writer fence/rebind using existing queues, specifically testing whether those queues can account for every accepted birth/migration during the fence. If that cannot be demonstrated without a new buffer, retain HOLD and keep V1 authoritative. Do not schedule a maintenance pause or rely on provider gap recovery.

Three small SQLite controls pass (old-handle persistence, mixed paired replacement, fixture size). They prove unsafe-switch counterexamples, not full service equivalence. RUNTIME_DEPLOYMENT_REQUIRED=false: read-only architecture and disposable semantics tests only. Zero provider calls/live WebSockets/production writes/restarts/deployment/cutover/deletion. New raw retention and runtime growth paths:0. No files reach500MB.
