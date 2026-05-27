#!/bin/bash
# Run listener with automatic restart on crash/disconnect
cd "$(dirname "$0")"
while true; do
    echo "[WATCHDOG] $(date -u +%Y-%m-%dT%H:%M:%SZ) Starting listener..."
    python -u -m src.core.pumpfun_curve_listener
    EXIT_CODE=$?
    echo "[WATCHDOG] $(date -u +%Y-%m-%dT%H:%M:%SZ) Listener exited with code $EXIT_CODE — restarting in 10s..."
    sleep 10
done
