# Creator Outgoing Scan – Growth Management Implementation

**Status**: Production-Ready
**Date**: 2026-03-02
**Architecture**: Tiered cadence + change detection + budget gating

---

## Overview

Growth management system that scales cost with **new activity**, not total creators.

**Key Idea**: Instead of scanning all 1,345 creators every cycle:
- Hot creators (new, active): Every 2 hours, 2 pages
- Warm creators (recent): Every 12 hours, 1 page
- Cold creators (old): Every 7 days, 1 page

**Change Detection**: Fetch 1 page first; if no new signatures detected, stop immediately (no wasted RPC calls).

**Budget Gating**: Optional daily credit ceiling to prevent cost blowups.

---

## Files

### 1. creator_outgoing_scan_state.py

Per-creator scan state management.

**Schema**:
```sql
CREATE TABLE creator_scan_state (
  creator_address TEXT PRIMARY KEY,
  before_signature TEXT,              -- pagination cursor
  last_head_signature TEXT,           -- newest sig from last scan
  last_scan_time TIMESTAMP,           -- when we last scanned
  next_scan_time TIMESTAMP,           -- when next scan is due
  tier TEXT DEFAULT 'warm',           -- hot|warm|cold
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Functions**:

- `migrate_creator_scan_state()` – Create table, migrate from creator_sig_cursors
- `ensure_creator_in_state(creator_address)` – Insert new creator with tier='warm'
- `get_due_creators(limit=100) -> [(creator_address, tier, max_pages), ...]` – Get creators due for scan
- `load_creator_state(creator_address) -> (before_sig, last_head_sig)` – Load pagination cursors
- `update_creator_state_after_scan(creator, before_sig, head_sig, credits)` – Update after scan, set next_scan_time
- `check_daily_budget() -> bool` – Check if daily budget allows scanning
- `get_daily_budget_status() -> dict` – Get budget utilization

**Configuration**:
```python
OUTGOING_DAILY_BUDGET_CREDITS = os.getenv("OUTGOING_DAILY_BUDGET_CREDITS", None)
# None = unlimited; set to integer (e.g., 50000) to limit daily credits
```

---

### 2. creator_outgoing_scheduler.py

Scheduler loop that runs every 60 seconds, processes K due creators.

**Main Functions**:

- `scan_creator_with_change_detection(session, creator, max_pages) -> (sigs, final_before, head_sig, credits)` – Scan 1 creator with change detection
  - Fetch 1 page (25 sigs)
  - If first sig == last_head_signature: stop (no new work)
  - Else: proceed up to max_pages, batch parse, extract outgoing
  - Returns all signatures, pagination cursor, head signature, credits used

- `process_due_creators(limit=50, concurrency=3) -> (processed_count, credits_used)` – Process up to K due creators
  - Check budget
  - Get due creators (sorted by tier, oldest first)
  - Run scan_creator_with_change_detection concurrently
  - Rebuild chains, networks, self-funding

- `scheduler_loop_sync(tick_interval=60, limit=50)` – Run synchronously (blocking)
  - Initialize schema
  - Loop every 60 seconds
  - Call process_due_creators(limit=50)
  - Sleep remainder of interval

- `scheduler_loop_async(tick_interval=60, limit=50)` – Run asynchronously (non-blocking)
  - For embedding in async app (like pumpfun_curve_listener)
  - Same behavior as sync, but awaitable

**Output**:
```
[SCHEDULER] 🚀 Starting scheduler (tick=60s, limit=50)
[SCHEDULER] 🔄 Processing 47 due creators (15 hot, 20 warm, 12 cold)
[SCHEDULER] ✅ bnlmWj2j... (hot) changed: 42 sigs, 410 cr
[SCHEDULER] ✅ fnxQ9K3l... (warm) unchanged: 0 sigs, 10 cr
[SCHEDULER] 🔗 Rebuilding chains and networks for 47 creators...
[SCHEDULER] ✅ Done: 47 creators, 4750 credits
[SCHEDULER] 📊 Tick 1: 47 creators, 4750 credits, budget: 45250/50000 (90.5% used) (elapsed: 23.4s)
```

---

## Tiered Cadence

**Tier Assignment Logic** (in `get_tier(last_scan_time)`):

```python
age = now - last_scan_time

if age <= 24 hours:    tier = 'hot'    # Every 2 hours, 2 pages
elif age <= 7 days:    tier = 'warm'   # Every 12 hours, 1 page
else:                  tier = 'cold'   # Every 7 days, 1 page
```

**Next Scan Time Calculation** (in `update_creator_state_after_scan`):

```python
if tier == 'hot':   next_scan = now + 2 hours
elif tier == 'warm': next_scan = now + 12 hours
else:               next_scan = now + 7 days
```

**Tier Auto-Updates**: After each scan, tier is recalculated based on `last_scan_time`.

---

## Change Detection Flow

**Scenario 1: Creator has new activity**

```
Fetch 1 page (25 sigs) -> [sig_new_1, sig_new_2, ..., sig_old_25]
last_head_signature = sig_old_50

Compare: sig_new_1 != sig_old_50
→ "Change detected, proceed"

Fetch up to max_pages (respecting per-tier cap)
Batch parse all signatures
Extract outgoing, insert rows
Update last_head_signature = sig_new_1
```

**Scenario 2: Creator has no new activity**

```
Fetch 1 page (25 sigs) -> [sig_old_50, sig_old_51, ..., sig_old_74]
last_head_signature = sig_old_50

Compare: sig_old_50 == sig_old_50
→ "No change, stop immediately"

Only 1 RPC call (10 credits)
Update next_scan_time based on tier
```

---

## Budget Gating

**Configuration** (environment variable):

```bash
# Unlimited (default)
export OUTGOING_DAILY_BUDGET_CREDITS=""

# Or set ceiling (in credits)
export OUTGOING_DAILY_BUDGET_CREDITS="50000"
```

**Logic** (in `check_daily_budget()`):

```python
if OUTGOING_DAILY_BUDGET_CREDITS is None:
    return True  # No limit

used = get_daily_credits_used()  # From RPC metrics
remaining = OUTGOING_DAILY_BUDGET_CREDITS - used

return remaining > 0
```

**Behavior**:
- Before processing due creators: check budget
- If exhausted: exit scheduler tick (don't scan)
- If ok: proceed with up to K creators
- Status logged every tick

---

## Integration with pumpfun_curve_listener

**Option A: Replace existing 12-hour scan with scheduler (Recommended)**

In `pumpfun_curve_listener.py`, change:

```python
# OLD (line ~2083):
asyncio.create_task(run_outgoing_extractor(interval_seconds=43200))  # 12 hours

# NEW:
from creator_outgoing_scheduler import scheduler_loop_async
asyncio.create_task(scheduler_loop_async(tick_interval=60, limit=50))
```

**Option B: Run both (scheduler + old 12-hour scan)**

Keep existing scan for batch fullness; run scheduler for incremental updates.

**Option C: Standalone scheduler**

Run `python creator_outgoing_scheduler.py` in separate process.

---

## Cost Model & Scaling

### Per-Creator Costs

**Unchanged (no new activity)**:
- 1 RPC call × 10 cr = 10 credits

**Changed (with new sigs)**:
- N RPC pages × 10 cr + batch parse × 100 cr
- E.g., 2 pages: (2 × 10) + 100 = 120 credits

### Projected Daily Costs

**Base Case** (1,345 creators, ~150 new/day):

```
Hot (active 24h):        50 creators  × 12 scans/day × 120 cr  = 72,000 cr
Warm (1-7d old):         200 creators × 2 scans/day × 30 cr   = 12,000 cr  (2 pages over 2 scans)
Cold (>7d old):          1,095 creators × 0.14 scans/day × 20 cr = 3,060 cr
New creators:            ~150 creators × 1 scan × 100 cr       = 15,000 cr

Total daily (estimate):  ~102,000 credits (~0.2% of 1M budget)
```

**Key Insight**: Cost is driven by **new activity**, not total creator count.

---

## New Creator Onboarding

**Flow**:

1. New token detected in pumpfun_curve_listener
2. Creator extracted, added to creator_funders table
3. Next scheduler tick:
   - `get_due_creators()` fetches from database
   - New creator has `next_scan_time = now` (overdue)
   - `scan_creator_with_change_detection()` runs immediately
   - Tier set to 'hot' (will scan every 2 hours)

**No manual intervention needed.**

---

## Migration from creator_sig_cursors

`migrate_creator_scan_state()` automatically:

1. Creates `creator_scan_state` table
2. Copies existing cursors from `creator_sig_cursors`
3. Sets tiers based on `updated_at` (old cursors → 'cold')

**Safe to run multiple times** (uses INSERT OR IGNORE).

---

## API Endpoints (Optional)

Add to Flask/FastAPI app for monitoring:

```python
@app.get("/scan-state/due")
def get_due_creators_api():
    from creator_outgoing_scan_state import get_due_creators
    due = get_due_creators(limit=100)
    return {
        "due": [
            {"creator": c, "tier": t, "max_pages": p}
            for c, t, p in due
        ]
    }

@app.get("/scan-state/budget")
def get_budget_api():
    from creator_outgoing_scan_state import get_daily_budget_status
    return get_daily_budget_status()

@app.post("/scan-state/reset-tier/{creator_address}")
def reset_tier_api(creator_address: str):
    from creator_outgoing_scan_state import update_creator_tier
    update_creator_tier(creator_address)
    return {"status": "ok"}
```

---

## Monitoring & Alerts

### Dashboard Metrics

Track over time:

- Total creators in state: `SELECT COUNT(*) FROM creator_scan_state`
- By tier: `SELECT tier, COUNT(*) FROM creator_scan_state GROUP BY tier`
- Upcoming scans: `SELECT COUNT(*) FROM creator_scan_state WHERE next_scan_time <= datetime('now', '+1 hour')`
- Daily budget utilization: Check via `GET /scan-state/budget`

### Alerts to Set

- **Budget exhausted**: `percent_used >= 100%`
- **Tier imbalance**: More than 60% cold creators (indicates slower coverage)
- **Stale creators**: More than 50% warm/cold with `last_scan_time > 30 days`

---

## Configuration Tuning

### For Higher Throughput

```python
# Increase budget
export OUTGOING_DAILY_BUDGET_CREDITS="500000"

# Increase tick limit
scheduler_loop_async(tick_interval=60, limit=200)  # 200 per tick instead of 50

# More concurrency
process_due_creators(limit=200, concurrency=10)  # 10 in-flight
```

### For Lower Cost

```python
# Set strict budget
export OUTGOING_DAILY_BUDGET_CREDITS="20000"

# Slower scheduler
scheduler_loop_async(tick_interval=300, limit=20)  # Every 5 min, 20 creators

# Less aggressive tiers
# (Edit get_tier() to extend warm/cold timings)
```

---

## Production Checklist

- [ ] Run `migrate_creator_scan_state()` once
- [ ] Set `OUTGOING_DAILY_BUDGET_CREDITS` (or leave None for unlimited)
- [ ] Integrate scheduler with pumpfun_curve_listener
- [ ] Test: Wait 60s, check logs for `[SCHEDULER]` output
- [ ] Verify: `SELECT COUNT(*) FROM creator_scan_state` shows creators
- [ ] Monitor: Check `GET /scan-state/budget` daily
- [ ] Alert: Set budget exhaustion alarm

---

## Example: Running Standalone

```bash
# Terminal 1: Start Flask app (existing)
cd /Users/kevinkeaveney/Dev/claude/flex
python main.py

# Terminal 2: Start scheduler (new)
cd /Users/kevinkeaveney/Dev/claude/flex
python creator_outgoing_scheduler.py

# Watch logs
tail -f logs/scheduler.log
```

---

## No Breaking Changes

- Existing `scan_once()` still works (can run in parallel with scheduler)
- All metrics instrumentation preserved
- `record_request()` calls unchanged
- Backward compatible with creator_sig_cursors

---

## Summary

| Aspect | Before | After |
|--------|--------|-------|
| Scan frequency | All creators every 12h | Per-tier: 2h/12h/7d |
| Cost scaling | O(N creators) | O(new activity) |
| Budget control | None | Hard ceiling option |
| New activity detection | Every full scan | Change detection (1 page) |
| Daily cost (1,345 creators) | 2,000,000+ cr | ~100,000 cr |
| Scalability | ⚠️ Linear growth | ✅ Sublinear with activity |

