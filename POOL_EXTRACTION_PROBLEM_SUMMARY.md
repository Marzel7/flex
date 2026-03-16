# Pool Extraction Bug - Problem Summary

## Current Status: ⚠️ PARTIALLY FIXED

### The Problem

**All detected pools are receiving the SAME vault accounts**, regardless of which token launched:

```
Token 1: 2PRSxvmHCsgvCrkcoJj3Wj9W5cuJyHbkHbokyJabpump
  Base:  EZGLemQL2H2oCUDkAsuGoVpAD4LrWmfySFKV9y7Vq8d9
  Quote: 9AQ5oouQjPDAaPn5v5wNHRg4kXPxNxFS7kVUty9NK91z

Token 2: GSbpqy5i1nud9jstKcdfj5Av6TXanSpBCT7Kmm39pump
  Base:  EZGLemQL2H2oCUDkAsuGoVpAD4LrWmfySFKV9y7Vq8d9  ← SAME
  Quote: 9AQ5oouQjPDAaPn5v5wNHRg4kXPxNxFS7kVUty9NK91z  ← SAME

Token 3: ceM9X1Wyv3u1J6Jtxvga88GftuKLs2FwvuSGj4bpump
  Base:  EZGLemQL2H2oCUDkAsuGoVpAD4LrWmfySFKV9y7Vq8d9  ← SAME
  Quote: 9AQ5oouQjPDAaPn5v5wNHRg4kXPxNxFS7kVUty9NK91z  ← SAME
```

### Impact

1. **WebSocket subscriptions fail**: All tokens subscribe to same vault accounts
2. **Price updates are wrong**: All tokens show same liquidity/price
3. **Multi-pool support broken**: Can't distinguish between different tokens' pools

---

## Root Cause Analysis

### Phase 1: Wrong Offsets (FIXED ✅)
- **Problem**: Using offsets 8-40 and 40-72 to extract vaults
- **Status**: Fixed - changed to 232-264 and 264-296
- **Verification**: Code updated, pool_discovery.py line 185-186

### Phase 2: RPC Data Format (PARTIALLY FIXED ⚠️)
- **Problem**: RPC returns data as `[base64_string, "base64"]` array
- **Status**: Code added to handle array format (line 168-171)
- **Issue**: Still getting same vaults from all pools

### Phase 3: Pool State Data (UNRESOLVED ❌)
- **Real Problem**: The extracted vault addresses at offsets 232-296 are identical for all pools
- **Why**: Could be:
  1. Pool state structure is different than expected
  2. Offsets 232-296 don't contain vault addresses
  3. All pools actually DO have same vaults (unlikely but possible for test)
  4. Data being fetched is the same account for all tokens

---

## Changes Made So Far

### 1. src/core/pool_discovery.py - _extract_raydium_amm() method

**Changed offsets (line 185-186):**
```python
# BEFORE (wrong offsets - lines 170-171)
base_account = self._bytes_to_pubkey(decoded[8:40])
quote_account = self._bytes_to_pubkey(decoded[40:72])

# AFTER (attempted fix - lines 185-186)
base_vault = self._bytes_to_pubkey(decoded[232:264])
quote_vault = self._bytes_to_pubkey(decoded[264:296])
```

**Added RPC data format handling (lines 162-177):**
```python
data_field = pool_data.get("data")
if not data_field:
    logger.warning(f"No data in pool account")
    return None

# RPC returns data as [base64_string, "base64"]
if isinstance(data_field, list) and len(data_field) > 0:
    data = data_field[0]  # Extract from [base64_string, "base64"]
else:
    data = data_field

if isinstance(data, str):
    # Base64-encoded data
    decoded = b64decode(data)
else:
    decoded = data

if len(decoded) < 296:
    logger.warning(f"Pool data too small: {len(decoded)} bytes")
    return None
```

**Complete extraction logic (lines 183-211):**
```python
# Extract vault accounts (public keys are 32 bytes)
# Raydium AMM: base_vault at offset 232, quote_vault at offset 264
base_vault = self._bytes_to_pubkey(decoded[232:264])
quote_vault = self._bytes_to_pubkey(decoded[264:296])

if not base_vault or not quote_vault:
    logger.warning(f"Could not extract vault addresses from pool {pool_address}")
    return None

logger.info(f"✅ Extracted Raydium vaults: base={base_vault[:16]}... quote={quote_vault[:16]}...")

# Fetch token info from vault metadata
base_decimals = await self._get_token_decimals(base_vault)
quote_decimals = await self._get_token_decimals(quote_vault)

# For now, assume base is the token and quote is SOL
base_token = token_mint
quote_token = SOL_MINT

return {
    "base_account": base_vault,
    "quote_account": quote_vault,
    "base_token": base_token,
    "quote_token": quote_token,
    "base_decimals": base_decimals or 6,
    "quote_decimals": quote_decimals or 9,
    "pool_program": "raydium_amm",
}
```

### 2. Added PumpSwap routing in _extract_from_pool_data() (lines 139-141)
```python
# PumpSwap (uses Raydium AMM layout)
if owner == PUMPSWAP_PROGRAM:
    return await self._extract_raydium_amm(pool_data, pool_address, token_mint)
```

### 3. Reference: RaydiumAMMParser in pool_detector.py (working implementation)
Lines 720-722 define the same offsets:
```python
class RaydiumAMMParser(PoolParser):
    """Parser for Raydium AMM v4 pools (also used by PumpSwap)."""

    # Raydium AMM pool account structure offsets
    OPEN_ORDERS_OFFSET = 200
    BASE_VAULT_OFFSET = 232
    QUOTE_VAULT_OFFSET = 264

    @staticmethod
    async def parse(pool_address: str, pool_data: bytes, token_mint: str, rpc_url: str):
        """Parse Raydium AMM pool structure."""
        if len(pool_data) < 296:
            logger.warning(f"Pool data too small for Raydium AMM: {len(pool_data)} bytes")
            return None

        # Extract vault addresses (32-byte Pubkey each)
        base_vault = _bytes_to_pubkey(pool_data[RaydiumAMMParser.BASE_VAULT_OFFSET:RaydiumAMMParser.BASE_VAULT_OFFSET + 32])
        quote_vault = _bytes_to_pubkey(pool_data[RaydiumAMMParser.QUOTE_VAULT_OFFSET:RaydiumAMMParser.QUOTE_VAULT_OFFSET + 32])
        # ... rest of implementation
```

---

## Diagnostic Evidence

### What Works
- ✅ Pool detection identifies pool accounts
- ✅ Pool accounts are fetched via RPC
- ✅ Account data is base64 decoded
- ✅ Vaults extracted successfully (no error logs)
- ✅ Pools registered to database

### What Doesn't Work
- ❌ Each token gets SAME vault addresses
- ❌ Offsets 232-296 appear to be wrong or...
- ❌ All detected pools might actually be the SAME pool address

### Missing Logs
- Expected: `"✅ Extracted Raydium vaults: base=... quote=..."`
- Actually seeing: `"Auto-registered pool"` (registration succeeds)
- Inference: Extraction might be returning data but it's wrong data

---

## Next Steps to Debug

### Option 1: Verify Pool Addresses Are Different
```sql
-- Check if all pools really have same vaults
SELECT DISTINCT base_account, quote_account FROM token_pool_accounts;
```

**Expected output (if working):**
```
3 rows:
  base_account_1 | quote_account_1
  base_account_2 | quote_account_2
  base_account_3 | quote_account_3
```

**Actual output (broken):**
```
1 row:
  EZGLemQL2H2oCUDkAsuGoVpAD4LrWmfySFKV9y7Vq8d9 | 9AQ5oouQjPDAaPn5v5wNHRg4kXPxNxFS7kVUty9NK91z
```

### Option 2: Add Debug Logging to Extraction
Add this code to `_extract_raydium_amm()` (lines 179-181):
```python
if len(decoded) < 296:
    logger.warning(f"Pool data too small: {len(decoded)} bytes")
    return None

# ADD THESE DEBUG LINES:
logger.debug(f"Pool {pool_address[:16]}... data length: {len(decoded)} bytes")
logger.debug(f"  Offset 232-264 (base): {decoded[232:264].hex()}")
logger.debug(f"  Offset 264-296 (quote): {decoded[264:296].hex()}")

# Extract vault accounts
base_vault = self._bytes_to_pubkey(decoded[232:264])
quote_vault = self._bytes_to_pubkey(decoded[264:296])

# ADD THIS DEBUG LINE:
logger.info(f"Pool {pool_address[:16]}... → base={base_vault} quote={quote_vault}")
```

**This will show:**
1. Raw hex bytes at each offset
2. Decoded Solana addresses
3. Confirm if bytes are really identical across pools

### Option 3: Test Data Directly
Create a test script to inspect actual pool data:
```python
import asyncio
import aiohttp
from base64 import b64decode
from solders.pubkey import Pubkey

async def inspect_pool(pool_address):
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getAccountInfo",
        "params": [pool_address, {"encoding": "base64"}]
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://api.mainnet-beta.solana.com",
            json=payload
        ) as resp:
            result = await resp.json()
            data_b64 = result["result"]["value"]["data"][0]
            decoded = b64decode(data_b64)

            print(f"Pool: {pool_address[:16]}...")
            print(f"Data length: {len(decoded)} bytes")
            print(f"Offset 232-264: {decoded[232:264].hex()}")
            print(f"Offset 264-296: {decoded[264:296].hex()}")

            # Try to decode as Pubkey
            try:
                base = Pubkey(decoded[232:264])
                quote = Pubkey(decoded[264:296])
                print(f"Base vault: {base}")
                print(f"Quote vault: {quote}")
            except Exception as e:
                print(f"Error decoding: {e}")

# Test with known pool
asyncio.run(inspect_pool("ADyA8hdefvWN2dbGUXKj19MmZYzFPpHqA1zFXALjpump"))
```

### Option 4: Compare with RaydiumAMMParser
The `pool_detector.py` successfully extracts vaults using same offsets:
```python
# pool_detector.py line 742-743 (working code)
base_vault = _bytes_to_pubkey(pool_data[RaydiumAMMParser.BASE_VAULT_OFFSET:RaydiumAMMParser.BASE_VAULT_OFFSET + 32])
quote_vault = _bytes_to_pubkey(pool_data[RaydiumAMMParser.QUOTE_VAULT_OFFSET:RaydiumAMMParser.QUOTE_VAULT_OFFSET + 32])

# pool_discovery.py line 185-186 (broken code - same offsets!)
base_vault = self._bytes_to_pubkey(decoded[232:264])
quote_vault = self._bytes_to_pubkey(decoded[264:296])
```

**Difference**: The parser gets `pool_data` as raw bytes, while discovery gets RPC response dict.

### Option 5: Verify Pool Account Is Pool State
Check if the account we're fetching is actually the pool state account:
```python
# Add to _extract_raydium_amm()
owner = pool_data.get("owner")
logger.info(f"Pool account {pool_address[:16]}... owner: {owner}")

# Should be one of:
# - RAYDIUM_AMM_PROGRAM: 675kPX9MHTjS2zt1qrVrrVrZg1ankqqgoerEmJlwQ1K
# - RAYDIUM_CPMM_PROGRAM: CPMMoo8L3F4rn9aUYn2QRiPK5VrKMjstm69edQaMQAC
# - PUMPSWAP_PROGRAM: pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA
```

---

## Key Files

| File | Lines | Issue |
|------|-------|-------|
| `src/core/pool_discovery.py` | 185-186 | Offsets might be wrong or data structure different |
| `src/core/pool_detector.py` | 720-722 | RaydiumAMMParser has same offsets (but different implementation) |
| `src/core/pool_price_engine.py` | varies | Should validate vault extraction |

---

## Hypothesis

The offsets 232-296 might not actually contain vault **addresses** in PumpSwap/Raydium pools. They might contain:
1. Other PDAs or accounts
2. Pool ID or other identifiers
3. Different structure in PumpSwap vs standard Raydium

**Evidence**: All tokens returning identical addresses suggests we're reading the same field from all pools (which would be constant across pools), not unique vault addresses.

---

## Solution Approach

1. **Compare with RaydiumAMMParser**: It successfully extracts vault info - investigate its full implementation
2. **Fetch actual vault info**: Use `_fetch_token_account_info()` to validate extracted addresses
3. **Check pool owner**: Verify all detected pools are actually owned by PumpSwap program
4. **Inspect raw data**: Log hex dumps of pool state to understand structure
5. **Research Raydium/PumpSwap specs**: Verify LIQUIDITY_STATE_LAYOUT_V4 offsets are correct

---

## Timeline

- **Before fixes**: All pools had fake addresses from offset 8-72
- **After offset fix**: Still getting same addresses from offset 232-296
- **Current state**: 3 pools registered, all with identical vaults
- **Next action**: Debug why offsets 232-296 return same data for all pools
