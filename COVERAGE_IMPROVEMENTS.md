# Analysis Coverage Improvements

## Problem
Analysis Coverage was showing only **6.0%**, meaning only ~6% of available transactions were being successfully fetched and analyzed.

## Root Cause
The analyzer was being too aggressive with RPC requests:
- **BATCH_SIZE=10**: 10 concurrent requests hitting rate limits (15 req/sec limit)
- **RPC_TIMEOUT=30**: Too short for slow RPC responses and indexing delays
- **MAX_RETRIES=7**: Not enough attempts to recover from rate limiting
- **Limited backoff**: Only delayed up to 15 seconds

## Solution
Optimized the analyzer for **conservative, rate-limit-aware operation**:

### Configuration Changes

#### Before
```python
BATCH_SIZE = 10              # Too many concurrent requests
RPC_TIMEOUT = 30             # Too aggressive
MAX_RETRIES = 7              # Not enough attempts
RETRY_DELAYS = [0.5, 1.0, 2.0, 3.0, 5.0, 10.0, 15.0]  # Max 15s
```

#### After
```python
BATCH_SIZE = 3               # Conservative batch size (safe for 15 req/sec)
RPC_TIMEOUT = 60             # Allows slow responses to complete
MAX_RETRIES = 10             # More attempts for better recovery
RETRY_DELAYS = [1.0, 2.0, 3.0, 5.0, 8.0, 13.0, 20.0, 30.0, 45.0, 60.0]  # Up to 60s
BATCH_DELAY = 0.5            # NEW: Pause between batches
```

### Key Improvements

1. **Batch Size Reduction (10 → 3)**
   - Reduces concurrent requests by 66%
   - Prevents hitting rate limits with initial requests
   - Leaves room for retries without overwhelming the endpoint

2. **Timeout Increase (30s → 60s)**
   - Accounts for RPC indexing delays on newly-confirmed transactions
   - Reduces false timeouts during high-load periods
   - Allows slower responses to complete

3. **Enhanced Retry Logic (7 → 10 attempts)**
   - More attempts to recover from transient failures
   - Longer backoff strategy (up to 60s vs 15s)
   - Better handles sustained rate limiting

4. **Batch Delay (New: 0.5s)**
   - Spreads requests over time
   - Gives RPC endpoint time to process
   - Prevents batch requests from overwhelming the connection

5. **Better Error Handling**
   - Recognizes more RPC error codes
   - Distinguishes between transient and permanent errors
   - Shows detailed retry messages with timing

## Expected Improvements

| Metric | Before | After |
|--------|--------|-------|
| Coverage | 6% | 80-95% |
| Concurrent Requests | 10 | 3 |
| Max Timeout | 30s | 60s |
| Max Retries | 7 | 10 |
| Max Backoff Delay | 15s | 60s |

## How to Verify

1. **Run the listener:**
   ```bash
   bash run_listener.sh
   ```

2. **Watch for new migrations:**
   Look for `[WEBSOCKET] 🚨 Migration detected` messages

3. **Check progress logs:**
   Look for `[ASYNC] Progress:` messages showing success rates

4. **Verify coverage:**
   Should see much higher than 6% when analysis completes

## Log Examples

**Good progress:**
```
[ASYNC] Progress: 3/1000 txs | Success: 3/3 (100.0%) | Failed: 0
[ASYNC] Progress: 6/1000 txs | Success: 6/6 (100.0%) | Failed: 0
[ASYNC] Progress: 9/1000 txs | Success: 8/9 (88.9%) | Failed: 1
```

**Retry messages:**
```
[FETCH_TX] 📝 Retrying XYZ... (RPC error -32008: ..., attempt 1/10, waiting 1.0s)
[FETCH_TX] 📝 Transaction not indexed yet for XYZ... (attempt 2/10, waiting 2.0s)
[FETCH_TX] ✓ Successfully fetched after retries
```

## Technical Details

### Rate Limiting Strategy
- QuickNode free tier: 15 requests/second
- Batch size 3 + 0.5s delay = safe operational window
- Conservative approach prevents rate limit violations

### Backoff Strategy
- Fibonacci-like delays: 1, 2, 3, 5, 8, 13, 20, 30, 45, 60 seconds
- Each retry gets longer backoff
- Final delay of 60s gives time for RPC to recover

### Error Recovery
- **Transient errors** (429, 5xx): Retried with backoff
- **Indexing delays**: Retried multiple times
- **Timeouts**: Treated as transient, retried
- **Permanent errors**: Logged and skipped

## Files Modified

- `pump_fun_post_migration_analyzer.py`: Configuration and retry logic
- `test_analyzer_coverage.py`: NEW test script

## Commit

`dc5670b` - "Fix: Improve analysis coverage by optimizing RPC request handling and rate limiting"
