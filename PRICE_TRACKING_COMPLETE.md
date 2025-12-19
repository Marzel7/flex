# Price Tracking and Liquidity Monitoring - Complete System

## Overview

You now have a complete system to **track token prices on-chain** and **detect liquidity removal events** when creators drain their pools.

## The Problem

When a token is launched on Meteora:
1. Creator provides initial liquidity
2. Price is calculated from vault ratios
3. At some point, creator may remove liquidity (rug pull / exit scam)
4. This causes **vault balances to change** and **price to collapse**

**Solution**: Monitor on-chain prices and detect when significant changes occur.

## The Solution: Three Tools

### 1. **meteora_price_fetcher_v2.py** (Core)

Fetches current on-chain price from a pool.

```bash
python meteora_price_fetcher_v2.py <POOL_ADDRESS>
```

**What it does:**
- Reads vault balances from Solana blockchain
- Extracts vault pairs (SOL/token or token/token)
- Calculates price as vault ratio
- Detects depleted pools

**Output example:**
```
Pool: 79EoTzCQjcrTFnxjRzFg2zABUy2keGsPSZd8qLKF4RYM
On-chain price (spot):  0.000000000321961972 SOL
DexScreener data:       Not indexed (pool may be too new)
```

### 2. **establish_baseline_price.py** (Setup)

Establishes initial price baseline for a new pool.

```bash
python establish_baseline_price.py <POOL_ADDRESS>
```

**What it does:**
- Takes N price samples from the pool
- Calculates average price as baseline
- Assesses price stability
- Saves baseline file for comparison
- Generates alert thresholds

**Output example:**
```
Baseline Price:  $0.000000000321961972 SOL/token
Stability:       VERY HIGH (Stable pool)

Detection Thresholds:
  50% drop alert:  $0.000000000160980986
  25% drop alert:  $0.000000000241471479
  2x increase:     $0.000000000643923945
```

**Creates file:** `baseline_price_79EoTzCQ.json`

### 3. **test_liquidity_monitoring.py** (Monitor)

Continuously monitors a pool for liquidity removal events.

```bash
python test_liquidity_monitoring.py <POOL_ADDRESS>
```

**What it does:**
- Fetches price every N seconds (default: 30)
- Compares with previous price
- Detects >50% changes (liquidity removal signal)
- Logs events with severity levels
- Saves history to JSON file
- Displays summary statistics

**Output example:**
```
[2025-12-19 16:38:04] Check #1... $0.000000000321961972
[2025-12-19 16:38:34] Check #2... $0.000000000321961972

🔴 CRITICAL - Price DROPPED 85.0% ($0.001 -> $0.00015)

================================================================================
MONITORING SUMMARY
================================================================================
Total checks: 45
Total events: 1 detected
```

**Creates file:** `liquidity_events_79EoTzCQ.json`

## Complete Workflow

### For New Token Launch

```bash
# Step 1: Get pool address (from main.py or DexScreener)
POOL="79EoTzCQjcrTFnxjRzFg2zABUy2keGsPSZd8qLKF4RYM"

# Step 2: Establish baseline (5 samples)
python establish_baseline_price.py $POOL

# Step 3: Monitor continuously (every 15 seconds)
python test_liquidity_monitoring.py $POOL 15

# Step 4: Check results
cat liquidity_events_${POOL:0:8}.json
```

### For Existing Pool

```bash
# Check current price
python meteora_price_fetcher_v2.py $POOL

# Compare against baseline
python establish_baseline_price.py $POOL  # Shows comparison if baseline exists

# Monitor if concerned about liquidity
python test_liquidity_monitoring.py $POOL 30 20  # 10 minutes of monitoring
```

### Automated Alert System

```bash
#!/bin/bash

POOL="79EoTzCQjcrTFnxjRzFg2zABUy2keGsPSZd8qLKF4RYM"

# Monitor for 1 hour with frequent checks
python test_liquidity_monitoring.py $POOL 15 240

# Parse results
CRITICAL=$(grep "CRITICAL" liquidity_events_${POOL:0:8}.json | wc -l)

if [ $CRITICAL -gt 0 ]; then
    echo "⚠️  LIQUIDITY REMOVAL DETECTED!"
    # Send alerts
    # telegram_alert "Pool $POOL: Liquidity removed!"
    # discord_webhook "Liquidity removal detected in pool"
fi
```

## Data Files Generated

### Baseline File
`baseline_price_79EoTzCQ.json`
- Established initial price
- Pool stability metrics
- Detection thresholds
- Time of establishment

### Monitoring Log
`liquidity_events_79EoTzCQ.json`
- Complete price history
- All detected events with timestamps
- Event severity levels
- Last update time

## Understanding the Output

### Price Stability

```
Variance: 0.00%     -> Pool is VERY STABLE (excellent for detection)
Variance: 1-5%      -> Pool is STABLE (good for detection)
Variance: 5-10%     -> Pool is MODERATE (acceptable for detection)
Variance: 10-20%    -> Pool is VOLATILE (may have false positives)
Variance: >20%      -> Pool is HIGHLY VOLATILE (unreliable)
```

### Event Severity

```
🔴 CRITICAL   >90% price change   -> Pool definitely drained
🟠 HIGH       50-90% change       -> Significant liquidity removal
🟡 MEDIUM     20-50% change       -> Notable event
🟢 LOW        10-20% change       -> Minor adjustment
```

### Price Format

All prices shown in **SOL per token**:
- `1.23e-10` = Extremely small (saturated/worthless token)
- `0.001` = Small/new token
- `0.1` = Moderate price
- `1.0` = Common price
- `100+` = Extremely valuable token

## Technical Accuracy

### When It Works Well ✅
- Simple constant-product pools (Raydium DAMM style)
- Raydium V4, CPMM pools
- Early Meteora DLMM with fewer bins
- Pools with 2-8 vaults

### When It's Approximate ⚠️
- Meteora DLMM with many vaults (16+ vaults)
- Complex multi-token pools
- Pools with imbalanced vault ratios

### Why It Works
```
For simple pools:
  price = vault_token_balance / vault_sol_balance

When liquidity removed:
  vault_token_balance ↓ (or vault_sol_balance ↓)
  price ratio changes → we detect it
```

### Limitations
1. **Doesn't work for true bin-based pricing** (Meteora's active_id)
2. **Can miss slow bleeds** (small changes per check)
3. **Needs baseline** (can't detect removal before monitoring started)
4. **Time-dependent** (needs frequent checks for accuracy)

## Integration with Main App

These tools can be integrated into `main.py` to:

```python
# In RaydiumMonitor or RaydiumDatabase:

# 1. Auto-establish baseline on new pool detection
monitor.establish_baseline_price(new_pool_address)

# 2. Continuous monitoring in background
monitor.start_liquidity_monitoring(pools_to_monitor)

# 3. Alert on removal events
monitor.on_liquidity_removal_detected(callback=notify_user)

# 4. Store baseline and events in database
db.save_baseline(pool_address, baseline_data)
db.save_liquidity_event(pool_address, event_data)
```

## Common Scenarios

### Scenario 1: Monitor Launch at T=0
```bash
# Token just launched, monitor immediately with frequent checks
python establish_baseline_price.py $POOL
python test_liquidity_monitoring.py $POOL 10 600  # 10s interval, 10 minutes
```

### Scenario 2: Check Suspected Rug Pull
```bash
# Token looks suspicious, check if liquidity was removed
python establish_baseline_price.py $POOL  # Shows comparison

# If baseline exists:
# - "Price is 0.05x baseline" = 95% drop = RUG PULL
# - "Price is 50x baseline" = 50x spike = unusual but possible
```

### Scenario 3: Long-Term Monitoring
```bash
# Watch a pool over several hours/days
nohup python test_liquidity_monitoring.py $POOL 300 > monitor.log 2>&1 &
# ^ Check every 5 minutes, run in background

# Later, analyze results
python -c "
import json
with open('liquidity_events_*.json') as f:
    data = json.load(f)
    events = [e for e in data['events'] if e['severity'] == 'CRITICAL']
    if events:
        print('CRITICAL EVENTS DETECTED:', len(events))
"
```

### Scenario 4: Automated Trader Setup
```bash
# In a trading bot, use baseline to detect removal:
from establish_baseline_price import BaselinePriceManager

manager = BaselinePriceManager(pool_address)
result = manager.check_against_baseline()

if result['ratio'] < 0.5:
    # Price dropped 50%+
    # Exit all positions, it's a rug pull!
    trader.exit_all_positions(pool_address)
```

## Troubleshooting

### "Failed to fetch price"
- Pool address might be invalid
- RPC endpoint might be down
- Pool might not have enough vaults

### "Price is same every check"
- Pool is stable (good for detection)
- Or it's not trading much (low activity)

### No events after hours of monitoring
- Pool is stable (no liquidity removal)
- Or deviation threshold (50%) is too high

### Wrong price calculation
- Pool might be Meteora DLMM (bin-based)
- Our calculation is vault-based approximation
- See `meteora_price_fetcher_v2.py` comments for details

## Files and Their Purposes

| File | Purpose |
|------|---------|
| `meteora_price_fetcher_v2.py` | Core price fetching (read vaults, calculate ratio) |
| `establish_baseline_price.py` | Baseline establishment and anomaly detection |
| `test_liquidity_monitoring.py` | Continuous monitoring and event detection |
| `liquidity_monitor.py` | Multi-pool monitoring system (advanced) |
| `baseline_price_*.json` | Stored baseline data |
| `liquidity_events_*.json` | Event logs and price history |
| `LIQUIDITY_MONITORING.md` | Detailed technical documentation |
| `MONITORING_QUICKSTART.md` | Quick start guide |
| `PRICE_TRACKING_COMPLETE.md` | This file |

## Next Steps

1. **Start monitoring a new token**
   ```bash
   python establish_baseline_price.py <POOL_ADDRESS>
   python test_liquidity_monitoring.py <POOL_ADDRESS>
   ```

2. **Integrate into main.py** for automatic pool monitoring

3. **Setup alerts** via email/Discord/Telegram when removal detected

4. **Build trading logic** that reacts to liquidity removal signals

## See Also

- [MONITORING_QUICKSTART.md](MONITORING_QUICKSTART.md) - Simple usage examples
- [LIQUIDITY_MONITORING.md](LIQUIDITY_MONITORING.md) - Technical details
- [meteora_price_fetcher_v2.py](meteora_price_fetcher_v2.py) - Price calculation code
- [CLAUDE.md](CLAUDE.md) - Overall application architecture
