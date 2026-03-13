# Pool Registration Guide

**Date:** March 13, 2026
**Purpose:** Enable WebSocket pool subscriptions and real-time price updates

---

## Overview

WebSocket pool subscriptions require **at least one registered pool** to connect. This guide explains how to find pool addresses and register them with the system.

---

## Database Structure

Pools are stored in the `token_pool_accounts` table:

```sql
CREATE TABLE token_pool_accounts (
    mint              TEXT,              -- Token mint address
    base_account      TEXT,              -- Pool base reserve account
    quote_account     TEXT,              -- Pool quote reserve account
    pool_program      TEXT,              -- AMM program (raydium_amm, orca, etc)
    base_token        TEXT,              -- Base token mint
    quote_token       TEXT,              -- Quote token mint (default: SOL)
    base_decimals     INTEGER,           -- Base token decimals
    quote_decimals    INTEGER,           -- Quote token decimals
    last_reserve_fetch INTEGER,          -- Last update timestamp
    is_active         BOOLEAN,           -- Active status
    created_at        INTEGER,           -- Creation timestamp
    updated_at        INTEGER,           -- Last update timestamp
    PRIMARY KEY (mint, base_account)     -- Support multiple pools per token!
);
```

---

## Quick Start: Register Test Pools

For testing, use the provided script:

```bash
./scripts/register-test-pools.sh
```

This registers 3 popular Raydium pools:
- **USDC** — High liquidity stablecoin
- **COPE** — Community token
- **mSOL** — Marinade staking derivative

After registration:
```bash
./scripts/restart.sh
```

WebSocket will **automatically connect** and start receiving events.

---

## Manual Registration

### Step 1: Find Pool Addresses

Use any of these methods:

**Option A: Raydium UI**
1. Go to https://raydium.io/fusion
2. Select a pool
3. Click "Pool ID" to copy the pool address
4. Look for reserve account addresses in the pool details

**Option B: Solana Explorer**
1. Go to https://solscan.io
2. Search for token mint address
3. Look for "Raydium" or "Orca" accounts
4. Note the base and quote reserve account addresses

**Option C: Raydium API**
```bash
# Get all pools for a token
curl https://api.raydium.io/v2/main/info

# Filter by your token mint
jq '.data.pools[] | select(.mintA.address == "YOUR_MINT")'
```

### Step 2: Gather Pool Information

You need:

| Field | Example | Where to find |
|-------|---------|---------------|
| `mint` | `EPjFWaLb3...` | Token contract address |
| `base_account` | `8K3HWwYv...` | Pool base reserve (Solana chain) |
| `quote_account` | `kinXVgW7K...` | Pool quote reserve (Solana chain) |
| `base_token` | `EPjFWaLb3...` | Same as mint |
| `quote_token` | `So1111...` | Usually SOL mint |
| `base_decimals` | `6` | Token decimals (check token info) |
| `quote_decimals` | `9` | SOL = 9, USDC = 6, etc |

### Step 3: Call the Registration API

```bash
curl -X POST http://localhost:5002/api/price/pool/register \
  -H 'Content-Type: application/json' \
  -d '{
    "pool_accounts": [
      {
        "mint": "EPjFWaLb3odRvqA8E8h6UPs4mkfrEFAJiUbhA84wHvHU",
        "base_account": "8K3HWwYvMKSRP9LsNYqEfKdtwq33P99qiDPVvfySN6qf",
        "quote_account": "kinXVgW7KPBCw5d4qz5x6W5eWTSAm9CAxaKeVXya5Ek",
        "base_token": "EPjFWaLb3odRvqA8E8h6UPs4mkfrEFAJiUbhA84wHvHU",
        "quote_token": "So11111111111111111111111111111111111111112",
        "base_decimals": 6,
        "quote_decimals": 9,
        "pool_program": "raydium_amm"
      }
    ]
  }'
```

**Response:**
```json
{
  "registered": 1,
  "status": "ok"
}
```

---

## Register Multiple Pools (Aggregation)

The system supports multiple pools per token for price aggregation:

```bash
# Pool 1
curl -X POST http://localhost:5002/api/price/pool/register \
  -H 'Content-Type: application/json' \
  -d '{
    "pool_accounts": [{
      "mint": "EPjFWaLb3odRvqA8E8h6UPs4mkfrEFAJiUbhA84wHvHU",
      "base_account": "POOL_1_BASE_ADDRESS",
      "quote_account": "POOL_1_QUOTE_ADDRESS",
      ...
    }]
  }'

# Pool 2 (same mint)
curl -X POST http://localhost:5002/api/price/pool/register \
  -H 'Content-Type: application/json' \
  -d '{
    "pool_accounts": [{
      "mint": "EPjFWaLb3odRvqA8E8h6UPs4mkfrEFAJiUbhA84wHvHU",
      "base_account": "POOL_2_BASE_ADDRESS",
      "quote_account": "POOL_2_QUOTE_ADDRESS",
      ...
    }]
  }'
```

System will:
- Track both pools independently
- Compute prices from each pool
- Aggregate using liquidity-weighted selection
- Annotate price source as `"pool(2)"`

---

## Verify Registration

### Check via Health Endpoint

```bash
curl -s http://localhost:5002/api/price/health | jq '.pool_stats'
```

Expected output:
```json
{
  "pools_registered": 1,
  "pool_prices_cached": 0,
  "pool_prices_fetched_last_cycle": 0,
  "ws": {
    "connected": false,
    "subscriptions": 0,
    "events_received": 0
  }
}
```

### Check Database

```bash
sqlite3 database/flex_complete_database.db \
  "SELECT mint, base_account, is_active FROM token_pool_accounts"
```

### Check WebSocket Status

After restart:
```bash
curl -s http://localhost:5002/api/price/health | jq '.pool_stats.ws'
```

Should show:
```json
{
  "connected": true,
  "subscriptions": 2,
  "events_received": N
}
```

---

## Troubleshooting

### WebSocket shows "✗ No"

**Cause:** No pools registered
**Fix:**
```bash
./scripts/register-test-pools.sh  # Or manually register a pool
./scripts/restart.sh
```

### Events not arriving

**Check 1:** Are pools registered?
```bash
curl http://localhost:5002/api/price/health | jq '.pool_stats.pools_registered'
# Should be > 0
```

**Check 2:** Are pools active?
```bash
sqlite3 database/flex_complete_database.db \
  "SELECT COUNT(*) FROM token_pool_accounts WHERE is_active=1"
```

**Check 3:** Is WebSocket connected?
```bash
curl http://localhost:5002/api/price/health | jq '.pool_stats.ws.connected'
# Should be true
```

**Check 4:** Are reserve accounts correct?
- Verify base_account and quote_account exist on-chain
- Use Solana Explorer to check account balances
- Wrong addresses = no events

### "Pool prices cached: 0"

**Cause:** Prices not computed yet (initial delay)
**Fix:** Wait 10-30 seconds for first refresh cycle
```bash
sleep 15
curl http://localhost:5002/api/price/health | jq '.pool_stats.pool_prices_cached'
```

---

## Common Pool Addresses

### Raydium Pools (High Liquidity)

| Token | Mint | Pool Program |
|-------|------|------|
| USDC | EPjFWaLb3odRvqA8E8h6UPs4mkfrEFAJiUbhA84wHvHU | raydium_amm |
| USDT | Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenEps | raydium_amm |
| mSOL | mSoLzYCxHdgNd4vkUcj9Xr8V2znhTW5mhTKSREm5LSb | raydium_amm |
| JTO | jtojtomepa8beP129n6jNijaQMNxTqyWqeTjWWstVX | raydium_amm |
| COPE | 8HGyAAB1yoM1ttS7pnqw6AFXeiTWEwzommLaYzrSF1xN | raydium_amm |

### Orca Pools

Orca uses different program: `whirLbMiicVdio4KfUqKKvsLrZtSqwNAUafgJMYco`

Search on Solscan for "Orca" accounts for any token.

---

## API Reference

### Register Pools

**Endpoint:** `POST /api/price/pool/register`

**Request:**
```json
{
  "pool_accounts": [
    {
      "mint": "string",           // Required: Token mint
      "base_account": "string",   // Required: Base reserve account
      "quote_account": "string",  // Required: Quote reserve account
      "base_token": "string",     // Optional: Base token mint
      "quote_token": "string",    // Optional: Quote token mint (default: SOL)
      "base_decimals": integer,   // Required: Base token decimals
      "quote_decimals": integer,  // Optional: Quote decimals (default: 9)
      "pool_program": "string"    // Optional: Program (default: raydium_amm)
    }
  ]
}
```

**Response:**
```json
{
  "registered": integer,  // Number of pools registered
  "status": "ok"
}
```

### Check Pools

**Endpoint:** `GET /api/price/health`

Returns `pool_stats` containing:
- `pools_registered` — Total registered
- `pool_prices_cached` — Distinct tokens with prices
- `ws.connected` — WebSocket status
- `ws.subscriptions` — Total subscriptions (2 per pool)

---

## Best Practices

### 1. Register High-Liquidity Pools
- Higher liquidity = more reliable prices
- More events = better real-time updates
- Less slippage = better price discovery

### 2. Use Multiple Pools (Aggregation)
- Register 2-3 pools per token
- System averages by liquidity
- Prevents manipulation attacks

### 3. Keep Pools Active
- Check `is_active` flag regularly
- Remove dead pools (no updates >5 min)
- Monitor WebSocket reconnects

### 4. Monitor Health Dashboard
- Check `/system-health` regularly
- Watch for disconnections
- Track event rates during trading

---

## Integration with Multi-Pool Aggregation

Once pools are registered, the system automatically:

1. **WebSocket subscribes** to all pool accounts
2. **Receives events** in real-time (<150ms latency)
3. **Decodes reserves** from SPL token accounts
4. **Computes prices** per pool every 10s
5. **Aggregates** using liquidity weighting
6. **Caches** final price with source annotation

Price source will show:
- Single pool: `"source": "pool"`
- Two pools: `"source": "pool(2)"`
- Three pools: `"source": "pool(3)"`

Check via API:
```bash
curl http://localhost:5002/api/price/{MINT} | jq '.source'
```

---

## Related Documentation

- [System Health Dashboard](SYSTEM_HEALTH_DASHBOARD.md) — Monitor pool connections
- [Multi-Pool Aggregation](MULTI_POOL_AGGREGATION_COMPLETE.md) — How aggregation works
- [WebSocket Implementation](WEBSOCKET_IMPLEMENTATION_COMPLETE.md) — Technical details

---

## Summary

1. **Find pool addresses** — Raydium UI, Solana Explorer, or API
2. **Gather pool info** — Mint, accounts, decimals
3. **Call register API** — POST to `/api/price/pool/register`
4. **Restart services** — WebSocket auto-connects
5. **Verify health** — Check `/system-health` dashboard
6. **Monitor prices** — Watch real-time updates

**WebSocket is now live! 🚀**
