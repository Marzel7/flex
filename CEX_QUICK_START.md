# CEX Wallet Detection - Quick Start Guide

## What is it?

The CEX Wallet Detection system identifies when token creators receive funding from known centralized exchange wallets (Coinbase, Kraken, Binance, etc.), which indicates professional/organized operations and increases rug probability.

---

## Quick Commands

### View All CEX Wallets
```bash
python3 scripts/manage_cex_wallets.py --list
```

### Add a New CEX Wallet
```bash
python3 scripts/manage_cex_wallets.py --add <ADDRESS> <EXCHANGE> <TYPE> [CONFIDENCE] [SOURCE] [NOTES]

# Example: Add Kraken hot wallet (the one you asked about)
python3 scripts/manage_cex_wallets.py --add \
  6LY1JzAFVZsP2a2xKrtU6znQMQ5h4i7tocWdgrkZzkzF \
  Kraken \
  "Hot Wallet" \
  95 \
  "Solscan" \
  "Kraken hot wallet identified"
```

### Remove a CEX Wallet
```bash
python3 scripts/manage_cex_wallets.py --delete <ADDRESS>
```

---

## Using the REST API

### Get all CEX wallets
```bash
curl http://localhost:5002/api/cex-wallets | jq .
```

### Add a wallet via API
```bash
curl -X POST http://localhost:5002/api/cex-wallets \
  -H 'Content-Type: application/json' \
  -d '{
    "address": "YOUR_CEX_ADDRESS",
    "exchange": "Kraken",
    "type": "Hot Wallet",
    "confidence": 95,
    "source": "Solscan",
    "notes": "Known Kraken wallet"
  }'
```

### Delete a wallet via API
```bash
curl -X DELETE http://localhost:5002/api/cex-wallets \
  -H 'Content-Type: application/json' \
  -d '{"address": "YOUR_CEX_ADDRESS"}'
```

---

## Current Known Wallets

| Exchange | Type | Address | Confidence |
|----------|------|---------|-----------|
| Coinbase | Custody/Staking | DPq... | 100% |
| Coinbase | Hot Wallet | Gei... | 95% |
| Kraken | Hot Wallet | 6LY... | 95% |
| Binance | Hot Wallet | 98r... | 95% |

---

## How It Works in the Listener

When a new token migrates:

1. ✅ Creator address extracted
2. ✅ Pre-migration SOL transfers to creator identified
3. ✅ For each transfer source, check: Is this a known CEX wallet?
4. 🏛️ If CEX found → Flag as high-risk funding
5. 📊 Risk score increases (CEX-funded tokens = more likely to rug)

---

## Example Output

```
[CREATOR] ✅ Extracted from earliest tx: AY5kpQXdwEevDfQptjUtPhUVt4Cuv2NhmT3Vb9wJ41Sp
[CEX] 🏛️ COINBASE Hot Wallet funding this creator
[RISK] 🔴 CRITICAL - CEX funding + multiple sources detected
```

---

## Database Location

CEX wallets stored in: `pumpswap_tokens.db` → `cex_wallets` table

Query directly if needed:
```bash
sqlite3 pumpswap_tokens.db "SELECT * FROM cex_wallets WHERE is_active = 1;"
```

---

## Key Points

- **Kraken Hot Wallet** (6LY1JzAFVZsP2a2xKrtU6znQMQ5h4i7tocWdgrkZzkzF) - Already added ✅
- **Confidence levels**: 100% = verified official, 95% = highly likely, 90% = suspected
- **Soft delete**: Wallets are deactivated (is_active=0), not permanently deleted
- **Integration ready**: Function `check_if_cex_funding()` available in listener for risk scoring

---

## Next Steps

1. ✅ Kraken wallet is in the system (you already added it)
2. Add more CEX wallets as you identify them
3. Integrate into funding analysis risk scoring
4. Monitor for CEX-funded tokens in UI

---

**System Status**: ✅ Production Ready

For detailed docs, see: `docs/CEX_WALLET_DETECTION_IMPLEMENTATION.md`
