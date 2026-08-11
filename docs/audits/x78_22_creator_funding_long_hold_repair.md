# X78.22 — Creator Funding Long-Hold SQL Attribution & Write-Boundary Repair

Date: 10 August 2026

## Executive outcome

The Creator Funding long hold was not network work or Python computation inside an open write transaction. It was SQLite executing this outer statement:

```sql
INSERT OR REPLACE INTO creator_funders (...)
VALUES (...)
```

The dominant work was its `AFTER INSERT` trigger, `trg_token_prediction_funding_inserted`. For every inserted funder, the trigger executed:

```sql
SELECT mint
FROM token_analysis
WHERE COALESCE(earliest_tx_creator, pf_ws_creator) = NEW.creator_address
  AND COALESCE(lifecycle_stage, '') = 'migrated'
  AND migrated_at IS NOT NULL
```

`EXPLAIN QUERY PLAN` returned `SCAN token_analysis`. Production contained 1,621,757 `token_analysis` rows. An `executemany` inserting N funders therefore performed N complete table scans while physically owning the global flock.

## Natural attribution capture

Instrumentation-only deployment used Creator Funding PID 44960. Statement start/end observations were correlated to the same connection ID, transaction ID and kernel lock owner without recording parameter values.

Measured outer-statement durations included:

| Inserted funders | Duration |
|---:|---:|
| 1 | 13.700 s |
| 1 | 16.145 s |
| 2 | 29.234 s |
| 3 | 41.723 s |
| 3 | 48.692 s |
| 6 | 97.661 s |

Other statements in the same path were small by comparison. A 522-row `transfer_index` batch took 346.8 ms. CEX lookup reads were generally below 1 ms.

The first write was the `creator_funders` batch insert. The full measured hold was SQLite mutation/trigger execution; there was no network await between lane acquisition and statement completion.

## Repair

No index was added. The existing indexes were sufficient, but the `COALESCE` predicate prevented their use.

The trigger now uses two mutually exclusive branches:

1. `earliest_tx_creator = NEW.creator_address`, explicitly using `idx_token_analysis_earliest_creator`.
2. `earliest_tx_creator IS NULL AND pf_ws_creator = NEW.creator_address`, explicitly using `idx_ta_pf_ws_creator`.

This preserves the original `COALESCE(earliest_tx_creator, pf_ws_creator)` precedence: `pf_ws_creator` is considered only when `earliest_tx_creator` is null. Both branches retain the migrated-state and migrated-timestamp requirements.

Post-repair plans are indexed searches, not scans.

The migration is idempotent and guarded: it replaces only the known legacy trigger marker. An unknown/custom trigger is left unchanged. Schema-lock failure is fail-open so it cannot create a worker crash loop.

## Deployment

- Instrumentation deployment: Creator Funding 41682 → 44960.
- First repair restart exposed a live schema-lock conflict; no partial trigger change occurred.
- Creator Funding and listener were paused briefly.
- A first bounded migration attempt was blocked by Creator Resolution; kernel ownership subsequently cleared.
- The retry completed with result `optimized`.
- Final deployment:
  - Creator Funding PID 45733.
  - Listener PID 45744.

No other service was deliberately restarted for the repair. Creator Resolution later restarted itself through its existing WAL-pinned watchdog.

## Post-repair results

Across the 15-minute qualification:

- Creator Funding PID 45733 remained stable.
- Listener PID 45744 remained stable for more than 15 minutes.
- Four fresh Creator Funding completions were recorded:
  - `55gRXTLaHdrp` / `2hqcXhWY3XBSBo6W`, 7 funders, 0.1 s.
  - `3FooJ7gRNmbw` / `8qJwvSbZaPB2jK2C`, 4 funders, 20.4 s.
  - `3FooJ7gRNmbw` / `8HbaGmhYgrWKvgqT`, 4 funders, 0.1 s.
  - `7WLBQwht1PA3` / `fPk242NK2uKRgPwL`, 6 funders, 85.4 s.
- The corrected `creator_funders` statement maximum was 13.356 ms, down from 97.661 s.
- The post-repair maximum instrumented Creator Funding SQL statement was 135.256 ms (`transfer_index`, 149 rows).
- Listener primary-database descriptors later contracted to 3 after a transient peak of 17. It did not restart during the qualification.
- API master remained stable and returned HTTP 302 in 8.2 ms on the local route.

Creator Resolution improved materially through cycles 8–16, including several 5/5 and 25/25 resolved cycles with zero failures. It subsequently hit its existing WAL-pinned watchdog and restarted. The triggering long holder was independently attributed to `token_prediction_builder.py:396 in _check`, running in a Creator Funding child thread; that is not the repaired `creator_funders` path and was not modified in X78.22.

WAL reached approximately 64.7 MB after the observation window, and one listener descriptor warning reached 17 before contracting. These conditions prevent a platform-wide readiness declaration despite the successful Creator Funding repair.

## Validation

- X78.22 focused tests: 4 passed.
- Relevant regression selection: 184 passed, 1 failed.
- The single failure is the historical X78.0 reproduction test asserting that a no-op/autocommit `CREATE TABLE` must leak a lease forever. X78.13 intentionally changed that behaviour by releasing write lanes when SQLite reports no transaction; the failure reproduces in isolation and is unrelated to X78.22.
- Python compilation passed.
- `git diff --check` passed.

Tests cover trigger semantic equality, indexed trigger shape, bounded execution, statement/transaction correlation, parameter redaction, migration idempotency and migration fail-open behaviour, plus X78.9–X78.21 write-lane, cancellation, listener, reaper and second-hop regressions.

## Final verdicts

- Exact Creator Funding SQL attribution: **COMPLETE**.
- Corrected Creator Funding statement: **REPAIRED; no recurrence above 60 seconds**.
- Creator Funding completion gate: **PASS (4 fresh completions)**.
- Listener 15-minute PID gate: **PASS**, with residual transient descriptor pressure.
- Creator Resolution: **PROGRESSING**, but WAL watchdog restart observed.
- Database health: **DEGRADED WITH A NEWLY IDENTIFIED INDEPENDENT HOLDER** (`token_prediction_builder`).
- Readiness clock: **NOT STARTED**.
- Evidence activation: **HOLD**.
- Acquisition: **HOLD_ACQUISITION**.

No funding attribution, creator classification, funder selection, CEX classification, BlockSec/SNS semantics, risk scoring, second-hop semantics, Evidence, acquisition, reconciliation, resolver or governance behaviour changed.
