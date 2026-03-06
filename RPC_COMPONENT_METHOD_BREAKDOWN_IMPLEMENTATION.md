# RPC Component-Method Breakdown - Implementation Plan

**Date**: March 6, 2026
**Scope**: Add component-level aggregation without schema changes
**Compatibility**: Uses existing `section`, `source_file`, `method` fields

---

## Overview

Expose component-level RPC spending through a new aggregation endpoint:

```
GET /metrics/rpc/component-breakdown?hours=24
```

Returns credit usage breakdown by component, with top methods per component.

---

## Component Mapping Strategy

### Option A: Use `source_file` as Component (Recommended)

Map source_file to component for clarity:

```python
SOURCE_FILE_TO_COMPONENT = {
    "pumpfun_curve_listener": "listener",
    "creator_funding_extractor": "creator_funding",
    "funder_incoming_extractor": "funder_incoming",
    "creator_outgoing_extractor": "creator_outgoing",
    "creator_watch_manager": "creator_watch",
    "cross_funding_network_analyzer": "clustering",
    "cluster_risk_checker": "clustering",
    "streaming": "streaming",
    "unknown": "other",
}
```

**Rationale**:
- source_file is already populated when recording requests
- Direct 1:1 mapping between process and component
- Creator/funder extractors are already separate files
- No schema changes needed

### Option B: Use `section` as Component

Use `section` field directly:

```
listener, creator_funding, funder_incoming, clustering, ...
```

**Limitation**: Less granular (multiple components map to same section)

**Recommendation**: Use Option A + Option B together (map source_file primarily, fall back to section)

---

## SQL Aggregation Queries

### Query 1: Components by Credits (Top Level)

```sql
SELECT
    source_file as component,
    COUNT(*) as calls,
    SUM(credits) as total_credits,
    COUNT(DISTINCT method) as unique_methods,
    AVG(credits) as avg_credits_per_call,
    MIN(timestamp) as first_call,
    MAX(timestamp) as last_call
FROM rpc_metrics
WHERE timestamp > ?
  AND source_file != 'unknown'
GROUP BY source_file
ORDER BY total_credits DESC
```

**Parameters**: `time.time() - (hours * 3600)`

**Result**: One row per component with aggregates

### Query 2: Top Methods per Component

```sql
SELECT
    source_file as component,
    method,
    COUNT(*) as calls,
    SUM(credits) as total_credits,
    AVG(credits) as avg_credits
FROM rpc_metrics
WHERE timestamp > ?
  AND source_file = ?
GROUP BY method
ORDER BY total_credits DESC
LIMIT 10
```

**Parameters**: `cutoff_timestamp, source_file`

**Result**: Top 10 methods for a specific component

### Query 3: All Components + Methods in Single Query

```sql
SELECT
    source_file as component,
    method,
    COUNT(*) as calls,
    SUM(credits) as total_credits
FROM rpc_metrics
WHERE timestamp > ?
GROUP BY source_file, method
ORDER BY source_file, total_credits DESC
```

**Return**: Flatten and structure in application logic

---

## Application Logic (Python)

```python
def get_component_breakdown(hours: int = 24) -> Dict:
    """
    Get RPC credit breakdown by component and method.

    Returns:
    {
        "timestamp": "ISO timestamp",
        "window_hours": 24,
        "total_credits": 78000,
        "total_calls": 12482,
        "by_component": {
            "listener": {
                "credits": 18400,
                "calls": 3900,
                "unique_methods": 4,
                "avg_credits_per_call": 4.72,
                "top_methods": [
                    {"method": "getTransaction", "credits": 8400, "calls": 840, "avg": 10},
                    ...
                ]
            },
            ...
        }
    }
    """
    try:
        import sqlite3
        cutoff_timestamp = time.time() - (hours * 3600)
        conn = sqlite3.connect(DB_PATH, timeout=30)

        # Query 1: Aggregate by component
        cursor = conn.execute("""
            SELECT
                source_file as component,
                COUNT(*) as calls,
                SUM(credits) as total_credits,
                COUNT(DISTINCT method) as unique_methods,
                ROUND(AVG(credits), 2) as avg_credits_per_call
            FROM rpc_metrics
            WHERE timestamp > ?
              AND source_file IS NOT NULL
              AND source_file != ''
            GROUP BY source_file
            ORDER BY total_credits DESC
        """, (cutoff_timestamp,))

        components_data = cursor.fetchall()

        # Query 2: Get top methods for each component
        by_component = {}
        total_credits = 0
        total_calls = 0

        for component, calls, credits, unique_methods, avg_credits in components_data:
            total_credits += credits
            total_calls += calls

            # Get top 5 methods for this component
            cursor = conn.execute("""
                SELECT
                    method,
                    COUNT(*) as method_calls,
                    SUM(credits) as method_credits,
                    ROUND(AVG(credits), 2) as method_avg
                FROM rpc_metrics
                WHERE timestamp > ?
                  AND source_file = ?
                GROUP BY method
                ORDER BY method_credits DESC
                LIMIT 5
            """, (cutoff_timestamp, component))

            top_methods = []
            for method, method_calls, method_credits, method_avg in cursor.fetchall():
                top_methods.append({
                    "method": method,
                    "calls": method_calls,
                    "credits": method_credits,
                    "avg_credits_per_call": method_avg,
                })

            by_component[component] = {
                "credits": credits,
                "calls": calls,
                "unique_methods": unique_methods,
                "avg_credits_per_call": avg_credits,
                "top_methods": top_methods,
            }

        conn.close()

        return {
            "timestamp": datetime.now().isoformat(),
            "window_hours": hours,
            "total_credits": total_credits,
            "total_calls": total_calls,
            "by_component": by_component,
        }

    except Exception as e:
        print(f"[RPC_METRICS] Error calculating component breakdown: {e}", flush=True)
        return {
            "timestamp": datetime.now().isoformat(),
            "window_hours": hours,
            "total_credits": 0,
            "total_calls": 0,
            "by_component": {},
            "error": str(e),
        }
```

---

## API Endpoint

Add to Flask app (main.py or rpc_metrics_api.py):

```python
@app.route("/metrics/rpc/component-breakdown")
def get_rpc_component_breakdown():
    """
    Get RPC credit breakdown by component and method.

    Query parameters:
        hours: Time window (default 24, options: 1, 24, 168, 720)

    Returns:
        {
            "timestamp": "2026-03-06T14:30:00Z",
            "window_hours": 24,
            "total_credits": 78000,
            "total_calls": 12482,
            "by_component": {
                "listener": {...},
                "creator_funding": {...},
                ...
            }
        }
    """
    hours = request.args.get("hours", default=24, type=int)

    # Validate hours
    if hours not in [1, 24, 168, 720]:
        hours = 24

    breakdown = get_recorder().get_component_breakdown(hours)
    return jsonify(breakdown)
```

---

## Example Response

### Full Response (24h window)

```json
{
  "timestamp": "2026-03-06T14:30:00Z",
  "window_hours": 24,
  "total_credits": 78000,
  "total_calls": 12482,
  "by_component": {
    "pumpfun_curve_listener": {
      "credits": 18400,
      "calls": 3900,
      "unique_methods": 4,
      "avg_credits_per_call": 4.72,
      "top_methods": [
        {
          "method": "getTransaction",
          "calls": 840,
          "credits": 8400,
          "avg_credits_per_call": 10.0
        },
        {
          "method": "getAccountInfo",
          "calls": 2100,
          "credits": 2100,
          "avg_credits_per_call": 1.0
        },
        {
          "method": "getBalance",
          "calls": 960,
          "credits": 960,
          "avg_credits_per_call": 1.0
        }
      ]
    },
    "creator_funding_extractor": {
      "credits": 22300,
      "calls": 210,
      "unique_methods": 2,
      "avg_credits_per_call": 106.19,
      "top_methods": [
        {
          "method": "helius_enhanced_addresses_transactions",
          "calls": 210,
          "credits": 21000,
          "avg_credits_per_call": 100.0
        },
        {
          "method": "getSignaturesForAddress",
          "calls": 13,
          "credits": 130,
          "avg_credits_per_call": 10.0
        }
      ]
    },
    "funder_incoming_extractor": {
      "credits": 19100,
      "calls": 1900,
      "unique_methods": 2,
      "avg_credits_per_call": 10.05,
      "top_methods": [
        {
          "method": "getSignaturesForAddress",
          "calls": 1900,
          "credits": 19000,
          "avg_credits_per_call": 10.0
        },
        {
          "method": "getTransaction",
          "calls": 10,
          "credits": 100,
          "avg_credits_per_call": 10.0
        }
      ]
    },
    "streaming": {
      "credits": 12200,
      "calls": 2472,
      "unique_methods": 1,
      "avg_credits_per_call": 4.94,
      "top_methods": [
        {
          "method": "enhanced_ws_bytes",
          "calls": 2472,
          "credits": 12200,
          "avg_credits_per_call": 4.94
        }
      ]
    }
  }
}
```

### Per-Component Request (creator_funding example)

```bash
GET /metrics/rpc/component-breakdown?hours=24

# Returns object for creator_funding_extractor:
{
  "credentials": 22300,
  "calls": 210,
  "unique_methods": 2,
  "avg_credits_per_call": 106.19,
  "top_methods": [
    {
      "method": "helius_enhanced_addresses_transactions",
      "calls": 210,
      "credits": 21000,
      "avg_credits_per_call": 100.0
    },
    {
      "method": "getSignaturesForAddress",
      "calls": 13,
      "credits": 130,
      "avg_credits_per_call": 10.0
    }
  ]
}
```

---

## Database Performance

### Indexes Required

Already created:
```sql
CREATE INDEX idx_rpc_metrics_source_file ON rpc_metrics(source_file, timestamp DESC)
CREATE INDEX idx_rpc_metrics_method ON rpc_metrics(method, timestamp DESC)
```

These cover the queries above.

### Query Performance

- **Component aggregation**: ~50-200ms (depends on table size)
- **Per-component method breakdown**: ~10-50ms per component
- **Total response**: ~200-500ms for full breakdown

Acceptable for dashboard (60s refresh rate).

---

## Dashboard Integration

### UI Components to Add

1. **Component Credits Card**
   - Ordered list of components by credit usage
   - Shows calls, unique methods, avg credits/call
   - Clickable to expand and show top methods

2. **Component Method Breakdown**
   - When component clicked, show top methods
   - Visualize as:
     - Table (method, calls, credits, avg)
     - Horizontal bar chart (credits by method)

3. **Creator vs Funder Comparison**
   - Side-by-side comparison of creator_funding vs funder_incoming
   - Show which extractor is more expensive
   - Show method differences

### Example Insights

```
Creator Funding Extraction:
- Uses enhanced endpoints (100 credits each)
- 210 calls over 24h = 21,000 credits
- Avg 100 credits/call

Funder Incoming Extraction:
- Uses historical queries (10 credits each)
- 1,900 calls over 24h = 19,000 credits
- Avg 10 credits/call

Recommendation:
- Creator funding is more expensive per call but fewer calls
- Funder incoming is cheaper per call but more frequent
- Consider caching creator funding results (high cost per call)
```

---

## Schema Improvement (Optional)

### Current State

```
rpc_metrics table:
- section (listener, creator_funding, clustering)
- source_file (pumpfun_curve_listener, creator_funding_extractor)
- method (getTransaction, helius_enhanced_addresses_transactions)
```

### Optional Enhancement

Add dedicated component field:

```sql
ALTER TABLE rpc_metrics
ADD COLUMN component TEXT DEFAULT 'other';

CREATE INDEX idx_rpc_metrics_component
ON rpc_metrics(component, timestamp DESC);
```

Map at insert time:

```python
SOURCE_FILE_TO_COMPONENT = {
    "pumpfun_curve_listener": "listener",
    "creator_funding_extractor": "creator_funding",
    "funder_incoming_extractor": "funder_incoming",
    ...
}

component = SOURCE_FILE_TO_COMPONENT.get(source_file, "other")
```

**Benefit**: Cleaner aggregation queries, better dashboard semantics
**Cost**: Minimal (one column, one index)
**Risk**: Low (optional field, backward compatible)

**Recommendation**: Add it (makes the system more coherent), but not required for MVP.

---

## Implementation Checklist

- [ ] Add `get_component_breakdown(hours)` method to RPCMetricsRecorder
- [ ] Add `/metrics/rpc/component-breakdown` endpoint to Flask app
- [ ] Add global convenience function `get_component_breakdown(hours)`
- [ ] Add component aggregation to RPC dashboard
- [ ] Test with various time windows (1h, 24h, 7d, 30d)
- [ ] Verify performance (< 500ms response time)
- [ ] Add error handling and fallback responses
- [ ] Document API shape in README
- [ ] Optional: Add `component` column to schema for future clarity

---

## Summary

**No schema changes required**: Uses existing `source_file`, `method`, `credits` fields.

**API endpoint**: `/metrics/rpc/component-breakdown?hours=24`

**Response**: Component-level aggregates with top methods per component.

**Creator/Funder separation**: Already supported (separate source_file values).

**Performance**: ~200-500ms for full breakdown (acceptable).

**Optional enhancement**: Add `component` field for semantic clarity (recommended but not required).

