# X78.23 — Token Prediction Builder Long-Hold Attribution & Write-Boundary Repair

Date: 2026-08-10 (Europe/London)

## Scope and safety

- Evidence Platform remained disabled; acquisition and 5K remained on hold.
- No prediction score, label, threshold, identity, attribution, primitive, discovery,
  motif, relationship, or governance semantics changed.
- Production population was not expanded and no prediction rows were deleted.
- Attribution was deployed first. The repair restarted only
  `creator_funding_worker`.

## Baseline

- Branch: `classification-attribution-axis`
- Baseline HEAD: `cb1fc110e105436c4baa9fe15f956628f80db3ce`
- X78.22 remained intact. Its creator-funders trigger repair was not reopened.
- Pre-repair attributed episode: `token_prediction_builder.py:396 in _check`,
  Creator Funding PID 47804, Thread-7, transaction
  `63745318-abd0-498c-9255-a7b134eb4e97`, held the physical writer for over
  60 seconds.

## Call graph and ownership

```text
Creator Funding job
  -> score_single(..., FUNDING_COMPLETE)
  -> _schedule_outcome_checks(mint)
  -> daemon threading.Timer (5m, 30m, 2h)
  -> _check
  -> db_connect
  -> _resolve_outcomes
  -> token_prediction_outcomes UPSERT
  -> commit / close
```

The child is a detached daemon `threading.Timer`, not an owned asyncio task.
Its connection is finally-closed on success or failure, preserving the X78.14
cleanup contract, but parent cancellation does not cancel an already scheduled
timer.

## Exact boundary

The source SELECT does **not** acquire the global write lane. The first mutating
statement is:

```sql
INSERT INTO token_prediction_outcomes (...)
VALUES (...)
ON CONFLICT(mint) DO UPDATE SET ...
```

Fingerprint: `ab3084b2f847d55f4eb9`.

There are no triggers on `token_prediction_outcomes`. The only work after first
mutation is the outcome UPSERT and commit. Source loading and Python outcome
classification are read-only work.

The timer is global, despite accepting a mint: it evaluates every eligible
unresolved prediction. At capture time the relevant cardinalities were:

| Table / population | Rows |
|---|---:|
| token_analysis | 1,622,428 |
| token_prediction_scores | 277,191 |
| token_prediction_events | 272,030 |
| distinct BIRTH/MIGRATED event mints | 267,332 |
| token_prediction_outcomes | 2,491 |
| unresolved outcome rows | 591 |
| token_pool_accounts | 28,308 |

## Query plan

The read plan materializes the token-pool aggregate, searches migrated launches
through `idx_ta_lifecycle`, scans the small outcomes table, searches events using
`idx_tpe_event_type`, builds a temporary B-tree for `DISTINCT`, searches scores
by their primary-key index, and joins the materialized pool aggregate through an
automatic covering index. This is a costly global read, but the natural capture
proved it runs with `write_lane_owned=false`; no speculative index was added.

## Atomicity finding

Verdict: **HISTORICAL_ACCIDENT / CONNECTION_REUSE_ONLY**.

Each outcome is independently addressed by primary key `mint`. Consumers read
individual rows and do not require all unresolved outcomes to become visible in
one global transaction. The main builder retains its existing single-transaction
behaviour. Only detached timer publication is divided into bounded, idempotent
200-row transactions. A failed batch contains no partial row; completed batches
remain valid and a subsequent timer safely retries remaining/updatable rows.

## Repair

- Added fail-open SQL and real phase diagnostics with connection, transaction,
  statement, fingerprint, thread, timing, row count, phase, and lane ownership.
- Added phase markers for `outcome_source_load`, `outcome_materialization`, and
  `outcome_persistence`; the active phase is published on the physical lease.
- Changed only timer-driven `_resolve_outcomes` calls to publish at most 200
  independently keyed outcomes per commit.
- Kept the main builder's pre-existing transaction boundary unchanged.

## Natural post-repair capture

Creator Funding PID 50324, Thread-1, connection
`9816458f-fc56-4898-960e-9484ce3029f1` ran the naturally scheduled five-minute
timer (no manufactured production invocation).

| Phase | Result |
|---|---:|
| source SELECT SQL | 6,023.231 ms |
| source phase including fetch/materialization by sqlite | 10,301.076 ms |
| rows read | 581 |
| Python outcome materialization | 0.989 ms |
| outcomes produced | 581 |
| batch sizes | 200 / 200 / 181 |
| batch 1 UPSERT SQL | 16.018 ms |
| batch 2 UPSERT SQL | 3.553 ms |
| batch 3 UPSERT SQL | 5.685 ms |
| longest measured persistence phase | 3,983.126 ms |

The first persistence phase includes approximately 3.97 seconds waiting to
acquire the already occupied lane; it did not own the flock during that wait.
After acquisition, its SQL and commit completed in milliseconds and the lane was
released before batch 2. No corrected-path hold exceeded 60 seconds or 15
seconds.

## Tests

Passed (16):

- `tests/test_x78_23_token_prediction_write_boundary.py` — 3
- `tests/test_x78_22_creator_funding_sql_boundary.py` — 4
- `tests/test_database_write_service.py` — 9

The X78.23 tests prove exact before/after row equality, bounded 200-row timer
publication, preservation of the main builder boundary, and retry-safe failure
at a batch boundary.

Six existing `tests/test_token_prediction_completeness.py` tests fail before
the changed outcome path because their fixture lacks the pre-existing
`creator_networks.total_tokens` column required by `_build_context`. They are
reported, not counted as completed regression passes, and were not altered.

## Runtime observation

Immediately after the natural capture:

- Creator Funding progressed from 6,848 to 6,851 complete jobs.
- Creator Resolution progressed from 5,645 to 5,665 complete jobs.
- API process was running and `/api/db-health` served successfully.
- recent SQLite lock errors: 0; failed writes: 0.
- Final serializer snapshot: p50 wait 0 ms, p95 wait 7.6 ms, p99 wait
  6,808.55 ms; p95 commit 0.57 ms and p99 commit 2.55 ms.
- WAL: 49.34 MB (up from a 7.5 MB point-in-time reading), checkpoint
  `busy=0`, 4,766 of 11,975 frames checkpointed. It remained below the 64 MB
  watchdog threshold, but this is not a completed 30-minute qualification.
- A separate short current owner was `rpc_metrics_recorder.py` and therefore not
  attributed to the corrected prediction timer.

The rolling historical p99 has not yet aged out and the required 30/60-minute
readiness window was not completed in this intervention.

## Verdicts

- Token Prediction: **A — LONG-HOLD ROOT CAUSE IDENTIFIED_AND_REPAIRED**
- Database: **B — TRANSIENT_ATTRIBUTED_CONTENTION**
- Creator Funding: **A — HEALTHY / ONGOING_FRESH_COMPLETIONS**
- Creator Resolution: **B — TRANSIENT_CONTENTION_RECOVERED**
- Listener: **A — STABLE** (PID 49819 crossed the 15-minute same-PID gate;
  final observed uptime 15m14s)
- Operational Intelligence: **D — INSUFFICIENT_WINDOW**
- Production Health: **C — DEGRADED / READINESS_BLOCKED**
- Evidence Activation: **HOLD**
- Acquisition: **HOLD_ACQUISITION**

The repair is effective for the scoped holder. Production readiness remains
blocked until the rolling database window ages out and a full 30-minute minimum
qualification is completed without another unexplained holder or WAL failure.
