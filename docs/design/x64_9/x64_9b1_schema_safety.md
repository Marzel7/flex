# X64.9B1 — Phase 3: Schema Safety

Documents the exact schema implemented in `ensure_cascade_schema()`
(`src/core/ws_cascade_store.py`), and how each of the task's schema
constraints is satisfied.

## Exact schema

```sql
CREATE TABLE IF NOT EXISTS wt_subprov_sig_dedupe_stats (
    subprov_wallet      TEXT NOT NULL,
    age_bucket          TEXT NOT NULL,
    duplicate_count     INTEGER NOT NULL DEFAULT 0,
    max_duplicate_age_s INTEGER,
    first_observed_at   INTEGER,
    last_observed_at    INTEGER,
    source_ws           INTEGER NOT NULL DEFAULT 0,
    source_catchup      INTEGER NOT NULL DEFAULT 0,
    source_retry        INTEGER NOT NULL DEFAULT 0,
    source_hot_burst    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (subprov_wallet, age_bucket)
);
CREATE INDEX IF NOT EXISTS ix_subprov_sig_dedupe_stats_bucket
    ON wt_subprov_sig_dedupe_stats(age_bucket);

CREATE TABLE IF NOT EXISTS wt_subprov_sig_dedupe_summary (
    id                    INTEGER PRIMARY KEY CHECK (id = 1),
    total_checked         INTEGER NOT NULL DEFAULT 0,
    total_duplicates      INTEGER NOT NULL DEFAULT 0,
    max_duplicate_age_s   INTEGER,
    first_duplicate_at    INTEGER,
    last_duplicate_at     INTEGER,
    updated_at            INTEGER NOT NULL
);
```

Both created inside the existing `ensure_cascade_schema(conn)` function
(`ws_cascade_store.py`), immediately before its final `conn.commit()` —
the same idempotent, run-once-at-startup mechanism every other cascade
table already uses (invoked via `store.operations_write("ws-cascade-schema-startup",
store.ensure_cascade_schema)` at `ws_cascade.py:2112`, per Phase 1's audit).

## Constraint-by-constraint compliance

| Constraint | How satisfied |
|---|---|
| Use a new, explicitly named metrics table | Two new tables, `wt_subprov_sig_dedupe_stats` and `wt_subprov_sig_dedupe_summary` — names chosen to be unambiguous about purpose and clearly distinct from `wt_subprov_sig_retry` |
| Do not modify the semantics of `wt_subprov_sig_retry` | Not touched — its schema, indexes, and row contents are completely unmodified by this change. The dedupe check's `SELECT` was widened from `status` to `status, last_attempt_at` (still a read-only query, same WHERE clause, same table) — this is the one and only change to any statement touching `wt_subprov_sig_retry`, and it remains a pure read |
| Do not add foreign keys | Confirmed — neither new table declares any `FOREIGN KEY`, consistent with this project's existing convention (no table in either database declares SQLite FK constraints, per X64.8's finding) |
| Avoid storing full duplicate payloads | No transaction data, no signature, no raw event payload is stored — only the wallet, an age bucket, counts, and timestamps (see the observability design's "what is deliberately not stored" section) |
| Use bounded aggregation or explicit retention | Bounded by construction: `wt_subprov_sig_dedupe_stats` is capped at `(distinct wallets with ≥1 duplicate) × 10 buckets`; `wt_subprov_sig_dedupe_summary` is capped at exactly 1 row via `CHECK (id = 1)` |
| Ensure writes use the project's approved database write lane | All writes go through `conn.execute()`/`conn.commit()` on a `TrackedConnection` obtained via `self._ops()` → `db_connect(OPS_DB_PATH, ...)`, the same mechanism every other write in this file uses — which transparently routes through the project's `DB_WRITE_SERIALIZE` process-wide write lane (per `db_locking.py`) |
| Avoid nested-write-lane behaviour | The new recording functions (`_record_subprov_sig_dedupe`, `_record_subprov_sig_checked_only`) always open their **own**, separate, short-lived connection via `self._ops()` — never reusing or nesting inside the dedupe check's own already-open `conn`. This directly avoids the `NestedDatabaseWriteError` class of bug this project has hit before (observed independently during X64.9A's production-observation window) |
| Make schema creation idempotent | Both tables use `CREATE TABLE IF NOT EXISTS`; the index uses `CREATE INDEX IF NOT EXISTS` — safe to call on every process startup, exactly like every other statement in `ensure_cascade_schema()` |

## Write-path summary (implemented in Phase 4)

- **Duplicate observed** (`row[0] == "DONE"`): `_record_subprov_sig_dedupe()`
  opens one connection, calls `record_subprov_sig_checked()` (increments
  `total_checked`) then `record_subprov_sig_duplicate()` (upserts the
  per-wallet/bucket row and the global summary), commits, closes. Both
  writes share the connection to avoid an extra connection-open on the
  hot duplicate path — but this is still fully separate from the
  dedupe-check's own `conn`.
- **Non-duplicate path**: `_record_subprov_sig_checked_only()` is called
  *after* the dedupe-check's `conn` has already been closed (in the
  `finally` block), incrementing only `total_checked` via its own
  independent connection.
- Every write function (`record_subprov_sig_duplicate`,
  `record_subprov_sig_checked`) is a plain, synchronous function taking
  a `conn` parameter — no implicit connection management, no global
  state, matching the existing style of every other `subprov_sig_*`
  function in `ws_cascade_store.py`.
