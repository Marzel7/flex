# X78.28 — Operational Intelligence Freshness Recovery

Date: 2026-08-10 (Europe/London)  
Branch: `classification-attribution-axis`  
Baseline HEAD: `cb1fc110e105436c4baa9fe15f956628f80db3ce`

## Executive result

The 4.14-million-second `watch pipeline` age did not describe current
Operational Intelligence or canonical WATCHTOWER monitoring. It described a
legacy, in-Gunicorn WATCH candidate processor whose lifecycle is deliberately
disabled in production. Current Operational Intelligence snapshots were fresh
throughout the investigation and canonical WATCHTOWER websocket monitoring
remained connected.

The 19-token missing-creator cohort was fully enqueued, but its nominal P0
priority (`100`) placed it in the same FIFO scheduling band as the two-day
historical queue. The cohort was elevated to priority `200`, which enters the
existing `>100` scheduling band while remaining below the `>200` expanded-RPC
boundary. All 19 rows completed through the existing resolver; the authoritative
one-hour missing count fell from 19 to 0.

Operational Intelligence's own five signals are now normal. Platform readiness
is still blocked by Creator Funding: its oldest eligible item is approximately
3.57 million seconds old and the current worker has recorded zero completions.
No readiness clock was started.

## Repository and service baseline

The worktree was already dirty with the X78.19–X78.27 repair series and other
unrelated changes. Those changes were preserved. At the measured baseline:

| Service | PID before | State |
| --- | ---: | --- |
| `watchtower_api` | 60095 | RUNNING |
| `watchtower_listener` | 67013 | RUNNING |
| `creator_funding_worker` | 64569 | RUNNING |
| `creator_resolution_worker` | 57921 | RUNNING |
| `intelligence_snapshot_scheduler` | 41768 | RUNNING |
| `walkback_worker` | 41735 | RUNNING |
| `ws_cascade` | 41781 | RUNNING |
| `alert_evaluator` | 41789 | RUNNING |

Baseline infrastructure, database and ingestion were healthy. Database p99 was
6.14 ms, serializer depth 0 and WAL 19.9 MB. Both ingestion sockets were
connected. The legacy heartbeat row was last updated at 2026-06-23 17:30:30 UTC
with status `error` and `database is locked`; its age was about 4,135,644
seconds. The current Operational Intelligence snapshot scheduler simultaneously
reported fresh 24h, 7d, 30d and all-time products.

## Watch pipeline source and meaning

- Health source: `src/core/main.py` (health assembly around line 25,518).
- Legacy executor: `_run_watch_pipeline()` at line 32,324.
- Legacy lifecycle owner: `_start_wt_candidate_processor()` at line 34,205.
- Persisted source: `wt_worker_heartbeat`, key `watch-pipeline`, using
  `last_seen`; configured interval 900 seconds.
- Successful progress: completion of the legacy candidate build/classify/
  cluster/discovery cycle followed by a heartbeat write.
- Production ownership: none. Supervisor runs Gunicorn with
  `FLEX_ENABLE_FLASK_BACKGROUND_WORKERS=0`, intentionally suppressing that
  in-process worker.

The authoritative current path is:

```text
production data
  -> intelligence_snapshot_scheduler (PID 41768)
  -> src.ops.operational_intelligence.build_operational_intelligence
  -> persisted versioned snapshots
  -> snapshot_health.classify_snapshot_health
  -> /api/health/full
```

It is periodic, supervised and independent of the retired legacy heartbeat.
Current WATCHTOWER monitoring is separately owned by `ws_cascade` (70 active
subscriptions during validation). There is no active cursor requiring reset and
no cursor was changed.

Root-cause verdict before repair: **F — HEALTH_PROJECTION_STALE**.  
Dependency between the retired watch heartbeat and creator resolution:
**INDEPENDENT**.

## Missing-creator cohort

The cohort was taken directly from the health predicate:

```sql
migrated_at >= now - 3600
AND lifecycle_stage = 'migrated'
AND earliest_tx_creator IS NULL
AND pf_ws_creator IS NULL
```

Initial census: 19. All 19 had migration signatures, no creation signature, and
an existing `creator_resolution_queue` row with status `pending`, priority 100,
reason `missing_creator_p0`, source `crq_worker`, attempts 0 and no prior error.

Initial classification:

| Classification | Count |
| --- | ---: |
| NOT_ENQUEUED | 0 |
| QUEUED_PENDING | 19 |
| CLAIMED | 0 |
| RETRYING | 0 |
| RESOLUTION_FAILED | 0 |
| RESOLVED_NOT_PERSISTED | 0 |
| RESOLVED_NOT_CONSUMED | 0 |

Queue-wide baseline was 1,235 pending, 5,981 complete, 353 skipped and 143
ignored. The oldest pending row dated to 2026-08-08. Throughput was 93 completes
in 15 minutes and 216 in one hour. `process_queue()` schedules priority `>100`
before the generic FIFO band; priority 100 therefore did not give recent P0
work precedence.

Post-repair lifecycle for the exact cohort was observed at 12 complete / 7
running, then 17 complete / 2 running, then 19 complete / 0 missing. The final
health query returned `missing_creators_1h=0`.

Primary root-cause verdict: **A — QUEUE_BACKLOG**, with a bounded scheduling
priority defect for current missing-creator work.

## Repair

1. `src/core/creator_resolution_queue.py`
   - Changed P0 priority from 100 to 200.
   - Added an idempotent, one-hour-bounded promotion for unresolved migrated
     tokens already in pending/retry state.
   - Preserved the existing RPC policy: expanded history remains restricted to
     priority `>200`.
2. `src/core/creator_resolution_worker.py`
   - Runs the bounded promotion before each queue claim and reports the count in
     heartbeat metadata.
3. `src/core/main.py`
   - Separates the retired legacy watch heartbeat from current snapshot health.
   - Exposes lifecycle, legacy age and current snapshot version/age/worker.
   - Sizes creator-resolution liveness from batch size and observed p95 runtime,
     because heartbeats are written at batch boundaries.
4. `src/ops/mission_control_capabilities.py`
   - Reports the legacy watch worker as retired rather than stale.
   - Adds the authoritative snapshot freshness signal.
   - Uses the emitted adaptive creator-resolution heartbeat threshold.

No creator attribution, funding attribution, Operation, identity, governance,
relationship, Primitive, Discovery or motif semantics changed.

## Deterministic validation

Focused test command collected 70 tests:

- `tests/test_x78_28_operational_intelligence_freshness.py`
- `tests/test_mc1_1_capability_layer.py`
- `tests/test_x67_28_snapshot_scheduler.py`
- `tests/test_x78_27_listener_enqueue_connection_ownership.py`
- `tests/test_x78_27_intelligence_refresh_singleflight.py`
- `tests/test_x78_25_wal_pin_watchdog.py`
- `tests/test_x78_26_broad_price_tracking_decommission.py`

Result: **70 passed**, 2 existing FastAPI deprecation warnings, 16.87 seconds.
Python compilation also passed for every touched module.

## Deployment

Only affected services were restarted:

| Service | Before | After | Reason |
| --- | ---: | ---: | --- |
| `creator_resolution_worker` | 57921 | 69682 | bounded priority promotion |
| `watchtower_api` | 60095 | 70504 | corrected health projection |
| `watchtower_listener` | 67013 | 69765 | future enqueue priority |

The scheduler, Creator Funding, walkback, websocket cascade and alert evaluator
were not restarted.

## Post-repair evidence

Final measured health:

- Operational snapshot: `FRESH`, version 383, scheduler PID 41768, age 140.1 s.
- Legacy watch pipeline: `RETIRED`; legacy age retained as 4,136,391 s for audit.
- Creator Resolution: 22 resolved and 3 skipped in its first completed cycle;
  heartbeat age 78 s against a measured p95-based threshold of 563 s.
- Missing creators in the current one-hour window: 0.
- Database: `HEALTHY`, p99 12.91 ms, serializer depth 0.
- WAL: 12.3 MB and checkpointing; no persistent risk state.
- Ingestion: `HEALTHY`; PumpPortal/PumpSwap connected, queues 0, births and
  migrations current.
- Token Prediction: remains decommissioned.
- Broad Price Tracking: remains decommissioned.
- Operational Intelligence dependency on Token Prediction: **NO**. Legacy
  display/candidate readers remain in retired code only.
- Operational Intelligence dependency on Broad Price Tracking: **NO**.

## Readiness gate

The authoritative readiness clock was **not started**. Creator Funding remains
the independent blocker: although its process and heartbeat are live, the
oldest eligible work is approximately 3,565,767 seconds old and the active
worker reports `total_completed=0`. The final listener PID also had not yet
reached the required 15-minute stability window after its scoped restart.

## Final verdicts

- Watch Pipeline: **B — LEGACY_PIPELINE_RETIRED_CORRECTLY**
- Missing Creator Attribution: **A — RECOVERED**
- Operational Intelligence: **B — RECOVERING** (all own signals healthy;
  platform status inherited from Creator Funding)
- Production Health: **C — DEGRADED / READINESS_BLOCKED**
- Evidence Activation: **HEALTH_REPAIR_REQUIRED**
- Acquisition: **HOLD_ACQUISITION**
