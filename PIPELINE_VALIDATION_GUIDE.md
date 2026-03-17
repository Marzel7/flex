# Pipeline Validation Guide — End-to-End Testing with WebSocket Confirmation

**Date:** 2026-03-17
**Status:** ✅ Implemented and ready for use

---

## Overview

The Pipeline Validator provides comprehensive end-to-end validation of the complete pool discovery → registration → WebSocket → pricing pipeline with **delayed confirmation** to catch transient states and false positives.

### The Problem It Solves

Common false positives in integration testing:
1. **First update arrives, then subscription drops** — WebSocket shows ready but no persistent state
2. **Snapshots written briefly, then stop** — Cache updates but not sustained
3. **Reserves spike momentarily** — Update received but not stable

The delayed confirmation step catches these by:
- ✓ Waiting for first reserve update
- ✓ Waiting confirmation delay (default 5 seconds)
- ✓ Verifying reserves STILL present after delay
- ✓ Checking that snapshots exist AND persisted

---

## Architecture

### Core Components

#### 1. **PipelineValidationResult** (Dataclass)
```python
@dataclass
class PipelineValidationResult:
    mint: str
    base_account: str = None
    quote_account: str = None
    ws_ready: bool = False                    # ✓ First update received
    ws_confirmed: bool = False                # ✓ State confirmed after delay
    reserves_changed: bool = False            # Optional: did state change?
    first_reserves: Optional[Tuple] = None    # (base, quote) at first ready
    confirmed_reserves: Optional[Tuple] = None # (base, quote) after delay
    snapshot_source: Optional[str] = None     # 'pool' source
    snapshot_price_usd: Optional[float] = None # Latest price
    snapshot_count: int = 0                   # Number of snapshots since ready
    passed: bool = False                      # All checks passed?
    errors: List[str] = []                    # Validation failures
    total_elapsed_ms: int = 0                 # Total test duration
```

#### 2. **PoolStateStore** (In-memory WebSocket state)
```python
class PoolStateStore:
    get_reserves(mint, base_account) -> (base, quote) or None
    set_reserves(mint, base_account, base, quote) -> None
```

Keyed by `(mint, base_account)` to support multi-pool tokens correctly.

#### 3. **PipelineValidator** (Main validator)
```python
class PipelineValidator:
    async def validate_pool_pipeline(
        token_mint,
        timeout_seconds=10,
        confirmation_delay_seconds=5
    ) -> PipelineValidationResult
```

---

## Validation Phases

### Phase 0: Pool Registration Check
Verify pool exists in database:
```sql
SELECT mint, base_account, quote_account, pool_program, discovery_method
FROM token_pool_accounts
WHERE mint = ?
```

**Failure case:** Pool not found in database

### Phase 1: WebSocket Reserve Readiness
Poll WebSocket state store for reserve updates:
- Check every 0.1 seconds
- Wait up to `timeout_seconds` (default 10)
- Requires: `base_reserve > 0 AND quote_reserve > 0`

**Failure case:** No reserves received after timeout

**Success case:** `ws_ready = True`, `first_reserves = (base, quote)`

### Phase 2: Confirmation Delay (Delayed Confirmation)
Wait for `confirmation_delay_seconds` (default 5):
- Allows transient spikes to settle
- Catches "first update then drop" pattern
- Gives pricing engine time to write snapshots

**Failure case:** None (just waiting)

### Phase 3: WebSocket Persistence Check
Re-check reserves after delay:
- Get reserves again from state store
- Verify they still exist and are > 0
- Record if they changed: `reserves_changed = (first != confirmed)`

**Failure case:** Reserves disappeared or became 0

**Success case:** `ws_confirmed = True`, reserves still alive

### Phase 4: Snapshot Verification
Check for price snapshots in database:
```sql
SELECT price_usd, source, created_at
FROM token_price_snapshots
WHERE mint = ? AND source = 'pool'
ORDER BY created_at DESC
LIMIT 1
```

Also count snapshots since first readiness:
```sql
SELECT COUNT(*)
FROM token_price_snapshots
WHERE mint = ? AND source = 'pool'
  AND created_at >= ? -- first_ready_at - 1 second
```

**Failure cases:**
- No snapshots found at all
- Latest snapshot has `price_usd <= 0`
- No snapshots written after WebSocket readiness

**Success case:** ≥1 snapshot exists with valid price

---

## Usage

### Standalone Validation

```bash
# Validate a specific token
python3 src/core/pipeline_validator.py <MINT>

# With custom timeouts
python3 src/core/pipeline_validator.py <MINT> \
  --timeout 15 \
  --confirmation-delay 3
```

### In Replay Test Harness

```python
from replay_test_harness import ReplayTestHarness

harness = ReplayTestHarness("database/flex_complete_database.db")

# Validate a pool asynchronously
result = await harness.validate_pool_pipeline_async(
    mint="<TOKEN_MINT>",
    timeout_seconds=10,
    confirmation_delay_seconds=5,
)

# Check results
if result['passed']:
    print("✓ Full pipeline working")
else:
    print("✗ Failures:")
    for error in result['errors']:
        print(f"  - {error}")
```

### Assert Block Pattern

```python
result = await validator.validate_pool_pipeline(
    token_mint=mint,
    timeout_seconds=10,
    confirmation_delay_seconds=5,
)

assert result.ws_ready, f"WebSocket not ready: {result.errors}"
assert result.ws_confirmed, f"WebSocket not confirmed: {result.errors}"
assert result.snapshot_source == "pool", f"Wrong snapshot source: {result.snapshot_source}"
assert result.snapshot_count >= 1, f"Expected ≥1 snapshot, got {result.snapshot_count}"
```

---

## Test Results Interpretation

### ✅ PASS (All phases successful)
```
ws_ready: True                  ← Reserves received in Phase 1
ws_confirmed: True              ← Reserves persisted in Phase 3
snapshot_count: 2               ← Snapshots written in Phase 4
errors: []
```

**Meaning:** Full pipeline is working correctly. Pool discovery → registration → WebSocket subscription → price calculation all functioning.

### ❌ FAIL: Phase 1 (WebSocket Readiness)
```
ws_ready: False
errors: ["WebSocket: no reserve updates after 10s"]
```

**Meaning:**
- Pool registered ✓
- WebSocket subscription started ✓
- But no reserve updates received ✗

**Likely causes:**
- WebSocket connection lost
- Pool not in WebSocket subscription list
- RPC not returning account data
- State store not updated by listener

### ❌ FAIL: Phase 3 (WebSocket Confirmation)
```
ws_ready: True
ws_confirmed: False
first_reserves: (1000000, 500000)
confirmed_reserves: None
errors: ["WebSocket: reserves disappeared after 5s confirmation delay"]
```

**Meaning:**
- Reserves arrived initially ✓
- But disappeared after delay ✗
- Typical "false positive" scenario

**Likely causes:**
- WebSocket subscription dropped mid-stream
- Connection timeout
- State store cleared
- Pool removed from active subscriptions

### ❌ FAIL: Phase 4 (Snapshots)
```
ws_ready: True
ws_confirmed: True
snapshot_count: 0
errors: ["Price snapshot: no pool snapshot found"]
```

**Meaning:**
- WebSocket working ✓
- But pricing engine not writing snapshots ✗

**Likely causes:**
- Price calculation not triggered on reserve updates
- Snapshot table not created
- SQL error writing snapshots
- Price engine crashed or not running

---

## Production Readiness Criteria

Use these thresholds to determine when system is production-ready:

### Threshold 1: Pool Registration Success
```
✓ 100% of detected pools registered
✓ All required fields populated (pool_address, discovery_method, etc.)
```

### Threshold 2: WebSocket Readiness (Phase 1 + 3)
```
✓ ≥95% of new pools reach ws_ready=True within 10 seconds
✓ ≥95% maintain ws_confirmed=True after 5-second delay
```

### Threshold 3: Snapshot Production (Phase 4)
```
✓ ≥95% of pools have ≥1 pool-sourced snapshot
✓ Preferably ≥2 snapshots per pool (streaming confirmation)
```

### Threshold 4: Latency
```
✓ ws_ready latency: median <5 seconds, p99 <10 seconds
✓ snapshot write latency: <2 seconds after ws_ready
```

### Overall: Production Decision
```
When ALL of the above thresholds are met across ≥5 NEW pools:
✅ PRODUCTION READY - Deploy to all environments
```

---

## Integration with Current System

### Current Status (5 NEW pools)
```
Registration:  ✓ 100% (5/5 pools registered)
ws_ready:      ✓ Works (confirmed with simulated data)
ws_confirmed:  ✓ Works (confirmed persists after delay)
snapshots:     ✗ 0% (pricing engine not running)
```

### Action Plan

1. **Verify WebSocket subscription working:**
   ```bash
   python3 src/core/pipeline_validator.py <MINT> --timeout 10
   ```
   Should show `ws_ready=True` and `ws_confirmed=True` when WebSocket is flowing data.

2. **Enable pricing engine:**
   Once WebSocket data is flowing, run pricing worker to generate snapshots.

3. **Re-run validation:**
   ```bash
   python3 src/core/pipeline_validator.py <MINT>
   ```
   Should show `snapshot_count ≥ 1` and `passed=True`.

4. **Run batch validation across all NEW pools:**
   ```bash
   python3 replay_test_harness.py --group fresh_live
   ```
   Will show which pools have full pipeline working.

---

## Debugging Failed Validation

### If `ws_ready=False`:

1. Check WebSocket connection:
   ```bash
   tail -f /tmp/listener.log | grep -i websocket
   ```
   Look for: `✓ Connected`, `Subscribed`, not `disconnected`

2. Check pool in subscription:
   ```bash
   sqlite3 database/flex_complete_database.db \
     "SELECT base_account, quote_account FROM token_pool_accounts WHERE mint = ?"
   ```
   Verify these accounts are being monitored by listener.

3. Manually trigger reserve update:
   Test the state store directly in Python to ensure updates flow.

### If `ws_confirmed=False`:

1. Check WebSocket stability:
   ```bash
   grep -i "error\|disconnect\|timeout" /tmp/listener.log | tail -20
   ```

2. Monitor subscription events:
   ```bash
   grep "Subscription\|reconnect" /tmp/listener.log | tail -10
   ```

3. Check if reserves are being cleared:
   Inspect state store code for any clearing/reset logic.

### If `snapshot_count=0`:

1. Verify pricing engine is running:
   ```bash
   ps aux | grep price_worker
   ```

2. Check for SQL errors:
   ```bash
   grep -i "error\|exception" /tmp/price_worker.log 2>/dev/null
   ```

3. Verify token_price_snapshots table exists:
   ```bash
   sqlite3 database/flex_complete_database.db \
     ".schema token_price_snapshots"
   ```

4. Check if snapshots are being written for ANY pool:
   ```bash
   sqlite3 database/flex_complete_database.db \
     "SELECT COUNT(*) FROM token_price_snapshots"
   ```

---

## Example: Complete Test Sequence

```bash
# 1. Ensure listener is running
ps -p $(cat /tmp/listener.pid) || \
  (source .env && python3 -m src.core.pumpfun_curve_listener &)

# 2. Get a new pool to test
MINT=$(sqlite3 database/flex_complete_database.db \
  "SELECT mint FROM token_pool_accounts WHERE is_legacy=0 LIMIT 1")

echo "Testing pool: $MINT"

# 3. Validate discovery (pool registered)
python3 validation_harness.py --check registration --new-only

# 4. Validate WebSocket + snapshots (full pipeline)
python3 src/core/pipeline_validator.py "$MINT" --timeout 10 --confirmation-delay 5

# 5. Check results
if [ $? -eq 0 ]; then
  echo "✅ Full pipeline working for $MINT"
else
  echo "❌ Pipeline validation failed"
  tail -20 /tmp/listener.log
fi
```

---

## Summary

The Pipeline Validator provides:
- ✅ Comprehensive end-to-end validation
- ✅ Delayed confirmation to catch false positives
- ✅ Clear failure diagnostics
- ✅ Integration with replay test harness
- ✅ Production readiness thresholds

Use it to verify complete pipeline: discovery → registration → WebSocket → pricing

**Current Status:** 5 NEW pools with 100% discovery & registration; awaiting WebSocket/pricing validation.
