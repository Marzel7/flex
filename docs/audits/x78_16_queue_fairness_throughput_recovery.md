# X78.16 — Funding Queue Fairness & Throughput Recovery

Implementation + regression + live-soak milestone. Builds on X78.15's
measured findings; does not reopen X78.12/X78.13/X78.14.

---

## 1. Implementation

### Phase A/B — Queue fairness (age promotion)

`_recover_stale_and_claim`'s claim query previously ordered strictly:

```sql
ORDER BY job_priority DESC, next_attempt_at ASC, created_at ASC
```

X78.15 measured this as a direct starvation mechanism: with a
continuously-replenished `job_priority=1` population (15,861 ready rows,
~27 arrivals/hour) against ~6.8 completions/hour, `job_priority=0` rows —
including one that had sat untouched for 1005.93 hours — were
mathematically guaranteed to never be reached.

**Fix: age promotion.** Each row's `effective_priority` is computed as:

```
effective_priority = job_priority + MIN(age_seconds / AGE_PROMOTION_INTERVAL_SEC, AGE_PROMOTION_CAP)
```

and the claim query orders by `effective_priority DESC` instead of raw
`job_priority DESC`. `AGE_PROMOTION_INTERVAL_SEC` defaults to 3600 (1
hour): a `job_priority=0` row becomes as eligible as a freshly-arrived
`job_priority=1` row after waiting 1 hour, bounding the maximum possible
deferral for any single priority gap.

**Chosen over the other Phase B options** (documented in the code at
`creator_funding_worker.py` lines ~89-127):
- Quota scheduling: rejected — needs hardcoded special-casing for exactly
  two tiers today, would silently misbehave with a third tier, and wastes
  reserved capacity when the low-priority population is empty.
- Weighted fair selection: rejected — unnecessarily complex for a two-tier
  system, harder to verify than a closed-form expression.
- Age promotion: chosen — single `ORDER BY` change, same query shape/index
  usage, deterministic and directly measurable per-row, generalizes to any
  number of priority tiers without special-casing.

Priority is preserved, not eliminated: two comparably-aged rows still
order by raw `job_priority` exactly as before (verified by
`test_fresh_high_priority_row_still_wins_against_comparably_aged_low_priority_row`).

**A real bug was found and fixed during implementation, before any commit**:
a first draft used `AGE_PROMOTION_CAP=24` (24 hours). Live-testing this
directly against the production queue (read-only) revealed 15,247
`job_priority=1` rows were *already* older than 24h — permanently capped
at `effective_priority=1+24=25`, which unconditionally outranked every
`job_priority=0` row's own capped ceiling of `0+24=24`, regardless of age.
This was the exact same starvation mechanism, reintroduced by an
undersized cap. Fixed by raising `AGE_PROMOTION_CAP` to 1000 (≈41.7 days
of promotion), comfortably exceeding the actual 0-1 priority spread. A
dedicated regression test
(`test_age_promotion_cap_does_not_reintroduce_starvation`) reproduces this
exact condition so the bug cannot silently reappear.

### Phase C — Retry amplification

X78.15 found `_mark_retry`'s existing backoff (`min(900, 120 * (attempts+1))`)
was already functioning correctly — all 58 retry rows had eligible
`next_attempt_at` values, none artificially delayed. The actual problem
was never a missing backoff: it was that a just-failed retry row re-enters
the same unified, unaged priority ordering as fresh arrivals, so a
job whose underlying cost hasn't changed could consume another full
claim-slot cycle for zero net completions, while also potentially
crowding out genuinely new work (or vice versa).

Age promotion (Phase A/B) is the direct lever here too: it bounds how long
*any* row — retry or pending — can be crowded out by a different
population, which is the actual mechanism "retries must not monopolize
claim capacity" requires. No separate retry-specific mechanism was added,
since the shared age-promotion ordering already applies uniformly to both
`pending` and `retry` rows (the claim query's `WHERE status IN ('pending',
'retry')` was already unified before this change).

Retry scheduling remains deterministic (unchanged formula), and no
duplicate-processing risk was introduced — the claim query's existing
`locked_until` guard is untouched.

### Phase D — Timeout accounting

Traced the full occupancy chain and found the *documented* budget
(`JOB_TIMEOUT_SECONDS=90` + `EXTRACTION_CANCEL_GRACE_SECONDS=10` +
`ORPHAN_TASK_WAIT_SECONDS=20` = 120s) did not match the observed tail of
retry-elapsed values (p50 122.7s ≈ budget, but p95 311.7s, max 819.2s —
far beyond it). Root cause: `job_started` was previously set *after*
`_await_stragglers_before_next_write()` (an intentionally *unbounded*
wait for a *prior* job's still-running background tasks, per X78.2's own
design — never race a write), meaning that wait's cost was invisible in
every `elapsed=` log line, while genuine execution-time variance in
`extract_funding_for_new_token` (RPC-bound, page count/funder count
dependent) accounted for the remaining tail — not a broken timeout
mechanism.

Added explicit stage timing, all read-only additions with no behavior
change:
- `claim_occupancy_started` now marks the *true* start of claim-slot
  occupancy (before the straggler wait), separate from `job_started`
  (execution start).
- A log line fires when straggler-wait time exceeds 1s, attributing it
  explicitly rather than folding it silently into the next job's numbers.
- The timeout-cancellation path now separately logs `execution=`s and
  `cleanup=`s in its raised exception message.
- Orphaned-task supervision time is logged explicitly when it exceeds 1s.
- Every `complete`/`retry`/`failed` log line now reports both `elapsed=`
  (execution-only) and `claim_slot=` (full occupancy including any
  straggler wait), so Phase D's five categories (execution, cancellation
  cleanup, retry scheduling, queue wait, claim occupancy) are now
  independently attributable from log data rather than folded into one
  undifferentiated number.

The existing `JOB_TIMEOUT_SECONDS=90` was confirmed to already bound
*execution* correctly (via `asyncio.wait_for`/`asyncio.shield`) — the gap
was purely in *measurement/attribution*, not in the bound itself.

### Phase F — Queue health (starvation exposure)

`print_status()` (`--status` CLI) previously reported only raw status
counts. Replaced/extended with:
- Oldest ELIGIBLE row per priority tier (ready to claim right now) with
  its computed `effective_priority`.
- Oldest BLOCKED row per priority tier (locked/deferred — genuinely
  in-flight or backoff-waiting, a structurally different condition).
- Oldest row by state (`pending` vs `retry` separately).
- A direct starvation-exposure count: eligible rows waiting more than 1
  hour.

This directly answers "is anything actually starving right now" from a
single command, rather than requiring the kind of manual multi-query
investigation X78.15 needed.

### Phase G — Database correlation (measurement only, no fix)

Measured `db_p99` values logged per cycle by the currently-running
(pre-deployment) process instance: 15 of 31 cycles (48%) showed spikes
above 5000ms, several near the 60s cross-process lock ceiling. This is
consistent with — but not proven to be *caused by* — the retry-amplification
pattern (jobs repeatedly re-claimed and re-timing-out generate repeated
write-lane contention). Per the charter's explicit instruction not to fix
database latency in this milestone, this correlation is documented as an
open observation to re-check during the live soak (does the spike
frequency/magnitude improve once fewer wasted retry cycles occur), not
asserted as proven causation.

---

## 2. Regression

New test file: `tests/test_x78_16_queue_fairness_age_promotion.py` — 6
tests, all passing:
- `test_ancient_low_priority_row_is_eventually_claimed_ahead_of_fresh_high_priority_flood`
  — reproduces the exact live starvation shape (1 ancient row vs 500 fresh
  high-priority rows) and proves the ancient row wins.
- `test_fresh_high_priority_row_still_wins_against_comparably_aged_low_priority_row`
  — proves priority is preserved for comparably-aged rows.
- `test_age_promotion_cap_does_not_reintroduce_starvation` — regression
  test for the cap-sizing bug found during development.
- `test_effective_priority_is_deterministic_and_measurable` — verifies the
  formula directly (Phase A's "must be measurable" requirement).
- `test_retry_backoff_still_functions_after_age_promotion_change` —
  confirms `_mark_retry`'s existing backoff formula is unchanged.
- `test_no_eligible_row_is_permanently_unclaimable_regardless_of_batch_repetition`
  — simulates repeated claim cycles and proves the ancient row is claimed
  essentially immediately (first), not merely "eventually by attrition."

Full existing suite re-run: 37/38 passing across
`test_x78_0_creator_funding_lease_poisoning.py`,
`test_x78_8_infra_sync_separation.py`, `test_x78_11_rpc_metrics_lease_poisoning.py`,
`test_x78_11b_reaper_cross_thread_lease_poisoning.py`,
`test_x78_12_domain_resolver_lease_timeline.py`,
`test_x78_14_infra_sync_hot_path_decoupling.py`,
`test_x78_16_queue_fairness_age_promotion.py`,
`test_x78_4_process_job_end_to_end.py`. The one failure
(`test_a_single_leaked_lease_poisons_every_subsequent_write_same_thread`)
is the same pre-existing, already-documented X78.0 legacy timing
fragility (asserts stale pre-X78.11b permanent-poisoning behavior),
confirmed zero-diff/untouched by this change.

**No creator-funding semantic change**: `extract_funding_for_new_token`,
its call signature, and every downstream enrichment call
(`_mark_complete`, `_mark_retry`, `_mark_failed`, risk scoring, second-hop
enqueue, prediction rescore, network assignment, intelligence refresh) are
byte-for-byte unchanged. Only the claim *query's ordering* and *logging*
changed. Replay/Evidence/Primitives/Runtime/Discovery/WATCHTOWER/OIP were
not touched.

---

## 3. Before/After (queue mechanics)

**Before** (measured live, X78.15): oldest eligible `job_priority=0` row
ranked below 15,861 other ready rows; effectively unclaimable under
sustained `job_priority=1` arrivals.

**After** (measured live, same production queue, read-only query):
the same row (age 1006.2h) now has `effective_priority=1000` (capped) and
only 30 rows in the entire ~16,900-row backlog rank ahead of it — all of
which are themselves >1000h-old `job_priority=1` rows, a population two
orders of magnitude smaller than the pre-fix 15,861.

---

## 4. Deployment & Live Soak

Deployed narrowly to `creator_funding_worker` only (the sole consumer of
this queue and the only process whose code changed). Soak results to be
appended after live validation; commit made locally first per the standard
"commit after regression, push after live soak" workflow this codebase
uses throughout the X78 series.
