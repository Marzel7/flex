#!/bin/bash

# Phase 2 Verification Script
# Validates all Phase 2 implementation

echo "=========================================="
echo "  PHASE 2 VERIFICATION"
echo "=========================================="
echo ""

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

PASS_COUNT=0
FAIL_COUNT=0

# Check function
check_file() {
    if [ -f "$1" ]; then
        echo -e "${GREEN}✓${NC} File exists: $1"
        ((PASS_COUNT++))
    else
        echo -e "${RED}✗${NC} File missing: $1"
        ((FAIL_COUNT++))
    fi
}

check_code() {
    if grep -q "$2" "$1" 2>/dev/null; then
        echo -e "${GREEN}✓${NC} Code found in $1: '$2'"
        ((PASS_COUNT++))
    else
        echo -e "${RED}✗${NC} Code NOT found in $1: '$2'"
        ((FAIL_COUNT++))
    fi
}

echo "1. Checking Main Implementation Files"
echo "======================================"
check_file "main.py"
check_file "test_pumpswap_detection.py"
check_file "test_pumpswap_phase2.py"
check_file "test_pumpswap_listener.py"
echo ""

echo "2. Checking Documentation Files"
echo "================================"
check_file "PHASE2_COMPLETION.md"
check_file "PUMPSWAP_QUICK_START.md"
check_file "PHASE2_SUMMARY.md"
check_file "PHASE2_CODE_MAP.md"
check_file "PUMPFUN_INTEGRATION_PLAN.md"
echo ""

echo "3. Checking Core Implementation"
echo "==============================="
check_code "main.py" "class TokenMonitor:"
check_code "main.py" "def is_pumpswap_token"
check_code "main.py" "def get_pumpfun_origin_info"
check_code "main.py" "def track_pumpswap_pool"
check_code "main.py" "def get_pool"
check_code "main.py" "def update_pool_data"
echo ""

echo "4. Checking Database Schema"
echo "==========================="
check_code "main.py" "is_pumpswap BOOLEAN"
check_code "main.py" "bonding_curve_address TEXT"
check_code "main.py" "pumpfun_migration_timestamp TIMESTAMP"
check_code "main.py" "pumpswap_initial_price REAL"
echo ""

echo "5. Checking WebSocket Integration"
echo "=================================="
check_code "main.py" "PHASE 2: Detect PumpSwap migrations"
check_code "main.py" "is_pumpswap_token(token_data)"
check_code "main.py" "🚀 DETECTED: PumpFun token migrated to PumpSwap"
check_code "main.py" "pumpswap_badge"
echo ""

echo "6. Checking Tests"
echo "================="
check_code "test_pumpswap_detection.py" "class PumpSwapDetectionTest:"
check_code "test_pumpswap_phase2.py" "class PumpSwapPhase2Test:"
check_code "test_pumpswap_listener.py" "class ContinuousPumpSwapListener:"
echo ""

echo "=========================================="
echo "  VERIFICATION SUMMARY"
echo "=========================================="
echo -e "${GREEN}Passed:${NC} $PASS_COUNT"
echo -e "${RED}Failed:${NC} $FAIL_COUNT"
echo ""

if [ $FAIL_COUNT -eq 0 ]; then
    echo -e "${GREEN}✓ ALL CHECKS PASSED - Phase 2 is complete!${NC}"
    exit 0
else
    echo -e "${RED}✗ Some checks failed${NC}"
    exit 1
fi
