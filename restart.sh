#!/bin/bash

# Restart services and show logs

echo "🔄 Restarting services..."
echo ""

# Kill Flask and listener
lsof -i :5002 | tail -1 | awk '{print $2}' | xargs kill -9 2>/dev/null || true
sleep 1
echo "✓ Port 5002 killed"

pkill -f "python.*pumpfun_curve_listener.py" 2>/dev/null || true
sleep 1
echo "✓ Listener killed"

pkill -f "python.*helius_cli_monitor.py" 2>/dev/null || true
sleep 1
echo "✓ Helius CLI monitor killed"

echo ""
echo "🚀 Starting Helius CLI monitor..."
python helius_cli_monitor.py &
HELIUS_PID=$!
sleep 2
echo "✓ Helius CLI monitor started (PID: $HELIUS_PID)"

echo ""

echo ""
echo "🚀 Starting listener..."
python pumpfun_curve_listener.py &
LISTENER_PID=$!
sleep 4
echo "✓ Listener started (PID: $LISTENER_PID)"

echo ""
echo "🚀 Starting Flask..."
python main.py &
FLASK_PID=$!
sleep 3
echo "✓ Flask started (PID: $FLASK_PID)"

echo ""
echo "✅ All services started!"
echo ""
echo "📊 Listener logs:"
tail -f /tmp/listener.log 2>/dev/null &

echo ""
echo "Press Ctrl+C to stop monitoring logs"
wait
