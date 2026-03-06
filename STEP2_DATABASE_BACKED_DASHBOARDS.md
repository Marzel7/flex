# Step 2: Database-Backed Dashboard Metrics

## Summary

Refactored the main dashboard endpoints to use database-backed queries exclusively. Dashboard metrics are now always accurate and consistent across process restarts and multiple workers, with no reliance on in-memory recorder state.

## Changes Made

### Updated Endpoints

All four main dashboard endpoints now query the database directly using `_query_rpc_metrics_from_db()`:

#### 1. `/metrics/rpc` - Full dashboard metrics
- **Old behavior**: Used `recorder.get_summary()`, `recorder.get_section_stats()`, etc.
- **New behavior**: Queries `rpc_metrics` table directly for all aggregations
- **Response**: Same structure, sourced from database instead of memory

#### 2. `/metrics/rpc/summary` - Quick summary
- **Old behavior**: Single call to `recorder.get_summary()`
- **New behavior**: Queries database for total_requests, total_credits, errors, 429s
- **Response**: Returns aggregated summary from `rpc_metrics` table

#### 3. `/metrics/rpc/sections` - Per-section breakdown
- **Old behavior**: Called `recorder.get_section_stats()`
- **New behavior**: Groups `rpc_metrics` by section, aggregates credits and requests
- **Response**: Dictionary of sections with request and credit counts

#### 4. `/metrics/rpc/methods` - Top methods by credits
- **Old behavior**: Called `recorder.get_top_methods(limit=10)`
- **New behavior**: Queries methods from `rpc_metrics`, sorts by credits, limits to requested count
- **Response**: Top N methods with their credit and request counts

#### 5. `/metrics/rpc/source-files` - Per-source-file breakdown
- **Old behavior**: Called `recorder.get_source_file_stats()`
- **New behavior**: Queries and aggregates by source_file and section with detailed stats
- **Response**: Dictionary of source files with requests, credits, errors, latency, sections breakdown

### Implementation Details

All endpoints now call `_query_rpc_metrics_from_db()` which:
- Opens SQLite connection with 30-second timeout
- Executes aggregation queries (SUM, COUNT, AVG, GROUP BY)
- Returns structured data with:
  - Summary: total_requests, total_credits, errors, 429s, timestamps
  - By method: requests and credits per method
  - By source file: requests, credits, errors, rate limits, latency, sections breakdown
  - By section: requests and credits per section

### In-Memory Recorder

The in-memory `recorder` object is no longer used for main dashboard endpoints. It remains available for:
- Optional dev/debug endpoints (can be added later if needed)
- Real-time recording of metrics as they occur
- Potential alert generation based on live data

## Testing

✅ `/metrics/rpc` - Returns full metrics from database
✅ `/metrics/rpc/summary` - Returns summary totals
✅ `/metrics/rpc/sections` - Returns per-section breakdown
✅ `/metrics/rpc/methods` - Returns top methods sorted by credits
✅ `/metrics/rpc/source-files` - Returns per-source-file stats with detailed breakdown

All endpoints return consistent, database-backed data that survives process restarts.

## Benefits

✅ **Process-Restart Safe** - Metrics persist in database, not lost on restart
✅ **Multi-Worker Consistent** - All workers query same database source of truth
✅ **Always Accurate** - Data comes directly from recorded RPC calls in database
✅ **Foundation for Step 3** - Ready to separate "tracked_local" vs "helius_billed" concepts
✅ **No In-Memory Risk** - Dashboard data never depends on recorder memory state

## Next Steps

Step 3 will separate the concepts of:
- `tracked_local` - What we've recorded in the database (from RPC calls)
- `helius_billed` - What Helius reports they've billed us for

This will enable accurate "untracked_usage" calculations showing the difference between what we record and what we're actually billed for.

## Files Modified

- `rpc_metrics_api.py`: Updated 5 endpoints to use `_query_rpc_metrics_from_db()`
  - `metrics_full()` (lines 261-284)
  - `metrics_summary()` (lines 287-294)
  - `metrics_sections()` (lines 297-304)
  - `metrics_source_files()` (lines 307-314)
  - `metrics_methods()` (lines 317-324)
