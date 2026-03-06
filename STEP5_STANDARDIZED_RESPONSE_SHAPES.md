# Step 5: Standardized Response Shapes

## Summary

Updated all RPC metrics endpoints to follow a consistent response structure. This ensures:
- Predictable field names across all endpoints
- Clear identification of data sources
- Consistent metadata (timestamp, window, source)
- Easy client implementation and parsing

## Standardized Response Pattern

All endpoints now return this structure:

```json
{
  "timestamp": "ISO timestamp of query",
  "window": "time window used (if applicable)",
  "source": "data source (database, helius_comparison, etc)",
  "tracked_local": {
    "total_credits": 123,
    "total_requests": 45,
    "by_method": { ... },
    "by_section": { ... },
    ...
  },
  "helius_billed": {
    "total_credits": 456,
    ...
  },
  "untracked_usage": {
    "credits": 333,
    "percent_of_billed": 73.0,
    ...
  },
  "reset_state": {
    "reset_at": "2026-03-06 08:17:25",
    "helius_baseline": 135528,
    ...
  },
  "recommendations": [ ... ]
}
```

## Updated Endpoints

### 1. `/metrics/rpc` - Full Metrics
**Response fields:**
- `timestamp` - Query timestamp
- `window` - Time window filter used
- `tracked_local` - Metrics from our database
  - `summary` - Total credits, requests, errors
  - `sections` - Breakdown by section
  - `top_methods` - Top 10 methods by credit
- `reset_state` - Reset baseline for context
- `helius_snapshot` - Helius account snapshot for reference

### 2. `/metrics/rpc/summary` - Quick Summary
**Response fields:**
- `timestamp` - Query timestamp
- `window` - Time window filter used
- `tracked_local` - Quick summary stats
  - `total_credits` - Total credits used
  - `total_requests` - Total request count
  - `total_errors` - Error count
  - `total_429s` - Rate limit count
- `since_reset` - Metrics since last reset
  - `credits` - Credits since reset baseline
  - `requests` - Requests since reset
- `reset_state` - Reset baseline details

### 3. `/metrics/rpc/sections` - Per-Section Breakdown
**Response fields:**
- `timestamp` - Query timestamp
- `window` - Time window filter used
- `sections` - Dictionary of sections with requests and credits
  - `listener` - Metrics for listener section
  - `creator_funding` - Metrics for creator funding
  - etc.

### 4. `/metrics/rpc/methods` - Top Methods
**Response fields:**
- `timestamp` - Query timestamp
- `window` - Time window filter used
- `top_methods` - Sorted list of methods by credits
  - `logsSubscribe` - Requests and credits
  - `getTransaction` - Requests and credits
  - etc.

### 5. `/metrics/rpc/source-files` - By Source File
**Response fields:**
- `timestamp` - Query timestamp
- `window` - Time window filter used
- `source_files` - Dictionary of source files with:
  - `requests` - Total requests
  - `credits` - Total credits
  - `errors` - Error count
  - `rate_limits_429` - 429 errors
  - `avg_latency_ms` - Average latency
  - `sections` - Breakdown by section

### 6. `/metrics/rpc/database` - Database Metrics
**Response fields:**
- `timestamp` - Query timestamp
- `window` - Time window filter used
- `source` - "database"
- `tracked_local` - Complete database metrics
  - `summary` - Summary stats
  - `by_method` - By method breakdown
  - `by_source_file` - By source file breakdown
  - `by_section` - By section breakdown
- `reset_state` - Reset baseline

### 7. `/metrics/rpc/untracked` - Tracked vs Billed Comparison
**Response fields:**
- `timestamp` - Query timestamp
- `source` - "database + helius_comparison"
- `helius_billed` - What Helius reports we owe
  - `total_credits` - Total billed credits
  - `source` - "Helius account"
- `tracked_local` - What we've instrumented
  - `total_credits` - Total tracked credits
  - `by_method` - Breakdown by method
  - `by_source_file` - Breakdown by source
  - `by_section` - Breakdown by section
  - `source` - "rpc_metrics table"
- `untracked_usage` - The gap
  - `total_credits` - Credits billed but not tracked
  - `tracking_coverage_percent` - Percentage of activity tracked
  - `note` - Human-readable explanation
- `reset_state` - Reset baseline
- `recommendations` - Action items if tracking is low

### 8. `/metrics/helius` - Helius Account Comparison
**Response fields:**
- `timestamp` - Query timestamp
- `helius_billed` - What we're charged
- `tracked_local` - What we've instrumented
- `untracked_usage` - The difference
- `reset_state` - Reset baseline
- `comparison_note` - Explanation

## Benefits of Standardization

✅ **Predictable API** - Clients know what fields to expect
✅ **Consistent Field Names** - `tracked_local`, `helius_billed`, `reset_state` everywhere
✅ **Clear Sources** - `source` field explicitly states where data comes from
✅ **Metadata Rich** - Timestamp, window, and source always included
✅ **Easy Parsing** - Standardized structure enables generic response handling
✅ **Self-Documenting** - Field names explain their purpose
✅ **Future-Proof** - Adding new fields won't break structure

## Common Query Patterns

### Get tracked activity for last 24 hours
```bash
curl "http://localhost:8001/metrics/rpc/summary?window=last_24h"
```

### Get tracked activity since reset
```bash
curl "http://localhost:8001/metrics/rpc?window=since_reset"
```

### Compare tracking vs billing
```bash
curl "http://localhost:8001/metrics/rpc/untracked"
```

### Get top RPC methods for this month
```bash
curl "http://localhost:8001/metrics/rpc/methods?limit=10&window=last_30d"
```

### Check section-level breakdown
```bash
curl "http://localhost:8001/metrics/rpc/sections?window=today"
```

## Response Consistency Matrix

| Endpoint | timestamp | window | source | tracked_local | helius_billed | reset_state | notes |
|----------|-----------|--------|--------|---------------|---------------|-------------|-------|
| `/metrics/rpc` | ✓ | ✓ | implicit | ✓ | via snapshot | ✓ | Full metrics |
| `/metrics/rpc/summary` | ✓ | ✓ | implicit | ✓ | N/A | ✓ | Quick stats |
| `/metrics/rpc/sections` | ✓ | ✓ | implicit | ✓ | N/A | N/A | By section |
| `/metrics/rpc/methods` | ✓ | ✓ | implicit | ✓ | N/A | N/A | By method |
| `/metrics/rpc/source-files` | ✓ | ✓ | implicit | ✓ | N/A | N/A | By source |
| `/metrics/rpc/database` | ✓ | ✓ | ✓ | ✓ | N/A | ✓ | Raw DB data |
| `/metrics/rpc/untracked` | ✓ | N/A | ✓ | ✓ | ✓ | ✓ | Gap analysis |
| `/metrics/helius` | ✓ | N/A | implicit | ✓ | ✓ | ✓ | Comparison |

## Migration Guide for Clients

### Old Pattern (Before Step 5)
```python
response = requests.get('/metrics/rpc/summary')
credits = response['summary']['total_credits']  # Different for each endpoint!
```

### New Pattern (After Step 5)
```python
response = requests.get('/metrics/rpc/summary')
credits = response['tracked_local']['total_credits']  # Consistent!
window = response['window']  # Now we know what window was used
reset = response['reset_state']  # Reset state always available
```

## Testing

✅ `/metrics/rpc` - Standardized response with window and tracked_local
✅ `/metrics/rpc/summary` - Quick summary with since_reset calculation
✅ `/metrics/rpc/sections` - Per-section with window support
✅ `/metrics/rpc/methods` - Top methods with window support
✅ `/metrics/rpc/source-files` - By source with window support
✅ `/metrics/rpc/database` - Raw DB data with window support
✅ `/metrics/rpc/untracked` - Gap analysis with recommendations
✅ `/metrics/helius` - Helius comparison fully standardized

All endpoints verified with consistent response structures.

## Next Steps

### Step 6: Documentation and Guides
- OpenAPI/Swagger documentation
- Dashboard integration guide
- Troubleshooting guide
- Common queries reference

### Step 7: Optional - Transport Segmentation (Future)
Could add ability to filter by transport type:
- RPC calls (via HTTP)
- WebSocket subscriptions
- Webhook processing

## Files Modified

- **rpc_metrics_api.py**: Updated response shapes for all endpoints
  - `/metrics/rpc` - Added window support and standardized response
  - `/metrics/rpc/summary` - Standardized with window support
  - `/metrics/rpc/sections` - Standardized with window support
  - `/metrics/rpc/methods` - Standardized with window support
  - `/metrics/rpc/source-files` - Standardized with window support
  - `/metrics/rpc/database` - Standardized with window support
  - `/metrics/rpc/untracked` - Standardized response structure
  - `/metrics/helius` - Already standardized

All changes maintain backward compatibility through default parameters.
