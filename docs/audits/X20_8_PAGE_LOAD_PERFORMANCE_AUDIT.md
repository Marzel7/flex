# Sprint X20.8 — Page Load Performance Audit & Request-Path Hardening

**Audit state:** complete, one minimal fix applied
**Target:** live gunicorn instance at `http://localhost:5002` (two workers, `config/gunicorn.conf.py`, `src.core.main:app`)
**Primary stores:** `database/flex_complete_database.db`, `database/wt_ops_v2.db`
**Method:** curl-based waterfall approximation (no playwright/puppeteer available — checked `which playwright`, `node_modules`, and the Python environment; none found) + a standalone `cProfile` run of the slow service function against the live databases

No detector, RPC, websocket, attribution, identity, behaviour, assessment, forecast, or UI/business-logic mutation was performed. The one change applied is a single additive SQLite index.

## Executive summary — root cause

**The four canonical pages were never slow. One JSON API behind one of them was catastrophically slow, one query, one table.**

`GET /discovery` renders in ~2–7ms and is otherwise healthy. Its page script calls `GET /api/discovery/entity/<mint>` (`src/discovery/service.py: DiscoveryService.resolve`) whenever a user opens Discovery with an `?entity=` query string (and this is also the endpoint the entity/operator pages implicitly chain into via cross-links). That endpoint measured **3.1–3.65 seconds** on every single call, cold or warm — not a cold-cache effect.

Root cause, isolated with `cProfile` against a direct Python call to `DiscoveryService.resolve()` (bypassing Flask/gunicorn entirely, so no serializer/lock explanation is possible): of 15 SQL `execute()` calls made by `_identify()`, one — `SELECT * FROM wt_active_subprov_sessions WHERE funding_signature = ? LIMIT 1` — took **3.14s** by itself; every other call in the same request took under 2ms. `EXPLAIN QUERY PLAN` confirmed why:

```
SCAN wt_active_subprov_sessions
```

`wt_active_subprov_sessions` has 91,809 rows. Its only index is on `state` (added in `src/core/ws_cascade_store.py:162`). There was never an index on `funding_signature`, the column `_identify()` looks up on **every single Discovery entity/operator resolution**, regardless of what the requested entity actually is. This is a full table scan on 91.8k rows, once per page load, unconditionally.

This is a missing-index bug, not SQLite contention, not the write serializer, and not request-time recomputation. It is fixed by adding one index.

## 1. Canonical pages — confirmed real routes

The sprint brief's guessed paths didn't all exist as literal routes; the real ones (grepped from `src/core/main.py` and blueprint files under `src/ops/`, `src/discovery/`, `src/intelligence/`) are:

| Brief's guess | Real route | Registered in |
|---|---|---|
| `/ops-os` | `/ops-os` (confirmed, exact match) | `src/ops/shell_routes.py` |
| `/discovery` | `/discovery` (confirmed, exact match) | `src/discovery/routes.py` |
| `/intelligence/entity/<id>` | `/intelligence/entity/<entity_id>` (confirmed) | `src/intelligence/page_routes.py` |
| `/intelligence/operator/<id>` | `/intelligence/operator/<operator_id>` — **exists but always 404s** (see §4) | `src/ops/operator_routes.py` |

Real test values used: mint `111126JExxuowNpaomcmkD4EAWwUcACxzEkZwBNj1NX` (`token_analysis.mint`), operation id `launcher-observatory` (from the live `/ops-os` HTML). For the operator page, `/intelligence/operator/<operator_id>` reads from a table literally named `operators` in `wt_ops_v2.db`, which **does not exist** in the live database (`sqlite3 ... "no such table: operators"`). Every call to that route is a guaranteed 404, for any id. Since it can't be exercised meaningfully, `/intelligence/operators` (the index page) was used as the 4th canonical page instead, and the 404 is documented as a finding, not silently swapped.

## 2. Phase 1 — Page shell timing (cold + 5 warm, curl)

All four page shells are fast. These are pure Jinja renders with no server-side data fetch; all data comes from client-side `fetch()` calls after load.

| Page | Cold | Warm min | Warm median | Warm max | Size |
|---|---:|---:|---:|---:|---:|
| `/ops-os` | 3.3ms | 1.5ms | 2.0ms | 2.3ms | 69,458 B |
| `/discovery` | 2.0ms | 1.4ms | 1.7ms | 2.7ms | 75,712 B |
| `/intelligence/entity/<mint>` | 30ms | 1.6ms | 1.8ms | 1.9ms | 84,026 B |
| `/intelligence/operators` | 6.0ms | 1.4ms | 1.7ms | 3.1ms | 47,841 B |

All well under the 300ms HTML-response budget. The `/intelligence/entity/<mint>` 30ms cold hit is gunicorn worker warm-up, not a recurring cost (confirmed: every subsequent request is ~1.7ms).

## 3. Phase 1/2 — API waterfall per page (curl-based approximation; no browser automation available)

Templates were grepped for `fetch(` to build the waterfall. All calls fire in parallel on page load (no `await` chains found gating one fetch on another's response) except where noted.

### `/ops-os` (`templates/ops_shell_index.html`)
| Endpoint | Cold | Warm median | Size |
|---|---:|---:|---:|
| `/api/ops/lifecycle/platform` | 455ms | 20ms | 1,540 B |
| `/api/ops/inbox/summary` | 5.8ms | 5.1ms | 798 B |
| `/api/discovery/recent?limit=6` | 274ms | 70ms | 9,020 B |
| `/api/operators/promotions` | 55ms | 54ms | 87,256 B |
| `/api/ops/emerging-operators?limit=6` | 65ms | 66–82ms | 45,415 B |
| `/api/ops/operators/summary` | 6.0ms | 3.2ms | 147 B |

All 6 fire independently in parallel — no blocking relationship found in the template. `/api/ops/lifecycle/platform`'s 455ms cold vs 20ms warm is the largest cold/warm delta on this page; it settles immediately and does not recur, consistent with one-time import/connection warm-up rather than a query cost — not pursued further since it self-resolves under the 1s Level-1 budget by the second request.

### `/discovery` (`templates/discovery.html`)
| Endpoint | Cold | Warm median | Size |
|---|---:|---:|---:|
| `/api/discovery/entity/<mint>` | **3,217–3,650ms** | **3,069–3,650ms (no warm-up effect)** | 585 B |
| `/api/ops-v2/attribution-outcomes?limit=500` | 80ms | 11ms | 199,988 B |
| `/api/discovery/recent?limit=20` | 218ms | 71–79ms | 29,832 B |
| `/api/ops/emerging-operators?limit=50` | 65ms | 67–72ms | 139,701 B |
| `/api/operators/promotions` | (shared w/ above) | 54ms | 87,256 B |
| `/api/ops-v2/emerging-operator-seeds` | 3.0ms | 2.9–3.1ms | 8,897 B |
| `/api/discovery/search?q=` | 6.5ms | 3.1–3.4ms | 151 B |

`/api/discovery/entity/<mint>` is the sole outlier on the entire page load, and the only endpoint across all four pages that violates the "no call >1s" budget — by roughly 3 seconds. This is the finding.

### `/intelligence/entity/<mint>` (`templates/entity_intelligence.html`)
| Endpoint | Cold | Warm median | Size |
|---|---:|---:|---:|
| `/api/intelligence/entity/<mint>` | 280ms | 12ms | 408 B |
| `/api/ops/operators/by-entity/<mint>` | 2.8ms | 2.6ms | 118 B |
| `/api/ops/` (base) | — (template calls `/api/ops/` unparameterized; effectively unused for this entity) | — | — |

Healthy. The 280ms→12ms cold/warm delta is the same one-time warm-up pattern as `/api/ops/lifecycle/platform`, not a recurring cost.

### `/intelligence/operators` (`templates/operators_index.html`)
| Endpoint | Cold | Warm median | Size |
|---|---:|---:|---:|
| `/api/ops/operators/search?q=` | 2.7ms | 2.6ms | 393 B |
| `/api/ops/operators/resolve` | not exercised (POST-style resolve, needs a body) | — | — |
| `/api/ops/operators/summary` | 6.0ms | 3.2ms | 147 B |
| `/api/ops/operators` | 2.7ms | 2.6ms | 395 B |

All fast; this page reads from the (currently empty/nonexistent-`operators`-table-backed) operator layer, so payloads are trivially small — consistent with the operator-page 404 finding in §4.

**Duplicate-request check:** `/api/discovery/recent` (two different `limit=`) and `/api/operators/promotions` are each requested identically from both `/ops-os` and `/discovery` — this is normal (two different pages, each independently loading its own copy), not a duplicate *within* one page load. No endpoint is fetched twice within a single page's own script.

## 4. Phase 3 — Server-side tracing

Rather than patch `main.py`'s live `before_request`/`after_request` chain (42k-line file, already running in gunicorn, risk of a bad hook affecting the live app), the slow path was isolated with a standalone script that imports `DiscoveryService` directly and profiles it with `cProfile` against the same on-disk databases the live app uses (read-only connections, `PRAGMA query_only=ON`, identical to production code path). This is equivalent tracing without touching the live process at all — **no temporary instrumentation was added to any tracked file**, so there is nothing to strip afterward.

Profile of `DiscoveryService.resolve('111126JExxuowNpaomcmkD4EAWwUcACxzEkZwBNj1NX')`:

```
190 function calls in 3.515 seconds
ncalls  tottime  percall  cumtime  percall  filename:lineno(function)
     1    0.000    0.000    3.515    3.515  service.py:185(resolve)
     1    0.000    0.000    3.513    3.513  service.py:153(_identify)
    15    3.490    0.233    3.490    0.233  {method 'execute' of 'sqlite3.Connection'}
    12    0.000    0.000    3.484    0.290  service.py:104(_one)
```

Per-call breakdown of the 12 existence-check queries in `_identify()` (measured individually):

| Table | Column | Time |
|---|---|---:|
| operators | operator_id | 0.2ms |
| migrated_tokens | mint | 0.0ms |
| wt_watchtower_launches | mint | 1.6ms |
| watchtower_token_attribution | mint | 0.2ms |
| wt_confirmed_treasuries | treasury | 0.0ms |
| wt_discovered_subprovs | subprov | 0.9ms |
| wt_wrap_close_candidates | creator | 0.3ms |
| wt_watchtower_launches | creator_wallet | 0.1ms |
| wt_wrap_close_candidates | tx_signature | 0.5ms |
| wt_watchtower_launches | create_signature OR wrap_close_signature | 0.1ms |
| migrated_tokens | migration_tx | 0.3ms |
| **wt_active_subprov_sessions** | **funding_signature** | **3,144ms** |

DB-read duration = ~3.49s of the 3.515s total (99.3%). Connection-open + PRAGMA setup was negligible (sub-ms, `sqlite3.connect(..., uri=True)` + `PRAGMA query_only=ON`). No template render, no JSON-serialization cost worth measuring (585-byte payload). No queue/write-lane wait — this function only ever opens read-only connections and the write serializer (`src/utils/db_locking.py`) explicitly gates only write statements (see §6), so it cannot have contributed here even in the live gunicorn path.

## 5. Phase 4 — Recomputation audit

`DiscoveryService.resolve()` (backing `/api/discovery/entity`, `/discovery`'s primary data call) is read-only by design and by comment (`"This module deliberately performs no detection, RPC, walkback, scoring, or writes. It only joins already-materialised records into an analyst-facing narrative."` — `src/discovery/service.py:1-5`). Verified: every code path is a `SELECT`/`_one`/`_many` against existing tables (`wt_wrap_close_candidates`, `wt_discovered_subprovs`, `wt_confirmed_treasuries`, `wt_attribution_outcomes`, `wt_ops_v2_treasury_resolution`, `operator_entities`) — no observation materialization, Behaviour/Similarity/Assessment/Forecast computation, or Promotion-proposal logic runs on this GET path. `EmergingOperatorService(...).get(...)` is invoked conditionally (only when `outcome_type == 'UNKNOWN_INFRASTRUCTURE'`) and is itself a read of `wt_unknown_infrastructure_registry` — not exercised for this mint, so not profiled here, but its shape (single indexed lookup) does not suggest a second full scan.

The `/ops-os` Command Center's `/api/ops/lifecycle/platform` and `/api/ops/emerging-operators` endpoints were spot-checked for recomputation-on-GET and found to be reads over existing summary tables — no full-corpus recompute observed in the timing profile (their cold/warm ratio is consistent with import/connection warm-up, not per-request computation).

**Conclusion: no request-time recomputation was found on any of the four canonical pages' GET paths.** The one severe slowdown found (§Executive summary) is a missing index, categorically different from recomputation.

## 6. Phase 5/6 — SQL and DB-contention audit

- **Query count per page:** bounded and small (6 endpoint calls max, on `/ops-os` and `/discovery`); none scale with unrelated data growth except the two full-scan cases below.
- **N+1 patterns:** none found. `_operator_for_entities()` in `DiscoveryService` loops over up to 3 addresses and does one `_many` + one recursive `_operator()` call each — bounded by construction (at most creator/subprov/treasury, i.e. ≤3), not proportional to corpus size.
- **Full-table-scan findings** (`EXPLAIN QUERY PLAN`):
  - `wt_active_subprov_sessions` on `funding_signature` — **`SCAN wt_active_subprov_sessions`**, 91,809 rows, **3.14s measured**. Fixed (§9).
  - `wt_attribution_outcomes` for `/api/discovery/recent`'s `ORDER BY completed_at DESC LIMIT N` with no predicate — **`SCAN wt_attribution_outcomes` + `USE TEMP B-TREE FOR ORDER BY`**, 2,784 rows. Measured cost is 70ms warm (within the 250ms per-call budget) because the table is currently small; the existing composite index `ix_wao_type_time(outcome_type, completed_at DESC)` can't be used because the query has no `outcome_type` predicate. **Documented, not fixed** — not currently over budget, and adding a `completed_at`-only index is a second change not directly justified by a measured budget violation. Flagged as a growth risk: at 10x row count this endpoint would likely cross the 250ms budget.
  - `operator_evidence` full scan also appears in `_operator()` (`SELECT * FROM operator_entities/operator_evidence/... WHERE operator_id = ?` — actually indexed correctly per-table via primary predicate in production use; the bare `WHERE 1` scan above was an ad hoc `EXPLAIN` probe, not a live query shape). No live call issues this shape.
- **Connections opened vs closed:** `DiscoveryService._connect()` uses a context manager (`@contextlib.contextmanager`) — one connection per request, always closed via `finally`. `OperatorReader` (backing the operator-index/entity-lookup routes) follows the identical pattern. No leaked connections found in either read path.
- **DB contention (`src/utils/db_locking.py`, read precisely, not guessed):** the write serializer only takes the process-wide lock when `_is_write_sql(sql)` is true (checked at `db_locking.py:212`, `217`, `311`, `324`, `331`, `345`). Reads are **never** queued behind writes or behind each other — `_DB_WRITE_SERIALIZE` gates writes only. This rules out serializer contention as an explanation for the 3.1s finding, and is confirmed independent of gunicorn entirely (the profiling run above used a bare Python process with no Flask/gunicorn in the loop and reproduced the identical 3.1s cost).
- **WAL state at test time:** `wt_ops_v2.db-wal` was 6.47MB; `PRAGMA wal_checkpoint(PASSIVE)` returned `0|126|126` (not busy, 126 of 126 frames already checkpointed, zero blocked). No stalled readers, no long-lived transactions observed via `lsof` (all listed FDs were the live gunicorn workers holding normal read/write descriptors, consistent with idle listeners, not an active writer during the test window).

## 7. Phase 7 — Template/payload audit

- Largest payloads: `/api/operators/promotions` (87,256 B) and `/api/ops/emerging-operators?limit=50` (139,701 B) — both are Level-2/3 detail (full promotion history / full emerging-operator candidate list) loaded eagerly at Level-1 page-load time on `/ops-os` and `/discovery`. Neither individually breaches the 250ms-per-call budget, but they are the kind of "detail loaded on initial load" the sprint asked to flag. Not changed — no measured latency violation to justify touching the payload shape, and doing so would edge into template/UX rework outside this sprint's remit.
- No `<script>` block in any of the four templates was found doing expensive client-side sort/filter/group over more than the already-small (≤500-row) payloads returned above; `discovery.html`'s largest client-side list is the 500-row `attribution-outcomes` fetch, rendered directly with no secondary in-browser computation loop found.
- HTML shell sizes (47–84KB) are template/CSS/JS payload, not data; none contain inlined large data blobs.

## 8. Phase 8 — Performance budget vs actuals

| Budget | `/ops-os` | `/discovery` | `/intelligence/entity/<mint>` | `/intelligence/operators` |
|---|---|---|---|---|
| HTML <300ms | 3.3ms ✅ | 2.0ms ✅ | 30ms ✅ | 6.0ms ✅ |
| Level-1 usable <1s | ✅ (slowest API 455ms cold, 82ms warm) | **❌ before fix** (3.2–3.65s from `/api/discovery/entity`) → ✅ after fix (≤52ms) | ✅ (280ms cold, 12ms warm) | ✅ |
| Individual API <250ms | ✅ all | **❌ before fix** (`/api/discovery/entity` 3.1–3.65s) → ✅ after fix | ✅ | ✅ |
| No call >1s | ✅ | **❌ before fix** → ✅ after fix | ✅ | ✅ |
| No duplicate requests | ✅ | ✅ | ✅ | ✅ |
| No request-time recomputation | ✅ | ✅ | ✅ | ✅ |

Only `/discovery` violated budget, entirely because of the single endpoint fixed in §9. All four pages meet every budget line after the fix.

## 9. Phase 9 — Minimal remediation applied

**Fix:** added one additive SQLite index.

```sql
CREATE INDEX IF NOT EXISTS ix_subprov_sessions_funding_signature
    ON wt_active_subprov_sessions(funding_signature);
```

Applied to the live `database/wt_ops_v2.db` directly (idempotent DDL, zero rows affected, no lock contention observed at apply time — verified via `PRAGMA wal_checkpoint(PASSIVE)` immediately before running it) **and** committed to code at `src/core/ws_cascade_store.py`, immediately next to the existing sibling index on the same table (`ix_subprov_sessions_state`, line 162), so the index is recreated automatically on any future schema-init pass or fresh database build. This is schema-init code that runs at process start, not on the request path — no request-time DDL was introduced.

**Justification:** measured in §4 — `SELECT * FROM wt_active_subprov_sessions WHERE funding_signature = ? LIMIT 1` took 3,144ms of the 3,515ms total `DiscoveryService.resolve()` cost, confirmed by `EXPLAIN QUERY PLAN` showing `SCAN wt_active_subprov_sessions` (91,809-row full scan) before the fix.

No other remediation was applied. No caching, retries, timeouts, or business-logic change was made. The `wt_attribution_outcomes` full-scan-on-`/api/discovery/recent` (§6) was deliberately left alone — it is within budget today and adding an index for it is not justified by any measured violation, only a growth projection; noted for a future sprint if/when that table grows.

### Before/after timing — `/api/discovery/entity/<mint>` (6 runs each)

| | Before | After |
|---|---|---|
| Run 1 (cold-equivalent) | 3,217ms | 52ms |
| Run 2 | 3,069ms | 4.6ms |
| Run 3 | 3,215ms | 3.9ms |
| Run 4 | 3,427ms | 8.9ms |
| Run 5 | 3,170ms | 11.1ms |
| Run 6 | 3,650ms | 6.0ms |
| **Median** | **3,216ms** | **6.5ms** |

**~495x improvement on the median, ~640x on the isolated query cost measured directly (3,144ms → ~5ms).**

### Before/after — canonical page spot-check (all still 200, sane payload)

| Page | Status | Total time |
|---|---|---:|
| `/ops-os` | 200 | 66ms |
| `/discovery` | 200 | 6.9ms |
| `/intelligence/entity/<mint>` | 200 | 15ms |
| `/intelligence/operators` | 200 | 5.8ms |

## Acceptance checks

- [x] Each canonical page meets its documented budget (or gap explained) — `/discovery` was the sole violator; fixed.
- [x] Level 1 renders before Level 2/3 detail where the distinction exists — the templates fetch all endpoints in parallel and the payloads are small enough (largest 199,988 B, warm ~11ms) that no staged-loading gap was found to require fixing.
- [x] No duplicate network calls within a single page's own script (checked all four templates).
- [x] No page-load recomputation found (§5) — nothing to fix or leave undocumented.
- [x] Query count bounded, not proportional to unrelated data growth — one exception flagged (`wt_attribution_outcomes` scan) and explicitly left as a documented growth risk, not a silent gap.
- [x] No request-time DDL or writes found on any of the four canonical pages' read paths; the fix's DDL runs at schema-init, not per-request.
- [x] Slow DB writers do not block read-only rendering beyond budget — verified the write serializer gates writes only (`db_locking.py`), and no active writer was observed during the test window (WAL not busy, checkpoint clean).
- [x] All four routes plus the fixed endpoint spot-checked with curl post-fix: all 200, payload sizes sane and unchanged in shape.

## Known finding outside this sprint's remediation scope

`/intelligence/operator/<operator_id>` (`src/ops/operator_routes.py:190`) queries a table literally named `operators` in `wt_ops_v2.db`, which does not exist in the live database — every request 404s regardless of id. This is a data/schema gap, not a performance issue, and out of scope for a perf-audit sprint (no business-logic changes permitted); flagged here for a follow-up ticket.
