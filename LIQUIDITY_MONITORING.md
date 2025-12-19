# Liquidity Removal Event Monitoring

This guide explains how to monitor newly launched tokens on Meteora and detect when liquidity removal events occur.

## Quick Start

Monitor a specific pool:
```bash
python test_liquidity_monitoring.py 79EoTzCQjcrTFnxjRzFg2zABUy2keGsPSZd8qLKF4RYM
```

Monitor with custom check interval (30 seconds):
```bash
python test_liquidity_monitoring.py 79EoTzCQjcrTFnxjRzFg2zABUy2keGsPSZd8qLKF4RYM 30
```

Monitor for limited number of checks (20 checks):
```bash
python test_liquidity_monitoring.py 79EoTzCQjcrTFnxjRzFg2zABUy2keGsPSZd8qLKF4RYM 30 20
```

## How It Works

### Price Monitoring

The monitor tracks price changes over time by:
1. **Fetching on-chain price** using `meteora_price_fetcher_v2.py`
2. **Recording price history** with timestamps
3. **Detecting price anomalies** that indicate liquidity removal

### Liquidity Removal Detection

The system detects liquidity removal through multiple signals:

#### 1. **Price Collapse (>50% Drop)**
When a pool's price drops more than 50% between checks, it indicates:
- Vault balances changed dramatically
- Someone removed liquidity from the pool
- Market conditions shifted

Example:
```
Price dropped from $0.01 -> $0.004 (60% drop)
-> CRITICAL LIQUIDITY_REMOVAL event detected
```

#### 2. **Pool Depletion Detection**
When vault-based price calculation returns extremely small values:
- This happens when one vault has been drained
- Remaining vault balance is nearly zero
- Pool is no longer functional for trading

#### 3. **Price Spikes (>50% Increase)**
Conversely, extreme price increases can also indicate:
- Liquidity was removed from one side
- Pool is in an imbalanced state
- High risk of continued volatility

## Output Files

Each monitored pool generates a JSON log file with:
- **Pool address** being monitored
- **Price history** - All recorded prices with timestamps
- **Events** - Detected liquidity removal events with details

Example: `liquidity_events_79EoTzCQ.json`

```json
{
  "pool_address": "79EoTzCQjcrTFnxjRzFg2zABUy2keGsPSZd8qLKF4RYM",
  "updated_at": "2025-12-19T16:38:29.930943",
  "price_history": [
    ["2025-12-19T16:38:08.129131", 0.000000000321961972],
    ["2025-12-19T16:38:13.490408", 0.000000000321961972]
  ],
  "events": [
    {
      "timestamp": "2025-12-19T16:38:45.123456",
      "event_type": "LIQUIDITY_REMOVAL",
      "severity": "CRITICAL",
      "direction": "DROPPED",
      "previous_price": 0.00001,
      "current_price": 0.000001,
      "change_pct": 90.0,
      "message": "Price DROPPED 90.0% ($0.00001 -> $0.000001)"
    }
  ]
}
```

## Understanding Results

### Price Format
Prices are shown in **SOL per token**:
- Small numbers (1e-10) = Very small token price (saturated market)
- Normal numbers (0.001-1.0) = Standard token price
- Large numbers (>100) = Extremely valuable token

### Severity Levels

| Level | Meaning | Action |
|-------|---------|--------|
| **CRITICAL** | >90% price change | Pool likely drained, high risk |
| **HIGH** | 50-90% price change | Significant liquidity removal |
| **MEDIUM** | 20-50% price change | Potential liquidity event |
| **LOW** | 10-20% price change | Minor balance shift |

### Detection Accuracy

The monitor is **most accurate** when:
- ✅ Monitoring from pool creation time (captures initial state)
- ✅ Running continuously (detects changes in real-time)
- ✅ Pool has been stable before liquidity removal
- ✅ Checking frequently (e.g., every 30 seconds)

The monitor may **miss events** if:
- ❌ Liquidity was removed before monitoring started
- ❌ Check interval is too long (hourly instead of minute-level)
- ❌ Price change is gradual (<50% per check)

## Advanced Usage

### Monitor Multiple Pools

Create a script `monitor_multiple_pools.py`:

```python
import subprocess
import time
from datetime import datetime

pools = [
    "79EoTzCQjcrTFnxjRzFg2zABUy2keGsPSZd8qLKF4RYM",
    "POOL2_ADDRESS_HERE",
    "POOL3_ADDRESS_HERE",
]

print(f"[{datetime.now()}] Starting monitoring of {len(pools)} pools...")

for pool in pools:
    print(f"\nMonitoring: {pool}")
    subprocess.run([
        "python", "test_liquidity_monitoring.py",
        pool, "30", "100"  # 30s interval, 100 checks
    ])
```

### Integrate with Main App

The monitoring can be integrated into `main.py` to automatically:
1. Detect new pools
2. Monitor them for liquidity removal
3. Alert users when removal occurs
4. Store events in database

### Parse Events Programmatically

```python
import json
from pathlib import Path

def analyze_events(log_file):
    with open(log_file) as f:
        data = json.load(f)

    events = data['events']
    critical = [e for e in events if e['severity'] == 'CRITICAL']

    print(f"Total events: {len(events)}")
    print(f"Critical: {len(critical)}")

    for event in critical:
        print(f"- {event['message']}")

analyze_events("liquidity_events_79EoTzCQ.json")
```

## Technical Details

### Price Calculation

Prices are calculated using vault balances:
```
price = vault_quote_balance / vault_base_balance
```

This works well for simple constant-product AMMs but has limitations for Meteora DLMM:
- **Accuracy**: ±10-20% for stable pools
- **Depleted pools**: Very unreliable (one vault empty)
- **Multi-bin pools**: Heuristic approximation only

### Vault Selection

For pools with multiple vault pairs, the monitor selects:
- **Primary pair**: Token with known quote (SOL preferred)
- **Multi-bin pools**: Smaller vault balances (closer to active price)
- **Simple pools**: Larger balances (better liquidity)

### Time Intervals

Recommended monitoring intervals:

| Use Case | Interval | Duration |
|----------|----------|----------|
| Real-time alerts | 10-30s | Continuous |
| Event logging | 1-2 min | Hours/Days |
| Daily reports | 5-10 min | Weeks |
| Archive history | Hourly | Months |

## Limitations

1. **Vault-Based Pricing**: Works well for Raydium but is approximate for Meteora DLMM
   - See `meteora_price_fetcher_v2.py` for details
   - True DLMM prices require reading active bin from pool account

2. **Detection Threshold**: 50% price change may miss gradual drains
   - Could be adjusted in code for sensitivity

3. **DexScreener Comparison**: Not used in monitoring (on-chain only)
   - Could add optional DexScreener verification

4. **Historical Data**: Needs continuous running to capture events
   - Can't detect removal that happened while not monitoring

## Troubleshooting

### "Failed to fetch price"
- Check pool address is valid
- Verify RPC endpoint is working
- Wait a few seconds and try again

### "Price unchanged"
- Normal for stable pools
- Can indicate pool is not trading actively
- Monitor for sustained changes (>5 checks)

### No events detected
- Pool may be stable (no liquidity removal)
- Check interval may be too long
- Threshold (50%) may be too high

## Example: Monitor New Token Launch

```bash
# 1. Find pool address for new token
TOKEN_MINT="8CzvPEDHQEnzNpZYhjKHJno5cSgb3TU4EYJtyWTSbonk"
POOL_ADDRESS="79EoTzCQjcrTFnxjRzFg2zABUy2keGsPSZd8qLKF4RYM"

# 2. Start monitoring immediately (frequent checks)
python test_liquidity_monitoring.py $POOL_ADDRESS 15 1000

# 3. Check events log
cat liquidity_events_${POOL_ADDRESS:0:8}.json | grep -i "liquidity_removal"

# 4. Alert on critical events
if grep -q "CRITICAL" liquidity_events_${POOL_ADDRESS:0:8}.json; then
    echo "⚠️  LIQUIDITY REMOVAL DETECTED!"
    # Send alert, log to database, notify users, etc.
fi
```

## Related Files

- `meteora_price_fetcher_v2.py` - Price fetching logic
- `main.py` - Pool discovery and tracking
- `liquidity_monitor.py` - Full-featured monitor (alternative)

## See Also

- [CLAUDE.md](CLAUDE.md) - Application architecture
- [price_tracking_fix_complete.md](price_tracking_fix_complete.md) - Price calculation details
