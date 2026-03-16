# WebSocket Price Delivery Diagnostic Report

**Date**: 2026-03-16
**Status**: 🔴 BLOCKED - Vault Address Validation Required
**Test Token**: Chibification (CHIBI) - `5cDhM4yMKipQkjSGdvYnqPdiJz685Z96rbe6GSYppump`

---

## Executive Summary

WebSocket connection to Helius RPC is **fully functional** and receiving real-time events from multiple pools. However, **Chibify token price delivery is blocked** because the vault addresses registered in the database do not appear to exist or are incorrectly identified on-chain. Without valid vault addresses, WebSocket subscriptions receive zero balance updates.

---

## System Status

### ✅ What's Working

| Component | Status | Evidence |
|-----------|--------|----------|
| **WebSocket Connection** | ✅ Connected | Helius RPC WS authenticated, 101 Switching Protocols response |
| **Authentication** | ✅ Valid API Key | Using `16f1a5fc-2592-466c-a5d4-b5799ae8da96` (in `.env`) |
| **Subscriptions** | ✅ 38/38 Confirmed | All pool accounts successfully subscribed |
| **Event Stream** | ✅ Active | 140+ events decoded from confirmed subscriptions |
| **Balance Decoding** | ✅ Fixed | Updated to handle both SPL token and native SOL accounts |
| **Real-time Updates** | ✅ Working | Base accounts from other pools receiving continuous updates |

### ❌ What's Not Working

| Component | Status | Issue |
|-----------|--------|-------|
| **Chibify Quote Vaults** | ❌ No Data | 8 subscriptions → 0 events received |
| **Chibify Base Vaults** | ⚠️ Partial | 4 subscriptions → 4 events total (2 accounts, 2 silent) |
| **Price Computation** | ❌ Blocked | Requires both base + quote reserves; only partial data available |
| **Vault Address Validation** | ❌ Missing | No RPC verification of vault existence |

---

## Detailed Findings

### Issue #1: Quote Vault Accounts Receiving Zero Events

**Symptom**: All Chibify quote (SOL vault) accounts subscribed but zero balance updates received.

**Chibify Quote Accounts Registered**:
```
Pool 1 Quote: 6TXTYRK8x4EdL3ZXEMULj8AmjEXneZEhcR3p5AYLM9gV (decimals: 9, token: SOL)
Pool 2 Quote: BDuTqDHSVVo4NohKAZHHjy9PKe7Xhah5FDWQttSjMW3z (decimals: 9, token: SOL)
Pool 3 Quote: 4Jr4LGEV1nSqwgqSVFinfGftyAHL7dkwB6Zd2pUPG2YR (decimals: 9, token: SOL)
Pool 4 Quote: 9L6EHRiXptAKYDSYAHzrP6PuqTgmJP9gFw3D2GXBDCkw (decimals: 9, token: SOL)
```

**WebSocket Evidence**:
- Subscription confirmations: ✅ All 4 quote accounts confirmed with subscription IDs
- Events received: ❌ 0 events across all 4 quote subscriptions
- Timeout: No timeout errors (subscriptions active, just silent)

**Analysis**:
Helius WebSocket only sends updates for accounts that:
1. Exist on-chain
2. Have state changes

Zero events suggests **these quote vault addresses either don't exist or aren't changing**. Since Chibify is actively trading (confirmed trading activity), the more likely explanation is **the addresses are incorrect**.

**Root Causes** (in order of likelihood):
1. **Incorrect vault extraction** - Quote vault addresses don't match actual on-chain vaults
2. **Fresh pools with no SOL activity** - Vaults exist but no trades have executed yet
3. **Helius account subscription limitation** - Rare, but Helius might not support certain account types
4. **Database registration error** - Addresses truncated, corrupted, or from wrong pool program

---

### Issue #2: Partial Base Vault Updates

**Symptom**: Only 2 out of 4 Chibify base (token) vault accounts receiving updates.

**Event Count by Base Account**:
```
Pool 1 Base (GqwZckw7Ntty6WTy...): 0 events
Pool 2 Base (ADyA8hdefvWN2dbG...): 3 events ✅
Pool 3 Base (4wTV1YmiEkRvAtNt...): 1 event  ✅
Pool 4 Base (QMMkXAnKyZQUJqzg...): 0 events
```

**Analysis**:
- 50% base accounts are receiving updates (Pools 2 & 3)
- 50% base accounts are silent (Pools 1 & 4)
- This pattern suggests **vault addresses are inconsistent** or **some are incorrect**

**Possible Explanations**:
1. Pools 1 & 4 vault addresses are wrong (non-existent accounts)
2. Pools 2 & 3 are the "real" Chibify pools, others are duplicates/test accounts
3. Vault extraction process had partial failures

---

### Issue #3: Price Computation Cannot Proceed

**Requirement**: `PoolPriceCalculator.compute_price()` requires **both** base and quote reserves.

**Current Data State** (after 15s of WebSocket updates):
```
Pool 1: base=None,  quote=None  ❌ No reserves
Pool 2: base=✅,    quote=None  ⚠️  Partial
Pool 3: base=✅,    quote=None  ⚠️  Partial
Pool 4: base=None,  quote=None  ❌ No reserves
```

**Code Logic** (in `PoolStateStore.get_pools_for_mint()`):
```python
for (m, base_account), s in self._state.items():
    if m == mint and not s['is_stale']:
        if s['base_reserve'] is not None and s['quote_reserve'] is not None:
            results.append((base_account, s['base_reserve'], s['quote_reserve']))
```

Result: `get_pools_for_mint('CHIBI')` returns **empty list** → no prices computed.

---

## Data Flow Analysis

### Expected Path (Ideal)
```
Trading Activity on Chibify
    ↓
Pool Vault Balances Update
    ↓
Helius Detects Account Change
    ↓
WebSocket accountNotification Sent
    ↓
_handle_message() Decodes Balance
    ↓
PoolStateStore Updated (base + quote)
    ↓
_recompute_prices_from_ws_state() Computes Price
    ↓
API /price/CHIBI Returns Live Price
```

### Actual Path (Currently)
```
Trading Activity on Chibify
    ↓
Pool Vault Balances Update
    ↓
Helius Detects Account Change
    ↓
WebSocket accountNotification Sent (PARTIAL - only some accounts)
    ↓
_handle_message() Decodes Balance (only for accounts that sent events)
    ↓
PoolStateStore Updated (base ONLY, quote=None)
    ↓
_recompute_prices_from_ws_state() Filters Out Incomplete Pools
    ↓
API /price/CHIBI Returns: unavailable (no reserves in cache)
```

---

## WebSocket Client Configuration

**File**: `src/core/pool_price_engine.py`

**Helius Credentials** (from `.env`):
```bash
HELIUS_WS_URL="wss://mainnet.helius-rpc.com/?api-key=16f1a5fc-2592-466c-a5d4-b5799ae8da96"
HELIUS_RPC_URL="https://mainnet.helius-rpc.com/?api-key=16f1a5fc-2592-466c-a5d4-b5799ae8da96"
```

**Subscription Details**:
- **Method**: `accountSubscribe` (Solana JSON-RPC standard)
- **Encoding**: `base64` (raw account data)
- **Commitment**: `confirmed` (not finalized, faster)
- **Per-Account Limit**: None (Helius supports unlimited subscriptions)
- **Total Subscriptions**: 38 active

**Recent Code Fix** (Line 682-688):
Added fallback to extract `lamports` field for native SOL accounts when SPL token data is unavailable.

```python
# Try to decode balance from SPL token account data first
data_list = account_data.get("data", [])
balance = None
if data_list:
    balance = PoolReserveFetcher._decode_spl_token_balance(data_list[0])

# If no balance from data (native SOL account), try lamports field
if balance is None:
    balance = account_data.get("lamports")
```

---

## Vault Address Investigation

### Registered Vault Addresses

**Location**: `database/flex_complete_database.db` → `token_pool_accounts` table

| Pool | Base Account | Quote Account | Status |
|------|--------------|---------------|--------|
| Pool 1 | `4wTV1YmiEkRvAtNts...` | `6TXTYRK8x4EdL3ZXE...` | ❌ No WS events |
| Pool 2 | `ADyA8hdefvWN2dbGG...` | `BDuTqDHSVVo4NohKA...` | ⚠️ Base only (3 events) |
| Pool 3 | `GqwZckw7Ntty6WTyJ...` | `4Jr4LGEV1nSqwgqSV...` | ⚠️ Base only (1 event) |
| Pool 4 | `QMMkXAnKyZQUJqzgv...` | `9L6EHRiXptAKYDSYA...` | ❌ No WS events |

### How Vaults Were Obtained

These addresses were likely extracted from:
1. **PumpFun pool PDA state decoding** (for V1 pools)
2. **PumpSwap state parsing** (for V2 pools)
3. **On-chain pool account data** via RPC

**Current State**: No validation that these addresses actually exist or match on-chain reality.

---

## Root Cause Summary

| Issue | Root Cause | Impact | Severity |
|-------|-----------|--------|----------|
| Quote vaults no data | Likely invalid addresses on-chain | No quote reserves → no prices | 🔴 Critical |
| 50% base vaults silent | Likely invalid addresses or duplicate pools | Partial data only | 🔴 Critical |
| Price computation fails | Filtering out incomplete pools (requires both reserves) | No price delivery | 🔴 Critical |
| Vault validation missing | No RPC verification step | Can't confirm addresses are correct | 🟠 High |

---

## Required Fixes (In Order of Priority)

### 1️⃣ Validate Vault Addresses (Blocking Issue)

**Approach**: Verify each registered vault address exists on-chain and belongs to the correct pool.

**Method A** (RPC-based - requires network):
```python
# For each registered vault:
account_info = rpc.get_account_info(vault_address)
if account_info is None:
    # Vault doesn't exist!
    vault_is_invalid = True
else:
    # Verify owner and structure
    owner = account_info.owner
    is_valid_spl_token = owner == "TokenkegQfeZyiNwAJsyFbPVwwQQYoQ3ZNrfin2qJAd"
```

**Method B** (Pool PDA decoding - accurate but complex):
```python
# Decode Raydium/Orca/PumpFun pool PDA
# Extract official vault addresses from pool state
# Compare against registered addresses
# Report mismatches
```

**Recommended**: Method B (more accurate, finds all 4 vaults simultaneously)

### 2️⃣ Fix Vault Address Registration Process

**Current**: Manual extraction → database insert (error-prone)

**Proposed**:
- Add vault validation step after extraction
- Verify both vaults exist before registering
- Log mismatches with full details
- Implement retry logic for fresh pools

### 3️⃣ Handle Partial Data (Fallback)

**If validation reveals addresses can't be fixed** (e.g., fresh pools without quote vault activity):

**Option A**: Relax price computation to accept partial data
```python
# Allow computing price with just base reserve
# (less accurate, but something vs. nothing)
if base_reserve and quote_reserve:
    # Ideal case
    price = quote / base
elif base_reserve:
    # Fallback: estimate quote from recent price
    estimated_quote = base * last_known_price_ratio
    price = estimated_quote / base
```

**Option B**: Keep WebSocket, add RPC fallback for quote only
```python
# Every 60s:
# 1. WebSocket provides base reserves (real-time)
# 2. RPC provides quote reserves (fallback)
# 3. Compute prices from combined data
```

### 4️⃣ Improve Diagnostics

Add health endpoint metrics:
```json
{
  "pool_diagnostics": {
    "total_registered": 34,
    "with_base_updates": 12,
    "with_quote_updates": 8,
    "with_both": 4,
    "invalid_vaults": 6,
    "unvalidated": 24
  }
}
```

---

## Testing Evidence

### WebSocket Connection Test (✅ PASSED)

```
Connection: OPEN (HTTP 101 Switching Protocols)
API Key: Valid (authenticated)
Subscriptions: 38/38 confirmed
Events received: 140+ decoded
Reconnects: 0 (stable connection)
```

### Pool Event Stream Test (⚠️ PARTIAL)

**Test Duration**: 15 seconds
**Total Events**: 140 decoded
**Accounts Sending**: 12 out of 38 (31%)
**Chibify Accounts Sending**: 2 out of 8 (25%)

### Chibify Specific Test (❌ FAILED)

```
Expected: 8 subscriptions (4 base + 4 quote) → continuous updates
Actual:
  - Pool 1: 0 events (base + quote both silent)
  - Pool 2: 3 events (base only)
  - Pool 3: 1 event  (base only)
  - Pool 4: 0 events (base + quote both silent)

Result: NO complete reserves available
        NO prices can be computed
        API returns: unavailable
```

---

## Recommendations

### Immediate (Required for WebSocket delivery)

1. **Validate all registered vault addresses** against on-chain data
   - Use RPC `getAccountInfo()` to confirm existence
   - Verify account ownership and structure
   - Replace invalid addresses

2. **Revalidate Chibify pools specifically**
   - Decode pool PDAs directly from blockchain
   - Extract official vault addresses
   - Update database with verified addresses

3. **Re-subscribe WebSocket client** to updated accounts
   - Trigger `refresh_pools()` after address updates
   - Reconnect WebSocket with new vault list

### Short-term (Improve robustness)

4. **Add vault address validation to registration flow**
   - Check vault existence before saving to database
   - Implement retry for fresh pools
   - Log all validation failures

5. **Implement hybrid RPC/WebSocket fallback**
   - WebSocket for real-time base updates
   - RPC fallback every 60s for quote reserves
   - Combine both data sources for price computation

6. **Add diagnostic metrics to health endpoint**
   - Track which pools have complete/partial data
   - Monitor vault validation status
   - Expose validation errors

### Long-term (Architecture improvements)

7. **Automate vault address extraction**
   - Pool discovery service extracts vaults automatically
   - Validates against on-chain state
   - Registers with verified addresses only

8. **Multi-source vault verification**
   - Cross-check with DexScreener/Birdeye vault data
   - Implement vault address consensus
   - Flag discrepancies

---

## Next Steps

**Decision Required**:
- **Option A**: Authorize RPC call to validate vault addresses (quick fix, requires network)
- **Option B**: Manual pool PDA decoding to extract correct vaults (slower, no network needed)
- **Option C**: Wait for trading activity to populate quote vaults naturally (unknown timeline)

Recommend **Option A** (single RPC batch call) → fix addresses → re-subscribe WebSocket.

---

## Files Affected

| File | Status | Note |
|------|--------|------|
| `src/core/pool_price_engine.py` | ✅ Fixed | Added `lamports` fallback for SOL accounts |
| `src/core/price_worker.py` | ✅ Updated | Calls `_recompute_prices_from_ws_state()` |
| `database/flex_complete_database.db` | ⚠️ At Risk | Vault addresses may be invalid |
| `.env` | ✅ Verified | API key is valid and loaded |

---

## Related Files

- [MULTI_POOL_AGGREGATION_IMPLEMENTATION.md](MULTI_POOL_AGGREGATION_IMPLEMENTATION.md) - Pool aggregation architecture
- [src/core/pumpfun_curve_listener.py](src/core/pumpfun_curve_listener.py) - Pool discovery and vault extraction
- [src/core/pool_price_engine.py](src/core/pool_price_engine.py:682) - WebSocket client and balance decoding
