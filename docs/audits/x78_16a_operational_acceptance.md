# X78.16A — Operational Acceptance & Throughput Validation

Read-only operational acceptance check. No implementation, no commits, no
production changes.

Investigation window: epoch ~1786264881–1786264934 (2026-08-09, ~7 minutes
after `creator_funding_worker` restarted onto commit `0d84fdbb`, pid
92395, restart_epoch `1786264495`). **This window is materially shorter
than the 30-minute checkpoint originally scheduled for this soak** — the
charter's request for immediate measurement is honored here, but every
throughput figure below should be read with that limitation explicit, not
smoothed over.

---

## Phase A — Throughput Comparison

| Metric | X78.15 baseline (24h) | X78.16A window (~7 min) |
|---|---:|---:|
| Arrivals/hour | ~27.1 | 11 in the last 1h (includes pre-deploy activity — not isolated to the new code) |
| Completions/hour | ~6.8 | 7 in the last 1h (same caveat); **1** confirmed specifically after `restart_epoch` |
| Jobs claimed since restart | — | 2 |
| Jobs completed since restart | — | 0 confirmed (both claimed jobs still mid-extraction at time of check) |
| Retries since restart | — | 0 (none has yet timed out) |

**This window is too short to produce a statistically meaningful
completions/hour or retries/hour figure for the new code specifically.**
Both jobs claimed since restart were still actively extracting (real RPC
calls, non-stalled, healthy log output) at time of measurement — normal
given this codebase's documented job duration profile (extraction can
legitimately take minutes). Phase A cannot be measured with confidence at
this window length; a longer soak is required before this figure is
reportable.

---

## Phase B — Fairness Validation

This is where the data is strong, but requires a correction to an
earlier interim read, made and caught during this same investigation:

An initial query joined on `creator_address` alone and found 3 `complete`
rows for creator `8y83ZUQH8gsbYa9qEyYF6Wdqw3so7L9ThsWREuCVXWTr` — the
creator whose queue row X78.15 flagged as 1005.93 hours old — and
initially misread this as the starved row itself completing. Direct
inspection of the specific `(creator_address, mint)` row
(`8y83ZUQH8gsbYa...` / `5jCQH9EwoqbSNs3ZHWE2b4e6x7PpecYfFuhgR3uRpump`, the
exact row X78.15 measured) shows **those 3 completions are unrelated,
pre-existing rows for the same creator's OTHER mints, completed months
ago (May/June 2026)** — this creator has 11 separate queue rows (one per
mint), and queue fairness/starvation is measured per-row, not per-creator.
**The specific starved row remains `status='pending', attempts=0`,
unchanged since original creation, at time of this check.**

What IS directly confirmed:

- **Effective priority is computing correctly for this exact row**: live
  query returns `effective_priority=1000` (capped, as designed for a row
  this old).
- **Its rank in the ready queue has measurably and substantially
  improved**: only **24 rows** now rank ahead of it in the entire
  ~16,900-row backlog, versus **15,861** before X78.16 — a reduction of
  >99.8%. This is the direct, measured effect of age promotion.
- **A DIFFERENT, similarly-starved row has already been reached**: the
  retry-queue's oldest row (`5jahCqsAv9MSDeoE8sAhY2HP1dakWTW2zcpxe63nYsUq`,
  1076.3h old at last measurement) was claimed and attempted within the
  first cycle after restart (visible directly in the log:
  `retry creator=5jahCqsAv9MS ... attempt=2`), and is now on its second
  attempt — active, not stuck.
- Both jobs claimed in this window were `priority=HIGH` (job_priority=1)
  — consistent with those rows also being old enough to be at/near their
  own age-promotion ceiling, not evidence against the fix (they are
  legitimately eligible under the new ordering too).

**Verdict for Phase B: the fairness mechanism is demonstrably functioning
as designed (rank improved by >99.8%, a comparably-starved row already
reached and being processed), but the SPECIFIC row X78.15 flagged has not
yet completed within this short window.** Given only 24 rows remain ahead
of it and the worker claims up to 5/cycle every ~3s when busy, it should
be reached within the next several cycles — this was not confirmed by
direct observation before this report was due, and should not be
asserted as complete without that confirmation.

---

## Phase C — Retry Amplification

**Insufficient data in this window.** Zero jobs have timed out or been
marked retry since the restart (both in-flight jobs are still within
their extraction window). No timeout rate, retry rate, or occupancy
comparison can be computed yet. The X78.15 baseline (68.3% claim-to-retry
rate, p50 elapsed 122.7s) remains the only available reference point;
whether it has changed cannot be measured from this window.

---

## Phase D — Queue Health

```
status    count
complete  6620
expired   622
pending   16866
retry     59
running   1
```

- Total backlog (pending+retry): 16,925 — statistically unchanged from
  the pre-restart baseline (16,925 at last X78.15-era check) at this
  timescale; no meaningful shrinkage or growth is measurable in a 7-minute
  window against a >800/day-scale queue.
- Oldest pending (by state): still age=1006.4h, creator=`8y83ZUQH8gsb`
  (per Phase B, this is real and current, not stale).
- Oldest retry: age=1076.3h, creator=`5jahCqsAv9MS` — actively being
  retried (attempts=2), not idle.
- 1 row currently `running` (in-flight, locked).
- Starvation-exposure count (`--status` output): 16,909 eligible rows
  waiting >1h — this number is expected to stay large in absolute terms
  even with the fix working, since it counts ALL waiting rows, not just
  starved ones; it is not, by itself, evidence of continued starvation
  (the correct signal is *rank*, measured in Phase B, not this raw count).

---

## Phase E — Database Correlation

- Heartbeat: 2 seconds stale at last check — healthy, current.
- Worker state: RUNNING, pid 92395, no restart since deployment.
- A `sqlite3.OperationalError: database is locked` was observed once in
  `_mark_retry` — this is the same pre-existing error class already
  documented in X78.12's closure report (not new, not attributable to
  this change).
- **Database p99 spike-rate comparison against the pre-deployment 48%
  baseline could not be computed** — too few `cycle=` log lines have
  accumulated since restart to produce a meaningful sample (fewer than 5
  cycles observed). This measurement requires the fuller soak window
  already scheduled.
- No causation is asserted, per the charter's explicit instruction —
  correlation only, and even that could not yet be computed with
  confidence.

---

## Phase F — Operational Stability

- Funding Worker: **RUNNING**, pid 92395, uptime growing normally, no
  restart during this check.
- Heartbeat: **current** (2s stale).
- Queue: actively being worked (2 claims, both legitimately in-flight,
  non-stalled extraction visible in logs).
- No new contention class observed — the one error seen
  (`database is locked`) is a previously-documented, pre-existing class.
- No X78.12 regression: `build_networks_release`/DomainResolver code
  paths not implicated in anything observed this window.
- No X78.13/X78.14 regression: no `sync_infra_wallets`-under-write-lease
  pattern, no multi-minute `build_networks_release` hold, observed in
  this window.

---

## Phase G — Acceptance Criteria

Checking each required condition against what was actually measured,
not assumed:

| Criterion | Status |
|---|---|
| Queue progresses continuously | **Partially confirmed** — 2 claims, active extraction, healthy heartbeat; 0 confirmed completions yet in this short window |
| Previously starved work advances | **Confirmed for a comparable row** (`5jahCqsAv9MS`, reached and actively retrying); **not yet confirmed for the exact X78.15 row** (`8y83ZUQH8gsb`/`5jCQH9Ew...`), though its queue rank improved >99.8% and it is mathematically next-in-line within ~24 rows |
| Completion rate materially improves or queue growth materially reduces | **Not measurable in this window** — insufficient elapsed time for a statistically meaningful rate |
| Worker remains healthy | **Confirmed** — RUNNING, heartbeat current, no restart, no new error class |
| No new regression class appears | **Confirmed** — only pre-existing, already-documented error classes observed |

**This does not meet the bar for full PASS**: two of the five required
conditions (materially-improved completion rate, and direct confirmation
that the specific flagged row completes) are not yet measurable or
confirmed from this window, through no fault of the implementation —
purely a function of elapsed time being far shorter than the process's
own job-completion cadence.

---

## Final Verdict

## PARTIAL

**Justification (measured data only):**

- The fairness mechanism (Phase A/B of X78.16's implementation) is
  **directly confirmed working**: the specific starved row's rank in the
  claim queue improved from 15,861-deep to 24-deep (>99.8% reduction), and
  a comparably-starved row was reached and is actively being retried
  within the first cycle after deployment — this is strong, measured,
  positive evidence.
- Worker health, heartbeat currency, and absence of any new regression
  class are all **directly confirmed**.
- However, **throughput improvement (completion rate vs. arrival rate)
  and full confirmation that the specific X78.15-flagged row completes
  cannot yet be asserted from measured data** — the soak window available
  for this report (~7 minutes) is far shorter than this worker's own
  documented job-completion cadence (historically 15-20+ minutes per job
  under RPC-bound load), and zero jobs have completed or timed out since
  restart to measure against.
- An interim misread (conflating a starved row's creator-level history
  with the specific row's own status) was caught and corrected within
  this same investigation before being reported as fact — noted here for
  transparency, not left silent.

**Recommendation**: re-run Phases A, C, and E of this exact acceptance
check once the already-scheduled 30-minute (and ideally 60-90 minute)
soak checkpoints have accumulated enough completions/retries to compute
real rates, and specifically confirm the exact flagged row
(`8y83ZUQH8gsbYa9qEyYF6Wdqw3so7L9ThsWREuCVXWTr` /
`5jCQH9EwoqbSNs3ZHWE2b4e6x7PpecYfFuhgR3uRpump`) reaches `status='complete'`
or a definitive terminal state. Do not push the X78.16 commit or declare
the X78 programme complete until that fuller measurement is available —
the evidence so far is positive and directionally strong, but the
charter's own PASS bar (materially-improved completion rate, confirmed
starved-work completion) is not yet met by what can currently be measured.

---

## Addendum — 60-Minute Checkpoint (post-report)

Re-measured 60 minutes after restart (pid 92395 unchanged, epoch
1786268290 vs restart_epoch 1786264495).

**Full post-restart tally**: 7 jobs claimed, 6 retried (timeout at 90s,
two took 300+ total seconds to fully clean up: 308.5s, 350.7s), 1
completed. **85.7% retry rate** — worse than the X78.15 baseline (68.3%).

**The specific flagged row** (`8y83ZUQH8gsbYa9qEyYF6Wdqw3so7L9ThsWREuCVXWTr`
/ `5jCQH9EwoqbSNs3ZHWE2b4e6x7PpecYfFuhgR3uRpump`) remains `status='pending',
attempts=0` a full hour after deployment. Its rank-ahead count is
unchanged at 30 (same as the 30-minute checkpoint) — not because age
promotion has stopped working (it is still correctly computing
`effective_priority=1000`, the capped ceiling), but because overall
throughput this hour has been dominated by long, genuine extraction-side
timeouts rather than completions, so very little of the ready queue has
actually drained.

One transient anomaly was investigated and resolved during this window:
heartbeat reached 452s stale with the process in state `S` at ~0% CPU,
traced precisely to a bounded 60s cross-process lease wait inside
`_rescore()` (`prediction rescore failed ... wait_seconds=60.003`) — this
resolved itself exactly at the 60s mark, consistent with designed
behavior, not a new stall class.

**This does not change the Phase B verdict** (the fairness mechanism is
correctly implemented and measurably improved this row's rank from
15,861-deep to 30-deep), but **it does mean the throughput/completion-rate
condition required for full PASS remains unmet after a full hour of
observation**, and the retry rate has not improved relative to the X78.15
baseline in this specific window. This should not be read as evidence
X78.16 caused a regression (the timeout/retry mechanism itself is
unchanged from before this milestone, and the individual timeout
durations observed are consistent with pre-existing, RPC-bound extraction
variance) — but the throughput improvement the charter's PASS bar
requires is not demonstrated by this hour's data.

**Verdict unchanged: PARTIAL.** Recommend holding the push and either (a)
continuing to soak significantly longer to see if this hour was an
unlucky sample of the underlying job-duration distribution, or (b)
treating the 85.7%-retry-rate finding as a separate, real operational
question (extraction timeout tuning / RPC latency) worth its own
investigation, independent of whether X78.16's fairness mechanism itself
is accepted on its own, narrower merits (which the rank-improvement
evidence supports).
