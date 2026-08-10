# X78.16 — Post-Repair Write-Lane Stabilization & Readiness Proof

Date: 2026-08-10 (Europe/London)

## Outcome

Readiness was **not started**. The repaired services remained available, but the prerequisite gates did not all pass: Creator Funding did not produce three qualifying fresh completions, database latency remained volatile, and Operational Intelligence had no valid upstream-healthy test window.

No Evidence Platform or acquisition component was enabled.

## Repository baseline

- Branch: `classification-attribution-axis`
- Starting commit: `749bcb9f6f9d8af2ab466752a5b355661f3c0877`
- X78.13: `463ae476`
- X78.14: `d9da7dfd`
- X78.15: `749bcb9f`
- Unrelated working-tree changes were preserved.
- Disk headroom: 56 GiB available.

## Runtime findings

### Listener

The expected X78.15 listener PID 34488 exited at 10:01:17 BST. The last listener diagnostic identified:

`CRITICAL_LISTENER_DB_HANDLE_LEAK fd_count=13 threshold=12`

A low-frequency descriptor census on the replacement process found two primary database handles and then one. The 13-handle reading was therefore a transient concurrency sample, not a persistent descriptor leak. WAL and SHM descriptors were already excluded by X78.15.

The watchdog now requires three consecutive high samples before a fatal restart. A recovered sample resets the streak. The final deployed listener PID was 35434. Both feeds continued carrying events, queues remained empty, and no further fatal watchdog event was observed during the bounded checkpoint.

### Creator Funding

PID 34622 remained alive and the X78.14 bounded cancellation contract remained intact. Natural timeouts remained near 90 seconds with immediate cleanup; no orphan-task, unbounded cleanup, or false WAL-watchdog restart recurred.

One apparent heartbeat stall was inspected with exactly one bounded stack sample. The process was not deadlocked: the main event loop was waiting on an executor task actively scanning SQLite (`sqlite3_step` / `pread`). It later recovered without intervention, refreshed its heartbeat, and claimed another mint. Classification: `READ_UNDER_WRITE_LEASE` / bounded post-completion enrichment, not a true worker stall.

The required completion proof nevertheless failed. No three qualifying jobs were observed after the final deployment. Stale-running recovery acknowledgements, known-creator skips, and historical completions were excluded as required.

### Creator Resolution

PID 33850 remained alive. Completed rows advanced from 5,396 to at least 5,417 during observation, cycles advanced, the heartbeat remained fresh, and no failure or poisoning was observed. The deep-read/write-lane separation from X78.15 did not regress.

### Database

The database remained volatile. Observed samples ranged from healthy low-latency operation to p99 values of approximately 3.55 seconds and 24.7 seconds. One material holder was attributed to Creator Funding (`creator_funding_worker.py:202 in _db_connect`), aged about 14.4 seconds, and released on the next check. The bounded process sample captured a finite SQLite scan rather than an unexplained stuck holder.

Serializer queue depth reached two during the highest-pressure sample. WAL remained small (roughly 0.8 MiB at the final high-pressure sample), so the event was not a critical WAL pin. There was no unexplained holder proven beyond 60 seconds and no timeout storm, but p99 was persistently unsafe enough to fail readiness.

### API and ingestion

API master PID 30675 remained stable. `/api/health/full` and `/intelligence/operators` returned HTTP 200 during production-safe loopback checks.

PumpSwap remained connected. PumpPortal briefly entered `RETRYING` while event persistence continued; the five-minute birth rate recovered to roughly 16.8/min, last birth remained recent, migrations continued, and listener queues remained empty. This is active but recovering ingestion, not silence.

### Operational Intelligence

Watch-pipeline freshness remained roughly 4.1 million seconds stale. Because Listener qualification, three Creator Funding completions, and stable database pressure were not simultaneously achieved, no causal upstream-healthy interval existed. Operational Intelligence was not modified.

## Regression validation

Focused validation completed successfully:

- 35 tests passed in 0.72 seconds.
- Included listener health recovery, Creator Funding cancellation cleanup, write-lane boundaries, reconnect isolation, retry handling, and nested-write protection.
- No full regression suite was launched.

## Gate result

| Gate | Result |
|---|---|
| Listener final deployment stable for 15 minutes | Not proven in the bounded post-restart checkpoint |
| Both feeds operational | Partial: PumpPortal briefly retrying; events persisted |
| Birth/migration persistence | Pass |
| Creator Funding three fresh completions | Fail |
| Creator Funding liveness | Pass at final check |
| X78.14 cancellation contract | Pass |
| WAL watchdog false restart absent | Pass |
| Creator Resolution progress | Pass |
| No stuck database holder | Pass for captured holders |
| Database not persistently AT_RISK | Fail |
| API master and availability | Pass |
| Operational Intelligence recovering | Not tested; insufficient window |
| WAL healthy | Pass |
| Disk headroom adequate | Pass |

## Final verdicts

- Listener: **B — REPAIRED_AND_STABLE** (bounded checkpoint; full 15-minute qualification not claimed)
- Creator Funding: **B — DEGRADED_BUT_PROGRESSING**
- Creator Resolution: **B — TRANSIENT_CONTENTION_RECOVERED**
- Live Ingestion: **RECOVERING**
- Database: **VOLATILE / READINESS BLOCKED**
- Operational Intelligence: **INSUFFICIENT_WINDOW**
- Production readiness: **NOT STARTED**
- Evidence activation: **HEALTH_REPAIR_REQUIRED**
- Acquisition: **HOLD_ACQUISITION**

## Decision

The listener watchdog defect was repaired narrowly and deterministically. No speculative database or intelligence change was made. The platform is serving traffic and upstream workers are progressing, but X78.16 cannot claim readiness until the completion and database-stability gates are met in a later clean observation window.
