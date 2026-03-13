# Pool Discovery Issue Analysis

**Status:** CRITICAL - Pool auto-registration failing despite pool address discovery
**Date:** 2026-03-13
**Impact:** WebSocket pricing unavailable for new tokens

---

## Executive Summary

The pool discovery system successfully detects pool addresses for new tokens (via RPC fallback), but fails to auto-register them for WebSocket pricing because the discovered address is a **vault account**, not the **pool PDA**.

The system confuses two different accounts:
- **Vault account:** Holds token balances, owner is the pool PDA
- **Pool account:** Contains pool structure and reserve information, needed for pricing

This prevents on-chain pricing from working despite having the pool address stored in the database.

---

## Current Behavior

### What Works ✅
1. **Migration detection:** WebSocket listener detects when tokens launch
2. **Pool address discovery:** RPC fallback successfully finds a pool-related account
3. **Database storage:** Pool address is saved to `token_analysis.pool_address`
4. **UI display:** New tokens appear in dashboard with price/market cap

### What Fails ❌
1. **Pool reserve extraction:** `PoolDiscovery.discover_and_register_pool()` cannot extract reserves from the discovered address
2. **Database registration:** Pools are never inserted into `token_pool_accounts` table (currently 0 pools registered)
3. **WebSocket subscription:** Without registered pools, WebSocket client stays disconnected
4. **On-chain pricing:** Price worker cannot fetch prices from vault reserves

### Test Case: Human Token
```
Token:          Human (2KZoR1XXpqrjDaaFThpe6tkUzNzYAKpD2iyhkUXmpump)
Created:        2026-03-13T18:05:10Z
Pool Discovered: J6Rb7pky6GsQ83EwYyG27w83hxqTKZ1uCcBhDqsNcjj1
Pool Registered: NO ❌
WebSocket:       DISCONNECTED
Pricing:         Unavailable
```

---

## Root Cause Analysis

### The Discovery Pipeline

```
Migration TX
    ↓
┌─ Cached TX Extraction
│  └─ Looks for PumpSwap program in innerInstructions
│     └─ Tries to extract first account from instruction
│        └─ FAILS: Transaction structure varies per token
│
└─ RPC Fallback (NEW)
   └─ getTokenLargestAccounts(mint)
      └─ Finds 20 token accounts
      └─ Checks smallest account's owner via getAccountInfo
      └─ Returns owner address
         └─ ⚠️ THIS IS A VAULT, NOT THE POOL
```

### Two Types of Accounts

**Vault Account (what we found):**
- Type: SPL Token Account
- Balance: Token reserves
- Owner: Pool PDA (the actual pool)
- Used for: Holding tokens
- Structure: Standard token account layout

**Pool Account (what we need):**
- Type: Varies by DEX (Raydium, Orca, etc)
- Balance: Pool state + reserve info
- Owner: Pool program (pAMMBay6... for PumpSwap)
- Used for: Pricing calculations
- Structure: DEX-specific binary format

### Why `extract_pool_reserves()` Fails

[src/core/pool_discovery.py:43-91](src/core/pool_discovery.py#L43-L91)

```python
async def extract_pool_reserves(self, pool_address: str, token_mint: str):
    # Fetches the account at pool_address
    pool_data = await self._fetch_account(pool_address)

    # Tries to extract based on pool program
    reserves = await self._extract_from_pool_data(pool_data, pool_address, token_mint)

    # When pool_address is actually a vault:
    # - pool_data has standard token account structure (32-byte owner field)
    # - Parsers expect Raydium/Orca pool structure (different layout)
    # - Extraction fails: "Could not extract reserves from pool"
```

The method tries these parsers in `_extract_from_pool_data()`:
- `_extract_raydium_amm()` — expects Raydium AMM pool structure
- `_extract_raydium_cpmm()` — expects Raydium CPMM pool structure
- `_extract_orca_whirlpool()` — expects Orca Whirlpool structure

None match a standard token account, so all return None.

---

## Evidence from Listener Logs

```log
[EVENT] 🚀 MIGRATION DETECTED: 2KZoR1XXpqrjDaaFThpe6tkUzNzYAKpD2iyhkUXmpump
[POOL] ⏳ Cached tx pool extraction failed, attempting RPC discovery...
[POOL] Checking 20 token accounts to find pool...
[POOL]   Checking <token_account_1> (balance: 1000)
[POOL]     Owner: J6Rb7pky6GsQ83EwYyG27w83hxqTKZ1uCcBhDqsNcjj1
[POOL] ✅ Pool discovered via RPC: J6Rb7pky6GsQ83EwYyG27w83hxqTKZ1uCcBhDqsNcjj1
[POOL] ⚠️  Pool auto-registration error: 'PumpFunCurveListener' object has no attribute 'database_path'
```

After fix (DB_PATH):
```log
[POOL] ✅ Pool discovered via RPC: J6Rb7pky6GsQ83EwYyG27w83hxqTKZ1uCcBhDqsNcjj1
[POOL] ⏳ Extracting pool reserves from J6Rb7pky6GsQ83EwYyG27w83hxqTKZ1uCcBhDqsNcjj1...
[POOL] ⚠️  Could not auto-register pool reserves
```

---

## Database State

### token_analysis Table (2311 tokens)
```sql
SELECT mint, pool_address FROM token_analysis
WHERE mint = '2KZoR1XXpqrjDaaFThpe6tkUzNzYAKpD2iyhkUXmpump';

2KZoR1XXpqrjDaaFThpe6tkUzNzYAKpD2iyhkUXmpump | J6Rb7pky6GsQ83EwYyG27w83hxqTKZ1uCcBhDqsNcjj1
```

✅ Pool address stored

### token_pool_accounts Table (0 pools)
```sql
SELECT COUNT(*) FROM token_pool_accounts;
0
```

❌ No pools registered → WebSocket not subscribing

---

## Why Two Different Pool Discovery Methods Exist

### Method 1: Cached Transaction Extraction
**Used in:** [pumpfun_curve_listener.py:2147](src/core/pumpfun_curve_listener.py#L2147)

```python
async def _extract_pool_from_tx(self, tx_data: Dict) -> Optional[str]:
    """Extract PumpSwap pool from transaction innerInstructions"""
    # Finds PumpSwap program calls in the migration transaction
    # Returns the first account in PumpSwap instruction
    # ⚠️ Assumes transaction structure is consistent
```

**Success Rate:** ~92% (from January commits)
**Failure Mode:** Fragile - position-based assumptions don't hold for all token structures

### Method 2: RPC-Based Vault Discovery
**Used in:** [pumpfun_curve_listener.py:2152](src/core/pumpfun_curve_listener.py#L2152) (NEW fallback)

```python
async def _find_pool_account(self, token_mint: str) -> Optional[str]:
    """Find pool via getTokenLargestAccounts + getAccountInfo"""
    # Queries token accounts for the mint
    # Returns the owner of the smallest account (usually the vault)
    # ⚠️ Returns vault, not pool PDA
```

**Success Rate:** 100% at finding *some* account
**Failure Mode:** Returns vault instead of pool → extract_pool_reserves() fails

---

## The Missing Piece: Pool PDA Discovery

To work correctly, we need to find the **pool account**, not just a vault. The current approaches both fail:

```
Cached TX Extraction ──────────→ Sometimes gets pool PDA ✓ or misses it ✗
RPC Vault Discovery ───────────→ Gets vault (owner=pool_pda) ✓ but not pool ✗
What we need ──────────────────→ Get the owner account (pool_pda itself)
```

The solution: When we find the vault, we need to **query the vault's owner** and use *that* as the pool address.

### Corrected Flow
```
Token launches
    ↓
Discover vault via getTokenLargestAccounts
    ↓
Get vault's owner via getAccountInfo (vault_owner)
    ↓
Use vault_owner as pool_address
    ↓
extract_pool_reserves(vault_owner)
    ↓
✅ Extracts actual pool structure
    ↓
Register in token_pool_accounts
    ↓
WebSocket subscribes
    ↓
On-chain pricing works
```

---

## Impact on Users

| Scenario | Current | With Fix |
|----------|---------|----------|
| New token launches | ✅ Appears in UI | ✅ Appears in UI |
| Price available | ❌ $0 or external | ✅ On-chain real-time |
| 5 min after launch | ❌ Still $0 | ✅ Accurate + updating |
| Multiple pools | N/A | ✅ Liquidity-weighted aggregate |

---

## Solution Architecture (Planned)

### Short-term (Easy Fix)
Modify `_find_pool_account()` to return the vault's owner instead of the vault:

[src/core/pumpfun_curve_listener.py:1280-1372](src/core/pumpfun_curve_listener.py#L1280-L1372)

```python
async def _find_pool_account(self, token_mint: str) -> Optional[str]:
    # ... find vault ...
    vault_owner = info.get("owner")  # This is the pool PDA
    return vault_owner  # ← Return the POOL, not the vault
```

**Estimated effort:** 10 minutes
**Risk:** Low - only changes return value
**Impact:** Would likely enable ~80% of tokens to work

### Medium-term (Robust)
Implement program ownership detection (from POOL_DISCOVERY_HARDENED_DESIGN.md):

- Query vault to get pool PDA (owner)
- Verify pool PDA is owned by correct program (Raydium/Orca/PumpSwap)
- Parse correct DEX structure based on owner program
- Extract base/quote accounts reliably

**Estimated effort:** 2-3 days
**Risk:** Medium - new parsing logic
**Impact:** 95%+ success rate

### Long-term (Complete)
Replace fragile transaction extraction entirely with program-aware detection:

- Detect when migration happens (via WebSocket)
- Query Solana for all program-created accounts for this mint
- Use program IDs as signal (Raydium=0x27afe...f, Orca=0xwhir...)
- Build account graph to find pool → vaults

**Estimated effort:** 1 week
**Risk:** Low - follows established patterns
**Impact:** 100% coverage + insight into pool structure

---

## Files to Modify

| File | Issue | Fix |
|------|-------|-----|
| [src/core/pumpfun_curve_listener.py](src/core/pumpfun_curve_listener.py) | `_find_pool_account()` returns vault instead of pool | Get vault owner and return that |
| [src/core/pool_discovery.py](src/core/pool_discovery.py) | Parser expects pool PDA structure | Will work once correct address is passed |
| [src/core/price_worker.py](src/core/price_worker.py) | No changes needed | Will benefit once pools register |

---

## Testing the Fix

### Before
```bash
# Token launches
curl http://localhost:5002/api/price/MINT | jq '.source'
"unavailable"

curl http://localhost:5002/api/price/health | jq '.pool_stats.pools_registered'
0
```

### After
```bash
# Token launches → pool auto-discovered
curl http://localhost:5002/api/price/MINT | jq '.source'
"pool"

curl http://localhost:5002/api/price/health | jq '.pool_stats.pools_registered'
1

curl http://localhost:5002/api/price/health | jq '.pool_stats.ws.connected'
true

curl http://localhost:5002/api/price/MINT | jq '.price_usd'
0.000000123
```

---

## Related Documentation

- [POOL_DISCOVERY_HARDENED_DESIGN.md](./POOL_DISCOVERY_HARDENED_DESIGN.md) — Comprehensive redesign addressing all fragility issues
- [POOL_DISCOVERY_AND_ONCHAIN_PRICING.md](./POOL_DISCOVERY_AND_ONCHAIN_PRICING.md) — Current system architecture
- [MULTI_POOL_AGGREGATION_REVIEW.md](./MULTI_POOL_AGGREGATION_REVIEW.md) — Aggregation system (ready once pools register)

---

## Summary

**What we learned:**
1. ✅ WebSocket migration detection works
2. ✅ RPC fallback successfully finds accounts
3. ✅ Database schema supports multiple pools
4. ✅ Multi-pool aggregation is implemented
5. ❌ **Single missing piece:** Vault ≠ Pool PDA

**One-line fix:** Return the vault's **owner** instead of the vault itself.

**Current blocker:** Pool account address discovery incomplete.
**Next action:** Implement vault owner lookup to find actual pool PDA.
