# X78.29 — Creator Funding Backlog Progress & Completion Accounting Recovery

Date: 2026-08-10 (Europe/London)  
Branch: `classification-attribution-axis`  
Baseline HEAD: `cb1fc110e105436c4baa9fe15f956628f80db3ce`

## Executive result

Creator Funding was completing real jobs, but its process-lifetime completion
counter could not observe them. The worker compared the global pending count
before and after processing a row already in `running`; a `running -> complete`
transition leaves that count unchanged. Consequently `total_completed` stayed
zero despite 29 persisted completions in the preceding hour.

The queue is also a mixed historical population. Of 18,162 eligible rows at
the measured census, 1,169 already had authoritative `creator_funders` output
but remained pending, while 16,993 remained unsatisfied or required deeper
input classification. The headline oldest row was one of the already-satisfied
rows. Recent work is materially delayed: 317 jobs arrived in the preceding
hour, all 317 remained pending at that sample, while the worker was naturally
claiming rows from 30 June.

The repair uses explicit per-job outcomes for accounting and reconciles only a
bounded oldest-first cohort of already-satisfied rows per worker cycle. It does
not change extraction, attribution, retry, priority or funding semantics.

## Baseline

X78.28 changes remained local and uncommitted and the unrelated dirty worktree
was preserved. Before deployment:

| Service | PID | State |
| --- | ---: | --- |
| Creator Funding | 64569 | RUNNING |
| API | 70504 | RUNNING |
| Listener | 69765 | RUNNING, >15 minutes |
| Creator Resolution | 69682 | RUNNING |
| Intelligence scheduler | 41768 | RUNNING |

Production remained available. Database, ingestion and current Operational
Intelligence were healthy at the investigation baseline; Token Prediction and
Broad Price Tracking remained decommissioned.

## Metric definitions

### Oldest eligible work

The original health SQL in `src/core/main.py` selected:

```sql
MIN(COALESCE(next_attempt_at, created_at))
WHERE status IN ('pending','retry')
```

It did not apply the worker's actual eligibility predicates
`locked_until < now` and `next_attempt_at <= now`, and its age source could be
the retry timestamp rather than original queue-entry time. The repaired metric
uses `MIN(created_at)` across rows eligible for claim now.

Threshold: 3,600 seconds. The threshold remains unchanged.

### `total_completed`

This is process-lifetime telemetry initialized to zero at worker start. It is
not a queue-wide count. It previously incremented only when the global pending
count fell during `_process_job()`, an invalid proxy for the current row's
terminal state. It also resets normally on worker restart.

### Health classification

Creator Funding uses:

- worker heartbeat status (`RUNNING`, `STALLED`, `STOPPED`);
- heartbeat age, threshold 120 seconds;
- oldest eligible age, threshold 3,600 seconds;
- pending-count trend placeholder.

The repair adds a descriptive progress state driven by a persisted
`last_completed_at`: `RUNNING_AND_COMPLETING`,
`BACKLOGGED_BUT_PROGRESSING`, or `RUNNING_BUT_NO_RECENT_COMPLETION`. The old
backlog remains a warning; it is not hidden to obtain a green dashboard.

## Queue inventory

Status census at baseline:

| Status | Count |
| --- | ---: |
| pending | 18,156 |
| complete | 6,913 |
| expired | 622 |
| running | 3 |
| failed | 1 |

Eligible age distribution:

| Age | Count |
| --- | ---: |
| <1 hour | 314 |
| 1–6 hours | 514 |
| 6–24 hours | 660 |
| 1–7 days | 3,664 |
| 7–30 days | 8,689 |
| >30 days | 4,318 |

Eligible satisfaction census:

| State | Count |
| --- | ---: |
| authoritative funding output already exists | 1,169 |
| no authoritative funding output yet | 16,993 |

Priority/reason distribution was dominated by priority 1:

- `brand_new_creator`: 13,382 pending;
- `p0_creator_resolved_new_creator`: 3,790 pending;
- priority-0 `unknown`: 536;
- priority-0 `known_creator`: 251;
- priority-0 `p0_creator_resolved_known_creator`: 197.

## Oldest-row forensics

Oldest eligible row:

- creator: `5DDEaV8fD1d5Ygn7P2Naq1WzWJAULa5TX2GBJxicZM9g`
- mint: `14jZTzx1ZMVBeT8eQhoYbQ29U9AZ7P36UWTX9Ehjpump`
- enqueued: 2026-06-30 08:52:13 UTC
- status: pending
- priority: 0
- source: `pf_ws_creator_existing_migration`
- attempts: 0
- authoritative `creator_funders` rows: 23

Classification: **ALREADY_SATISFIED / STALE_QUEUE_STATE**. It remained eligible
because stale recovery reconciled satisfied `retry` and expired `running` rows,
but not satisfied `pending` rows.

The next-oldest population is mixed. Some rows have authoritative output and
can be reconciled; others have known creators and migration signatures but no
funding output and remain actionable or input-limited.

## Claim order and current usefulness

The claim query computes:

```text
effective_priority = job_priority + min(age_hours, 1000)
```

then orders by effective priority, retry time and creation time. Batch size is
5 under normal backlog pressure and 1 under high database p99. This is a
single-worker, serial execution model.

Natural claims during the audit were historical rows from 30 June. In the
preceding hour the queue recorded 317 arrivals and 29 terminal completions.
At the recent-cohort sample all 317 recent rows remained waiting. Therefore:

- the worker is useful and completes real extraction;
- the historical backlog dominates claim order;
- current arrival rate exceeded measured completion throughput;
- recent work is delayed behind historical work.

No scheduling change was made in X78.29 because the current age-promotion
policy is an intentional X78.16 fairness contract. Changing it requires a
separate capacity/scheduling decision, not a telemetry repair.

## Natural job ledger and timeout evidence

Pre-repair logs contained many genuine completions with persisted funder counts
(including 3, 4, 5, 6, 7, 22, 186 and 486), while heartbeat telemetry still
reported `total_completed=0`. One natural timeout was observed at the 90-second
extraction boundary and transitioned to retry; it did not dominate the bounded
sample.

A single three-second process sample was taken when post-extraction work delayed
the next claim. It showed the process predominantly in SQLite work/schema
preparation rather than network wait or idle polling. No additional hot-path
change was made because the requested completion threshold was reached and the
sample alone does not justify another write-boundary redesign.

## Root-cause verdicts before repair

- Worker progress: **F — MULTIPLE_CAUSES**
  - real completions with broken process accounting;
  - historical backlog dominance;
  - already-satisfied pending rows lacking reconciliation.
- Oldest eligible metric: **E — MIXED_POPULATION**. The specific oldest row was
  satisfied and misleading; genuine actionable backlog remains immediately
  behind it.
- Accounting: **COUNTER_NOT_UPDATED**.

## Repair

### Creator Funding worker

- `_process_job()` now returns the exact terminal outcome: `complete`, `retry`
  or `failed`.
- Cycle and process totals consume that explicit outcome rather than global
  queue-count deltas.
- Heartbeat metadata includes `last_completed_at`.
- Existing-output reconciliation now covers pending rows, oldest first, capped
  at 25 rows per cycle.
- Reconciliation remains append-preserving/idempotent: rows are transitioned to
  the existing `complete` state, retained in the queue, and only when an
  authoritative `creator_funders` row exists.

### Health projection

- Oldest age now measures truly eligible rows using original queue-entry time.
- Health exposes the process's completion-recency/progress state.
- The one-hour backlog warning remains active while genuine historical backlog
  exists.

## Tests

Focused repair/regression suite:

- `tests/test_x78_29_creator_funding_accounting.py`
- `tests/test_x78_14_creator_funding_cancellation_cleanup.py`
- `tests/test_x78_16_queue_fairness_age_promotion.py`
- `tests/test_x78_17_creator_funding_read_boundary.py`
- `tests/test_x78_27_intelligence_refresh_singleflight.py`
- `tests/test_x78_28_operational_intelligence_freshness.py`

Result: **22 passed in 1.01 seconds**.

A broader 51-test run completed with 49 passes and two failures not caused by
X78.29: one pre-existing X78.22 assertion expects an obsolete log label, and
one live-database trend determinism assertion sampled changing production birth
data between calls. Neither failure exercises the repaired queue/accounting
paths.

## Deployment and post-repair proof

Only affected services were restarted:

| Service | Before PID | After PID | Reason |
| --- | ---: | ---: | --- |
| Creator Funding | 64569 | 74315 | accounting and bounded reconciliation |
| API | 70504 | 72498 | corrected health projection |

Listener, Creator Resolution, scheduler, walkback, websocket cascade and alert
evaluator were not restarted.

Each worker cycle reconciles at most 25 already-satisfied historical rows.
These are not counted as fresh worker completions. Separate genuine extraction
completions were observed for:

1. `9wAK5SgX1pQrEZnZvEK2mHSdRE88dhwUujZV2G99pump` — 3 funders;
2. `4EgKb1mdYMdmYWaeWE64aWKTqPCQEYwxUn75CytSpump` — 5 funders;
3. `FBTxHnzMXVLiyFNfniqh7exJyd7mz5hZq68BMaXnpump` — 3 funders.

All three reached the existing `complete` persistence transition. The final
heartbeat reported `total_completed=3` and a current `last_completed_at`. No
stale-reaper acknowledgement was counted as throughput.

Final platform sample: database `HEALTHY`, p99 12.91 ms, serializer depth 0,
WAL 2.6 MB; ingestion `HEALTHY`; both websocket feeds connected; Operational
Intelligence snapshot `FRESH` at version 386; missing creators in the current
one-hour window 0. Listener PID 69765 remained unchanged for 30 minutes.

## Readiness

Creator Funding is now proven to perform useful work and completion accounting
has a truthful source. Nevertheless the historical backlog is materially
larger than throughput and recent jobs remain delayed. Production readiness is
therefore not declared by this audit. The listener satisfied the unchanged-PID
15-minute gate during observation, but a full 30/60-minute all-platform
qualification was not started.

Final state:

- Creator Funding: **BACKLOGGED_BUT_PROGRESSING**
- Accounting: **ACCOUNTING_CORRECT after repair**
- Historical queue: **MIXED; bounded reconciliation active**
- Scheduling/capacity: **separate remaining blocker**
- Production readiness clock: **NOT STARTED**
- Evidence activation: **HEALTH_REPAIR_REQUIRED**
- Acquisition: **HOLD_ACQUISITION**
