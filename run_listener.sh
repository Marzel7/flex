#!/bin/bash
# Run listener with automatic restart on crash/disconnect
cd "$(dirname "$0")"
# PARK the creator-resolution BACKFILL: its get_creator_from_earliest_tx paging grinds
# hundreds of RPC pages on stubborn tokens and starves LIVE migration capture. Parked so
# migrations keep flowing. Set to 1 to re-enable once the live path is confirmed healthy.
export CREATOR_BACKFILL_ENABLED=0
# PARK listener-local price worker bootstrap: it can block startup on DB locks before the
# critical migration websocket is even scheduled. Flask/other workers can still handle UI pricing.
export LISTENER_PRICE_WORKER_ENABLED=0
# PARK nonessential listener background jobs while live websocket capture is being restored.
# These jobs can synchronously wait on SQLite locks and starve the asyncio loop before sockets connect.
export LISTENER_CREATOR_ACTIVITY_ENABLED=0
export LISTENER_LIVE_PRICE_UPDATER_ENABLED=0
# X73.2 — permanently 0, not a temporary park. creator_funding_queue's sole
# canonical consumer is now the standalone src.core.creator_funding_worker
# supervisord process (see config/supervisor/supervisord.conf). Re-enabling
# this would start a second, redundant in-listener consumer racing the
# standalone worker for the same rows. Do not re-enable.
export LISTENER_CREATOR_FUNDING_QUEUE_ENABLED=0
# creator_resolution_queue's sole canonical consumer is the standalone
# src.core.creator_resolution_worker supervisord process — same reasoning.
export LISTENER_CREATOR_RESOLUTION_QUEUE_ENABLED=0
export LISTENER_DB_MAINTENANCE_ENABLED=0
export LISTENER_MIGRATION_RECONCILER_ENABLED=1
export LISTENER_PORTAL_VSOL_FLUSH_ENABLED=0
export LISTENER_DB_STARTUP_MAINTENANCE_ENABLED=0
export LISTENER_BONDING_INDEX_FULL_HYDRATE_ENABLED=0
export LISTENER_RPC_METRICS_DB_ENABLED=0
export LISTENER_PREDICTION_SCORING_ENABLED=0
export CROSS_FUNDING_CLUSTER_ANALYZER_ENABLED=0
export TRADING_SIM_AUTO_BUY_ENABLED=0
export LISTENER_PUMPPORTAL_BIRTHS_ENABLED=1
# Helius logsSubscribe on PUMPFUN_PROGRAM — DO NOT enable: ~96MB/5min bandwidth (full firehose).
export HELIUS_BIRTH_WS_ENABLED=0
# RPC reconciler: getSignaturesForAddress on PUMPFUN_PROGRAM, runs only when PumpPortal is down.
# Cost: ~10cr/sweep for sig list + 10cr per create fetched (at limit=100, ~110cr max/sweep).
# PARKED: sync DB writes inside the reconciler path stall the asyncio loop when PumpPortal is
# down (write-lane acquire blocks the event loop for up to 60s per attempt). The loop lag
# (115-477s CRITICAL) and PumpSwap reconnect failures are downstream of this. Re-enable once
# the async write path is fixed or PumpPortal is reliably up.
export PUMPFUN_BIRTH_RECONCILER_ENABLED=0
export PUMPFUN_BIRTH_RECONCILE_ONLY_WHEN_PUMPPORTAL_DOWN=1
export PUMPFUN_BIRTH_RECONCILE_INTERVAL_SECONDS=60
export PUMPFUN_BIRTH_RECONCILE_LIMIT=100
export PUMPFUN_BIRTH_TX_CONCURRENCY=1
export DB_WRITE_SERIALIZE=1
export LISTENER_MINIMAL_DB_SCHEMA_ENABLED=1
export LISTENER_WEBHOOK_BIRTH_DRAINER_ENABLED=1
# RPC backpressure: limit concurrent getTransaction chains and retry budget.
# At concurrency=8 + retries=[1,2,4,8], RPC degradation fills all 8 slots with sleeping
# coroutines → 239s avg critical lag observed. Semaphore=3 keeps slots free; retries=[1]
# means 2 attempts total (15s→2s max per sig) — reconciler backstop covers any misses.
export LISTENER_DISCOVERY_RPC_CONCURRENCY=3
export LISTENER_GETTX_RETRY_DELAYS=1
echo "[LISTENER] $(date -u +%Y-%m-%dT%H:%M:%SZ) Starting listener (exec — supervisord owns the Python process)..."
exec python -u -m src.core.pumpfun_curve_listener
