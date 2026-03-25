# Vaults Page - Quick Reference Guide

## ✅ Status: FULLY IMPLEMENTED AND WORKING

The Vaults page is complete and production-ready. All features have been implemented and integrated into your dashboard.

---

## What You Get

### Frontend Components (in `templates/flex_dashboard.html`)

| Function | Purpose | Lines |
|----------|---------|-------|
| `loadVaultsPage()` | Load vaults page with stats, filters, and table | 3880-4067 |
| `filterVaultsTable()` | Client-side real-time filtering | 4069-4086 |
| `showVaultDetail(mint)` | Show modal with full vault/token details | 4088-4243 |
| `formatVaultDiscoveryTime(secs)` | Convert seconds to "Xh Ym" format | 3545-3556 |
| `formatPrice(price)` | Format USD prices with appropriate precision | 3559-3564 |

### Page Layout

```
┌─────────────────────────────────────┐
│ Vault Discovery & Validation        │
│ Token/pool vault discovery latency  │
└─────────────────────────────────────┘

[Total] [Validated] [Pending] [Rejected]
[Avg Time] [Avg Attempts] [Possibly Late] [Likely Late]

Search: [____] Status: [dropdown] Quality: [dropdown]

│ Token Mint │ Pool │ Status │ Strategy │ Attempts │ Time │ Quality │ Category │ Confidence │
├─────────────────────────────────────────────────────────────────────────────────────────┤
│ FVNedi...  │ Gc... │ ✓      │ unknown  │ 0        │ 0s   │ ✓ good  │ slow_rug │ 27%        │
│ 4UPUU...   │ 95... │ ✓      │ unknown  │ 0        │ 0s   │ ✓ good  │ unknown  │ N/A        │
└─────────────────────────────────────────────────────────────────────────────────────────┘

Click row → Detail Modal
```

---

## Rendering Rules (Important!)

### Tracking Quality
```javascript
good              → ✓ green
possibly_late     → ⚠ yellow
likely_late       → ⚠⚠ red
null/missing      → ? gray
invalid           → ? gray
```

### Category
Valid: `immediate_rug`, `runner`, `faded_runner`, `choppy_runner`, `rug`, `slow_rug`, `insufficient_history`, `unknown`
Invalid/missing → `N/A`

### Status
```
validated  → green color
pending    → yellow color
rejected   → red color
```

### Discovery Time
```
If vault_discovery_time_secs exists   → formatVaultDiscoveryTime(value)
Else if status is "pending"           → "Pending"
Else                                  → "N/A"
```

### Strategy
```
Use vault_discovery_strategy if available
Fall back to discovery_method
Default to "N/A"
(DO NOT default to "unknown")
```

### Attempts
```
If numeric and non-null  → show value
DO NOT default to 0
Default to "N/A"
```

### Confidence
```
If numeric and non-null  → "X%" (rounded)
Else                     → "N/A"
```

---

## API Endpoints

All endpoints are in `src/core/flex_dashboard_routes.py`

### 1. GET /api/vaults/stats/summary
**Returns**: Summary statistics for dashboard cards

```json
{
  "total_vaults": 42,
  "validated": 42,
  "pending": 0,
  "rejected": 0,
  "avg_discovery_time_secs": 3.5,
  "avg_discovery_attempts": 1.0,
  "pct_possibly_late": 0.0,
  "pct_likely_late": 2.4
}
```

### 2. GET /api/vaults?limit=500&category=&tracking_quality=&status=
**Returns**: Paginated list of vaults with optional filters

```json
{
  "filters": {
    "category": null,
    "status": null,
    "strategy": null,
    "tracking_quality": null
  },
  "total": 42,
  "vaults": [
    {
      "mint": "FVNediAcMzQ69RsnYLnijFRqT7tC7u1yYmiYsx3Gpump",
      "pool_address": "GcpyrpRqx9qXx5r2qTVSdgHogZLHZyYKV7AXMjeHMtv4",
      "vault_validation_status": "validated",
      "vault_discovery_strategy": "unknown",
      "vault_discovery_attempts": 0,
      "vault_discovery_time_secs": 0,
      "tracking_quality": "good",
      "category": "slow_rug",
      "confidence": 0.276,
      ...
    }
  ]
}
```

### 3. GET /api/vaults/<mint>
**Returns**: Complete vault and token behavior information

```json
{
  "mint": "FVNediAcMzQ69RsnYLnijFRqT7tC7u1yYmiYsx3Gpump",
  "vault": {
    "base_account": "JA2qd9WYYbP65gQoL3WJuJzQqaEkkD9Zr9czmjCDmDYq",
    "pool_address": "GcpyrpRqx9qXx5r2qTVSdgHogZLHZyYKV7AXMjeHMtv4",
    "validation_status": "validated",
    "resolution_state": "pending",
    "discovery_strategy": "unknown",
    "discovery_method": "pumpfun_v1_discovered",
    "discovery_attempts": 0,
    "discovery_time_secs": 0,
    "created_at": 1774288501,
    "last_validation_at": 1774288501,
    "resolved_at": null,
    ...
  },
  "token": {
    "category": "slow_rug",
    "confidence": 0.276,
    "tracking_quality": "good",
    "initial_price_observed_usd": 5.473e-05,
    "peak_price_usd": 8.096e-05,
    "latest_price_usd": 1.088e-05,
    "max_return_observed": 1.479,
    "drawdown_from_peak": 0.866,
    ...
  }
}
```

---

## Features

### Summary Cards (8)
- Total Vault Records
- Validated (green)
- Pending (yellow)
- Rejected (red)
- Avg Discovery Time (formatted: "1h 30m" or "45s")
- Avg Attempts (decimal: "1.2")
- % Possibly Late (yellow)
- % Likely Late (red)

### Filtering
- **Mint Search**: Case-insensitive substring match
- **Status Filter**: Dropdown (validated/pending/rejected)
- **Quality Filter**: Dropdown (good/possibly_late/likely_late)
- **Real-time**: Updates as you type/select

### Table (9 columns)
1. Token Mint (shortened)
2. Pool Address (shortened)
3. Status (colored badge)
4. Strategy (with fallback)
5. Attempts (no false 0s)
6. Discovery Time (formatted)
7. Quality (icon + text)
8. Category (validated)
9. Confidence (as %)

### Detail Modal
- **Vault Discovery**: Status, strategy, method, attempts, time
- **Timeline**: Created, validated, resolved timestamps
- **Pool Info**: Addresses, tokens, decimals
- **Token Tracking**: Quality, category, confidence, prices, returns, drawdown

---

## How It Works

### 1. User clicks "Vaults" in sidebar
```javascript
loadPage('vaults')  // Routes to loadVaultsPage()
```

### 2. Page loads
- Fetches stats from `/api/vaults/stats/summary`
- Renders 8 stat cards
- Fetches vaults from `/api/vaults?limit=500`
- Renders table with proper formatting
- Attaches filter event listeners

### 3. User types in search or selects filter
- `filterVaultsTable()` runs immediately
- Shows/hides table rows based on criteria
- No additional API calls (client-side only)

### 4. User clicks table row
- `showVaultDetail(mint)` called
- Fetches `/api/vaults/<mint>`
- Renders modal with full details
- Modal shown via Bootstrap 5

---

## Key Implementation Details

### Error Handling
- Missing data rendered as "N/A" (not empty or 0)
- Invalid categories filtered out
- Quality states validated
- Null checks on all optional fields

### Performance
- Client-side filtering (no API calls on filter changes)
- Summary stats cached (8 metrics, static)
- Table pagination via `limit` parameter (500 default)
- Detail modals lazy-loaded on click

### Data Quality
- Confidence shown as percentage only if numeric
- Strategy falls back to discovery_method
- Attempts never defaults to 0
- Discovery time shows "Pending" for pending vaults

---

## Testing

### Check it's working:
```bash
# 1. Page loads (sidebar has Vaults link)
curl http://localhost:5002/ | grep -o 'Vaults'

# 2. Stats endpoint works
curl http://localhost:5002/api/vaults/stats/summary | python3 -m json.tool

# 3. Table endpoint works
curl http://localhost:5002/api/vaults?limit=1 | python3 -m json.tool

# 4. Detail endpoint works
curl http://localhost:5002/api/vaults/FVNediAcMzQ69RsnYLnijFRqT7tC7u1yYmiYsx3Gpump \
  | python3 -m json.tool
```

### Check features:
- ✅ Navigate to Vaults page - shows stats and table
- ✅ Click table row - shows detail modal
- ✅ Type in search - filters table in real-time
- ✅ Select status filter - shows only matching rows
- ✅ Select quality filter - shows only matching rows
- ✅ Close modal - clean removal

---

## Files Modified

### Backend
- **File**: `src/core/flex_dashboard_routes.py`
- **Lines**: 778-1060 (new Vaults API section)
- **Functions**:
  - `_build_vaults_select()` - Dynamic SQL builder
  - `_vault_row_to_dict()` - Row normalization
  - `/api/vaults` - List endpoint
  - `/api/vaults/stats/summary` - Stats endpoint
  - `/api/vaults/<mint>` - Detail endpoint

### Frontend
- **File**: `templates/flex_dashboard.html`
- **Navigation**: Lines 956-958 (sidebar link)
- **Route**: Line 1047 (`'vaults': loadVaultsPage`)
- **Functions**:
  - `loadVaultsPage()` - Lines 3880-4067
  - `filterVaultsTable()` - Lines 4069-4086
  - `showVaultDetail()` - Lines 4088-4243
  - `formatVaultDiscoveryTime()` - Lines 3545-3556
  - `formatPrice()` - Lines 3559-3564

---

## Next Steps (Optional)

1. **Server-side Filtering**: Move filtering to backend for >1000 vaults
2. **Sorting**: Click column headers to sort (DataTables integration)
3. **Export**: CSV export of filtered results
4. **History**: Show token behavior classification history
5. **Auto-refresh**: Periodic reload of stats (like token_behaviour page)
6. **Pagination**: Visual pagination controls for large result sets

---

## Support

### Common Issues

**Q: "Vaults" link doesn't work**
- Check that `loadVaultsPage()` is defined
- Check that route `'vaults': loadVaultsPage` exists in `loadPage()`

**Q: Table shows "N/A" for everything**
- Check API endpoints are responding: `/api/vaults/stats/summary`
- Check browser console for errors
- Check Flask server is running on port 5002

**Q: Detail modal doesn't load**
- Check `/api/vaults/<mint>` endpoint responds
- Check network tab in browser dev tools
- Check console for JavaScript errors

**Q: Filters don't work**
- Check `filterVaultsTable()` function is defined
- Check filter input IDs match: `vault-search-mint`, `vault-filter-status`, `vault-filter-quality`
- Try page refresh

---

## Reference

### Supported Category Values
- `immediate_rug`
- `runner`
- `faded_runner`
- `choppy_runner`
- `rug`
- `slow_rug`
- `insufficient_history`
- `unknown`

### Supported Quality Values
- `good`
- `possibly_late`
- `likely_late`

### Supported Status Values
- `validated`
- `pending`
- `rejected`

---

## Summary

Your Vaults page is **production-ready** with:
- ✅ Full frontend implementation
- ✅ All requested features
- ✅ Proper data handling
- ✅ Real API integration
- ✅ Complete error handling
- ✅ No garbage data

You can navigate to it now and start using it!
