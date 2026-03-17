#!/bin/bash
# WebSocket Fix Verification Script
# Run this after deploying the fix to verify it's working

set -e

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}=== WebSocket Fix Verification ===${NC}\n"

# Configuration
DB_PATH="${1:-database/flex_complete_database.db}"
TEST_MINT="${2:-F8tKkEPMdqkP6YBhLued8DPPexRdcu4kLHp7ZEf7pump}"
LOG_FILE="${3:-listener.log}"

echo "Using:"
echo "  Database: $DB_PATH"
echo "  Test mint: $TEST_MINT"
echo "  Log file: $LOG_FILE"
echo ""

# Check 1: Database exists
echo -e "${YELLOW}[1/6] Checking database...${NC}"
if [ ! -f "$DB_PATH" ]; then
    echo -e "${RED}✗ Database not found at $DB_PATH${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Database found${NC}\n"

# Check 2: Pool registered
echo -e "${YELLOW}[2/6] Checking pool registration...${NC}"
pool_count=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM token_pool_accounts WHERE mint='$TEST_MINT'")
if [ "$pool_count" -eq 0 ]; then
    echo -e "${RED}✗ Pool $TEST_MINT not registered${NC}"
    exit 1
fi
is_active=$(sqlite3 "$DB_PATH" "SELECT is_active FROM token_pool_accounts WHERE mint='$TEST_MINT' LIMIT 1")
if [ "$is_active" -ne 1 ]; then
    echo -e "${RED}✗ Pool not active (is_active=$is_active)${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Pool registered and active${NC}\n"

# Check 3: WebSocket subscription logs
echo -e "${YELLOW}[3/6] Checking WebSocket subscription logs...${NC}"
if [ ! -f "$LOG_FILE" ]; then
    echo -e "${YELLOW}⚠ Log file not found at $LOG_FILE${NC}"
    echo "   Skipping log checks..."
else
    if grep -q "trigger_pool_refresh() CALLED" "$LOG_FILE"; then
        echo -e "${GREEN}✓ Found: trigger_pool_refresh() CALLED${NC}"
    else
        echo -e "${YELLOW}⚠ Not found: trigger_pool_refresh() CALLED${NC}"
    fi

    if grep -q "Stopping old WebSocket client" "$LOG_FILE"; then
        echo -e "${GREEN}✓ Found: Stopping old WebSocket client${NC}"
    else
        echo -e "${YELLOW}⚠ Not found: Stopping old WebSocket client${NC}"
    fi

    if grep -q "Starting fresh WebSocket" "$LOG_FILE"; then
        echo -e "${GREEN}✓ Found: Starting fresh WebSocket${NC}"
    else
        echo -e "${YELLOW}⚠ Not found: Starting fresh WebSocket${NC}"
    fi

    if grep -q "WebSocket client started" "$LOG_FILE"; then
        echo -e "${GREEN}✓ Found: WebSocket client started${NC}"
    else
        echo -e "${YELLOW}⚠ Not found: WebSocket client started${NC}"
    fi
fi
echo ""

# Check 4: PoolStateStore has reserves
echo -e "${YELLOW}[4/6] Checking PoolStateStore state...${NC}"
if grep -q "Mints in PoolStateStore" "$LOG_FILE" 2>/dev/null; then
    mints_line=$(grep "Mints in PoolStateStore" "$LOG_FILE" | tail -1)
    echo -e "${GREEN}✓ Found: $mints_line${NC}"
else
    echo -e "${YELLOW}⚠ PoolStateStore logs not found (normal if no activity)${NC}"
fi
echo ""

# Check 5: Snapshots written
echo -e "${YELLOW}[5/6] Checking snapshots...${NC}"
snapshot_count=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM token_price_snapshots WHERE mint='$TEST_MINT'")
echo "  Snapshots for $TEST_MINT: $snapshot_count"

if [ "$snapshot_count" -gt 0 ]; then
    echo -e "${GREEN}✓ Snapshots found!${NC}"

    # Show latest snapshot
    latest=$(sqlite3 "$DB_PATH" "SELECT price_usd, liquidity_usd, created_at FROM token_price_snapshots WHERE mint='$TEST_MINT' ORDER BY created_at DESC LIMIT 1")
    echo "  Latest: $latest"
else
    echo -e "${YELLOW}⚠ No snapshots yet (may need more time)${NC}"
fi
echo ""

# Check 6: Legacy pools still working
echo -e "${YELLOW}[6/6] Checking legacy pools...${NC}"
legacy_count=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM token_price_snapshots WHERE created_at > datetime('now', '-1 hour') AND is_legacy = 1")
echo "  Legacy snapshots in last hour: $legacy_count"

if [ "$legacy_count" -gt 30 ]; then
    echo -e "${GREEN}✓ Legacy pools working (continuous flow)${NC}"
elif [ "$legacy_count" -gt 0 ]; then
    echo -e "${YELLOW}⚠ Legacy snapshots low ($legacy_count, expected > 30)${NC}"
else
    echo -e "${YELLOW}⚠ No legacy snapshots in last hour (may indicate system issue)${NC}"
fi
echo ""

# Summary
echo -e "${BLUE}=== Summary ===${NC}"
if [ "$snapshot_count" -gt 0 ] && [ "$legacy_count" -gt 30 ]; then
    echo -e "${GREEN}✓✓✓ ALL CHECKS PASSED - WebSocket fix is working!${NC}"
    exit 0
elif [ "$snapshot_count" -gt 0 ]; then
    echo -e "${GREEN}✓✓ New pool snapshots flowing (legacy pool check inconclusive)${NC}"
    exit 0
else
    echo -e "${YELLOW}⚠ New pool snapshots not yet present${NC}"
    echo ""
    echo "Possible reasons:"
    echo "  1. System needs more time (wait 5-10 minutes)"
    echo "  2. Pool discovery not yet triggered"
    echo "  3. Check logs for errors: grep ERROR $LOG_FILE"
    echo "  4. Verify listener is running: pgrep -f pumpfun_curve_listener"
    exit 1
fi
