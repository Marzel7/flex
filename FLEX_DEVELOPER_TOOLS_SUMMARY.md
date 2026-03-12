# FLEX Developer Tools — Quick Reference

**Status**: ✅ PRODUCTION READY
**Date**: March 12, 2026
**Commit**: bf6f363

---

## Overview

Extended the FLEX Intelligence Dashboard with three integrated API developer tools to help developers understand, debug, and integrate with the FLEX UI API.

---

## Three Features Implemented

### 1️⃣ API Reference Page

**Access**: Click **"API Reference"** in Developer section of sidebar

**What It Shows**:
- Complete documentation for all 8 API endpoints
- For each endpoint:
  - Route and HTTP method
  - Purpose and description
  - Request parameters with types
  - Response schema (JSON format)
  - Example JSON response
  - Which dashboard pages use it

**Endpoints Documented**:
1. `GET /api/dashboard` — System overview with alerts
2. `GET /api/launch-leaderboard` — Ranked organizations
3. `GET /api/organizations` — All organizations
4. `GET /api/organization/<id>` — Organization detail
5. `GET /api/signals/<id>` — Predictive signals
6. `GET /api/launch-waves` — Wave detection
7. `GET /api/dev-clusters` — Developer clusters
8. `GET /api/wallet/<address>` — Wallet intelligence

**Features**:
- ✅ Sticky table of contents for quick navigation
- ✅ Click TOC links to jump to endpoint
- ✅ Page badges show which dashboard pages use each endpoint
- ✅ Code blocks for response examples
- ✅ Clean two-column layout (content + TOC)
- ✅ Dark theme styling

---

### 2️⃣ Debug Panel

**Access**: Click **"Debug Panel"** in Developer section of sidebar

**What It Shows**:
Real-time log of all API calls with:
- **Timestamp** — Time of call (HH:MM:SS)
- **Method** — HTTP method (GET, POST, etc.)
- **Endpoint** — API path (e.g., `/api/organizations`)
- **Status** — HTTP status code (200, 400, 500, etc.)
- **Duration** — Response time in milliseconds
- **Size** — Response payload size in bytes

**Color Coding**:
- 🟢 **Green border** (200-299) — Success
- 🟠 **Orange border** (400-499) — Client error
- 🔴 **Red border** (500+) — Server error

**Features**:
- ✅ Fixed position (bottom-right corner)
- ✅ Automatic fetch() interception
- ✅ No code changes needed
- ✅ Keeps last 100 calls
- ✅ Real-time updates
- ✅ Toggle on/off easily
- ✅ Responsive on mobile

**How It Works**:
Every time an API call is made anywhere on the dashboard, it's automatically captured and logged. You see it happen in real-time as you navigate pages.

---

### 3️⃣ Sidebar Developer Section

**Location**: Bottom of sidebar navigation

**Content**:
```
Developer
├─ API Reference  ← Click to view endpoint docs
└─ Debug Panel    ← Click to toggle API log
```

**Features**:
- ✅ Integrated seamlessly into existing sidebar
- ✅ Icons for quick recognition
- ✅ Dark theme styling
- ✅ Works on mobile

---

## Usage Examples

### Example 1: Understanding How Organizations API Works

1. Click **API Reference** in Developer section
2. Use table of contents to find `GET /api/organizations`
3. Read:
   - **Purpose**: "Browse all detected developer organizations..."
   - **Parameters**: `limit`, `offset`, `min_score`
   - **Example**: See real JSON response
4. See which pages use it (Organizations, Org Explorer)

### Example 2: Debugging Slow Dashboard Load

1. Open **Debug Panel**
2. Load the Dashboard page
3. Watch API calls appear in real-time
4. Check `/api/dashboard` call:
   - Is duration > 200ms? (might be slow)
   - Is response size > 50KB? (might have too much data)
5. Review in API Reference to understand what it returns

### Example 3: Identifying Failed API Call

1. Open **Debug Panel**
2. See red border entry (error status code)
3. Check:
   - **Endpoint** — Which API failed?
   - **Status** — What error? (404 = not found, 500 = server error)
   - **Time** — When did it happen?
4. Go back to **API Reference** to check what parameters are required

### Example 4: Building New Feature Using API

1. Open **API Reference**
2. For each endpoint you need:
   - Review parameters required
   - Review response schema
   - Copy example JSON
3. Open **Debug Panel** while navigating that feature
4. Watch API calls to verify correct parameters being sent

---

## Quick Navigation

### Where Are My API Docs?

**In Dashboard**:
1. Click "API Reference" in Developer section

**Online**:
- Full docs: See `FLEX_DEVELOPER_TOOLS_GUIDE.md` in project root

### How Do I Know If An API Call Failed?

**In Debug Panel**:
- Red border = error (status 400+)
- Status code shows exact error (404 = not found, 500 = server error)

### How Do I Check What An Endpoint Returns?

**In API Reference**:
1. Find the endpoint
2. Scroll to "Example Response" section
3. See real JSON data

### Which Pages Use An Endpoint?

**In API Reference**:
1. Find the endpoint
2. Scroll to "Consuming Pages" section
3. See page badges

---

## Performance Insights from Debug Panel

### Response Times

| Duration | Assessment | Example |
|----------|------------|---------|
| 0-50ms | Very fast | Small cached response |
| 50-150ms | Normal | Typical API call |
| 150-500ms | Slow | Large data fetch or DB query |
| 500ms+ | Very slow | Bottleneck exists |

### Payload Sizes

| Size | Assessment | Example |
|------|------------|---------|
| <5KB | Very small | Dashboard KPIs |
| 5-50KB | Normal | Organization list |
| 50-500KB | Large | Full org with members |
| 500KB+ | Too large | Optimize needed |

---

## API Endpoints at a Glance

### Dashboard & Overview
- `GET /api/dashboard` — System KPIs and alerts

### Organizations
- `GET /api/organizations?limit=500` — Browse all
- `GET /api/organization/1` — Get specific org
- `GET /api/launch-leaderboard?limit=100` — Ranked list

### Intelligence
- `GET /api/signals/1` — Get org signals

### Analysis
- `GET /api/launch-waves?limit=50` — Waves
- `GET /api/dev-clusters?limit=50` — Clusters

### Wallet
- `GET /api/wallet/8GhG...` — Wallet details

---

## Files Changed

```
src/core/flex_dashboard_routes.py
  +1 route: /api-reference

templates/flex_dashboard.html
  +Developer nav section
  +CSS for tooling UI (275 lines)
  +JavaScript functions (350+ lines)
  +Router integration
  +Fetch interception
```

---

## Testing

✅ All features tested and working:
- API Reference page loads without errors
- All 8 endpoints documented with examples
- Debug Panel captures all API calls
- Status codes color-coded correctly
- Performance metrics accurate
- No console errors
- Responsive on mobile

---

## Deployment

Already integrated and ready to use:

```bash
python3 src/core/main.py
# Open http://localhost:5002/
# Click "API Reference" or "Debug Panel" in Developer section
```

---

## Documentation

**This Document**:
- Quick reference for all three features

**Full Guides**:
1. **FLEX_DEVELOPER_TOOLS_GUIDE.md** — Complete user guide with usage scenarios
2. **FLEX_DEVELOPER_TOOLS_IMPLEMENTATION.md** — Technical implementation details

---

## Summary

✅ **API Reference** — Understand all endpoints
✅ **Debug Panel** — Monitor API calls in real-time
✅ **Sidebar Integration** — Easy access from anywhere
✅ **Production Ready** — No changes needed, use immediately

**Status**: READY 🚀

---

**Date**: March 12, 2026
**Commit**: bf6f363
**Status**: ✅ Complete & Production Ready
