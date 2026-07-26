# X64.9 — Phase 4: Maintenance Operation Design

Design only — nothing in this document is implemented. Each design
below covers verification, backup requirement, the operation itself,
post-operation verification, and (where relevant) reclaim strategy.

## Design 1: Obsolete table removal (`funder_networks` hot-DB copy)

**Verification (pre-condition, re-run immediately before execution)**:
1. Confirm `flex_investigation_archive.db` is attached and its
   `funder_networks` row count is still ≥ the hot copy's 41,734 (it
   should be substantially higher by execution time, per its ongoing
   growth).
2. Confirm zero code references remain (`grep -rn` for direct
   `funder_networks` usage, excluding `arch.funder_networks` and
   `atomic_funder_networks`).
3. Confirm no process holds the hot DB file exclusively locked in a way
   that would block a `DROP TABLE` (a normal serialized write should be
   fine, per this project's `DB_WRITE_SERIALIZE` pattern).

**Backup requirement**: given the disk-space constraint documented in
X64.8 Phase 7, a full pre-operation backup is **not recommended** (it
would itself consume disk the operation is trying to free). Instead:
confirm the archive copy's integrity via `PRAGMA quick_check` on
`flex_investigation_archive.db` immediately before proceeding — this
serves as the effective "backup already exists" verification, following
the same reasoning X64.7C used for the June 4 backup deletions.

**Operation**:
```sql
DROP TABLE funder_networks;
```
(A single `DROP TABLE`, not a `DELETE` + separate cleanup — this table
has no other objects depending on its structure, per the zero-FK schema
convention already confirmed project-wide.)

**Verify**:
1. Confirm the table no longer exists (`sqlite_master` query).
2. Confirm the archive copy is unaffected (separate file, but verify
   anyway — `SELECT COUNT(*) FROM funder_networks` against the archive DB).
3. Confirm `watchtower_listener` and `walkback_worker` remain running
   with no new errors (same process-health check pattern as X64.7C).

**VACUUM strategy**: SQLite does not reclaim disk space from a `DROP
TABLE` until a `VACUUM` runs (the freed pages are marked free within
the file but the file doesn't shrink). A `VACUUM` on a 9.9GB database
requires roughly as much free space again as the database's final size
to complete safely, and holds an exclusive lock for its duration —
**this is the single riskiest part of this whole design** given the
disk is already at 91% capacity. Recommend deferring `VACUUM` to a
dedicated, explicitly-scheduled maintenance window (matching this
project's existing `--i-am-in-a-maintenance-window` gating convention
used by `scripts/reclaim_funder_networks_space.py`), run only after
confirming free space is sufficient, and only during a period of low
write activity (checked via `active_sessions≈0` per this project's
"Unified worker health + DB maintenance plan" precedent). The `DROP
TABLE` step alone is safe to run immediately; `VACUUM` is a separate,
later, more carefully gated step.

## Design 2: Retry queue purge (`wt_subprov_sig_retry`)

**Verification**: re-run the status distribution query immediately
before purging (`SELECT status, COUNT(*) FROM wt_subprov_sig_retry
GROUP BY status`) to confirm the DONE/FAILED share hasn't shifted
unexpectedly, and confirm `due_subprov_sig_retries()`'s filter (the
sole reader) still excludes DONE/FAILED as of the current code.

**Backup requirement**: none — this is fully reconstructable-from-chain
data (retry bookkeeping, not primary evidence) and low-risk given the
scoped WHERE clause.

**Purge completed rows**:
```sql
DELETE FROM wt_subprov_sig_retry WHERE status IN ('DONE', 'FAILED');
```

**Preserve active rows**: the `WHERE` clause above is the entire
preservation mechanism — PENDING and RUNNING rows are never touched.

**Batching strategy**: given 2,310,617+5 = 2,310,622 rows to delete,
a single unbatched `DELETE` risks a long write-lock hold on a
continuously-written table. Recommend batching:
```sql
DELETE FROM wt_subprov_sig_retry
WHERE rowid IN (
  SELECT rowid FROM wt_subprov_sig_retry
  WHERE status IN ('DONE', 'FAILED')
  LIMIT 50000
);
```
repeated in a loop with a short pause (e.g. 200-500ms) between batches
to let other writers interleave, until the query returns 0 affected
rows. This matches the general pattern of avoiding long single-write
holds already established by this project's `db_locking.py` serialized-write layer.

**Verify**: re-run the status distribution query; DONE/FAILED counts
should be at or near zero (new DONE rows may have landed since the
purge started, which is expected and fine).

## Design 3: WS-watch queue purge (`wt_candidate_websocket_watches`)

**Prerequisite (must happen before any purge, per Phase 2's BLOCKED-adjacent finding)**:
the existence check at `ws_cascade_store.py:771-774` must first be
either (a) refactored to consult a small, dedicated "subprov ever
seen" marker table/column instead of scanning
`wt_candidate_websocket_watches` directly, or (b) the purge must
exclude the single most-recent EXPIRED row per `subprov_wallet`. Option
(a) is architecturally cleaner and should be preferred if there's
appetite for a small code change; option (b) is a pure-SQL mitigation
requiring no code change and is described below as the default design.

**Verification**: confirm the state distribution
(`AUDIT_ONLY`/`BUY_SWARM`/`EXPIRED`/`EXPIRED_SIBLING`/`FIRED_CREATE`)
is still consistent with the revalidation snapshot before purging.

**Backup requirement**: none — same reasoning as Design 2.

**Purge completed rows, preserving the most-recent EXPIRED row per subprov**:
```sql
DELETE FROM wt_candidate_websocket_watches
WHERE state = 'EXPIRED'
  AND id NOT IN (
    SELECT MAX(id) FROM wt_candidate_websocket_watches
    WHERE state = 'EXPIRED'
    GROUP BY subprov_wallet
  );
```

**Batching strategy**: same rowid-batched-loop pattern as Design 2,
given the row count (~3M EXPIRED rows, minus one retained per distinct
`subprov_wallet`).

**Verify**: confirm `ws_cascade_store.py:771-774`'s existence check
still returns the same True/False answer for a sample of wallets that
previously had only-EXPIRED history, both before and after the purge —
this is the critical regression check this design exists to protect
against.

## Design 4: Cache (`rpc_response_cache`) — no action, already healthy

No new design is proposed — this table's existing TTL eviction
(`rpc_cache.py:135,219,248`, `pumpfun_curve_listener.py:4069`) is
already the correct design. If a future review wants to tune it
further: **TTL** is already per-row (`ttl_seconds` column, set at
insert time, not globally fixed); **eviction** already runs both
lazily (on read-miss) and via periodic bulk sweep; **rebuild** is
implicit — any evicted entry is simply re-fetched from RPC on next
demand, which is the entire point of a cache. No changes recommended.

## Design 5: Archive move (`prediction_decision_context` + `token_prediction_events`, as a representative archive design)

**Move**: using the same `ATTACH DATABASE` pattern already proven for
`flex_investigation_archive.db`/`funder_networks`:
```sql
ATTACH DATABASE 'database/flex_investigation_archive.db' AS arch;
CREATE TABLE IF NOT EXISTS arch.prediction_decision_context AS
  SELECT * FROM prediction_decision_context WHERE 1=0;  -- schema only, then...
INSERT INTO arch.prediction_decision_context
  SELECT * FROM prediction_decision_context
  WHERE <age-cutoff, e.g. created_at < date('now', '-6 months')>;
```

**Verify**: row-count comparison between the source subset and the
newly-archived rows (must match exactly), plus a `PRAGMA quick_check`
on the archive DB after the insert.

**Detach and confirm code repointing**: this is the step that requires
actual code changes (not just data movement) — every reader of
`prediction_decision_context` needs to become "hot rows here, older
rows in `arch.*`" aware, exactly as `main.py` already does for
`funder_networks`. This is real engineering work, not a one-off script,
and should be scoped as its own follow-on task once this design is
approved — not attempted inline with the data move.

**Delete from hot DB only after the above is confirmed working** (a
separate, later step, following the same "copy-verify-then-delete"
discipline as every prior deletion in this project's history).
