# X78.21 — Kernel Flock Attribution & Listener Connection Closure

Date: 10 August 2026

## Outcome

X78.21 removed the remaining ambiguity around the physical SQLite write lock and repaired one deterministic listener connection leak. It did **not** restore production readiness. Creator Funding remains the identified write-lane blocker, and the listener still reached its protective file-descriptor restart threshold while waiting behind those holds.

## Kernel flock attribution

The write-lock file now contains an immutable, lock-bound owner record while the process physically owns the kernel flock. A separate descriptor can probe the flock without trusting the sidecar:

- `FREE`: the probe acquires and immediately releases `LOCK_EX | LOCK_NB`.
- `HELD`: the probe reads the owner record from the same lock inode and reports PID, thread, transaction ID, caller, acquisition time, inode and device.

The sidecar remains operational telemetry, but it is no longer the authority for physical lock ownership.

During qualification, ordinary held samples matched the sidecar and kernel-bound owner. One sidecar-null episode was also fully attributed by the kernel record:

- waiter: Creator Resolution PID 41724, RPC metrics flusher;
- wait: 53.347 seconds;
- physical holder: listener PID 42438, thread `asyncio_exec_11`;
- transaction: `de8550a5-ded0-4c97-83e3-6b781ab565bd`;
- caller: `db_locking.py:887 in managed_db_connect`.

This proves a null sidecar no longer means an unknown physical owner.

Repeated long holds were attributed to Creator Funding PID 41682, primarily `realtime_creator_funding_extractor.py:1309 in extract_for_creator`. Several exceeded 90 seconds and some exceeded 140 seconds. A bounded process sample showed the worker inside SQLite execution and B-tree page reads. Connection-origin telemetry does not expose the exact SQL statement, so no statement-level claim is made.

**Kernel verdict: A — KERNEL FLOCK ATTRIBUTION COMPLETE.**

## Listener connection closure

The production leak was at `_enrich_read` in `pumpfun_curve_listener.py`. It opened a read-only connection, executed a query, and closed the connection only on the success path. Exceptions or cancellation could leave the connection registered and open.

The path now uses `managed_db_connect(..., read_only=True)` as a context manager, giving it owner-thread, exception-safe and cancellation-safe closure. The connection reaper also refuses to foreign-close any connection marked as holding or waiting for the write lane.

Before repair, two `_enrich_read` connections remained for more than 137 seconds and emitted `CLOSE_FAILED_WRONG_THREAD`. After listener restart, that caller did not recur during the 15-minute observation.

The broader listener qualification nevertheless failed: tracked connections created by other callbacks accumulated while Creator Funding held the write lane. The listener reached 12–18 main-database descriptors for three consecutive watchdog cycles and restarted cleanly. The restart was protective and supervised; it was not proof that the repaired `_enrich_read` path regressed.

**Listener verdict: B — LEAK IDENTIFIED AND REPAIRED; production qualification remains failed because blocked connection accumulation persists.**

## Worker and database health

- Creator Funding remained live but degraded. It produced one fresh successful completion during the window, below the required three-completion gate. Most preceding claims timed out after 90 seconds, and long SQLite holds blocked other services.
- Creator Resolution made genuine progress: cycles 6 and 7 each processed five items and resolved three, but each also recorded one write-lane failure and DB p99 values around 24–26 seconds.
- WAL remained bounded and checkpointed from approximately 14 MB to below 1 MB before growing only to approximately 2 MB.
- API and supervised workers remained available, but the listener restarted after its descriptor watchdog threshold.
- No second-hop activity was observed.

**Residual database verdict: C — VOLATILE WITH IDENTIFIED BLOCKER.**

**Creator Funding verdict: B — DEGRADED BUT PROGRESSING; completion gate not met.**

**Production health verdict: C — DEGRADED / READINESS BLOCKED.**

**Evidence activation: HOLD.**

**Acquisition: HOLD_ACQUISITION.**

The readiness clock did not start because the no-hold-over-60-seconds condition failed and Creator Funding did not produce three fresh completions.

## Validation

Focused regression suite:

```text
31 passed in 8.02s
```

Covered:

- exact kernel attribution when the sidecar is absent;
- deterministic flock and observation identities;
- active write-lane connections excluded from foreign-thread reaping;
- listener `_enrich_read` context-managed closure contract;
- X78.9 cross-process timeout behaviour;
- X78.10 listener retry behaviour;
- X78.11B reaper safety;
- X78.19 ownership metrics;
- X78.20 null-owner recovery.

## Files changed

- `src/core/database_write_service.py`
- `src/utils/db_locking.py`
- `src/core/pumpfun_curve_listener.py`
- `tests/test_x78_20_null_owner_recovery.py`
- `tests/test_x78_21_listener_enrich_cleanup.py`
- this audit report

No Evidence, acquisition, attribution, reconciliation, resolver or governance behaviour was changed.
