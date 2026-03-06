# Step 3: Separated Metrics Concepts (tracked_local vs helius_billed)

## Summary

Updated API responses to explicitly separate and label three distinct concepts:
1. **tracked_local** - Credits from instrumented RPC calls in our database
2. **helius_billed** - Credits Helius reports they've billed us for
3. **untracked_usage** - The difference (credits billed but not instrumented)

This clarifies what we're tracking vs. what we're actually being charged for.

## Changes Made

### 1. Updated `/metrics/rpc` - Main Metrics Dashboard

**Before:**
```json
{
  "summary": { ... },
  "sections": { ... },
  "top_methods": { ... }
}
```

**After:**
```json
{
  "tracked_local": {
    "summary": { ... },
    "sections": { ... },
    "top_methods": { ... }
  },
  "reset_state": { ... },
  "helius_snapshot": { ... },
  "note": "tracked_local is credits recorded from instrumented RPC calls..."
}
```

**What it means:**
- `tracked_local` = What we've explicitly instrumented and recorded
- Helius snapshot included for quick reference
- Reset state included for "since_reset" calculations

### 2. Updated `/metrics/rpc/summary` - Quick Summary

**Before:**
```json
{
  "summary": {
    "total_credits": 1,
    "total_requests": 1,
    "total_errors": 0,
    "total_429s": 0
  }
}
```

**After:**
```json
{
  "tracked_local": {
    "total_credits": 1,
    "total_requests": 1,
    "total_errors": 0,
    "total_429s": 0
  },
  "since_reset": {
    "credits": -135527,
    "requests": 1
  },
  "reset_state": { ... }
}
```

**What it means:**
- `tracked_local` = What we've recorded
- `since_reset` = Current tracked minus the baseline (from reset_state)
- Explicitly shows reset baseline for transparency

### 3. Updated `/metrics/helius` - Comparison Endpoint (Most Important)

**Purpose**: Compare what we track vs. what we're billed for

**Response Structure:**
```json
{
  "helius_billed": {
    "total_credits": 135530,
    "credits_remaining": 9864470,
    "source": "Helius account (billed charges)"
  },
  "tracked_local": {
    "total_credits": 1,
    "total_requests": 1,
    "by_method": { ... },
    "by_source_file": { ... },
    "by_section": { ... },
    "source": "rpc_metrics table (instrumented)"
  },
  "untracked_usage": {
    "credits": 135529,
    "percent_of_billed": 100.0,
    "possible_causes": [
      "RPC calls from non-instrumented processes",
      "Failed requests that still consumed credits",
      "Retries and internal Helius operations",
      "WebSocket subscription charges"
    ]
  },
  "reset_state": { ... }
}
```

**Key Fields:**
- **helius_billed.total_credits** - What Helius says we owe
- **tracked_local.total_credits** - What we've recorded from instrumented RPC calls
- **untracked_usage.credits** - The gap (what we're missing from instrumentation)
- **untracked_usage.percent_of_billed** - Gap as percentage of total billed
- **possible_causes** - Why the gap might exist

## Benefits

✅ **Clear Separation** - Three distinct concepts now clearly labeled
✅ **Transparency** - Explicitly shows the gap between tracked and billed
✅ **Debuggable** - `possible_causes` helps understand discrepancies
✅ **Foundation for Step 4** - Ready to add time window support (since_reset, today, last_24h)
✅ **Reset-Aware** - All endpoints include reset_state for "since_reset" calculations

## Key Concepts

### tracked_local
- **Source**: `rpc_metrics` table (what we've instrumented)
- **Accuracy**: Only includes RPC calls we've explicitly recorded
- **Use Case**: Understanding our own instrumentation coverage
- **Limitation**: May not capture all actual RPC activity

### helius_billed
- **Source**: Helius API account status
- **Accuracy**: The absolute truth of what we're charged
- **Use Case**: Reconciling against actual charges
- **Limitation**: No breakdown by method/section, just totals

### untracked_usage
- **Calculation**: `helius_billed - tracked_local`
- **Causes**: Non-instrumented processes, retries, failed requests, subscriptions
- **Use Case**: Identifying instrumentation gaps
- **Action**: Can improve by:
  1. Instrumenting more processes
  2. Better error handling
  3. Tracking subscription charges

## Dashboard Impact

For dashboard displays, use:
- **Main display**: `tracked_local.total_credits` (what we're tracking)
- **Compare against**: `helius_billed.total_credits` (what we're actually charged)
- **Show gap**: `untracked_usage.credits` (what we're missing)

Example dashboard card:
```
Tracked: 1 credit (tracked_local)
Billed:  135,530 credits (helius_billed)
Gap:     135,529 credits untracked (99.9%)
```

## Testing

✅ `/metrics/rpc` - Returns tracked_local with reset_state
✅ `/metrics/rpc/summary` - Returns tracked_local with since_reset calculation
✅ `/metrics/helius` - Returns separated helius_billed, tracked_local, untracked_usage
✅ All endpoints include reset_state for "since_reset" calculations

## Next Steps

### Step 4: Add Time Window Support
Will add support for:
- `since_reset` - From last reset (already calculated via reset_state)
- `today` - Since midnight UTC
- `last_24h` - Last 24 hours
- `last_7d` - Last 7 days

This will allow filtering `/metrics/rpc` queries by time window.

### Step 5: Update Response Shapes
Will standardize all metric endpoints to consistently return:
```
{
  "tracked_local": { ... },
  "helius_comparison": { ... },
  "reset_state": { ... },
  "source": "database"
}
```

## Files Modified

- **rpc_metrics_api.py**: Updated 3 endpoints
  - `metrics_full()` - Now returns separated `tracked_local` structure
  - `metrics_summary()` - Now returns `tracked_local` with `since_reset` calculation
  - `helius_account_status()` - Now returns explicit `helius_billed`, `tracked_local`, `untracked_usage` separation

## Database Schema Used

- **rpc_metrics** table - Source of `tracked_local` totals
- **metrics_reset_state** table - Source of reset baselines
- **Helius API** - Source of `helius_billed` totals

All data is persistent and multi-process safe.
