# FLEX V2 Phase 1 Implementation Guide — Address Cursors

**Status**: Ready to Deploy
**Expected Duration**: 2 weeks
**Expected Impact**: 60% RPC cost reduction
**Risk Level**: Low (can disable and fall back to full scans)

---

## Overview

Phase 1 implements persistent address cursors, the foundation for all RPC optimizations.

**Key Concept**: Instead of fetching all signatures every time, fetch only NEW signatures after the last one we processed.

```
OLD WAY (inefficient):
Check creator A → fetch 0-100 signatures
Check creator A again → fetch 0-100 signatures (DUPLICATE!)
Check creator A again → fetch 0-100 signatures (DUPLICATE!)
Result: 3× RPC cost for same data

NEW WAY (cursor-based):
Check creator A → fetch 0-100, save cursor at signature #100
Check creator A again → fetch signatures after #100 (only new ones!)
Check creator A again → fetch signatures after latest (only new ones!)
Result: Same data, 1/3 RPC cost
```

---

## Files Created for Phase 1

### 1. **CursorManager** (`src/core/cursor_manager.py`)
Core module for cursor state management.

**Features**:
- Get cursor for an address
- Update cursor after extraction
- Activity-based next scan time calculation
- Mark failed/paused addresses
- Database table: `address_scan_state` (4 columns, minimal)

**Key Methods**:
```python
cursor = cursor_mgr.get_cursor(address)  # Load cursor
cursor_mgr.update_cursor(address, last_sig, activity_count)  # Save cursor
due_addresses = cursor_mgr.get_addresses_due_for_scan()  # For scheduler
```

### 2. **Incremental Extraction Utils** (`src/core/incremental_extraction.py`)
Helper functions to integrate cursors into existing code.

**Features**:
- Wrapper function for cursor-based extraction
- Validation utilities (run old+new in parallel)
- Comparison tools (verify identical results)

**Key Functions**:
```python
new_result = await extract_with_cursor(address, rpc_client, cursor_mgr)
comparison = compare_extraction_results(old, new, address)
validator = CursorValidationRunner(cursor_mgr, rpc_client)
```

### 3. **Database Migration** (`database/migrations/phase1_cursors_migration.sql`)
SQL script to create tables and indexes.

```sql
CREATE TABLE address_scan_state (
    address TEXT PRIMARY KEY,
    last_signature TEXT,
    last_scan_at TIMESTAMP,
    next_scan_at TIMESTAMP,
    status TEXT DEFAULT 'active'
);
```

---

## Step-by-Step Deployment

### Step 1: Deploy Database (Off-Peak, 5 minutes)

```bash
# Connect to production database
sqlite3 flex_complete_database.db < database/migrations/phase1_cursors_migration.sql

# Verify tables created
sqlite3 flex_complete_database.db "SELECT name FROM sqlite_master WHERE type='table' AND name='address_scan_state';"
# Expected: address_scan_state
```

### Step 2: Import CursorManager in Main Code (5 minutes)

In `pumpfun_curve_listener.py` or wherever extraction is triggered:

```python
from src.core.cursor_manager import CursorManager

# At startup
DB_PATH = 'flex_complete_database.db'
cursor_mgr = CursorManager(DB_PATH)

print("✅ CursorManager initialized")
```

### Step 3: Run Old Code in Parallel (1 week)

In `realtime_creator_funding_extractor.py`, around the `extract_for_creator` method:

```python
# OLD WAY - keep running for now
all_signatures = await rpc.get_signatures(creator, limit=100)

# NEW WAY - run in parallel for validation
from src.core.incremental_extraction import extract_with_cursor

new_result = await extract_with_cursor(
    creator,
    rpc_client,
    cursor_mgr,
    DB_PATH
)

# Log both for comparison
logger.info(f"OLD: {len(all_signatures)} sigs, NEW: {new_result['signature_count']} sigs")

# Use OLD result for now (old extraction logic continues)
# NEW result is saved in DB for validation
```

### Step 4: Validate Results (1 week)

Monitor logs for signature counts:

```
[REALTIME_FUNDING] OLD: 100 sigs, NEW: 5 sigs, Status: incremental ✅
[REALTIME_FUNDING] OLD: 100 sigs, NEW: 100 sigs, Status: first_time ✅
[REALTIME_FUNDING] OLD: 100 sigs, NEW: 0 sigs, Status: no_change ✅
```

**Expected Pattern**:
- First time: OLD ≈ NEW (both return ~100 signatures)
- Subsequent times: NEW << OLD (only new signatures returned)
- After 1 week: all comparisons match ✅

### Step 5: Switch to Cursor-Based Extraction (Deploy)

Once validated, modify extraction to use cursors:

```python
# SWITCH: Use NEW way instead of OLD way

# Load cursor (where we left off)
cursor = await cursor_mgr.get_cursor(creator)
before_sig = cursor.last_signature if cursor else None

# Fetch only new signatures
signatures = await rpc.get_signatures(
    creator,
    before=before_sig,  # Only fetch after this signature
    limit=100
)

# Update cursor for next time
if signatures:
    cursor_mgr.update_cursor(
        creator,
        signatures[0].signature,
        activity_count=len(signatures)
    )

# Continue with normal extraction logic
for sig in signatures:
    # ... parse transfers, save to DB
```

### Step 6: Verify Impact (Monitor)

**Expected Results After 1 Week**:
- RPC calls should drop by 60%
- Daily RPC cost: $50 → $20 (target: $10-15 after caching)
- Query `address_scan_state` table should have cursors for all creators

**Monitor Queries**:
```sql
-- Check cursor coverage
SELECT COUNT(*) as creators_with_cursors
FROM address_scan_state
WHERE last_signature IS NOT NULL;

-- Check cursor freshness
SELECT COUNT(*) as creators_due_for_scan
FROM address_scan_state
WHERE next_scan_at <= CURRENT_TIMESTAMP
AND status = 'active';

-- Check cursor activity distribution
SELECT
    status,
    COUNT(*) as count
FROM address_scan_state
GROUP BY status;
```

---

## Integration Checklist

- [ ] Database migration applied (phase1_cursors_migration.sql)
- [ ] CursorManager imported and initialized
- [ ] Old extraction running in parallel with new
- [ ] Validation code logging comparisons
- [ ] Validation period: 1 week of monitoring
- [ ] No discrepancies found
- [ ] Cursor-based extraction deployed
- [ ] RPC cost monitoring active
- [ ] Verify 60% reduction achieved

---

## Rollback Plan (If Issues Arise)

If cursor-based extraction causes problems:

```python
# Simply remove the cursor.update_cursor() call
# Extraction falls back to full scans (no cursors)
# No data loss, no schema changes

# Takes 5 minutes to revert
```

---

## Expected Metrics

### RPC Call Reduction
| Timeframe | Full Scans | Cursor-Based | Reduction |
|-----------|-----------|--------------|-----------|
| First 24h | 1000 | 500 | 50% (first-time scans) |
| Day 2-3 | 1000 | 100 | 90% (most are incremental) |
| Week 1 | 7000 | 1500 | 79% (new creators appear) |
| Steady State | 1000/day | 400/day | 60% |

### Cost Impact
```
Before Phase 1:
- RPC: $50/day
- Infrastructure: $40/day
- Total: $90/day

After Phase 1 (cursors):
- RPC: $20/day (60% reduction)
- Infrastructure: $40/day (same)
- Total: $60/day

After Phase 2 (caching):
- RPC: $10-15/day (35% additional reduction)
- Infrastructure: $40/day (same)
- Total: $50-55/day

Annual savings: $12,000-15,000 just from Phase 1-2
```

---

## Monitoring During Rollout

### Day 1-2: Validate Parallel Execution
```
✅ Both old and new methods working
✅ Cursors being saved to DB
✅ Signature counts matching on first scan
```

### Day 3-7: Monitor Incremental Behavior
```
✅ NEW method returning < OLD method on 2nd+ scan
✅ Activity-based scheduling working
✅ Database cursors being updated
```

### Week 2: Verify RPC Reduction
```
✅ RPC calls down by 60% (from baseline 1000/day to 400/day)
✅ Cost reduction visible in Helius dashboard
✅ No extraction failures or data loss
```

### Week 3+: Production Ready
```
✅ Switch to cursor-based extraction only
✅ Remove old extraction code
✅ Begin Phase 2 (RPC caching)
```

---

## Common Issues and Solutions

### Issue: Cursor shows wrong signature
**Cause**: Last signature was from skipped transfer (e.g., dust)
**Solution**: Update cursor tracking to include ALL signatures (not just processed ones)

### Issue: New method returns fewer signatures than old
**Expected!** This is correct behavior:
- First scan: both return ~100 signatures
- Subsequent scans: new returns only signatures after cursor (fewer)

### Issue: Extraction starting to fail
**Action**: Check `status` column in `address_scan_state`
- If `status = 'failed'`, address had error
- Mark as 'active' again to retry
- Investigate the error in logs

### Issue: Cursor not updating
**Check**: Is `cursor_mgr.update_cursor()` being called?
- Add logging to confirm it's called
- Verify database write succeeds
- Check for SQLite lock timeout

---

## Success Criteria

Phase 1 is complete when:

1. ✅ Cursors saved for 100% of active creators
2. ✅ RPC calls reduced by 60% (verified in Helius dashboard)
3. ✅ Zero extraction failures (no data loss)
4. ✅ Cursor-based extraction deployed in production
5. ✅ Ready to proceed to Phase 2 (caching)

---

## Next Phase

Once Phase 1 is complete and verified:
→ **Phase 2 (Weeks 3-4)**: Redis RPC Caching
- Adds 35% additional RPC reduction
- Caches signatures (1h TTL) and transactions (24h TTL)
- Total: 70% reduction (cursors + caching)

---

## Questions?

Refer to:
- `FLEX_V2_FINAL_ARCHITECTURE.md` - Section 4 (Worker System Design)
- `FLEX_V2_MODULE_REFACTORING.md` - Section 3.2 (CursorManager code)
- `FLEX_V2_QUICKSTART.md` - Quick reference

---

**Ready to deploy Phase 1?**

Steps:
1. Run migration script
2. Deploy CursorManager
3. Run old + new in parallel
4. Monitor for 1 week
5. Switch to cursor-based extraction

**Expected Outcome**: 60% RPC cost reduction in 2 weeks.
