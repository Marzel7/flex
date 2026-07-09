# DB Concurrency Issue & Proposals

## The Problem

The app and background scan scripts both write directly to `flex_complete_database.db` (SQLite). SQLite allows only one writer at a time. Even with WAL mode enabled, simultaneous write attempts cause `database is locked` errors — the losing process either retries or skips the write entirely.

**Observed impact:**
- Operator downstream scans skip ~20-30% of operators due to lock errors
- App cannot be restarted while a scan is running without risking data loss
- Scan stalls entirely when a long-running app worker holds the write lock

## Why WAL Didn't Fix It

WAL (Write-Ahead Log) allows concurrent *readers* and one *writer*. It does not allow two simultaneous writers. The app's background workers (activation poller, webhook handler, second-hop workers) and the scan script are both writers — WAL only reduces contention, it doesn't eliminate it.

---

## Proposals

### Option 1: Single Writer via HTTP API (Recommended)
**Effort: Medium | Risk: Low**

Make the app the sole DB writer. Background scan scripts POST results to internal API endpoints instead of writing SQLite directly.

- Scan does all RPC work independently
- POSTs results to `POST /api/internal/graph-edge`, `POST /api/internal/launch-candidate` etc.
- App writes to DB via its existing connection pool
- No SQLite contention — one process owns the DB

**Pros:** Clean architecture, no migration, works immediately  
**Cons:** Scan must be co-located or have network access to app; adds HTTP overhead

---

### Option 2: Write Queue in Scan Script
**Effort: Low | Risk: Low**

Scan collects all results in memory, then writes in a single batched transaction at the end of each operator — minimising the window the write lock is held.

Already partially implemented (Phase 1/Phase 2 split). Full version:
- Complete all RPC work for all operators first
- Open DB once, write everything in one transaction, close
- App uses `PRAGMA busy_timeout=30000` to wait rather than fail

**Pros:** Minimal code change, no architecture change  
**Cons:** If scan crashes mid-run, nothing is saved; doesn't help if app workers write frequently

---

### Option 3: Separate Scan Database, Periodic Merge
**Effort: Low | Risk: Medium**

Scan writes to `scan_staging.db`. App reads only from `flex_complete_database.db`. A merge job runs every N minutes via `ATTACH DATABASE` to copy staging rows into main.

**Pros:** Zero contention during scan  
**Cons:** App doesn't see scan results in real time; merge logic adds complexity; two DBs to manage

---

### Option 4: Migrate to PostgreSQL
**Effort: High | Risk: Medium**

Replace SQLite with PostgreSQL. Proper MVCC — unlimited concurrent readers and writers with row-level locking.

**Pros:** Solves the problem permanently; enables multi-process and multi-machine scaling  
**Cons:** Significant migration effort; requires Postgres running locally or hosted; all raw SQL queries need review for compatibility

---

## Recommendation

**Short term:** Option 2 — batch all writes into a single end-of-operator transaction. Low risk, can be done today, reduces lock window from ~30s to <1s per operator.

**Medium term:** Option 1 — single writer via HTTP API. Eliminates contention entirely without a DB migration. Scan scripts become thin RPC clients that POST results to the app.

**Long term:** Option 4 (PostgreSQL) if the dataset or team scales.
