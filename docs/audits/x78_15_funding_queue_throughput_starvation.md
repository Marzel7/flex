# X78.15 — Funding Queue Throughput & Starvation Analysis

Read-only operational investigation. No fixes, no commits, no production changes.

Investigation window: epoch ~1786263315–1786263400 (2026-08-09 ~08:15-08:17
local), cross-referenced against `creator_funding_worker` pid 71880's full
log history since its last restart (~8 hours of continuous, stable
operation post-X78.14).

---

## Phase A — Queue Census

```
status    count
complete  6619
expired   622
pending   16868
retry     58
```

- **All 16,868 pending rows have `attempts = 0`** — none are currently
  in-flight (`locked_until` is not in the future for any of them). The
  entire pending backlog has genuinely never been claimed even once.
- **58 retry rows, all at `attempts = 1`** — none have exhausted
  `MAX_ATTEMPTS = 5`. All have `next_attempt_at` far in the past
  (min −334,919s, i.e. ~93h overdue), meaning none are artificially
  delayed — every retry row is immediately eligible for reclaim right now.
- **622 `expired` rows are historical** — no code path in the current
  codebase (`creator_funding_worker.py`, nor any grep hit elsewhere in
  `src/` or `scripts/`) sets `status='expired'`. This status value exists
  only as a legacy artifact from a mechanism that is no longer present;
  there is currently **no active expiry/aging sweep** operating on this
  queue.

**Age distribution of pending rows:**

```
0-1h:    17
1-24h:   635
1-7d:    2,969
7-30d:   9,220
30d+:    4,027
```

Over 78% of the pending backlog (13,247 of 16,868) is more than 7 days old.

**Priority distribution of pending rows** (this is the central finding —
see Phase B):

```
job_priority=1: 15,861 rows (oldest: 2026-06-29 22:21:00)
job_priority=0:  1,007 rows (oldest: 2026-06-28 10:19:34)
```

---

## Phase B — Oldest Pending Item

The oldest pending row (`8y83ZUQH8gsbYa9qEyYF6Wdqw3so7L9ThsWREuCVXWTr` /
`5jCQH9EwoqbSNs3ZHWE2b4e6x7PpecYfFuhgR3uRpump`) is **1005.93 hours**
(~41.9 days) old, `attempts=0`, `job_priority=0`, `priority_reason='unknown'`.

**Classification: Never claimed — not waiting, not retrying, not blocked,
not poisoned, not orphaned, not permanently failed.** It is eligible for
claim right now (`next_attempt_at` is 0, `locked_until` is not in the
future) and would be selected the moment the claim query reaches it.

**Why it remains:** the claim query
(`creator_funding_worker.py:403-413`, `_recover_stale_and_claim`) orders
strictly:

```sql
ORDER BY job_priority DESC, next_attempt_at ASC, created_at ASC
LIMIT ?
```

This is a strict lexicographic priority order with **no aging or starvation
protection** — a row at `job_priority=0` is only ever considered after
every single `job_priority=1` row (regardless of the `job_priority=1`
row's own age) has been exhausted from the ready set. Right now there are
**15,861 ready `job_priority=1` rows**, all with `next_attempt_at <= now`.
As long as that population stays non-empty (which Phase C shows it does,
continuously, via ongoing arrivals), no `job_priority=0` row — including
the 1006-hour-old one — will ever be reached by the ordinary claim path.

This is **not absolute** starvation: priority-0 rows have completed
historically (2,138 all-time) and expired historically (89 all-time),
proving the population is not permanently frozen — but the current
standing backlog of 1,007 priority-0 pending rows, including the single
oldest item, has accumulated because priority-1 arrivals have kept the
ready priority-1 population non-empty continuously since at least
2026-06-28.

---

## Phase C — Throughput

Measured directly against the live queue:

| Metric | Value |
|---|---:|
| Arrivals (created_at), last 1h | 17 |
| Arrivals, last 24h | 651 |
| Completions (funding_extracted_at), last 24h | 163 |
| Net queue growth, last 24h | **+488** |
| Implied arrival rate | ~27.1/hour |
| Implied completion rate | ~6.8/hour |
| Total backlog (pending+retry) | 16,926 |
| Days to double backlog at current net growth rate | ~34.7 |

**Throughput does NOT exceed arrival rate.** Completion rate (~6.8/hour)
is roughly a quarter of the arrival rate (~27.1/hour). This is a
sustained, structural net-growth condition, not a temporary dip — the
backlog will continue to grow at the current ratio regardless of any
individual stall/recovery event.

**Claim-vs-completion detail from this process instance's own 8-hour
window** (pid 71880, `Starting pid=71880` through current tail):

```
claims:      82
completions: 10  (12.2% of claims)
retries:     56  (68.3% of claims)
```

(The remainder, ~16 claims, correspond to jobs still in-flight or spanning
outside this exact log excerpt.)

---

## Phase D — Retry Behaviour

- **Retry-elapsed-time distribution** (from `elapsed=` values on the 56
  `retry creator=...` log lines in the 8-hour window): min 90.0s, p50
  122.7s, p95 311.7s, max 819.2s.
- All 56 retries are at `attempts=1` (first retry) — `MAX_ATTEMPTS=5` has
  not been exhausted by any row yet in this window, meaning the 1s-based
  count of eventual permanent failures is not yet observable at this
  timescale.
- **Retries are NOT preventing forward progress in the poisoning/deadlock
  sense** (no `NestedDatabaseWriteError`/lock-based starvation contributes
  to this — that mechanism was addressed by X78.12/X78.14 and is not
  implicated here). Retries **are** contributing to throughput loss:
  every retried job consumes a full `JOB_TIMEOUT_SECONDS=90` (+ up to
  `EXTRACTION_CANCEL_GRACE_SECONDS=10`) claim-slot window without
  producing a completion, then re-enters the same priority-1 ready pool
  to compete for a claim slot again later — the same underlying
  extraction cost (large funding history) will very plausibly cause it to
  time out again on its next attempt, since nothing about its cost profile
  changes between attempts.

**Conclusion: retry behaviour amplifies wasted claim-slot time (a job that
will time out again consumes another ~90-100s+ of a worker cycle for zero
net completions) but is a secondary contributor, not the primary
mechanism** — the primary mechanism is throughput/arrival imbalance
(Phase C) combined with priority ordering with no aging (Phase B).

---

## Phase E — Scheduling

- **Worker utilization**: the worker is continuously busy — `INTERVAL_SEC=3`
  (busy) governs cycle cadence, and `_adaptive_batch()` returns
  `BATCH_SIZE_MAX=5` whenever `pending >= BACKLOG_THRESHOLD=10` (always
  true at 16,868+ pending) and the DB write-serializer's own p99 is under
  5000ms. There is no meaningful idle time under the current backlog size
  — the worker is not sitting idle waiting for work.
- **Blocked time**: minimal post-X78.14. The only blocking observed in
  this window is the designed, bounded `JOB_TIMEOUT_SECONDS=90` wait per
  job (not indefinite), plus occasional bounded 60s cross-process lease
  timeouts (pre-existing, unrelated class, already documented).
- **Queue-selection policy**: strict `job_priority DESC, next_attempt_at
  ASC, created_at ASC` — a pure priority-then-FIFO order with **no aging,
  no priority boost by wait time, no round-robin between priority tiers,
  and no separate lane/reserved capacity for lower-priority work.**
- **Fairness**: NOT fair across priority tiers by design — this is a
  strict priority queue, and the current data shows that design choice
  directly causes indefinite (though not provably permanent) deferral of
  the entire `job_priority=0` population while `job_priority=1` arrivals
  continue.
- **Batching**: `batch=5` per cycle is a claim-count limit, not a
  concurrency limit — jobs within a batch are still processed
  sequentially in the observed loop structure (one `_process_job` await
  per row in the `for row in rows:` loop), so batch size affects how many
  jobs are claimed atomically per cycle, not how many run in parallel.

---

## Phase F — Dependency Analysis

`priority_reason` breakdown for the dominant `job_priority=1` population:

```
brand_new_creator:              13,314
p0_creator_resolved_new_creator: 2,547
```

Both reasons describe **freshly-created, never-before-seen creators** —
this is new-arrival work, not blocked/dependency-waiting work. No
evidence was found of jobs blocked on a missing external dependency
(missing transaction, missing RPC availability, infrastructure
unavailability) — the queue's `last_error` field for pending rows was not
separately audited in this pass (all pending rows are `attempts=0`, so
`last_error` is necessarily NULL for the entire pending population by
definition; the retry population's `last_error` values are the
`"creator funding timed out after 90s"` message already captured in
Phase D, which is a timeout-classification, not an external-dependency
classification).

**Dependency classification: overwhelmingly "new arrival, never
attempted," not "blocked on X."**

---

## Phase G — Queue Health

| Class | Count | Basis |
|---|---:|---|
| Healthy (recently arrived, will be claimed in normal course) | ~17,000 minus stale tail | priority-1, attempts=0, ready |
| Recoverable (retry, under MAX_ATTEMPTS, next_attempt_at eligible now) | 58 | all retry rows |
| Waiting (never claimed, priority-starved) | 1,007 | job_priority=0 pending |
| Stuck | 0 | no rows found locked/in-flight beyond bounded timeouts |
| Poisoned | 0 | no NestedDatabaseWriteError-class poisoning observed in this queue's own state (that mechanism is orthogonal — a DB-lease issue, not a queue-row issue — and was already addressed by X78.11b/X78.12/X78.14) |
| Orphaned | 0 | no rows found with a `locked_until` in the past and status still `running` at time of check |
| Permanent (failed, exhausted MAX_ATTEMPTS) | 0 observed this window | none of the 58 retry rows have reached attempts=5 yet |

The most consequential class here is **Waiting (1,007, priority-starved)**
— this is the class the 1006-hour-old row belongs to, and it is
structurally guaranteed to keep growing as long as priority-1 arrivals
continue outpacing the worker's ability to drain the priority-1 pool to
empty.

---

## Phase H — Operational KPIs

| KPI | Value |
|---|---:|
| Arrival rate | ~27.1/hour (~651/24h) |
| Completion rate | ~6.8/hour (~163/24h) |
| Net queue growth rate | ~+488/24h |
| Queue "half-life" | **Not applicable — the queue is growing, not shrinking.** No half-life exists under current conditions; the relevant figure is the doubling time, ~34.7 days at the current net growth rate. |
| Average completion time (this window, completed jobs only) | mean of {8.4, 0.0, 83.9, 1.7, 14.3, 89.8, 3.8, 3.5, 42.9, 51.7}s ≈ 30.0s |
| Median completion time | ~9.35s (midpoint of the above sorted set) |
| 95th percentile completion time | not statistically meaningful at n=10; the broader retry-elapsed p95 (311.7s) is a better proxy for the tail of ALL attempted-job durations, not just successful ones |
| Oldest pending trend | growing — the same row observed at 1005.93h in this investigation was already the oldest in earlier X78.13-era checks; it has not been touched since |

---

## Phase I — Root Cause

## E — Mixed

Supported by measured evidence across two independently-confirmed,
co-occurring mechanisms, neither of which alone fully explains the
observed backlog persistence:

1. **Throughput limited (A)**: completion rate (~6.8/hour) is
   structurally below arrival rate (~27.1/hour) — a ~4:1 imbalance,
   confirmed via direct 24-hour counts. This alone guarantees net queue
   growth regardless of any scheduling policy.

2. **Starvation (B)**: the strict `job_priority DESC` ordering with no
   aging mechanism structurally guarantees the 1,007-row `job_priority=0`
   population (including the single 1006-hour-old row) is deferred
   indefinitely behind the much larger, continuously-replenished
   `job_priority=1` population (15,861 ready rows, arriving faster than
   they drain). This is a distinct mechanism from throughput imbalance —
   even if completion rate matched or exceeded arrival rate, priority-0
   rows would still only be reached once priority-1 rows were fully
   drained to empty, which the arrival pattern prevents from ever
   happening in practice.

3. **Retry amplification (C) is a measured secondary contributor, not
   primary**: 68.3% of claimed jobs in the observed window timed out and
   re-entered the ready pool, each consuming a full ~90-100s+ claim-slot
   window for zero net completions. This reduces effective throughput
   below its theoretical maximum but is not, by itself, sufficient to
   explain the oldest-item starvation (which is explained purely by
   priority ordering, independent of retry behavior).

**Queue hygiene (D) is NOT a significant contributor**: no orphaned,
poisoned, or permanently-stuck rows were found. The `expired` status
class is legacy/inactive but does not itself cause backlog growth (it is
simply an unused terminal state). The queue's own bookkeeping (locking,
recovery of stale `running` rows, retry eligibility) functions correctly
per direct inspection.

**Answer to the charter's central question**: *"What prevents recovery
from outpacing accumulation?"* — Two independent, measured mechanisms:
(1) the worker's completion rate is structurally below the arrival rate
under current job-duration/timeout characteristics, and (2) even the
throughput that does exist is entirely consumed by the newest-arriving,
highest-priority work, because the priority-ordering policy contains no
mechanism to guarantee older, lower-priority work ever receives a share
of that throughput. These are complementary, not competing, explanations
— fixing only the throughput imbalance would still leave priority-0 work
permanently deferred; fixing only the priority-ordering would still leave
the overall backlog growing, just with a different composition.

No fixes were implemented or recommended in scope of this investigation,
per the charter's explicit constraints.
