# X78.30 — Creator Funding Freshness-First Queue & Stale-Work Expiry

Date: 2026-08-10  
Branch: `classification-attribution-axis`  
Baseline commit: `cb1fc110e105436c4baa9fe15f956628f80db3ce`

## Outcome

Creator Funding now reserves live extraction capacity for queue rows created in
the six-hour operational window. Older pending/retry rows cannot be selected,
are retained as `expired` in bounded 100-row transitions with reason
`STALE_OUTSIDE_OPERATIONAL_WINDOW`, and consume zero expiry RPC. Existing
authoritative `creator_funders` evidence still wins: those rows are excluded
from expiry and reconciled to `complete` by X78.29's bounded 25-row path.

No funding extraction, attribution, CEX, BlockSec/SNS, relationship, second-hop,
risk, Operation, identity, or governance semantics changed.

## Operational usefulness boundary

`HOT_MAX_AGE = 6 hours` (`CFQ_HOT_MAX_AGE_SECONDS`, default 21,600).

Creator Funding is consumed by current Operational Intelligence snapshots,
creator/network and relationship intelligence, second-hop/risk enrichment,
WATCHTOWER inputs, and live UI/health. Its highest decision value is immediately
after launch; network and relationship refresh remains useful during the same
operating shift. No current consumer requires an unprocessed row older than six
hours to consume scarce live capacity. Historical evidence remains queryable.

| Age | Operational value |
|---|---|
| <15m | CRITICAL |
| 15–60m | HIGH |
| 1–3h | USEFUL |
| 3–6h | USEFUL / declining |
| 6–12h | LOW; no live capacity |
| 12–24h | LOW / historical |
| >24h | NONE for live processing |

The six-hour boundary is therefore consumer-derived, not a queue-size or
24-hour default.

## Baseline and capacity

Measured eligible cohorts before mutation:

| Cohort | Rows | Already satisfied |
|---|---:|---:|
| <15m | 80 | 3 |
| 15–60m | 259 | 13 |
| 1–3h | 281 | 11 |
| 3–6h | 314 | 17 |
| 6–12h | 191 | 8 |
| 12–24h | 491 | 13 |
| 1–7d | 3,672 | 210 |
| 7–30d | 8,682 | 528 |
| >30d | 4,209 | 276 |

Recent arrivals were 80/15m, 339/1h, 934/6h, and 1,616/24h. The observed
arrival rate materially exceeds the serial worker's prior measured extraction
throughput. Verdict: **SERIAL_CAPACITY_INSUFFICIENT**. X78.30 prevents
historical interference but does not conceal this capacity constraint or add
unsafe concurrency. A separate milestone should profile RPC, shared caches,
SQLite writes, downstream builders, and rate limits before changing worker
concurrency.

## Scheduler contract

- HOT: pending/retry, due and unlocked, `created_at >= now - HOT_MAX_AGE`.
- RETRY_HOT: same boundary; existing `MAX_ATTEMPTS=5`, exponential bounded
  backoff, and 90-second job timeout remain unchanged.
- STALE: original `created_at` outside the boundary; never selectable.
- TERMINAL: bounded transition to `expired`; audit row and attempt history
  retained.
- HOT order: newest `created_at`, then explicit priority, attempts, and retry
  eligibility. Equal-recency priority remains deterministic. X78.16 global age
  promotion is retained for historical documentation but no longer operates
  across the stale boundary.

## Shadow policy simulation (no writes, no RPC)

| Selection | Old policy | New policy |
|---|---|---|
| Next 100 | 100 stale; age 981.84–987.62h | 100 HOT; age 0–0.32h |
| Next 500 | 500 stale; age 931.97–987.62h | 500 HOT; age 0–1.59h |

At simulation time there were 960 HOT eligible and 17,215 stale eligible rows.
The new policy excluded every stale row.

## Deployment proof

Minimum process set restarted:

- Creator Funding: PID 74315 → 77091
- API: PID 72498 → 77110
- Listener remained PID 69765
- Creator Resolution remained PID 69682

The first deployment cycle expired exactly 100 stale rows and reconciled 25
already-satisfied historical rows. The latter do not increment genuine worker
completion counters.

The worker then reported exactly three genuine fresh completions:

| Creator | Enqueue → completion | Extraction runtime |
|---|---:|---:|
| `4MZszFFp58tq…` | 24s | 22.8s |
| `9De4ZnaZjf3j…` | 140s | 4.7s |
| `6ZPhsATLnLD2…` | 241s | 17.1s |

Heartbeat after proof: `total_claimed=5`, `total_completed=3`, `total_retried=0`,
`total_failed=0`, `total_expired=100`. Historical stale extraction claims after
deployment: **0**.

Live health after deployment:

- Creator Funding: RUNNING / BACKLOGGED_BUT_PROGRESSING
- HOT pending: approximately 960
- oldest HOT: approximately 5.8h
- recent genuine completion: present
- Database: HEALTHY, p99 wait 4.89ms, serializer depth 0, WAL 28.0MB
- ingestion: HEALTHY; PumpPortal and PumpSwap connected
- Operational Intelligence snapshot: FRESH

The bounded observation arrival rate still exceeded genuine serial completion
rate, so trend verdict is **FALLING_BEHIND**. The freshness policy is working;
capacity remains the next problem.

## Validation

Focused suite: **58 passed**.

Covered X78.30 lifecycle, X78.16 superseded fairness contract, X78.17 read/write
boundary, X78.29 truthful accounting, X78.14 cancellation, X78.27 refresh
single-flight, X78.28 Operational Intelligence freshness, and MC1 capability
health. `git diff --check` passed.

One separately run X78.22 assertion remains stale: it expects the retired log
text `funding rescore trigger=...`, while the current decommission path emits
`token prediction triggers=...`. The other three X78.22 SQL-boundary tests
passed; this is unrelated to X78.30 queue behavior.

## Readiness

Freshness policy: **operational and proven**.  
Creator Funding capacity: **insufficient for the measured arrival rate**.  
Evidence Platform readiness: **HOLD / HEALTH_REPAIR_REQUIRED** until the HOT
queue trend is stabilized or safe capacity is separately proven.
