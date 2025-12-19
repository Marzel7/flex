# Get Started - Liquidity Removal Detection

## TL;DR (30 seconds)

```bash
# 1. Find a Meteora pool address

# 2. Establish baseline price
python establish_baseline_price.py <POOL_ADDRESS>

# 3. Monitor for liquidity removal
python test_liquidity_monitoring.py <POOL_ADDRESS>

# Done! Monitor will alert you when liquidity is removed
```

## What You Get

A system that **automatically detects when token creators remove liquidity** from their pools by monitoring on-chain prices.

**Liquidity Removal = Price Drops >50% = We Detect It ✓**

## Step 1: Get Pool Address

You already have one: `79EoTzCQjcrTFnxjRzFg2zABUy2keGsPSZd8qLKF4RYM`

Or find new pools from:
- Running `python main.py` (real-time detection)
- DexScreener API
- Solscan or other Solana explorers

## Step 2: Establish Baseline (First Time Only)

```bash
python establish_baseline_price.py 79EoTzCQjcrTFnxjRzFg2zABUy2keGsPSZd8qLKF4RYM
```

This will:
1. Take ~5 price samples
2. Calculate average as baseline
3. Show price stability
4. Save baseline to file

**Output:**
```
================================================================================
BASELINE PRICE SUMMARY
================================================================================

Baseline Price:  $0.000000000321961972 SOL/token
Stability:       VERY HIGH (Stable pool)

Detection Thresholds:
  50% drop alert:  $0.000000000160980986
  25% drop alert:  $0.000000000241471479
```

## Step 3: Monitor for Removal

```bash
python test_liquidity_monitoring.py 79EoTzCQjcrTFnxjRzFg2zABUy2keGsPSZd8qLKF4RYM
```

This will:
1. Check price every 30 seconds (default)
2. Compare with previous check
3. Alert if price changes >50%
4. Log all events to JSON file

**Output:**
```
[2025-12-19 16:38:04] Check #1... $0.000000000321961972
[2025-12-19 16:38:34] Check #2... $0.000000000321961972
[2025-12-19 16:39:04] Check #3... $0.000000000160980986

🔴 CRITICAL - Price DROPPED 50.0% ($0.000000000321961972 -> $0.000000000160980986)
  Time: 2025-12-19T16:39:04.123456

================================================================================
MONITORING SUMMARY
================================================================================
Total checks: 3
Total events: 1 detected

Detected Events:
  1. [CRITICAL] LIQUIDITY_REMOVAL - Price DROPPED 50.0% (...)
```

## Quick Reference

### Check Current Price (Anytime)
```bash
python meteora_price_fetcher_v2.py <POOL>
```

### Monitor for 1 Hour (Every 30 seconds)
```bash
python test_liquidity_monitoring.py <POOL>
```

### Monitor for 10 Minutes (Every 15 seconds)
```bash
python test_liquidity_monitoring.py <POOL> 15 40
```

### Monitor All Night (Every 5 minutes)
```bash
nohup python test_liquidity_monitoring.py <POOL> 300 > monitor.log 2>&1 &
```

### Compare Against Baseline
```bash
python establish_baseline_price.py <POOL>
# Shows current price vs baseline if baseline exists
```

## Understanding Results

### Event Severity

```
🔴 CRITICAL    >90% drop   = Pool definitely drained
🟠 HIGH        50-90% drop = Serious liquidity removal
🟡 MEDIUM      20-50% drop = Notable event
🟢 LOW         10-20% drop = Minor change
```

### What Prices Mean

```
1.23e-10     = Extremely small (token worth almost nothing)
0.00001      = Very small
0.001        = Small
0.1          = Moderate
1.0          = Normal
100+         = Very valuable
```

### Files Generated

Each run creates:
- `baseline_price_79EoTzCQ.json` - Baseline data
- `liquidity_events_79EoTzCQ.json` - Event log

**Example:**
```json
{
  "pool_address": "79EoTzCQjcrTFnxjRzFg2zABUy2keGsPSZd8qLKF4RYM",
  "updated_at": "2025-12-19T16:40:29.930943",
  "price_history": [...],
  "events": [
    {
      "timestamp": "2025-12-19T16:39:04.123456",
      "event_type": "LIQUIDITY_REMOVAL",
      "severity": "CRITICAL",
      "change_pct": 50.0,
      "message": "Price DROPPED 50.0% ($0.001 -> $0.0005)"
    }
  ]
}
```

## Common Tasks

### Task 1: Monitor New Launch
```bash
# Immediately after launch
python establish_baseline_price.py POOL
python test_liquidity_monitoring.py POOL 10 600  # 10s checks, 10 minutes
```

### Task 2: Check if Pool is Rugged
```bash
python establish_baseline_price.py POOL
# If baseline exists, compares current price vs baseline
# Price 0.05x baseline = 95% drop = RUG PULL
```

### Task 3: Continuous Overnight Watch
```bash
# Run in background
nohup python test_liquidity_monitoring.py POOL 60 > watch.log 2>&1 &

# Check next morning
cat liquidity_events_POOL*.json | grep CRITICAL
```

### Task 4: Alert on Removal
```bash
# Monitor for 1 hour
python test_liquidity_monitoring.py POOL 30

# Check for critical events
if grep -q "CRITICAL" liquidity_events*.json; then
    echo "ALERT: Liquidity removed from pool $POOL"
    # Send notification (email, Telegram, Discord, etc.)
fi
```

## Customization

### Change How Often to Check
```bash
python test_liquidity_monitoring.py POOL 5      # Every 5 seconds
python test_liquidity_monitoring.py POOL 10     # Every 10 seconds
python test_liquidity_monitoring.py POOL 60     # Every 60 seconds
```

### Change How Long to Monitor
```bash
python test_liquidity_monitoring.py POOL 30 10   # 10 checks = 5 minutes
python test_liquidity_monitoring.py POOL 30 20   # 20 checks = 10 minutes
python test_liquidity_monitoring.py POOL 30 60   # 60 checks = 30 minutes
python test_liquidity_monitoring.py POOL 30 120  # 120 checks = 1 hour
```

### Both
```bash
python test_liquidity_monitoring.py POOL 15 240  # Every 15s, 1 hour total
python test_liquidity_monitoring.py POOL 60 480  # Every 60s, 8 hour overnight watch
```

## Troubleshooting

### "Failed to fetch price"
- Invalid pool address?
- RPC endpoint down?
- Pool doesn't have vaults?
- Try with a different pool to test

### "Price unchanged"
- Normal! Pool is stable
- Good sign (reliable baseline)

### "No events detected"
- Pool is stable (no removal)
- Or threshold (50%) is too high
- Try monitoring a different pool

### Want More Details?
See [MONITORING_QUICKSTART.md](MONITORING_QUICKSTART.md)
See [LIQUIDITY_MONITORING.md](LIQUIDITY_MONITORING.md)
See [PRICE_TRACKING_COMPLETE.md](PRICE_TRACKING_COMPLETE.md)

## How It Works (Simple)

```
Pool Creation:
  Creator provides tokens + SOL
  Price = token_balance / sol_balance

Creator Removes Liquidity:
  Withdraws tokens (or SOL)
  Vault balance decreases
  Price ratio changes
  WE DETECT THE CHANGE ✓

Detection:
  Check price every 30 seconds
  If drops >50% = CRITICAL alert
  Log to file for analysis
```

## Tips for Success

✓ **Monitor from start** - Establish baseline immediately
✓ **Use frequent checks** - Every 10-30 seconds works best
✓ **Continuous run** - Don't stop and restart, continuous is better
✓ **Save results** - JSON logs persist for analysis

## Next Steps

1. **Start monitoring**: `python establish_baseline_price.py <POOL>`
2. **Begin watch**: `python test_liquidity_monitoring.py <POOL>`
3. **Analyze results**: Check `liquidity_events_*.json`
4. **Integrate with bot**: Use JSON output in your trading logic

## Questions?

See full docs:
- [MONITORING_QUICKSTART.md](MONITORING_QUICKSTART.md) - Quick examples
- [LIQUIDITY_MONITORING.md](LIQUIDITY_MONITORING.md) - Technical details
- [PRICE_TRACKING_COMPLETE.md](PRICE_TRACKING_COMPLETE.md) - Complete guide

## Summary

You now have **production-ready tools** to:
- ✅ Monitor token prices on-chain
- ✅ Detect liquidity removal events
- ✅ Generate alerts and logs
- ✅ Track pool health over time

**Ready to start? Run:**
```bash
python establish_baseline_price.py 79EoTzCQjcrTFnxjRzFg2zABUy2keGsPSZd8qLKF4RYM
python test_liquidity_monitoring.py 79EoTzCQjcrTFnxjRzFg2zABUy2keGsPSZd8qLKF4RYM
```
