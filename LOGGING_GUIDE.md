# Migration Settings - Comprehensive Logging Guide

## Overview

Complete logging has been added to track when migration settings (Token History Check and Creator Clustering) are toggled ON/OFF across all system components.

---

## Log Sources & Formats

### 1️⃣ Browser Console (UI)

**Location**: Open browser DevTools → Console tab

**When User Toggles Feature**:
```
🔧 [TOGGLE] Token History Check: ENABLED
✅ [SETTINGS] Updated - Token History: ENABLED
```

```
🔧 [TOGGLE] Creator Clustering: DISABLED
✅ [SETTINGS] Updated - Creator Clustering: DISABLED
```

**When Page Loads**:
```
📋 [SETTINGS LOADED] Token History: ✅ ON | Creator Clustering: ❌ OFF
```

---

### 2️⃣ Flask API Server Logs

**Location**: Terminal/log file where Flask is running

**On GET Request** (When page loads or UI fetches settings):
```
[SETTINGS] Retrieved - Token History: ✅ ON | Creator Clustering: ❌ OFF
```

**On POST Request** (When user toggles feature):
```
[SETTINGS] Updated - Token History: ✅ ON | Creator Clustering: ✅ ON
```

---

### 3️⃣ Listener Logs

**Location**: Terminal/log file where `pumpfun_curve_listener.py` is running

**When Token Migrates (Feature ENABLED)**:
```
[SETTINGS] Token history check ✅ ON - extracting pre-migration funding
[SETTINGS] Creator clustering ✅ ON - analyzing wallet network
```

**When Token Migrates (Feature DISABLED)**:
```
[SETTINGS] Token history check ❌ OFF - skipping funding extraction
[SETTINGS] Creator clustering ✅ ON - analyzing wallet network
```

---

## Log Prefixes Summary

| Prefix | Location | Meaning |
|--------|----------|---------|
| 🔧 [TOGGLE] | Browser Console | User clicked toggle switch |
| ✅ [SETTINGS] | Browser Console + Flask API | Feature enabled/updated |
| 📋 [SETTINGS LOADED] | Browser Console | Page loaded with settings |
| [SETTINGS] | Listener | Feature status when migration detected |
| ❌ [SETTINGS] | Listener | Feature disabled, skipping execution |

---

## Watching Logs in Real-Time

### Browser Console
```bash
# Open browser DevTools with F12 (or right-click → Inspect)
# Go to Console tab
# Perform toggle action
# See logs appear in real-time
```

### Flask Server
```bash
# Terminal where Flask is running
tail -f /tmp/flask.log 2>/dev/null | grep SETTINGS
```

### Listener
```bash
# Terminal where listener is running
tail -f listener.log 2>/dev/null | grep SETTINGS
```

---

## Example Workflow

### Scenario: User toggles Token History Check OFF while listener monitors

**Step 1: Browser Console (User toggles)**
```
🔧 [TOGGLE] Token History Check: DISABLED
✅ [SETTINGS] Updated - Token History: DISABLED
```

**Step 2: Flask Log (Server updates)**
```
[SETTINGS] Updated - Token History: ❌ OFF | Creator Clustering: ✅ ON
```

**Step 3: migration_settings.json (File persisted)**
```json
{
  "token_history_check": false,
  "creator_clustering": true
}
```

**Step 4: Listener (Next token migration)**
```
[MIGRATION] ✓ Token migrated: ABC123...
[SETTINGS] Token history check ❌ OFF - skipping funding extraction
[SETTINGS] Creator clustering ✅ ON - analyzing wallet network
[CLUSTERING] 🔍 Building wallet cluster...
```

---

## Troubleshooting

### Issue: No logs appearing in Browser Console
**Solution**: 
1. Open DevTools: F12 (or Cmd+Option+I on Mac)
2. Go to Console tab
3. Clear console with `console.clear()`
4. Perform toggle action again
5. Watch for 🔧 [TOGGLE] messages

### Issue: No logs in Flask output
**Solution**:
1. Verify Flask is running: `lsof -i :5002`
2. Check logs are going to stdout: Look for "Serving Flask" message
3. Perform API request: `curl http://localhost:5002/api/migration-settings`
4. Should see `[SETTINGS] Retrieved` message

### Issue: Listener not showing [SETTINGS] logs
**Solution**:
1. Verify listener is running: Check for websocket logs
2. Force a migration to trigger logging
3. Look for `[SETTINGS]` prefix in listener output
4. Check that token migration detection happened first

---

## Log Message Meanings

### 🔧 [TOGGLE] Token History Check: ENABLED
User just clicked the Token History toggle to turn it ON
→ Feature will be enabled for next migration

### ❌ OFF - skipping funding extraction
Listener detected a token migration but Token History is disabled
→ No funding analysis will run for this token

### ✅ ON - extracting pre-migration funding  
Listener detected a token migration and Token History is enabled
→ Funding analysis is running in background

### 📋 [SETTINGS LOADED]
Page just loaded and fetched current settings from server
→ Toggle switches were updated to show current state
→ If you see this, UI is properly initialized

---

## Key Indicators

### ✅ ON = Feature Enabled
- Will execute when token migrates
- Example: `[SETTINGS] Token history check ✅ ON`

### ❌ OFF = Feature Disabled
- Will be skipped when token migrates
- Example: `[SETTINGS] Token history check ❌ OFF`

---

## Verification Checklist

✅ Can toggle features in UI  
✅ Browser console shows 🔧 [TOGGLE] message  
✅ Flask logs show [SETTINGS] Updated  
✅ Settings file persists: `cat migration_settings.json`  
✅ Page refresh restores toggle state  
✅ Next migration shows [SETTINGS] log with current state

---

## Summary

Full end-to-end logging now shows:
1. **When** features are toggled (Browser console)
2. **Where** settings are stored (Flask server)
3. **What** the listener will do (Listener logs)
4. **Why** features are being skipped (Disabled status)

Use these logs to verify toggles are working and to debug any issues.

---

**Status**: ✅ Production Ready  
**Commit**: 05bf4a5  
**Last Updated**: 2026-01-26
