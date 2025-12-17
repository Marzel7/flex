# Test Suite Documentation

This directory contains comprehensive tests to validate database integrity and UI consistency.

## Available Tests

### 1. `test_db_ui_match.py` - Database vs UI Validation
**Purpose:** Verifies that values stored in the database match those displayed in the UI.

**What it tests:**
- Pool data consistency between database and API
- Price accuracy and precision
- Price conversion to USD
- All core fields match between DB and API

**Usage:**
```bash
python test_db_ui_match.py
```

**Output:**
- ✓ MATCH: Database value equals API value
- ❌ MISMATCH: Database and API values differ
- ❌ NOT IN DB: Pool exists in API but not in database

**Example output:**
```
Pool Name                 Symbol   Price Match     Details
----------------------------------------------------------------------------------------------------
Polymarket                POLY     ✓ MATCH         3.104776840030757848e-11 SOL
  USD Conversion:                                  $0.0000000039 USD
```

---

### 2. `test_price_fetcher_accuracy.py` - Price Fetcher Validation
**Purpose:** Validates that the price fetcher returns accurate and valid prices.

**What it tests:**
1. **Price Exists** - Price is not None or 0
2. **Price Precision** - Price is in valid range (1e-20 to 1e6 SOL)
3. **SOL/USD Rate** - Rate exists and is reasonable ($10-$1000)
4. **DB/API Consistency** - Database and API prices match
5. **USD Conversion** - Price conversion calculation is correct

**Usage:**
```bash
python test_price_fetcher_accuracy.py
```

**Output:**
- ✓ PASS: Test passed
- ❌ FAIL: Test failed
- Shows success rate: `Passed: X/Y tests (X%)`

**Example output:**
```
Pool                      Symbol   Test                                     Result
---------------------------------------------------------------------------------------------------------
Polymarket                POLY     Price Exists                             ✓ PASS   Valid price: 2.783e-11 SOL
Polymarket                POLY     Price Precision                          ✓ PASS   Price in valid range
Polymarket                POLY     SOL/USD Rate                             ✓ PASS   Valid rate: $126.38/SOL
Polymarket                POLY     DB/API Consistency                       ✓ PASS   DB and API prices match
Polymarket                POLY     USD Conversion                           ✓ PASS   USD price: $0.0000000035

[RESULTS] Passed: 5/5 tests
[RESULTS] Success Rate: 100.0%
```

---

### 3. `test_continuous_validation.py` - Continuous Monitoring
**Purpose:** Monitors the application for data integrity issues over time.

**What it tests:**
- Database and API consistency continuously
- Price data validity throughout application runtime
- Detection of data corruption or inconsistencies
- Regular checks for price updates

**Usage:**
```bash
# Run for 5 minutes (default)
python test_continuous_validation.py

# Run for custom duration (in minutes)
python test_continuous_validation.py 10
```

**Output:**
- Periodic checks showing pool count and consistency status
- Reports any issues found
- Summary of total checks run and issues detected

**Example output:**
```
[09:35:42] Check #1 - ✓ OK (1 pools, 1 in DB)
[09:35:52] Check #2 - ✓ OK (1 pools, 1 in DB)
[09:36:02] Check #3 - ✓ OK (1 pools, 1 in DB)

[SUMMARY] Ran 15 checks over 240 seconds
[SUMMARY] Total issues found: 0

[SUCCESS] ✓ All checks passed!
```

---

## Running All Tests

```bash
# Individual tests
python test_db_ui_match.py
python test_price_fetcher_accuracy.py
python test_continuous_validation.py 2

# Or run with a script
for test in test_db_ui_match.py test_price_fetcher_accuracy.py; do
    echo "Running $test..."
    python $test || exit 1
done
```

---

## Test Coverage

| Aspect | DB vs UI | Price Accuracy | Continuous |
|--------|----------|----------------|-----------|
| Database consistency | ✓ | ✓ | ✓ |
| Price accuracy | ✓ | ✓ | ✓ |
| Price precision | ✓ | ✓ | ✓ |
| USD conversion | ✓ | ✓ | ✓ |
| SOL/USD rate | ✓ | ✓ | ✓ |
| Real-time monitoring | | | ✓ |
| Data corruption detection | | | ✓ |

---

## Validation Criteria

### Price Validation
- **Valid range:** 1e-20 to 1e6 SOL
- **Precision:** Supports scientific notation (e.g., 1.23e-10)
- **Tolerance:** 0.1% floating-point tolerance for DB/API comparison

### SOL/USD Rate Validation
- **Valid range:** $10 to $1,000 per SOL
- **Required:** Must be present for USD conversion

### USD Conversion
- **Formula:** `USD Price = Current Price × SOL/USD Rate`
- **Validation:** Calculated USD price must be reasonable (>1e-20)

---

## Troubleshooting

### Test fails with "Connection refused"
- Ensure application is running on port 5002
- Check that database file exists at `/Users/kevinkeaveney/Dev/claude/flex/raydium_pools.db`

### Test shows price mismatches
- Check if price updater thread is running
- Verify V2 fetcher is working: `python meteora_price_fetcher_v2.py <pool_address>`
- Ensure database is not locked by another process

### Test shows inconsistent USD conversion
- Verify SOL/USD rate is available in database
- Check that conversion formula: `USD = Price × Rate` is used in UI
- See `main.py` line ~3062 for UI price conversion logic

---

## Database Columns Validated

- `name` - Pool name
- `symbol` - Token symbol
- `current_price` - On-chain price in SOL
- `sol_usd_price` - SOL to USD exchange rate
- `dexscreener_price_usd` - DexScreener price reference
- `liquidity` - Pool liquidity
- `base_mint` - Token mint address
- `amm_id` - Pool address

---

## Known Precision Issues

### Price Display Format (Detailed Price Box)
The detailed price information box uses `.toFixed(10)` formatting:
```javascript
const onChainDisplay = `$${onChainUsd.toFixed(10)}`;
```

This can potentially display different prices identically if they round to the same 10 decimals.

**Example:**
- Price A: $0.00000002904924 (rounds to $0.0000000290)
- Price B: $0.00000003050000 (rounds to $0.0000000305)

**Recommendation:** Use dynamic `formatPrice()` function instead for better precision at various scales.

### Main Pool Display (Correct)
The main pool list uses the `formatPrice()` function which provides appropriate precision:
- Prices < $0.01: 8 decimals
- Prices < $1: 6 decimals
- Prices ≥ $1: 4 decimals

---

## Success Criteria

✓ All tests pass if:
1. Every pool in API exists in database
2. Prices match between DB and API (within 0.1% tolerance)
3. All prices are valid numbers in reasonable ranges
4. SOL/USD rates are available and realistic
5. USD conversion calculations are correct
6. No data corruption detected over time
7. Price values in database match the actual on-chain prices fetched
