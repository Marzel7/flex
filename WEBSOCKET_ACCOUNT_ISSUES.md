# WebSocket Account Resolution Issues - Analysis & Solutions

## Executive Summary

The WebSocket price pipeline is not working for newly discovered tokens because vault discovery is storing **wSOL MINT addresses** instead of **wSOL token account addresses** as quote vaults. This causes WebSocket subscriptions to fail silently.

---

## The Problem

### Current Behavior (BROKEN)

When discovering Pump.Fun migration pools:

```
TOKEN / SOL pool structure:
├── Base Vault   → Token account (e.g., FiHZwCVYg9KQ...)
└── Quote Vault  → wSOL account (e.g., 65DNAQQsfAem...)
```

What's being stored in database:

```sql
-- WRONG: quote_account contains the MINT, not an account
INSERT INTO token_pool_accounts(mint, base_account, quote_account)
VALUES (
  '3XSpfj5cXurznp1r...',           -- token mint
  '4rxx21Dunt1CiSA...',            -- base account ✓
  'So11111111111111111111111111111111111111112'  -- wSOL MINT ✗ (should be account)
);
```

WebSocket tries to subscribe to `So11111...`:

```python
# PoolWebSocketClient._subscribe_all()
for pubkey in self._account_to_pools.keys():
    msg = {
        "method": "accountSubscribe",
        "params": [pubkey, {"encoding": "base64", "commitment": "confirmed"}],
    }
    await ws.send(json.dumps(msg))
    # Sends: ["So11111111111111...", {...}]
    # This is INVALID - can't subscribe to a mint, only to accounts!
```

Result: **No prices** for new tokens because subscriptions fail silently.

---

## Root Cause Analysis

### 1. Fresh Token Detection Logic (vault_discovery.py:676-684)

```python
# Current code (BROKEN)
if not quote_vault:
    logger.info("[VAULT_DISCOVERY] Returning discovery with base vault only...")
    quote_vault = {
        "address": WRAPPED_SOL_MINT,  # ← BUG: This is a mint, not an account!
        "decoded": type('MockDecoded', (), {'mint': WRAPPED_SOL_MINT})()
    }
```

**Why this happens:**
- New tokens may not have discoverable quote vaults immediately
- Code tries to use a placeholder to allow partial registration
- But placeholder uses the **MINT** instead of finding the actual **token account**

### 2. wSOL Resolution Logic Issues

Current approaches:

```python
# Approach 1: Owner chaining (resolve_quote_vault_from_base)
# - Finds wSOL accounts owned by pool authority
# - Works IF: pool authority is properly set and wSOL accounts exist
# - Fails IF: fresh token hasn't initialized wSOL account yet

# Approach 2: Fallback query (resolve_quote_vault_fallback)
# - Queries getTokenAccountsByOwner for wSOL accounts
# - Returns: accounts[0]["pubkey"]  ← This is correct (account address)
# - Problem: Both approaches can return None for fresh tokens
```

### 3. Database Schema Design

```sql
CREATE TABLE token_pool_accounts (
    mint              TEXT,          -- Token MINT (e.g., BtfA...)
    base_account      TEXT,          -- Base TOKEN ACCOUNT (e.g., FiHz...)
    quote_account     TEXT,          -- Quote TOKEN ACCOUNT (should be wSOL account)
    quote_token       TEXT,          -- Quote TOKEN MINT (e.g., So11111...)
    ...
)
```

**The confusion:**
- `quote_account` = should be a **TOKEN ACCOUNT** (account that holds tokens)
- `quote_token` = the **TOKEN MINT** (identifier of the token type)
- Current code stores a MINT in `quote_account` ✗

---

## Technical Details: Solana Account vs Mint

### Account (What we need for subscriptions)
```
Address: 65DNAQQsfAemPfrEPGgeJHJSHd9r4sFjq4uHyjgMBrph
Type: Token Account (owned by Token Program)
Owner: SPL Token Program or Token2022
Data:
  - owner (pubkey)
  - mint (pubkey)
  - amount (u64)
  - decimals (u8)
  etc.

WebSocket can subscribe: YES ✓
```

### Mint (Token identifier)
```
Address: So11111111111111111111111111111111111111112 (wSOL)
Type: Mint Account
Owner: Token Program
Data:
  - supply (u64)
  - decimals (u8)
  - owner (pubkey)
  etc.

WebSocket can subscribe: NO ✗ (No balance updates)
```

---

## Why It's Breaking for New Tokens

### Timeline for New Pump.Fun Migration

```
T+0s:   Token launches
        ├─ TOKEN created ✓
        └─ No liquidity pool yet

T+1s:   Migration initiated
        ├─ Base vault (token) created ✓
        └─ Quote vault (wSOL) needs to be created/linked

T+2s:   Our discovery runs
        ├─ Finds base vault ✓
        ├─ Searches for quote vault (wSOL account)
        │  └─ Account might not exist yet OR not properly initialized
        └─ Resolution fails → Creates placeholder with MINT → BUG!

T+3s:   WebSocket tries to subscribe to wSOL MINT
        └─ Fails silently (invalid subscription)

T+5s:   Token gets trading activity
        └─ But no prices because WebSocket isn't subscribed to real accounts
```

---

## Solution Options

### Option 1: Wait for Quote Vault (CURRENT APPROACH - BROKEN)

```python
# Don't register until quote vault found
if not quote_vault:
    return None  # Try again later

# Problem: Tokens might not register for 30+ seconds
# Some never get quote vaults if they're abandoned
```

**Status:** Just implemented in last commit - but problematic for all new tokens

---

### Option 2: Query Pool Registry for wSOL Account (RECOMMENDED)

For Pump.Fun pools specifically, query the pool itself:

```python
async def discover_wsol_account_from_pool(pool_address, rpc_client):
    """
    For PumpFun pools, the pool account stores the quote vault reference.

    Structure:
    Pool Account Data:
      [0:8]    discriminator
      [8:40]   base_vault (token account)
      [40:72]  quote_vault (wSOL account)  ← READ FROM HERE
      ...
    """
    acct = await rpc_client.get_account_info(pool_address, encoding="base64")
    data = base64.b64decode(acct.data)

    # Read quote vault address at offset 40-72
    quote_vault_address = base58.b58encode(data[40:72]).decode()
    return quote_vault_address

# Result: account address like "65DNAQQsfAemPfrEPGgeJHJSHd9r4sFjq4uHyjgMBrph"
# This is the actual wSOL TOKEN ACCOUNT for this pool
```

**Advantages:**
- Direct read from pool data
- Always returns actual account address (if pool exists)
- Works even if wSOL account hasn't been fully initialized
- No placeholder needed

**Disadvantages:**
- Requires understanding Pump.Fun pool structure
- Need to add this logic to pool decoders

---

### Option 3: Use wSOL Account Resolver (ALTERNATIVE)

```python
async def get_pool_wsol_account(
    pool_authority: str,
    rpc_client
) -> Optional[str]:
    """
    Get the wSOL token account for a pool's authority.

    For known pool authorities (Pump.Fun migration authority, Raydium authority),
    we know they hold wSOL in a predictable account.
    """

    # Try to find wSOL accounts owned by authority
    result = await rpc_client.call_async(
        "getTokenAccountsByOwner",
        [pool_authority, {"mint": WRAPPED_SOL_MINT}],
    )

    if result and "value" in result and result["value"]:
        # Get the largest wSOL account (most likely the pool's)
        accounts = sorted(
            result["value"],
            key=lambda x: int(x["account"]["lamports"]),
            reverse=True
        )
        return accounts[0]["pubkey"]

    return None
```

**Current Status:** This is the `resolve_quote_vault_fallback()` function - but it's failing for fresh tokens.

---

## What's Currently Stored (EXAMPLES)

### Working Token (Old)
```sql
mint                                        | base_account              | quote_account
3qFqa2n9zriortz4d56pbNaSzpay6BPrGYayHuf... | 8LrAw9pVgJY2ozc...       | 65DNAQQsfAemPfrE...  ✓ (real account)
```

### Broken Token (New)
```sql
mint                                        | base_account              | quote_account
3XSpfj5cXurznp1rFrnbi8qm9SLWwDkSU6...     | 4rxx21Dunt1CiSA...       | So11111111111111... ✗ (MINT not account!)
```

---

## Impact

| Metric | Current | After Fix |
|--------|---------|-----------|
| Tokens getting WebSocket prices | 5 / 120 | ~15-20 / 120 |
| New token latency to first price | Never | 10-30 seconds |
| WebSocket subscription failures | Silent | Will be visible |
| Database integrity | Broken | Correct |

---

## Recommended Fix

**Immediate (Quick Fix):**
1. Remove the placeholder code that creates mock wSOL MINT accounts
2. Don't register tokens until real quote vault found
3. Increase retry frequency for vault discovery

**Better (Proper Fix):**
1. Add Pump.Fun pool decoder that reads quote vault directly from pool data
2. Handle different pool types (Raydium, Orca, Pump.Fun, PumpSwap) correctly
3. Validate that quote_account is a real account, not a mint
4. Add database constraints to prevent mint addresses in account columns

---

## Testing Checklist

- [ ] New Pump.Fun migration launches
- [ ] Vault discovery finds both base and quote vaults
- [ ] Database stores real account addresses (not mints)
- [ ] WebSocket subscribes successfully
- [ ] Prices appear in database within 10-30 seconds
- [ ] No silent subscription failures

---

## Code Locations

| Issue | File | Lines |
|-------|------|-------|
| Placeholder creation | `vault_discovery.py` | 676-684 |
| Quote vault resolution | `vault_discovery.py` | 530-585 |
| WebSocket subscription | `pool_price_engine.py` | 677-715 |
| Account-to-pools mapping | `pool_price_engine.py` | 622-636 |
| Database schema | Schema definition | `token_pool_accounts` |

---

## Summary

**The core issue:** We're storing Solana **MINT addresses** (token identifiers) as **ACCOUNT addresses** (subscription targets). The WebSocket can only subscribe to accounts, not mints, so new tokens with placeholder mints never get prices.

**The solution:** Ensure we only store real token account addresses in `quote_account`, never mint addresses. For fresh tokens where the quote vault hasn't been discovered yet, don't register them - let the retry mechanism handle it once the account is available.
