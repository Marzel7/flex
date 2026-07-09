# FLEX Architecture Problems — Current State (2026-06-19)

## 1. SQLite Write Contention (The Root Cause of Most Problems)

**What's happening:** `avg_wait_ms = 27,456ms` (27 seconds average wait per write). p95 = 55s. Queue depth hitting 15. All processes compete for a single write lane on `flex_complete_database.db` (9.7GB).

**Observed symptoms across all processes:**
- `listener.log`: `[DB_RETRY] Database locked (attempt 1/6)`, `[RPC_METRICS] batch write failed: database is locked`, `[WAL_CHECKPOINT] Some frames blocked by active readers`
- `api.log`: `[WORKER] Error (will retry): database is locked`, `[CREATOR_RESOLUTION_QUEUE] enqueue failed: database is locked` — happening continuously
- `ws_cascade.log`: `event write failed TREASURY_WEBSOCKET_OPENED: database is locked` — telemetry writes failing on every subscription event

**Root cause:** Multiple OS processes (API/gunicorn, listener, ws_cascade, operation_scheduler) all write to the same SQLite file. SQLite WAL allows one writer at a time. The write serializer (`TrackedConnection`) works within a single process but cannot coordinate across the 5 separate supervisor processes. Each process has its own queue; contention is OS-level.

**Impact:** Writes that should take <1ms are waiting 27–55 seconds. Any path that does a synchronous DB write on the critical event loop (births, migrations, WS subscription telemetry) blocks real-time capture.

---

## 2. WAL Checkpoint Blocked — Persistent Reader Holdback

**What's happening:** `[WAL_CHECKPOINT] RESTART: busy=1 log=N ckpt=N` — the WAL checkpoint restarts repeatedly because at least one long-lived reader is holding a read transaction open, preventing frames from being checkpointed. Previously grew to 8GB WAL in an earlier incident.

**Root cause:** Long-running background loops (CREATOR page walker, reconciler, index hydration) open read transactions and hold them for seconds to minutes while iterating. The WAL can't checkpoint past the oldest open reader.

**Impact:** WAL grows continuously → DB file effectively grows → slower reads → more lock contention → slower reads. A vicious cycle. Eventually causes the full lock storm previously experienced.

---

## 3. Hot DB Is Too Large (9.7GB) for SQLite on a Single Machine

**What's happening:** `flex_complete_database.db` is 9.7GB. Two 8.1GB backups sit alongside it consuming an additional ~16GB. `flex_investigation_archive.db` is 2.7GB.

**Root cause:** The biggest table (`funder_networks`, 2.64GB) was archived to cold DB but the `DELETE + VACUUM` to reclaim that space was never run — it's gated behind a maintenance window script (`reclaim_funder_networks_space.py`) that hasn't been executed.

**Impact:**
- Every query does more I/O
- WAL checkpoints are slower (more pages to flush)
- macOS page cache pressure: competing with the DB file for RAM, causing more OOM risk
- SQLite is not designed for multi-process concurrent writes at this file size

---

## 4. PumpPortal WebSocket Instability (103 Reconnects Logged)

**What's happening:** 103 `[PUMPPORTAL_MIG] ⚠ WS closed` entries in the listener log. The dedicated migration WS hits a pattern of 4 fast `ConnectionClosedError (1006)` then escalates to `TimeoutError` with exponential backoff up to 60s.

**Root cause:** PumpPortal's `wss://pumpportal.fun/api/data` drops connections under load or when the listener process is under memory/CPU stress (the OOM events caused rapid reconnect storms that PumpPortal may rate-limit or drop). The `1006` code = abnormal closure without a close frame — typically a TCP reset or server-side drop.

**Impact:** During backoff windows (up to 60s), all PumpPortal events (births AND migrations) are missed. Helius `pumpswap_logs` WS catches most migrations, but births have no fallback.

---

## 5. Creator Backfill Loop Consuming RPC Budget

**What's happening:** `[CREATOR] Page 8: prefilter candidates=761 (checking first 80), skipped_post_migration=0, skipped_errored=239` — the creator backfill is scanning 1,000 candidates per cycle, making `getSignaturesForAddress` calls (10cr each) for tokens that will never resolve.

**Status:** Parked via `CREATOR_BACKFILL_ENABLED=0` in `run_listener.sh`. But the code still executes the page loop even when disabled — it only skips the expensive `get_creator_from_earliest_tx` step. The prefilter scanning itself still runs and makes DB reads.

**Impact:** Background RPC credit drain. Also holds read transactions open on the hot DB during each page scan, contributing to WAL checkpoint blocking (problem #2).

---

## 6. Multi-Process Architecture on Single SQLite — Structural Mismatch

**The real problem underneath 1–5:** The app runs 5 concurrent processes (API, listener, ws_cascade, operation_scheduler, helius_monitor) all sharing one SQLite database. SQLite is designed for single-process use or low-concurrency reads. The write serializer in `db_locking.py` only serializes within one process — it cannot help cross-process.

**Processes and their write patterns:**
| Process | Writes to hot DB | Frequency |
|---|---|---|
| `watchtower_listener` | births, migrations, creator resolution, RPC metrics, WAL checkpoint | Continuous |
| `watchtower_api` (gunicorn) | creator resolution queue, market cap snapshots, worker tasks | Per request |
| `ws_cascade` | WS telemetry events, launch records | Per subscription event |
| `operation_scheduler` | wt_ops_v2.db only (separate DB) | Every 3–15 min |
| `watchtower_helius_monitor` | helius monitor tables | Periodic |

The ops DB (`wt_ops_v2.db`, 584MB) is separate and healthy. Only the hot DB is the problem.

---

## 7. Future Bound Panel — Stale Since April 2026

**What's happening:** `is_about_to_migrate=1` on 445 tokens, all last updated April 2026. The Future Bound panel shows "No near-migration Pump.fun tokens right now."

**Root cause:** The vSol threshold tracking (`LISTENER_PORTAL_VSOL_FLUSH_ENABLED=0` in `run_listener.sh`) is parked. Even when enabled, the flush writes vSol state to the hot DB — adding more write pressure. The PumpPortal `subscribeTokenTrade` subscription for near-migration tokens is inside `listen_pumpportal_websocket`, which IS now enabled but won't produce Future Bound data until the flush is re-enabled.

---

## 8. No Fallback for Birth Events

**What's happening:** Births rely entirely on PumpPortal `subscribeNewToken`. There is no Helius birth webhook registered (only 2 Helius webhooks exist, both for `/api/webhook/watchtower`). The `webhook_birth_queue` has 0 all-time rows.

**Impact:** When PumpPortal WS drops (problem #4), births are completely blind for the duration. During the 23-hour OOM-kill outage, all births were missed with no recovery path.

---

## Summary — Priority Order for Fixes

| # | Problem | Severity | Fix |
|---|---|---|---|
| 1 | SQLite cross-process write contention (27s avg wait) | **Critical** | Migrate to PostgreSQL or isolate writes to one process via IPC queue |
| 2 | WAL checkpoint blocked by long readers | **Critical** | Enforce short read transactions in page walkers; set `busy_timeout` lower |
| 3 | Hot DB 9.7GB, space not reclaimed | **High** | Run `reclaim_funder_networks_space.py --i-am-in-a-maintenance-window` |
| 4 | PumpPortal WS drops (103 reconnects) | **High** | Add Helius birth webhook as fallback; tune reconnect strategy |
| 5 | Creator backfill RPC drain | **Medium** | Actually gate the page loop on `CREATOR_BACKFILL_ENABLED`; add budget cap |
| 6 | Future Bound stale | **Medium** | Re-enable `LISTENER_PORTAL_VSOL_FLUSH_ENABLED=1` once write contention reduced |
| 7 | No birth fallback | **Medium** | Register Helius birth webhook at `/api/webhook/pumpfun-birth` |

## The One Fix That Unblocks Everything

**Problem #1 is the root cause of problems #2, #4 (indirectly), and most application slowness.** The fastest path that doesn't require PostgreSQL: move all writes from `ws_cascade` and `watchtower_api` into the `watchtower_listener` process via a lightweight IPC channel (Unix socket or queue file), making the listener the sole writer. This is the SQLite-native pattern and would eliminate cross-process lock contention entirely.
