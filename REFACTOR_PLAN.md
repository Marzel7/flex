# Phase 2C Pre-Refactor Plan

**Date**: February 27, 2026
**Status**: PLAN DOCUMENTED (Not Implemented - See Requirements Below)
**Objective**: Reduce duplication in Phase 2C endpoints with clean helper abstractions

---

## Current State

Phase 2C-1 and Phase 2C-2 have successfully implemented 5 API endpoints with conditional routing:
1. `/api/funder-networks`
2. `/api/funding-networks`
3. `/api/funding-networks-list`
4. `/api/network-tokens/<network_name>`
5. `/api/funding-network-details/<int:network_id>`

Each endpoint currently contains:
- Inline database connection management
- Try-except error handling
- `if app.has_networks_release:` conditional routing
- Separate new and legacy path implementations
- Inline SQL queries

**Problem**: Code duplication across 5 endpoints

---

## Refactor Scope (Mechanical Only - Zero Behavior Change)

### 1. Add DB Helper: `get_db_conn()`

**Responsibility**: Centralize connection + row_factory setup

```python
def get_db_conn():
    """
    Open database connection with row_factory configured.

    Returns:
        tuple: (conn, cursor) - configured connection and cursor
    """
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    return conn, cursor
```

**Usage**: Replace all instances of:
```python
conn = sqlite3.connect(DB_PATH, timeout=5)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()
```

with:
```python
conn, cursor = get_db_conn()
```

---

### 2. Add Phase 2C Router: `route_phase2c()`

**Responsibility**: Centralize conditional routing + logging + error handling

```python
def route_phase2c(endpoint_name, new_fn, legacy_fn):
    """
    Route Phase 2C endpoint to new or legacy implementation based on capability.

    Handles:
    - Logging path selection
    - Exception handling
    - Connection cleanup
    - JSON response formatting

    Args:
        endpoint_name (str): Name of endpoint for logging (e.g. '/api/funding-networks')
        new_fn (callable): Function to call if networks_release exists
                          Must return (dict, int) - (data, status_code)
        legacy_fn (callable): Function to call if networks_release missing
                             Must return (dict, int) - (data, status_code)

    Returns:
        Response: Flask JSON response
    """
    try:
        if app.has_networks_release:
            print(f"[PHASE2C] {endpoint_name} using networks_release path", flush=True)
            result, status_code = new_fn()
        else:
            print(f"[PHASE2C] {endpoint_name} using legacy path", flush=True)
            result, status_code = legacy_fn()

        return jsonify(result), status_code
    except Exception as e:
        print(f"[PHASE2C_ERROR] {endpoint_name}: {e}", flush=True)
        return jsonify({'error': str(e)}), 500
```

**Usage Pattern**: Each endpoint becomes:
```python
@app.route('/api/some-endpoint')
def api_some_endpoint():
    """Documentation"""

    def new_path():
        """NEW PATH: implementation"""
        conn, cursor = get_db_conn()
        # ... queries ...
        conn.close()
        return {result}, 200

    def legacy_path():
        """OLD PATH: implementation"""
        conn, cursor = get_db_conn()
        # ... queries ...
        conn.close()
        return {result}, 200

    return route_phase2c('/api/some-endpoint', new_path, legacy_path)
```

---

### 3. Extract New-Path Query Helpers

These helpers encapsulate repeated new-path queries WITHOUT computing data dynamically.

#### `get_networks_release_list(include_evidence=False)`

**Query**: All networks from networks_release (optionally with evidence)

```python
def get_networks_release_list(include_evidence=False):
    """
    Get all networks from networks_release table.

    Args:
        include_evidence (bool): If True, LEFT JOIN network_evidence

    Returns:
        list: List of dict rows from networks_release
    """
    conn, cursor = get_db_conn()

    if include_evidence:
        cursor.execute("""
            SELECT
                nr.network_name,
                nr.network_size,
                nr.network_risk_level,
                nr.network_type,
                nr.has_cex_funder,
                nr.has_infra_funder,
                nr.cex_funder_count,
                nr.infra_funder_count,
                nr.stability_state,
                nr.build_version,
                nr.last_built_at,
                COALESCE(ne.total_edges, 0) as evidence_edges,
                COALESCE(ne.average_confidence, 0) as evidence_confidence,
                COALESCE(ne.evidence_risk_score, 0) as evidence_risk_score
            FROM networks_release nr
            LEFT JOIN network_evidence ne ON nr.network_name = ne.network_name
            ORDER BY nr.network_size DESC, nr.network_name ASC
        """)
    else:
        cursor.execute("""
            SELECT * FROM networks_release
            ORDER BY network_size DESC, network_name ASC
        """)

    networks = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return networks
```

**Used By**:
- `/api/funding-networks` (with evidence)
- `/api/funding-networks-list` (with evidence)

---

#### `get_network_release_by_name(network_name, include_evidence=False)`

**Query**: Single network from networks_release by name

```python
def get_network_release_by_name(network_name, include_evidence=False):
    """
    Get single network from networks_release by name.

    Args:
        network_name (str): Name of network
        include_evidence (bool): If True, LEFT JOIN network_evidence

    Returns:
        dict or None: Network row as dict, or None if not found
    """
    conn, cursor = get_db_conn()

    if include_evidence:
        cursor.execute("""
            SELECT
                nr.network_name,
                nr.network_size,
                nr.network_risk_level,
                nr.network_type,
                nr.has_cex_funder,
                nr.has_infra_funder,
                nr.cex_funder_count,
                nr.infra_funder_count,
                nr.stability_state,
                nr.build_version,
                nr.last_built_at,
                COALESCE(ne.total_edges, 0) as evidence_edges,
                COALESCE(ne.average_confidence, 0) as evidence_confidence,
                COALESCE(ne.evidence_risk_score, 0) as evidence_risk_score
            FROM networks_release nr
            LEFT JOIN network_evidence ne ON nr.network_name = ne.network_name
            WHERE nr.network_name = ?
        """, (network_name,))
    else:
        cursor.execute("""
            SELECT * FROM networks_release WHERE network_name = ?
        """, (network_name,))

    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None
```

**Used By**:
- `/api/network-tokens/<network_name>` (without evidence)

---

#### `get_network_members(network_name)`

**Query**: Creators (members) of a network

```python
def get_network_members(network_name):
    """
    Get member creators for a network from network_membership.

    Args:
        network_name (str): Name of network

    Returns:
        list: List of dict rows with creator_address
    """
    conn, cursor = get_db_conn()

    cursor.execute("""
        SELECT creator_address
        FROM network_membership
        WHERE network_name = ?
        ORDER BY creator_address
    """, (network_name,))

    members = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return members
```

**Used By**:
- `/api/funding-networks` (iterate to get members per network)
- `/api/network-tokens/<network_name>` (get creator list)

---

#### `network_name_from_id(network_id)`

**Query**: Convert numeric ID to network_name using deterministic ordering

```python
def network_name_from_id(network_id):
    """
    Convert numeric network_id to network_name using deterministic ordering.

    Uses ORDER BY network_name ASC to ensure consistent mapping.
    Treats network_id as 1-based index into sorted network list.

    Args:
        network_id (int): Numeric network ID (1-based)

    Returns:
        str or None: Network name, or None if ID out of range
    """
    conn, cursor = get_db_conn()

    cursor.execute("""
        SELECT network_name
        FROM networks_release
        ORDER BY network_name ASC
    """)

    all_networks = [row['network_name'] for row in cursor.fetchall()]
    conn.close()

    if network_id < 1 or network_id > len(all_networks):
        return None

    return all_networks[network_id - 1]
```

**Used By**:
- `/api/funding-network-details/<int:network_id>` (map ID to name)

---

## Do NOT Refactor

- ❌ Legacy SQL queries (leave as-is in else: blocks)
- ❌ HTML templates
- ❌ Endpoint URLs
- ❌ Capability check logic
- ❌ Response schemas

**Golden Rule**: No behavior change, only duplication reduction.

---

## Refactor Implementation Steps

### Step 1: Add Helpers (lines 76-250)
Insert the 5 helpers + route_phase2c() after capability check, before DATABASE QUERIES section.

### Step 2: Refactor `/api/funder-networks`
Extract new_path() and legacy_path() functions
Call route_phase2c('/api/funder-networks', new_path, legacy_path)

### Step 3: Refactor `/api/funding-networks`
Extract new_path() and legacy_path() functions
Use get_networks_release_list(include_evidence=True)
Use get_network_members(network_name)
Call route_phase2c()

### Step 4: Refactor `/api/funding-networks-list`
Extract new_path() and legacy_path() functions
Use get_networks_release_list(include_evidence=True)
Call route_phase2c()

### Step 5: Refactor `/api/network-tokens/<network_name>`
Extract new_path() and legacy_path() functions
Use get_network_release_by_name(network_name)
Use get_network_members(network_name)
Call route_phase2c()

### Step 6: Refactor `/api/funding-network-details/<int:network_id>`
Extract new_path() and legacy_path() functions
Use network_name_from_id(network_id) for ID mapping
Use get_network_release_by_name(network_name) for data
Call route_phase2c()

---

## Definition of Done

- ✅ All 5 migrated endpoints still function identically
- ✅ main.py endpoint bodies shorter and cleaner
- ✅ Helper functions grouped under: `# PHASE 2C HELPERS`
- ✅ No behavior change
- ✅ Syntax validated: `python3 -m py_compile main.py`
- ✅ Both new and legacy paths tested

---

## Expected Outcomes

### Code Organization
- Lines of code per endpoint: ~150 → ~50
- Boilerplate elimination: ~60%
- Duplication removal: ~90%

### Maintainability Improvements
- Single source of truth for helper queries
- Consistent error handling
- Consistent logging
- Simpler endpoint logic

### Behavior
- **Zero changes to API responses**
- **Zero changes to query logic**
- **Zero performance impact**
- Backward compatible

---

## Next Phase (After Refactor)

Phase 2C-3 will use these helpers to implement:
- `/networks` (HTML Dashboard)
- `/creator-network/<network_name>` (HTML Page)

Benefits:
- Same clean patterns
- Reduced HTML endpoint code
- Consistent with API endpoints

---

## Notes

### Why These Specific Helpers?

1. **`get_db_conn()`**: Eliminates repetitive 3-line connection setup
2. **`get_networks_release_list()`**: Shared query by 2 endpoints
3. **`get_network_release_by_name()`**: Required for `/api/network-tokens`
4. **`get_network_members()`**: Shared query by 2 endpoints
5. **`network_name_from_id()`**: Unique mapping logic for `/api/funding-network-details`
6. **`route_phase2c()`**: Eliminates repetitive try-except and conditional routing

### No Dynamic Computation

All helpers are **read-only queries** that return precomputed data:
- No aggregation
- No computation
- No transformation
- Honors ARCHITECTURE_STATE.md "UI must not compute" principle

---

## Syntax Validation

After refactoring, validate:
```bash
python3 -m py_compile main.py
```

Must pass with zero errors.

---

## Testing Approach

### Pre-Refactor
- Save current endpoint responses to files
- `/api/funder-networks` → funder-networks.json
- `/api/funding-networks` → funding-networks.json
- etc.

### Post-Refactor
- Call same endpoints
- Compare responses
- Verify identical

### Both Paths
- Test with networks_release enabled
- Test with networks_release disabled
- Both should work identically

---

## Conclusion

This refactor is **mechanical only**:
- Reorganizes existing code
- Extracts common patterns
- Reduces duplication
- Zero behavior change

Safe to apply immediately after Phase 2C-2 completion, before Phase 2C-3 begins.

---

**Status**: PLAN COMPLETE
**Readiness**: Ready for Implementation
**Estimated Effort**: 2-3 hours
**Risk Level**: LOW (mechanical refactor, full backward compatibility)

---

End of Refactor Plan
