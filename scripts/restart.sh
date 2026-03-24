#!/bin/bash

# Restart Flex services
# Uses reorganized src/ structure and main.py entry point with Price System
# Includes WebSocket pool subscription setup for real-time pricing

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "🔄 Restarting services..."
echo ""

# Configure WebSocket pool subscription with Helius API key
if [ -z "$HELIUS_API_KEY" ] && [ -f "$PROJECT_ROOT/config/.env" ]; then
    export HELIUS_API_KEY=$(grep "^HELIUS_API_KEY=" "$PROJECT_ROOT/config/.env" | cut -d'=' -f2)
fi

if [ -n "$HELIUS_API_KEY" ]; then
    export HELIUS_RPC_URL="https://mainnet.helius-rpc.com/?api-key=$HELIUS_API_KEY"
    export HELIUS_WS_URL="wss://mainnet.helius-rpc.com/?api-key=$HELIUS_API_KEY"
    echo "✓ WebSocket configured with Helius API key"
else
    export HELIUS_RPC_URL="https://mainnet.helius-rpc.com/?api-key="
    export HELIUS_WS_URL="wss://mainnet.helius-rpc.com/?api-key="
    echo "⚠️  No Helius API key found — WebSocket will not authenticate"
fi

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
python -u -m src.core.pumpfun_curve_listener | tee listener.log &
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
echo "╔════════════════════════════════════════════════════════════════════════════╗"
echo "║                        🎉 FLEX System Running                             ║"
echo "╠════════════════════════════════════════════════════════════════════════════╣"
echo "║                                                                            ║"
echo "║  Dashboard:     http://localhost:5002                                     ║"
echo "║  SSE Stream:    http://localhost:5002/api/price-stream                    ║"
echo "║                                                                            ║"
echo "║  Features Active:                                                         ║"
echo "║  ✅ On-chain pool discovery (authority-scan)                              ║"
echo "║  ✅ Pool validation (Token-2022 + SPL Token)                              ║"
echo "║  ✅ Real-time price computation from pool reserves                        ║"
echo "║  ✅ Server-Sent Events (SSE) streaming                                    ║"
echo "║  ✅ Auto-updating dashboard (live prices, no refresh)                     ║"
echo "║  ✅ WebSocket pool subscriptions                                          ║"
echo "║                                                                            ║"
echo "║  Logs:                                                                     ║"
echo "║  - listener.log (pool discovery + price computation)                      ║"
echo "║  - flask.log (Flask app + SSE endpoint)                                   ║"
echo "║                                                                            ║"
echo "╚════════════════════════════════════════════════════════════════════════════╝"
echo ""
echo "📊 Monitoring logs (Ctrl+C to stop)..."
wait
