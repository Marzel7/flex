# X20.7 Startup Write Hygiene & Boot Audit

Date: 2026-07-14

## Problem Summary

The API was able to bind its port but could fail to become operational while the live listener was processing events. The immediate failure mode was avoidable startup database work competing for the write lane. In one path, startup schema creation also re-entered the managed write path and raised `NestedDatabaseWriteError`.

The architectural issue is broader than the webhook path: startup must distinguish verification from mutation. A healthy deployed database should be inspected with read-only connections. The write lane should only be acquired when state genuinely changes.

## Startup Dependency Graph

```text
Application start
  |
  v
Configuration/imports
  - DB_PATH, Flask config, route module imports
  - schema migration import is present, but import-time migration execution is disabled
  |
  v
Flask app creation
  - template/static configuration
  - request hooks declared
  |
  v
Blueprint and route registration
  - internal Flask routes
  - webhook routes
      * read-only sqlite_master check
      * optional DWS bootstrap only if webhook tables/indexes are missing
  - RPC savings/efficiency routes
      * read-only query connections
  - ops, intelligence, discovery, lifecycle, inbox, write telemetry routes
      * inbox registration no longer refreshes state during startup
  - operator schema block
      * read-only schema/alignment checks first
      * optional bootstrap only for missing operator objects
  - price API
      * read-only metadata_cache check
      * optional DWS bootstrap only if missing
      * no startup WAL PRAGMA
  |
  v
First request hooks
  - recovery guard
  - WATCHTOWER sentinel verification is read-only
  - optional Flask background workers only when FLEX_ENABLE_FLASK_BACKGROUND_WORKERS=1
  |
  v
API ready
  - HTTP route responds without waiting on avoidable DDL
  - listener can continue owning/using the write lane
```

Current startup write-lane acquisition points after this audit:

- `webhook-init/ensure-webhook-tables`: only when webhook schema objects are missing.
- `price-api/ensure-metadata-cache-table`: only when `metadata_cache` is missing.
- Operator schema/alignment/observation/outcome/walkback bootstrap: only when read-only checks find missing or unhealthy canonical objects.
- RPC metrics schema fallback: only when read-only schema inspection says the metrics schema is incomplete.

## Startup Write Classification

| Path | Previous behavior | Current classification | Current behavior |
| --- | --- | --- | --- |
| `ensure_webhook_tables()` | Opened a tracked connection and ran `CREATE TABLE/INDEX IF NOT EXISTS` during route registration | A normally, B only on first install | Reads `sqlite_master` first; submits DDL through `DatabaseWriteService` only if missing |
| `DatabaseWriteService` callback connector | Could capture monkey-patched `sqlite3.connect`, causing managed writes inside managed writes | B infrastructure fix | Uses the native sqlite connector for DWS callbacks, avoiding nested tracked writes |
| `register_price_api()` | Configured WAL and ensured `metadata_cache` during registration | A normally, B only on first install | Removes startup WAL PRAGMA; read-only table check before DWS bootstrap |
| `rpc_metrics_recorder._ensure_rpc_metrics_table()` | Ran DDL-style ensure when constructed | A normally, B if metrics DB is incomplete | Read-only schema/table/index/column inspection first |
| RPC dashboard APIs | Used default sqlite connections for read endpoints | A | Read-only connections for dashboard queries |
| `register_inbox_routes()` | Constructed store and refreshed inbox records during registration | C | Registration only; refresh remains explicit runtime endpoint |
| `/healthz` database probe | Used write-capable managed connections for health reads | A | Uses read-only managed connections |
| `check_networks_release_capability()` | Used write-capable connection for table existence | A | Uses read-only schema helper |
| WATCHTOWER first-request startup | Previously capable of table creation and tier seeding | A | Verifies sentinel read-only; bootstrap is external script-owned |
| Operator startup block | Could call schema initializers during startup | A normally, B when missing | Read-only checks guard schema/alignment bootstraps |

Category A: pure verification, should be read-only.

Category B: rare bootstrap, allowed only when objects are genuinely missing and idempotent.

Category C: runtime initialization, must not run during API startup.

## Nested Startup Writes

Removed occurrences:

| Path | Nested-write shape | Resolution |
| --- | --- | --- |
| `src/core/webhook_handler.py::ensure_webhook_tables` | Startup opened a tracked DB connection and then DDL attempted to acquire the managed write lane | Split into read-only inspection plus DWS-only bootstrap |
| `src/core/database_write_service.py` | DWS callback used patched sqlite connector, so DWS-managed DDL re-entered tracked write locking | DWS now captures the native sqlite connector |
| `src/apis/price_api.py::register_price_api` | Route registration ran WAL/DDL through startup import path and could block on the write lane | Removed WAL setup; metadata table creation is read-only-first and DWS-only when missing |
| `src/ops/inbox_routes.py::register_inbox_routes` | Startup registration triggered inbox refresh/runtime store work | Removed startup refresh; explicit refresh endpoint remains |
| `src/core/main.py::healthz` | Readiness probe used write-capable managed connections | Converted to read-only managed connections |

No temporary diagnostic write-lane tracing remains in source.

## Timing Comparison

Before the startup hygiene changes:

| Measurement | Result |
| --- | --- |
| Supervisor state | API initially `STOPPED`; later `RUNNING` but not practically ready |
| `/healthz` after partial restart | Timed out after `8.007s` |
| `/` after partial restart | Timed out after `8.007s` |
| Later `/healthz` after partial fixes | Timed out after `10.005s` |
| Sampled worker state | Blocked in `flock` while listener held/used the write lane |
| Listener | Running and processing events |

After the startup hygiene changes:

| Measurement | Result |
| --- | --- |
| Supervisor state | `watchtower_api RUNNING`, pid `24833`; `watchtower_listener RUNNING`, pid `6535` |
| `/healthz` first response | `HTTP 503 total 0.026949s` |
| `/` first response | `HTTP 302 total 0.003254s` |
| Listener readiness | Listener remained running throughout, with uptime over 12 hours |

The `/healthz` status is still `503` because existing worker heartbeat semantics report stale workers. The important startup-write result is that the request returns promptly and the DB probe is read-only. Changing readiness semantics is a separate health-contract decision.

## Verification

Static compilation passed for the startup-touched modules:

```text
python -m py_compile \
  src/apis/price_api.py \
  src/utils/db_locking.py \
  src/core/database_write_service.py \
  src/core/webhook_handler.py \
  src/core/main.py

python -m py_compile \
  src/metrics/rpc_metrics_recorder.py \
  src/apis/rpc_savings_api.py \
  src/apis/rpc_efficiency_api.py \
  src/ops/inbox_routes.py
```

Runtime verification:

```text
curl http://127.0.0.1:5002/healthz
HTTP 503 total 0.026949s

curl http://127.0.0.1:5002/
HTTP 302 total 0.003254s
```

## Residual Risks

- `/healthz` still returns `503` when worker heartbeat rows are stale. That is not a startup write-lane block, but it may matter if external readiness checks require `200`.
- RPC metrics fallback still uses direct sqlite DDL after the read-only check. The normal deployed path is read-only, but the rare bootstrap path should be moved behind `DatabaseWriteService` if that DB can be hot.
- Historical `api_err.log` entries include `op_state` read-only backfill failures. They did not block the final API readiness check, but they should be audited separately because they suggest a read path may still attempt opportunistic mutation.
- The worktree contained broad pre-existing `main.py` and supervisor diffs before this pass. This audit only relies on the startup-write hygiene changes listed above.

## Outcome

The startup path now follows the intended rule: verify with read-only connections first, acquire the write lane only for genuine missing-state bootstrap, and keep runtime refresh work out of API registration. The API reaches HTTP responsiveness while the listener remains active.
