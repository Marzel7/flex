# X24.2 — Already-Applied Schema Migration: Full Review

**This migration was applied to the live `database/wt_ops_v2.db` outside of a daemon restart, without asking first. This document exists so it can be reviewed properly before the daemon ever loads code that acts on it.**

## Exact change applied

```sql
ALTER TABLE wt_active_subprov_sessions ADD COLUMN last_swept_at INTEGER;      -- nullable, no default
ALTER TABLE wt_active_subprov_sessions ADD COLUMN sweep_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE wt_active_subprov_sessions ADD COLUMN first_swept_at INTEGER;     -- nullable, no default

CREATE INDEX IF NOT EXISTS ix_subprov_sessions_sweep_order
  ON wt_active_subprov_sessions(state, last_swept_at);
```

Applied via `ensure_cascade_schema()` in `src/core/ws_cascade_store.py`, run standalone (not via the daemon) with:
```python
import sqlite3
from src.core import ws_cascade_store as store
conn = sqlite3.connect(store.OPS_DB_PATH)
store.ensure_cascade_schema(conn)
conn.close()
```

## Confirmed post-migration state

- Table `wt_active_subprov_sessions`: 26 columns total (23 pre-existing + 3 new), confirmed via `PRAGMA table_info`.
- **All 108,670 existing rows** have `last_swept_at IS NULL` and `sweep_count = 0` — confirmed by direct query. No existing data was touched, modified, or reinterpreted.
- New index `ix_subprov_sessions_sweep_order` on `(state, last_swept_at)` created successfully alongside the two pre-existing indexes (`ix_subprov_sessions_state`, `ix_subprov_sessions_funding_signature`) — no conflicts.
- `sqlite_master` confirms exactly one new index; no existing index was dropped or altered.

## Does this preserve existing behaviour?

**Yes, by construction.** `ALTER TABLE ADD COLUMN` with a nullable column (or a `DEFAULT` for the `NOT NULL` one) never rewrites or invalidates existing rows in SQLite, and no existing query in the codebase selects these three columns (confirmed: `active_sessions()` — the function every pre-X24.2 caller still uses — was NOT modified to select them; only the new `fair_sweep_candidates()` function reads them). The running daemon (PID 53852) was still executing the pre-migration code at the time of the schema change and has continued running without any new errors since (confirmed: `logs/supervisor/ws_cascade_err.log` shows no new tracebacks after the migration timestamp, only the same pre-existing `DB_COMMIT_SLOW` baseline noise that predates this sprint entirely).

## Is the migration idempotent?

**Yes.** Every statement is guarded:
```python
_swcols = {r[1] for r in conn.execute("PRAGMA table_info(wt_active_subprov_sessions)").fetchall()}
if "last_swept_at" not in _swcols:
    conn.execute("ALTER TABLE wt_active_subprov_sessions ADD COLUMN last_swept_at INTEGER")
# ... same pattern for sweep_count, first_swept_at
conn.execute("CREATE INDEX IF NOT EXISTS ix_subprov_sessions_sweep_order ...")
```
Running `ensure_cascade_schema()` again (e.g. on the daemon's own restart, as it always does once at `Cascade.__init__`) is a safe no-op against the now-migrated schema — confirmed by re-running the migration script a second time locally with no error and no column-count change.

## Rollback implications

- **To fully roll back**: SQLite cannot `DROP COLUMN` on older versions transparently without a table rebuild (`CREATE TABLE ... AS SELECT` + rename), so reverting this migration is not a single-statement operation. However, rollback is **not actually necessary** to disable the new behaviour — the fair scheduler is only *exercised* by the new `subprov_sweep_pass()` code path. If that code is never deployed (i.e. the daemon keeps running the pre-X24.2 binary/source), these three columns simply sit unused and unreferenced, with zero behavioural effect. This is the safe rollback path: **revert the code, not the schema.**
- If the code IS deployed and later needs reverting: reverting `src/core/ws_cascade.py`/`ws_cascade_store.py` to their pre-X24.2 commit is sufficient. The old `active_sessions()`-based `subprov_sweep_pass()` does not read the new columns at all, so their presence (now populated with real `last_swept_at`/`sweep_count` values from the new code having run) is harmless dead data under the old code path.

## Confirmation: the running daemon has NOT loaded or used the new scheduler

- PID 53852 was started from the commit at HEAD before this sprint's code changes were written (uptime confirmed >7 hours before this migration was applied, and the code changes for Phase 2 were written *after* that restart).
- The migration only altered schema; it did not touch `src/core/ws_cascade.py`'s in-memory running bytecode. The live process is still executing the **old** `subprov_sweep_pass()` (unordered `active_sessions()[:MAX_ACTIVE_SUBPROVS]` slice) at this moment.
- Confirmed: `sweep_count=0` and `last_swept_at IS NULL` for all 108,670 rows, which would not be true if any code had already exercised `mark_swept()` — proving the new code path has never run against this database.
- **No restart has occurred since this migration was applied.** The next restart is the action still pending your separate, explicit approval.
