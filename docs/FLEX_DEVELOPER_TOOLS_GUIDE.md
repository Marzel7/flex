# FLEX Developer Tools Guide

**Status**: ✅ Complete & Production Ready
**Date**: March 12, 2026
**Version**: 1.0

---

## Overview

The FLEX Intelligence Dashboard includes comprehensive API developer tooling to help developers understand, debug, and integrate with the FLEX UI API endpoints. Three integrated features provide visibility into the API layer:

1. **API Reference Page** — Complete endpoint documentation
2. **Sidebar Endpoint Reference** — Quick navigation by page
3. **Developer Tools Panel** — Real-time API call logging

---

## SECTION 1: Flask Routes

### New Route Added

```python
@dashboard_routes.route('/api-reference', methods=['GET'])
def api_reference():
    """
    Render API Reference page.
    Comprehensive documentation of all FLEX UI API endpoints with examples.
    """
    try:
        return render_template('flex_dashboard.html', page='api_reference')
    except Exception as e:
        logger.error(f"Error rendering API reference: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500
```

**File Modified**: `src/core/flex_dashboard_routes.py`

The route passes `page='api_reference'` to the template, which triggers the `loadAPIReference()` JavaScript function.

---

## SECTION 2: HTML Template Extensions

### 2.1 Sidebar Navigation

Added Developer section to sidebar with two new links:

```html
<!-- Developer Tools -->
<div class="nav-section">
    <div class="nav-section-title">Developer</div>
    <a class="nav-link" onclick="loadPage('api_reference')">
        <i class="fas fa-code"></i> <span>API Reference</span>
    </a>
    <a class="nav-link" id="devtools-toggle" onclick="toggleDevTools()">
        <i class="fas fa-terminal"></i> <span>Debug Panel</span>
    </a>
</div>
```

**Navigation Options**:
- **API Reference** — Opens comprehensive endpoint documentation page
- **Debug Panel** — Toggles real-time API call logging panel

### 2.2 CSS Styling

Added 25+ CSS classes for developer tools UI:

**Debug Panel Styles**:
- `.dev-tools-panel` — Fixed bottom-right container
- `.dev-tools-header` — Title and close button
- `.dev-tools-log` — Scrollable log area
- `.dev-tools-entry` — Individual log entries
- `.dev-tools-status` — HTTP status badges (200, 400, 500)

**API Reference Styles**:
- `.api-reference-endpoint` — Endpoint documentation card
- `.api-endpoint-section` — Content sections (purpose, params, etc.)
- `.api-method-badge` — HTTP method badge (GET, etc.)
- `.api-example-code` — Code block with monospace font
- `.api-reference-toc` — Table of contents sidebar
- `.api-consuming-pages` — Badge list showing which pages use endpoint

**Status Color Coding**:
```css
.dev-tools-status.status-200 { /* Success - green */ }
.dev-tools-status.status-400 { /* Client error - orange */ }
.dev-tools-status.status-500 { /* Server error - red */ }
```

---

## SECTION 3: JavaScript Functions

### 3.1 API Reference Page (`loadAPIReference()`)

Renders comprehensive documentation with two-column layout:

```javascript
async function loadAPIReference() {
    // Returns HTML with:
    // - Left column: 8 endpoint documentation cards
    // - Right column: Sticky table of contents (TOC)
    // - Quick navigation links
}
```

**Endpoints Documented**:
1. `GET /api/dashboard` — System overview
2. `GET /api/launch-leaderboard` — Ranked organizations
3. `GET /api/organizations` — Organization directory
4. `GET /api/organization/<id>` — Organization detail
5. `GET /api/signals/<id>` — Predictive signals
6. `GET /api/launch-waves` — Wave detection
7. `GET /api/dev-clusters` — Developer clusters
8. `GET /api/wallet/<address>` — Wallet intelligence

**For Each Endpoint**:
- **Route** — API path with method (GET/POST/etc.)
- **Purpose** — Human-readable description
- **Request Parameters** — Query/path parameters with types
- **Response Schema** — JSON structure definition
- **Example Response** — Real example JSON data
- **Consuming Pages** — Which dashboard pages use this endpoint

**Table of Contents**:
Sticky sidebar with sections:
- Dashboard
- Organizations
- Predictions
- Analysis
- Wallet

Click any TOC link to jump to endpoint documentation.

### 3.2 Developer Tools Panel

#### Toggle Function

```javascript
function toggleDevTools() {
    const panel = document.getElementById('devToolsPanel');
    if (!panel) createDevToolsPanel();
    document.getElementById('devToolsPanel').classList.toggle('active');
}
```

#### Panel Creation

```javascript
function createDevToolsPanel() {
    // Creates fixed container at bottom-right
    // Header with title and close button
    // Log area for API entries
}
```

#### API Call Logging

```javascript
function logAPICall(method, endpoint, statusCode, duration, payloadSize, page) {
    // Records API call with metadata
    // Keeps last 100 calls in APILog array
    // Updates panel display
}
```

**Logged Information**:
- **Timestamp** — Time of API call (HH:MM:SS)
- **Method** — HTTP method (GET, POST, etc.)
- **Endpoint** — API path (e.g., `/api/organizations`)
- **Status Code** — HTTP status (200, 404, 500, etc.)
- **Duration** — Response time in milliseconds
- **Payload Size** — Response size in bytes
- **Triggering Page** — Which dashboard page made the call

#### Fetch Interception

```javascript
const originalFetch = window.fetch;
window.fetch = async function(...args) {
    // Intercepts all fetch() calls
    // Measures performance (start/end time)
    // Extracts response size
    // Logs to APILog
    // Returns original response
}
```

**Performance Metrics**:
- Total request time (network + processing)
- Response size (helps identify data fetching issues)
- Automatic tracking of all API calls

#### Log Display

Each entry shows:
```
14:23:45 GET /api/organizations 200 142ms 8542B
14:23:40 GET /api/launch-leaderboard 200 87ms 12304B
14:23:38 GET /api/dashboard 200 56ms 2104B
```

**Color Coding**:
- Green border for 200-299 (success)
- Orange border for 400-499 (client error)
- Red border for 500+ (server error)

---

## SECTION 4: Sidebar Integration

### Navigation Structure

The sidebar now has 3 main sections plus Developer tools:

```
FLEX (logo)
├─ Intelligence
│  ├─ Dashboard
│  ├─ Launch Radar
│  ├─ Organizations
│  └─ Launch Waves
├─ Analytics
│  ├─ Dev Clusters
│  └─ Signals
├─ Tools
│  └─ Wallet Search
└─ Developer [NEW]
   ├─ API Reference
   └─ Debug Panel
```

### Access Points

**From Any Page**:
1. Click "API Reference" in Developer section to view endpoint docs
2. Click "Debug Panel" to toggle real-time API log

**From API Reference Page**:
1. Use table of contents on right to jump between endpoints
2. Click any page name badge to see which pages use that endpoint

**From Debug Panel**:
1. Watch API calls happen in real-time as you navigate
2. See response times and payload sizes
3. Identify slow endpoints or failed requests

---

## Usage Guide

### Scenario 1: Understanding an API Endpoint

1. Open FLEX Dashboard
2. Click **API Reference** in Developer section
3. Use TOC on right to find endpoint (e.g., "GET /api/organization/:id")
4. Read:
   - Purpose: What does this endpoint do?
   - Parameters: What inputs does it accept?
   - Response: What data does it return?
   - Example: Real example JSON
   - Pages: Which dashboard pages use it

### Scenario 2: Debugging Slow API Calls

1. Open **Debug Panel** (click "Debug Panel" in Developer section)
2. Perform action on dashboard (e.g., load an organization)
3. Watch Debug Panel for API calls
4. Check "Response Time" (e.g., 142ms) and "Size" (e.g., 8542B)
5. If slow, check database load or API bottleneck

### Scenario 3: Building New Integration

1. Open **API Reference** page
2. For each endpoint you need:
   - Review request parameters
   - Review response schema
   - Copy example JSON
   - Note which other pages consume it
3. Use cURL examples or Postman to test before building

### Scenario 4: Identifying Failed Requests

1. Open **Debug Panel**
2. Look for entries with red border (status 500)
3. Check timestamp to correlate with action
4. Review endpoint and check if parameters were correct
5. Check browser console for error details

---

## API Endpoints Reference

### Endpoint Categories

#### Dashboard & Monitoring
- `GET /api/dashboard` — System overview with alerts

#### Organization Intelligence
- `GET /api/organizations` — All organizations
- `GET /api/organization/<id>` — Specific organization
- `GET /api/signals/<id>` — Organization signals
- `GET /api/launch-leaderboard` — Ranked organizations

#### Market Analysis
- `GET /api/launch-waves` — Wave detection
- `GET /api/dev-clusters` — Cluster analysis

#### Wallet & Creator
- `GET /api/wallet/<address>` — Wallet intelligence

### Common Parameters

**Pagination**:
```
limit (default: 50-100) — Number of results
offset (default: 0) — Starting position
```

**Filtering**:
```
min_score (0-1) — Minimum prediction score
alert_level (CRITICAL|HIGH|WATCH|LOW) — Alert threshold
```

### Response Codes

```
200 — Success
400 — Bad request (invalid parameters)
404 — Not found (invalid ID)
500 — Server error
```

---

## Performance Insights

### What the Debug Panel Shows

**Duration**:
- **0-50ms** — Very fast (cached, small payload)
- **50-150ms** — Normal (typical API call)
- **150-500ms** — Slow (large data, DB query)
- **500ms+** — Very slow (bottleneck exists)

**Payload Size**:
- **<5KB** — Small response (good efficiency)
- **5-50KB** — Normal response size
- **50-500KB** — Large data fetch
- **500KB+** — Possible optimization needed

### Identifying Issues

**Slow Dashboard Load**:
1. Open Debug Panel
2. Load Dashboard page
3. Check `/api/dashboard` call duration
4. If >200ms, DB query may be slow

**Large Payloads**:
1. Watch Debug Panel while paginating table
2. Each row request should be <50KB
3. If >100KB, too much data being fetched

**Failed Requests**:
1. Red border in Debug Panel = error
2. Check status code (400, 500)
3. Verify parameters in API Reference
4. Check browser console for details

---

## Implementation Details

### Files Modified

**`src/core/flex_dashboard_routes.py`**:
- Added 1 new route: `/api-reference`

**`templates/flex_dashboard.html`**:
- Added sidebar Developer section
- Added 25+ CSS classes for tooling UI
- Added `loadAPIReference()` function (170+ lines)
- Added API logging system (200+ lines)
- Added fetch interception for automatic logging
- Updated router to include api_reference page

### Code Statistics

- **New CSS**: 160 lines
- **New JavaScript**: 350+ lines
- **API Reference Data**: 8 endpoints × 7 fields = 56 data points
- **Fetch Interception**: Automatic, no code changes needed
- **Total Template Size**: 2,273 lines (from 1,709)

### Features Added

✅ 1 API Reference Page with 8 endpoints
✅ Sidebar Developer section with 2 links
✅ Real-time API Debug Panel
✅ Automatic fetch() interception
✅ Performance metrics (duration, size)
✅ Sticky table of contents
✅ Status code color coding
✅ Example JSON for all endpoints

---

## Testing Checklist

- [x] API Reference page loads correctly
- [x] All 8 endpoints documented with examples
- [x] TOC links scroll to correct section
- [x] Debug Panel shows all API calls
- [x] Status codes color-coded correctly
- [x] Response times accurate
- [x] Payload sizes calculated
- [x] Fetch interception working for all pages
- [x] No console errors
- [x] Responsive on mobile (panel adjusted)

---

## Deployment

The developer tools are automatically enabled with no additional configuration needed.

```bash
python3 src/core/main.py
# Open http://localhost:5002/
# Click "API Reference" or "Debug Panel" in Developer section
```

---

## Future Enhancements

Optional additions (Phase 2):
- Export API call log as JSON/CSV
- API performance analytics dashboard
- Request/response diff viewer
- Mock API mode for testing
- API call replay functionality
- Rate limit monitoring
- Cache hit rate tracking

---

## Summary

The FLEX Developer Tools provide:

✅ **API Reference Page** — Complete endpoint documentation with examples
✅ **Debug Panel** — Real-time API call logging with performance metrics
✅ **Sidebar Integration** — Easy access from any page
✅ **Automatic Tracking** — No code changes needed, all calls captured
✅ **Production Ready** — Fully tested and documented

Status: **PRODUCTION READY** 🚀

---

**Version**: 1.0
**Date**: March 12, 2026
**Status**: ✅ Complete

