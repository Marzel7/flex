# Integrated Test: Curve Listener + Pre-Migration Analyzer

Comprehensive test suite that runs the pump.fun curve listener and automatically analyzes detected tokens with pre-migration rug risk metrics.

## Overview

This integrated test combines:
1. **PumpFunCurveListener** - Real-time detection of pump.fun bonding curves
2. **PumpFunPreMigrationAnalyzer** - 14-metric pre-migration rug risk analysis
3. **TokenAnalysisQuery** - Database query and reporting tool

## Quick Start

### Run Listener for Fixed Duration
```bash
python3 test_curve_listener_integration.py --duration 60
```

Runs for 60 seconds, detecting tokens and analyzing them automatically.

### Run Listener Indefinitely
```bash
python3 test_curve_listener_integration.py
```

Press Ctrl+C to stop. Shows analysis results at the end.

### Query Existing Results (No Listening)
```bash
python3 test_curve_listener_integration.py --query-only
```

Shows all previously analyzed tokens without starting the listener.

## Usage Examples

### Example 1: Quick Test (60 seconds)
```bash
$ python3 test_curve_listener_integration.py --duration 60

================================================================================
  🚀 STARTING CURVE LISTENER
================================================================================

[LISTENER] Monitoring pump.fun bonding curves...
[LISTENER] Market cap range: $50k - $80k USD
[LISTENER] Analysis: Pre-migration rug risk (14 metrics)
[LISTENER] Running for 60 seconds...

[FETCH] 📡 Found 20 recent Pump.Fun transactions
[FILTER] ❌ Market cap $6,717 < $50000 USD - SKIPPED
[FILTER] ✅ Market cap $54,856 within target range - PROCEEDING
[DB] ✅ Stored token (market data)
[ANALYZER] 🔍 Analyzing token...
[ANALYZER] 🟡 MEDIUM RISK | Score: 59.20%
[DB] ✅ Stored analysis for token
[ANALYZER] 🟢 LOW RISK | Score: 12.50%
[DB] ✅ Stored analysis for token

... (continues for 60 seconds)

[LISTENER] Duration 60s reached

================================================================================
  📊 QUERYING ANALYSIS RESULTS
================================================================================

Found 3 analyzed tokens
...
```

### Example 2: Query Previously Analyzed Tokens
```bash
$ python3 test_curve_listener_integration.py --query-only

================================================================================
  📊 QUERY-ONLY MODE
================================================================================

================================================================================
  📊 QUERYING ANALYSIS RESULTS
================================================================================

Found 8 analyzed tokens

MINT                                        RISK         RUG %    ANALYZED
DPGsga4z7jKJqrhSeyLi3cgbXoSoK5iKMtWp7gk1... 🟡 MEDIUM    59.2%    10:45:32
5GabsSPpAwouAwtoJfXKbzQeyFUX4PUeLdFdTjxz... 🟢 LOW       15.3%    10:42:10
JKqrhSeyLi3cgbXoSoK5iKMtWp7gk1pumpXYZ123... ☠️ CRITICAL   87.4%    10:38:45
...
```

### Example 3: Continuous Monitoring
```bash
$ python3 test_curve_listener_integration.py

# Ctrl+C to stop

[STATUS] Elapsed: 120s
[STATUS] Detected mints: 25
[STATUS] Filtered mints: 3
[STATUS] Analyzed tokens: 3

... (shows analysis results)
```

## Output Format

### During Listening
```
[FETCH] 📡 Found 20 recent Pump.Fun transactions
[EVENT] 📍 Detected token: <MINT>
[FILTER] ✅ Market cap $XX,XXX within target range - PROCEEDING
[DB] ✅ Stored <MINT> ($XX,XXX)
[ANALYZER] 🔍 Analyzing <MINT>
[PRE-MIGRATION] ✅ Parsed 208 events
[ANALYZER] 🟡 MEDIUM RISK | Score: 59.20% | <MINT>
[DB] ✅ Stored analysis for <MINT>
```

### Query Results Table
```
MINT                                        RISK         RUG %    ANALYZED
5GabsSPpAwouAwtoJfXKbzQeyFUX4PUeLdFdTjxz... 🟢 LOW       15.3%    10:42:10
DPGsga4z7jKJqrhSeyLi3cgbXoSoK5iKMtWp7gk1... 🟡 MEDIUM    59.2%    10:45:32
JKqrhSeyLi3cgbXoSoK5iKMtWp7gk1pumpXYZ123... ☠️ CRITICAL   87.4%    10:38:45
```

### Detailed Analysis
```
================================================================================
📊 TOKEN: 5GabsSPpAwouAwtoJfXKbzQeyFUX4PUeLdFdTjxzpump
================================================================================

⏰ Analyzed: 2026-01-09 10:42:10

🔴 RISK ASSESSMENT:
  • AMM Risk Level: 🟢 LOW
  • Rug Probability: 15.30%

💰 BONDING CURVE METRICS (208 events):
  • Mint Concentration: 0.180
  • Unique Minters Ratio: 0.840
  • Sell Suppression: 0.220
  • Creator Activity Ratio: 0.380

📈 ACTIVITY METRICS:
  • Mint Velocity: 1.23 mints/sec
  • Buy Size Variance: 890
  • Sell Volume Concentration: 0.150

🎯 PURCHASE STRATEGY FOR 5GabsSPpAwouAwtoJfXKb...
================================================================================
✅ SAFE - Low rug probability (15.3%)
   Recommendation: SAFE TO BUY during AMM migration

📌 KEY FACTORS:
   ✅ Low mint concentration (0.18) - Well distributed supply
   ✅ High unique minters (0.84) - Great participation
   ✅ Normal sell activity (0.22) - No suppression
```

### Summary Statistics
```
================================================================================
  📈 ANALYSIS SUMMARY
================================================================================

Risk Distribution:
  🟢 Low Risk (<=25%):     3 tokens
  🟡 Medium Risk (25-50%): 2 tokens
  🔴 High Risk (50-75%):   2 tokens
  ☠️  Critical (>75%):     1 token

Average Metrics:
  • Rug Probability: 45.2%
  • Mint Concentration: 0.512
  • Unique Minters: 0.418
  • Events Analyzed: 165

Extreme Cases:
  ✅ Safest: 5GabsSPpAwouAwtoJfXKb... (15.3% rug)
  ☠️  Riskiest: JKqrhSeyLi3cgbXoSoK... (87.4% rug)
```

## Command-Line Options

```bash
python3 test_curve_listener_integration.py [OPTIONS]

OPTIONS:
  --duration SECONDS    Run listener for specified seconds (default: indefinite)
                       Example: --duration 300 (run for 5 minutes)

  --query-only         Query existing analysis only (no listening)
                       Example: --query-only

  -h, --help           Show this help message and exit
```

## Test Workflow

```
┌─────────────────────────────────────────────────────┐
│ 1. LISTENER STARTED                                 │
│    python3 test_curve_listener_integration.py       │
└────────────────┬──────────────────────────────────┘
                 │
┌────────────────▼──────────────────────────────────┐
│ 2. MONITOR BONDING CURVES (every 5 seconds)        │
│    • Fetch recent Pump.Fun transactions            │
│    • Extract token mints from transaction logs     │
└────────────────┬──────────────────────────────────┘
                 │
┌────────────────▼──────────────────────────────────┐
│ 3. FILTER BY MARKET CAP ($50k-$80k USD)            │
│    • Low cap (<$50k): Skip                         │
│    • Mid cap ($50k-$80k): Proceed                  │
│    • High cap (>$80k): Already migrated, skip      │
└────────────────┬──────────────────────────────────┘
                 │
┌────────────────▼──────────────────────────────────┐
│ 4. ANALYZE WITH 14 METRICS                         │
│    • Fetch 200 bonding curve transactions          │
│    • Calculate rug probability and risk level      │
│    • Store all metrics in database                 │
└────────────────┬──────────────────────────────────┘
                 │
┌────────────────▼──────────────────────────────────┐
│ 5. DISPLAY & SUMMARIZE RESULTS                     │
│    • Show all analyzed tokens                      │
│    • Risk distribution statistics                  │
│    • Purchase strategy recommendations             │
└─────────────────────────────────────────────────────┘
```

## Integration with main.py

The test uses the same components as main.py:

1. **pumpfun_curve_listener.py** - Standalone listener
2. **pump_fun_pre_migration_analyzer.py** - Analysis engine
3. **query_token_analysis.py** - Query tool
4. **pumpswap_tokens.db** - Shared SQLite database

All tests update the same database, so results persist across runs.

## Database

Results are stored in `pumpswap_tokens.db`:

**Tables Used:**
- `curve_completions` - Basic token detection data
- `token_analysis` - 14-metric analysis results

**Query from Python:**
```python
from query_token_analysis import TokenAnalysisQuery

query = TokenAnalysisQuery()
tokens = query.get_all_analysis()
print(f"Found {len(tokens)} analyzed tokens")
```

## Performance

Typical performance on mainnet:

- **Token Detection**: 1 per 2-5 minutes (depends on market cap range)
- **Market Cap Fetch**: 2-3 seconds per token
- **Analysis**: 10-30 seconds per token (depends on transaction volume)
- **Database Store**: <100ms per record

Total latency from detection to stored analysis: 15-60 seconds

## Testing Strategies

### Strategy 1: Quick Validation (5 minutes)
```bash
python3 test_curve_listener_integration.py --duration 300
```
Typical results: 5-10 tokens detected, 2-5 analyzed

### Strategy 2: Extended Monitoring (30 minutes)
```bash
python3 test_curve_listener_integration.py --duration 1800
```
Typical results: 20-40 tokens detected, 10-20 analyzed

### Strategy 3: Continuous with Manual Stop
```bash
python3 test_curve_listener_integration.py
# ... wait for results ...
# Ctrl+C to stop
```

### Strategy 4: Query Previously Analyzed
```bash
python3 test_curve_listener_integration.py --query-only
```
Shows all results without new listening.

## Troubleshooting

### No tokens detected
- **Cause**: No pump.fun activity in market cap range during test period
- **Solution**: Run for longer duration (300+ seconds)
- **Alternative**: Check if your RPC endpoint is working

### Analysis takes a long time
- **Cause**: Slow RPC endpoint or rate limiting
- **Solution**: Use Helius API key for better performance
- **Check**: `HELIUS_API_KEY` is set in `.env`

### Database is locked error
- **Cause**: Multiple processes writing simultaneously
- **Solution**: Already fixed with WAL mode, wait for operation to complete

### ImportError: cannot import name 'PumpFunCurveListener'
- **Cause**: Running from wrong directory
- **Solution**: Run from project root: `cd /path/to/flex && python3 test_curve_listener_integration.py`

## Files Used

- **test_curve_listener_integration.py** - This test file
- **pumpfun_curve_listener.py** - Listener implementation
- **pump_fun_pre_migration_analyzer.py** - Analysis engine
- **query_token_analysis.py** - Query tool
- **pumpswap_tokens.db** - Database (created automatically)

## Related Documentation

- **PURCHASE_STRATEGY_GUIDE.md** - Full workflow guide
- **TOKEN_ANALYSIS_STORAGE.md** - Analysis metrics explained
- **PUMPFUN_LISTENER_STATUS.md** - Listener overview
- **PUMPFUN_DATABASE_FIX.md** - Database implementation details

## Status

✅ **READY FOR TESTING** - Integrated test suite complete and functional
