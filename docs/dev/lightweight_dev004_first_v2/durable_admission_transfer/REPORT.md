# DEV004 Durable Admission and Queue Authority Transfer

DEV004_DURABLE_TRANSFER_STATUS: HOLD.

Decision: NO_LOSSLESS_BOUNDARY_WITHOUT_INDEPENDENT_DURABLE_STORAGE — meaning complete, cutover-independent admission authority is required. Physical fallback files already exist; this does not assert that a new service or general-purpose queue is necessary. None of the existing accepted owners currently supplies the required complete contract. No implementation was performed.

Frozen authority: evidence base10716e65248d7cffdbaf4c715e78c27ca5b56bcd; baseline FLEX_STABLE_V2=2d825bbcf27f802bd0bfcef90cedeba43cc968eb; installed accepted65da0e22 listener SHA256751d40d14150b112513cfbed44329cf1c9f0e3e58f34369dd8f29550a1cd3a5f freshly matches. DEV004 executable candidate506fe25c unchanged; previous receipt/source reconciliation remains evidence-only and accepted. No historical, discovery, schema or price investigations reopened. Receipt/retry/quarantine semantics, BIRDEYE-only credentials and budgets unchanged.

## BIRTH_ADMISSION_AUTHORITY

Successful canonical birth persistence belongs to Core token_analysis and associated canonical CREATE authority. Compact receipts are identity/outcome proofs, not complete replay records. Compact PumpPortal create retries belong to Core webhook_birth_queue; general birth retries belong to Core birth_persist_queue. They require the very database intentionally fenced at final reconciliation.

The existing <DB_PATH>.birth_fallback.jsonl can be written without SQLite, but it is a failure-path journal rather than universal pre-dispatch admission. The actual helper closes a normal text append without fsync; this task observed zero fsync calls. Its PumpPortal-provenance variant bounds record/file size (4096bytes/2000000bytes), but that does not prove crash/power-loss durability. The compact frame handler's failed retry path explicitly has no raw/file fallback. Previous five actual-handler controls establish this boundary; no repeat of those controls.

## MIGRATION_ADMISSION_AUTHORITY

Initial PumpPortal/PumpSwap migration routing schedules handle_migration in memory. The handler first acquires/extracts transaction evidence; migration_persist_queue and migration_walkback_handoff are later Core persistence/intent owners, not durable admission before dispatch.

The existing <DB_PATH>.migration_fallback.jsonl stores mint/signature/source/received_at/optional slot after final-write and queue-insert failure. It uses O_APPEND and fsync on the file, so it is genuinely independent of SQLite at that stage. It is not called for every raw notification before the task can be lost, does not retain every input/evidence field, has no generation/owner transfer marker and has no enforced total file-size cap in the inspected helper. File fsync alone does not establish durable first-file directory publication. No claim that appending twice creates two canonical migrations: the importer has queue uniqueness; the file itself contains duplicate records.

## RETRY_AND_HANDOFF_AUTHORITY

| Work | Existing owner | Independent of fenced Core/OPS? |
|---|---|---|
| Compact birth retries | Core webhook_birth_queue | No |
| General birth persistence retries | Core birth_persist_queue | No |
| Migration persistence retries | Core migration_persist_queue | No |
| Migration Walkback intent/completion | Core migration_walkback_handoff | No |
| Pending creator resolution | Core creator_resolution_queue | No |
| Walkback leases/retry/attribution/projection | OPS wt_walkback_queue and canonical/projection tables | No |
| Birth/migration fallback files | DB_PATH-derived filesystem journals | Physically yes; incomplete admission/durability/transfer contract |
| Quarantine and receipt outcomes | Existing Core/OPS quarantine and filesystem receipt authority | Preserve; not a replacement event stream |

The accepted Core intent → OPS enqueue/readback → Core completion contract supports idempotent recovery within known authorities. It does not make those commits atomic or give a migration notification durable identity before asynchronous dispatch.

## WRITE_FENCE_BEHAVIOUR

Reused prior controls: excluding retry writes from the fence permits new V1 commits; including them prevents new compact durable retry admission. A raw notification received during that interval must not be called durably admitted. No new retry logic was added to fenced V1. Already committed/pending durable V1 records remain available and must be preserved, but that does not cover received-only tasks.

## QUEUE_TRANSFER_FEASIBILITY

Existing file helpers remain physically writable without opening Core/OPS: freshly tested with no database files/connections. However changing DB_PATH changes fallback lookup to the V2-derived filename; pending V1 journal records are not automatically followed. The actual helper test leaves those V1 records intact and demonstrates the new path is absent. This is a namespace/ownership gap, not deletion.

Minimum viable boundary would require one durable admission authority with explicit capacity, fsync/publication semantics, deterministic identity, quarantine-safe handling, stable ordering, an admission acknowledgement, and a frozen transfer watermark. It must remain writable while V1 canonical/retry owners are fenced, then transfer exactly one active drain owner to the matched V2 pair. Existing journals may be candidates for reuse only after a separately authorized bounded contract/correction; this task does not certify that such a correction is minimal or sufficient. No new independent buffer is implemented or assumed.

## CRASH_RESTART_RECOVERY / PAIRED_REBIND_READINESS

HOLD. Five new actual-helper diagnostics pass: files write without SQLite; birth append has no fsync; migration append fsyncs its file; duplicate migration append retains two lines; DB rebind changes journal lookup and strands its pending V1 namespace unless explicitly reconciled. Maximum scratch file312bytes before results; process file ceiling16000000bytes. Reopen/readback does not certify power-loss survival.

The requested full matrix (before/during fence, crash transfer, restart before activation, stale handle, V2 admission and rollback) is NOT passed. Prior duplicate-birth/pending-queue and stale-handle controls are reused as limited evidence. Durable-owner stop rule applies before end-to-end transfer tests: inventing a new test-only owner would not qualify existing Flex. No live leases copied, quarantine consumed, completed attribution changed or provider allowance reset. Paired startup/reverse reconciliation remain unqualified.

GO_HOLD_DECISION: HOLD — no accepted complete durable owner accepts all new births/migrations independently of fenced V1. Existing filesystem failure stores are not sufficient as currently wired. They must not be mislabeled absent, but neither can they be treated as a lossless universal queue.

NEXT_ACTION: Explicitly authorize only a bounded DEV admission-journal contract/correction, preferably reusing the existing files, covering durable-before-dispatch records, strict byte/file limits and exclusive generation-bound drain/transfer. If that additional capability is outside scope, stop with V1 authoritative; do not schedule cutover. No new general-purpose queue, providers or production actions are authorized by this report.

RUNTIME_DEPLOYMENT_REQUIRED=false (source inspection/provider-free helper diagnostics only). No application source changes, production database opens/writes, configuration/credential changes, provider calls, live WebSockets, restarts, deletion, deployment or cutover. No large database copies or new runtime storage paths.
