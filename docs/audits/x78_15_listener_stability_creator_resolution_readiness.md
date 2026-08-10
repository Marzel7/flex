# X78.15 — Listener Stability, Creator Resolution Contention & Readiness Recovery

Date: 10 August 2026 (Europe/London)

## Outcome

X78.15 found three independent production defects. Two caused listener restart
instability and one caused Creator Resolution to retain the global write lane
during deep population reads.

The scoped fixes are:

- PumpPortal recovery now measures the current outage, rather than the age of
  the last successful connection. A first disconnect after a long healthy
  session is therefore retryable rather than immediately fatal.
- reconnect subscription seeding reads the active-token population through a
  genuine read-only connection.
- Creator Resolution closes its schema/write connection before both deep
  population scans and performs those scans through SQLite `mode=ro`.
- the listener FD watchdog counts primary database descriptors (connections),
  not each connection's database, WAL, and SHM companion descriptors.
- Creator Funding's WAL watchdog now requires both a WAL at/above the
  configured 64 MB threshold and three persistent busy samples. Previously
  either condition alone caused a process exit.

No Evidence Platform component was activated and no acquisition was run.

## Evidence and classification

### Listener restart reconstruction

Supervisor recorded unexpected listener exits at 09:01:53, 09:12:58,
09:20:10, 09:27:46, and 09:32:57 BST before the scoped deployment. Listener
logs tied the early sequence to:

`FATAL: 1 consecutive failures, ... since last connection`

The process was comparing the wall-clock age of an earlier healthy connection
to a three-minute outage threshold. This is a reconnect-policy defect, not a
recurrence of X78.13's never-connected startup defect.

After that correction, Supervisor recorded unexpected exits at 09:46:12 and
09:48:43. Each was directly preceded by:

`CRITICAL_LISTENER_DB_HANDLE_LEAK fd_count=14 threshold=12`

An `lsof` census proved that the watchdog's substring match counted the main
database, `-wal`, and `-shm` descriptors independently. At the checkpoint,
five matching file descriptors represented only three primary database
descriptors. This caused normal concurrent SQLite use to be classified as a
fatal connection leak.

The final deployed listener is the first process containing all three listener
corrections. PID 34488 remained stable for the bounded six-minute checkpoint,
past both former 2–3 minute watchdog restart intervals. Readiness is assessed
from that deployment only.

### Creator Resolution timeout owner

The observed `CrossProcessDatabaseWriteTimeout` identified
`creator_resolution_queue.py:51 in connect` as a write-lane owner while the
worker performed a full eligible-population scan. Committing schema work did
not release the cross-process lease because lease lifetime followed connection
lifetime. Closing that connection before entering `mode=ro` scans removes the
long-holder class without changing claim, resolution, retry, or funding-handoff
semantics.

After deployment, Creator Resolution advanced from 5,375 to 5,392 completed
rows. One subsequent cycle still reported ordinary SQLite contention; the
queue was not poisoned and continued advancing.

### Creator Funding

PID 33085 was not restarted during this repair. X78.14 cancellation cleanup did
not recur: supervised timeout logs report `extraction cleanup complete` with
`cleanup=0.0s` and no unbounded owned-task or future drain.

Four same-timestamp completions at 08:48:43 UTC were stale-running recovery
acknowledgements and are excluded. The first verified fresh end-to-end
completion was:

- `5i8moQucM7iuNr3z...` / creator `6Nh9gVwZ3wRa...`, completed 08:48:54 UTC,
  normal extraction, 10.5 seconds, zero funders.

The preceding `GYTEiu6a...` completion was a known-creator skip backed by 510
already stored funders and is also excluded from fresh proof.

At 09:52:51 BST, the unchanged worker exited on
`CRITICAL_WAL_PINNED` with a 38.8 MB WAL, below its configured 64 MB alert.
The condition was `size >= threshold OR busy_cycles >= 3`; normal concurrent
writers therefore forced a restart. The minimal repair changes this to require
both a large WAL and persistent checkpoint contention. PID 34622 remained up
through the former three-cycle failure interval. This is a newly proven
watchdog defect, not an X78.14 cancellation regression.

### Database, API, ingestion, and intelligence

The first post-repair health projection measured database write p99 at 0.01 ms,
serializer depth 0, WAL 27.4 MB, and 17.1 writes/minute. A prior post-deploy
sample reached 10,851 ms while the old listener subscription seed still held
the write lane; that path is now read-only. This contrast proves improvement
but is not yet a stable readiness window. The final projection was again
`AT_RISK`, with p99 6,012.05 ms, serializer depth 0, WAL 0.2 MB, and 22.6
writes/minute. Database volatility therefore remains directly measured.

The Gunicorn master remained PID 30675. A direct loopback health request
returned HTTP 200 in 0.907 seconds. The earlier apparent curl refusal was a
sandboxed loopback-access result; `lsof` and the escalated health request
confirmed the master and worker listening on port 5002. Final loopback checks
returned HTTP 200 for `/api/health/full` (2.243 seconds) and
`/intelligence/operators` (0.018 seconds).

Live ingestion recovered to connected PumpPortal and PumpSwap flows and
persisted births/migrations; the final birth was three seconds old and both
ingestion queues were empty. The earlier sampled 15-minute birth rate remained
below its 17.87/min baseline. Operational Intelligence remained stale, with
the watch pipeline age measured at 4,115,856 seconds. Because upstream health
had not yet established an eligible interval, its internal semantics were not
investigated.

## Validation

Focused production-path regression:

- 34 tests passed in 0.70 seconds.
- reconnect isolation, startup retry, Creator Resolution write boundaries,
  cancellation cleanup, cross-thread reaping, RPC metrics lease handling, and
  autocommit write-lane behavior were included.
- no full-suite run was used to manufacture a readiness result.

## Readiness gates

No readiness clock started. At the observed checkpoint:

- fewer than three fresh Creator Funding completions were proven;
- the final listener deployment had not yet completed a 15-minute stable
  interval;
- database health had not remained stable for an eligible interval;
- Operational Intelligence had no healthy upstream interval in which to test
  recovery.

Therefore the 15-, 30-, and 60-minute checkpoints are not applicable. This is
an explicit gate failure, not an incomplete or implied readiness window.

## Verdicts

- Listener: **B — REPAIRED_AND_STABLE** for the bounded checkpoint; this is not
  a 15- or 30-minute readiness claim.
- Creator Resolution: **B — TRANSIENT_TIMEOUT_RECOVERED**.
- Creator Funding: **B — DEGRADED_BUT_PROGRESSING**.
- Database: **C — VOLATILE / READINESS_BLOCKED**.
- Operational Intelligence: **D — INSUFFICIENT_HEALTHY_WINDOW**.
- Production Health: **C — DEGRADED / READINESS_BLOCKED**.
- Evidence Activation: **HEALTH_REPAIR_REQUIRED**.
- Acquisition: **HOLD_ACQUISITION**.

## Crash-safe checkpoint

- Listener final deployment: 09:50 BST; PID 34488.
- Creator Resolution deployed PID: 33850.
- Creator Funding initial PID: 33085; final WAL-watchdog deployment PID: 34622.
- API master PID: 30675 (unchanged).
- Creator Funding fresh completions: 1 proven at the initial checkpoint.
- X78.14 cancellation recurrence: NO.
- Readiness start: NOT STARTED.
- 15m / 30m / 60m: NOT APPLICABLE.
- Evidence Activation: HEALTH_REPAIR_REQUIRED.
- Acquisition: HOLD_ACQUISITION.
