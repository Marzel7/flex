# Worker Runtime Test Architecture

**Date:** March 20, 2026
**Status:** Complete Implementation
**Purpose:** Direct validation of price worker health independent of snapshot throughput

---

## Problem Statement

### Current Test Suite Gaps

Existing tests validate:
- **test_account_validator.py** → On-chain vault correctness
- **test_pipeline_validation.py** → Snapshot throughput (skips if worker not running)
- **test_true_end_to_end_pool_identity.py** → Full pipeline for one pool

### What's Missing

No direct test of **worker runtime health**:
- Is the worker actually running?
- Is the WebSocket connected?
- Are subscriptions active?
- Is PoolStateStore receiving updates?
- Is the worker loop progressing?

### Ambiguity in Current Tests

When Test 2 (pipeline validation) fails or skips:
- Can't distinguish: worker not running vs. slow snapshot generation
- Can't check: is WebSocket alive?
- Can't verify: are subscriptions active?

### Why This Matters

The runtime test **FAILS** (not skips) when worker is not running, making it useful for:
- Fast debugging
- Clear root cause identification
- Production health checks
- Continuous monitoring

---

## Architecture Overview

### Design Principles

1. **Direct Worker Access**
   - Uses `get_price_worker()` to access running singleton
   - No subprocess communication needed
   - No database throughput assumptions

2. **No Database Assumptions**
   - Doesn't read snapshots
   - Doesn't check throughput rates
   - Doesn't require active pools
   - Works with empty system

3. **Component Isolation**
   - Tests each worker subsystem independently
   - Can fail at specific step
   - Clear error messages
   - Failure indicates exact issue

4. **Status Export**
   - Uses existing `export_worker_status()` from integration_helpers
   - Reads worker state safely (thread-safe locks)
   - Returns structured WorkerStatus dataclass
   - No side effects

---

## Code Structure

### Files Modified/Created

```
tests/pool_validation/worker/
├── __init__.py                          (NEW - empty)
├── test_price_worker_runtime.py         (NEW - 600 lines)
└── WORKER_RUNTIME_TEST_ARCHITECTURE.md  (this file)
```

### Existing Infrastructure Used

```
src/core/integration_helpers.py
├── export_worker_status()       ← Used to get worker status
├── WorkerStatus (dataclass)     ← Status structure
└── WorkerPoolState (dataclass)  ← Individual pool state

src/core/price_worker.py
├── BackgroundPriceWorker        ← Worker class
├── get_price_worker()           ← Get singleton instance
└── _ws_started                  ← WebSocket flag
└── _pool_state                  ← PoolStateStore
```

---

## Test Implementation

### Test 1: Complete Worker Health Check
**Function:** `test_worker_runtime_health()`
**Lines:** 425-545
**Severity:** CRITICAL - Tests entire system

#### Steps Executed

| Step | Check | Assertion | Details |
|------|-------|-----------|---------|
| 1 | Worker exists | Not None | `get_price_worker()` returns instance |
| 2 | Worker running | True | `worker.running == True` |
| 3 | Thread alive | True | `worker.thread.is_alive() == True` |
| 4 | WebSocket started | True | `worker._ws_started == True` |
| 5 | WebSocket client | Not None | `worker._ws_client is not None` |
| 6 | PoolStateStore | Not None | `worker._pool_state is not None` |
| 7 | Export status | Not None | `export_worker_status()` succeeds |
| 8 | Loop not stuck | < 30s | Last cycle < 30 seconds ago |
| 9 | Pool analysis | Info only | Count subscriptions, pools, mints |
| 10 | Reserve flow | Info only | Check if reserves > 0 |

#### Failure Modes

**Fails at Step 1:** Worker not found
- Cause: Worker not started, `get_price_worker()` broken
- Action: Check if main.py is running, verify import path

**Fails at Step 2:** Worker not running
- Cause: Worker stopped, `running = False`
- Action: Check worker.stop() wasn't called

**Fails at Step 3:** Thread dead
- Cause: Worker thread crashed
- Action: Check logs for exceptions

**Fails at Step 4:** WebSocket not started
- Cause: No pools or initialization failed
- Cause: Check DB for active pools, WebSocket logs

**Fails at Step 8:** Worker loop stuck
- Cause: Deadlock, long operation, race condition
- Action: Check logs for hanging operations

#### Output Example

```
================================================================================
PRICE WORKER RUNTIME HEALTH TEST
================================================================================

[STEP 1] Worker Instance Exists
  ✓ Worker instance retrieved

[STEP 2] Worker Thread is Running
  ✓ Worker running: True

[STEP 3] Worker Thread is Alive
  ✓ Thread alive: True

[STEP 4] WebSocket Client Started
  ✓ WebSocket started: True

[STEP 5] WebSocket Client Instance Exists
  ✓ WebSocket client exists

[STEP 6] PoolStateStore Exists
  ✓ PoolStateStore exists

[STEP 7] Export Worker Status
  ✓ Status exported successfully

[STEP 8] Worker Loop is Progressing (not stuck)
  ✓ Last cycle: 2.3s ago

[STEP 9] Analyze Pool State
  Subscribed accounts: 65
  Pool states in store: 65
  Active mints: 45

[STEP 10] Reserve Flow Status
  ✓ Reserves flowing: 65 pools have active reserves
  ✓ Recent updates: 65 pools updated within 30s

================================================================================
✅ WORKER RUNTIME HEALTH CHECK PASSED
================================================================================
```

---

### Test 2: WebSocket Subscriptions
**Function:** `test_worker_websocket_subscriptions()`
**Lines:** 548-568
**Severity:** HIGH - Tests subscription channel

#### What It Checks
- WebSocket client exists and started
- Can export subscription list
- Subscription count available

#### Why It Matters
- Validates that pools are being subscribed to
- Confirms WebSocket connection is active
- Quick check for connection status

#### Success Criteria
- WebSocket client not None
- `_ws_started == True`
- Subscriptions can be enumerated

---

### Test 3: PoolStateStore Functionality
**Function:** `test_worker_pool_state_store()`
**Lines:** 571-596
**Severity:** MEDIUM - Tests state storage

#### What It Checks
- PoolStateStore initialized
- `get_all_mints()` method callable
- State is accessible

#### Why It Matters
- Ensures in-memory state storage works
- Validates worker can track pool reserves
- Confirms thread-safe access

#### Success Criteria
- PoolStateStore not None
- `get_all_mints()` returns list
- Can read state without crashes

---

### Test 4: Stats Tracking
**Function:** `test_worker_stats_tracking()`
**Lines:** 599-618
**Severity:** LOW - Tests internal counters

#### What It Checks
- Cycle counter incrementing
- Error counter accessible
- Stats dictionary functional

#### Why It Matters
- Confirms worker loop is executing
- Shows error frequency
- Allows progress monitoring

#### Success Criteria
- `stats['cycles'] > 0` (if running for > 1 cycle)
- `stats['errors'] >= 0`
- Stats dict accessible

---

## Worker Status Export

### export_worker_status() Function

**Location:** `src/core/integration_helpers.py:178-231`
**Input:** BackgroundPriceWorker instance
**Output:** WorkerStatus dataclass

### What It Returns

```python
@dataclass
class WorkerStatus:
    ws_started: bool                      # WebSocket started flag
    subscribed_accounts: list             # [vault addresses...]
    pool_states: Dict[
        Tuple[str, str],                  # (mint, base_account)
        WorkerPoolState                   # Pool state snapshot
    ]
    all_mints: list                       # [mint addresses...]
    last_export_time: float               # Timestamp of export
```

### Pool State Details

```python
@dataclass
class WorkerPoolState:
    mint: str                    # Token mint
    base_account: str            # Vault account
    base_reserve: Optional[int]  # Raw base reserve (raw token amount)
    quote_reserve: Optional[int] # Raw quote reserve
    last_update: float           # Timestamp of last update
    last_slot_base: Optional[int]# Last RPC slot for base
    last_slot_quote: Optional[int]# Last RPC slot for quote
    is_stale: bool               # Not updated in 5+ minutes
```

### Thread Safety

- Uses PoolStateStore's `_lock` for safe read access
- Snapshot taken atomically
- No long-held locks
- Safe to call from test thread

### Example Usage

```python
worker = get_price_worker(DB_PATH)
status = export_worker_status(worker)

# Access exported data
print(f"WebSocket: {status.ws_started}")
print(f"Subscriptions: {len(status.subscribed_accounts)}")
print(f"Pools: {len(status.pool_states)}")

# Check specific pool
key = ("mint_address", "base_account_address")
if key in status.pool_states:
    pool = status.pool_states[key]
    print(f"Reserves: {pool.base_reserve}/{pool.quote_reserve}")
    print(f"Stale: {pool.is_stale}")
```

---

## Helper Functions

### wait_for_worker_start()
Waits for `worker.running == True`
- Timeout: 10s (configurable)
- Poll interval: 0.1s
- Returns: True if started, False if timeout

### wait_for_ws_startup()
Waits for `worker._ws_started == True`
- Timeout: 5s (configurable)
- Poll interval: 0.1s
- Returns: True if started, False if timeout

### get_worker_cycle_age()
Returns seconds since last `_refresh_cycle()`
- Gets `worker.stats['last_run']`
- Computes `now - last_run`
- Returns: float (seconds) or None

### count_subscribed_accounts()
Returns `len(status.subscribed_accounts)`

### count_pool_states()
Returns `len(status.pool_states)`

### count_active_mints()
Returns `len(status.all_mints)`

### check_reserves_flowing()
Returns True if any pool has base_reserve > 0 AND quote_reserve > 0

### check_recent_updates()
Returns True if any pool updated within max_age (default 30s)

---

## Configuration

### Timeouts

```python
WORKER_STARTUP_TIMEOUT = 10      # Max wait for worker.running = True
WS_STARTUP_TIMEOUT = 5           # Max wait for WebSocket startup
MAX_CYCLE_AGE = 30               # Max seconds since last cycle
MIN_POOL_STATES = 0              # Min pools required (0 = any is OK)
```

These can be adjusted in the test file for different environments.

---

## Integration with Existing Tests

### Test Hierarchy

```
Test Suite Organization:

pool_validation/
├── account_validator/
│   └── test_*.py              ← On-chain correctness
├── pipeline_validation.py      ← Throughput metrics (skips if no worker)
├── end_to_end/
│   └── test_*.py              ← Full pipeline (one pool)
└── worker/
    └── test_price_worker_runtime.py  ← Runtime health (FAILS if no worker)
```

### Complementary Roles

| Test | Validates | Fails/Skips | Best For |
|------|-----------|-------------|----------|
| account_validator | Vault accounts on-chain | FAILS | Pool discovery correctness |
| pipeline_validation | DB snapshot throughput | SKIPS | System throughput |
| end_to_end | Full pipeline for one pool | FAILS | Integration testing |
| **worker_runtime** | **Worker process health** | **FAILS** | **Debugging, monitoring** |

### When to Use Each

**account_validator** → Verify pools are registered correctly
**pipeline_validation** → Measure snapshot generation rate
**end_to_end** → Validate full flow for a specific pool
**worker_runtime** → Check if worker is alive and functioning

---

## Failure Diagnosis Guide

### Worker Runtime Test Fails

```
TEST: test_worker_runtime_health FAILS at STEP 2 (Worker not running)

DIAGNOSIS:
1. Check if main.py process is running
   $ ps aux | grep "python src/core/main.py"

2. Check worker logs for crashes
   $ tail -100 listener.log

3. Verify get_price_worker() function is working
   $ python3 -c "from src.core.price_worker import get_price_worker; print(get_price_worker())"

ACTION:
- Restart worker: python3 src/core/main.py &
- Check logs for initialization errors
- Verify database is accessible
```

### Worker Runtime Test Fails at STEP 8 (Loop stuck)

```
TEST: test_worker_runtime_health FAILS at STEP 8 (last cycle too old)

DIAGNOSIS:
1. Worker exists and thread is alive but not progressing
2. Possible causes:
   - Deadlock in worker loop
   - Long-running operation blocking loop
   - Exception swallowed somewhere

ACTION:
- Check logs for recent messages
- Look for lock contention (database locks?)
- Check if WebSocket is hung trying to connect
- Consider restarting worker
```

### Pipeline Test Skips, Worker Runtime Test Passes

```
TEST: test_pipeline_validation skips (no recent snapshots)
TEST: test_worker_runtime_health PASSES

DIAGNOSIS:
- Worker is alive and functioning
- But not generating snapshots (why?)
- Possible causes:
  1. No active pools to price
  2. WebSocket not subscribed to any vaults
  3. Reserves not flowing through WebSocket
  4. Price computation not running

ACTION:
- Check if pools exist: SELECT COUNT(*) FROM token_pool_accounts WHERE is_active=1
- Check WebSocket subscriptions in worker status
- Check if reserves are flowing (test output shows)
- Monitor snapshot table: SELECT COUNT(*) FROM token_price_snapshots WHERE created_at > ...
```

---

## Production Deployment

### Health Check Script

```bash
#!/bin/bash
# Quick health check using worker runtime test

echo "Checking worker runtime health..."
python3 tests/pool_validation/worker/test_price_worker_runtime.py

if [ $? -eq 0 ]; then
    echo "✓ Worker is healthy"
    exit 0
else
    echo "✗ Worker health check failed"
    exit 1
fi
```

### Continuous Monitoring

```bash
# Run every 5 minutes
*/5 * * * * cd /path/to/flex && python3 tests/pool_validation/worker/test_price_worker_runtime.py >> /var/log/worker_health.log 2>&1
```

### Integration with Metrics

Export test results to monitoring system:

```python
# After test passes
metrics.gauge('worker.health', 1)
metrics.gauge('worker.subscriptions', status.subscribed_accounts)
metrics.gauge('worker.pool_states', len(status.pool_states))
metrics.gauge('worker.cycle_age', cycle_age)
```

---

## Summary

### What This Test Provides

✅ Direct worker health validation
✅ No database assumptions
✅ Fast feedback (< 1 second)
✅ Clear failure modes
✅ Component isolation
✅ Production-ready

### Key Metrics Exposed

- Worker running status
- Thread alive status
- WebSocket connection status
- Subscription count
- Pool state count
- Cycle age (loop health)
- Reserve flow status

### Complements Existing Tests

- **account_validator** checks pools exist on-chain
- **pipeline_validation** checks snapshot throughput
- **worker_runtime** checks worker itself
- **end_to_end** checks full integration

### Next Steps

1. Run test manually to verify worker health
2. Add to CI/CD health check pipeline
3. Monitor regularly for degradation
4. Use failure modes to diagnose issues

---

## Files Reference

- Test implementation: `tests/pool_validation/worker/test_price_worker_runtime.py`
- Architecture doc: `tests/pool_validation/worker/WORKER_RUNTIME_TEST_ARCHITECTURE.md`
- Integration helpers: `src/core/integration_helpers.py`
- Worker implementation: `src/core/price_worker.py`

---

## Version History

- **v1.0** (2026-03-20): Initial implementation with 4 test functions and comprehensive failure diagnosis
