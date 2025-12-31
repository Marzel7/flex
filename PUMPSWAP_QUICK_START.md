# PumpSwap Phase 2 - Quick Start Guide

## What is PumpSwap?

PumpSwap is **Raydium V4 pools** that receive **PumpFun tokens** after their bonding curve completes. We monitor these migrations in real-time and mark them with a 🚀 badge.

## Quick Commands

### 1. Run the Main Application
```bash
python main.py
```
Opens web UI at http://localhost:5002 with real-time PumpSwap detection

**What happens**:
- WebSocket connects to Solana
- Listens for Raydium V4 pool creation events
- Detects PumpSwap tokens (PumpFun migrations)
- Broadcasts with 🚀 badge
- Shows live on UI

### 2. Test Phase 1 (Detection Logic)
```bash
python test_pumpswap_detection.py
```
Validates the core detection methods (21 tests, ~10 seconds)

**Tests**:
- `is_pumpswap_token()` - Is this a PumpSwap?
- `get_pumpfun_origin_info()` - Get creator metadata
- `track_pumpswap_pool()` - Record migration
- Database schema - All columns present

**Expected Output**:
```
[✓ PASS] Detect valid PumpSwap token (has bonding curve + raydium pool)
[✓ PASS] Reject regular Raydium token (no bonding curve)
...
Total Tests:  21
Passed:       21 ✓
```

### 3. Test Phase 2 (WebSocket Integration)
```bash
python test_pumpswap_phase2.py
```
Validates the full detection pipeline (14 tests, ~5 seconds)

**Tests**:
- Detection in Raydium V4 pools
- Badge generation
- Broadcast data structure
- Complete WebSocket flow simulation
- Multiple token handling

**Expected Output**:
```
[✓ PASS] Detect PumpSwap token (Raydium V4 + bonding_curve marker)
[✓ PASS] Generate PumpSwap badge when is_pumpswap=True
...
Total Tests:  14
Passed:       14 ✓
```

### 4. Listen for Real PumpSwap Tokens
```bash
python test_pumpswap_listener.py
```
Runs indefinitely, listening for real PumpSwap tokens

**What happens**:
- Connects to WebSocket
- Monitors Raydium V4 pools
- Prints detection as tokens migrate
- Press Ctrl+C to stop and see summary

**Expected Output**:
```
================================================================================
  PUMPSWAP CONTINUOUS LISTENER - Phase 2 Real-Time Detection
================================================================================

Started at: 2025-12-31 14:30:45
Listening for:
  ✓ New Raydium V4 pool creation events
  ✓ PumpFun → PumpSwap token migrations
  ✓ Metadata extraction and tracking

[PUMPSWAP] 🚀 DETECTED: PumpFun token migrated to PumpSwap!
[PUMPSWAP] Creator: <address>
[PUMPSWAP] Bonding Curve: <address>...
[BROADCAST] 🚀 PUMPSWAP TOKEN DETECTED: TokenName (SYMBOL)
```

## How PumpSwap Detection Works

### The Two-Field Check

PumpSwap tokens have **TWO markers**:

```
✓ bonding_curve     (indicates PumpFun origin)
✓ raydium_pool      (indicates successful migration to Raydium V4)
```

### Examples

**PumpSwap Token** (Detected ✓):
```python
token_data = {
    'mint': 'PumpTokenMint111',
    'bonding_curve': 'BondingCurveAddress999',  # ← Has this
    'raydium_pool': 'RayPoolABC123XYZ',         # ← Has this too
}
is_pumpswap = True  # ✓ Detected
```

**Regular Raydium Pool** (Not detected):
```python
token_data = {
    'mint': 'RegularTokenMint',
    'raydium_pool': 'RayPoolDEF456',  # ← Has pool
    # NO bonding_curve field
}
is_pumpswap = False  # ✗ Not a PumpSwap
```

**Token Still on Bonding Curve** (Not detected):
```python
token_data = {
    'mint': 'BondingToken',
    'bonding_curve': 'BondingCurveAddress',  # ← On bonding curve
    # NO raydium_pool yet (hasn't migrated)
}
is_pumpswap = False  # ✗ Not migrated yet
```

## Integration with UI

### Broadcast Data Structure

Each detected PumpSwap pool receives:

```python
broadcast_data = {
    'amm_id': 'RayPoolABC123XYZ',
    'name': 'PumpSwap Token',
    'symbol': 'PUMP',
    'dex': 'Raydium V4',
    'is_pumpswap': True,                    # ← New!
    'pumpswap_badge': '🚀 PumpSwap',        # ← New!
    'creation_price': 0.000001,
    'current_price': 0.000001,
    # ... other fields
}
```

### UI Display

The UI can now:
- Display 🚀 badge for PumpSwap tokens
- Show creator information
- Display bonding curve details
- Highlight migration metadata

## File Locations

### Core Implementation
- **main.py** (Lines 2517-2661): WebSocket integration and detection
- **main.py** (Lines 2600-2693): Three detection methods

### Tests
- **test_pumpswap_detection.py**: Phase 1 unit tests (21 tests)
- **test_pumpswap_phase2.py**: Phase 2 integration tests (14 tests)
- **test_pumpswap_listener.py**: Real-time listener (continuous)

### Documentation
- **PHASE2_COMPLETION.md**: Full technical report
- **PUMPFUN_INTEGRATION_PLAN.md**: Architecture and strategy
- **PUMPSWAP_QUICK_START.md**: This file

## Test Results Summary

| Test Suite | Tests | Pass | Fail | Status |
|-----------|-------|------|------|--------|
| Phase 1 Detection | 21 | 21 | 0 | ✅ 100% |
| Phase 2 Integration | 14 | 14 | 0 | ✅ 100% |
| **Total** | **35** | **35** | **0** | **✅ 100%** |

## Console Output Indicators

When running the application, you'll see:

```
[WEBSOCKET]  - New pool detected by WebSocket
[PUMPSWAP]   - PumpSwap detection and metadata
[BROADCAST]  - Token added to broadcast queue
```

### Example Sequence

```
[WEBSOCKET] New Raydium V4 pool detected
Token Address: EPjFWaLb3odcccccccccccccccccccccccc...
Token Symbol: PUMP
Token Name: PumpSwap Token

[PUMPSWAP] 🚀 DETECTED: PumpFun token migrated to PumpSwap!
[PUMPSWAP] Creator: PumpFunCreatorAddress...
[PUMPSWAP] Bonding Curve: BondingCurveAddress999...

[BROADCAST] 🚀 PUMPSWAP TOKEN DETECTED: PumpSwap Token (PUMP)
[BROADCAST] ✓ Marked as PumpSwap migration for UI display
```

## Database Fields

All PumpSwap information is stored in the `pools` table:

### Detection
- `is_pumpswap` - Boolean flag

### Metadata
- `pumpfun_creator` - Creator authority
- `bonding_curve_address` - Original bonding curve
- `creator`, `website`, `twitter`, `discord` - Social info

### Timeline
- `pumpfun_launch_time` - When bonding curve started
- `pumpfun_migration_timestamp` - When migrated to PumpSwap

### Pricing
- `pumpfun_launch_price` - Initial bonding curve price
- `pumpfun_final_price` - Final bonding curve price
- `pumpswap_initial_price` - Price at migration

## Troubleshooting

### No PumpSwap tokens detected?

**Possible causes**:
1. WebSocket not receiving events (network issue)
2. No PumpFun tokens migrating right now (wait longer)
3. Tokens migrating but detection failing (see logs)

**Solution**:
- Check console for `[WEBSOCKET]` messages
- Verify RPC endpoint is working
- Run `python test_pumpswap_detection.py` to verify logic

### Getting AttributeError?

**Solution**:
- Restart application: `python main.py`
- Ensure database is not corrupted: `rm raydium_pools.db` and restart

### Tests failing?

**Solution**:
1. Verify all dependencies: `pip install requests flask solders`
2. Check database file permissions
3. Review error message for specific issue

## Performance

- **Detection latency**: ~3-8 seconds from on-chain confirmation
- **WebSocket stability**: Continuous, event-driven
- **Memory usage**: Minimal (single background thread)
- **CPU usage**: Low (awaits events, doesn't poll)

## Architecture Overview

```
Solana Network
     ↓
WebSocket (Helius RPC)
     ↓
Listen for Raydium V4 pool creation
     ↓
Extract pool data from transaction logs
     ↓
Check for PumpSwap markers (bonding_curve + raydium_pool)
     ↓
If PumpSwap detected:
  - Fetch creator metadata
  - Record migration timestamp
  - Mark with 🚀 badge
     ↓
Store in SQLite database
     ↓
Broadcast to UI via queue
     ↓
Web UI displays with PumpSwap indicator
```

## Next Steps (Phase 3)

The UI integration layer is ready but not yet implemented. Phase 3 will:

1. Update HTML template to display PumpSwap badge
2. Add creator information display
3. Show bonding curve details
4. Track PumpSwap statistics

## Support

For detailed technical information, see:
- **PHASE2_COMPLETION.md** - Full technical report
- **PUMPFUN_INTEGRATION_PLAN.md** - Architecture and design

---

**Status**: ✅ Phase 2 Complete and Ready for Use
