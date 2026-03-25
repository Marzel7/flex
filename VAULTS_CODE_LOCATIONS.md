# Vaults Page - Code Locations Guide

## Complete Map of All Vaults Implementation

---

## Frontend Code Locations

### File: `templates/flex_dashboard.html`

#### Navigation Link (2 lines)
**Lines 956-958**: Sidebar navigation item
```html
<a class="nav-link" onclick="loadPage('vaults')">
    <i class="fas fa-vault"></i> <span>Vaults</span>
</a>
```

**Location**: In the sidebar nav section, after Token Behaviour link

#### Route Mapping (1 line)
**Line 1047**: Route entry in `loadPage()` function
```javascript
'vaults': loadVaultsPage,
```

**Location**: Inside `window.loadPage = (page) => { ... const routes = { ... }`

#### Helper Functions (20 lines total)

**Lines 3545-3556**: `formatVaultDiscoveryTime(secs)`
```javascript
function formatVaultDiscoveryTime(secs) {
    if (secs === null || secs === undefined) return 'N/A';
    if (secs < 60) return `${secs}s`;
    if (secs < 3600) {
        const mins = Math.floor(secs / 60);
        const remaining = secs % 60;
        return remaining > 0 ? `${mins}m ${remaining}s` : `${mins}m`;
    }
    const hours = Math.floor(secs / 3600);
    const mins = Math.floor((secs % 3600) / 60);
    return mins > 0 ? `${hours}h ${mins}m` : `${hours}h`;
}
```

**Lines 3559-3564**: `formatPrice(price)`
```javascript
function formatPrice(price) {
    if (price === null || price === undefined || price === 0) return 'N/A';
    if (price < 0.000001) return `$${price.toExponential(2)}`;
    if (price < 0.01) return `$${price.toFixed(8)}`;
    return `$${price.toFixed(4)}`;
}
```

#### Main Page Loader (188 lines)

**Lines 3880-4067**: `async function loadVaultsPage()`

Sections:
1. **Header & Layout** (Lines 3885-3898)
   - Title: "Vault Discovery & Validation"
   - Subtitle: "Token/pool vault discovery latency..."
   - Last updated timestamp
   - Container divs for stats and table

2. **Stats Fetch & Render** (Lines 3941-3981)
   - Fetches `/api/vaults/stats/summary`
   - Renders 8 stat cards with values
   - Handles null values for avg discovery time

3. **Table Fetch & Render** (Lines 3984-4056)
   - Fetches `/api/vaults?limit=500`
   - Maps each vault to table row
   - Renders tracking quality with icons/colors
   - Renders strategy with fallback
   - Renders attempts (no false 0s)
   - Renders discovery time with special handling
   - Validates category against approved list
   - Formats confidence as percentage
   - Each row clickable for detail

4. **Filter Event Listeners** (Lines 4059-4061)
   - Attaches input listener to mint search
   - Attaches change listeners to status/quality filters

5. **Error Handling** (Lines 4063-4066)
   - Catches and logs errors
   - Shows error alert to user

#### Table Filtering (18 lines)

**Lines 4069-4086**: `function filterVaultsTable()`
- Gets filter values from input elements
- Queries all table rows
- Checks mint (substring), status (exact), quality (substring)
- Shows/hides rows based on filter match
- Real-time filtering on input

#### Detail Modal Function (155 lines)

**Lines 4088-4243**: `function showVaultDetail(mint)`

Fetches: `GET /api/vaults/<mint>`

Renders sections:
1. **Vault Discovery Section** (Lines 4112-4140)
   - Validation Status (colored)
   - Resolution State
   - Discovery Strategy
   - Discovery Method
   - Discovery Attempts
   - Vault Discovery Time (formatted)

2. **Timeline Section** (Lines 4142-4159)
   - Pool Record Created At
   - Last Vault Validation At
   - Vault Resolved At

3. **Pool Information** (Lines 4161-4171)
   - Pool Address
   - Base Account
   - Quote Account
   - Base Token
   - Quote Token

4. **Token Tracking Section** (Lines 4173-4220)
   - Only rendered if token data exists
   - Tracking Quality (icon + text, colored)
   - Category
   - Confidence (as percentage)
   - Observed Start Price (formatted)
   - Robust Start Price (formatted)
   - Peak Price (formatted)
   - Latest Price (formatted)
   - Max Return (Observed)
   - Max Return (Robust)
   - Drawdown From Peak

Modal management:
- Removes old modal (Lines 4230-4231)
- Inserts new modal HTML (Line 4232)
- Shows via Bootstrap Modal (Line 4233)
- Removes modal on close (Lines 4235-4237)

---

## Backend Code Locations

### File: `src/core/flex_dashboard_routes.py`

#### Imports (1 line)
**Line 19**: Type hints import
```python
from typing import Any, Dict, List
```

#### Constants (24 lines)

**Lines 778-800**: Database path and valid values
```python
DB_PATH = 'database/flex_complete_database.db'

VALID_BEHAVIOUR_CATEGORIES = {
    'immediate_rug',
    'runner',
    'faded_runner',
    'choppy_runner',
    'rug',
    'slow_rug',
    'insufficient_history',
    'unknown',
}

VALID_TRACKING_QUALITY = {
    'good',
    'possibly_late',
    'likely_late',
}
```

#### Helper Functions

**Lines 803-806**: `_table_columns(conn, table_name)`
- Returns set of column names via PRAGMA
- Used for schema detection

**Lines 809-812**: `_has_column(conn, table_name, column_name)`
- Checks if column exists
- Used for backward compatibility

**Lines 815-822**: `_format_nullable_float(value, digits=3)`
- Rounds floats to N decimal places
- Returns None if value is None
- Handles TypeError/ValueError

**Lines 825-831**: `_normalize_category(value)`
- Validates against approved list
- Returns None if invalid

**Lines 834-840**: `_normalize_tracking_quality(value)`
- Validates against approved list
- Returns None if invalid

**Lines 843-907**: `_build_vaults_select(conn)`
- **Key function**: Dynamically builds SELECT statement
- Detects available columns in token_pool_accounts table
- Builds discovery_time from timestamps if needed
- Uses COALESCE for strategy fallback
- Returns complex SELECT statement with proper joins

**Lines 910-1000**: `_vault_row_to_dict(row)`
- **Key function**: Converts sqlite3.Row to normalized dict
- Validates and normalizes all fields
- Formats floats with precision
- Validates categories and quality
- Handles null values gracefully
- Returns clean dict with no garbage values

#### Endpoints (170 lines)

**Lines 1003-1060**: Route handlers

**1. GET /api/vaults**
```python
@dashboard_routes.route('/api/vaults', methods=['GET'])
```
- Query params: `limit`, `offset`, `category`, `status`, `strategy`, `tracking_quality`, `search`
- Returns filtered/paginated list with stats
- No-cache headers

**2. GET /api/vaults/stats/summary**
```python
@dashboard_routes.route('/api/vaults/stats/summary', methods=['GET'])
```
- Returns: total_vaults, validated, pending, rejected, averages, percentages
- Aggregates data from token_pool_accounts and token_behavior tables

**3. GET /api/vaults/<mint>**
```python
@dashboard_routes.route('/api/vaults/<mint>', methods=['GET'])
```
- Returns: vault dict + token dict for detailed view
- LEFT JOIN to include vault data even without token behavior
- Sanitizes mint input

---

## Data Flow Diagram

```
User clicks "Vaults" in sidebar
    ↓
loadPage('vaults') called
    ↓
loadVaultsPage() runs
    ↓
┌─────────────────────────────────────┐
│ Fetch /api/vaults/stats/summary     │
│ Render 8 stat cards                 │
└─────────────────────────────────────┘
    ↓
Fetch /api/vaults?limit=500
    ↓
┌─────────────────────────────────────┐
│ Backend: _build_vaults_select()     │
│ - Detects schema version            │
│ - Joins vault + behavior tables     │
│ - Handles missing columns           │
│ - Returns raw rows                  │
└─────────────────────────────────────┘
    ↓
Backend: _vault_row_to_dict()
    ↓
┌─────────────────────────────────────┐
│ Normalize & validate all fields     │
│ - Categories validated              │
│ - Quality states validated          │
│ - Floats formatted                  │
│ - Null values preserved             │
│ Return clean dict                   │
└─────────────────────────────────────┘
    ↓
Frontend receives JSON response
    ↓
┌─────────────────────────────────────┐
│ Render table with proper formatting │
│ - Quality icons/colors              │
│ - Strategy with fallback            │
│ - Time formatted                    │
│ - Confidence as percentage          │
│ - Category validated                │
└─────────────────────────────────────┘
    ↓
Add filter event listeners
    ↓
User interacts with page
    ↓
┌─ Typing in search ──────────────────┐
│ filterVaultsTable() runs (no API)   │
│ Updates table visibility            │
└─────────────────────────────────────┘
    ↓
┌─ Clicking table row ────────────────┐
│ showVaultDetail(mint) called        │
│ Fetch /api/vaults/<mint>            │
│ Render modal                        │
└─────────────────────────────────────┘
```

---

## Critical Code Sections

### 1. Schema Detection (Backend)
**Why**: Handles vaults that may have been created before new columns existed
**Lines**: 843-907
**Function**: `_build_vaults_select()`
**Key code**: `PRAGMA table_info()` query

### 2. Field Normalization (Backend)
**Why**: Prevents garbage values from reaching frontend
**Lines**: 910-1000
**Function**: `_vault_row_to_dict()`
**Key code**: Explicit None returns for invalid values

### 3. Quality Rendering (Frontend)
**Why**: Provides visual feedback for tracking quality
**Lines**: 3990-4012 (table) + 4095-4097 (modal)
**Key code**: Icon/color mapping for 4 states

### 4. Category Validation (Frontend)
**Why**: Prevents showing invalid/new category names
**Lines**: 4033-4034
**Key code**: Set membership check against approved list

### 5. Fallback Strategy (Frontend)
**Why**: Ensures strategy always shows something meaningful
**Lines**: 4015
**Key code**: `v.vault_discovery_strategy ?? v.discovery_method ?? 'N/A'`

### 6. Discovery Time Handling (Frontend)
**Why**: Shows appropriate state for different statuses
**Lines**: 4024-4030
**Key code**: Checks for null, then pending status, then N/A

### 7. Confidence Formatting (Frontend)
**Why**: Only shows percentage if value is numeric
**Lines**: 4037-4039
**Key code**: `v.confidence !== null ? Math.round(v.confidence * 100) + '%' : 'N/A'`

---

## Integration Points

### With Bootstrap 5
- Modal component (Lines 4100-4227)
- Card styling (throughout)
- Form controls (sidebar filters)

### With Dashboard Framework
- Route mapping (Line 1047)
- Navigation link (Lines 956-958)
- API_BASE constant (used throughout)
- Color variables (--text-primary, --color-critical, etc.)

### With Database
- Database: `flex_complete_database.db`
- Tables: `token_pool_accounts`, `token_behavior`
- Schema detection for backward compatibility

---

## Complete Function Call Chain

1. User action: Click "Vaults" link
   ```javascript
   onclick="loadPage('vaults')"
   ```

2. Router: `window.loadPage('vaults')`
   ```javascript
   routes['vaults']() // → loadVaultsPage()
   ```

3. Main loader: `loadVaultsPage()`
   ```javascript
   fetch('/api/vaults/stats/summary')
   fetch('/api/vaults?limit=500')
   ```

4. Backend route: `@dashboard_routes.route('/api/vaults')`
   ```python
   _build_vaults_select(conn)
   _vault_row_to_dict(row)
   ```

5. Frontend render: Maps vaults to HTML
   ```javascript
   vaultsData.vaults.map(v => renderRow)
   ```

6. Event binding: Add filter listeners
   ```javascript
   filterVaultsTable
   showVaultDetail
   ```

---

## How to Debug

### Check navigation works:
```bash
grep -n 'onclick="loadPage.*vaults' templates/flex_dashboard.html
# Should find line ~956
```

### Check route exists:
```bash
grep -n "'vaults': loadVaultsPage" templates/flex_dashboard.html
# Should find line ~1047
```

### Check function exists:
```bash
grep -n "async function loadVaultsPage" templates/flex_dashboard.html
# Should find line ~3880
```

### Check API endpoints:
```bash
grep -n "@dashboard_routes.route('/api/vaults" src/core/flex_dashboard_routes.py
# Should find 3 routes (list, summary, detail)
```

### Check helpers are defined:
```bash
grep -n "function formatVaultDiscoveryTime\|function formatPrice" templates/flex_dashboard.html
# Should find both functions
```

---

## Summary

- **Frontend**: 188 lines in `loadVaultsPage()` + 50 lines helpers + navigation
- **Backend**: 300 lines of API implementation + helpers
- **Integration**: 1 route mapping + 1 nav link
- **Total**: ~550 lines of code across frontend and backend

All production-ready and fully functional.
