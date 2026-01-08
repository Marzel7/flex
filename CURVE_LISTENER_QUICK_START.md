# Pump.Fun Curve Listener - Quick Start

## Start the Listener

```bash
python3 pumpfun_curve_listener.py
```

## What to Expect

The listener will:
1. Connect to Helius WebSocket RPC (uses HELIUS_API_KEY from .env)
2. Subscribe to pump.fun program logs
3. Wait for bonding activity
4. Detect mints and fetch their market caps
5. Filter tokens by $50,000 USD market cap threshold
6. Poll for bonding curve completion every 10 seconds
7. Run pre-migration analysis when curve completes
8. Output risk level and rug probability

## Output Format

```
[LISTENER] Starting pump.fun curve completion listener...
[WS] ✅ Subscribed to bonding curve program logs
[EVENT] 📍 Detected bonding activity for <MINT>...
[FILTER] ✅ Market cap $X >= $50,000 - PROCEEDING
[EVENT] ⏳ Still waiting for curve completion (60s elapsed)...
[EVENT] 🔴 Bonding curve COMPLETE: <MINT>...
[ANALYZER] 🔍 Analyzing <MINT>...
[ANALYZER] 🟢 Low | Score: 12.5% | <MINT>...
```

## Status Codes

- 🟢 **Low**: 0-25% rug probability - Safe
- 🟡 **Medium**: 25-50% rug probability - Caution
- 🔴 **High**: 50-75% rug probability - Risky
- ☠️ **Critical**: 75-100% rug probability - Likely rug

## Configuration

**Market Cap Threshold:**
- Located in `pumpfun_curve_listener.py`
- Currently set to $50,000 USD
- Change line 38: `MARKET_CAP_THRESHOLD_USD = 50000`

**RPC Endpoint:**
- Uses HELIUS_API_KEY from `.env`
- Set in `.env` file: `HELIUS_API_KEY=your_key_here`
- Falls back to mainnet-beta if key not provided

## Testing

Run validation without WebSocket:
```bash
python3 test_curve_listener_validation.py
```

This tests:
- Market cap API (DexScreener)
- Configuration loading
- Listener initialization
- Filtering logic

## Key Features

✅ **Real-time detection** (~3-8 seconds after on-chain)
✅ **Market cap filtering** (>= $50k USD)
✅ **Automatic analysis** (when curve completes)
✅ **Risk scoring** (11-metric analysis)
✅ **Graceful shutdown** (Ctrl+C shows summary)

## Summary Output (on Ctrl+C)

```
[LISTENER] Summary:
  Total detected: 42
  Passed market cap filter: 8
  Curves completed: 3
  Curves analyzed: 3
```

## Troubleshooting

**No output/connection refused:**
- Check HELIUS_API_KEY in `.env`
- Verify Solana RPC is responding
- Check network connectivity

**Market cap shows $0:**
- Token not on DexScreener yet
- DexScreener API rate limit (5 second timeout)
- Token has no market cap data

**Curve never completes:**
- Still waiting (normal - can take hours)
- Token migrated (check explorer)
- Press Ctrl+C after expected completion time

**ImportError for dependencies:**
- Install: `pip install solders solana requests python-dotenv`

## Next Steps

1. **Persistent storage**: Modify to save analyzed tokens to database
2. **Alerts**: Add Discord/webhook notifications for high-risk tokens
3. **Tracking**: Monitor rug score trends over time
4. **Whale tracking**: Identify whales during curve phase

## Documentation

- **Full guide**: See `CURVE_LISTENER_README.md`
- **Code reference**: See `pumpfun_curve_listener.py` (233 lines)
- **Analyzer details**: See `pump_fun_pre_migration_analyzer.py`
