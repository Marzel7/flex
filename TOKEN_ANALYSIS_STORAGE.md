# Token Analysis Storage & Purchase Strategy

## Overview

The pump.fun curve listener now automatically stores detailed pre-migration analysis for every detected token. This data is used to determine optimal purchase strategy when tokens migrate to the PumpSwap AMM.

## What Gets Stored

When a token is analyzed, **14 metrics** are saved to the `token_analysis` database table:

### Risk Assessment
- **amm_rug_probability**: 0.0-1.0 probability of rug pull
- **amm_risk_level**: 🟢 Low / 🟡 Medium / 🔴 High / ☠️ Critical

### Bonding Curve Metrics (Pre-Migration)
- **mint_concentration**: Wallet concentration (0.0-1.0) - How much supply top wallets hold
- **unique_minters_ratio**: Decentralization (0.0-1.0) - How many unique wallets participated
- **sell_suppression_ratio**: Selling activity (0.0-1.0) - How much selling happened on curve
- **creator_activity_ratio**: Creator participation (0.0-1.0) - Creator's activity level
- **events_parsed**: Total transactions analyzed (integer)

### Activity Metrics
- **mint_velocity_sec**: Buys per second during curve phase
- **buy_size_variance**: Variance in purchase amounts
- **sell_volume_concentration**: How concentrated are sells among wallets

### Pre-Migration Risk (Bonding Curve)
- **rug_probability**: 0.0-1.0 rug probability during curve phase
- **risk_level**: 🟢 Low / 🟡 Medium / 🔴 High / ☠️ Critical

### Metadata
- **analyzed_at**: Unix timestamp of analysis
- **mint**: Token mint address

## Database Schema

```sql
CREATE TABLE token_analysis (
    mint TEXT PRIMARY KEY,                      -- Token address
    analyzed_at REAL,                          -- Analysis timestamp
    events_parsed INTEGER,                     -- Transactions analyzed
    mint_concentration REAL,                   -- Wallet concentration
    unique_minters_ratio REAL,                 -- Decentralization metric
    sell_suppression_ratio REAL,               -- Sell activity
    mint_velocity_sec REAL,                    -- Buys/second
    buy_size_variance REAL,                    -- Purchase variance
    sell_volume_concentration REAL,            -- Sell wallet concentration
    rug_probability REAL,                      -- Pre-migration rug %
    risk_level TEXT,                           -- Pre-migration risk level
    creator_activity_ratio REAL,               -- Creator participation
    amm_rug_probability REAL,                  -- AMM phase rug %
    amm_risk_level TEXT,                       -- AMM phase risk level
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
```

## How Storage Works

### 1. Listener Flow
```
Token Detected (Market Cap Filter)
    ↓
Fetch market cap from DexScreener
    ↓
Store in curve_completions (mint, market_cap, signature)
    ↓
Analyze with PumpFunPreMigrationAnalyzer (async)
    ↓
Store 14 metrics in token_analysis (mint, all metrics)
    ↓
Data ready for purchase strategy decisions
```

### 2. Automatic Storage
Analysis is stored automatically in `analyze_curve()`:
```python
async def analyze_curve(self, mint: str):
    analyzer = PumpFunPreMigrationAnalyzer(mint, rpc_url=RPC_HTTP)
    analyzer.fetch_curve_activity(limit=200)
    summary = analyzer.summary()  # 14-item dict

    # Automatically store in database
    await self._store_analysis(mint, summary)
```

### 3. Concurrent Access
- All writes protected by `asyncio.Lock()`
- SQLite WAL mode enables concurrent access
- Multiple tokens can be analyzed simultaneously

## Querying Stored Data

### Command Line Tool

```bash
# Show all analyzed tokens
python3 query_token_analysis.py

# Show specific token details
python3 query_token_analysis.py DPGsga4z7jKJqrhSeyLi3cgbXoSoK5iKMtWp7gk1pump

# Show only high-risk tokens (75%+ rug probability)
python3 query_token_analysis.py --risk-only

# Show only safe tokens (25%- rug probability)
python3 query_token_analysis.py --safe-only

# Sort all by rug probability (highest first)
python3 query_token_analysis.py --sort-by-rug

# Get help
python3 query_token_analysis.py --help
```

### Programmatic Access

```python
from query_token_analysis import TokenAnalysisQuery

query = TokenAnalysisQuery()

# Get all tokens
tokens = query.get_all_analysis()

# Get specific token
token = query.get_token_analysis("DPGsga4z7jKJqrhSeyLi3cgbXoSoK5iKMtWp7gk1pump")

# Get high-risk tokens
high_risk = query.get_high_risk()

# Get safe tokens
safe = query.get_safe_tokens()
```

## Purchase Strategy Examples

### Example 1: Safe Token
```
Token: 8xNvpTr3Q9d5FYqGd8xA7zK5q3vW2c8P1n9...
AMM Risk Level: 🟢 Low
Rug Probability: 12.5%

Key Factors:
  ✅ Low mint concentration (0.23) - Supply well distributed
  ✅ High unique minters (0.87) - Lots of participation
  ✅ Normal sell activity (0.45) - No suppression

→ STRATEGY: SAFE TO BUY during AMM migration
  Confidence: HIGH
  Position size: Normal allocation acceptable
```

### Example 2: Medium Risk
```
Token: 5GabsSPpAwouAwtoJfXKbzQeyFUX4PUeLdFdTjxzpump
AMM Risk Level: 🟡 Medium
Rug Probability: 42.3%

Key Factors:
  ⚠️  Moderate mint concentration (0.58) - Some wallet dominance
  ⚠️  Medium unique minters (0.52) - Moderate participation
  ⚠️  Some sell suppression (0.68) - Limited exit liquidity

→ STRATEGY: MODERATE RISK - Use position sizing
  Confidence: MEDIUM
  Position size: 50% normal allocation max
```

### Example 3: High Risk (Avoid)
```
Token: DPGsga4z7jKJqrhSeyLi3cgbXoSoK5iKMtWp7gk1pump
AMM Risk Level: 🔴 High
Rug Probability: 59.2%

Key Factors:
  🔴 High mint concentration (0.76) - Top wallets control supply
  🔴 Low unique minters (0.31) - Limited participation
  🔴 Very high sell suppression (0.89) - Almost no selling

→ STRATEGY: AVOID or minimal position size only
  Confidence: LOW
  Position size: 10% normal allocation max
```

## Understanding the Metrics

### Mint Concentration (0.0-1.0)
- **0.0-0.3**: Good distribution - wallets spread out
- **0.3-0.6**: Moderate concentration - some wallet dominance
- **0.6-1.0**: High concentration - few whales control supply
- **Risk**: High concentration = easier to rug

### Unique Minters Ratio (0.0-1.0)
- **0.0-0.3**: Few participants - high risk
- **0.3-0.7**: Moderate participation - medium risk
- **0.7-1.0**: Many participants - lower risk
- **Risk**: Low ratio = less community support

### Sell Suppression Ratio (0.0-1.0)
- **0.0-0.4**: Normal selling - healthy token
- **0.4-0.7**: Some suppression - caution
- **0.7-1.0**: Heavy suppression - red flag
- **Risk**: High suppression = no exit liquidity for holders

### Mint Velocity (buys/second)
- **0.0-2.0**: Slow buying - less hype
- **2.0-5.0**: Moderate buying - normal
- **5.0+**: Fast buying - high volume
- **Strategy**: Higher velocity = more momentum but also more risky

## Real-Time Example Flow

```
[LISTENER] Starting Pump.Fun monitoring...
[FETCH] 📡 Found 20 recent Pump.Fun transactions
[EVENT] 📍 Detected token: DPGsga4z7jKJqrhSeyLi3cgbXoSoK5iKMtWp7gk1pump
[FILTER] ✅ Market cap $54,856 within target range ($50,000 - $80,000) - PROCEEDING
[DB] ✅ Stored DPGsga4z7jKJqrhSeyLi3cgbXoSoK5iKMtWp7gk1pump ($54,856)
[ANALYZER] 🔍 Analyzing DPGsga4z7jKJqrhSeyLi3cgbXoSoK5iKMtWp7gk1pump
[PRE-MIGRATION] ✅ Parsed 208 events
[ANALYZER] 🟡 MEDIUM RISK | Score: 59.20% | DPGsga4z7jKJqrhSeyLi3cgbXoSoK5iKMtWp7gk1pump
[DB] ✅ Stored analysis for DPGsga4z7jKJqrhSeyLi3cgbXoSoK5iKMtWp7gk1pump
```

Then later when token migrates:
```bash
$ python3 query_token_analysis.py DPGsga4z7jKJqrhSeyLi3cgbXoSoK5iKMtWp7gk1pump

📊 TOKEN: DPGsga4z7jKJqrhSeyLi3cgbXoSoK5iKMtWp7gk1pump
⏰ Analyzed: 2026-01-09 10:45:32

🎯 PURCHASE STRATEGY FOR DPGsga4z7jKJqrhSey...
================================================================================
⚠️  CAUTION - Medium rug probability (59.2%)
   Recommendation: MODERATE risk - Use position sizing

📌 KEY RISK FACTORS:
   ⚠️  High mint concentration (0.76) - Top wallets control supply
   ⚠️  Low unique minter ratio (0.31) - Limited participation
   ⚠️  High sell suppression (0.89) - Few sell opportunities

💡 CONFIDENCE LEVEL: MEDIUM
   Based on 208 bonding curve events
```

## Integration with Trading Bot

When implementing automated buying on AMM migration:

```python
def should_buy(mint: str) -> tuple[bool, str]:
    """Determine if we should buy based on stored analysis"""
    query = TokenAnalysisQuery()
    token = query.get_token_analysis(mint)

    if not token:
        return False, "No analysis available"

    rug_prob = token['amm_rug_probability']

    if rug_prob <= 0.25:
        return True, "SAFE - normal position size"
    elif rug_prob <= 0.50:
        return True, "CAUTION - 50% position size"
    elif rug_prob <= 0.75:
        return True, "HIGH RISK - 10% position size"
    else:
        return False, "CRITICAL - do not buy"

# Usage
should_buy_flag, reason = should_buy(token_mint)
if should_buy_flag:
    position_size = calculate_position_size(reason)
    execute_buy_order(token_mint, position_size)
```

## Data Retention

- Analysis data is stored indefinitely in SQLite
- Used to track token outcomes post-migration
- Can correlate analysis metrics with actual rug probability
- Historical data improves future predictions

## Files

- **pumpfun_curve_listener.py** - Main listener (detects and analyzes)
- **pump_fun_pre_migration_analyzer.py** - Analysis engine (calculates 14 metrics)
- **query_token_analysis.py** - Query tool (retrieve and display data)
- **TOKEN_ANALYSIS_STORAGE.md** - This documentation

## Status

✅ **READY FOR PRODUCTION** - Listener automatically stores analysis, query tool ready for deployment
