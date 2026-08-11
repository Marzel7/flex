# X78.25 — WAL Pin Attribution & Creator Resolution Watchdog Recovery

Date: 2026-08-10 (Europe/London)

## Verdict

- Root cause: `C — WATCHDOG_FALSE_POSITIVE`.
- WAL: `B — WATCHDOG_FALSE_POSITIVE_IDENTIFIED_AND_REPAIRED`.
- Creator Resolution: `C — DEGRADED` (stable repaired PID, but a lock-error cycle remains).
- Database: `C — VOLATILE_WITH_IDENTIFIED_BLOCKER`.
- Production Health: `C — DEGRADED / READINESS_BLOCKED`.
- Evidence Activation: `HEALTH_REPAIR_REQUIRED`.
- Acquisition: `HOLD_ACQUISITION`.

The false-positive restart defect is repaired. This report does not claim a
30- or 60-minute production qualification.

## Repository and runtime baseline

- Branch: `classification-attribution-axis`.
- Baseline HEAD: `cb1fc110e105436c4baa9fe15f956628f80db3ce`.
- X78.19–X78.24 changes were already present and remain uncommitted; unrelated
  dirty files were preserved.
- Main DB: 10,600,730,624 bytes.
- Initial WAL: 44,104,632 bytes; SHM: 196,608 bytes.
- SQLite: WAL journal, 4,096-byte pages, `wal_autocheckpoint=1000`.
- Initial supervised processes included Creator Resolution, Creator Funding,
  listener, API, walkback, intelligence scheduler, WS cascade, and alert
  evaluator.

## Watchdog implementation audit

Before repair, `src/core/creator_resolution_worker.py`:

- sampled every 60 seconds;
- used `PRAGMA wal_checkpoint(PASSIVE)`;
- retained only tuple element zero as `busy`;
- configured a 64 MB size threshold and three busy cycles;
- exited through `os._exit(1)` when **either** size exceeded 64 MB **or** three
  busy samples accumulated.

This did not match the stated contract. It discarded `log_frames` and
`checkpointed_frames`, treated checkpoint-lock contention as a reader pin, and
allowed either signal to restart the process independently.

### Historical restart reconstruction

- PID 54802: 61.1 MB at busy cycle 2, then 72.9 MB at busy cycle 3; exited.
- PID 56256: 73.0 MB at busy cycle 1; exited immediately because size alone
  satisfied the `OR` condition.
- PID 56524: exited at **42.3 MB** after busy cycle 3. This is conclusive proof
  that the 64 MB threshold was not actually required.
- PID 57630 subsequently experienced write-lane timeouts with `current_owner=null`.

The historical holder list was an `lsof` descriptor census, not reader-snapshot
attribution. It cannot prove which connection pinned an old frame.

## Checkpoint observations

Bounded PASSIVE observations returned both forms:

1. `(busy=1, log=-1, checkpointed=-1)` while another checkpoint owned the
   checkpoint operation. This is an unavailable sample, not proof of a reader
   pin.
2. `(busy=0, log=10,791, checkpointed=10,139)`, proving the checkpoint could
   execute and exposing a 652-frame gap.
3. Later `(busy=0, log=12,346, checkpointed=10,139)`, exposing a 2,207-frame
   gap. It was the first valid post-deploy gap sample and therefore not yet a
   persistent pin.

The WAL grew from roughly 42 MB to 50 MB during active writes but remained
below 64 MB. File size alone cannot distinguish reusable, checkpointed WAL from
an old reader snapshot.

## Repair

Creator Resolution now:

- records all three checkpoint values;
- treats negative frame counts as unavailable samples;
- requires a positive uncheckpointed-frame gap;
- requires that gap to make no checkpoint progress across repeated samples;
- requires the existing 64 MB threshold **and** three stalled cycles;
- emits frame counts, gap, holders, and process-local tracked connection state
  before a critical exit.

The 64 MB threshold, 60-second interval, and three-cycle threshold were not
changed. Creator Resolution semantics, attribution, RPC behavior, and queue
semantics were not changed.

## Deterministic fixtures and regression

`tests/test_x78_25_wal_pin_watchdog.py` contains six passing tests:

- true stagnant WAL gap detection;
- large fully checkpointed WAL rejection;
- checkpoint-progress reset despite `busy=1`;
- unavailable sample rejection;
- process-local connection attribution;
- real SQLite reader fixture showing a fixed checkpoint boundary while a read
  snapshot is open and full progress after it closes.

Targeted result: **6 passed**.

Combined X78.19–X78.24 targeted run: 29 passed, 1 failed. The failure is the
X78.22 test that expects the legacy funding-rescore prediction trigger; X78.24
intentionally decommissioned that trigger. X78.23 and X78.24 tests passed.

## Deployment and observation

- Only Creator Resolution was restarted.
- Post-repair PID: `57921`.
- It survived the same repeating `busy=1` condition that previously killed
  PID 56524.
- Samples with `log=-1/checkpointed=-1` remained at `stalled_cycles=0/3`.
- A valid 2,207-frame gap was recorded at `stalled_cycles=0/3` because no prior
  valid stagnant sample existed.
- No post-repair `CRITICAL_WAL_PINNED` restart was observed in the bounded
  window.
- API remained supervised and returned `/api/db-health`; WAL was about 50 MB.
- Creator Funding made at least one fresh completion during the observation.
- Token Prediction remained decommissioned.

## Attribution limits and remaining blockers

No exact external pinning connection was proven. SQLite does not expose a
cross-process reader-to-connection mapping through the checkpoint pragma, and
`lsof` only proves open descriptors. Process sampling showed Creator Funding
performing large SQLite `fetchall` work, but that alone does not prove it held
the oldest WAL reader mark. It was not modified.

Checkpoint-lock contention remains measurable: several samples returned
`(1,-1,-1)`. A valid sample later became available, so this is not sufficient
to call a permanent reader pin. Separately, Creator Resolution logged one
`database is locked` cycle after deployment. Historical p99 wait remained
elevated and must age out before qualification.

## Required next observation

Do not activate Evidence and do not run acquisition. Continue a bounded live
observation until all pre-readiness gates pass. In particular require:

- the same Creator Resolution PID with multiple completed cycles;
- consecutive valid checkpoint tuples demonstrating bounded or advancing gap;
- no unexplained reader transaction over 60 seconds;
- no write-lane timeout recurrence;
- fresh Creator Funding heartbeat/completions;
- stable listener and API;
- post-repair p99 samples that are no longer persistently at risk.

Only then start and persist the 30–60 minute readiness clock.
