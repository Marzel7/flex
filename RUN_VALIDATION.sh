#!/bin/bash
# Production Validation Script
# Runs the complete validation suite for the PumpSwap discovery pipeline

set -e

DB_PATH="database/flex_complete_database.db"
RESULTS_DIR="/tmp/validation_results"
RPC_URL="${HELIUS_RPC_URL:-https://mainnet.helius-rpc.com/?api-key=16f1a5fc-2592-466c-a5d4-b5799ae8da96}"

echo "=================================="
echo "PumpSwap Discovery Pipeline Validation"
echo "=================================="
echo ""
echo "Date: $(date)"
echo "RPC: ${RPC_URL:0:50}..."
echo ""

# Create results directory
mkdir -p "$RESULTS_DIR"

# ============================================================
# STEP 1: Extract test signatures
# ============================================================
echo "[1/6] Extracting test signatures..."

# Known-good historical migrations (successful before fixes)
sqlite3 "$DB_PATH" \
  "SELECT DISTINCT migration_tx FROM token_pool_accounts \
   WHERE discovery_method IN ('tx_parsing', 'vault_inference') \
   AND migration_tx IS NOT NULL \
   ORDER BY created_at DESC LIMIT 10" \
  > "$RESULTS_DIR/known_good_sigs.txt" 2>/dev/null || true

# Previously failing migrations (stuck in pending)
sqlite3 "$DB_PATH" \
  "SELECT DISTINCT migration_tx FROM token_pool_accounts \
   WHERE vault_validation_status = 'pending' \
   AND migration_tx IS NOT NULL \
   ORDER BY created_at DESC LIMIT 5" \
  > "$RESULTS_DIR/failing_sigs.txt" 2>/dev/null || true

GOOD_COUNT=$(wc -l < "$RESULTS_DIR/known_good_sigs.txt" 2>/dev/null || echo "0")
FAIL_COUNT=$(wc -l < "$RESULTS_DIR/failing_sigs.txt" 2>/dev/null || echo "0")

echo "  ✓ Found $GOOD_COUNT known-good signatures"
echo "  ✓ Found $FAIL_COUNT previously-failing signatures"
echo ""

# ============================================================
# STEP 2: Syntax & imports verification
# ============================================================
echo "[2/6] Verifying code syntax and imports..."

python3 -m py_compile src/core/pool_discovery.py 2>/dev/null && echo "  ✓ pool_discovery.py" || echo "  ✗ pool_discovery.py FAILED"
python3 -m py_compile src/core/vault_discovery.py 2>/dev/null && echo "  ✓ vault_discovery.py" || echo "  ✗ vault_discovery.py FAILED"
python3 -m py_compile src/core/pool_detector.py 2>/dev/null && echo "  ✓ pool_detector.py" || echo "  ✗ pool_detector.py FAILED"
python3 -m py_compile src/core/pumpfun_curve_listener.py 2>/dev/null && echo "  ✓ pumpfun_curve_listener.py" || echo "  ✗ pumpfun_curve_listener.py FAILED"

echo ""

# ============================================================
# STEP 3: Program ID alignment check
# ============================================================
echo "[3/6] Verifying program ID alignment..."

python3 << 'PYEOF'
import sys
sys.path.insert(0, '/Users/kevinkeaveney/Dev/claude/flex')

try:
    from src.core.vault_discovery import (
        SPL_TOKEN_PROGRAM_ID,
        RAYDIUM_PROGRAM_ID,
        PUMPSWAP_PROGRAM_ID
    )
    from src.core.pool_detector import AMMPrograms

    tests = {
        'SPL_TOKEN_PROGRAM_ID': 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA',
        'RAYDIUM_PROGRAM_ID': '675kPX9MHTjS2zt1qrVrrVrZg1ankqqgoerEmJlwQ1K',
        'PUMPSWAP_PROGRAM_ID': 'pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA',
    }

    all_good = True
    for name, expected in tests.items():
        if name == 'SPL_TOKEN_PROGRAM_ID':
            actual = SPL_TOKEN_PROGRAM_ID
        elif name == 'RAYDIUM_PROGRAM_ID':
            actual = RAYDIUM_PROGRAM_ID
        elif name == 'PUMPSWAP_PROGRAM_ID':
            actual = PUMPSWAP_PROGRAM_ID

        match = actual == expected
        symbol = "✓" if match else "✗"
        print(f"  {symbol} {name}")
        if not match:
            all_good = False

    # Check pool_detector
    if AMMPrograms.RAYDIUM_AMM == '675kPX9MHTjS2zt1qrVrrVrZg1ankqqgoerEmJlwQ1K':
        print(f"  ✓ AMMPrograms.RAYDIUM_AMM")
    else:
        print(f"  ✗ AMMPrograms.RAYDIUM_AMM")
        all_good = False

    if AMMPrograms.ORCA_WHIRLPOOL == 'whirLbMiicVdio4KfUqKKvsLrZtSqwNAUafgJMYco':
        print(f"  ✓ AMMPrograms.ORCA_WHIRLPOOL")
    else:
        print(f"  ✗ AMMPrograms.ORCA_WHIRLPOOL")
        all_good = False

    sys.exit(0 if all_good else 1)

except Exception as e:
    print(f"  ✗ Error: {e}")
    sys.exit(1)
PYEOF

echo ""

# ============================================================
# STEP 4: Database schema verification
# ============================================================
echo "[4/6] Verifying database schema..."

# Check token_pool_accounts has required columns
POOL_COLS=$(sqlite3 "$DB_PATH" ".schema token_pool_accounts" | grep -c "pool_address\|pool_score\|discovery_method" || echo "0")
if [ "$POOL_COLS" -ge 3 ]; then
    echo "  ✓ token_pool_accounts has pool_address, pool_score, discovery_method"
else
    echo "  ✗ token_pool_accounts missing required columns"
fi

# Check telemetry table exists
TELEMETRY=$(sqlite3 "$DB_PATH" ".schema token_resolution_telemetry" | wc -l)
if [ "$TELEMETRY" -gt 5 ]; then
    echo "  ✓ token_resolution_telemetry table exists"
else
    echo "  ✗ token_resolution_telemetry table missing or incomplete"
fi

echo ""

# ============================================================
# STEP 5: Query recent telemetry metrics
# ============================================================
echo "[5/6] Querying telemetry metrics..."

sqlite3 "$DB_PATH" << 'SQLEOF'
.header on
.mode line

-- Resolution rate
SELECT
    'Resolution Rate' as metric,
    ROUND(100.0 *
        COUNT(CASE WHEN resolved_at IS NOT NULL THEN 1 END) /
        NULLIF(COUNT(*), 0), 1) || '%' as value
FROM token_resolution_telemetry
WHERE created_at > strftime('%s', 'now') - 86400;

-- Average resolve time
SELECT
    'Avg Resolve Time' as metric,
    ROUND(AVG(resolve_seconds), 2) || 's' as value
FROM token_resolution_telemetry
WHERE resolved_at IS NOT NULL
  AND created_at > strftime('%s', 'now') - 86400;

-- Top resolve source (last 24h)
SELECT
    'Top Resolve Source' as metric,
    resolve_source as value
FROM (
    SELECT resolve_source, COUNT(*) as count
    FROM token_resolution_telemetry
    WHERE resolved_at IS NOT NULL
      AND created_at > strftime('%s', 'now') - 86400
    GROUP BY resolve_source
    ORDER BY count DESC
    LIMIT 1
);

-- Vault validation rate
SELECT
    'Vault Validation Rate' as metric,
    ROUND(100.0 *
        COUNT(CASE WHEN vault_validation_status = 'validated' THEN 1 END) /
        COUNT(*), 1) || '%' as value
FROM token_pool_accounts;

-- Total pools registered
SELECT
    'Total Pools Registered' as metric,
    COUNT(DISTINCT mint) as value
FROM token_pool_accounts;

SQLEOF

echo ""

# ============================================================
# STEP 6: Check for validation violations
# ============================================================
echo "[6/6] Checking for validation violations..."

VIOLATIONS=$(sqlite3 "$DB_PATH" << 'SQLEOF'
SELECT COUNT(*) as count FROM (
    SELECT CASE
        WHEN pool_address IS NULL THEN 'pool_address NULL'
        WHEN pool_address = base_account THEN 'pool == base'
        WHEN pool_address = quote_account THEN 'pool == quote'
        WHEN base_account = quote_account THEN 'base == quote'
        WHEN pool_program NOT IN (
            'pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA',
            '675kPX9MHTjS2zt1qrVrrVrZg1ankqqgoerEmJlwQ1K',
            '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P'
        ) THEN 'pool_program invalid'
        ELSE NULL
    END AS violation
    FROM token_pool_accounts
)
WHERE violation IS NOT NULL;
SQLEOF
)

if [ "$VIOLATIONS" -eq 0 ]; then
    echo "  ✓ No validation violations found"
else
    echo "  ⚠️  Found $VIOLATIONS pools with violations"
fi

echo ""

# ============================================================
# SUMMARY
# ============================================================
echo "=================================="
echo "Validation Summary"
echo "=================================="
echo ""
echo "Results saved to: $RESULTS_DIR"
echo ""
echo "Next steps:"
echo "1. Run replay test harness:"
echo "   python3 replay_test_harness.py --group historical_good"
echo ""
echo "2. Run batch validation:"
echo "   python3 validation_harness.py --check discovery --check vault"
echo ""
echo "3. View live metrics:"
echo "   ./monitoring_dashboard.sh"
echo ""
echo "See PRODUCTION_VALIDATION_STRATEGY.md for detailed validation procedures."
echo ""
