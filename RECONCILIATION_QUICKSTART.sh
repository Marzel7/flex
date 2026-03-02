#!/bin/bash
# Quick-start script for reconciliation system
# Run once to set up, then add to crontab

set -e

echo "=================================="
echo "RECONCILIATION SYSTEM QUICK START"
echo "=================================="

# 1. Initialize schema
echo "[1/4] Initializing reconciliation schema..."
python reconciliation_schema.py

# 2. Test collection
echo "[2/4] Testing snapshot collection..."
python reconciliation_main.py --collect

# 3. Test reconciliation
echo "[3/4] Running first reconciliation..."
python reconciliation_main.py --reconcile

# 4. Show results
echo "[4/4] Displaying results..."
python reconciliation_main.py --latest

echo ""
echo "✅ Setup complete!"
echo ""
echo "Next: Add to crontab for automated collection (every 5 minutes)"
echo ""
echo "  Run: crontab -e"
echo "  Add: */5 * * * * cd /path/to/flex && python reconciliation_main.py"
echo ""
echo "Then monitor with:"
echo "  python reconciliation_main.py --health          (7-day summary)"
echo "  python reconciliation_main.py --daily $(date +%Y-%m-%d)  (today's data)"
echo ""
