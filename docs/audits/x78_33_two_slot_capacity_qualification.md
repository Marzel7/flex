# X78.33 — Two-Slot Sustained Capacity & HOT Queue Recovery Qualification

Date: 10 August 2026  
Observation: 1,907 seconds (31m 47s)  
Creator Funding PID: 87504  
Configured extraction slots: 2  
Behaviour changes during window: none

## Executive verdict

The two-slot architecture is operationally safe, but it is not sufficient for the measured HOT workload.

During the persisted qualification window, 153 genuinely new HOT rows arrived and 119 live worker completions occurred. The sustained arrival rate was **288.831/hour**, completion rate **224.646/hour**, and capacity ratio **0.7778**. The apparent near-flat HOT depth was produced while **2,500 pre-existing rows expired at the six-hour boundary**. The 3–6 hour cohort grew from 321 to 413.

This is an expiry-masked capacity deficit, not queue recovery.

The deficit is not explained by database pressure, provider errors, fast-path regression, or same-creator duplication. The dominant measured constraint is the long tail of full extraction: p95 74.6 seconds, 22 completed full jobs above 30 seconds, eight above 60 seconds, plus six separate 90-second timeouts. Timeout slot loss was material but not dominant by itself.

No third slot is authorized. Evidence activation and acquisition remain blocked.

## Persisted checkpoints

Machine-readable checkpoints: [x78_33_capacity_checkpoints.json](./x78_33_capacity_checkpoints.json)

| Metric | T0 | T15 | T30 |
|---|---:|---:|---:|
| Elapsed | 0 | 986s | 1,907s |
| HOT ready | 1,133 | 1,141 | 1,131 |
| Distinct HOT creators | 948 | 950 | 943 |
| Oldest HOT | 21,591s | 21,181s | 21,577s |
| <15m | 32 | 27 | 13 |
| 15–60m | 166 | 117 | 85 |
| 1–3h | 614 | 618 | 620 |
| 3–6h | 321 | 379 | 413 |
| Arrivals | 0 | 80 | 153 |
| Live completions | 0 | 58 | 119 |
| Full | 0 | 43 | 89 |
| Fast | 0 | 15 | 30 |
| Retries | 0 | 2 | 6 |
| Failed | 0 | 0 | 0 |
| Expired maintenance | 0 | 1,200 | 2,500 |
| Capacity ratio | — | 0.725 | 0.7778 |

Supervisor rotated the worker log shortly after T0. Heartbeat deltas therefore provide the authoritative full/fast/retry counters. They reconcile with durable queue transitions after subtracting 625 explicitly logged satisfied-row reconciliations. No values were retroactively invented for missing log-offset data.

## Arrival and completion accounting

Only queue rows with `created_at >= T0` count as arrivals. Maintenance transitions do not.

Only `FULL_COMPLETION + COMPLETE_FAST` count as live capacity:

- full completion: 89;
- complete fast: 30;
- total live completion: 119;
- satisfied reconciliation: 625, excluded from throughput;
- stale expiry: 2,500, excluded from throughput;
- retry: 6, excluded from completion;
- failed: 0.

Durable `funding_extracted_at >= T0` transitions totalled 741 at T30. The small difference from `625 + 119` reflects checkpoint timing while the next batch was active; it is telemetry timing, not fabricated throughput.

## Queue and freshness

Headline HOT depth fell by two rows, but this cannot be considered catch-up because 2,500 older rows were terminally expired during the same window.

Freshness moved in the wrong direction:

- oldest HOT remained effectively pinned at the six-hour boundary;
- 3–6h increased by 92 rows (+28.7%);
- 1–3h increased by six rows;
- fewer rows remained under one hour because new work was being processed and aging, but not fast enough to drain older cohorts.

No post-T0 arrival could naturally reach six hours during this 31-minute window, so newly arrived rows expired: **0**. The 2,500 expiries were pre-existing eligible HOT work crossing the boundary. They still represent real operational loss caused by the pre-existing capacity deficit.

HOT classification: **EXPIRY_MASKING_DEFICIT**.

## Fast path and creator-keyed safety

- Fast-path share: 30 / 119 = **25.2%**.
- Satisfied evidence reconciliation: 625 additional mint rows, not counted as worker throughput.
- Same creator observed active in both slots: **0**.
- Same-creator duplicate deep extraction: **0 observed**.
- Every natural claim batch remained creator-distinct by construction.
- No fast-path regression was found.

The exact number of duplicate scans prevented is not separately persisted. It would be incorrect to equate all 625 reconciliations with prevented concurrent scans, although they demonstrate that authoritative creator evidence is collapsing sibling mint work as designed.

## Slot utilization

Existing telemetry does not assign stable slot A/B identities, so separate A and B utilization cannot be truthfully reported.

Approximate aggregate utilization from logged job/enrichment spans was **about 87%** of the two-slot wall-clock budget. Minute checkpoint samples observed:

- both slots active: approximately 50%;
- one slot active: approximately 23%;
- both slots idle at the sampling instant: approximately 27%.

The point-sampled idle percentage overstates scheduler idle because several samples landed in the short boundary between completed five-row batches. It is retained as approximate only. The logs nevertheless show a real batch-tail effect: with five claimed rows and two slots, the final wave can use only one slot.

No sustained `IDLE_NO_ELIGIBLE_WORK` condition occurred; HOT backlog remained above 1,100.

## Long jobs and timeout cohort

Completed full-extraction service time:

| Metric | Value |
|---|---:|
| p50 | 17.5s |
| p95 | 74.6s |
| maximum completed | 84.8s |
| >30s | 22 |
| >60s | 8 |

Six separate creators reached the 90-second timeout on attempt one:

- `maseF7RcQAad…` / `E2vex8cGcBpfA24H…`
- `9THzoX5yGNSg…` / `8b6VG32MpzYYhrb4…`
- `6d22FozaKK23…` / `T9WjUzdestKcD5DX…`
- `BduBwyQgi681…` / `5LzWLBBi4TXzMq16…`
- `4mZZbmrSYtsn…` / `4V8GUhaZLLBcaeVK…`
- `6ghi2WeAUe37…` / `2fabD4gP4uPLMtbQ…`

All were unique; no repeat timeout creator appeared during the window. Cleanup remained bounded and the other slot continued.

Timeout slot loss: **540 slot-seconds**, approximately 14.2% of the available two-slot window. This is material but insufficient to explain the entire 22.2% capacity gap, so the verdict is not `TIMEOUT_DOMINATED`.

## Provider performance

- RPC observations: 12,559;
- recorded credits: 131,810;
- average latency: 84.82 ms;
- maximum latency: 15,468.95 ms;
- HTTP 429: 0;
- HTTP 5xx: 0;
- other HTTP 4xx: 1;
- transport retry count: 0.

The shared semaphore remained capped at eight. Semaphore wait duration is not currently persisted, so saturation frequency cannot be quantified. The absence of 429/5xx/retry pressure and low average RPC latency do not support a `PROVIDER_LIMITED` classification.

## Service-time decomposition

The instrumentation available for the window supports these measured components:

- full extraction p50/p95: 17.5s / 74.6s;
- post-extraction p50/p95: 5.526s / 11.410s;
- post-extraction maximum: 26.175s;
- post-extraction total represented approximately 14.7% of available slot time;
- DB wait p95/p99 at T30: 1.59ms / 62.24ms.

Cheap classification, individual RPC semaphore wait, funding-persistence hold, and mint-handoff time are not separately persisted. They are not backfilled by inference.

Post-extraction work remained far below the pre-X78.31 109–118 second infrastructure scan. It is bounded but still consumes meaningful slot time because it remains inside the creator flight/slot, preserving same-creator mutation safety.

## Deficit attribution

| Contributor | Measured contribution | Verdict |
|---|---:|---|
| TIMEOUT_SLOT_LOSS | 540s / ~3,814 slot-seconds = 14.2% | material, not dominant |
| RPC_SEMAPHORE_LIMIT | wait duration unavailable; no 429/5xx storm | not demonstrated |
| PROVIDER_LATENCY | extraction p95 74.6s; RPC average 84.82ms, max 15.47s | full-job long tail material |
| FAST_PATH_MISS | 25.2% fast plus 625 reconciliations; zero duplicate active creators | no defect found |
| POST_EXTRACTION_COST | p50 5.526s, p95 11.410s, ~14.7% slot budget | bounded secondary cost |
| SCHEDULER_IDLE_GAP | no empty backlog; batch-tail/short inter-cycle gaps observed | secondary utilization loss |
| DB_SERIALIZATION | p95 1.59ms, p99 62.24ms, depth 0 | not limiting |
| OTHER_EXPLICIT | broad full-extraction runtime distribution and five-row batch tail | primary remaining class |

The approved exact capacity classification is therefore `OTHER_CAPACITY_DEFICIT`, not timeout-, provider-, fast-path-, or DB-limited.

## Database and WAL

| Metric | T0 | T15 | T30 |
|---|---:|---:|---:|
| p50 wait | 0.00ms | 0.00ms | 0.00ms |
| p95 wait | 1.60ms | 1.94ms | 1.59ms |
| p99 wait | 95.88ms | 95.88ms | 62.24ms |
| p95 commit | 0.62ms | 0.51ms | 0.37ms |
| p99 commit | 4.82ms | 4.74ms | 2.61ms |
| serializer depth | 0 | 1 | 0 |
| max depth | 3 | 3 | 3 |

WAL moved through normal checkpoint cycles: 0.43 MB at T0, 35.6 MB at T15, 18.4 MB at T30, and 3.9 MB at the final health read. No pin, critical growth, new >60-second writer, nested-write regression, or queue-transition corruption was observed.

## Platform health

The same processes remained running for the complete qualification:

- Creator Funding PID 87504;
- Creator Resolution PID 69682;
- listener PID 69765;
- API master PID 77110.

Final health:

- Database: HEALTHY, p99 61.45ms, serializer depth 0.
- Creator Resolution: current, zero failures.
- Operational Intelligence: snapshot FRESH, zero missing creators in the last hour; WARNING inherited from Creator Funding capacity.
- Legacy watch worker: RETIRED.
- Listener: same PID, PumpPortal and PumpSwap connected, listener log fresh.
- API: HEALTHY, zero errors in five minutes.
- Ingestion: HEALTHY, birth and migration queues empty.
- Token Prediction/broad price runtime: remains decommissioned; price worker runtime disabled.

Services were stable for 30 minutes, but production readiness remains blocked by the qualified Creator Funding deficit.

## Required verdicts

### Creator-Keyed Dedupe

**A — HEALTHY**

### Two-Slot Safety

**A — SUSTAINED_SAFE**

### Capacity

**G — OTHER_CAPACITY_DEFICIT**

Equivalent Part AB decision: **TWO_SLOTS_INSUFFICIENT_OTHER**.

### HOT Queue

**D — EXPIRY_MASKING_DEFICIT**

### Production Health

**D — DEGRADED / READINESS_BLOCKED**

### Evidence Activation

**HEALTH_REPAIR_REQUIRED**

### Acquisition

**HOLD_ACQUISITION**

## Final decision

Keep the safe two-slot ceiling and creator-keyed deduplication. Do not add a third slot under X78.33. The next approved work should address the explicitly measured long-tail/batch-utilization deficit without increasing provider or SQLite pressure by assumption. Do not activate Evidence Platform and do not run the 5,000-attempt acquisition.
