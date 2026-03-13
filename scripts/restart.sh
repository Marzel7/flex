#!/bin/bash

# Restart Flex services
# Uses reorganized src/ structure and main.py entry point with Price System
# Includes WebSocket pool subscription setup for real-time pricing

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "🔄 Restarting services..."
echo ""

# Configure WebSocket pool subscription (optional — uses defaults if not set)
# Set your Helius API key here or via environment
if [ -z "$HELIUS_RPC_URL" ]; then
    export HELIUS_RPC_URL="https://mainnet.helius-rpc.com/?api-key="
    echo "ℹ️  Using default HELIUS_RPC_URL (no API key set)"
fi

if [ -z "$HELIUS_WS_URL" ]; then
    export HELIUS_WS_URL="wss://mainnet.helius-rpc.com/?api-key="
    echo "ℹ️  Using default HELIUS_WS_URL (no API key set)"
fi

# Add your API key by setting it before running this script:
# export HELIUS_API_KEY="your_api_key_here"
# Then uncomment these lines:
# if [ -n "$HELIUS_API_KEY" ]; then
#     HELIUS_RPC_URL="https://mainnet.helius-rpc.com/?api-key=$HELIUS_API_KEY"
#     HELIUS_WS_URL="wss://mainnet.helius-rpc.com/?api-key=$HELIUS_API_KEY"
# fi

echo "✓ WebSocket pool subscription ready"
echo ""

# Kill existing processes
lsof -i :5002 | tail -1 | awk '{print $2}' | xargs kill -9 2>/dev/null || true
sleep 1
echo "✓ Port 5002 killed"

pkill -9 -f "pumpfun_curve_listener" 2>/dev/null || true
sleep 1
echo "✓ Listener killed"

pkill -f "src.monitoring.helius_cli_monitor\|helius_cli_monitor.py" 2>/dev/null || true
sleep 1
echo "✓ Helius CLI monitor killed"

echo ""
echo "⏳ Verifying cleanup..."
sleep 1

# Verify all listener instances are killed
remaining=$(pgrep -f "pumpfun_curve_listener" | wc -l)
if [ "$remaining" -gt 0 ]; then
    echo "⚠️  Still found $remaining listener instances, forcing hard kill..."
    pkill -9 -f "pumpfun_curve_listener" 2>/dev/null || true
    sleep 1
fi

echo "✓ Cleanup verified"
echo ""
cd "$PROJECT_ROOT"

echo "🚀 Starting Helius CLI monitor..."
python -m src.monitoring.helius_cli_monitor &
HELIUS_PID=$!
sleep 2
echo "✓ Helius CLI monitor started (PID: $HELIUS_PID)"

echo ""
echo "🚀 Starting listener..."
python -m src.core.pumpfun_curve_listener &
LISTENER_PID=$!
sleep 4
echo "✓ Listener started (PID: $LISTENER_PID)"

echo ""
echo "🚀 Starting Flask app with Token Price System + WebSocket Pools..."
PYTHONPATH="$PROJECT_ROOT" \
HELIUS_RPC_URL="$HELIUS_RPC_URL" \
HELIUS_WS_URL="$HELIUS_WS_URL" \
python src/core/main.py &
FLASK_PID=$!
sleep 3
echo "✓ Flask started with Price System (PID: $FLASK_PID)"
echo "  ℹ️  WebSocket pool subscriptions will auto-connect for registered pools"

echo ""
echo "✅ All services started!"
echo ""
echo "📊 Monitoring logs (Ctrl+C to stop)..."
wait
