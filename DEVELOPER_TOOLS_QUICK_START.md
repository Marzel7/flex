# FLEX Developer Tools — Quick Start

**Status**: ✅ Ready to Use
**Date**: March 12, 2026

---

## 30-Second Overview

The FLEX Dashboard now includes three built-in developer tools:

1. **API Reference** — See all endpoints, parameters, and examples
2. **Debug Panel** — Watch API calls happen in real-time
3. **Developer Sidebar** — One-click access to both tools

---

## Getting Started (2 steps)

### Step 1: Start the Dashboard
```bash
python3 src/core/main.py
```

Then open: `http://localhost:5002/`

### Step 2: Access Developer Tools

Look for the **Developer** section at the bottom of the sidebar:
- Click **API Reference** → View endpoint documentation
- Click **Debug Panel** → Monitor API calls

---

## API Reference

### What It Shows

Complete documentation for all 8 API endpoints used by the dashboard:

```
GET /api/dashboard              System overview
GET /api/launch-leaderboard     Ranked organizations
GET /api/organizations          Organization directory
GET /api/organization/<id>      Organization detail
GET /api/signals/<id>           Predictive signals
GET /api/launch-waves           Wave detection
GET /api/dev-clusters           Developer clusters
GET /api/wallet/<address>       Wallet intelligence
```

### For Each Endpoint

- **Purpose** — What does it do?
- **Parameters** — What inputs does it accept?
- **Response** — What JSON structure does it return?
- **Example** — Real example JSON data
- **Pages** — Which dashboard pages use it?

### How to Use

1. Click **API Reference** in sidebar
2. Use table of contents on right to find endpoint
3. Click to jump to that endpoint
4. Read description, parameters, and example
5. Copy example JSON if needed

---

## Debug Panel

### What It Shows

Real-time log of all API calls as they happen:

```
14:23:45  GET  /api/organizations    200  142ms  8542B
14:23:40  GET  /api/launch-leaderboard 200  87ms  12304B
14:23:38  GET  /api/dashboard        200  56ms   2104B
```

**Columns**:
- **Time** — When the API call happened
- **Method** — HTTP method (GET, POST, etc.)
- **Endpoint** — Which API was called
- **Status** — HTTP status code (200 = success, 400 = error, 500 = error)
- **Duration** — How long it took (milliseconds)
- **Size** — Response size (bytes)

### Color Coding

- 🟢 **Green border** — Success (200-299)
- 🟠 **Orange border** — Client error (400-499)
- 🔴 **Red border** — Server error (500+)

### How to Use

1. Click **Debug Panel** to enable it
2. Perform action on dashboard (load page, search, etc.)
3. Watch API calls appear in real-time
4. Check:
   - **Status** — Did it succeed?
   - **Duration** — Was it fast or slow?
   - **Size** — How much data?

---

## Common Tasks

### Task 1: I Want to Understand How `/api/organizations` Works

1. Click **API Reference**
2. Use TOC to find `GET /api/organizations`
3. Read:
   - Purpose: Browse all organizations
   - Parameters: `limit`, `offset`, `min_score`
   - Example: Shows real JSON response
4. See which pages use it: Organizations, Org Explorer

### Task 2: My Dashboard Loads Slowly

1. Click **Debug Panel** to enable logging
2. Reload Dashboard page
3. Watch for API calls
4. Check if any are slow (>500ms)
5. If slow:
   - Click **API Reference**
   - Find that endpoint
   - Understand what it returns
   - Check if response is large (>100KB)

### Task 3: An API Call Failed

1. Look in **Debug Panel**
2. Find entry with red border (error status)
3. Note:
   - Which endpoint failed
   - What status code (400, 404, 500, etc.)
   - When it happened
4. Click **API Reference**
5. Find endpoint and check:
   - Required parameters
   - Error codes it might return
6. Verify dashboard sent correct parameters

### Task 4: I'm Building Integration With FLEX API

1. Open **API Reference**
2. For each endpoint you need:
   - Study request parameters
   - Review response schema
   - Copy example JSON
3. Build your integration with those parameters/responses
4. Test with:
   - cURL: `curl http://localhost:5002/api/...`
   - Browser: DevTools Network tab
   - **Debug Panel**: Watch dashboard API calls

---

## Performance Guidelines

### Response Time Interpretation

| Duration | Meaning |
|----------|---------|
| 0-50ms | Very fast (cached or small response) |
| 50-150ms | Normal (typical API call) |
| 150-500ms | Slow (large data or DB query) |
| 500ms+ | Very slow (bottleneck) |

### Payload Size Interpretation

| Size | Meaning |
|------|---------|
| <5KB | Small (good) |
| 5-50KB | Normal (expected) |
| 50-100KB | Large (consider pagination) |
| 100KB+ | Too large (optimization needed) |

---

## Endpoints at a Glance

### Dashboard & Overview
- `GET /api/dashboard` — KPIs, alerts, counts

### Organizations
- `GET /api/organizations` — Browse all (with filters)
- `GET /api/organization/<id>` — Specific organization
- `GET /api/launch-leaderboard` — Ranked by score

### Intelligence
- `GET /api/signals/<id>` — 8 predictive signals

### Market Analysis
- `GET /api/launch-waves` — Coordinated launches
- `GET /api/dev-clusters` — Developer clusters

### Wallets
- `GET /api/wallet/<address>` — Wallet details

---

## Tips & Tricks

### Tip 1: Copy Example JSON

In API Reference:
1. Find endpoint
2. Scroll to "Example Response" section
3. Example is in code block with monospace font
4. Highlight and copy

### Tip 2: See Which Pages Use An Endpoint

In API Reference:
1. Find endpoint
2. Scroll to "Consuming Pages" section
3. See colored badges with page names
4. Click page link to go to that page

### Tip 3: Jump Between Endpoints

In API Reference:
1. Use table of contents on right
2. Click endpoint name
3. Page auto-scrolls to that endpoint

### Tip 4: Monitor Performance

In Debug Panel:
1. Leave it open while using dashboard
2. Watch for slow API calls
3. If see duration >200ms, that endpoint might be slow
4. Open **API Reference** to check what it returns
5. Consider if response size is too large

---

## Troubleshooting

### API Reference Won't Load

**Check**: Are other pages working?
- If Dashboard, Radar, etc. work → might be browser cache
- If nothing works → check server logs

**Solution**:
1. Refresh page (Cmd+R or Ctrl+R)
2. Clear browser cache
3. Check browser console for errors

### Debug Panel Not Showing

**Check**: Is it enabled?
- Click "Debug Panel" in sidebar
- Look for active state (button should be highlighted)

**Check**: Is there API activity?
- Debug Panel only shows when API calls happen
- Try loading a page that makes API calls

### Some Endpoints Missing

**Check**: Are you on the latest code?
- Might be on older version
- Pull latest code and restart server

---

## Need More Info?

**For Quick Reference**:
- `FLEX_DEVELOPER_TOOLS_SUMMARY.md` — Overview and examples

**For Complete Details**:
- `FLEX_DEVELOPER_TOOLS_GUIDE.md` — Full user guide
- `docs/FLEX_DEVELOPER_TOOLS_COMPLETE.md` — Complete documentation

---

## Summary

✅ **API Reference** — Understand all endpoints
✅ **Debug Panel** — Monitor API calls
✅ **Easy Access** — One click from sidebar
✅ **No Configuration** — Works out of the box

**Ready to use!**

Open dashboard, click Developer section, start exploring.

---

**Date**: March 12, 2026
**Status**: ✅ Production Ready
