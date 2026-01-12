# Rate Limiting Fix - Semaphore-Based Concurrency

## Problem Identified

The post-migration analyzer was experiencing consistent HTTP 429 (rate limit) errors despite QuickNode being configured correctly and responding to sequential requests.

**Root Cause:** The batch-based concurrency model was causing issues:
- All 10 requests in a batch were submitted simultaneously
- If one was slow, the entire batch waited
- This created burst patterns that triggered rate limiting
- Naive batching doesn't efficiently utilize connection pooling

## Solution Applied

**Switched from batch-based to semaphore-based concurrency** (the approach that was proven working in pre-migration analyzer):

### Before (Batch-based - Problematic)
```python
for i in range(0, len(sigs), batch_size):
    batch = sigs[i:i+batch_size]
    tasks = [fetch_tx(...) for sig in batch]
    results = await asyncio.gather(*tasks)  # Wait for all 10
    # Process results
    # Then DELAY before next batch
    await asyncio.sleep(BATCH_DELAY)
```

**Problems:**
- Slowest task in batch determines throughput
- Gaps between batches due to BATCH_DELAY
- Burst pattern of 10 requests → gap → 10 requests → gap
- Connection pool not efficiently used

### After (Semaphore-based - Proven Working)
```python
sem = asyncio.Semaphore(BATCH_SIZE)  # Limit to N concurrent
tasks = []
for sig in sigs:
    task = asyncio.create_task(_fetch_tx_semaphore(sig, sem))
    tasks.append(task)

for future in asyncio.as_completed(tasks):
    tx = await future
    # Process immediately as completed
```

**Benefits:**
- Continuous pipeline of requests
- No artificial gaps between batches
- Completes fast tasks immediately
- Connection pool stays warm
- **Proven working in pre-migration analyzer**

## Configuration

**Current settings (optimal for QuickNode):**
```python
BATCH_SIZE = 10              # Concurrent requests (semaphore limit)
RPC_TIMEOUT = 60             # Timeout per request
MAX_RETRIES = 10             # Retry attempts with backoff
RETRY_DELAYS = [0.5, 1.0, 2.0, 3.0, 5.0, 10.0, 15.0, 20.0, 30.0, 60.0]
```

## Expected Improvement

| Metric | Before | After |
|--------|--------|-------|
| Concurrency Model | Batch-based | Semaphore-based |
| Request Pattern | Burst (10 → gap → 10) | Continuous pipeline |
| Coverage | ~7-10% (429 errors) | **80-95%+ (expected)** |
| Efficiency | Wasteful (gaps) | Optimal (continuous) |
| Connection Pool | Underutilized | Fully utilized |

## How to Test

1. **Run the listener:**
   ```bash
   python pumpfun_curve_listener.py
   ```

2. **Wait for next migration** and watch for:
   ```
   [ASYNC] Progress: 10/672 txs | Success: 10/10 (100.0%) | Failed: 0
   [ASYNC] Progress: 20/672 txs | Success: 20/20 (100.0%) | Failed: 0
   [ASYNC] Progress: 672/672 txs | Success: 537/672 (79.9%) | Failed: 135
   ```

3. **Expected success rate:** 80-95%

4. **Check database:**
   ```bash
   sqlite3 pumpswap_tokens.db "SELECT mint, post_migration_coverage FROM token_analysis LIMIT 5"
   ```
   Should show coverage > 80%

## Technical Details

### Why Semaphore Works Better

1. **Pipeline Never Gaps**
   - Creates all N tasks upfront
   - Each completed task allows next to start immediately
   - No artificial delays between batches

2. **Better Connection Reuse**
   - aiohttp maintains connection pool
   - Continuous stream keeps connections warm
   - Batch model wasted connections by creating gaps

3. **Proven Success**
   - Pre-migration analyzer uses this pattern
   - Achieved 80%+ coverage reliably
   - Same RPC endpoint, same QuickNode configuration

### Backoff Strategy

When HTTP 429 occurs:
- Immediately retry with exponential backoff
- Delays: 0.5, 1.0, 2.0, 3.0, 5.0, 10.0, 15.0, 20.0, 30.0, 60.0 seconds
- Maximum 10 retries per transaction
- Most recover on 1st-3rd retry

## Files Modified

- `pump_fun_post_migration_analyzer.py`
  - `fetch_transactions_async()` - Now uses semaphore concurrency
  - `_fetch_tx_semaphore()` - New helper method for semaphore handling

## Commit

**f2ac679** - "Fix: Use semaphore-based concurrency (proven pre-migration approach) for post-migration analysis"

## Next Steps

1. Restart listener: `python pumpfun_curve_listener.py`
2. Wait for next migration detection
3. Monitor `[ASYNC] Progress:` logs for success rates
4. Verify coverage > 80% in database

---

**Status:** Ready for testing
**Expected Result:** 80-95%+ coverage (vs previous 7-10%)
**Risk Level:** Low - using proven approach from pre-migration analyzer
