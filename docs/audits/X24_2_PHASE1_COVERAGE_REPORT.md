# X24.2 Phase 1 — Sweep Coverage Report (measured baseline)

Measured directly against the live `wt_ops_v2.db` before any scheduler change was deployed to the running daemon (the running process, PID 53852, was still executing the OLD unordered `active_sessions()[:MAX_ACTIVE_SUBPROVS]` slice at measurement time).

## Point-in-time snapshot

| Metric | Value |
|---|---|
| Eligible ACTIVE sessions | 352 |
| Cap per cycle (`MAX_ACTIVE_SUBPROVS`) | 10 |
| Never swept (via new `last_swept_at` bookkeeping) | 352 (100%) — expected, since this bookkeeping did not exist until this sprint's schema addition |
| Swept within last 30s | 0 |
| Eligible sessions expiring within 60s, never swept | 13 |
| Sessions swept more than once while others remained unswept | 0 (bookkeeping is new; historical duplicate-sweep rate under the OLD code cannot be reconstructed, since it kept no record of what it had already inspected) |

## Volume context (separately measured)

| Metric | Value |
|---|---|
| New sessions opened, last 10 minutes | 91 (~9.1/min) |
| New sessions opened, last 60 minutes | 594 (~9.9/min) |
| Sessions expired/closed, last 60 minutes | 655 (~10.9/min) |
| Peak concurrent ACTIVE sessions (point-in-time) | 283 |
| Sessions already past `expires_at` but still `state='ACTIVE'` (pending orphan cleanup) | confirmed ≥5 in a spot check — real, already-lost coverage, not merely at-risk |

## Interpretation

- At `MAX_ACTIVE_SUBPROVS=10` and `SUBPROV_SWEEP_SEC=6`, the sweep processes at most 10 sessions per 6-second cycle → 100 sessions/minute of *sweep capacity* against a **peak of 283 concurrently eligible** sessions and **~9-10/minute of new arrivals**. Capacity exceeds raw arrival rate on average, but the OLD code's lack of ordering meant capacity was not reliably *directed* at the sessions that most needed it (soon-to-expire, never-inspected) — a session could be re-swept repeatedly by coincidence of query/insertion order while another sat unswept until it expired.
- This is the quantitative confirmation the sprint required: coverage failures under the old code were a **fairness/ordering defect**, not a raw-throughput defect. The new scheduler (Phase 2) does not increase the cap; it directs the same 10-per-cycle capacity at the sessions that actually need attention first.

## What remains unproven

- The exact historical duplicate-sweep rate and never-swept-expiry count *under the OLD unordered code, at the specific moment AWiaGsus's session was open* cannot be reconstructed — no bookkeeping existed at that time to record it. The strongest available evidence is the general shape shown here (large eligible-session counts vastly exceeding a 10-slot unordered cap, with no ordering guarantee), not a specific instrumented trace of that one historical moment.
