# Funder Extraction Integration - Complete

## Overview

The funder transfer extraction is now fully integrated into the real-time token detection system. When a new token launches and is detected as a migration:

1. Creator is extracted
2. Creator funding is extracted (sender → creator relationships)
3. **NEW**: If funder extraction toggle is ON, funder transfers are also extracted

## Flow Diagram

```
New Token Detected
    ↓
Creator Extraction
    ↓
Creator Funding Extraction (Sender → Creator)
    ↓
Check Funder Extraction Toggle
    ├─ If ON  → Extract Funder Transfers (Sender → Funder → Creator)
    └─ If OFF → Skip funder extraction
    ↓
Creator Clustering & Analysis
```

## Toggle Control

### UI Button
- **Location**: Controls panel on main page
- **Color**: Amber/gold when OFF, Green when ON
- **Label**: "Funder Extraction OFF" or "Funder Extraction ON"
- **Endpoint**: `/api/funder-extraction-control`

### Database Storage
- **Table**: `polling_settings`
- **Key**: `funder_extraction_enabled`
- **Values**: `'1'` (ON) or `'0'` (OFF)

## Implementation Details

### New Functions (pumpfun_curve_listener.py)

#### `is_funder_extraction_enabled()`
```python
def is_funder_extraction_enabled() -> bool:
    """Check if funder transfer extraction is enabled via UI toggle"""
    # Queries polling_settings table for 'funder_extraction_enabled' key
    # Returns True if value is '1', False otherwise
```

#### `extract_funder_transfers_async(creator_address)`
```python
async def extract_funder_transfers_async(creator_address: str):
    """Async wrapper for funder transfer extraction"""
    # Runs extract_for_creator in thread pool to avoid blocking
    # Logs completion or errors
```

### Integration Point
**File**: `pumpfun_curve_listener.py` line ~1550

When a new creator is detected:
```python
if earliest_creator:
    # Extract creator funding (existing)
    asyncio.create_task(extract_funding_for_new_token(...))

    # NEW: Check funder extraction toggle
    if is_funder_extraction_enabled():
        asyncio.create_task(extract_funder_transfers_async(creator_address))
```

## What Gets Extracted

### When Toggle is ON

For each creator's funders, the script extracts:

**Incoming Transfers** (Sender → Funder)
- Who sent SOL to the funder
- How much SOL
- Transaction signature
- Sender classification (CEX, INFRA, unknown)

**Outgoing Transfers** (Funder → Creator)
- Who the funder sent SOL to
- How much SOL
- Transaction signature
- Recipient classification

### Database Tables

**`funder_incoming_transfers`**
```
sender_address       - Who funded the funder
funder_address       - The intermediate funder account
amount_sol           - Amount transferred
sender_type          - 'cex', 'infra', or 'unknown'
transaction_signature - Blockchain tx ID
block_time           - Block timestamp
is_cex               - 1 if CEX, 0 otherwise
cex_exchange         - Exchange name (if CEX)
cex_type             - Wallet type (if CEX)
```

**`funder_outgoing_transfers`**
```
funder_address       - The intermediate account
recipient_address    - Where SOL was sent
amount_sol           - Amount transferred
recipient_type       - 'cex', 'infra', or 'unknown'
transaction_signature - Blockchain tx ID
block_time           - Block timestamp
is_cex               - 1 if CEX, 0 otherwise
cex_exchange         - Exchange name (if CEX)
cex_type             - Wallet type (if CEX)
```

## Benefits

### 1. **Complete Funding Visibility**
See the full 3-tier chain: Sender → Funder → Creator

### 2. **Real-time Detection**
Extractions happen automatically when new tokens launch (if toggle is ON)

### 3. **On-demand Control**
Toggle ON/OFF from the UI without restarting the listener

### 4. **Non-blocking**
Extractions run in background threads, don't block token detection

### 5. **Flexible RPC Usage**
Only makes RPC calls when extraction is enabled

## Usage

### Enable Extraction
1. Click "Funder Extraction OFF" button on main page
2. Button turns green and shows "Funder Extraction ON"
3. Future token launches will trigger funder extraction automatically

### Disable Extraction
1. Click "Funder Extraction ON" button
2. Button turns amber and shows "Funder Extraction OFF"
3. Future token launches will skip funder extraction

### Manual Extraction
```bash
python3 funder_incoming_extractor.py <creator_address>
```

## Performance Notes

- **RPC Rate Limiting**: 1 second delay between RPC calls to avoid rate limits
- **Transaction Limit**: Processes up to 1000 recent transactions per funder
- **Dust Filter**: Ignores transfers < 0.001 SOL
- **Balance Matching**: Uses ±5% threshold to match balance changes to accounts

## Logging

When extraction runs, logs appear in the listener:

```
[FUNDER_EXTRACTION] Toggle enabled - extracting funder transfers for AbCdEf12...
[INCOMING] Sender1... → Funder... | 1.2345 SOL
[OUTGOING] Funder... → Creator... | 10.5678 SOL
[SUMMARY] Funder...: 5 incoming, 1 outgoing, 15.8023 SOL total
[FUNDER_EXTRACTION] Completed for AbCdEf12...: {...}
```

## Testing Checklist

- [x] Toggle button appears in UI
- [x] API endpoint responds correctly
- [x] Toggle persists to database
- [x] Functions import without errors
- [x] Listener can check toggle status
- [x] Async wrapper is functional
- [x] Rate limiting is in place
- [x] Both incoming and outgoing transfers detected

## Files Modified

1. **main.py** (Flask UI)
   - Added "Funder Extraction" toggle button
   - Added `/api/funder-extraction-control` endpoint
   - Added JavaScript functions for toggle control

2. **pumpfun_curve_listener.py** (Listener)
   - Added `is_funder_extraction_enabled()` function
   - Added `extract_funder_transfers_async()` function
   - Integrated toggle check into token detection flow

3. **funder_incoming_extractor.py** (Extraction Script)
   - Handles both incoming and outgoing transfers
   - Rate limiting to avoid RPC throttling
   - Classifies all accounts (CEX, INFRA, unknown)

## Next Steps (Optional)

1. **Monitoring Dashboard**: Show extraction progress in real-time
2. **Statistics**: Track total transfers extracted per creator
3. **Export**: Allow exporting extracted data as CSV
4. **Batch Mode**: Run extraction on multiple creators at once
5. **Historical Analysis**: Compare funding patterns over time

---

**Status**: ✅ COMPLETE & TESTED
**Last Updated**: 2026-02-13
**Integration**: Real-time token detection with on-demand funder extraction
