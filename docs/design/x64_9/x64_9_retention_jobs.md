# X64.9 — Phase 5: Automated Retention Job Design

Design only — no job is implemented or scheduled by this document. All
jobs below are proposed additions to the existing crontab (which today
runs the Helius monitor every 5 minutes, graph analyzers every 4 hours,
and a migration-coverage audit hourly — confirmed via `crontab -l`).

## Daily jobs

### Job: `purge_expired_subprov_sig_retries.py`

- **Schedule**: daily, off-peak (e.g. `0 4 * * *`, matching low
  overnight detection volume if one exists, or otherwise any hour not
  overlapping the existing 4-hourly graph-analyzer run to avoid
  compounding lock contention)
- **SQL scope**: `DELETE FROM wt_subprov_sig_retry WHERE status IN
  ('DONE','FAILED')`, executed in batches of ~50,000 rows per the
  batching strategy in Design 2 (Phase 4)
- **Expected runtime**: a few seconds per batch on an indexed
  status column (`ix_subprov_sig_retry_status` already exists); total
  job runtime for a full daily catch-up likely under 1-2 minutes given
  the indexed lookup, even at the current ~2.3M-row backlog on the
  first run
- **Locking impact**: brief per-batch write locks only, using this
  project's existing `DB_WRITE_SERIALIZE` serialized-write lane — no
  new locking pattern introduced
- **Rollback**: none needed in the traditional sense (this is a pure
  data-reduction operation with no schema change) — if a bug in the
  job accidentally purges more than intended, recovery would rely on
  the fact that DONE/FAILED rows have zero ongoing value, so the
  practical "rollback" is simply accepting the loss, which is why the
  scoping (`WHERE status IN (...)`) must be verified correct before
  first deployment, not after

### Job: `expire_stale_websocket_watches.py`

- **Schedule**: daily, same off-peak window as above
- **SQL scope**: the retain-latest-per-subprov purge from Design 3
  (Phase 4) — **only after** the existence-check mitigation is in
  place (either the code refactor or the retain-latest-per-subprov
  scoping baked into this job itself)
- **Expected runtime**: longer than the retry-queue job given the
  larger row count (~3M EXPIRED rows) and the `GROUP BY subprov_wallet`
  subquery required to identify rows to retain — recommend a first-run
  time estimate via a `EXPLAIN QUERY PLAN` check and a dry-run count
  before scheduling for real; likely single-digit minutes given
  `ix_cand_watch_state` and `ix_cand_watch_subprov` already exist
- **Locking impact**: same batched-delete pattern as above; the
  `GROUP BY` subquery should be read-only and fast given existing indexes
- **Rollback**: same reasoning as above — no schema change, and the
  preserved most-recent-row-per-subprov design is specifically there to
  avoid needing a rollback for the existence-check dependency

## Weekly jobs

### Job: `cache_health_check.py` (observability only, not a new eviction mechanism)

- **Schedule**: weekly (e.g. `0 6 * * 0`)
- **SQL scope**: read-only — report `rpc_response_cache` size, hit-rate
  (via existing `hit_count` column), and expired-but-not-yet-purged
  count (the same metric `main.py:25405-25409` already computes) as a
  health-dashboard data point. **No new purge logic** — the existing
  inline TTL eviction (Design 4, Phase 4) already handles this; this
  job exists only to catch a regression if that eviction logic ever
  breaks (e.g. cache growing unbounded despite TTLs being set)
- **Expected runtime**: seconds — simple aggregate queries
- **Locking impact**: negligible, read-only
- **Rollback**: N/A — observability-only, no data modified

## Monthly jobs

### Job: `archive_aged_prediction_data.py`

- **Schedule**: monthly (e.g. `0 3 1 * *`, first of the month)
- **SQL scope**: the archive-move design from Design 5 (Phase 4) for
  `prediction_decision_context` + `token_prediction_events`, scoped to
  rows older than a 6-month cutoff (configurable) — **only after** the
  code-repointing work described in Design 5 is completed and deployed;
  this job should not run against a codebase that doesn't yet know how
  to read the archive-tier rows
- **Expected runtime**: depends on the monthly volume of newly-eligible
  rows (rolling window, so most months will move a modest incremental
  slice, not the full historical backlog) — recommend a dry-run row
  count check before first live run
- **Locking impact**: an `INSERT ... SELECT` into the archive DB is a
  separate-file operation and shouldn't contend with the hot DB's write
  lane at all; the subsequent `DELETE` from the hot DB should use the
  same batched pattern as the daily jobs
- **Rollback**: if the archive copy is later found incomplete/corrupted,
  the hot-DB rows are still present until the (separate, later) delete
  step runs — so there is a natural safety window between archive-insert
  and hot-DB-delete where a `PRAGMA quick_check` + row-count comparison
  can catch problems before anything is removed from the hot DB

## Quarterly jobs

### Job: `x64_storage_audit.py` (a lightweight, automated successor to this manual X64.8/X64.9 process)

- **Schedule**: quarterly (e.g. `0 5 1 */3 *`)
- **SQL scope**: re-run the `dbstat`-based size ranking, row-count
  snapshot, and a lightweight version of the code-reference cross-check
  (grep-based) for the known candidate/archive/cleanup tables tracked
  by this and future audits — producing a diffable report against the
  previous quarter's snapshot rather than a from-scratch investigation
  each time
- **Expected runtime**: minutes (dbstat scans on a multi-GB DB are the
  dominant cost, as observed directly during this audit's own dbstat
  queries needing to run in the background)
- **Locking impact**: read-only `dbstat` queries; low impact, though
  should still be scheduled off-peak given the scan cost on a 10GB+
  live database
- **Rollback**: N/A — read-only, produces a report only

## General design notes applying to all jobs above

- Every job should log its own start/end time, rows affected, and any
  errors to a dedicated log file (matching the existing
  `logs/graph_analyzers.log` pattern), so that a future audit doesn't
  need to reconstruct "did this job actually run" from first principles
  the way this audit had to for `coordinated_creator_edges`.
- Every job should check a `QUEUE_GUARD`-style precondition where
  relevant (e.g. don't run the WS-watch purge if `ws_cascade` is mid-
  processing a fan-out burst) — reusing the existing guard pattern
  already proven for the graph-analyzer job rather than inventing a new
  coordination mechanism.
- None of these jobs should be scheduled to run simultaneously with
  each other or with the existing graph-analyzer / migration-coverage
  jobs, to avoid compounding write-lock contention on the same database
  files.
