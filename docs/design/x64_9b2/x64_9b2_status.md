# X64.9B2 — DONE-Row Retention Analysis — Terminated at Preconditions Check

**Per this task's own explicit instruction: "If the measurement
contract has not yet been met: report current progress, identify
remaining requirements, terminate the task without further analysis."
That condition is met. No Phase 1-9 analysis was performed. No data
was read beyond the single status check below, and nothing was
modified.**

## Measurement contract (defined in X64.9B1, [x64_9b1_measurement_contract.md](../x64_9/x64_9b1_measurement_contract.md))

Both conditions must hold simultaneously before analysis may begin:

1. At least **14 days** of observation, spanning multiple `ws_cascade`
   restarts and at least one naturally-occurring reconnect/replay event.
2. At least **500,000 signatures checked** (`total_checked` in
   `wt_subprov_sig_dedupe_summary`).

## Current progress (checked 2026-07-21T16:09:27Z)

| Requirement | Target | Current | Met? |
|---|---|---|---|
| Observation duration | 14 days | **~7 minutes** (instrumentation deployed this same session, as X64.9B1, immediately prior to this task) | ❌ No — off by roughly 3 orders of magnitude |
| Signatures checked | 500,000 | **413** (`total_checked`) | ❌ No — off by roughly 3 orders of magnitude |
| Multiple restarts observed | ≥2 (implied by "multiple") | 0 since instrumentation deployment (only the original deploy-restart itself) | ❌ No |
| Reconnect/replay event observed | ≥1, if naturally occurring | 0 CATCHUP-sourced duplicates recorded yet (`total_duplicates=0`) | ❌ No — though this is also consistent with simply not enough time having passed for one to occur |
| Duplicate suppressions recorded so far | N/A (informational) | 0 | — |

## Remaining requirements

- **Time**: approximately 14 days of continued production operation
  with the instrumentation running, essentially undisturbed, from the
  X64.9B1 deployment forward (2026-07-21T16:02:22Z or later, since a
  restart resets `ws_cascade`'s in-process state but **not** the
  durable `total_checked`/`total_duplicates` counters — those persist
  across restarts by design, per X64.9B1's schema). The 14-day clock
  should be measured from first deployment, not reset by intervening
  restarts, since the whole point of the durable schema is that
  restarts don't invalidate the accumulated measurement.
- **Volume**: approximately 500,000 - 413 ≈ 499,587 more signatures
  need to be checked. At the very rough rate observed in this task's
  brief check (413 signatures in ~7 minutes ≈ ~59/minute, ~85,000/day
  if that rate held steady, which it likely does not — traffic almost
  certainly varies by time of day and market activity), reaching
  500,000 could plausibly take on the order of 6-10 days *if* volume
  ran steadily, but this is a rough extrapolation from a single
  7-minute sample, not a reliable forecast — the 14-day duration
  requirement is likely to be the binding constraint rather than
  volume, but both must independently clear their thresholds
  regardless of which turns out to bind first.
- **Restarts/replay**: no action needed — these should occur naturally
  during normal operation given this project's own documented
  operational patterns (periodic FD-watchdog-style restarts on related
  processes, WS reconnects). If 14 days elapses with genuinely zero
  CATCHUP-sourced activity recorded, that itself is a data point worth
  noting when the contract is eventually re-checked (per the
  Measurement Contract's own guidance: absence of a replay event
  after 14 days should prompt extending the window, not concluding
  "no replay risk").

## Recommendation

**Re-run this task (X64.9B2) no earlier than 2026-08-04** (14 days
from X64.9B1's deployment), and only once a fresh precondition check
confirms `total_checked ≥ 500,000` at that time. If 14 days elapses
without reaching 500,000 checked signatures, extend the observation
window further rather than proceeding with an under-powered sample —
per the Measurement Contract's own explicit reasoning, satisfying only
one of the two conditions is insufficient.

## What was and was not done in this task

- ✅ Checked the current state of `wt_subprov_sig_dedupe_summary`
  (read-only).
- ✅ Compared against the X64.9B1 measurement contract's explicit
  thresholds.
- ✅ Reported progress and remaining requirements, per this task's own
  instruction for the precondition-not-met case.
- ❌ Phases 1-9 (Measurement Validation, Duplicate Behaviour Analysis,
  Replay Age Analysis, Operational Dependency Review, Retention Window
  Modelling, Safety Margin Analysis, Storage Impact, Recommendation,
  Executive Summary) were **not performed** — there is not yet enough
  data for any of them to produce meaningful, evidence-based output,
  and performing them now would mean guessing rather than measuring,
  which is precisely the failure mode X64.9B/X64.9B1 already
  identified and corrected for.
- No row in any table was deleted, purged, archived, modified, or
  retimed.
