# Price Freshness - Quick Reference Card

## TL;DR
**Are prices automatically updated?** YES ✓
**How fresh are they?** Very fresh (30s-5min) ✓
**How do I check?** Run: `python get_price_from_pools.py <MINT>`

---

## Quick Check (30 seconds)

### 1. Run This
```bash
python get_price_from_pools.py 47bXryb6KGkF4kTGmveUAzFfigHSSzRkZi3ibtjhUbJY
```

### 2. Look For This Line
```
Price Status: Updated 30s ago ✓ (fresh)
```

That's it! Price is automatically updated every 30 seconds to 5 minutes.

---

## Status Indicators

| Status | Meaning | What To Do |
|--------|---------|-----------|
| `Updated 30s ago ✓ (fresh)` | Price is very current | Use it! |
| `Updated 2m ago ✓ (fresh)` | Price is still current | Use it! |
| `Updated 5m ago ~ (ok)` | Price is okay | Still reliable |
| `Updated 30m ago ~ (moderate)` | Price is a bit old | Check if needed |
| `Updated 1h ago ⚠ (stale)` | Price might be old | Wait for update or check live |

---

## What's Happening in Background

**When `python main.py` runs:**

```
Every 10 seconds:
  ├─ Check which pools need price updates
  └─ For each pool needing update:
     ├─ Fetch live price from blockchain
     ├─ Get token supply
     ├─ Calculate market cap
     ├─ Fetch from DexScreener API
     └─ Store in database with timestamp
```

**Update Frequency:**
- **New pools (0-5 min old)**: Every 30 seconds
- **Medium pools (5-30 min old)**: Every 2 minutes
- **Old pools (30+ min old)**: Every 5 minutes

---

## Verify It's Working

### Method 1: Watch the Logs
```bash
python main.py
```

Look for lines like:
```
[PRICE UPDATER] === Cycle 42 ===
[PRICE UPDATER] Found 3 pool(s) needing update
[PRICE UPDATER] ✓ Updated 5wD5oj...: $0.00000061
[DEXSCREENER] Updated for 5wD5oj...: $0.000000615
```

### Method 2: Check Timestamps
```bash
python get_price_from_pools.py <MINT>
```

Compare these two runs 60 seconds apart - timestamp should change:
```
Run 1: Updated 2m ago
Run 2: Updated 1m ago  ← Changed!
```

### Method 3: Database Query
```bash
python -c "
import sqlite3
from datetime import datetime
conn = sqlite3.connect('pumpswap_tokens.db')
cursor = conn.cursor()
cursor.execute('SELECT symbol, last_price_update FROM pools LIMIT 3')
for symbol, last_update in cursor.fetchall():
    if last_update:
        age = (datetime.now() - datetime.fromisoformat(last_update)).total_seconds()
        print(f'{symbol}: {int(age)}s ago')
"
```

---

## Files to Know About

| File | Purpose | Run It For |
|------|---------|-----------|
| `main.py` | Keeps prices fresh | Automatic background updates |
| `get_price_from_pools.py` | Check token prices | Freshness status, all PumpSwap tokens |
| `get_price_live_with_balances.py` | Show vault data | Advanced price lookup with vault info |
| `PRICE_FRESHNESS_GUIDE.md` | Full documentation | Deep understanding of the system |

---

## Common Questions

### Q: Is my token price stale?
**A:** Check the status line:
```
Price Status: Updated 30s ago ✓ (fresh)  ← Fresh!
Price Status: Updated 2h ago ⚠ (stale)   ← Stale!
```

### Q: How often are prices updated?
**A:** It depends on the token's age:
- New (0-5 min): Every 30 seconds
- Medium (5-30 min): Every 2 minutes
- Old (30+ min): Every 5 minutes

### Q: Why is my price showing as stale?
**A:** Either:
1. The background updater (`main.py`) isn't running
2. The pool is older than 5 minutes and is on the slower update cycle

**Solution:** Run `python main.py` in the background

### Q: Can I update a specific token right now?
**A:** The background updater handles it automatically. If you need it sooner:
1. Stop `python main.py`
2. Restart it to trigger immediate checks

### Q: What if prices differ from DEXScreener?
**A:** We fetch from both:
- **Our price** = Calculated from vault balances
- **DexScreener price** = External source for comparison

Usually match within 1-2% due to network timing.

---

## One-Liner Status Check

```bash
python -c "import sqlite3, json; conn = sqlite3.connect('pumpswap_tokens.db'); [print(f\"{dict(conn.execute('SELECT symbol, last_price_update FROM pools WHERE symbol IS NOT NULL AND symbol != \\\"N/A\\\"').fetchall()[i])}\" ) for i in range(min(3, len(conn.execute('SELECT * FROM pools').fetchall())))]" 2>/dev/null || echo "No database"
```

Or simpler:
```bash
python get_price_from_pools.py | grep "Price Status:"
```

---

## Setup (First Time Only)

1. **Start the monitor:**
   ```bash
   python main.py &
   ```

2. **Wait for first prices:**
   ```bash
   # Takes ~30 seconds for new pools
   # Takes ~5 minutes for all pools
   ```

3. **Check prices:**
   ```bash
   python get_price_from_pools.py
   ```

That's it! Prices are now automatically updated.

---

## Troubleshooting

| Problem | Cause | Solution |
|---------|-------|----------|
| All prices show "Never updated" | `main.py` not running | Run `python main.py` |
| Prices showing "stale" | Update cycle hasn't run | Wait 30-300s depending on age |
| No tokens in database | No pools detected yet | Run `python main.py` and wait |
| Different from DEXScreener | Network timing difference | Usually within 1-2%, acceptable |

---

## What's Under the Hood

**Database Timestamps:**
- `first_seen`: When pool was detected (never changes)
- `last_price_update`: When price was last refreshed (updates constantly)

**Age Calculation:**
```
age = now - first_seen
```

**Update Logic:**
```
if (now - last_price_update) >= update_interval:
    update the price
    set last_price_update = now
```

---

## Key Insight

The system is designed to:
1. ✓ Update frequently when prices are volatile (new pools)
2. ✓ Update less often when prices stabilize (old pools)
3. ✓ Track timestamps so you always know freshness
4. ✓ Fetch from multiple sources for comparison
5. ✓ Store everything locally (no API limit issues)

---

## Next Steps

1. **For daily use:**
   ```bash
   # Terminal 1: Run monitor
   python main.py

   # Terminal 2: Check prices anytime
   python get_price_from_pools.py <MINT>
   ```

2. **For scripting:**
   ```bash
   # Get JSON output for automation
   python -c "
   import sqlite3, json
   conn = sqlite3.connect('pumpswap_tokens.db')
   cursor = conn.cursor()
   cursor.execute('SELECT symbol, dexscreener_price_usd, last_price_update FROM pools')
   for row in cursor.fetchall():
       print(json.dumps({'symbol': row[0], 'price_usd': row[1], 'last_update': row[2]}))
   "
   ```

---

**Bottom Line:** Prices are fresh, automatically updated, and you can always check freshness by running `get_price_from_pools.py` ✓

