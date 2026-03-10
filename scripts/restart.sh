#!/bin/bash

# Restart Flex services
# Uses reorganized src/ structure and run.py entry point

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "🔄 Restarting services..."
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
echo "🚀 Starting Flask app..."
python run.py &
FLASK_PID=$!
sleep 3
echo "✓ Flask started (PID: $FLASK_PID)"

echo ""
echo "✅ All services started!"
echo ""
echo "📊 Monitoring logs (Ctrl+C to stop)..."
wait
