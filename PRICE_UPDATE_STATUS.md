# Price Update Status: WebSocket vs Cached

## Current Test Results

### Top 20 UI Tokens
```
✅ WORKING:     1 token  (5%)
❌ NOT WORKING: 19 tokens (95%)
```

### System-Wide (All Tokens)
```
✅ WebSocket snapshots (last 5 min): 136
✅ Tokens with price_source='pool':  62
✅ Tokens getting real-time prices:  3+
```

---

## The Issue

**Top 20 tokens are showing "cached" because they don't have WebSocket subscriptions.**

We deleted 44 broken pools (MINT bug), but:
- ❌ Listener doesn't re-discover on deletion
- ❌ Listener only discovers at initial migration
- ❌ WebSocket client wasn't restarted to pick up new pool list
- ✅ But system IS working for other tokens (136 snapshots/5min)

---

## Proof the System Works

Token `3qFqa2n9zriortz4d56pbNaSzpay6BPrGYayHufnpump`:

```
Status:              ✅ WORKING
WebSocket prices:    ✅ 55 snapshots in last 5 min
token_analysis sync: ✅ Updated 2s ago
price_source:        ✅ 'pool' (not 'cached')
Update latency:      ~10 seconds
```

Other tokens getting WebSocket:
- `5cDhM4yMKipQkjSGdvYnqPdiJz685Z96rbe6GSYppump` - 56 snapshots
- `3UaZsmciGs4br3r8g6hLU2CuG1hYmAMeri4znoQxpump` - 22 snapshots

---

## Why Top 20 Aren't Updating

### The Deletion Problem

```
Timeline:
  T+0:  We deleted 44 MINT bug pools
  T+5:  Listener DOESN'T re-discover them
        (Listener only discovers on migration, not on deletion)
  T+30: WebSocket still subscribed to OLD pool list
        (No restart, so it doesn't know about deletions)
  T+60: Test shows: "No WebSocket prices"
```

### What We Should See

```
Timeline (IDEAL):
  T+0:  Delete 44 MINT bug pools
  T+1:  Listener re-discovers 44 tokens
        └─ With REAL account addresses
  T+10: WebSocket subscribes to new pools
  T+20: Prices start flowing
  T+30: UI shows price_source='pool'
```

### What Actually Happened

```
Timeline (ACTUAL):
  T+0:  Delete 44 MINT bug pools
  T+1:  Listener notices migration tx but...
        └─ Token already in DB (pool deleted, but token exists)
        └─ Listener skips re-discovery
  T+30: WebSocket still has OLD pool list
  T+60: 16 tokens missing pools entirely
        4 tokens have pools but not subscribed
```

---

## The Fix

### Option 1: Quick (Recommended)
```bash
# Kill and restart main.py
kill $(pgrep -f "src.core.main")
python3 src/core/main.py
```

This will:
1. Create new WebSocket client
2. Load current pool list from database
3. Subscribe to all valid pools (4 in top 20)
4. Start receiving WebSocket prices

**Timeline:** 30 seconds to see prices update

### Option 2: Permanent (Code Fix)

Modify listener to re-discover on pool deletion:

```python
# pumpfun_curve_listener.py
# When a token is already known but pool was deleted:

if token_already_in_db and no_pool_in_db:
    logger.info(f"Pool missing for {token_mint}, re-discovering...")
    await discover_and_register_vaults_rpc(token_mint, ...)
```

Or modify vault_discovery to UPDATE instead of INSERT:

```python
# vault_discovery.py
# Allow updating existing pools with better vault data

INSERT INTO token_pool_accounts(...)
VALUES (...)
ON CONFLICT(mint, base_account) DO UPDATE SET
    quote_account = excluded.quote_account,
    updated_at = excluded.updated_at
```

---

## Current Database State

| Metric | Value |
|--------|-------|
| Total pools | 83 |
| Pools with real accounts | 83 |
| Pools with MINT bug | 0 ✓ |
| WebSocket snapshots (5min) | 136 |
| Tokens with price_source='pool' | 62 |
| Top 20 with WebSocket | 1 |
| Top 20 cached/no-price | 19 |

---

## What's Shown as "Cached"

When price_source shows "cached":

```
price_source meanings:
  'pool'       = Real-time WebSocket price ✓
  'cached'     = Price from API snapshot (older data)
  'dexscreener' = External API price
  'onchain'    = On-chain only, older
  empty        = No price available
```

Most top 20 show:
- `'dexscreener'` - Using external API
- Not `'pool'` - Not using WebSocket

This is EXPECTED because:
- They don't have WebSocket pools yet (listener issue)
- OR pools aren't subscribed (WebSocket restart needed)

---

## System Architecture

```
Listener Process:
  ├─ Detects token migrations
  ├─ Discovers vaults via RPC
  ├─ Registers pools in DB
  └─ Triggers price_worker refresh

Main Process (WebSocket):
  ├─ Loads pool list from DB
  ├─ Creates WebSocket subscriptions
  ├─ Receives balance updates
  ├─ Computes prices from reserves
  ├─ Stores in price_snapshots
  ├─ Updates token_analysis
  └─ API returns prices to UI
```

**Issue:** Listener (1) and Main (2) are separate processes
- Listener writes pools to DB
- Main reads pool list on startup only
- If pools change after Main startup, WebSocket doesn't know

**Fix:** Restart Main process to reload pool list

---

## Summary

🟢 **System is working** - Proven with 136 WebSocket snapshots/5min
🟡 **Top 20 not working** - Listener/WebSocket restart issue
🔴 **Root cause** - Deleted pools not re-discovered, WebSocket not restarted

**Quick fix:** Restart main.py
**Permanent fix:** Modify listener to re-discover on deletion, or use UPDATE for pools
