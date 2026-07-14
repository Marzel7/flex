# Watchtower Migration/Birth Ingest Incident - 2026-06-19

## Summary

Migrations stopped being persisted after `2026-06-18 12:09:52 UTC`. The original visible symptom was "no migrations", but the root problem evolved through several layers:

1. The listener had been OOM-killed repeatedly under macOS memory pressure.
2. After memory was freed, the listener still could not reliably connect because synchronous SQLite startup/background work blocked the asyncio event loop.
3. After recovery-mode parking, the dedicated PumpPortal migration websocket connected and received a live migration.
4. The live migration was not persisted because SQLite writes were failing with `database is locked`.
5. PumpPortal birth/trade ingestion was parked to reduce lock pressure, but the expected API/webhook birth path is currently not feeding births into the DB.

## Current State

As of the latest checks:

- Dedicated PumpPortal migration websocket: ON
- PumpSwap websocket: ON
- Listener PumpPortal birth/trade socket: OFF, intentionally parked
- Webhook/API birth queue drainer: ON
- Webhook birth queue contents: empty
- New births saved via API/webhook: not currently confirmed; DB shows no new birth rows after 2026-06-18
- Latest persisted migration in `token_analysis`: `2026-06-18 12:09:52 UTC`
- One live migration was received after recovery but not persisted:
  - Mint prefix: `7sjgj9278rm2aJVZ...`
  - Signature prefix: `4xu538MZi8Yc1Mjv...`

## Evidence

Listener recovery worked:

```text
[PUMPPORTAL_MIG] ✓ Connected — subscribeMigration (dedicated)
[WEBSOCKET][PUMPSWAP] ✓ Connected via Helius
```

Live migration was received:

```text
[PUMPPORTAL_MIG] 🚀 Migration: 7sjgj9278rm2aJVZ… sig=4xu538MZi8Yc1Mjv… (#1)
```

But DB persistence did not confirm it:

```text
matches by migration_tx prefix:
```

Birth queue is empty:

```text
webhook_birth_queue:
{'total': 0, 'pending': None, 'max_id': None}
```

Recent `token_analysis` birth/migration-like rows are stale, from 2026-06-18.

## Important Code Changes Made

### `run_listener.sh`

Recovery-mode flags were added to prioritize live migration capture:

```bash
export CREATOR_BACKFILL_ENABLED=0
export LISTENER_PRICE_WORKER_ENABLED=0
export LISTENER_CREATOR_ACTIVITY_ENABLED=0
export LISTENER_LIVE_PRICE_UPDATER_ENABLED=0
export LISTENER_CREATOR_FUNDING_QUEUE_ENABLED=0
export LISTENER_DB_MAINTENANCE_ENABLED=0
export LISTENER_MIGRATION_RECONCILER_ENABLED=0
export LISTENER_PORTAL_VSOL_FLUSH_ENABLED=0
export LISTENER_DB_STARTUP_MAINTENANCE_ENABLED=0
export LISTENER_BONDING_INDEX_FULL_HYDRATE_ENABLED=0
export LISTENER_PUMPPORTAL_BIRTHS_ENABLED=0
```

### `src/core/pumpfun_curve_listener.py`

Added/kept:

- Dedicated migration-only PumpPortal websocket.
- Guarded startup DB maintenance/full bonding hydrate.
- Guarded price worker, creator activity worker, creator funding queue, DB maintenance, migration reconciler, and portal vSOL flush.
- Guarded PumpPortal birth/trade websocket behind `LISTENER_PUMPPORTAL_BIRTHS_ENABLED`.
- Migration critical writes now use retrying write helpers:
  - `_create_minimal_token_entry`
  - `_mark_token_migrated_in_db`
  - migration TX store update

### `src/utils/db_write_retry.py`

Fixed connection cleanup in `async_write_with_retry()`:

- On `sqlite3.OperationalError`, rollback/close the connection before sleeping/retrying.
- This prevents lock-error paths from leaving abandoned connections open.

## What Was Ruled Out

- PumpPortal itself was reachable: standalone `subscribeMigration` connected and received the subscription confirmation quickly.
- Helius key was being used correctly by existing config/logs.
- The listener process can now survive startup once memory pressure and startup DB scans are controlled.

## Remaining Problems

### 1. SQLite write contention is still a system-level risk

Other processes still hold the DB open, especially:

- listener
- gunicorn/API process
- Helius monitor
- rpc metrics/API processes
- other supervised workers

Telemetry writes such as `rpc_metrics` are still capable of producing:

```text
[RPC_METRICS] batch write failed (... rows): database is locked
```

This is less critical than migration persistence, but it proves the database can still be contended.

### 2. Birth ingest path is currently unclear/broken

With PumpPortal birth/trade socket parked, births are expected to arrive via API/webhook.

Observed:

- `drain_webhook_birth_queue` is running.
- `webhook_birth_queue` is empty.
- No new `token_analysis` birth rows are visible.

Conclusion: births are probably not being saved right now unless another path exists outside the checked queue/table.

### 3. Previously received live migration should be reconciled

The received migration `4xu538MZi8Yc1Mjv...` did not appear in `token_analysis`. It should be manually reconciled or replayed once DB writes are healthy.

## Recommended Next Fixes

1. Fix/verify API webhook birth ingest.
   - Confirm Helius/PumpPortal webhook delivery target.
   - Confirm Flask/API route receives birth payloads.
   - Confirm route inserts into `webhook_birth_queue` or directly into `token_analysis`.

2. Keep PumpPortal birth/trade socket parked until API births are confirmed or implement a low-write birth path.

3. Move high-volume telemetry off the live trading DB.
   - Best: separate SQLite DB for `rpc_metrics`/telemetry.
   - Acceptable recovery option: drop telemetry writes on lock instead of retrying against the live DB.

4. Re-enable background/enrichment jobs one at a time only after:
   - dedicated migration websocket stays connected,
   - new migrations persist successfully,
   - birth ingest is verified.

5. Add a health check that separately reports:
   - PumpPortal migration websocket connected,
   - last migration websocket message timestamp,
   - last persisted migration timestamp,
   - webhook birth queue last insert timestamp,
   - latest token birth row timestamp,
   - SQLite lock errors in last 5 minutes.

## Useful Verification Commands

Check migration websocket/log events:

```bash
grep -n "PUMPPORTAL_MIG\\|MIGRATION DETECTED\\|database is locked" logs/supervisor/listener.log | tail -120
```

Check latest persisted migrations:

```bash
python - <<'PY'
import sqlite3
conn=sqlite3.connect('database/flex_complete_database.db', timeout=5)
cur=conn.cursor()
for row in cur.execute("""
select mint, migration_tx, datetime(migrated_at,'unixepoch'), migration_source
from token_analysis
where migrated_at is not null or migration_tx is not null
order by coalesce(migrated_at,0) desc
limit 12
"""):
    print(row)
PY
```

Check webhook birth queue:

```bash
python - <<'PY'
import sqlite3
conn=sqlite3.connect('database/flex_complete_database.db', timeout=5)
cur=conn.cursor()
print(cur.execute("""
select count(*) total,
       sum(case when consumed=0 then 1 else 0 end) pending,
       max(id) max_id
from webhook_birth_queue
""").fetchone())
PY
```

Check active DB holders:

```bash
lsof database/flex_complete_database.db database/flex_complete_database.db-wal database/flex_complete_database.db-shm 2>/dev/null
```

## Bottom Line

The listener can now connect and receive migrations again, but persistence and births are not fully healthy.

The next critical task is not another websocket change. It is to stabilize SQLite writes and verify the API/webhook birth ingest path.
