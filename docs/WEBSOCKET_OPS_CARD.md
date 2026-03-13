# WebSocket Pool Upgrade — Ops Quick Reference Card

**Print this or keep in your on-call runbook.**

---

## Health Check (30 seconds)

```bash
curl -s http://localhost:5002/api/price/health | jq '.pool_stats.ws'
```

**All green if:**
- ✅ `connected: true`
- ✅ `events_received` > 0 (and increasing)
- ✅ `is_stale: false`
- ✅ `reconnects: 0` (or very low)

---

## Alerts & Troubleshooting

### 🔴 `ws.connected: false`

**Problem:** WebSocket not connected

**Check:**
```bash
grep "Pool WebSocket" logs/dev_intelligence.log | tail -5
```

**Fix:**
1. Verify `HELIUS_WS_URL` environment variable set correctly
2. Check network connectivity to Helius endpoint
3. Verify API key in URL is valid
4. Restart price service

### 🟡 `ws.is_stale: true`

**Problem:** No WS events for >2 minutes

**What happens:** System automatically switches to RPC fallback polling every 30s (instead of 60s). Prices still accurate, just less frequent.

**Check:**
```bash
curl -s http://localhost:5002/api/price/health | jq '.pool_stats.pool_prices_fetched_last_cycle'
# Should still be > 0 (fallback working)
```

**Fix:**
1. Check Helius status page
2. Verify network to Helius not blocked
3. Check if Solana blockchain itself is slow
4. Wait 2-5 minutes — may auto-recover

### 🟡 `events_deduplicated` very high (>50%)

**Problem:** Too many duplicate events (same slot)

**What happens:** Normal during high trading volume. System skips duplicates.

**Fix:**
- No action needed (working as designed)
- Monitor CPU usage — if high, may need to optimize event processing

### 🔴 `reconnects` increasing frequently (>1 per minute)

**Problem:** WebSocket repeatedly disconnecting

**Check:**
```bash
grep "Pool WebSocket reconnecting" logs/dev_intelligence.log | wc -l
# Should be very low
```

**Fix:**
1. Check Helius provider status
2. Check network stability
3. Check if Solana network is under stress
4. If persistent, contact Helius support

---

## Manual Recovery Steps

### Full Restart
```bash
# Stop the service (adjust command for your setup)
pkill -f price_worker

# Wait 5 seconds
sleep 5

# Start the service
python3 src/core/main.py

# Monitor logs
tail -f logs/dev_intelligence.log | grep "Pool\|WebSocket"
```

### Force Fallback Polling
If WebSocket is completely broken:

```bash
# Set env var to empty (disables WS startup)
export HELIUS_WS_URL=""

# Restart
pkill -f price_worker
sleep 5
python3 src/core/main.py

# System will revert to RPC polling every 60s
# All prices still work, just higher RPC cost
```

### Check Pool Registrations
```bash
curl -s http://localhost:5002/api/price/health | jq '.pool_stats.pools_registered'
# If 0: no pools registered. Use /api/price/pool/register to add them.
```

---

## Performance Baseline

### Expected Stats (Normal Operation)

```json
{
  "connected": true,
  "subscriptions": 10,           // 2 × pools_registered
  "events_received": 500,        // Depends on trading volume
  "events_deduplicated": 50,     // ~10% of events (okay)
  "events_decoded": 450,         // ~90% of events (okay)
  "reconnects": 0,               // Should be zero
  "last_event_at": 1710350000,   // Recent timestamp
  "is_stale": false              // Must be false
}
```

### Normal RPC Usage

- **With healthy WS:** ~60 calls/hour
- **With WS stale:** ~120 calls/hour
- **WS disabled:** ~360 calls/hour
- **Before upgrade:** ~360 calls/hour

### Expected Latency

- Pool price update: <200ms after on-chain swap
- RPC fallback poll: every 60s (or 30s if stale)
- `/api/price/MINT` response: <1ms

---

## Commands Reference

```bash
# Health check
curl http://localhost:5002/api/price/health | jq '.pool_stats.ws'

# Check all pool prices
curl http://localhost:5002/api/price/health | jq '.pool_stats'

# Register a new pool
curl -X POST http://localhost:5002/api/price/pool/register \
  -H 'Content-Type: application/json' \
  -d '{
    "pool_accounts": [{
      "mint": "EPjFWaLb...",
      "base_account": "...",
      "quote_account": "...",
      "base_decimals": 6,
      "quote_decimals": 9
    }]
  }'

# Check price for a token
curl http://localhost:5002/api/price/MINT | jq '{source, price_usd}'

# View logs
tail -f logs/dev_intelligence.log | grep "Pool\|WebSocket"

# Count WS reconnections
grep "Pool WebSocket reconnecting" logs/dev_intelligence.log | wc -l

# Check for stale pools
grep "Marked.*pools as stale" logs/dev_intelligence.log
```

---

## Decision Tree

```
ws.connected == false?
  ├─ YES: Check HELIUS_WS_URL, network, restart
  └─ NO: Continue

ws.is_stale == true?
  ├─ YES: Check Helius status, network. System using fallback. OK.
  └─ NO: Continue

events_received increasing?
  ├─ YES: System healthy. No action.
  ├─ NO: May be low trading volume or issues.
  │     Check: pool registrations, pool still active on-chain
  └─ Continue

reconnects > 3?
  ├─ YES: Network/provider issues. Escalate to Helius.
  └─ NO: System healthy.

pool_prices_fetched_last_cycle == 0?
  ├─ YES: Fallback poll failing. Check RPC endpoint, restart.
  └─ NO: System healthy. No action.
```

---

## Escalation Path

| Issue | First Response | If Persists |
|-------|---|---|
| `connected: false` | Restart | Check Helius status |
| `is_stale: true` | Wait 2 min | Investigate Helius/network |
| High reconnects | Check status page | Contact Helius support |
| Events dropped | Check trading volume | Check Solana RPC status |
| Prices wrong | Verify pool registration | Check pool still active |

---

## SLA / Expected Availability

- **WS uptime:** 99%+ (managed by Helius)
- **Fallback polling:** 99.9%+ (RPC always available)
- **Overall price availability:** 99.9%+ (pool + fallback chain)

If WS down but RPC working:
- Pool prices: 60s stale (fallback poll running)
- Other sources: Normal (Dexscreener, Jupiter, Birdeye)

---

## Contact Info

**For WebSocket issues:**
- Check: https://status.helius.dev
- Support: https://docs.helius.dev/support

**For Solana network issues:**
- Check: https://status.solana.com
- Explorer: https://solscan.io

**Internal escalation:**
- Price system owner: [Team slack]
- On-call: [Pagerduty/rotation]

---

**Last updated:** March 14, 2026
**Next review:** March 30, 2026 (after 2 weeks production)
