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

export TRADING_SIM_DRY_RUN_PUBLIC_KEY="${TRADING_SIM_DRY_RUN_PUBLIC_KEY:-5ZpgwwHAxs5kuer3dwwJQxjxvWaXHaLvchZJCRqigPtJ}"
echo "✓ Trading sim dry-run public key configured"
echo ""

# Kill existing processes
lsof -i :5002 | awk 'NR>1 {print $2}' | sort -u | xargs kill -9 2>/dev/null || true
pkill -9 -f "src/core/main.py" 2>/dev/null || true
sleep 1
echo "✓ Port 5002 killed"

pkill -9 -f "pumpfun_curve_listener" 2>/dev/null || true
sleep 1
echo "✓ Listener killed"

pkill -9 -f "helius_cli_monitor" 2>/dev/null || true
sleep 1
echo "✓ Helius CLI monitor killed"

echo ""
echo "⏳ Verifying cleanup..."
sleep 1

# Verify all instances are killed
remaining=$(pgrep -f "pumpfun_curve_listener" | wc -l)
if [ "$remaining" -gt 0 ]; then
    echo "⚠️  Still found $remaining listener instances, forcing hard kill..."
    pkill -9 -f "pumpfun_curve_listener" 2>/dev/null || true
    sleep 1
fi

remaining=$(pgrep -f "helius_cli_monitor" | wc -l)
if [ "$remaining" -gt 0 ]; then
    echo "⚠️  Still found $remaining helius monitor instances, forcing hard kill..."
    pkill -9 -f "helius_cli_monitor" 2>/dev/null || true
    sleep 1
fi

echo "✓ Cleanup verified"
echo ""
cd "$PROJECT_ROOT"

# Clear logs on each restart so they only contain the current session
> "$PROJECT_ROOT/migration.log"
echo "✓ migration.log cleared"
> "$PROJECT_ROOT/listener.log"
echo "✓ listener.log cleared"
> "$PROJECT_ROOT/logs/premigration.log"
echo "✓ logs/premigration.log cleared"
> "$PROJECT_ROOT/logs/ws_snapshot.log"
echo "✓ ws_snapshot.log cleared"
> "$PROJECT_ROOT/logs/ws_price_trace.log"
echo "✓ ws_price_trace.log cleared"
> "$PROJECT_ROOT/flask.log"
echo "✓ flask.log cleared"
> "$PROJECT_ROOT/logs/monitor.log"
echo "✓ monitor.log cleared"
echo ""

echo "🚀 Starting Helius CLI monitor..."
nohup python -m src.monitoring.helius_cli_monitor >> helius.log 2>&1 &
HELIUS_PID=$!
disown $HELIUS_PID
sleep 2
echo "✓ Helius CLI monitor started (PID: $HELIUS_PID)"

echo ""
echo "🚀 Starting listener..."
RPC_METRICS_DB="$PROJECT_ROOT/database/flex_complete_database.db" \
nohup python -u -m src.core.pumpfun_curve_listener >> listener.log 2>&1 &
LISTENER_PID=$!
disown $LISTENER_PID
sleep 4
echo "✓ Listener started (PID: $LISTENER_PID)"

echo ""
echo "🚀 Starting Flask app with Token Price System + WebSocket Pools..."
PYTHONPATH="$PROJECT_ROOT" \
HELIUS_RPC_URL="$HELIUS_RPC_URL" \
HELIUS_WS_URL="$HELIUS_WS_URL" \
FLEX_WS_DISABLED=1 \
RPC_METRICS_DB="$PROJECT_ROOT/database/flex_complete_database.db" \
nohup python src/core/main.py >> flask.log 2>&1 &
FLASK_PID=$!
disown $FLASK_PID
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
echo "║  - migration.log (pool migration/detection events only)                   ║"
echo "║  - flask.log (Flask app + SSE endpoint)                                   ║"
echo "║  - logs/ws_snapshot.log (WS subscriptions + snapshot lifecycle)           ║"
echo "║                                                                            ║"
echo "╚════════════════════════════════════════════════════════════════════════════╝"
echo ""
echo "📊 All output written to log files. To monitor:"
echo "   tail -f listener.log"
echo "   tail -f flask.log"
echo ""

# Kill any existing monitor instance
pkill -f "scripts/monitor.py" 2>/dev/null || true

# Launch monitor in a new Terminal window
echo "🔍 Starting monitor..."
osascript -e "tell application \"Terminal\"
    activate
    do script \"/Users/kevinkeaveney/anaconda3/envs/algotrader/bin/python '$PROJECT_ROOT/scripts/monitor.py' --interval 15\"
end tell" 2>/dev/null && echo "✓ Monitor running in new Terminal window" || {
    # Fallback: run in background and write to monitor.log
    nohup python "$PROJECT_ROOT/scripts/monitor.py" --interval 15 >> "$PROJECT_ROOT/logs/monitor.log" 2>&1 &
    MONITOR_PID=$!
    disown $MONITOR_PID
    echo "✓ Monitor started in background (PID: $MONITOR_PID) → logs/monitor.log"
}
echo ""
