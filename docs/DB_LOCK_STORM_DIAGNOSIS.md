# DB Lock Storm — RESOLVED (root cause: VACUUM)

## ✅ ROOT CAUSE FOUND & FIXED (2026-06-09, second session)
The catastrophic lock storm was **`VACUUM`** in two periodic jobs:
- `pumpfun_curve_listener.py` `_db_maintenance_periodic` (~line 3624) — ran `conn.execute("VACUUM")`
- `storage_cleanup.py` (~line 173) — same
`VACUUM` rewrites the ENTIRE database into the WAL (caught a **8.9 GB WAL spike** live) and
holds an **exclusive lock** for its whole multi-minute duration → every other writer gets
"database is locked". It re-triggered on **every startup**, which is why restarts kept
re-igniting the storm.
**Fix:** removed both VACUUMs (DELETEs already free pages for reuse). Plus tightened WAL
config in `db_locking.py`: journal_size_limit 256MB→32MB, autocheckpoint 1000→400, watchdog
200MB→32MB/30s/TRUNCATE, abandoned-txn reap 120s→45s.
**Result:** WAL 8.9GB → 37KB (oscillates healthily <8MB); webhook hits recording again
(115/hour, real Cgwr treasury hits landing). Auto-arm pipeline can now fire.

**Residual (minor, non-blocking):** normal multi-writer SQLite contention (API + listener +
helius_monitor on one file) causes occasional write collisions that retry. High-frequency
`RPC_METRICS` writes are the main contention source — could be batched/deferred later, but
non-critical (important writes get through via retry).

---
# (Original diagnosis below — kept for reference)
# DB Lock Storm — Diagnosis & Remaining Work

_Written 2026-06-09 after an extended live investigation. The storm intermittently
blocks ALL writes to `flex_complete_database.db` (webhook hits, arm loop, heartbeats),
bloating the WAL to 137–230 MB. This is the recurring `listener-conn-leak-wal-hang`
pattern. One leak was fixed; two causes remain. Diagnose the rest when the system is calm
— NOT with live restart-roulette, which re-triggers the storm each time._

## Symptoms
- `wt_webhook_hits` stops recording (POST → 200 but row never lands).
- `PRAGMA wal_checkpoint(TRUNCATE)` returns `1|N|N` (blocked — an active reader pins the WAL).
- `flex_complete_database.db-wal` grows to 137–230 MB and freezes at a fixed size.
- `[DB_LOCK_ERROR]` floods `logs/supervisor/api_err.log`; `watch-pipeline` heartbeat = error.
- A process accumulates dozens-to-100 open handles on the DB (`lsof` on the `-wal` file).

## FIXED this session
**Connection leak in the funding-queue processor.**
`src/core/pumpfun_curve_listener.py` — `_process_creator_funding_queue_periodic` (~line 4334).
Opened `conn = db_connect(...)` inside `async with self.db_lock` in a `while True` loop,
closing only on the happy path (line ~4446). Any query exception skipped the close →
**leaked one connection per failed iteration**. Under lock contention this leaked fast
(observed listener holding 100 handles).
**Fix applied:** `conn = None` before the `async with`; added `finally: conn.close()` to the
loop's outer `except` (~line 4715). Verified: listener handles stopped climbing 100 →
stabilised ~18–40.

## REMAINING cause #1 — a long-lived read transaction pins the WAL
Stopping `operation_scheduler` + `watchtower_helius_monitor` together dropped the WAL from
**137 MB → 440 KB** and let the checkpoint succeed. Restarting the **scheduler** alone
re-pinned it (smaller now, ~4 MB, checkpoint still blocked `1|84|84`). So one of those two
holds a read transaction open across its whole cycle instead of per-query, pinning old WAL
frames so `TRUNCATE` can't reclaim them.
**Where to look:**
- `src/core/operation_scheduler.py` — does it hold a connection open across the full
  intake/forward cycle? Should open per-query (or use `with`) and not straddle the cadence.
- `src/monitoring/helius_cli_monitor.py` — same check.
- General: any `db_connect` that lives for the duration of a long loop is a WAL pin even if
  it never writes. Audit for `conn` opened outside the per-iteration scope.

## REMAINING cause #2 — webhook hits enqueue but never drain (separate from the WAL)
Even with the WAL healthy (440 KB) and other writes working, `wt_webhook_hits` still records
0 for a posted template hit. This is the **infra-processor queue** problem first seen earlier:
`/api/webhook/watchtower` enqueues into `_wt_infra_queue` and returns 200, but the
`wt-infra-processor` thread (`_start_wt_infra_processor`, `src/core/main.py` ~33174) isn't
draining it — its heartbeat (`wt_worker_heartbeat` component `wt-infra-processor`) was absent
after lock-storm restarts, suggesting the thread died or never started under gunicorn.
**Where to look:**
- Does `wt-infra-processor` appear in `wt_worker_heartbeat` after a clean boot? If absent,
  the thread isn't running (it starts inside the `[STARTUP] Watchtower tables verified`
  block at `main.py:859`, which itself was failing on `database is locked` during the storm).
- Gunicorn runs `workers=1, threads=8, preload_app=False`. Confirm the thread starts in the
  single worker and survives. The dynamic-role recognition fix (treasuries → TREASURY) is
  already in place and proven (`dynamic_roles=2 [yUpm, Cgwr]` at runtime) — the gap is purely
  the drain, not recognition.

## What IS working (don't re-break)
- Dynamic webhook recognition of enrolled wt_ops_v2 treasuries (`_load_dynamic_infra_roles`,
  `main.py`). yUpm + Cgwr enrolled on Helius webhook `106e20f6` (19 addresses).
- Forward-monitor RPC fix (signature-gate + treasury/collector-only targeting): ~2.3M →
  ~70-100k credits/day, detection intact.
- Auto-arm loop (`operation_armed.py`) + real-time creator resolver (`_resolve_and_arm_creator`,
  `main.py`). Resolver chain-follow verified in isolation (relay `EgB7X3NY` → creator
  `8UQ35j29`). Unverified end-to-end ONLY because webhook writes are blocked (cause #2).

## Recommended order when calm
1. **Cause #2 first** (webhook drain) — it's the blocker for pre-launch discovery and is
   independent of the WAL. Boot clean, confirm `wt-infra-processor` heartbeats, post a test
   template hit, confirm it lands + the arm fires.
2. **Cause #1** (scheduler WAL pin) — audit for the long-lived read connection; make it
   per-query / `with`-scoped. This keeps the WAL bounded permanently.
3. Re-run the end-to-end arm test (treasury template hit → resolve → arm → countdown →
   disarm-on-migration).

## Operational note
Do not do rapid API/listener restarts to "fix" the storm — each full restart runs the
CLUSTERING rebuild (rewrites super_clusters for ~2,700 creators = a 137 MB WAL spike) which,
combined with any pin, re-triggers the storm. Stabilise by pausing writers, checkpoint once
with no holders, then start services spaced out.
