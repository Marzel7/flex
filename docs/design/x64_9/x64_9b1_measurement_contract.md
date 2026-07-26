# X64.9B1 — Phase 7: Measurement Contract

Defines the minimum observation period required before a retention
cutoff for `wt_subprov_sig_retry` DONE rows may be proposed, per the
gap X64.9B's abort exposed (a redelivery/replay rate that was assumed,
not measured).

## Minimum elapsed duration

**At least 14 days of continuous or near-continuous instrumentation
uptime**, not necessarily 14 calendar days if there are gaps —
specifically:

- Must span **multiple routine `ws_cascade` process restarts**. Given
  this project's own observed restart cadence for related processes
  (the FD-watchdog pattern documented in X64.7C fires roughly every
  10-20 minutes on `watchtower_listener` under load; `ws_cascade` was
  observed in this task's own Phase 6 to have run 4 days uninterrupted
  before this deployment, suggesting a different, longer natural
  restart interval) — 14 days should comfortably capture several
  restarts regardless of which cadence actually applies, satisfying
  the "durable across restarts" requirement empirically, not just by
  design.
- Must include **at least one reconnect or replay event**, where
  naturally occurring — i.e., at least one `CATCHUP`-sourced call to
  `_process_subprov_sig_durable` (visible via the `source_catchup`
  column in `wt_subprov_sig_dedupe_stats`, or the pre-existing
  `subprov_sig_catchup_recovered` in-memory metric). If 14 days elapses
  with zero CATCHUP-sourced calls at all, extend the observation
  window rather than concluding "no replay risk" — CATCHUP not firing
  means this particular risk path simply wasn't exercised yet, which
  is different from having measured it and found it safe.
- Must include **both normal and elevated queue conditions** — i.e.,
  the observation window should not be cut short during an unusually
  quiet period. Cross-reference `wt_subprov_sig_retry`'s own PENDING
  count over the window (already-existing data) against historical
  norms to confirm the window included at least one period of
  above-median queue depth.

## Minimum number of signatures checked

**At least 500,000 signatures checked** (`total_checked` in
`wt_subprov_sig_dedupe_summary`), chosen for this reasoning: the
existing code comment's offline sample (0/48) is explicitly
acknowledged in this project's own code as too small to trust. Scaling
up by roughly 4 orders of magnitude gives a result that, even if it
again finds zero duplicates, is statistically far more persuasive than
0/48 — and if the true redelivery rate were, hypothetically, as low as
1-in-100,000, a 500K-signature sample would have a reasonable chance of
observing several instances rather than plausibly missing all of them
by chance. This is a pragmatic threshold, not a formally derived
statistical power calculation — if a rigorous confidence-interval-based
threshold is wanted before the actual retention-cutoff decision, that
should be computed once real interim data exists (e.g. after the first
50,000-100,000 checked signatures, using the observed rate so far to
refine the target sample size), rather than guessed now with zero data.

## Both conditions, not either/or

**Both the 14-day/multi-restart/replay condition AND the
500,000-signature condition must be satisfied simultaneously** before
a retention cutoff may be proposed — satisfying only one is
insufficient:
- 500,000 signatures checked in, hypothetically, 2 days (if traffic is
  very high) would not yet have exercised multiple restart cycles or a
  plausible reconnect/replay scenario.
- 14 days elapsed with, hypothetically, only 50,000 signatures checked
  (if traffic is very low) would not give the sample size needed for a
  zero-duplicate result to be meaningful.

## What "sufficient evidence" looks like at the end of the window

At minimum, the following should be pulled from
`wt_subprov_sig_dedupe_summary` and `wt_subprov_sig_dedupe_stats` and
reported:

- `total_checked` and `total_duplicates` (global rate)
- Full age-bucket distribution across all wallets (which buckets, if
  any, actually saw duplicates — this directly informs where a safe
  retention cutoff could sit; e.g. if all observed duplicates fall
  under `<24h`, a cutoff well beyond 24h would be well-justified)
- `max_duplicate_age_s` (the single most extreme observed case — this,
  not the median, should anchor any proposed cutoff, since the goal is
  to never break the dedupe check for a duplicate that legitimately
  arrives late)
- Per-source breakdown (`source_ws`/`source_catchup`/`source_retry`/`source_hot_burst`)
  — if duplicates are concentrated in one source (e.g. `HOT_BURST`,
  which Phase 1's audit flagged as the source most likely to race with
  WS), that's directly actionable context, not just a number

## What this contract does NOT authorize

This document defines *when enough evidence exists to propose* a
retention cutoff — it does not itself propose one, and it does not
authorize any purge. Per this task's own constraints and the
established audit-then-execute discipline (X64.8 → X64.9 → X64.9A →
X64.9B → this task), a future retention-cutoff proposal and any
subsequent purge execution remain separately-scoped work, to be
initiated only after this contract's conditions are met.
