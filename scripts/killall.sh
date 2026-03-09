#!/bin/bash
# Kill the Flask application and related services
# Uses reorganized src/ structure

# Kill Flask app (run.py entry point)
pkill -f "run.py" 2>/dev/null || true
sleep 1
echo "✓ Flask app terminated"

# Kill listener if still running
pkill -f "src.core.pumpfun_curve_listener" 2>/dev/null || true
sleep 1
echo "✓ Listener terminated"

# Kill Helius monitor if still running
pkill -f "src.monitoring.helius_cli_monitor" 2>/dev/null || true
sleep 1
echo "✓ Helius monitor terminated"

echo ""
echo "✅ All Flex services killed"
