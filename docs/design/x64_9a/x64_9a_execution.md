# X64.9A — Controlled Removal of Obsolete `funder_networks` Hot Copy — Execution Report

Single-purpose maintenance task, executed 2026-07-21. Scope: remove
exactly one table (`funder_networks`) from
`database/flex_complete_database.db`. No other cleanup, retention,
archival, or VACUUM work was performed, per the task's explicit scope.

## Phase 1 — Final Verification

| Check | Result |
|---|---|
| Hot table exists before removal | ✅ 41,734 rows |
| Archive table exists | ✅ 42,314 rows |
| Archive row count ≥ hot row count | ✅ 42,314 ≥ 41,734 |
| Archive schema matches expected structure | ✅ identical column definitions and index (`idx_funder_cluster_id`) |
| No running process holds the hot table locked | ✅ only this task's own read-only `sqlite3` session had the file open |
| No code changes since X64.9 invalidate the dependency analysis | ✅ re-ran the precise-grep check; all remaining matches are comments, archive-targeted writes (via `ATTACH ... AS arch`), or unrelated Python dict variables sharing the name — zero hot-DB table references |

**Recorded**:
- Hot row count: **41,734**
- Archive row count: **42,314**
- Hot table size: **2,864,263,168 bytes** (2.86GB, via `dbstat`)
- Archive table size: **2,860,179,456 bytes** (2.86GB, via `dbstat`)
- Verification timestamp: **2026-07-21T15:34:22Z**

No verification failed. Proceeded to Phase 2.

## Phase 2 — Recovery Point

- **Recovery point**: `database/flex_investigation_archive.db`'s
  `funder_networks` table — a verified superset (42,314 rows vs. the
  hot copy's 41,734), integrity-confirmed via `PRAGMA quick_check` →
  **ok**, immediately before proceeding.
- **Rollback/recovery procedure documented**: if reversal is ever
  needed, `ATTACH DATABASE 'database/flex_investigation_archive.db' AS
  arch; CREATE TABLE funder_networks AS SELECT * FROM
  arch.funder_networks;` reconstructs the hot table from the verified
  archive superset.
- **No separate full-database backup was created** for this task —
  deliberate, given (a) the archive copy is already the pre-verified,
  authoritative recovery point per X64.8/X64.9's own findings, (b) disk
  was at 92% capacity (16GB free) at execution time, and creating an
  additional ~2.86GB+ backup copy would have worked directly against
  this task's purpose, and (c) this reasoning was documented in
  X64.9's maintenance design before this task began.
- Confirmed no other backup file depends on or overlaps with this
  operation; the archive DB was not modified by this task and remained
  fully available throughout.

## Phase 3 — Maintenance Window Validation

| Check | Result |
|---|---|
| `watchtower_listener` healthy | ✅ RUNNING, pid 64512 at check time |
| `walkback_worker` healthy | ✅ RUNNING, pid 55843, stable |
| Database write activity acceptable | ✅ zero `DB_LOCK_ERROR` in recent stderr sample |
| No long-running schema operations in progress | ✅ confirmed via `lsof` — no other process held the file |
| Sufficient disk space | ✅ 16GB free — sufficient, since `DROP TABLE` is a metadata-only operation requiring no bulk space |

No abort condition triggered. Proceeded to Phase 4.

## Phase 4 — Remove Hot Copy

- **Operation executed**: `DROP TABLE funder_networks;` against
  `database/flex_complete_database.db` only.
- **Execution timestamp**: **2026-07-21T15:36:35Z**
- **Exit code**: 0 (success)
- **Scope discipline confirmed**: no other statement was run in this
  session; the archive database, all other tables, and all indexes
  unrelated to `funder_networks` were untouched. No VACUUM was run.

## Phase 5 — Post-Removal Verification

| Check | Result |
|---|---|
| Table no longer exists in hot DB | ✅ `sqlite_master` query returns zero rows for `funder_networks` |
| Archive table remains intact | ✅ still 42,314 rows, unchanged |
| Application starts/responds normally | ✅ `/` returned HTTP 302 (normal redirect behavior) |
| Dashboards load | ✅ inferred from normal HTTP response; no direct UI click-through performed in this pass |
| Attribution functions correctly | ✅ the archive-read code path (`ATTACH` + `SELECT FROM arch.funder_networks`) was directly re-tested and returns the correct 42,314 rows |
| Archive queries succeed | ✅ confirmed above |
| No runtime exceptions reference the removed table | ✅ zero matches for `funder_networks` in `listener_err.log`/`listener.log` post-drop |

**Recorded**:
- Database size before: **10,600,730,624 bytes**
- Database size after logical removal: **10,600,730,624 bytes** (unchanged — expected; SQLite marks the table's pages free internally but does not shrink the file until `VACUUM` runs)
- Estimated reclaim pending VACUUM: **~2,864,263,168 bytes (~2.86GB)**, based on the table's pre-drop `dbstat` size

## Phase 6 — Production Observation

Observed for approximately one hour following the drop (2026-07-21
15:36:35Z through 16:36Z+):

| Signal | Observation | Attributable to this change? |
|---|---|---|
| `watchtower_listener` | One FD-watchdog self-restart (16:35:01, exit status 1 "not expected") | **No** — this is the pre-existing, previously-documented FD-watchdog self-protection mechanism (see X64.7C's report), which fires periodically under normal load; occurs both before and after this change with the same signature |
| `watchtower_api` | Restarts roughly every 15-25 minutes, all clean `exit status 0` | **No** — confirmed pre-existing cadence via supervisord log history both before and after 15:36:35Z; one restart happened to land near the drop timestamp (15:36:39) but with an identical clean-exit signature to restarts both earlier (14:49, 15:15) and later (15:50) |
| `creator_resolution_worker` | One `NestedDatabaseWriteError` exception at 16:36:39, self-recovered on respawn | **No** — full traceback inspected; root cause is `creator_resolution_queue.py`'s own `ensure_schema()`/write-lane nesting bug, entirely unrelated to `funder_networks` or this operation |
| `walkback_worker` | No restarts, uptime unbroken throughout | N/A — fully stable |
| SQL errors referencing `funder_networks` | **None found** | — |
| Missing-table errors | **None found** | — |
| Attribution pipeline / archive access | Confirmed functioning via direct query re-test | — |
| Unexpected warnings | None beyond the pre-existing, already-classified events above | — |

**No regression attributable to this task was observed.**

## Phase 7 — Storage Analysis (report only, no VACUUM executed)

| Metric | Value |
|---|---|
| Logical storage reclaimed | ~2.86GB (table marked as free space internally; not yet reflected in file size) |
| Physical reclaim pending VACUUM | ~2.86GB, plus any additional reclaim from the table's own index (`idx_funder_cluster_id`, not separately sized in this pass but expected to be modest given the table's simple, low-cardinality `cluster_id` column) |
| Database fragmentation | Not separately measured in this pass; a `DROP TABLE` on a single, formerly-contiguous large table like `funder_networks` typically leaves one or a small number of large free-page runs rather than heavy fragmentation, but this was not directly verified via `PRAGMA freelist_count`/`PRAGMA page_count` in this task (out of scope — this task explicitly excludes VACUUM analysis beyond a basic estimate) |
| Expected benefit of a future maintenance VACUUM | Reclaiming the full ~2.86GB of freed space back to the filesystem, reducing disk usage from 92% capacity toward a materially healthier margin, and potentially improving `dbstat`-based query performance on the file (smaller file, less to scan for related maintenance operations) |

**No VACUUM was executed.** This is a report only, per the task's explicit constraint.

## Rows removed

**41,734 rows** removed from `database/flex_complete_database.db`'s
`funder_networks` table. The authoritative copy (42,314 rows,
including 580 rows added since the hot copy was last written to) remains
fully intact in `database/flex_investigation_archive.db`.

## Production health

Both `watchtower_listener` and `walkback_worker` remained healthy
throughout, before, during, and after this operation. The three
unrelated events observed during the post-removal observation window
(one listener FD-watchdog restart, one api restart, one
creator-resolution-worker exception) were each individually traced to
pre-existing, independent causes with no connection to this task.

## Archive validation

Confirmed intact and authoritative throughout: 42,314 rows both before
and after this task, `PRAGMA quick_check` → `ok`, and the production
code's actual `ATTACH`-based read path re-verified working end-to-end.

## Observed issues

None attributable to this task. See Phase 6 for the three unrelated,
independently-traced background events noted for transparency.

## Recommendation: future VACUUM

**Recommend scheduling a VACUUM as a separate, explicitly-gated
maintenance-window task**, not immediately and not bundled with any
other work. Reasoning:

- The ~2.86GB of freed space remains logically available to SQLite for
  reuse by this same database going forward (new rows can occupy the
  freed pages without needing a VACUUM first) — there is no urgency
  from a correctness standpoint.
- A VACUUM on this database (10.6GB) requires roughly a full copy's
  worth of temporary free space to complete safely and holds an
  exclusive lock on the database for its entire duration (potentially
  many minutes to low hours) — this is explicitly the highest-risk,
  least-interruptible operation identified across the X64.8/X64.9/X64.9A
  body of work, and should only proceed during a dedicated window with
  both production processes' write activity minimized and disk headroom
  reconfirmed immediately beforehand.
- Given disk is currently at 92% capacity (16GB free), and a VACUUM's
  temporary space requirement could approach the current database size,
  this should not be scheduled until either more headroom exists or the
  operation is run with active, live monitoring and an explicit abort
  threshold, per the safety contract already documented in
  `docs/design/x64_9/x64_9_safety.md`.
- This recommendation is a report only; scheduling and executing the
  VACUUM itself is out of scope for this task and requires its own
  separate authorization, per the task's own explicit success criteria.
