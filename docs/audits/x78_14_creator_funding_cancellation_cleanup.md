# X78.14 — Creator Funding Cancellation Cleanup & Operational Recovery

Date: 2026-08-10 (Europe/London)
Branch: `classification-attribution-axis`
Starting commit: `463ae4767e7f49251350a86df474f78f8552b8c7`

## Executive result

Creator Funding's cancellation boundary was repaired and validated locally and
once against a real supervised job. Production readiness was **not** started:
the listener continued restarting, Creator Resolution reported a database
write timeout, and the Creator Funding health projection had not recovered to
healthy after the final worker restart.

Evidence Platform remained disabled. No acquisition ran.

## Root cause and causal proof

The observed 197.7-second job was not one indivisible 90-second extraction.
It was the composition of three defects:

1. `creator_funding_worker._process_job` used
   `wait_for(shield(extraction_task), 90)`, but the extraction performed
   synchronous SQLite write-lane acquisition on that same event-loop thread.
   A 60-second lane wait could prevent the 90-second timer callback from
   executing on time.
2. After cancellation, the worker diffed the process-wide asyncio task set,
   waited another 20 seconds, and handed remaining tasks to an unbounded
   next-job straggler gate. Ownership was heuristic and could include unrelated
   work from the singleton extractor.
3. `CancelledError` from a successfully cancelled task was logged as
   "did not finish cleanup within 10s". Live proof: the first repaired timeout
   logged that warning yet reported `cleanup=0.0s`; separating the exception
   branches proved this was a false cleanup-failure classification. Queue-state
   persistence then accounted for the remainder of its 113.7-second claim
   wall time.

Exact classifications:

- `CLEANUP_BLOCKED_ON_DB`
- `NONCANCELLABLE_THREAD_WORK`
- `DETACHED_ASYNC_TASK` / incorrect global ownership boundary
- `QUEUE_FINALIZATION_BLOCK`
- false `CancelledError` telemetry classification

## Timeout and ownership contract

```text
creator_funding_worker._process_job
  -> extraction Task (owned by the job)
     -> extract_funding_for_new_token
        -> main funding scan
        -> gather: Jito / deBridge / Axiom / outgoing
        -> tracked CEX / BlockSec / post-launch tasks
        -> tracked outgoing-transfer executor futures
```

- Job timeout: 90 seconds.
- Write waits preserve the normal 60-second policy while time remains, but a
  context-local monotonic deadline prevents a synchronous wait crossing the
  extraction deadline.
- Async children are cancelled with their owning job.
- Executor futures are explicitly retained and awaited; cancellation is not
  treated as thread termination.
- Resource cleanup budget: 5 seconds inside the extraction, guarded by the
  worker's existing 10-second ceiling.
- Timeout queue persistence: bounded to 3 seconds; the existing stale-running
  reaper is the durable fallback if the database cannot accept the transition.
- No process-global task diff or unbounded straggler gate remains on the live
  job path.

## Cancellation/retry census

| Boundary | Timeout / retry | Cancellation |
|---|---:|---|
| Job extraction | 90s | explicit Task cancel |
| JSON-RPC | 30s per request, up to 5 retries | aiohttp coroutine cancellation |
| Enhanced HTTP calls | 30s | shared acquisition coroutine cancellation |
| SNS/domain request | shared async request timeout | parent cancellation propagates |
| Outgoing DB save | deadline-bounded write wait | OS thread is not killed; future remains owned |
| Background CEX/BlockSec/automation | task-specific internals | owned task cancel + gather |
| Timeout queue transition | 1s lane/SQLite bound, 3s outer bound | stale reaper fallback |
| RPC metrics | best effort | drop/retry safe; not awaited by job cleanup |

`except Exception` sites do not catch `asyncio.CancelledError` on Python 3.11.
`gather(return_exceptions=True)` remains inside the owned extraction Task, so
parent cancellation cancels its children. No shield exists below the worker's
explicit top-level shield except around the truthful executor future handle.

## Resource and queue evidence

- Pre-fix production wall: up to ~197.7s.
- First repaired natural timeout: timeout fired at the 90s boundary; cleanup
  acknowledgement was immediate (`cleanup=0.0s`). The then-unfixed terminal
  queue write produced a 113.7s total claim wall.
- Final deployed cancellation evidence (during controlled worker stop):
  `extraction cleanup complete ... cancelled=true`.
- The scoped fixture retained a non-cancellable executor future after its
  awaiter was cancelled, then proved the future was removed only after the OS
  thread really finished.
- Twenty sequential scoped cancellations left zero owned tasks, zero owned
  futures, and no thread-count growth.
- Final queue census: complete 6,780; expired 622; failed 1; pending 17,456;
  retry 2; running 1. Three rows advanced to complete during the deployment
  window, but they were stale-row recoveries, not sufficient proof of three
  fresh end-to-end production completions.
- WAL at final sample: 1.08 MB, not pinned.

## Validation

64 distinct focused assertions passed across:

- new X78.14 scope/deadline/repeated-timeout tests;
- X78.2 detached-task/job-boundary tests;
- X78.3 RPC-cache lease reproduction;
- X78.4 cancellation and queue-write tests;
- X78.9 cross-process timeout tests;
- X78.10 release safety;
- X78.11/11b RPC-metrics and reaper poisoning;
- X78.12 resolver lifecycle;
- X78.13 symbol, schema, autocommit, resolution and risk boundaries.

`py_compile` and `git diff --check` passed. No full regression run was
claimed. A single bounded macOS `sample` captured the worker after the first
timeout; it showed the event loop polling and executor threads, not a surviving
per-job write owner. It was not treated as a T+90 stack because it occurred
after queue finalization had begun.

## Deployment record

Only `creator_funding_worker` was restarted. The final deployed PID was 33085.
The listener and API were not intentionally restarted for this repair.

Independent production failures observed:

- listener PID changed repeatedly (31238 -> 32062 -> 32954), so listener
  stability failed;
- API supervisor state was RUNNING, but localhost was initially unavailable;
  it later returned health JSON;
- final health JSON classified Creator Funding `WARNING` / `STALLED`, with a
  136-second pre-restart heartbeat;
- Creator Resolution heartbeat status was `error` with a
  `CrossProcessDatabaseWriteTimeout`;
- an earlier sample showed write p99 24,525.86ms; the final API projection
  later showed database pressure `HEALTHY` and p99 43.67ms, so pressure was
  volatile rather than a sustained validated recovery;
- migrations continued (five in the bounded final 15-minute query window),
  but listener restarts precluded declaring ingestion healthy;
- Operational Intelligence was not causally validated after a healthy
  upstream interval because that prerequisite never existed.

## Readiness checkpoints

- Readiness start: **NOT STARTED**.
- 15m: not applicable.
- 30m: not applicable.
- 60m: not applicable.

The stop gates were already present: listener restarts, stale/degraded worker
liveness, Creator Resolution database timeout, and insufficient fresh
Creator Funding completion proof.

## Required verdicts

Cancellation: **A — CANCELLATION CLEANUP BOUNDED AND VALIDATED**

Creator Funding: **B — DEGRADED BUT PROGRESSING**

Operational Intelligence: **C — STILL STALE / CAUSE UNRESOLVED**

Production Health: **C — DEGRADED / READINESS_BLOCKED**

Evidence Activation: **HEALTH_REPAIR_REQUIRED**

Acquisition: **HOLD_ACQUISITION**
