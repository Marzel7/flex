# OIP v2.2E.0 — Production Evidence Authority & Migration Health Readiness

Audit date: 2026-08-10 (Europe/London)  
Repository: `classification-attribution-axis` at `a4f030a63378df7af65713d7eae6c0d58be20035`  
Mode: read-only, failed closed

## Final decision

Production Evidence Authority: **B — EVIDENCE_PLATFORM_NOT_CURRENTLY_ACTIVE_IN_PRODUCTION**

Authoritative DB: **NONE**

Platform Health: **D — NEW_BLOCKER_FOUND**

v2.2E Disposition: **PRODUCTION_EVIDENCE_ACTIVATION_REQUIRED_INSTEAD_OF_MIGRATION**

Acquisition: **HOLD_ACQUISITION**

Compact migration did not begin. No migration state, outbox, sidecar, trigger,
writer pause, authority switch, Evidence mutation, RPC acquisition, deletion, or
service restart was performed.

## 1. Repository baseline

- Branch: `classification-attribution-axis`
- HEAD: `a4f030a63378df7af65713d7eae6c0d58be20035`
- v2.2D.1 implementation commit remains in ancestry: `d5cdd60f`
- v2.2E failed-closed preflight record is HEAD: `a4f030a6`
- The pre-existing dirty worktree was preserved. No unrelated path was staged,
  reverted, or modified by this audit.

## 2. Production process and lifecycle census

The production application is owned by the single Supervisor configuration at
`config/supervisor/supervisord.conf` (supervisord PID 3115, parent PID 1 at the
audit snapshot). Its observed project processes were:

| Process | Observed lifecycle/state | Database role |
|---|---|---|
| `watchtower_api` / gunicorn | Supervisor; respawned around 08:15; new master PID 28061 and worker PID 28062 | legacy application DB; no Evidence handle |
| `pumpfun_curve_listener` | Supervisor; recently restarted/starting during audit | legacy application DB; no Evidence handle |
| `creator_funding_worker` | Supervisor; long-lived process | legacy application DB READ/WRITE; no Evidence handle |
| `creator_resolution_worker` | Supervisor; long-lived process | legacy application DB READ/WRITE; no Evidence handle |
| `walkback_worker` | Supervisor; long-lived process | legacy application DB and `wt_ops_v2`; no Evidence handle |
| `intelligence_snapshot_scheduler` | Supervisor; long-lived process | legacy application DB and `wt_ops_v2`; no Evidence handle |
| `ws_cascade` | Supervisor; long-lived process | `wt_ops_v2`; no Evidence handle |
| `alert_evaluator` | Supervisor; long-lived process | application telemetry; no Evidence handle |
| Evidence writer/mirror/normalizer/primitive replay | **No supervised production process exists** | none |

Configured but stopped processes included infra sync, operation scheduler,
WATCHTOWER Helius monitor, webhook worker, and dust observatory enrichment.
No systemd/launch-agent Evidence lifecycle or alternative production Evidence
writer was located.

## 3. Evidence configuration census

`src/evidence/config.py` defines the default Evidence DB as
`database/evidence_platform/evidence.db`, with sibling intake queue, artifact
store, and mirror spool paths. The default DB does not exist.

All relevant Evidence flags default OFF:

- `EVIDENCE_PLATFORM_ENABLED`
- `EVIDENCE_WRITER_ENABLED`
- `EVIDENCE_QUEUE_ENABLED`
- `EVIDENCE_ARTIFACT_STORE_ENABLED`
- `EVIDENCE_HEALTH_ENABLED`
- `EVIDENCE_MIRROR_ENABLED`
- `EVIDENCE_NORMALIZATION_ENABLED`
- `EVIDENCE_PRIMITIVE_ENGINE_ENABLED`

No `EVIDENCE_*` override was present in `.env`, Supervisor configuration, or
the safely inspected runtime environments for the API, listener, and Creator
Funding worker. There was no open file handle under
`database/evidence_platform` and no process had an Evidence DB, WAL, SHM,
artifact store, queue, or mirror spool open.

### Live write-path state

The shared acquisition capability exists in code and Creator Funding remains a
legacy production consumer. The Evidence mirror, intake writer, normalization,
Primitive generation, Discovery, and motif/relationship pipeline are not
enabled as a live production chain. The Evidence writer entry point is a code
capability only; it has no production supervisor owner, configured DB, or live
queue.

## 4. Evidence database candidate census

Candidates were classified by path and their milestone manifests/checkpoints,
not by size or recency. Device for the listed files was 16777234.

| Candidate | Approx size | Inode | Classification |
|---|---:|---:|---|
| `database/oip_v2_1a_pilot/evidence.db` | 1.3 GB | 479418584 | OIP_PILOT |
| `database/oip_v2_1c_retry_failover/evidence.db` | 1.6 GB | 479632130 | OIP_STAGED |
| `database/oip_v2_1e_stage_1000/evidence.db` | 2.5 GB | 479740085 | OIP_STAGED |
| `database/oip_v2_1f_stage_1000/evidence.db` | 3.5 GB | 479810399 | OIP_STAGED |
| `database/oip_v2_1g_stage_2000/evidence.db` | 3.5 GB | 480364215 | OIP_STAGED |
| `database/oip_v2_1g_stage_2000_frozen/evidence.db` | 5.3 GB | 480371059 | OIP_FROZEN |
| `database/three_sw2_shadow_ep3_2a/evidence.db` | 10 MB | 478883403 | SHADOW |
| `database/watchtower_shadow/evidence.db` | 365 MB | 478384067 | SHADOW |
| `database/watchtower_shadow_ep3_0d/evidence.db` | 741 MB | 478454728 | SHADOW |

The expected production default is absent. None of the candidates has a live
writer, a live reader, production configuration authority, or an
operation-neutral production identity. Expensive corpus scans were deliberately
not run: direct configuration, lifecycle, file-handle, and manifest evidence is
sufficient to exclude these corpora, and additional scans would worsen an
already degraded platform.

The active `database/flex_complete_database.db` (about 10.6 GB) is the legacy
application database. Its active WAL/SHM and writer handles do not make it an
Evidence Platform database.

## 5. Authority decision

**Is any live process currently using an Evidence Platform database? NO.**

Authority proof:

1. The configured default Evidence DB is absent.
2. Every Evidence feature flag is disabled and has no production override.
3. No Evidence writer or consumer has a supervised production lifecycle.
4. No live process holds an Evidence DB, WAL, SHM, queue, artifact, or spool.
5. All discovered Evidence DBs have direct pilot/staged/frozen/shadow identity.

The current architecture is therefore production acquisition plus legacy
intelligence, with Evidence Platform persistence existing only in offline and
shadow corpora. Selecting the largest or newest corpus would invent authority.

## 6. Mission Control health baseline

Final read-only health snapshot returned overall `WARNING`:

| Capability | State | Direct evidence |
|---|---|---|
| Infrastructure | HEALTHY in final snapshot | DB p99 0 ms, serializer depth 2, WAL 0.6 MB; this had recently been AT_RISK with p99 22,904.38 ms |
| Live Ingestion | WARNING | 2.00 births/min vs 17.93 baseline; last birth 742 s; sockets connected |
| Creator Funding | WARNING / STALLED | heartbeat 990 s; 17,434 pending; oldest pending 3,560,105 s; 101 claimed, **0 completed**, 16 retried |
| Operational Intelligence | WARNING | watch pipeline age 4,110,546 s; creator resolution age 743 s; four recent migrated tokens missing creator |
| WATCHTOWER | WARNING | cascaded degradation from operational intelligence; cascade itself connected |

This is not a healthy baseline. The short apparent recovery in the database
pressure gauge does not negate process restarts, repeated lock errors, zero
Creator Funding completions, ingestion collapse, or stale operational
intelligence.

## 7. Lock and serializer diagnosis

Bounded recent logs contain a sustained series of `DB_LOCK_ERROR` events in
`repository.py:508 get_next_creator_activity_job` and
`db_locking.py:621/_patched_connect`. Additional observed classes include:

- `NestedDatabaseWriteError` during API startup for webhook initialization,
  operator schema startup, and RPC metrics table setup.
- `CrossProcessDatabaseWriteTimeout` with a 60-second waiter and writers in
  usage tracking, migration storage, price service, and RPC cache paths.
- Listener nested-write failures during database initialization.

The earlier 22,904.38 ms p99 is retained in Creator Resolution worker metadata.
The health endpoint does not expose enough samples to attribute a mathematically
exact p50/p95/p99 decomposition, so no false decomposition is asserted.
Available evidence supports a lock-contention/nested-write defect class, not
normal transaction execution. No current permanent lock holder was proved in
the final snapshot, and no lock was broken or released by this audit.

## 8. Creator Funding diagnosis and progress

Classification: **LOCK_CONTENTION / TRUE_STALL**.

The process exists, but existence is not progress. Across the bounded health
observations its heartbeat aged from 180 seconds to 990 seconds, pending work
remained approximately 17.4k, and completed work remained zero. The worker
metadata continued to report a processing mint while producing no completed
jobs. This rules out a merely stale label and fails migration readiness.

## 9. Listener and ingestion diagnosis

The listener had a recent restart and changed state again during this audit.
The API also respawned during the audit. Logs show dense recent lock errors and
nested database-write failures, but the available Supervisor logs do not retain
enough exit metadata to prove one unique listener exit reason. Restart root
cause is therefore classified **UNKNOWN WITH ACTIVE LOCK-RELATED EVIDENCE**, not
silently labelled as a websocket reconnect or manual action.

Current sockets were connected and log heartbeat was fresh, but birth flow was
only 2.00/min against a 17.93/min baseline. Current ingestion is therefore
degraded despite live sockets.

## 10. Health root causes and blocker

- Creator Funding: active defect/stall with lock-contention evidence.
- Live Ingestion: active degraded flow; recent listener restart.
- Operational Intelligence: active stale pipelines.
- API/listener lifecycle: active restart instability observed during audit.
- Database pressure: recently severe, temporarily healthy at final sample; not
  sufficient to establish stability.

The newly observed API respawn plus listener state transition during the audit
is a migration-relevant blocker. No service was restarted because restart was
not authorized and would have hidden rather than diagnosed the condition.

## 11. Migration readiness criteria and stability window

Required criteria were frozen before any window:

- database pressure not AT_RISK;
- no stuck holder, permanent nesting, crash loop, or timeout storm;
- Creator Funding heartbeat and genuine completions moving;
- healthy live ingestion and stable listener;
- operational intelligence fresh;
- WAL not critically pinned;
- adequate disk headroom.

Stability window: **NOT_STARTED**.

The healthy-baseline gate never passed. Consequently there are no synthetic
15/30/60-minute checkpoints, and waiting would not make this milestone pass.

## 12. Disk and headroom

- Filesystem available: 62,020,732 KiB (about 63.5 GB decimal)
- Filesystem usage: 71%
- Legacy application DB: about 10.6 GB
- Authority projection directory: about 1.7 GB
- Shadow migration workspace: about 6.7 GB
- Evidence candidates: approximately 10 MB to 5.3 GB each as listed above

Migration headroom: **NOT APPLICABLE** because no authoritative production
Evidence corpus exists. A headroom calculation against a shadow corpus would be
misleading.

## 13. Production Evidence lifecycle gap

There is currently nothing that can truthfully be called a production Evidence
corpus to migrate. The correct next lifecycle is activation, not migration:

1. repair and observe production health;
2. define an explicit, reviewed production Evidence authority contract;
3. deploy the compact representation as the initial production persistence
   format;
4. enable the Evidence writer/mirror deliberately;
5. only then consider production consumers.

### Required explicit production configuration

Future activation must explicitly configure:

- absolute authoritative Evidence DB path;
- absolute Artifact Store path;
- single writer process and Supervisor ownership;
- intake queue and mirror spool;
- authority DB/projection path;
- compact-provenance mode;
- health endpoint and alert ownership;
- reader consumers and their authority mode.

Startup must fail closed if a path is absent, a shadow/OIP path is supplied, two
authorities are configured, or writer ownership is ambiguous. It must not
silently create a production DB from the current default.

## 14. Required next actions

1. Open a separate health-repair task for Creator Funding, listener/API restart
   instability, and the nested/cross-process write failures.
2. Prove genuine worker and ingestion progress, then complete a new 30-minute
   minimum readiness window.
3. Define and review the explicit production Evidence authority contract.
4. Replace v2.2E migration with a bounded production Evidence activation plan.
5. Keep acquisition on hold.

## Final report fields

- Production Evidence Authority: **B — EVIDENCE_PLATFORM_NOT_CURRENTLY_ACTIVE_IN_PRODUCTION**
- Authoritative DB: **NONE**
- Evidence Platform live in production: **NO**
- Evidence writer: **NONE**
- Evidence consumers: **NONE**
- Evidence feature flags: **ALL DISABLED**
- Database pressure: **recently AT_RISK; final point-in-time HEALTHY; stability not established**
- Write p50/p95/p99: **p99 22,904.38 ms recent; exact p50/p95 unavailable from exposed bounded telemetry**
- Cross-process lock: **recent contention/timeouts; no permanent current owner proved**
- Recent timeout classification: **ACTIVE LOCK CONTENTION / NESTED WRITE DEFECT**
- Creator Funding: **STALLED; 0 completions**
- Queue progress: **NO GENUINE PROGRESS PROVED**
- Listener restart: **UNKNOWN WITH ACTIVE LOCK-RELATED EVIDENCE**
- Live Ingestion: **WARNING / below birth baseline**
- WAL: **0.6 MB final sample; not critically pinned**
- Disk: **about 63.5 GB free**
- Stability window: **NOT_STARTED**
- New blocker: **API/listener restart instability plus continuing worker/ingestion degradation**
- Production Evidence Authority Verdict: **B — EVIDENCE_PLATFORM_NOT_CURRENTLY_ACTIVE_IN_PRODUCTION**
- Platform Health Verdict: **D — NEW_BLOCKER_FOUND**
- v2.2E Disposition: **PRODUCTION_EVIDENCE_ACTIVATION_REQUIRED_INSTEAD_OF_MIGRATION**
- Acquisition: **HOLD_ACQUISITION**

