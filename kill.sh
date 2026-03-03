#!/bin/bash

# Kill all services

echo "🔴 Killing all services..."
echo ""

# Kill Flask
lsof -i :5002 | tail -1 | awk '{print $2}' | xargs kill -9 2>/dev/null || true
sleep 1
echo "✓ Port 5002 killed"

# Kill listener
pkill -f "python.*pumpfun_curve_listener.py" 2>/dev/null || true
sleep 1
echo "✓ Listener killed"

# Kill RPC Metrics API
lsof -i :8001 | tail -1 | awk '{print $2}' | xargs kill -9 2>/dev/null || true
sleep 1
echo "✓ Port 8001 killed"

# Kill Helius CLI monitor
pkill -f "python.*helius_cli_monitor.py" 2>/dev/null || true
sleep 1
echo "✓ Helius CLI monitor killed"

# Kill all remaining Python processes (excluding mcp server)
pkill -9 -f "python" --inverse -f "mpc" 2>/dev/null || true
sleep 1
echo "✓ Remaining Python processes killed (mpc server preserved)"

# Kill all remaining Node processes (excluding mcp server)
pkill -9 -f "node" --inverse -f "mcp" 2>/dev/null || true
sleep 1
echo "✓ Remaining Node processes killed (mcp server preserved)"

echo ""
echo "✅ All services stopped!"
