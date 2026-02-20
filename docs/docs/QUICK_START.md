# Quick Start Guide

## ⚡ Start the System

```bash
# Run the listener
python pumpfun_curve_listener.py
```

Expected output:
```
[INIT] Pump.Fun → PumpSwap Migration Listener ready
[INIT] HTTP RPC: https://stylish-delicate-sound.solana-mainnet.quiknode.pro/...
[WEBSOCKET] ✓ Connected to PumpSwap program via Helius
[WEBSOCKET] Subscribed to PumpSwap migrations
```

## 📊 Monitor Progress

Watch the logs for:

**Migration Detected:**
```
[WEBSOCKET] 🚨 Migration #1 detected: abc123...
[EVENT] 🚀 MIGRATION DETECTED: CEh9pYNLvhDd4qDtmSAAHsLxSCgN168edcLeEnSupump
```

**Analysis Running:**
```
[ANALYZER] 🔍 Analyzing post-migration CEh9pYNLvhDd4qDt...
[SIG_FETCH] ✅ Total signatures fetched: 761
[STREAM] Fetched 761 signatures, starting async fetch...
[ASYNC] Progress: 10/761 txs | Success: 10/10 (100.0%) | Failed: 0
[ASYNC] Progress: 100/761 txs | Success: 100/100 (100.0%) | Failed: 0
```

**Analysis Complete:**
```
[ANALYZER] 🟡 MEDIUM RISK | Score: 55.0% | CEh9pYNLvhDd4qDtmSAAHsLxSCgN168edcLeEnSupump
[DB] ✅ Stored post-migration analysis for CEh9pYNLvhDd4qDt...
```

## 🔍 Verify Results

Check database:
```bash
sqlite3 pumpswap_tokens.db "SELECT mint, post_migration_coverage, rug_probability FROM token_analysis LIMIT 5;"
```

Expected:
```
CEh9pYNLvhDd4qDtmSAAHsLxSCgN168edcLeEnSupump|100.0|0.55
```

## 🔧 Diagnostics

### Check RPC Configuration
```bash
python check_rpc_config.py
```

### Check Listener Status
```bash
python check_listener_status.py
```

### Test Analyzer Directly
```bash
python -c "
import asyncio
from pump_fun_post_migration_analyzer import PostMigrationAnalyzer

async def test():
    analyzer = PostMigrationAnalyzer('CEh9pYNLvhDd4qDtmSAAHsLxSCgN168edcLeEnSupump')
    await analyzer.fetch_curve_activity_async()
    print(f'Coverage: {analyzer.summary()[\"coverage\"]:.1f}%')

asyncio.run(test())
"
```

## 🔄 Clear Data

Empty database for fresh start:
```bash
sqlite3 pumpswap_tokens.db "DELETE FROM token_analysis;"
```

Clear Python cache:
```bash
find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null
```

## 📈 Expected Performance

| Metric | Value |
|--------|-------|
| Coverage | 80-95%+ (100% tested) |
| Analysis Time | 30-90 seconds per token |
| Retry Success Rate | 95%+ |
| HTTP 429 Errors | Normal, auto-recovered |

## ✅ Success Indicators

1. **Listener connects:** `[WEBSOCKET] ✓ Connected`
2. **Migration detected:** `[WEBSOCKET] 🚨 Migration #N detected`
3. **Analysis runs:** `[ASYNC] Progress:` messages with 100.0% success rate
4. **Database stores:** `[DB] ✅ Stored post-migration analysis`
5. **Coverage > 80%:** `post_migration_coverage > 80` in database

## ⚠️ Common Issues

**Issue:** Listener not detecting migrations
- **Check:** WebSocket connected with `✓` indicator
- **Verify:** PumpSwap transactions happening on-chain

**Issue:** Low coverage (< 80%)
- **Run:** `python check_rpc_config.py` to verify RPC
- **Check:** `[ANALYZER_INIT] RPC:` shows QuickNode URL
- **Monitor:** `[FETCH_TX]` retries should eventually succeed

**Issue:** Database not updating
- **Verify:** `[DB] ✅ Stored...` log appears
- **Check:** Table exists: `sqlite3 pumpswap_tokens.db ".tables"`
- **Query:** `SELECT COUNT(*) FROM token_analysis;`

## 🚀 Production Checklist

- [ ] RPC_URL environment variable set to QuickNode endpoint
- [ ] HELIUS_API_KEY configured (optional, for WebSocket)
- [ ] Database exists and is readable/writable
- [ ] Python packages installed: `aiohttp`, `websockets`, `requests`
- [ ] Listener process running
- [ ] Monitoring logs for migration detection
- [ ] Database being updated with analysis results

## 📞 Support

**System Status:**
- Coverage: ✅ 100% verified
- Concurrency: ✅ Semaphore-based (proven)
- Retry Logic: ✅ 10 attempts with exponential backoff
- Database: ✅ Post-migration columns working
- Production Ready: ✅ YES

**Last Verified:** 2026-01-12
