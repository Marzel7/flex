# X78.31 — Creator Funding Capacity, Existing Fast-Path Audit & Safe Parallelism

Date: 10 August 2026  
Scope: bounded production audit and repair of proven existing fast paths only  
Authority: Creator Funding remains authoritative; Evidence acquisition remains on hold

## Executive result

The worker already had a known-creator shortcut, but three implementation defects reduced its value:

1. an optional profile bridge ran before the authoritative `creator_funders` check and attempted to use a `threading.RLock` as an async context manager;
2. the persistent graph cache read and write payloads did not match the cache contract;
3. known-creator jobs repeated creator-level second-hop, risk, network, and intelligence refresh work even though they produced no new creator evidence.

Those defects are repaired. A separate measured bottleneck was also removed from the full-extraction path: live network assignment rebuilt dynamic infrastructure by scanning the large token tables for every creator, despite the supervised infrastructure synchronizer already materializing that data in `infra_wallets`. The live assignment now consumes that materialized set. Other callers retain the previous default behaviour.

The repair reduced measured network-assignment time from **117.957 s / 109.029 s** to **4.886 s / 5.399 s**. Full extraction remained **17.0–23.9 s** in the final cohort and total post-extraction enrichment was **6.435–6.439 s**.

This is a substantial serial-capacity improvement, but it does not establish capacity sufficiency. The final worker heartbeat showed **6 completions in 169 s (~128/h)** while **19 jobs arrived in the same interval (~405/h)**. The HOT queue remained **1,104**, with the oldest HOT item at **20,983 s**. No parallel execution was enabled because creator-key single-flight and extractor shared-state safety are not proven.

## Phase A — Existing shortcut census

### 1. Authoritative known-creator path

- Source: `creator_funders` rows keyed by `creator_address`.
- Before repair: checked only after the optional profile bridge.
- After repair: checked first and returns `already_processed` without history RPC.
- Worker result: `complete_fast`; the mint is marked complete and rescored, while duplicate creator-level enrichment is skipped.

### 2. Session-local shortcut

- Source: `RealtimeCreatorFundingExtractor.processed_creators`.
- Behaviour: avoids duplicate extraction within the reused extractor process.
- Limitation: process-local mutable state, not a concurrency gate and not durable across restart.

### 3. Persistent creator graph cache

- Source: `CreatorFundingGraphCache`.
- Defect: the reader expected an outer `funders` member although the cache returns the address map; the writer passed the extractor result envelope rather than the required address-to-metadata mapping.
- Repair: read keys directly and write the correctly shaped funder mapping.

### 4. Queue-level reuse

- The HOT census measured **1,074 rows / 920 distinct creators** at audit time.
- **120 rows (11.2%)** already had authoritative `creator_funders` evidence.
- **154 duplicate rows across 36 creators** were present; the largest creator represented 70 HOT rows.
- A completed full extraction therefore allows later rows for the same creator to use the durable fast path, but batch selection is not yet creator-deduplicated.

## Phase B — Capacity measurements

### Pre-repair X78.30 cohort

- 23 claims/completions over 1,947 s: approximately **42.5 completions/h**.
- No retries or failures.
- 4 fast skips: **17.4%**.
- Extraction p50 **14.3 s**, p95 **20.5 s**, maximum **40.9 s**.
- The low observed throughput was not explained by extraction alone.

### Bottleneck attribution

One fully instrumented job measured:

| Stage | Time |
|---|---:|
| Full extraction | 24.3 s |
| Second hop | 0.006 s |
| Risk | 1.116 s |
| Network assignment | 117.957 s |
| Refresh | 0.034 s |
| Total enrichment | 119.113 s |

The network stage called `build_excluded_set`, which repeatedly scanned dynamic bonding-curve, pool and PumpSwap columns in `token_analysis`. A first candidate-bounded query still took **109.029 s**, proving the missing indexes made that approach insufficient. The final live path uses the scheduler-owned `infra_wallets` materialization and does not scan `token_analysis`.

### Post-repair cohort

| Job | Extraction | Network | Total enrichment |
|---|---:|---:|---:|
| pkzEu8… | 23.9 s | 4.886 s | 6.439 s |
| 9RAuf… | 17.0 s | 5.399 s | 6.435 s |

At the final bounded sample:

- worker PID 84633 was RUNNING;
- 10 claimed, 6 completed, 1 retried, 0 failed in 169 s;
- 19 new queue rows arrived during the same worker uptime;
- HOT pending: 1,104;
- oldest HOT age: 20,983 s;
- progress state: `BACKLOGGED_BUT_PROGRESSING`.

The sample is short and must not be represented as a sustained throughput qualification. It is sufficient to show the repaired serial worker still did not match the contemporaneous arrival rate.

## Phase C — Safe parallelism audit

Parallelism was not enabled.

| Surface | Finding | Parallel-readiness |
|---|---|---|
| Queue row claim | Eligibility is repeated in a short atomic claim transaction | Safe per row |
| SQLite mutations | Routed through the existing serialized write boundary | Must remain serialized |
| Same-creator jobs | Multiple rows for one creator can be claimed in one batch; no creator-keyed single-flight | Blocked |
| Extractor singleton | Shared `processed_creators`, aiohttp session, semaphore, cursor/cache state, and background task set | Not proven |
| RPC concurrency | Extractor has an internal semaphore (`MAX_CONCURRENT_RPC=8`) | Does not prove job-level safety |
| Post-extraction work | Creator-scoped and mutating | Must not run concurrently for the same creator |

The safe next capacity change, if required, must first introduce and test creator-keyed single-flight/deduplicated claiming and explicitly validate all shared extractor state. Adding worker concurrency before those gates would risk duplicate RPC, duplicate creator work, and state races.

## Phase D — Production health

Final `/api/health/full` sample:

- API: HEALTHY, zero errors in 5 minutes.
- Listener ingestion: HEALTHY; PumpPortal and PumpSwap connected.
- Database: HEALTHY; write-lane p99 **6.27 ms**, queue depth 0, WAL 7.2 MB.
- Creator Funding: WARNING solely because oldest HOT age exceeded the 3,600 s threshold; heartbeat age 1 s and status `BACKLOGGED_BUT_PROGRESSING`.
- Operational Intelligence: WARNING with one migrated token missing creator in the last hour.
- WATCHTOWER: WARNING inherited from Operational Intelligence, not from connection failure.

This separates service health from capacity: the write lane and application are healthy, but Creator Funding freshness is not ready.

## Validation

- Focused X78.31/X78.30/X78.29/X78.14/X78.17/X78.27 set: **21 passed**.
- Expanded focused set including infrastructure separation: **28 passed**.
- Final targeted fast-path/infrastructure/accounting set: **14 passed**.
- Python compilation checks passed.
- No full regression suite was rerun.
- Three orphaned `tail -f` processes created during measurement were stopped; application services were not affected.

## Required verdicts

### Fast Paths

**B — EXISTING FAST PATHS REPAIRED**

### Capacity

**E — SERIAL CAPACITY INSUFFICIENT**

### HOT Queue

**C — HOT QUEUE FALLING BEHIND**

### Production Health

**C — HEALTHY SERVICES, READINESS BLOCKED BY CREATOR FUNDING CAPACITY**

### Evidence activation

**HEALTH_REPAIR_REQUIRED**

### Acquisition

**HOLD_ACQUISITION**

## Deployment decision

The proven fast-path and repeated-scan repairs remain deployed. Job-level parallelism is not approved. Evidence activation and further acquisition remain blocked until a sustained observation window demonstrates that HOT age and depth recover, or creator-keyed safe parallelism is implemented and validated under the frozen write boundary.
