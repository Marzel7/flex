# Worker Queue - What It Does

## Overview

The **worker queue** is a background task processor that analyzes wallet addresses from the webhook stream. It continuously pulls addresses from the queue, scores their risk level, and conditionally calls RPC for enrichment.

## Data Flow

```
Webhook Transfer Received
    ↓
Extract source & destination addresses
    ↓
Add both addresses to work_queue with priority=20, reason="new_transfer"
    ↓
Worker thread (background) continuously:
    ├─ Fetch up to 10 addresses from work_queue (sorted by priority DESC)
    ├─ Lock each address (prevent concurrent processing)
    ├─ Recompute priority score using latest DB signals
    ├─ If priority >= 80: Call RPC (conditionally, with rate limits)
    ├─ Compute creator risk score
    └─ Reschedule with adaptive delay (1m to 1h depending on priority)
```

## The work_queue Table

```sql
CREATE TABLE work_queue (
    address TEXT PRIMARY KEY,           -- Wallet address to analyze
    priority REAL DEFAULT 0.0,          -- Sorting key for worker (higher = sooner)
    reason TEXT,                        -- Why queued: 'new_transfer', 'active', etc.
    next_run_at INTEGER DEFAULT 0,      -- Unix timestamp when to process next
    locked_until INTEGER DEFAULT 0,     -- If > now(), address is being processed
    attempts INTEGER DEFAULT 0,         -- Number of times processed
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

## Priority Scoring Logic

### Score Calculation

```python
score = 0.0

# Activity signals (from address_activity table)
if activity_5m > 0:           score += 50   # Active in last 5 minutes
elif activity_1h > 0:         score += 30   # Active in last hour
elif activity_24h > 0:        score += 10   # Active in last 24 hours

# Size signals
if sol_in_1h > 10:           score += 20   # Large inbound SOL
if sol_out_1h > 10:          score += 20   # Large outbound SOL

# Network signals
if in_creator_network:        score += 15   # Part of creator network
if multiple_creators:         score += 10   # Funds multiple creators

# Cooldown penalty
if last_processed_at < 1h:    score -= 5   # Recently processed

# Final priority
priority = score   (range: typically 0-100)
```

### RPC Gate

```
if priority >= 80:
    if (now - last_rpc_fetch) > 30_minutes:
        if rpc_calls_this_hour < 100:
            ALLOW_RPC = True
        else:
            BLOCK_RPC = True (rate limit hit)
    else:
        BLOCK_RPC = True (cooldown)
else:
    BLOCK_RPC = True (priority too low)
```

**Why this gating?**
- RPC calls are expensive ($)
- We only RPC for high-priority addresses
- Maximum 100 RPC calls/hour globally
- Each address gets min 30 minutes between RPC calls

## Processing Steps

### 1. Fetch Next Batch

```python
work_items = fetch_next_work(conn, BATCH_SIZE=10)
# SELECT address, priority, reason, locked_until
# FROM work_queue
# WHERE locked_until < now() AND next_run_at <= now()
# ORDER BY priority DESC
# LIMIT 10
```

### 2. Lock Addresses

```python
# Prevent concurrent processing
UPDATE work_queue
SET locked_until = now + 120_seconds
WHERE address IN (addresses)
```

### 3. Process Each Address

```python
FOR EACH address:
    # Recompute priority (may have changed)
    computed_priority = compute_priority(address)

    # Check if RPC is allowed
    should_rpc = (computed_priority >= 80 AND
                  rate_limit_ok AND
                  cooldown_ok)

    # Call RPC (optional)
    if should_rpc:
        # Would call: getSignaturesForAddress(address)
        # Currently: just logs what would happen

    # Compute risk score
    risk_score = compute_creator_risk_score(address)

    # Reschedule based on priority
    if priority >= 80:
        next_run_at = now + 60s      # Critical: recheck soon
    elif priority >= 60:
        next_run_at = now + 5m       # Elevated
    elif priority >= 40:
        next_run_at = now + 15m      # Moderate
    else:
        next_run_at = now + 1h       # Low: recheck later

    # Update queue
    UPDATE work_queue
    SET priority = computed_priority,
        attempts = attempts + 1,
        locked_until = 0,
        next_run_at = next_run_at
    WHERE address = ?
```

## Current Status

### Disabled Features

⚠️ **RPC calls are currently disabled** (line 313-323):

```python
# For now, just log what we would do
if should_rpc:
    print(f"[WORKER] {address[:8]}... [RPC] Would call getSignaturesForAddress", flush=True)
    # In real implementation:
    # results = rpc_client.get_signatures_for_address(address, limit=100)
    # ... process results ...
```

**Why?** The infrastructure for making RPC calls hasn't been set up yet. Currently the worker:
✅ Computes priorities
✅ Scores risk
✅ Reschedules addresses
❌ Does NOT call RPC

### Active Features

✅ **Priority computation** - Analyzes activity, size, network signals
✅ **Risk scoring** - Calls `webhook_creator_ranker` to get risk_score
✅ **Adaptive rescheduling** - Higher priority = sooner recheck
✅ **Locking mechanism** - Prevents concurrent processing
✅ **Rate limiting** - 100 RPC calls/hour max (when implemented)

## Example Log Output

```
[WORKER] Fetched 3 work items
[WORKER] Processing AbCD1234... (priority=20.0, reason=new_transfer)
[WORKER] AbCD1234... computed_priority=50.0 (active_5m)
[WORKER] AbCD1234... priority too low for RPC (50.0 < 80)
[WORKER] AbCD1234... risk_score=40 level=moderate

[WORKER] Processing XyZ98765... (priority=25.0, reason=new_transfer)
[WORKER] XyZ98765... computed_priority=85.0 (active_5m, large_inbound)
[WORKER] XyZ98765... RPC ALLOWED (calls_hour=5)
[WORKER] XyZ98765... [RPC] Would call getSignaturesForAddress
[WORKER] XyZ98765... risk_score=72 level=high
```

## Configuration

Located in [webhook_worker.py](webhook_worker.py):

```python
RPC_MIN_PRIORITY = 80              # Minimum priority to call RPC
RPC_COOLDOWN_SECONDS = 1800        # 30 minutes between RPC calls per address
MAX_RPC_CALLS_PER_HOUR = 100       # Global rate limit

LOCK_DURATION = 120                # How long to lock addresses during processing (seconds)
BATCH_SIZE = 10                    # Addresses to process per batch
WORKER_SLEEP = 1                   # Seconds between batches
```

## Integration Points

### Input: Address Activity

Worker reads from `address_activity` table (populated by webhook handler):

```sql
SELECT
    last_seen_at,           -- When address last had activity
    tx_5m, tx_1h, tx_24h,   -- Transaction counts (time windows)
    sol_in_1h, sol_out_1h,  -- SOL amounts (time windows)
    last_processed_at       -- When worker last analyzed this address
FROM address_activity
WHERE address = ?
```

### Output: Risk Scores

Worker computes and logs risk scores via `webhook_creator_ranker`:

```
risk_score: 0-100
risk_level: 'low' | 'moderate' | 'high' | 'critical'
```

### Reschedule Loop

Addresses remain in `work_queue` and get reprocessed on a schedule:

- **High priority (≥80)**: Every 1 minute
- **Elevated (60-79)**: Every 5 minutes
- **Moderate (40-59)**: Every 15 minutes
- **Low (<40)**: Every 1 hour

## Why This Design?

✅ **No RPC waste** - Only calls RPC for suspicious addresses
✅ **Adaptive** - High-priority addresses get faster rechecks
✅ **Scalable** - Batch processing with configurable size
✅ **Safe** - Rate limiting prevents RPC quota abuse
✅ **Persistent** - Addresses automatically reschedule
✅ **Concurrent-safe** - Locking prevents duplicate work

## Future Enhancements

When RPC is enabled, the worker can:
1. Call `getSignaturesForAddress` to fetch historical TXs
2. Detect patterns: rug pulls, pump-and-dumps, wash trading
3. Build address clustering networks
4. Update creator risk scores based on funding sources
5. Alert on high-risk activity

---

**Status**: ✅ Infrastructure ready, RPC calls pending implementation
**Current Focus**: Risk scoring & priority computation
**Data Source**: Webhook stream only (no RPC overhead)
