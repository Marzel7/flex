# Liquidity Monitoring - Quick Start Guide

## What You Can Do Now

You now have tools to **detect when token creators remove liquidity** from Meteora pools by monitoring on-chain prices and vault balances.

## One-Line Usage

Monitor a pool in real-time:
```bash
python test_liquidity_monitoring.py <POOL_ADDRESS>
```

Example:
```bash
python test_liquidity_monitoring.py 79EoTzCQjcrTFnxjRzFg2zABUy2keGsPSZd8qLKF4RYM
```

## What Happens

The monitor will:
1. **Every 30 seconds** (by default): Fetch the current price
2. **Compare with previous price**: Detect if price changed >50%
3. **Log events** to a JSON file: All detected anomalies with timestamps
4. **Show summary**: Statistics at the end

Example output:
```
================================================================================
Liquidity Removal Monitor - Pool: 79EoTzCQjcrTFnxjRzFg2zABUy2keGsPSZd8qLKF4RYM
================================================================================
Check interval: 30s
Log file: liquidity_events_79EoTzCQ.json

[2025-12-19 16:38:04] Check #1... $0.000000000321961972
[2025-12-19 16:38:34] Check #2... $0.000000000321961972
...

🔴 CRITICAL - Price DROPPED 85.0% ($0.001 -> $0.00015)
  Time: 2025-12-19T16:39:45.123456

================================================================================
MONITORING SUMMARY
================================================================================
Total checks: 45
Total events: 1 detected

Detected Events:
  1. [CRITICAL] LIQUIDITY_REMOVAL - Price DROPPED 85.0% (...)

Detailed history saved to: liquidity_events_79EoTzCQ.json
```

## Customization

### Change Check Interval
```bash
# Check every 15 seconds
python test_liquidity_monitoring.py 79EoTzCQ... 15
```

### Stop After N Checks
```bash
# Monitor for 100 checks (50 minutes at 30s interval)
python test_liquidity_monitoring.py 79EoTzCQ... 30 100
```

### Both
```bash
# Check every 20 seconds, stop after 60 checks
python test_liquidity_monitoring.py 79EoTzCQ... 20 60
```

## Understanding Results

### Event Severity
- **CRITICAL** 🔴 >90% price change = Pool likely drained
- **HIGH** 🟠 50-90% change = Significant liquidity removal
- **MEDIUM** 🟡 20-50% change = Notable event
- **LOW** 🟢 10-20% change = Minor adjustment

### Price Format
Prices are shown as **SOL per token**:
- `0.000000000321961972` = Extremely small (saturated/worthless token)
- `0.001` = Small/new token
- `1.0` = Normal trading price
- `100+` = Extremely valuable

### Event Detection

The system detects liquidity removal via **price changes** because:
1. On-chain price = ratio of vault balances
2. When liquidity is removed → vault balance changes
3. When balance changes → price changes
4. Large removal → large price change (>50%)

## Output Files

Each monitoring session creates a JSON file:
```
liquidity_events_79EoTzCQ.json
```

Contains:
- `price_history[]` - All prices checked with timestamps
- `events[]` - Detected liquidity removal events
- `updated_at` - Last update time

You can analyze this later:
```python
import json

with open('liquidity_events_79EoTzCQ.json') as f:
    data = json.load(f)

# Check if any critical events
critical = [e for e in data['events'] if e['severity'] == 'CRITICAL']
print(f"Critical events: {len(critical)}")
```

## Common Scenarios

### Scenario 1: Monitor New Launch
```bash
# Just launched? Start monitoring immediately with frequent checks
python test_liquidity_monitoring.py POOL_ADDRESS 10 1000  # Every 10s, 1000x
```

### Scenario 2: Check Specific Time Period
```bash
# Monitor for ~1 hour (every 30s, 120 checks)
python test_liquidity_monitoring.py POOL_ADDRESS 30 120
```

### Scenario 3: Continuous Overnight Monitoring
Run in background:
```bash
nohup python test_liquidity_monitoring.py POOL_ADDRESS 60 > pool_monitor.log 2>&1 &
```

### Scenario 4: Alert on Liquidity Removal
Create alert script:
```bash
python test_liquidity_monitoring.py POOL_ADDRESS 30 1000

# Check for critical events
if grep -q "CRITICAL" liquidity_events_*.json; then
    echo "⚠️  LIQUIDITY REMOVAL DETECTED - NOTIFY USERS"
    # Send email, SMS, Telegram, Discord, etc.
fi
```

## How Accuracy Works

### Most Accurate When:
✅ Monitoring from pool creation (captures initial state)
✅ Frequent checks (every 10-30 seconds)
✅ Pool is stable before removal
✅ Continuous running (no gaps)

### May Miss Events If:
❌ Liquidity already removed before monitoring started
❌ Check interval too long (hourly)
❌ Price change is gradual (<50% per check)
❌ Pool's price is already extremely low

## Technical Details

### Why This Works

For constant-product AMMs (like Meteora):
```
price = token_vault_balance / sol_vault_balance
```

When liquidity is removed:
- Vault balance decreases
- Price ratio changes
- We detect the change

### Why It Might Not Work

For Meteora DLMM pools:
- True price depends on **active bin ID** (not vault balances)
- Our calculation is an **approximation**
- Accurate pricing requires reading bin data from account

**Bottom line**: Detection is **reliable**, pricing **may be approximate** for DLMM

## Need More Details?

See [LIQUIDITY_MONITORING.md](LIQUIDITY_MONITORING.md) for:
- Advanced integration examples
- Multi-pool monitoring setup
- Event analysis scripts
- Troubleshooting guide
- Technical architecture

## See Also

- `meteora_price_fetcher_v2.py` - On-chain price calculation
- `main.py` - Pool discovery system
- `CLAUDE.md` - Overall application architecture
