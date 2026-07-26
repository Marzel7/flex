# X64.6 — Phase 5: Timing Analysis

All 42 rows' `enqueued_at` timestamps (2026-07-20T18:58:58 through
2026-07-21T10:48:47, ~15.8 hour span) were sorted and gap-analyzed.

## Gap statistics

- **Median gap between consecutive failures**: 1,118 seconds (~18.6 min)
- **Mean gap**: 1,390 seconds (~23.2 min)
- **Max gap**: 4,482 seconds (~1.25 hours)

## No supported temporal cluster found

Per the task's explicit instruction ("report clusters only when supported
by timestamps"), no cluster is reported. The gaps between consecutive
failures are fairly evenly distributed across the full ~16-hour window —
there is no sub-window where failures bunch up dramatically relative to
the rest of the period, and no single gap large enough to suggest one
discrete outage bookended by resumed-service periods on either side. The
largest gap (1.25 hours) is only ~3.2x the median, not the kind of
order-of-magnitude silence that would indicate a listener/process outage.

## What this rules out

- **A specific outage window**: not supported — failures continue
  throughout, not concentrated before/after a gap.
- **A single process restart**: not supported for the same reason — a
  restart would typically produce one identifiable silent gap followed by
  a burst of catch-up activity; neither pattern appears.
- **A deployment**: not independently verifiable from this data (no
  deployment-timestamp table was available to cross-reference), but the
  even spread argues against a single deploy-triggered incident.
- **A websocket backlog burst**: not supported — a backlog would produce
  clustering (many failures processed in a short window once the backlog
  drains), not even spacing.
- **A database lock period**: not supported for the same reason as
  above — a lock period would produce a gap, not even spacing.

## What this does support

**A continuous, low-rate background failure mode** — consistent with
Phase 4's finding that the failure is per-mint (not per-creator, not
systemic-for-all-launches), most plausibly a per-CREATE-event capture
race or a low-probability code-path miss that fires on a small,
roughly-constant fraction of launches regardless of time of day or system
load. This is the more concerning finding operationally: a one-time
outage self-resolves once the outage ends, but a continuous low-rate
structural gap keeps producing new stuck rows indefinitely until the
underlying capture-path issue (Phase 4/9) is fixed.

## Creator concurrency note

Several creators recur across multiple stuck mints within this window
(e.g. `GeBJSHK4WsGrz2HRvTbqvWGx4JRMpHfJG2ikzrYBDuwR` appears on 4 of the
42 rows: `AyEnFJZ6…`, `7xZBhuzL…`, `9mCdjXLF…`, `9x66fFR9…`). This is
reported as an observation, not a temporal cluster — those 4 mints are
spread from `enqueued_at=1784602841` to `1784612182`, a ~2.6 hour span,
not tightly bunched, and this creator has many other successfully-captured
launches in `creator_funding_queue` (confirmed in the master audit's
Phase 1/2 section) — so this is not evidence of a creator-specific outage
either, simply a highly active creator hitting the same low-rate
background gap multiple times by volume.
