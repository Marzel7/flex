#!/bin/bash
# Quick Reference — Production Migration Status
# 2026-03-17

DB="database/flex_complete_database.db"

echo "========================================"
echo "MIGRATION STATUS QUICK REFERENCE"
echo "========================================"
echo ""

# Check 1: Listener running?
if ps -p $(cat /tmp/listener.pid 2>/dev/null) > /dev/null 2>&1; then
    echo "✓ Listener: Running (PID $(cat /tmp/listener.pid))"
else
    echo "✗ Listener: NOT RUNNING — restart with:"
    echo "  source .env && python3 -m src.core.pumpfun_curve_listener &"
fi

echo ""

# Check 2: New registrations?
NEW_POOLS=$(sqlite3 "$DB" "SELECT COUNT(*) FROM token_pool_accounts WHERE is_legacy=0" 2>/dev/null)
echo "New pools registered: $NEW_POOLS"

if [ "$NEW_POOLS" -gt 0 ]; then
    echo "  ✓ New data detected! Sample:"
    sqlite3 "$DB" << 'EOF'
SELECT mint, pool_address, discovery_method, pool_score
FROM token_pool_accounts
WHERE is_legacy = 0
ORDER BY created_at DESC
LIMIT 3;
EOF
fi

echo ""

# Check 3: Telemetry?
TELEMETRY=$(sqlite3 "$DB" "SELECT COUNT(*) FROM token_resolution_telemetry WHERE created_at > strftime('%s', 'now') - 3600" 2>/dev/null)
echo "Telemetry records (last hour): $TELEMETRY"

echo ""

# Check 4: Legacy data isolated?
LEGACY=$(sqlite3 "$DB" "SELECT COUNT(*) FROM token_pool_accounts WHERE is_legacy=1" 2>/dev/null)
QUARANTINE=$(sqlite3 "$DB" "SELECT COUNT(*) FROM token_pool_accounts WHERE is_active=0" 2>/dev/null)
echo "Legacy rows: $LEGACY (quarantined invalid: $QUARANTINE)"

echo ""
echo "========================================"

if [ "$NEW_POOLS" -ge 5 ]; then
    echo "✅ Ready for validation: python3 validation_harness.py --check all"
else
    echo "⏳ Waiting for new migrations... (need ~5-10 new pools)"
fi

echo ""
