# Pool Validation Test Suite

Comprehensive tests for pool account validation, discovery, and end-to-end pipeline verification.

## Test Files

### 1. `test_account_validator.py`
Tests pool account validation using on-chain RPC calls.

**What it tests:**
- Single account validation (exists/doesn't exist)
- Pool pair validation (both base and quote accounts)
- Native token accounts (SOL, WSOL)
- Batch validation of multiple pools
- Convenience wrapper function

**Run:**
```bash
python3 tests/pool_validation/test_account_validator.py
```

**Example output:**
```
================================================================================
POOL ACCOUNT VALIDATION TEST SUITE
================================================================================
RPC Endpoint: https://mainnet.helius-rpc.com/?api-key=...

================================================================================
TEST 1: Single Account Validation
================================================================================

[1a] Checking valid account (SOL native token)...
  Address: So11111111111111111111111111111111111111112
  Exists: True
  Owner: 11111111111111111111111111111111
  Data size: 0
  ✅ PASS

[1b] Checking invalid account...
  Address: InvalidAddressNotRealAddress12345678901234567
  Exists: False
  ✅ PASS
```

---

### 2. `test_pipeline_validation.py`
Tests the complete end-to-end pipeline from pool discovery to snapshot storage.

**What it tests:**
- Database health and integrity
- Snapshot production rate (40+/min)
- WebSocket subscription coverage
- Liquidity filter functionality
- End-to-end pipeline (registration → websocket → pricing → snapshot)
- Price accuracy and consistency
- Continuous snapshot streaming

**Requirements:**
- Running listener process (`pumpfun_curve_listener.py`)
- Database populated with active pools (`database/flex_complete_database.db`)
- WebSocket subscriptions active

**Run:**
```bash
python3 tests/pool_validation/test_pipeline_validation.py
```

**Example output:**
```
================================================================================
PIPELINE VALIDATION TEST SUITE
================================================================================

[TEST 1] Database Health
  Total pools: 118
  Total snapshots: 2387
  Snapshots (last hour): 2292
  DB file size: 156.45 MB
  ✅ PASS

[TEST 2] Production Snapshot Throughput (60s window)
  Snapshots in last 60s: 42
  Rate: 42/min
  ✅ PASS

[TEST 3] WebSocket Coverage
  Active pools: 118
  Recent snapshot sources: 67
  ✅ PASS

[TEST 4] Liquidity Filter
  High liquidity pools (>$100.0): 67
  Low liquidity pools (<$100.0): 1
  ✅ PASS

[TEST 5] End-to-End Pipeline (latest token)
  Testing mint: 5cDhM4yMKipQkjSGdvYnqPdiJz685Z96rbe6GSYppump
  Price: $0.00004516
  Source: pool
  ✅ PASS - Pipeline validated

[TEST 6] Price Accuracy (latest token)
  Sample size: 42 snapshots
  Price range: $0.00004500 - $0.00004532
  Average: $0.00004516
  Deviation: 1.01x
  ✅ PASS

================================================================================
ALL PIPELINE TESTS PASSED
================================================================================
```

---

## Pool Validation Test Coverage

| Layer | Test | File | Status |
|-------|------|------|--------|
| **Account Validation** | Account exists on-chain | `test_account_validator.py` | ✅ |
| **Pool Pair** | Both base & quote accounts exist | `test_account_validator.py` | ✅ |
| **Discovery** | Pools registered to database | `test_pipeline_validation.py` | ✅ |
| **WebSocket** | Subscriptions active & coverage | `test_pipeline_validation.py` | ✅ |
| **Reserve Updates** | Continuous updates via WebSocket | `test_pipeline_validation.py` | ✅ |
| **Price Computation** | Prices computed every 10 seconds | `test_pipeline_validation.py` | ✅ |
| **Snapshot Persistence** | Snapshots written to database | `test_pipeline_validation.py` | ✅ |
| **Throughput** | 40-50 snapshots/minute sustained | `test_pipeline_validation.py` | ✅ |
| **Liquidity Filter** | Low-liquidity pools filtered | `test_pipeline_validation.py` | ✅ |
| **Price Accuracy** | Prices reasonable & consistent | `test_pipeline_validation.py` | ✅ |

---

## How to Use These Tests

### Quick System Validation
```bash
# Verify entire system is working
python3 tests/pool_validation/test_pipeline_validation.py
```

### Check Pool Account Validity
```bash
# Validate specific pool accounts before registration
python3 tests/pool_validation/test_account_validator.py
```

### Integration with Pool Discovery
The `PoolAccountValidator` can be integrated into `src/core/pool_discovery.py`:

```python
from src.core.pool_account_validator import validate_pool_accounts

# Before registering a pool:
valid, details = await validate_pool_accounts(
    rpc_url=HELIUS_RPC,
    base_account=pool_base,
    quote_account=pool_quote
)

if not valid:
    logger.warning(f"Skipping invalid pool: {details}")
    continue

# Register to database
register_pool_to_db(mint, base_account, quote_account, ...)
```

---

## Test Dependencies

### Account Validator Tests
- `aiohttp` - Async HTTP client
- `dotenv` - Load RPC URL from `.env`
- Helius RPC endpoint (via `HELIUS_RPC_URL` env var)

### Pipeline Validation Tests
- `sqlite3` - Database queries
- Running listener process
- Active pool registrations in database
- WebSocket subscriptions live

---

## Database Schema Expected

The pipeline tests assume these tables exist:

```sql
-- Pool accounts
CREATE TABLE token_pool_accounts (
    mint TEXT,
    base_account TEXT,
    quote_account TEXT,
    pool_program TEXT,
    is_active INTEGER,
    created_at INTEGER,
    PRIMARY KEY (mint, base_account)
);

-- Price snapshots
CREATE TABLE token_price_snapshots (
    mint TEXT,
    base_account TEXT,
    price_usd REAL,
    liquidity_usd REAL,
    source TEXT,
    created_at INTEGER,
    PRIMARY KEY (mint, base_account, created_at)
);
```

---

## Common Issues and Fixes

### "Account not found on-chain"
This means the pool account address was never valid on-chain. Check:
1. Address format is correct (58 char base58)
2. Pool was not deleted from chain
3. RPC endpoint has the account in its state

### "No snapshots found"
This means the pipeline is not running. Check:
1. Listener process is running: `ps aux | grep pumpfun_curve_listener`
2. WebSocket subscriptions are active: Check `listener.log`
3. Price updater is enabled: Line 3079 of `pumpfun_curve_listener.py` should be `PRICE_UPDATER_ENABLED = True`

### "Snapshot rate too low"
The system is producing fewer than 40 snapshots/minute. Check:
1. Active pools count is reasonable (>50)
2. Recent snapshots exist (last 5 minutes)
3. No RPC rate limiting
4. Liquidity threshold is not too high

---

## Future Enhancements

1. **Pytest Integration** - Convert to pytest format for CI/CD
2. **Mock RPC Responses** - Test without hitting live RPC
3. **Stress Testing** - Validate 1000+ pools
4. **Performance Profiling** - Measure validation latency
5. **Alert Integration** - Send alerts when tests fail
6. **Historical Analysis** - Compare performance over time

---

## See Also

- [Pool Account Validator Module](../../src/core/pool_account_validator.py)
- [Pool Discovery Module](../../src/core/pool_discovery.py)
- [Price Worker Module](../../src/core/price_worker.py)
- [DEPLOYMENT_APPROVED.md](../../DEPLOYMENT_APPROVED.md)
- [PRODUCTION_VALIDATION_EVIDENCE.md](../../PRODUCTION_VALIDATION_EVIDENCE.md)
