# Growth Management – Integration with Existing Code

**How to integrate the tiered scheduler with pumpfun_curve_listener**

---

## Step 1: Update pumpfun_curve_listener.py

Replace the old 12-hour scan task with the scheduler.

**Location**: pumpfun_curve_listener.py, approximately line 2083

**Before**:
```python
asyncio.create_task(run_outgoing_extractor(interval_seconds=43200))
```

**After**:
```python
from creator_outgoing_scheduler import scheduler_loop_async

asyncio.create_task(scheduler_loop_async(tick_interval=60, limit=50))
```

---

## Step 2: New Creators Auto-Enrollment

When a new token is detected, the creator is automatically enrolled in the scheduler.

**Location**: pumpfun_curve_listener.py, around line 1728 where `extract_funding_for_new_token()` is called

**Already compatible** – No changes needed. The scheduler's `get_due_creators()` queries the database directly.

**Optional: Explicit enrollment**

If you want to ensure a creator is in the state table immediately:

```python
from creator_outgoing_scan_state import ensure_creator_in_state

# After extracting creator from new token
ensure_creator_in_state(creator_address)
```

---

## Step 3: Optional – Add Budget Monitoring Endpoint

In main.py (Flask), add endpoints for monitoring:

```python
@app.route("/api/scan-state/due", methods=["GET"])
def api_scan_state_due():
    """Get creators due for scan in next hour"""
    try:
        from creator_outgoing_scan_state import get_due_creators
        due = get_due_creators(limit=100)
        return jsonify({
            "due_creators": [
                {
                    "creator_address": c,
                    "tier": t,
                    "max_pages": p,
                }
                for c, t, p in due
            ],
            "total": len(due)
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/scan-state/budget", methods=["GET"])
def api_scan_state_budget():
    """Get daily budget status"""
    try:
        from creator_outgoing_scan_state import get_daily_budget_status
        return jsonify(get_daily_budget_status())
    except Exception as e:
        return jsonify({"error": str(e)}), 500
```

---

## Step 4: Set Budget (Optional)

In your environment or startup script:

```bash
# Unlimited (default)
unset OUTGOING_DAILY_BUDGET_CREDITS

# Or limit to 50,000 credits/day
export OUTGOING_DAILY_BUDGET_CREDITS="50000"
```

---

## Step 5: Run Migration Once

Before starting the scheduler, ensure the schema exists:

```python
from creator_outgoing_scan_state import migrate_creator_scan_state

# Call once at startup
migrate_creator_scan_state()
```

**Automatically handled** if you call `scheduler_loop_async()` (it calls migrate internally).

---

## Step 6: Verify Integration

```bash
# Start listener (which now includes scheduler)
python pumpfun_curve_listener.py

# Watch logs for scheduler output
tail -f /path/to/output | grep SCHEDULER

# Should see every 60 seconds:
# [SCHEDULER] 🔄 Processing N due creators (X hot, Y warm, Z cold)
# [SCHEDULER] ✅ creator_address... (tier) changed: N sigs, X cr
# [SCHEDULER] 📊 Tick N: M creators, C credits (elapsed: Ts)
```

---

## Code Interaction Diagram

```
pumpfun_curve_listener.py
├─ Existing: WebSocket → new tokens
├─ Existing: extract_funding_for_new_token()
├─ NEW: scheduler_loop_async() [every 60s]
│       └─ get_due_creators()
│           └─ query creator_scan_state
│
creator_outgoing_scheduler.py
├─ process_due_creators(limit=50)
│   ├─ scan_creator_with_change_detection()
│   │   ├─ load_creator_state()
│   │   ├─ rpc_get_signatures()  [existing]
│   │   ├─ helius_enhanced_parse()  [existing]
│   │   ├─ extract_outgoing_sol()  [existing]
│   │   ├─ insert_outgoing_rows()  [existing]
│   │   └─ update_creator_state_after_scan()
│   │
│   └─ build_funding_chains_incremental()  [existing]
│   └─ build_coordinated_edges_incremental()  [existing]
│
creator_outgoing_scan_state.py
└─ Manages creator_scan_state table
    ├─ Per-creator: before_sig, last_head_sig, tier, next_scan_time
    ├─ Budget checking
    └─ Tier auto-assignment
```

---

## Database Schema (New Table Only)

```sql
CREATE TABLE creator_scan_state (
  creator_address TEXT PRIMARY KEY,
  before_signature TEXT,              -- pagination cursor for next page
  last_head_signature TEXT,           -- newest signature from last scan
  last_scan_time TIMESTAMP,           -- when we last scanned this creator
  next_scan_time TIMESTAMP,           -- when next scan is due
  tier TEXT DEFAULT 'warm',           -- hot (<=24h) | warm (1-7d) | cold (>7d)
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_css_tier ON creator_scan_state(tier);
CREATE INDEX idx_css_next_scan ON creator_scan_state(next_scan_time);
```

No changes to existing tables (creator_sig_cursors, creator_outgoing_transfers, etc.).

---

## Function Mapping

**From creator_outgoing_extractor.py** (already exist, reused as-is):

| Function | Usage |
|----------|-------|
| `rpc_get_signatures()` | Fetch 1 page of signatures |
| `helius_enhanced_parse()` | Batch parse signatures |
| `extract_outgoing_sol()` | Extract SOL transfers |
| `insert_outgoing_rows()` | Insert rows to DB |
| `build_funding_chains_incremental()` | Update chains |
| `build_coordinated_edges_incremental()` | Update coordinated edges |
| `detect_and_update_networks_from_outgoing()` | Update networks |
| `calculate_and_store_self_funding()` | Calculate self-funding |

**From creator_outgoing_scan_state.py** (new, adds state management):

| Function | Purpose |
|----------|---------|
| `migrate_creator_scan_state()` | Create table, migrate legacy data |
| `ensure_creator_in_state()` | Add new creator to state |
| `get_due_creators()` | Get creators ready for scan (sorted by tier, oldest first) |
| `load_creator_state()` | Load before_sig, last_head_sig |
| `update_creator_state_after_scan()` | Update after scan (tier, next_scan_time) |
| `check_daily_budget()` | Check if budget allows scanning |
| `get_daily_budget_status()` | Return budget {budget, used, remaining, %} |

**From creator_outgoing_scheduler.py** (new, runs the loop):

| Function | Purpose |
|----------|---------|
| `scan_creator_with_change_detection()` | Scan 1 creator with early exit on no-change |
| `process_due_creators()` | Process K due creators concurrently |
| `scheduler_loop_sync()` | Blocking scheduler (for standalone) |
| `scheduler_loop_async()` | Async scheduler (embed in listener) |

---

## Backward Compatibility

**Old code still works**:

```python
# Still callable
from creator_outgoing_extractor import scan_once
await scan_once()  # Scans all 1000 creators, no tier logic
```

**Both can run together** (scheduler + old scan_once), but not recommended.

**Suggested migration**:

1. Deploy scheduler alongside existing 12h task
2. Monitor both for 1 week
3. Confirm scheduler covers all new activity
4. Disable 12h task
5. Keep old code available (revert if needed)

---

## Testing

### Unit Test: Change Detection

```python
# Mock: creator has new sig
# Mock: last_head_signature = sig_1
# Fetch page: [sig_new, sig_1, sig_2, ...]
# Expected: Change detected, proceed to max_pages

# Mock: creator has no new sig
# Mock: last_head_signature = sig_1
# Fetch page: [sig_1, sig_2, sig_3, ...]
# Expected: No change, stop after 1 RPC call
```

### Integration Test: Full Scan

```python
# Start scheduler
# Wait 60s
# Check logs for [SCHEDULER] output
# Query: SELECT COUNT(*) FROM creator_scan_state WHERE last_scan_time IS NOT NULL
# Should be > 0
# Check metrics: /api/scan-state/budget should show usage
```

### Load Test: Scaling

```python
# With 1,345 creators:
# - Hot (active): ~50 → 12 scans/day each = 600 scans
# - Warm (recent): ~200 → 2 scans/day each = 400 scans
# - Cold (old): ~1,095 → 0.14 scans/day each = 150 scans
# Total: ~1,150 scans/day (vs 1,345 in old 12h model)
# Expected: 50% cost reduction, better coverage of new activity
```

---

## Monitoring Queries

```sql
-- How many creators in each tier?
SELECT tier, COUNT(*) FROM creator_scan_state GROUP BY tier;

-- Which creators are due in next hour?
SELECT creator_address, tier FROM creator_scan_state
WHERE next_scan_time <= datetime('now', '+1 hour')
ORDER BY next_scan_time ASC;

-- When was last activity?
SELECT creator_address, last_scan_time, tier
FROM creator_scan_state
WHERE last_scan_time IS NOT NULL
ORDER BY last_scan_time DESC
LIMIT 10;

-- Any creators not yet scanned?
SELECT COUNT(*) FROM creator_scan_state
WHERE last_scan_time IS NULL;
```

---

## Troubleshooting

### Scheduler not running
- Check: `grep SCHEDULER /var/log/app.log`
- Check: `ps aux | grep scheduler`
- Verify: `from creator_outgoing_scheduler import scheduler_loop_async` works

### No creators in state table
- Run: `migrate_creator_scan_state()`
- Check: Creators exist in `token_analysis` or `creator_funders`

### Budget exhausted immediately
- Check: OUTGOING_DAILY_BUDGET_CREDITS value
- Check: RPC metrics showing high usage from other sources
- Increase budget or reduce limit

### Tier not updating
- Check: `update_creator_tier()` called after each scan
- Check: `next_scan_time` is being set correctly
- Query: `SELECT creator_address, last_scan_time, tier FROM creator_scan_state LIMIT 5`

---

## Rollback Plan

If scheduler causes issues:

1. Stop listener: `pkill -f pumpfun_curve_listener`
2. Revert code:
   ```python
   asyncio.create_task(run_outgoing_extractor(interval_seconds=43200))
   ```
3. Restart: `python pumpfun_curve_listener.py`
4. Old 12h scan resumes immediately

No data loss (creator_scan_state is read-only for old code).

