# X78.27 — WAL Checkpoint Gap, Database-Lock Attribution & API Availability Recovery

Date: 2026-08-10  
Branch: `classification-attribution-axis`  
Baseline commit: `cb1fc110e105436c4baa9fe15f956628f80db3ce`  
Mode: live attribution, bounded repair, no Evidence activation or acquisition

## Executive result

The previously fixed checkpoint boundary was real, but it was not permanent SQLite corruption. Live correlation identified overlapping Creator Funding post-extraction global graph rebuilds as the common cause of the long WAL snapshot/write-lane hold and the observed Creator Resolution lock waits. The per-job global rebuild was removed from the hot path; its existing four-hour cron owner remains unchanged. Creator Funding now also rejects overlapping post-extraction refreshes with a process-local non-blocking single-flight gate.

After that deployment the WAL repeatedly advanced, caught up, and reset. Creator Resolution completed successful cycles without new failed resolutions, and the API remained responsive throughout bounded, privileged localhost probing.

Pre-readiness then exposed a separate listener connection-ownership defect. The listener watchdog recorded a queue-worker connection from `_enqueue_creator_funding_job._enqueue_sync` older than 60 seconds with `CLOSE_FAILED_WRONG_THREAD`; primary database descriptors remained at 17–21 for three samples, causing two natural listener exits. The priority/cache connection was closed only on its success path. Both that connection and its secondary update connection now close in owner-thread `finally` blocks. The focused regression suite passed and only the listener was restarted to deploy it.

Formal readiness did **not** start. The final listener deployment had not accumulated the required 15-minute stable PID window, and `/api/health/full` independently reported stale Operational Intelligence: watch pipeline age about 4.14 million seconds, Creator Resolution freshness 179 seconds, and 25 migrated tokens missing a creator in the last hour. The contract requires stopping before readiness when Operational Intelligence is an independent blocker.

## Repository and process baseline

The worktree was already dirty with prior X78 and unrelated changes. Those changes were preserved. Relevant baseline services were:

| Service | Baseline PID | Relevant final PID | Observation |
|---|---:|---:|---|
| API master | 60095 | 60095 | master unchanged |
| API worker | 60096 | 61881 during audit | normal Gunicorn `max_requests` recycle; no master failure |
| Listener | 61030 | 67013 | two natural FD-watchdog exits, then one deliberate repair deployment |
| Creator Funding | 60957 | 64569 | two deliberate deployments; final repair removes global rebuild |
| Creator Resolution | 57921 | 57921 | unchanged |
| Walkback | 41735 | 41735 | unchanged |
| Intelligence snapshot scheduler | 41768 | 41768 | unchanged |
| WS cascade | 41781 | 41781 | unchanged |
| Alert evaluator | 41789 | 41789 | unchanged |

The final repair deployment was listener `65288 → 67013`. Creator Funding `64569`, Creator Resolution `57921`, and API master `60095` were not restarted with it.

## Root-cause separation

| Blocker | Runtime evidence | Root cause | Shared? | Repair |
|---|---|---|---|---|
| WAL checkpoint gap | Valid PASSIVE samples showed a fixed boundary while the WAL grew; Creator Funding was the only process retaining the decisive DB/WAL handles; completed global builds took roughly 984–1013 seconds. After the holder ended/restarted, the boundary advanced and WAL reset. | Creator Funding timed out waiting after 30 seconds, but `asyncio.to_thread` continued the synchronous global rebuild. Later jobs started overlapping rebuilds. | Shared with Creator Resolution lock pressure. | Removed duplicate global graph/relationship rebuild from the per-creator hot path; retained its existing cron owner; added single-flight refresh guard. |
| Creator Resolution lock | A natural lock event identified Creator Funding's `build_networks_release.py:32 in db_transaction` as the current write-lane owner. Creator Resolution later completed cycles with zero failed resolutions. | `GLOBAL_FLOCK_WAIT` / `BUSY_FROM_OTHER_CONNECTION`, caused by the overlapping Creator Funding global rebuild, not a Creator Resolution self-pin. | Shared with WAL blocker. | Same Creator Funding repair. No resolution semantics, queue behavior, or RPC behavior changed. |
| API availability | Privileged localhost probes consistently returned HTTP 200. Master 60095 and port 5002 remained present. One worker recycled while the master stayed stable; Gunicorn is configured with `max_requests=500`. No current timeout, boot failure, or socket loss was captured. | Prior intermittent failure was not reproduced; observed worker lifecycle was expected. | No DB causal claim supported. | None. |
| Listener restart | Connection snapshots showed `_enqueue_sync` queue-worker handles at 57–103 seconds, `CLOSE_FAILED_WRONG_THREAD`, and 17–21 primary DB descriptors for three watchdog samples. Supervisor recorded two unexpected exit-status-1 restarts. | Exception before `_check.close()` leaked the cache/priority connection; secondary `conn2` also lacked guaranteed closure. | Independent readiness blocker discovered during qualification. | Owner-thread `finally` closure for both connections. |

The intelligence snapshot scheduler produced bounded reader periods, but the checkpoint boundary advanced after those periods. It is transient checkpoint contention, not the persistent pin root cause.

## WAL time-series findings

Representative post-Creator-Funding-repair samples:

| WAL bytes | PASSIVE tuple | Classification |
|---:|---|---|
| 82,432 | `(0, 20, 20)` | fully checkpointed |
| 8,895,? | `(0, 2159, 2064)` | advancing |
| 14,757,872 | `(0, 3391, 3335)` | catching up |
| 7,634,392 | `(0, 1853, 1705)` | advancing after bounded reader |
| 36,820,472 | `(0, 7515, 7462)` | catching up |
| 5,883,392 | `(0, 1428, 1307)` | WAL generation reset and advancing |

Samples `(1,-1,-1)` were classified only as `CHECKPOINT_LOCK_UNAVAILABLE`; they were not treated as proof of a pinned reader. No post-repair persistent fixed boundary was observed.

Immediately after the first Creator Funding-only restart the checkpointed boundary advanced from 12,829 to 16,073 and the WAL fell from roughly 66 MB to 128 KB, then reached a fully checkpointed `(61,61)` sample. This is the strongest causal release observation.

## API and current health

The bounded post-repair probe series returned HTTP 200 throughout. Typical latency was about 0.48–1.81 seconds, with isolated 4–5 second samples under load. No failure capture existed on which to justify an API code change.

The final health payload reported:

- infrastructure `HEALTHY`;
- database `HEALTHY`, p99 wait 2.3 ms, serializer queue depth 0, about 162.8 writes/min;
- live ingestion `HEALTHY`, PumpPortal and PumpSwap connected, current births and migrations present;
- broad price worker `DECOMMISSIONED`;
- Creator Funding worker `RUNNING`, heartbeat age 6 seconds;
- Operational Intelligence `WARNING` with an independently stale watch pipeline and recent missing creator attribution.

Disk had about 54 GiB available. Evidence Platform remained disabled. No acquisition ran.

## Repairs

### Creator Funding hot-path ownership

- Added a non-blocking, process-local single-flight gate around post-extraction intelligence refresh.
- Removed `take_snapshot`, direct `build_networks_release`, and `rebuild_after_scan` from the per-creator path.
- Preserved durable funding extraction, queue handoff, risk scoring, live network membership, and the lightweight IRC candidate update.
- Preserved the global graph rebuild's existing four-hour cron lifecycle owner.

### Listener queue connection ownership

- Guaranteed closure of the creator-funding priority/cache connection on success, timeout, and exception.
- Guaranteed closure of the secondary `token_analysis` update connection on success and exception.
- No queue, creator, funding, attribution, or ingestion semantics changed.

## Validation

Completed:

```text
python -m py_compile src/core/pumpfun_curve_listener.py src/core/creator_funding_worker.py

pytest -q \
  tests/test_x78_27_listener_enqueue_connection_ownership.py \
  tests/test_x78_27_intelligence_refresh_singleflight.py \
  tests/test_x78_19_connection_ownership_metrics.py \
  tests/test_x78_19_birth_persist_queue.py \
  tests/test_x78_25_wal_pin_watchdog.py \
  tests/test_x78_26_broad_price_tracking_decommission.py

33 passed
```

An earlier broader focused invocation completed 23 tests and had one failure in `test_x78_22_creator_funding_sql_boundary.py::test_trigger_migration_failure_is_fail_open`. The assertion expects the old optimized prediction-trigger log, while X78.24/X78.26 deliberately decommissioned those triggers. It also fails in isolation and was not caused by the X78.27 repairs. No full regression suite was claimed.

## Readiness qualification

`UPSTREAM_HEALTHY_START` was not persisted because all pre-readiness gates did not pass.

Stop conditions encountered:

1. listener restarted naturally twice due the now-repaired FD leak;
2. after the final listener repair, the required 15-minute same-PID gate was not yet satisfied;
3. Operational Intelligence returned `INDEPENDENT_BLOCKER` from the current health evidence.

Therefore:

- 15-minute checkpoint: **not accepted**;
- 30-minute minimum readiness: **not started**;
- 60-minute preferred readiness: **not started**;
- Evidence activation: **not authorized**.

## Final verdicts

WAL: **C — PIN_IDENTIFIED_AND_REPAIRED**

Creator Resolution: **B — TRANSIENT_LOCK_RECOVERED**

API: **A — STABLE**

Database: **B — TRANSIENT_ATTRIBUTED_CONTENTION**

Operational Intelligence: **C — INDEPENDENT_BLOCKER**

Production Health: **D — NEW_DEFECT_FOUND**

Evidence Activation: **HEALTH_REPAIR_REQUIRED**

