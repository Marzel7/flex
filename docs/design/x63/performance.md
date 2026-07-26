# x63 — Walkback Queue: Measured Performance (read-only, database/wt_ops_v2.db)

All numbers below are live query results run 2026-07-21 via
`sqlite3 database/wt_ops_v2.db`. No estimates presented as fact — anything
not directly queryable is stated as such.

## Queue length / status distribution
```sql
SELECT status, COUNT(*) FROM wt_walkback_queue GROUP BY status;
```
| status | count |
|---|---|
| COMPLETE (capitalized outlier) | 1 |
| complete | 6363 |
| failed | 1 |
| skipped | 84 |
| waiting | 304 |

Total rows: **6753**. No rows currently `pending` or `running` at query
time. Note the single `COMPLETE` (uppercase) row is inconsistent with the
`STATUSES` tuple defined in `walkback_queue.py:44`
(`("pending","running","complete","skipped","failed")`) — a data anomaly,
likely from an external/manual write (possibly the shadow-validation script
path noted in `entry_points.md` §4, though that script sets `status='running'`
not `'COMPLETE'` — origin not further traced).

## walkback_class distribution
```sql
SELECT walkback_class, COUNT(*) FROM wt_walkback_queue GROUP BY walkback_class;
```
| class | count |
|---|---|
| FULL_WALKBACK | 5888 |
| LINK_ONLY | 182 |
| PARTIAL_TREASURY | 599 |
| SKIP | 84 |

No `PARTIAL_SUBPROV`, `LINK_ONLY_GRAPH`, `OP_GRAPH_ROLE_MISMATCH`, or
`SELF_ROOTED_OPERATION` rows currently present.

## intelligence_outcome distribution
```sql
SELECT COALESCE(intelligence_outcome,'NULL'), COUNT(*) FROM wt_walkback_queue GROUP BY 1;
```
| outcome | count |
|---|---|
| LINEAGE_GAP | 1769 |
| NON_WATCHTOWER | 84 |
| NO_ATTRIBUTION_FOUND | 4376 |
| NULL (unresolved / waiting) | 304 |
| WATCHTOWER_CONFIRMED | 220 |

`NO_ATTRIBUTION_FOUND` is 64.8% of all rows — the large majority of
`FULL_WALKBACK` rows terminate without finding attribution.
`WATCHTOWER_CONFIRMED` is 3.3% of rows.

## attempts distribution
```sql
SELECT attempts, COUNT(*) FROM wt_walkback_queue GROUP BY attempts;
```
| attempts | count |
|---|---|
| 0 | 570 |
| 1 | 6065 |
| 2 | 117 |
| 3 | 1 |

Only 1 row has hit `attempts=3` (the `MAX_ATTEMPTS` default) — retries are
rare; the vast majority of rows complete or terminate on their first claim
(`attempts=1`, since `claim_with_lease` increments before processing).

## priority distribution
```sql
SELECT priority, COUNT(*) FROM wt_walkback_queue GROUP BY priority;
```
| priority | count |
|---|---|
| 0 | 6753 |

**Every single row currently has `priority=0`.** The prioritization
mechanism described in `worker_lifecycle.md` and `queue_schema.md`
(`watchtower_candidates.py`'s `HIGH_PRIORITY=100` on X63 candidate
detection) exists in code and is wired into the worker's selection query,
but has never fired on this dataset. This was traced to a root cause, not
left as a hypothesis: `wt_watchtower_candidates` (the table
`evaluate_and_enqueue_candidate` inserts into before it ever reaches the
`priority` UPDATE) has **zero rows**, despite 3,052,976 rows of matching
`funding_mechanism` evidence in `wt_candidate_websocket_watches`
(3,017,300 `WSOL_WRAP_CLOSE` + 35,676 `SEEDED_ACCOUNT_CLOSE`) and 862 rows
in `wt_wrap_close_candidates`. The function never reaches its own INSERT.

The blocker is `classify_quick_birth_migration()`
(`src/ops/operational_intelligence.py:68-108`), called at
`watchtower_candidates.py:147-151`:
```python
quick = classify_quick_birth_migration(
    primitive.get("block_time"), timing.get("created_at"), timing.get("migrated_at")
)
if not quick["is_quick_birth_migration"]:
    return None
```
`is_quick_birth_migration` is only `True` when the classifier's internal
`reason` is exactly `"OK"`, which requires all three of birth/create/
migration timestamps to be non-NULL and within threshold — in particular
`timing.get("migrated_at")` (read from `token_analysis.migrated_at`,
`watchtower_candidates.py:112`) must already be set. But
`evaluate_and_enqueue_candidate` is invoked from `walkback_queue.py`'s
`enqueue_migration()` and the live curve listener — i.e. at/near CREATE
time, before a token has migrated to PumpSwap (if it ever does; migration
can be minutes away or may never happen). At that point
`token_analysis.migrated_at` is virtually always NULL, so `reason` resolves
to `"MISSING_MIGRATION"`, which is **not** in the classifier's own
`evaluable` set (`{"OK","BIRTH_TOO_OLD","MIGRATION_TOO_SLOW"}`) and
`is_quick_birth_migration` is `False` by construction. The function returns
`None` before the INSERT, on essentially every real call. This is not a
rare edge case or a detector-conditions miss — it is the default execution
order making the gate almost structurally unsatisfiable at its current call
site. (A secondary, non-blocking issue: `primitive.get("block_time")` — the
wrap-close event time — is passed as the classifier's `creator_birth_at`
argument, which is a naming/semantic mismatch but not what causes the
zero-row outcome.)

## RPC usage
```sql
SELECT SUM(rpc_used), AVG(rpc_used), MAX(rpc_used) FROM wt_walkback_queue;
```
| SUM | AVG | MAX |
|---|---|---|
| 48604 | 7.20 | 34 |

Average of ~7.2 RPC credits consumed per row across the full table
(including zero-RPC LINK_ONLY/SKIP rows, which drag the average down).
Restricting to `FULL_WALKBACK`/`PARTIAL_TREASURY` rows would give a more
representative per-processed-job figure but was not separately queried here
— flagged as a gap rather than estimated. Given the code path
(`SIG_PAGE_COUNT=3` pages + up to `TX_FETCH_LIMIT=5` `getTransaction` calls
per hop, up to 2 hops for `FULL_WALKBACK` plus optional deep expansion to
`DEEP_MAX_HOPS=8`), the theoretical worst case per row is much higher than
the observed average, consistent with most rows resolving quickly (hop1
absent → `NO_ATTRIBUTION_FOUND` in 1-2 RPC calls) per the outcome
distribution above.

## Failure / error rate
```sql
SELECT COUNT(*) FROM wt_walkback_queue WHERE status='failed';
```
Result: **1** row currently in terminal `failed` state (0.015% of all
rows). This is consistent with the attempts distribution above — retries
and hard failures are both rare in this dataset.

## Average processing latency
```sql
SELECT AVG(completed_at-started_at) FROM wt_walkback_queue
WHERE status='complete' AND started_at IS NOT NULL AND completed_at>=started_at;
```
Result: **1.44 seconds** average (across rows where `started_at` is
non-NULL, i.e. rows that actually went through the `running` claim state
rather than completing instantly at enqueue time via the zero-RPC path,
which never sets `started_at`).

## Oldest pending row
```sql
SELECT MIN(enqueued_at) FROM wt_walkback_queue WHERE status='pending';
```
Result: **no rows** (empty result set) — there is currently no pending
backlog at all; the queue is fully drained at time of measurement.

## What could NOT be derived from current schema/data
- **Per-walkback_class average processing time** — `started_at`/`completed_at`
  exist but were not cross-tabulated by class in this pass; derivable in
  principle, not computed here.
- **RPC calls per job broken out by class** — `rpc_used` exists per-row but
  a class-conditioned average was not separately queried.
- **Historical queue-depth trend over time** — no time-series snapshot table
  exists; only current-instant counts are available. `queue_stats()`'s
  `trend_24h` (walkback_queue.py:475-482) computes a 24h enqueue-time split
  live but was not invoked as part of this audit (would require importing
  and running Python, not a plain read-only `sqlite3` query, and was
  skipped to keep this audit to declared read-only SQL per the task's
  encouraged method).
- **Wall-clock throughput (rows/hour) under sustained load** — not derivable
  from a single point-in-time snapshot without a time-series log; the
  `queue_stats()`/`build_walkback_health()` `completed_per_minute`/
  `completed_last_hour` counters exist in code but reflect only the moment
  they're queried, not historical throughput.
