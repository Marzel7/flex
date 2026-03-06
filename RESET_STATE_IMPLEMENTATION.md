# Persistent Reset State Implementation

## Summary

Implemented persistent reset baseline tracking to ensure "since reset" calculations remain consistent across process restarts and worker processes.

## Changes Made

### 1. Database Migration
**File**: `migrations/001_add_metrics_reset_state.sql`

Created new table `metrics_reset_state` to track reset history:

```sql
CREATE TABLE IF NOT EXISTS metrics_reset_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reset_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    helius_baseline INTEGER DEFAULT 0,      -- Helius credits at reset time
    local_baseline INTEGER DEFAULT 0,       -- Local tracked credits at reset time
    notes TEXT,
    initiated_by TEXT,
    UNIQUE(reset_at)
);
```

**Fields**:
- `reset_at`: Timestamp of when the reset occurred
- `helius_baseline`: Total Helius credits used at reset time (from Helius API)
- `local_baseline`: Total local tracked credits at reset time (from rpc_metrics table)
- `notes`: Human-readable notes about the reset
- `initiated_by`: Process that initiated the reset (e.g., "rpc_metrics_api", "flask")

**Status**: ✅ Applied to database

### 2. Helper Functions in rpc_metrics_api.py

Added two new helper functions:

#### `_get_latest_reset_state()`
Gets the most recent reset state from the database.

**Returns**:
```json
{
  "reset_at": "2026-03-06 08:17:25",
  "helius_baseline": 135528,
  "local_baseline": 4458,
  "notes": "Reset initiated by rpc_metrics_api"
}
```

#### `_record_reset_state(helius_baseline, local_baseline, initiated_by)`
Records a new reset state persistently in the database.

**Parameters**:
- `helius_baseline`: int - Current Helius usage
- `local_baseline`: int - Current local tracked usage
- `initiated_by`: str - Process name (default: "api")

### 3. Updated `/metrics/rpc/reset` Endpoint

**Changes**:
- Now captures current Helius and local totals BEFORE resetting
- Calls `_record_reset_state()` to persistently record these baselines
- Returns the recorded baselines in the response
- Maintains backward compatibility with Flask app reset call

**New Response**:
```json
{
  "status": "success",
  "message": "Reset complete. Reset baselines recorded persistently.",
  "reset_state": {
    "helius_baseline": 135528,
    "local_baseline": 4458
  }
}
```

### 4. New `/metrics/rpc/reset-state` Endpoint

**Purpose**: Query the current persistent reset state

**Method**: GET

**Response**:
```json
{
  "status": "success",
  "reset_state": {
    "reset_at": "2026-03-06 08:17:25",
    "helius_baseline": 135528,
    "local_baseline": 4458,
    "notes": "Reset initiated by rpc_metrics_api"
  }
}
```

**Use Cases**:
- Dashboard can query this to display when last reset occurred
- Calculate "since reset" metrics using these baselines
- Verify reset consistency across processes

## Benefits

✅ **Cross-process Consistency**: Reset baselines persist in database, not memory
✅ **Restart Resilient**: Survives API process restarts
✅ **Multi-worker Safe**: Multiple worker processes can safely query same baseline
✅ **Audit Trail**: Timestamps and `initiated_by` track reset history
✅ **Foundation for Next Steps**: Ready for "since_reset" metric calculations

## Next Steps

This foundation enables:

1. **Time Window Calculations**:
   - `since_reset = current_total - reset_state.baseline`
   - Consistent across all processes

2. **Dashboard Updates**:
   - Query `/metrics/rpc/reset-state` for baseline
   - Calculate and display `since_reset` metrics
   - Show reset timestamp in UI

3. **Separated Metrics**:
   - `helius_actual_since_reset = helius_current - helius_baseline`
   - `local_tracked_since_reset = local_current - local_baseline`
   - `untracked_since_reset = helius_actual_since_reset - local_tracked_since_reset`

## Testing

✅ Migration applied successfully
✅ Reset state endpoint returns initial state
✅ Reset endpoint persists new baseline
✅ Query endpoint returns latest reset state
✅ Database persists across API restarts

## Files Modified

- `rpc_metrics_api.py`: Added helper functions and new endpoint, updated reset endpoint
- `migrations/001_add_metrics_reset_state.sql`: New migration file

## Database Status

```
metrics_reset_state table created
Index: idx_metrics_reset_state_reset_at
Seed row: epoch (1970-01-01) with 0 baselines
Latest reset: 2026-03-06 08:17:25 with helius_baseline=135528, local_baseline=4458
```
