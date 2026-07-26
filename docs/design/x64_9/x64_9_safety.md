# X64.9 — Phase 6: Safety Validation

Every proposed maintenance action from Phase 4/5 is restated here with
its rollback plan, failure detection, verification queries, expected
runtime, maximum lock duration, and abort criteria. No action described
here has been executed — this is a pre-execution safety contract for
each one.

## Action: `DROP TABLE funder_networks` (hot-DB copy)

- **Rollback plan**: none needed post-drop (the archive copy is the
  permanent record) — but the actual point of no return is the later
  `VACUUM`, not the `DROP TABLE` itself. Rollback for the `DROP TABLE`
  step specifically: none required, since the archive is a verified
  superset taken *before* the drop.
- **Failure detection**: a failed `DROP TABLE` (e.g. due to an
  unexpected lock) raises a normal SQLite exception — no silent-failure
  risk.
- **Verification queries**:
  ```sql
  SELECT name FROM sqlite_master WHERE type='table' AND name='funder_networks';
  -- expect 0 rows after drop
  ```
  plus a `PRAGMA quick_check` on the hot DB post-drop.
- **Expected runtime**: a `DROP TABLE` on SQLite is near-instant
  (metadata-only operation, does not physically move the dropped
  table's pages) — well under 1 second.
- **Maximum lock duration**: the `DROP TABLE` itself briefly needs the
  write lock (sub-second); the deferred `VACUUM` is the operation with
  a real, potentially long lock duration (see below) and is treated as
  a separate, later action.
- **Abort criteria**: abort if the archive DB's `funder_networks` row
  count is not ≥ 41,734 at re-verification time, or if `PRAGMA
  quick_check` on the archive DB does not return `ok`.

## Action (separate, later, maintenance-window-gated): `VACUUM` on the hot DB

- **Rollback plan**: none possible mid-`VACUUM` — this is the one
  action in this entire design that is not safely interruptible.
  Mitigation: never start it without confirming free space ≥ the
  current DB size (a hard SQLite requirement, `VACUUM` needs roughly a
  full copy's worth of temporary space) and confirming write activity
  is near-zero first.
- **Failure detection**: an interrupted `VACUUM` (e.g. process killed,
  disk fills mid-operation) can leave the database in a temporary
  intermediate state; SQLite's `VACUUM` is transactional in modern
  versions, so an interrupted `VACUUM` should roll back cleanly on next
  open — but this must be confirmed against the actual SQLite version
  in use before relying on it.
- **Verification queries**: `PRAGMA quick_check` immediately after, and
  a file-size check confirming the DB actually shrank.
- **Expected runtime**: for a 9.9GB database, potentially many minutes
  to low hours depending on disk speed — must be measured on a
  non-production copy first if at all possible, or scheduled with a
  generous window and monitored live.
- **Maximum lock duration**: for the entire `VACUUM` duration, the
  database is exclusively locked — no other reads or writes can
  proceed. **This is the single highest-risk action in this entire
  document** and is explicitly deferred to a dedicated, separately
  authorized maintenance window, per this project's own established
  `--i-am-in-a-maintenance-window` gating convention.
- **Abort criteria**: do not start if `active_sessions` is not
  approximately 0, if free disk space is less than the current DB
  file size, or if either production process
  (`watchtower_listener`/`walkback_worker`) cannot be safely paused for
  the operation's duration.

## Action: `DELETE FROM wt_subprov_sig_retry WHERE status IN ('DONE','FAILED')` (batched)

- **Rollback plan**: none needed — DONE/FAILED rows have zero confirmed
  ongoing value (Phase 1/2). If the scoping is later found wrong, the
  data is gone; this is why the WHERE clause must be independently
  re-verified against the live reader code immediately before each
  scheduled run, not just at design time.
- **Failure detection**: standard SQL error handling; a batch failing
  mid-loop should log and stop rather than skip to the next batch
  silently.
- **Verification queries**: `SELECT status, COUNT(*) FROM
  wt_subprov_sig_retry GROUP BY status` before and after — DONE/FAILED
  counts should be at or near zero after (new rows may have completed
  during the run, which is expected).
- **Expected runtime**: seconds per 50,000-row batch on the indexed
  `status` column; full first-run cleanup of ~2.3M rows likely
  completes within a few minutes total across all batches.
- **Maximum lock duration**: bounded per-batch (sub-second to low
  seconds per 50,000-row batch), with a deliberate pause between
  batches — no single long lock hold.
- **Abort criteria**: abort the job (not just the current batch) if
  `due_subprov_sig_retries()`'s filter logic is found to have changed
  (e.g. a code change now reads DONE rows for some new reason) —
  this should be checked as a precondition at job start, not discovered
  mid-run.

## Action: scoped `DELETE FROM wt_candidate_websocket_watches WHERE state='EXPIRED' AND id NOT IN (...)` (batched)

- **Rollback plan**: none needed for the deleted rows themselves (their
  content has no further value once the most-recent-per-subprov row is
  retained) — but this action's safety depends entirely on the
  retain-latest logic being correct. **Recommend a dry-run mode**
  (count-only, no actual delete) for at least one full cycle before the
  first live run, specifically to validate the `GROUP BY subprov_wallet`
  logic against real data before trusting it destructively.
- **Failure detection**: same batched-loop error handling as above.
- **Verification queries**: before/after row counts, plus the critical
  regression check from Design 3 (Phase 4) — sample a set of wallets
  known to have only-EXPIRED history and confirm
  `ws_cascade_store.py:771-774`'s existence check still returns the
  same answer post-purge.
- **Expected runtime**: longer than the retry-queue job given the
  larger row count and the `GROUP BY` subquery — measure via
  `EXPLAIN QUERY PLAN` before scheduling for real.
- **Maximum lock duration**: bounded per-batch, same pattern as above.
- **Abort criteria**: abort if the existence-check regression test
  (above) fails for any sampled wallet — this indicates the retain-latest
  scoping missed a case and must be investigated before any further
  rows are deleted.

## Action: any future action on `wt_active_subprov_sessions` EXPIRED rows

**No safety plan is proposed here** — per Phase 1/2's BLOCKED
classification, this table requires new engineering work (a
summary-table migration for the `session_tag` classifier's findings)
before any deletion action can even be safely designed. This is
intentionally left undesigned in this document; do not attempt a purge
here based on any prior document's (X64.8's) now-superseded guidance.

## Action: any future action on `rpc_response_cache` or `sol_transfers`

**No safety plan is proposed** — both are excluded from the
cleanup-candidate list entirely per Phase 1/2's findings (one already
self-maintaining, one a live production dependency of a currently-idle
but not-dead subsystem).

## General safety principles applied throughout

- **No maintenance action in this document requires extended
  production downtime**, except the single explicitly-flagged
  exception (`VACUUM`), which is deliberately isolated as its own,
  separately-scheduled, maintenance-window-gated action rather than
  bundled with anything else.
- Every scoped `DELETE` uses a `WHERE` clause independently re-derived
  from live code inspection in this same audit (Phase 1/2), not
  inherited unverified from X64.8.
- Every batched operation has an explicit batch size and pause,
  following this project's own established pattern (serialized writes,
  no long single-transaction holds) rather than introducing a new
  locking model.
