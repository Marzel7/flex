# x63 — walkback_worker.py: Worker Lifecycle

## Tuning knobs (all `os.environ.get`, walkback_worker.py:63-71)
| Knob | Default | Meaning |
|---|---|---|
| `WALKBACK_BATCH_SIZE` | 8 | rows claimed per `drain_batch` call |
| `WALKBACK_INTERVAL_SEC` | 45 | `time.sleep()` between `run_loop` iterations (walkback_worker.py:1328) |
| `WALKBACK_MAX_ATTEMPTS` | 3 | attempts ceiling before a row is treated as exhausted |
| `WALKBACK_RPC_BUDGET_BATCH` | 80 | RPC credits allowed per batch before the remaining claimed rows are deferred |
| `WALKBACK_SIG_LIMIT` | 20 | unused directly in the visible pagination path (see `SIG_PAGE_LIMIT`) |
| `WALKBACK_TX_FETCH_LIMIT` | 5 | max `getTransaction` calls per hop |
| `WALKBACK_SIG_PAGE_LIMIT` | 100 | signatures per `getSignaturesForAddress` page |
| `WALKBACK_SIG_PAGE_COUNT` | 3 | max pages fetched per wallet history window |
| `WALKBACK_RPC_TIMEOUT_S` | 8 | per-RPC-call timeout |
| `WALKBACK_LEASE_SECONDS` | 300 | claim lease duration (walkback_worker.py:501) |
| `WALKBACK_WORKER_ID` | `walkback-{pid}` | identity written to `claimed_by` |
| `WALKBACK_DEEP_MAX_HOPS` | 8 | max hops in `_expand_unknown_upstream` deep walk |
| `WALKBACK_DEEP_ROW_RPC_BUDGET` | 80 | RPC budget ceiling for one row's deep-expansion phase |

## Polling
`run_loop()` (walkback_worker.py:1241-1328) is a `while True:` loop. Each
iteration: writes a heartbeat, counts pending rows
(`WHERE status='pending' AND attempts < MAX_ATTEMPTS`,
walkback_worker.py:1306-1309), calls `drain_batch()` if any exist, then
unconditionally `time.sleep(INTERVAL_SEC)` (45s default) regardless of
whether work was found or the batch was full. No backoff/speedup logic
adjusts the sleep interval based on queue depth.

## Claim (concurrency-safety)
`_mark_running()` (walkback_worker.py:498-507) calls
`deep_walkback.claim_with_lease()` (deep_walkback.py:231-240), which performs
a single atomic UPDATE:
```sql
UPDATE wt_walkback_queue SET status='running', path_state='CLAIMED',
  claimed_by=?, claimed_at=?, lease_expires_at=?, started_at=COALESCE(started_at,?),
  updated_at=?, attempts=attempts+1
WHERE mint=? AND attempts < 100
  AND (status='pending' OR (status='running' AND COALESCE(lease_expires_at,0) < ?))
  AND COALESCE(next_retry_at,0) <= ?
```
Success is `cursor.rowcount == 1`. This is a compare-and-set claim pattern:
multiple worker processes can run `drain_batch` concurrently and race to
claim the same row, but only one UPDATE affects a row (SQLite's own
row-level write serialization under WAL), so exactly one worker wins;
`drain_batch` counts the losers as `skipped_claimed`
(walkback_worker.py:1184-1186). Note the hardcoded `attempts < 100` inside
`claim_with_lease` is a second, higher ceiling independent of
`MAX_ATTEMPTS=3` used by `drain_batch`'s own SELECT — `drain_batch` never
selects a row with `attempts >= MAX_ATTEMPTS` in the first place
(walkback_worker.py:1164), so this inner `<100` guard is effectively
unreachable dead code under normal operation.

## Selection query (exact SQL)
`drain_batch()` (walkback_worker.py:1160-1167):
```sql
SELECT mint, creator, subprov, treasury, walkback_class, attempts
FROM wt_walkback_queue
WHERE (status='pending' OR (status='running' AND COALESCE(lease_expires_at,0) < ?))
  AND attempts < ? AND COALESCE(next_retry_at,0) <= ?
ORDER BY COALESCE(priority,0) DESC, enqueued_at ASC
LIMIT ?
```
This is **not** pure FIFO: it orders first by `priority DESC` (defaulting to
0), then by `enqueued_at ASC` as the tiebreak. See `performance.md` — in
current production data every row has `priority=0`, so in practice the
ordering degenerates to FIFO-by-enqueue-time, but the mechanism to override
that is live code, not hypothetical (see `integration_recommendations.md`).

## Processing
`_process_row()` (walkback_worker.py:859-1054) dispatches on
`walkback_class`:
- `PARTIAL_TREASURY`: single hop from `subprov` (walkback_worker.py:874-890).
- `PARTIAL_SUBPROV`: single hop from `creator` (walkback_worker.py:892-913).
- `FULL_WALKBACK`: recovers creator from DB if missing
  (`_recover_creator_from_db`), hop1 from creator anchored before the CREATE
  signature, hop2 from hop1's funder (searching the oldest edge of the
  window with `prefer_oldest=True`), and if hop2 is unknown, optionally
  `_expand_unknown_upstream()` continues up to `DEEP_MAX_HOPS=8` more hops
  (walkback_worker.py:915-1037, `_expand_unknown_upstream` at 818-854).
- Any other `walkback_class` value reaching the worker is treated as a bug
  and immediately `_mark_failed` with `"unexpected class {wclass} in worker"`
  (walkback_worker.py:1038-1039) — this should be unreachable since
  `LINK_ONLY*`/`SKIP`/`OP_GRAPH_ROLE_MISMATCH`/`SELF_ROOTED_OPERATION` never
  reach `status='pending'`.

## Completion
- Success → `_mark_complete()` (walkback_worker.py:573-630): sets
  `status='complete'`, `intelligence_outcome`, COALESCEs `subprov`/`treasury`,
  increments `rpc_used`, sets `completed_at`; also fans out to
  `materialize_outcome`, `sync_walkback_result`, and conditionally
  `_ensure_subprov_lead`/`_surface_treasury_review_lead` for `LINEAGE_GAP`.

## Failure handling
- Any exception inside `_process_row`'s try block is caught
  (walkback_worker.py:1041-1052). If `attempts >= MAX_ATTEMPTS` (checked
  against the `attempts` value read at row-claim time, already incremented
  by `claim_with_lease`), the row is `_mark_exhausted` — terminal
  `status='failed'`, `intelligence_outcome='NO_ATTRIBUTION_FOUND'`
  (walkback_worker.py:647-660). Otherwise the row is reset to
  `status='pending'` with `last_error` set, to be retried in a later batch
  (walkback_worker.py:1047-1052) — this is a same-connection UPDATE, not a
  call through `_mark_failed`.
- Explicit non-exception failure paths also exist:
  `_mark_failed()` (walkback_worker.py:633-644, used e.g. when
  `PARTIAL_TREASURY`/`PARTIAL_SUBPROV` has a NULL required field) sets
  `status='failed'` with `last_error` but does **not** set
  `intelligence_outcome`.

## Retry / backoff
`MAX_ATTEMPTS=3` (default) is the retry ceiling. There is **no explicit
exponential/linear backoff timer set on retry** in the paths read here —
`attempts` is incremented at claim time by `claim_with_lease`, and a failed
row simply goes back to `status='pending'` and is re-selected on the next
`drain_batch` call (subject to `next_retry_at`, but no code path in
`walkback_worker.py` or `walkback_queue.py` was observed setting
`next_retry_at` to a future value — it defaults to NULL/0, so
`COALESCE(next_retry_at,0) <= now` is always true). **This is flagged as
unconfirmed**: `next_retry_at` may be set elsewhere (e.g. inside
`deep_walkback.py` beyond what was read, or by another module not covered
in this audit) — no evidence of an active backoff schedule was found in the
files read.

## Dead-letter / terminal state
`status='failed'` is terminal within the normal batch-processing path — once
`attempts >= MAX_ATTEMPTS`, `_mark_exhausted` (or the direct exception-path
`_mark_exhausted` call) sets `status='failed'` with
`intelligence_outcome='NO_ATTRIBUTION_FOUND'`, and `drain_batch`'s SELECT
excludes `attempts >= MAX_ATTEMPTS` rows entirely, so they are never
reclaimed by normal operation. `finalize_exhausted_pending()`
(walkback_worker.py:663-671), run at worker startup, sweeps any row stuck
`status='pending'` with `attempts>=max_attempts` (a recovery case, e.g. from
a crash) into the same terminal `_mark_exhausted` state.

## Stalled-job recovery (separate from retry)
`recover_stalled_running_jobs()` (`src/ops/walkback_health.py:114-164`),
called at worker startup (walkback_worker.py:1266-1269) with
`stalled_after_seconds=max(INTERVAL_SEC*3, 180)`: any row `status='running'`
whose `COALESCE(started_at,updated_at,enqueued_at)` is older than the
cutoff is either requeued to `pending` (if `attempts<max_attempts`) or
force-failed with `last_error='STALLED_RUNNING_JOB_ATTEMPTS_EXHAUSTED'` (if
exhausted). This is the crash-recovery mechanism for a worker that claimed a
row (setting `lease_expires_at`) and died before completing it — note
`drain_batch`'s own SELECT also independently reclaims expired leases
(`status='running' AND lease_expires_at < now`), so there are two
overlapping mechanisms for reclaiming abandoned `running` rows: the
per-batch lease-expiry check inside `drain_batch`, and the startup-only
`recover_stalled_running_jobs` sweep.

## Duplicate-prevention (enqueue-side, not worker-side)
Covered in `entry_points.md` — `INSERT OR IGNORE` on `mint` PK inside
`enqueue_migration()`.
