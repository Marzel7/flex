# Pool Validation Test Suite — Complete Guide

Three complementary tests for different purposes.

---

## Quick Reference

| Test | Purpose | Requires | Time | What It Proves |
|---|---|---|---|---|
| `test_account_validator.py` | Vault existence smoke test | RPC only | <30s | Vault accounts exist on-chain |
| `test_pipeline_validation.py` | Production metrics | Running worker | ~10s | System throughput health (76 snapshots/min) |
| `test_true_end_to_end_pool_identity.py` | **NEW** Complete pipeline validation | DB + optional RPC | ~30s | One pool works end-to-end (or DB/RPC only) |

---

## Test 1: Account Validator

**File:** `test_account_validator.py`

**What it does:** Validates that base_account and quote_account addresses exist on Solana

**Run:**
```bash
python3 tests/pool_validation/test_account_validator.py
```

**Output Example:**
```
Valid pool: 5cDhM4yMKipQkjSGdvYnqPdiJz685Z96rbe6GSYppump
  base_account: Dv2fVimeVWQBwjag4G5MziTWHKWkCb9MA8QMCCmhgT5J exists
  quote_account: 42mRiqwoYbkfNgdnxVXppj5wmwRQexsqQUZzjyQzs4zb exists
  ✓ PASS

Invalid pool: non-existent accounts
  base_account: A1HFqQZF3t16RQ8ENV9NLkVXL6E5Fu31sWk5s33jH5wn does not exist
  ✗ FAIL
```

**Use Case:** Quick smoke test to verify vault accounts are valid

**Replaces:** Previous test_pool_address_validator logic (was validating base_account directly, now test 1 does this)

---

## Test 2: Pipeline Validation

**File:** `test_pipeline_validation.py`

**What it does:** Validates production metrics — snapshots per minute, WebSocket coverage, liquidity filtering

**Run:**
```bash
python3 tests/pool_validation/test_pipeline_validation.py
```

**Output Example:**
```
Production Health Summary
========================
Total snapshots: 76
Snapshots per minute: 76/min (excellent)
Snapshot coverage (100 top mints): 100%
Pools with WebSocket subscription: 64/64 (100%)
Pools with snapshots: 64/64 (100%)
Min liquidity threshold: $100 (filters very low liquidity)

✓ System producing snapshots at expected rate
✓ All registered pools have WebSocket subscriptions
✓ Price data flowing to database
```

**Use Case:** Verify system is healthy and functioning at production volume

**What it Doesn't Test:**
- Correctness of pool data model (fields in wrong columns)
- Correctness of prices (matching reserves)
- Decoding of on-chain pool accounts

---

## Test 3: End-to-End Pool Identity **[NEW]**

**File:** `test_true_end_to_end_pool_identity.py`

**What it does:** Validates one exact pool flows through complete pipeline

**Run without worker:**
```bash
python3 tests/pool_validation/test_true_end_to_end_pool_identity.py
```

**Run with worker:**
```bash
# Terminal 1
python3 -c "from src.core.price_worker import start_price_worker; start_price_worker('database/flex_complete_database.db')"

# Terminal 2
python3 tests/pool_validation/test_true_end_to_end_pool_identity.py
```

**7-Step Validation:**
1. Pool identity — pool_address, base_account, quote_account are distinct
2. Decoded vaults — on-chain pool account decodes correctly
3. Vault existence — both vaults exist on Solana
4. WebSocket subscriptions — both vaults in live subscriptions
5. Pool state reserves — PoolStateStore has live reserve data
6. Snapshot exists — price snapshot in DB
7. Price correctness — computed price matches snapshot (±5%)

**Output Example (without worker):**
```
[STEP 1/7] Validate pool identity...
  ⚠️  IDENTITY: pool_address == base_account (should be distinct)

[STEP 2/7] Validate decoded vaults...
  ⚠️  Skipped (pool_program is 'unknown')

[STEP 3/7] Validate vault accounts exist on-chain...
  ✓ Passed

[STEP 4/7] Validate WebSocket subscriptions...
  ⊘ Skipped (worker not running)

[STEP 5/7] Validate pool state reserves...
  ⊘ Skipped (worker not running)

[STEP 6/7] Validate snapshot exists...
  ✓ Passed

[STEP 7/7] Validate price correctness...
  ⊘ Skipped (worker not running)

RESULT: PASSED WITH WARNINGS (1 issues)
  ⚠️  IDENTITY: pool_address == base_account (should be distinct)
```

**Use Case:** Gold standard — proves the system works end-to-end for at least one pool

**Unique Features:**
- Automatically selects rpc_authoritative pool with snapshot
- Works without worker running (DB + RPC validation)
- Works with worker running (complete pipeline validation)
- Tests exact issues mentioned in plan: pool_address correctness, WebSocket subscriptions, reserve updates, pricing accuracy

---

## Test Pyramid

```
                    ▲
                   /|\
                  / | \  Integration Test
                 /  |  \ (End-to-End)
                /   |   \
               ┌────┴────┐
              /|          |\
             / |          | \  Acceptance Tests
            /  |          |  \ (Metrics)
           /   |          |   \
          ┌────┴──────────┴────┐
         /|                     |\
        / |                     | \  Unit Tests
       /  |                     |  \ (Account Validation)
      /   |                     |   \
     ┌────┴─────────────────────┴────┐
     │  Database + RPC (Foundation)   │
     └────────────────────────────────┘
```

**Base Layer:** Database schema and RPC connection (verified by test setup)
**Unit Tests:** Account validator (proves vaults exist)
**Acceptance Tests:** Pipeline metrics (proves system throughput)
**Integration Test:** End-to-end pool (proves complete pipeline works)

---

## Combined Test Run

Run all three together for complete validation:

```bash
#!/bin/bash
set -e

echo "================================"
echo "Pool Validation Test Suite"
echo "================================"
echo

echo "[1/3] Account Validator..."
python3 tests/pool_validation/test_account_validator.py
echo

echo "[2/3] Pipeline Validation..."
python3 tests/pool_validation/test_pipeline_validation.py
echo

echo "[3/3] End-to-End Integration..."
python3 tests/pool_validation/test_true_end_to_end_pool_identity.py
echo

echo "================================"
echo "All tests completed"
echo "================================"
```

**Expected Results:**
1. ✓ All vault accounts exist
2. ✓ System producing snapshots at expected rate
3. ✓ One pool validates through complete pipeline (or DB+RPC if worker not running)

---

## What Each Test Catches

### Account Validator Catches
- ✓ Non-existent vault accounts
- ✓ Accounts owned by wrong token program
- ✓ Invalid base_account field

### Pipeline Validation Catches
- ✓ Broken WebSocket subscriptions
- ✓ Stalled snapshot generation
- ✓ Liquidity filtering too aggressive
- ✓ System not producing prices at expected rate

### End-to-End Integration Catches
- ✓ Pool data model bugs (pool_address == base_account)
- ✓ On-chain pool structure mismatches
- ✓ WebSocket message handling issues
- ✓ Reserve update timing problems
- ✓ Price calculation errors
- ✓ Snapshot storage issues
- ✓ Unknown program ID problems

---

## Debugging Workflow

### If Account Validator Fails
```bash
# Check if vault accounts actually exist
solana account <base_account> -u <rpc_url>

# Check if they're owned by token program
solana account <base_account> -u <rpc_url> | grep owner
```

### If Pipeline Validation Fails
```bash
# Check if worker is running
ps aux | grep price_worker

# Check WebSocket subscriptions
sqlite3 database/flex_complete_database.db "SELECT COUNT(*) FROM token_pool_accounts"

# Check recent snapshots
sqlite3 database/flex_complete_database.db "SELECT COUNT(*) FROM token_price_snapshots WHERE captured_at > strftime('%s') - 300"
```

### If End-to-End Integration Fails
```bash
# Check pool identity
sqlite3 database/flex_complete_database.db "
SELECT mint, pool_address, base_account, quote_account
FROM token_pool_accounts
WHERE mint = '3qFqa2n9zriortz4d56pbNaSzpay6BPrGYayHufnpump'
"

# Check if program ID is known
sqlite3 database/flex_complete_database.db "
SELECT pool_program
FROM token_pool_accounts
WHERE mint = '3qFqa2n9zriortz4d56pbNaSzpay6BPrGYayHufnpump'
"

# Check if snapshot exists
sqlite3 database/flex_complete_database.db "
SELECT price_usd, source
FROM token_price_snapshots
WHERE mint = '3qFqa2n9zriortz4d56pbNaSzpay6BPrGYayHufnpump'
ORDER BY captured_at DESC LIMIT 1
"

# If worker running, check pool state
python3 -c "
from src.core.price_worker import get_price_worker
from src.core.integration_helpers import export_worker_status
w = get_price_worker('database/flex_complete_database.db')
s = export_worker_status(w)
mint = '3qFqa2n9zriortz4d56pbNaSzpay6BPrGYayHufnpump'
print(f'Mints in store: {s.all_mints[:5]}')
print(f'WS started: {s.ws_started}')
print(f'Pool states: {len(s.pool_states)}')
"
```

---

## Issues These Tests Can't Catch

- **Network latency:** Tests are local, don't measure actual RPC latency
- **Price accuracy vs real markets:** Can't validate prices are "correct" without external oracle
- **Edge cases:** Tests use existing data, may miss edge cases in new code
- **Performance degradation:** No load testing or stress testing

---

## Next Steps

1. **Run all three tests** to establish baseline
2. **Fix pool_address == base_account bug** (found by end-to-end test)
3. **Populate pool_program correctly** during discovery
4. **Rerun end-to-end test** to verify fixes
5. **Monitor production** for any regressions

---

## Files Reference

```
tests/pool_validation/
├── test_account_validator.py              ← Unit test
├── test_pipeline_validation.py            ← Acceptance test
├── test_true_end_to_end_pool_identity.py ← Integration test [NEW]
├── README_INTEGRATION_TEST.md             ← Integration test guide
└── TEST_SUITE_GUIDE.md                    ← This file

src/core/
└── integration_helpers.py                 ← New helpers (decode, price, status)
```

---

## Quick Start for User

```bash
# 1. Run account validator (5 seconds)
python3 tests/pool_validation/test_account_validator.py

# 2. Run pipeline metrics (5 seconds)
python3 tests/pool_validation/test_pipeline_validation.py

# 3. Run integration test (20 seconds, without worker)
python3 tests/pool_validation/test_true_end_to_end_pool_identity.py

# 4. If issues found:
#    - Use debugging workflow above
#    - Reference plan document
#    - Fix pool discovery bugs
#    - Rerun tests to verify fixes
```

All tests should pass (or skip gracefully with clear messages).
