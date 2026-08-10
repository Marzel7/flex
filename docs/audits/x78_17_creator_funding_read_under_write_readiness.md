# X78.17 — Creator Funding Read-Under-Write-Lease Elimination & Readiness Recovery

Date: 2026-08-10 (Europe/London)

## Outcome

Two proven Creator Funding read-under-write boundaries were corrected and validated. The original X78.16 queue scan no longer owns the global write lane, and page-level funder classification finishes before persistence acquires it.

Production readiness was **not started**. Observation exposed independent stop conditions: the listener restarted after a genuine three-sample descriptor buildup, the API master remained alive with no serving Gunicorn workers, database p99 remained approximately 14.1 seconds, and additional long-holder paths remain outside the two corrected boundaries.

Evidence remained disabled. Acquisition remained on hold.

## Repository baseline

- Branch: `classification-attribution-axis`
- Starting commit: `30eb3921170aa3c6f5351eff286decf507485d6e`
- X78.13: `463ae476`
- X78.14: `d9da7dfd`
- X78.15: `749bcb9f`
- X78.16: `30eb3921`
- Unrelated working-tree changes were preserved.

## Root cause 1: queue selection under a recovery lease

The X78.16 holder attributed to `creator_funding_worker.py:202 in _db_connect` was `_recover_stale_and_claim()`.

Old transaction:

1. recover completed stale rows (`UPDATE`);
2. reap genuinely stale rows (`UPDATE`);
3. scan and sort all eligible rows (`SELECT ... ORDER BY effective_priority`);
4. claim rows (`UPDATE`);
5. commit.

The first `UPDATE` acquired the global write lease. The priority scan therefore retained it even though the scan was read-only.

Production census and plan:

- queue rows: 24,926;
- ready rows: 17,503;
- funder rows: 83,800;
- plan: `idx_creator_funding_queue_status` plus `USE TEMP B-TREE FOR ORDER BY`.

The temporary age-priority sort explains the sampled `sqlite3_step` / `pread` activity.

- Old observed write-lease duration: approximately 14.4 seconds.
- Current isolated read duration: 0.19 seconds for the same query family at 17,503 ready rows.
- New write-lease duration: the deep read owns no write lease; concurrent-writer fixture completed in under one second.

New transaction boundaries:

1. short recovery write, commit, close;
2. genuine `mode=ro` candidate selection, close;
3. short conditional claim transaction, commit, close.

The claim repeats status, lock and retry-time eligibility predicates. A concurrent claimant therefore cannot create a duplicate claim. Priority ordering and returned row semantics remain unchanged in the uncontended single-worker case.

## Root cause 2: classification reads interleaved with page writes

`RealTimeCreatorFundingExtractor._flush_page_batch()` previously processed funders one by one:

1. CEX/classification reads;
2. first funder insert acquires write lease;
3. later funders perform more CEX reads while that lease remains held;
4. remaining inserts;
5. commit.

The function now completes all CEX and in-memory infrastructure classification first, prepares the exact persistence tuples, and then performs one `executemany` followed by the existing commit. Attribution and classification semantics are unchanged.

## Connection and transaction census

| Path | Classification | Boundary result |
|---|---|---|
| `_pending_count`, `_funder_count` | READ_ONLY | Existing `mode=ro`; safe |
| stale recovery | WRITE_ONLY | Short mutation committed before scan |
| ready-row priority selection | READ_ONLY | Moved to `mode=ro` |
| conditional claim | WRITE_ONLY | Separate short transaction |
| mark complete/retry/failed | WRITE_ONLY | Existing short transactions |
| second-hop-lite enqueue | MIXED | Reads precede first write; no read-under-write found |
| risk scoring setup | SCHEMA + READ + WRITE | Existing commit releases schema lease before scoped context reads; persistence uses separate connection |
| page extraction connection | MIXED | Page classification reads now precede batch write |
| network membership assignment | READ then WRITE | Existing explicit read/write split |
| post-extraction intelligence refresh | MIXED | Existing bounded caller; not changed |
| second-hop rebuild | MIXED, full rebuild | Runtime-proven remaining long-holder; not safely reducible within this narrow patch |

No Creator Funding connection was found intentionally spanning Helius, SNS, HTTP retry or sleep while holding the lease after the corrected page flush. The extraction connection remains open across RPC pagination, but commits release each tracked write lease; connection lifetime is not itself lease lifetime.

## Deterministic validation

The new regression suite proves:

- recovery write lease is absent before the ready-row scan begins;
- a concurrent writer commits in under one second while a deliberately held read-only scan remains open;
- a read failure leaves no lease behind;
- every page-level classification `SELECT` occurs before the first persistence statement;
- queue age-promotion ordering remains unchanged;
- X78.14 cancellation behavior remains unchanged.

Targeted regression: **63 passed in 1.47 seconds**.

No index was added. Boundary correction removes writer blocking even though the read still requires a temporary sort.

## Deployment

Only `creator_funding_worker` was restarted.

- Previous PID: 34622
- Final deployed PID: 37085
- Listener was not restarted for this deployment.
- API and Creator Resolution were not restarted.

## Immediate production proof

The first natural post-deployment job was:

- creator: `GUyxTH8fvrFxEocifc9dCj93MqsN7awRgZbP4R4eWwNs`
- mint: `DW664t9dMhRa1cTtsaQ1JBjkCp9EpWBvkQftr6X2pump`

During its RPC/read phase there was no current global owner file, confirming that the long-lived extraction connection itself did not own the lane.

The job later encountered a bounded `CrossProcessDatabaseWriteTimeout` while flushing its first recovered transaction. Owner metadata was absent at the timeout. X78.14 cleanup completed and the stale reaper retained recovery responsibility. This is not a recurrence of unbounded cancellation, but it prevented a qualifying completion.

Post-deployment Creator Funding heartbeat remained current and advanced to a second claimed job. Fresh qualifying completions: **0 / 3**.

## New and remaining blockers

### Listener

The X78.16 three-sample watchdog behaved as designed but proved the high descriptor count was persistent:

- PID 35434;
- samples: 12 (`1/3`), 12 (`2/3`), 13 (`3/3`);
- fatal restart at 10:18:21 BST;
- replacement PID 36308.

The same interval included event-loop lags of approximately 77–86 seconds and cross-process write timeouts. Listener qualification therefore reset and then failed the X78.17 prerequisite.

### Additional Creator Funding process holders

Runtime logs identified further exact holders:

- `second_hop_builder.py:99 in build`, Creator Funding PID 34622, background priority, blocking a listener price callback for 60 seconds;
- `realtime_creator_funding_extractor.py:1298 in extract_for_creator`, Creator Funding PID 34622, blocking listener startup/metrics writers;
- `rpc_metrics_recorder.py:341 in _metric_flush_loop`, Creator Funding PID 34622, blocking an API worker cache initialization for 60 seconds.

The first two motivated the safe page-boundary correction, but the second-hop rebuild remains a large mixed read/write transaction. Its SQL performs deletes/inserts, full relationship reads, Python graph computation, and later writes on one connection. Splitting it safely requires its own scoped materialization/transaction design; doing that here would violate X78.17's no-speculative-broad-repair rule.

### API

Supervisor reported API master PID 30675 as RUNNING, but repeated loopback checks returned connection refused and no Gunicorn worker processes were present. The master-only state is not API availability and is an explicit readiness stop condition.

### Creator Resolution

Completed rows advanced to 5,422. The latest heartbeat recorded a bounded `CrossProcessDatabaseWriteTimeout` at cycle 16. Progress is genuine, but the current contention means the worker cannot be classified fully healthy.

### Database

Latest serializer snapshot:

- p50: 0.00 ms;
- p95: 55.13 ms;
- p99: 15,965.57 ms;
- average wait: 443.35 ms;
- queue depth: 2;
- maximum queue depth: 3;
- WAL: not critically pinned;
- write rate: 16.2/min.

This remains volatile and readiness-blocking despite low median/95th-percentile latency.

### Ingestion

The database contained 12 recent births and 5 recent migrations in the bounded 15-minute query window. Event flow therefore continued, but listener restart and feed reconnects prevent a healthy qualification.

## Readiness gates

- Corrected queue read does not own write lane: **PASS (deterministic)**
- Corrected page classification boundary: **PASS (deterministic)**
- No remaining Creator Funding read-under-write holder: **FAIL**
- Three fresh Creator Funding completions: **FAIL (0/3)**
- X78.14 cancellation bounded: **PASS**
- Creator Resolution progress: **PASS, with current bounded contention**
- Listener stable for 15 minutes: **FAIL**
- API available: **FAIL**
- Database not persistently AT_RISK: **FAIL**
- Operational Intelligence causal test: **NOT STARTED**
- Disk headroom: **PASS (56 GiB)**

## Final verdicts

- Creator Funding Boundary: **B — MATERIAL HOLD REDUCED BUT RECURRENCE EXISTS**
- Database: **D — NEW_WRITE_LANE_DEFECT**
- Creator Funding: **B — DEGRADED_BUT_PROGRESSING**
- Listener: **C — UNSTABLE**
- Creator Resolution: **B — TRANSIENT_CONTENTION_RECOVERED**
- Operational Intelligence: **D — INSUFFICIENT_WINDOW**
- Production Health: **D — NEW_DEFECT_FOUND**
- Evidence Activation: **HEALTH_REPAIR_REQUIRED**
- Acquisition: **HOLD_ACQUISITION**

## Readiness clock

- Start: **NOT STARTED**
- 15 minutes: **NOT STARTED**
- 30 minutes: **NOT STARTED**
- 60 minutes: **NOT STARTED**

The next work must be a narrowly scoped repair for the proven long mixed transaction(s) and API master-without-workers failure. Evidence Platform activation and the 5,000-attempt acquisition remain prohibited.
