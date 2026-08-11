# X78.19 — Listener Connection Ownership and Second-Hop Metrics

Date: 2026-08-10

## Outcome

X78.19 added bounded, fail-open listener connection lifecycle diagnostics and
per-generation second-hop build metrics. It did not alter relationship scoring,
exclusions, topology, RPC acquisition, Evidence, or governance.

Production readiness did not start. The final health sample remained WARNING,
database pressure was AT_RISK, Creator Funding had no three qualifying fresh
completions, and no natural production second-hop build completed during the
observation window.

## Implemented instrumentation

- Every tracked connection now receives a UUID and records open/close metadata:
  PID, thread, async task, caller fingerprint, purpose, lifecycle intent, mode,
  timestamps, age, transaction state, and write-lane state.
- Failed native closes remain in the live registry and emit `close_failed`.
- Listener watchdog snapshots correlate primary database descriptors with the
  process-local registry, excluding WAL/SHM, with connection growth/recovery IDs
  and age buckets.
- Second-hop builds emit a generation ID, trigger source, read/materialization
  phases, graph cardinalities, TEMP footprint, output deltas, publication phase
  timings, write-lane wait/hold, outcome, and failure type.
- Both streams are JSONL and fail open.

## Proven observations

### Connection lifecycle

The initial instrumented deployment proved that the existing reaper attempts to
close tracked connections from `db-conn-reaper`. SQLite rejects those closes when
the connection was created on the main thread:

`sqlite3.ProgrammingError: SQLite objects created in a thread can only be used in that same thread`

The old registry ordering removed the entry before native close, hiding the
still-open object. X78.19 now removes the entry only after successful native
close. The instrumentation initially exposed a separate double-close regression;
the final implementation preserves sqlite3's idempotent close contract and has a
deterministic regression test.

Final PID 39707 produced bounded early samples:

| Sample | Primary DB FDs | Registry | Delta | Oldest tracked age |
|---|---:|---:|---:|---:|
| 1 | 0 | 0 | 0 | none |
| 2 | 2 | 1 | 1 | 0.0s |

No object in those samples exceeded one second. The short observation is not a
15-minute qualification and does not prove that the historic growth is repaired.

### Residual write lane

Post-X78.18 production repeatedly timed out with `current_owner=null`. A controlled
creator-resolution restart immediately released one blocked interval, but the
condition recurred with Creator Funding and Creator Resolution active. That is
process correlation, not exact caller proof, so no transaction-boundary patch was
made.

Final serializer sample:

- p50 wait: 0.0ms
- p95 wait: 8.24ms
- p99 wait: 8,762.06ms
- queue depth: 5
- p99 commit: 1.03ms
- top measured wait: `usage_tracker`, 4,381.03ms average over two writes
- infrastructure pressure: AT_RISK

The low commit time and high wait time locate the problem in lane acquisition,
but blank owner metadata prevents exact long-holder attribution.

### Second-hop

The metric contract was validated deterministically on the frozen X78.18 fixture:

- read materialization write lane owned: false
- source rows and output rows: exact
- phase timings: emitted
- publication wait/hold: emitted
- metrics path failure: build remains successful
- previous generation preservation: unchanged

No natural production build completed after the final worker deployment. Therefore
production duration, trigger rate, redundancy, and publication ratio remain
unmeasured rather than estimated.

### Runtime

- API: serving; master PID 30675 remained up.
- Listener: feeds connected, but final same-PID window was under 15 minutes.
- Creator Funding: RUNNING heartbeat, but observed genuine jobs timed out/retried;
  zero qualifying fresh completions were proven.
- Creator Resolution: RUNNING after controlled restart; freshness 40s in final
  health response.
- Ingestion: PumpPortal and PumpSwap CONNECTED; birth rate below baseline.
- Operational Intelligence: WARNING; pipeline freshness remained stale.
- WAL: 27MB.
- Disk free: 56.5GB.
- Evidence activation: not performed.
- Acquisition expansion: not performed.

## Validation

- Focused X78.13–X78.19 operational regression: 51 passed.
- Final X78.18/X78.19 instrumentation suite: 11 passed.
- Python compilation: passed.
- No full-suite rerun was started.

## Required verdicts

- Listener Connections: **E — OWNER_UNRESOLVED**
- Second-Hop: **A — ISOLATION_VALIDATED_IN_PRODUCTION** only for the X78.18
  transaction boundary; X78.19 natural-build performance qualification remains
  incomplete.
- Residual Database: **D — VOLATILE_OWNER_UNRESOLVED**
- Creator Funding: **C — STALLED**
- Production Health: **C — DEGRADED / READINESS_BLOCKED**
- Evidence Activation: **HOLD**
- Acquisition: **HOLD_ACQUISITION**

