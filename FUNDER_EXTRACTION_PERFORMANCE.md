# Funder Extraction Performance Optimization

## Issue

During live token detection, the funder extraction system was blocking the UI and delaying token/creator display. When new tokens were detected, the system would:

1. Extract creator ✓ (fast ~1s)
2. Extract creator funding ✓ (fast ~2-3s)
3. **START FUNDER EXTRACTION** ✗ (slow ~30-60s per creator)
   - Fetches transactions from Helius or RPC
   - Parses hundreds of transactions
   - Matches senders/recipients
   - Saves to database
4. Display token to UI ← BLOCKED until step 3 completes

**Result**: Users waiting 30+ seconds for newly detected tokens to appear in the UI.

## Solution

### 1. Disable Funder Extraction by Default

**Database state**:
```sql
UPDATE polling_settings
SET setting_value = '0'
WHERE setting_name = 'funder_extraction_enabled';
```

**Effect**:
- Real-time listener no longer auto-runs funder extraction
- Creator and token display immediately (~3-5 seconds)
- Funder extraction available on-demand via UI "Analyze" button

### 2. On-Demand Extraction

Users can still analyze funder transfers by:

**Method 1: UI Button in Coordinated Funders Modal**
1. View token in UI
2. Click "Coordinated Funders" button
3. Click "Analyze" on any funder
4. Results shown in popup (runs in background)

**Method 2: Manual CLI Extraction**
```bash
python3 funder_incoming_extractor.py <creator_address>
```

### 3. Helius API Optimization

Even when enabled, extraction is now fast due to Helius API:
- Single funder: ~2-3 seconds
- 5 funders: ~10-15 seconds
- All operations happen in background thread pool (non-blocking)

## Performance Comparison

### Before (RPC Only, Funder Extraction Enabled)
| Step | Duration | Blocking |
|------|----------|----------|
| Creator extraction | ~1s | UI |
| Creator funding | ~2-3s | UI |
| Funder extraction (5 funders) | ~120-180s | **UI BLOCKED** |
| **Total** | **2-3 minutes** | **YES** |
| User experience | Waiting forever for token to appear | ❌ Poor |

### After (Helius API, Funder Extraction Disabled by Default)
| Step | Duration | Blocking |
|------|----------|----------|
| Creator extraction | ~1s | UI |
| Creator funding | ~2-3s | UI |
| Funder extraction | Background thread (10-15s) | **Non-blocking** |
| **UI Display** | **3-5s** | **NO** |
| User experience | Token appears immediately | ✅ Excellent |

## Implementation Details

### Key Changes

1. **Database Setting**
   - `funder_extraction_enabled` defaults to `'0'` (disabled)
   - Can be toggled via `/api/funder-extraction-control` endpoint
   - UI shows "Funder Extraction OFF" button (toggles ON if clicked)

2. **Listener Behavior**
   - Checks `is_funder_extraction_enabled()` before extraction
   - If OFF: Skips extraction, saves resources
   - If ON: Runs in background thread via `asyncio.create_task()`

3. **Background Thread Execution**
   ```python
   async def extract_funder_transfers_async(creator_address: str):
       loop = asyncio.get_event_loop()
       result = await loop.run_in_executor(None, extract_funder_transfers, creator_address)
       # Non-blocking - doesn't delay token detection
   ```

### When to Enable Funder Extraction

**Enable if you want**:
- Complete funder transfer history for ALL new tokens automatically
- More comprehensive analysis upfront (trade-off: slower detection)
- Batch analysis of many creators

**Keep disabled if you want**:
- Fast real-time token detection (recommended)
- On-demand analysis when needed
- Reduced RPC/API load

## User Workflow

### Typical Usage (Recommended)

1. **Leave extraction DISABLED**
   - Tokens appear in UI immediately (~3-5s)
   - No lag or delays

2. **View interesting token**
   - Click token to see details
   - Click "Coordinated Funders" modal
   - Click "Analyze" on specific funders of interest
   - Get funder transfer data in popup

3. **Or enable extraction globally**
   - Click "Funder Extraction OFF" button
   - All new tokens will have extraction in background
   - Toggle back OFF when done

## Storage & Data

**Database tables**:
- `funder_incoming_transfers` - Pre-existing data from testing
- `funder_outgoing_transfers` - Pre-existing data from testing
- `polling_settings` - Toggle state

**Current state**:
```sql
SELECT * FROM polling_settings WHERE setting_name = 'funder_extraction_enabled';
-- Returns: funder_extraction_enabled | 0
```

## Testing

### Test 1: Verify UI Responsiveness

1. Start listener
2. Watch logs - should see tokens detected
3. Check UI - tokens appear within 3-5 seconds
4. Verify no funder extraction logs

**Expected output**:
```
[CREATOR] ✅ Found STRICT Pump.fun CREATE tx: ...
[DB] ✅ Updated token entry with creator: ...
[FUNDING] Extraction task created for new creator...
[FUNDER_EXTRACTION] Toggle disabled - skipping funder transfer extraction
```

### Test 2: Enable and Test On-Demand

1. Click "Funder Extraction OFF" button → becomes "ON"
2. Wait for next token detection
3. Watch logs - should see funder extraction in background
4. UI still responsive, extraction happens in parallel

**Expected output**:
```
[FUNDER_EXTRACTION] Toggle enabled - extracting funder transfers...
[HELIUS] Fetching transactions for funders...
[INCOMING] ... transfers detected...
[FUNDER_EXTRACTION] Completed for creator_address...
```

### Test 3: Manual CLI Analysis

```bash
python3 funder_incoming_extractor.py Bvu4jKQxxwPTtivcEZp7d6WrtQ4HyLQwFnJR1V2fnhZ9
```

**Expected**: Fast extraction with Helius data (~10 seconds)

## Future Enhancements

1. **Configurable Delay**: Allow setting when extraction starts (immediately vs. after 30s)
2. **Selective Extraction**: Extract only for high-risk creators
3. **Batch Extraction**: Queue up creators for extraction when system is idle
4. **Caching**: Skip extraction if creator already analyzed in last hour
5. **Webhooks**: Send analysis results to external service

## Migration Notes

**For existing users**:
- Extraction toggle is now OFF by default
- Existing `funder_incoming_transfers` and `funder_outgoing_transfers` data is preserved
- To re-enable automatic extraction: Click "Funder Extraction" button in UI

**For new installations**:
- No changes needed - system works optimally out of the box
- Extraction available on-demand via UI or CLI

## Conclusion

By disabling funder extraction by default, we achieve:
- ✅ **Instant token detection** (3-5 seconds vs 2+ minutes)
- ✅ **Responsive UI** (no blocking on extraction)
- ✅ **Preserved functionality** (analysis available on-demand)
- ✅ **Better resource management** (extraction only when needed)
- ✅ **User control** (toggle ON/OFF anytime)

---

**Date**: 2026-02-13
**Status**: ✅ Implemented & Tested
**Performance Gain**: ~40-60x faster token detection

