#!/bin/bash
# Start WATCHTOWER under Supervisor + caffeinate (prevents macOS sleep killing workers)
#
# macOS sleep kills:
#   - TCP sockets stall, Helius webhook delivery stops
#   - threading.Timer and time.sleep() freeze
#   - SQLite WAL checkpoints stop, WAL grows unbounded
#   - gevent hub freezes under gevent workers
#
# caffeinate flags:
#   -d  prevent display sleep
#   -i  prevent system idle sleep
#   -s  prevent system sleep (only when on AC power)
#   -m  prevent disk sleep

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
SUPERVISORD="/Users/kevinkeaveney/anaconda3/envs/algotrader/bin/supervisord"
SUPERVISORCTL="/Users/kevinkeaveney/anaconda3/envs/algotrader/bin/supervisorctl"
CONF="$PROJECT_ROOT/config/supervisor/supervisord.conf"

# Load env vars
if [ -f "$PROJECT_ROOT/config/.env" ]; then
    [ -z "$HELIUS_API_KEY" ] && export HELIUS_API_KEY=$(grep "^HELIUS_API_KEY=" "$PROJECT_ROOT/config/.env" | cut -d'=' -f2)
    [ -z "$CREATOR_MOVEMENT_WEBHOOK_ID" ] && export CREATOR_MOVEMENT_WEBHOOK_ID=$(grep "^CREATOR_MOVEMENT_WEBHOOK_ID=" "$PROJECT_ROOT/config/.env" | cut -d'=' -f2)
    [ -z "$CREATOR_MOVEMENT_WEBHOOK_URL" ] && export CREATOR_MOVEMENT_WEBHOOK_URL=$(grep "^CREATOR_MOVEMENT_WEBHOOK_URL=" "$PROJECT_ROOT/config/.env" | cut -d'=' -f2)
fi

if [ -n "$HELIUS_API_KEY" ]; then
    export HELIUS_RPC_URL="https://mainnet.helius-rpc.com/?api-key=$HELIUS_API_KEY"
    export HELIUS_WS_URL="wss://mainnet.helius-rpc.com/?api-key=$HELIUS_API_KEY"
fi

export TRADING_SIM_DRY_RUN_PUBLIC_KEY="${TRADING_SIM_DRY_RUN_PUBLIC_KEY:-5ZpgwwHAxs5kuer3dwwJQxjxvWaXHaLvchZJCRqigPtJ}"
export PYTHONPATH="$PROJECT_ROOT"
export RPC_METRICS_DB="$PROJECT_ROOT/database/flex_complete_database.db"
export HELIUS_RPC_URL HELIUS_WS_URL CREATOR_MOVEMENT_WEBHOOK_ID CREATOR_MOVEMENT_WEBHOOK_URL

# WATCHTOWER CREATE Interceptor (Passive validation mode)
export ENABLE_CREATE_INTERCEPTOR=true
export INTERCEPTOR_MODE=PASSIVE
export INTERCEPTOR_BUY_SOL=0

# Benchmark mode: monitor ALL pump.fun CREATEs for latency/position validation
# Set CREATE_BENCHMARK_TTL_HOURS=4 for initial trial run, 24 for full benchmark
export INTERCEPTOR_CREATE_BENCHMARK=true
export CREATE_BENCHMARK_TTL_HOURS=4

echo "🔄 Stopping any existing supervisor instance..."
"$SUPERVISORCTL" -c "$CONF" shutdown 2>/dev/null || true
pkill -f "supervisord.*watchtower" 2>/dev/null || true
sleep 2

# Kill legacy processes that supervisor will now manage
lsof -i :5002 | awk 'NR>1 {print $2}' | sort -u | xargs kill -9 2>/dev/null || true
pkill -9 -f "src/core/main.py" 2>/dev/null || true
pkill -9 -f "pumpfun_curve_listener" 2>/dev/null || true
pkill -9 -f "run_listener.sh" 2>/dev/null || true
pkill -9 -f "helius_cli_monitor" 2>/dev/null || true
sleep 2

# Clear logs
> "$PROJECT_ROOT/migration.log"
> "$PROJECT_ROOT/listener.log"
> "$PROJECT_ROOT/logs/premigration.log"
> "$PROJECT_ROOT/logs/ws_snapshot.log"
> "$PROJECT_ROOT/logs/ws_price_trace.log"
> "$PROJECT_ROOT/flask.log"
> "$PROJECT_ROOT/logs/monitor.log"
echo "✓ Logs cleared"

cd "$PROJECT_ROOT"

echo ""
echo "🚀 Starting Supervisor (manages all WATCHTOWER processes)..."
# caffeinate keeps macOS awake; supervisord manages process lifecycle
caffeinate -dims "$SUPERVISORD" -c "$CONF" &
CAFF_PID=$!
echo "✓ caffeinate + supervisord launched (PID: $CAFF_PID)"

sleep 5

# Explicitly start the autostart=false standalone services. They are autostart=false so a
# bare `supervisord -c` reload does NOT auto-launch them — but a deliberate start via this
# script SHOULD bring them up. (operation_scheduler writes wt_operation_activity that feeds
# the /ops/cards activity feed; ws_cascade is the real-time launch detector. Leaving them
# down silently staled the feed by ~18h.)
echo ""
echo "▶️  Starting standalone services (operation_scheduler, ws_cascade)..."
for svc in operation_scheduler ws_cascade; do
    "$SUPERVISORCTL" -c "$CONF" start "$svc" 2>&1 | sed 's/^/   /'
done

echo ""
echo "📋 Process status:"
"$SUPERVISORCTL" -c "$CONF" status

echo ""
echo "╔════════════════════════════════════════════════════════════════════════════╗"
echo "║                    🎉 WATCHTOWER Supervised Mode                          ║"
echo "╠════════════════════════════════════════════════════════════════════════════╣"
echo "║                                                                            ║"
echo "║  Dashboard:     http://localhost:5002                                     ║"
echo "║  Health:        http://localhost:5002/healthz                             ║"
echo "║                                                                            ║"
echo "║  Managed processes:                                                        ║"
echo "║  ✅ watchtower_api          (Gunicorn + gevent)                           ║"
echo "║  ✅ watchtower_listener     (pumpfun curve listener + watchdog)            ║"
echo "║  ✅ watchtower_helius_monitor                                              ║"
echo "║                                                                            ║"
echo "║  Control:                                                                  ║"
echo "║  supervisorctl -c config/supervisor/supervisord.conf status               ║"
echo "║  supervisorctl -c config/supervisor/supervisord.conf restart all          ║"
echo "║  supervisorctl -c config/supervisor/supervisord.conf tail watchtower_api  ║"
echo "║                                                                            ║"
echo "║  Logs:          logs/supervisor/                                           ║"
echo "╚════════════════════════════════════════════════════════════════════════════╝"
echo ""
echo "⚙️  caffeinate prevents macOS sleep. System will stay awake until you run:"
echo "   ./scripts/stop_supervised.sh"
echo ""
