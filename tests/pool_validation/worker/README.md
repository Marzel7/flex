# Worker Runtime Tests

Quick validation of the price worker's real-time health.

## Quick Start

```bash
# Run complete worker health check
python3 tests/pool_validation/worker/test_price_worker_runtime.py

# Run with pytest
python3 -m pytest tests/pool_validation/worker/test_price_worker_runtime.py -v

# Run single test
python3 -m pytest tests/pool_validation/worker/test_price_worker_runtime.py::test_worker_runtime_health -v
```

## What It Tests

| Test | Purpose | Pass Criteria |
|------|---------|---------------|
| `test_worker_runtime_health()` | Complete worker health | All 8 steps pass |
| `test_worker_websocket_subscriptions()` | WebSocket active | Client started + not None |
| `test_worker_pool_state_store()` | State store functional | Can call get_all_mints() |
| `test_worker_stats_tracking()` | Stats incrementing | cycles > 0 |

## Key Differences from pipeline_validation

| Aspect | pipeline_validation | worker_runtime |
|--------|-------------------|-----------------|
| **Tests** | Snapshot throughput | Worker process health |
| **Depends on** | DB, snapshots | Worker instance only |
| **No active pools** | Skips gracefully | Still validates |
| **Worker down** | Skips | FAILS |
| **Use case** | Measure performance | Debug issues |

## Typical Output

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

## Failure Scenarios

### Worker not running
```
AssertionError: Worker.running = False, worker not started
```
**Action:** Start worker with `python3 src/core/main.py &`

### Thread crashed
```
AssertionError: Worker thread not alive: is_alive=False
```
**Action:** Check logs for exceptions, restart worker

### WebSocket not connected
```
AssertionError: WebSocket client not started after timeout
```
**Action:** Verify pools exist in DB, check WebSocket logs

### Worker loop stuck
```
AssertionError: Worker loop appears stuck (last cycle 45.2s ago, max 30s)
```
**Action:** Check logs for long-running operations, restart worker

## Integration with Test Suite

```
Test Flow:

1. account_validator
   ├─ Validates vaults exist on-chain
   └─ Status: PASS = pools OK on-chain

2. worker_runtime (NEW)
   ├─ Validates worker is alive
   └─ Status: PASS = worker OK, FAIL = worker down

3. pipeline_validation
   ├─ Validates snapshot throughput
   └─ Status: PASS = throughput OK, SKIP = no snapshots

4. end_to_end
   ├─ Validates full pipeline for one pool
   └─ Status: PASS = integration OK
```

## What Each Test Mode Means

### All Tests Pass
```
✅ account_validator ......... Pool discovery working
✅ worker_runtime ........... Worker alive and healthy
✅ pipeline_validation ....... Snapshots generating at expected rate
✅ end_to_end ............... Full pipeline validated
```
**Meaning:** System fully operational

### worker_runtime FAILS, pipeline_validation SKIPS
```
❌ worker_runtime ........... Worker is not running or crashed
⊘ pipeline_validation ....... Skipped because no snapshots
```
**Meaning:** Worker needs to be restarted

### worker_runtime PASSES, pipeline_validation SKIPS
```
✅ worker_runtime ........... Worker is running
⊘ pipeline_validation ....... No snapshots in last 15s (worker slow/no pools)
```
**Meaning:** Worker alive but not generating snapshots yet

### worker_runtime PASSES, pipeline_validation FAILS
```
✅ worker_runtime ........... Worker is running
❌ pipeline_validation ....... Snapshots too low (< 40/min)
```
**Meaning:** Worker running but undershooting performance target

## Configuration

Edit these constants in `test_price_worker_runtime.py`:

```python
WORKER_STARTUP_TIMEOUT = 10      # Max wait for worker.running = True
WS_STARTUP_TIMEOUT = 5           # Max wait for WebSocket startup
MAX_CYCLE_AGE = 30               # Max seconds since last cycle
DB_PATH = "database/flex_complete_database.db"
```

## Architecture

See [WORKER_RUNTIME_TEST_ARCHITECTURE.md](WORKER_RUNTIME_TEST_ARCHITECTURE.md) for:
- Complete implementation details
- Failure diagnosis guide
- Integration patterns
- Production deployment

## Files

```
tests/pool_validation/worker/
├── __init__.py                            (empty module init)
├── test_price_worker_runtime.py           (main tests - 600 lines)
├── WORKER_RUNTIME_TEST_ARCHITECTURE.md    (detailed design)
└── README.md                              (this file)
```

## Status

✅ Implemented
✅ Tested
✅ Documented
✅ Ready for production
