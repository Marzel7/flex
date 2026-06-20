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
export LISTENER_CREATOR_FUNDING_QUEUE_ENABLED=0
export LISTENER_CREATOR_RESOLUTION_QUEUE_ENABLED=0
export LISTENER_DB_MAINTENANCE_ENABLED=0
export LISTENER_MIGRATION_RECONCILER_ENABLED=0
export LISTENER_PORTAL_VSOL_FLUSH_ENABLED=0
export LISTENER_DB_STARTUP_MAINTENANCE_ENABLED=0
export LISTENER_BONDING_INDEX_FULL_HYDRATE_ENABLED=0
export LISTENER_PUMPPORTAL_BIRTHS_ENABLED=1
export LISTENER_MINIMAL_DB_SCHEMA_ENABLED=1
export LISTENER_WEBHOOK_BIRTH_DRAINER_ENABLED=1
while true; do
    echo "[WATCHDOG] $(date -u +%Y-%m-%dT%H:%M:%SZ) Starting listener..."
    python -u -m src.core.pumpfun_curve_listener
    EXIT_CODE=$?
    echo "[WATCHDOG] $(date -u +%Y-%m-%dT%H:%M:%SZ) Listener exited with code $EXIT_CODE — restarting in 10s..."
    sleep 10
done
