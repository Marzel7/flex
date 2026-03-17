# Vault Discovery Debug: New Token Stuck on DexScreener

## Token in Question
- **Mint**: `BWGFePEdaTBSEqRzZ27fsFSrdLo7uE1AzAnXbYqGpump`
- **Created**: ~20 minutes ago (detected by listener)
- **Trading**: Yes, actively on PumpSwap with 2+ pairs
- **Current Source**: `dexscreener` (expected: `pool`)
- **Current Price**: Working (from DexScreener API)

## The Problem
Token has been trading for 20+ minutes but:
1. ✅ Listener detected migration (token exists in DB)
2. ✅ Initial pool discovery attempt made
3. ❌ No pools registered (`token_pool_accounts` empty)
4. ❌ No automatic retry triggered OR retries all failed
5. ❌ Still showing `dexscreener` source instead of `pool`

## Discovery Pipeline Flow

```
1. Migration detected by listener WebSocket
   ↓
2. _process_migration_with_mint() called
   ├─ STAGE 1: RPC vault discovery (authoritative)
   │  └─ get_token_largest_accounts() → RPC indexing delay?
   ├─ STAGE 2: TX parsing (fast path)
   │  └─ Extract pool from migration TX
   ├─ If both fail → SCHEDULE RETRY
   │  └─ asyncio.create_task(_retry_pool_discovery(...))
   │     ├─ Attempt 1 @ 3s:  discover_and_register_all_pools()
   │     ├─ Attempt 2 @ 8s:  discover_and_register_all_pools()
   │     ├─ Attempt 3 @ 20s: discover_and_register_all_pools()
   │     ├─ Attempt 4 @ 45s: discover_and_register_all_pools()
   │     └─ Fallback phase:
   │        ├─ TX candidates extraction
   │        ├─ PumpFun V1 vault pair discovery
   │        └─ Post-migration pool discovery
   └─ STAGE 3: price_worker.trigger_pool_refresh()
      └─ Start WebSocket with discovered pools
```

## Why It Might Fail

### Scenario A: getTokenLargestAccounts Still Returning Empty
- RPC node has not indexed this token's accounts yet
- This is rare after 20 minutes but possible on slow indexing
- **Evidence**: Check what getTokenLargestAccounts returns

### Scenario B: Retry Task Never Ran
- If listener restarted after token creation, retry task was lost
- Retry tasks are in-memory; they don't survive process restart
- **Evidence**: Check listener logs for "[POOL_RETRY]" or "[POOL_DISCOVER_FALLBACK]"

### Scenario C: Retry Ran But All Attempts Failed
- All 4 RPC retries returned empty
- All fallback strategies failed
- Token got stuck in "resolving" state with no pools
- **Evidence**: Check token_states in memory (not persisted)

### Scenario D: TX Parsing Failed to Extract Pool
- Initial TX parsing (STAGE 2) should have extracted pool address
- If that also failed, retry wouldn't help unless RPC indexing caught up
- **Evidence**: Check if pool address was extracted from migration TX

## How to Debug This

### 1. Check Database State
```sql
-- Token in DB?
SELECT mint, price_source, price_current, created_at FROM token_analysis
WHERE mint = 'BWGFePEdaTBSEqRzZ27fsFSrdLo7uE1AzAnXbYqGpump';

-- Pools registered?
SELECT COUNT(*) FROM token_pool_accounts
WHERE mint = 'BWGFePEdaTBSEqRzZ27fsFSrdLo7uE1AzAnXbYqGpump';

-- Migration TX stored?
SELECT migration_tx FROM token_analysis
WHERE mint = 'BWGFePEdaTBSEqRzZ27fsFSrdLo7uE1AzAnXbYqGpump';
```

### 2. Test RPC getTokenLargestAccounts
```bash
# Does RPC have the accounts indexed?
curl -s -X POST https://api.mainnet-beta.solana.com \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "getTokenLargestAccounts",
    "params": ["BWGFePEdaTBSEqRzZ27fsFSrdLo7uE1AzAnXbYqGpump", {"limit": 20}]
  }' | jq '.result.value | length'
```

### 3. Test TX Parsing
- Get migration_tx from DB
- Parse logs to extract pool address
- Verify pool address is valid

### 4. Check Listener Logs
```bash
# Was retry scheduled?
grep -i "POOL_RETRY\|POOL_DISCOVER_FALLBACK\|Scheduling retry" listener.log | tail -20

# Did listener restart after token creation?
grep -i "listener.*start\|listening.*migration" listener.log | tail -5
```

### 5. Simulate Vault Discovery
Run the discovery function manually for this token

---

## Most Likely Root Cause

Given the evidence pattern:
- Token exists in DB (listener ran)
- No pools registered (discovery failed initially)
- DexScreener fallback active (system working correctly)
- But no automatic retry triggered OR retries didn't work

**Most likely**: Listener restarted after token was created, losing the in-memory retry task.

Retry tasks are scheduled via `asyncio.create_task()` and live only in memory. They don't survive:
- Process restart
- Signal handling (SIGTERM)
- Listener crash/recovery

If listener restarted at any point between migration detection and retry completion, the retry would be lost.

---

## Solution Options

1. **Persistence**: Store pending retries in DB, restore on restart
2. **Periodic Refresh**: Every 30s, check all tokens without pools and retry
3. **Manual Trigger**: Provide manual command to re-trigger discovery
4. **DexScreener Fallback**: Accept that new tokens use DexScreener until RPC indexing catches up

**Recommended**: Option 2 (Periodic Refresh) — simple, robust, handles restarts automatically.
