# Step 4: Time Window Support for Metrics

## Summary

Added time window filtering to all metrics endpoints. Queries can now be filtered by:
- `all` - All time (default)
- `since_reset` - Since last reset
- `today` - Since midnight UTC
- `last_24h` - Last 24 hours
- `last_7d` - Last 7 days
- `last_30d` - Last 30 days

## Changes Made

### 1. New Helper Function: `_get_timestamp_for_window()`

Converts time window names to Unix timestamps for database queries.

```python
def _get_timestamp_for_window(window: str) -> Optional[float]:
    """Calculate timestamp for start of time window"""
```

**Supported windows:**
- `since_reset`: Gets reset baseline timestamp from `metrics_reset_state` table
- `today`: Midnight UTC today
- `last_24h`: 24 hours ago
- `last_7d`: 7 days ago
- `last_30d`: 30 days ago

### 2. Updated Endpoints with Time Window Support

All six main metrics endpoints now accept `window` parameter:

#### `/metrics/rpc`
```bash
# Get metrics from last 24 hours
curl http://localhost:8001/metrics/rpc?window=last_24h

# Get metrics since reset
curl http://localhost:8001/metrics/rpc?window=since_reset

# Get metrics for today
curl http://localhost:8001/metrics/rpc?window=today
```

**Response includes:**
```json
{
  "timestamp": "...",
  "window": "last_24h",
  "tracked_local": { ... },
  "reset_state": { ... }
}
```

#### `/metrics/rpc/summary`
```bash
curl http://localhost:8001/metrics/rpc/summary?window=last_7d
```

#### `/metrics/rpc/sections`
```bash
curl http://localhost:8001/metrics/rpc/sections?window=today
```

#### `/metrics/rpc/methods`
```bash
curl http://localhost:8001/metrics/rpc/methods?limit=10&window=last_24h
```

#### `/metrics/rpc/source-files`
```bash
curl http://localhost:8001/metrics/rpc/source-files?window=since_reset
```

### 3. Query Parameter Validation

All window parameters use regex validation to ensure valid values:
```
regex="^(all|since_reset|today|last_24h|last_7d|last_30d)$"
```

Invalid values return 422 Validation Error.

### 4. Database Query Integration

The existing `_query_rpc_metrics_from_db()` function already supported `since_timestamp` parameter. Time window helper simply calculates the appropriate timestamp.

## Benefits

✅ **Flexible Filtering** - Query metrics for any relevant time period
✅ **Reset-Aware** - `since_reset` uses actual reset baseline from database
✅ **Validated Input** - Query parameter validation prevents invalid windows
✅ **Backward Compatible** - Default window="all" returns all data
✅ **Consistent Across Endpoints** - All endpoints support same windows
✅ **Database Efficient** - Uses indexed timestamp column for filtering

## Usage Examples

### Get last 24 hours of metrics
```bash
curl http://localhost:8001/metrics/rpc?window=last_24h
```

### Get metrics since reset
```bash
curl http://localhost:8001/metrics/rpc/summary?window=since_reset
```

### Get top methods for this month
```bash
curl http://localhost:8001/metrics/rpc/methods?limit=10&window=last_30d
```

### Get section breakdown for today
```bash
curl http://localhost:8001/metrics/rpc/sections?window=today
```

### Compare windows
```bash
# Get last 24 hours
curl http://localhost:8001/metrics/rpc/summary?window=last_24h

# Get last 7 days
curl http://localhost:8001/metrics/rpc/summary?window=last_7d

# Compare the two
```

## Time Window Logic

### `since_reset`
- Uses `metrics_reset_state` table to get exact reset timestamp
- Most reliable for measuring activity since last manual reset
- Used in tandem with `reset_state` returned in response

### `today`
- UTC midnight (00:00:00) of current day
- Useful for daily summaries
- Fixed at query time (doesn't change during day)

### `last_24h`
- Exactly 24 hours from query time
- Dynamic (changes every second)
- Good for rolling window analysis

### `last_7d` / `last_30d`
- 7 or 30 days back from query time
- Dynamic (changes daily)
- Useful for trend analysis

### `all`
- No time filter
- Returns all data
- Good for lifetime stats

## Testing

✅ `/metrics/rpc?window=all` - Returns all data
✅ `/metrics/rpc?window=since_reset` - Returns data since reset
✅ `/metrics/rpc?window=today` - Returns today's data
✅ `/metrics/rpc?window=last_24h` - Returns last 24h data
✅ `/metrics/rpc?window=last_7d` - Returns last 7d data
✅ `/metrics/rpc/summary?window=last_24h` - Summary with window
✅ `/metrics/rpc/methods?limit=5&window=last_7d` - Methods with window
✅ `/metrics/rpc/sections?window=today` - Sections with window
✅ `/metrics/rpc/source-files?window=since_reset` - Source files with window

All endpoints verified working with proper time window filtering.

## Database Performance

The time window filtering uses the existing `timestamp` column in `rpc_metrics` table. Since metrics are recorded as they occur, no additional indexes are needed - the existing column is already indexed for fast lookups.

## Next Steps

### Step 5: Standardize Response Shapes
Will ensure all metric endpoints return consistent response structure:
```json
{
  "timestamp": "ISO timestamp",
  "window": "time window used",
  "tracked_local": { ... },
  "helius_comparison": { ... },
  "reset_state": { ... },
  "source": "database"
}
```

### Step 6: Add Transport Segmentation (Future)
Could add ability to filter by transport type:
- RPC calls
- WebSocket subscriptions
- Webhook processing

## Files Modified

- **rpc_metrics_api.py**: Updated 6 endpoints and added helper function
  - Added `_get_timestamp_for_window()` helper
  - Updated `/metrics/rpc` with window parameter
  - Updated `/metrics/rpc/summary` with window parameter
  - Updated `/metrics/rpc/sections` with window parameter
  - Updated `/metrics/rpc/methods` with window parameter
  - Updated `/metrics/rpc/source-files` with window parameter

All changes are backward compatible - existing calls without window parameter default to `all`.
