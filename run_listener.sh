#!/bin/bash
# Run listener with automatic restart on crash/disconnect
cd "$(dirname "$0")"
# PARK the creator-resolution BACKFILL: its get_creator_from_earliest_tx paging grinds
# hundreds of RPC pages on stubborn tokens and starves LIVE migration capture. Parked so
# migrations keep flowing. Set to 1 to re-enable once the live path is confirmed healthy.
export CREATOR_BACKFILL_ENABLED=0
while true; do
    echo "[WATCHDOG] $(date -u +%Y-%m-%dT%H:%M:%SZ) Starting listener..."
    python -u -m src.core.pumpfun_curve_listener
    EXIT_CODE=$?
    echo "[WATCHDOG] $(date -u +%Y-%m-%dT%H:%M:%SZ) Listener exited with code $EXIT_CODE — restarting in 10s..."
    sleep 10
done
