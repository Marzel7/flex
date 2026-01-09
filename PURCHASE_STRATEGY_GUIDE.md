# Pump.Fun → PumpSwap Purchase Strategy Guide

Complete workflow from token detection to post-migration purchase decisions.

## 🚀 Quick Start

### Step 1: Start the Listener
```bash
python3 pumpfun_curve_listener.py
```

The listener will:
1. Monitor pump.fun bonding curves in real-time
2. Filter tokens by $50k-$80k USD market cap
3. Analyze pre-migration risk factors
4. Automatically store 14 detailed metrics per token

Expected output:
```
[LISTENER] Starting Pump.Fun monitoring...
[FETCH] 📡 Found 20 recent Pump.Fun transactions
[EVENT] 📍 Detected token: DPGsga4z7jKJqrhSeyLi3cgbXoSoK5iKMtWp7gk1pump
[FILTER] ✅ Market cap $54,856 within target range
[DB] ✅ Stored DPGsga4z7jKJqrhSeyLi3cgbXoSoK5iKMtWp7gk1pump
[ANALYZER] 🔍 Analyzing DPGsga4z7jKJqrhSeyLi3cgbXoSoK5iKMtWp7gk1pump
[ANALYZER] 🟡 MEDIUM RISK | Score: 59.20% | DPGsga4z7jKJqrhSeyLi3cgbXoSoK5iKMtWp7gk1pump
[DB] ✅ Stored analysis for DPGsga4z7jKJqrhSeyLi3cgbXoSoK5iKMtWp7gk1pump
```

### Step 2: Monitor Token Progress
```bash
# List all analyzed tokens
python3 query_token_analysis.py

# Get details for specific token
python3 query_token_analysis.py DPGsga4z7jKJqrhSeyLi3cgbXoSoK5iKMtWp7gk1pump
```

### Step 3: When Token Migrates to PumpSwap
Use the stored analysis to decide on position size:

```python
from query_token_analysis import TokenAnalysisQuery

query = TokenAnalysisQuery()
token = query.get_token_analysis(token_mint)

if token['amm_rug_probability'] <= 0.25:
    position_size = 100  # Full allocation
elif token['amm_rug_probability'] <= 0.50:
    position_size = 50   # Half allocation
elif token['amm_rug_probability'] <= 0.75:
    position_size = 10   # 10% only
else:
    position_size = 0    # Do not buy
```

## 📊 Data Collected

For each analyzed token, 14 metrics are stored:

### Risk Indicators
| Metric | Range | Interpretation |
|--------|-------|-----------------|
| **rug_probability** | 0.0-1.0 | Pre-migration rug probability |
| **amm_rug_probability** | 0.0-1.0 | AMM phase rug probability |
| **risk_level** | Low/Med/High/Crit | Pre-migration risk |
| **amm_risk_level** | Low/Med/High/Crit | AMM phase risk |

### Wallet Distribution
| Metric | Range | Interpretation |
|--------|-------|-----------------|
| **mint_concentration** | 0.0-1.0 | Top wallets' supply ownership |
| **unique_minters_ratio** | 0.0-1.0 | Decentralization (more is better) |
| **creator_activity_ratio** | 0.0-1.0 | Creator participation level |

### Liquidity & Activity
| Metric | Range | Interpretation |
|--------|-------|-----------------|
| **sell_suppression_ratio** | 0.0-1.0 | How much selling was suppressed |
| **sell_volume_concentration** | 0.0-1.0 | Seller wallet concentration |
| **mint_velocity_sec** | 0.0+ | Buys per second |
| **buy_size_variance** | 0.0+ | Buy amount consistency |
| **events_parsed** | Integer | Total curve transactions analyzed |

## 🎯 Decision Framework

### Red Flags ⚠️ (Do Not Buy)
- [ ] `amm_rug_probability` > 0.75 (Critical)
- [ ] `mint_concentration` > 0.8 (Few whales control supply)
- [ ] `unique_minters_ratio` < 0.2 (Almost no participation)
- [ ] `sell_suppression_ratio` > 0.9 (No exit liquidity)

### Caution Flags 🟡 (Reduced Position Size)
- [ ] `amm_rug_probability` 0.50-0.75 (High Risk)
- [ ] `mint_concentration` 0.6-0.8 (Moderate whale dominance)
- [ ] `unique_minters_ratio` 0.2-0.5 (Low participation)
- [ ] `sell_suppression_ratio` 0.7-0.9 (Limited exit liquidity)

### Safe Tokens ✅ (Normal Position Size)
- [ ] `amm_rug_probability` <= 0.25 (Low Risk)
- [ ] `mint_concentration` < 0.3 (Well distributed)
- [ ] `unique_minters_ratio` > 0.7 (High participation)
- [ ] `sell_suppression_ratio` < 0.4 (Normal selling)

## 📈 Real-World Examples

### Example 1: Safe Token → BUY NORMAL
```
Token: 5GabsSPpAwouAwtoJfXKbzQeyFUX4PUeLdFdTjxzpump
Rug Probability: 15.3%
Risk Level: 🟢 LOW

Metrics:
  ✅ Mint Concentration: 0.18 (well distributed)
  ✅ Unique Minters: 0.84 (great participation)
  ✅ Sell Suppression: 0.22 (normal selling)
  ✅ Seller Concentration: 0.15 (distributed sellers)

Decision: BUY with full position size
Reason: Low concentration, high participation, normal liquidity
```

### Example 2: Medium Risk → BUY CAUTIOUSLY
```
Token: DPGsga4z7jKJqrhSeyLi3cgbXoSoK5iKMtWp7gk1pump
Rug Probability: 59.2%
Risk Level: 🟡 MEDIUM

Metrics:
  ⚠️  Mint Concentration: 0.76 (whales control 76%)
  ⚠️  Unique Minters: 0.31 (low participation)
  ⚠️  Sell Suppression: 0.89 (heavy selling suppression)
  ⚠️  Seller Concentration: 0.68 (concentrated sellers)

Decision: BUY with 50% position size
Reason: Some risk factors present, but not critical
Monitoring: Watch for whale dumping in first 24h
```

### Example 3: High Risk → AVOID
```
Token: JKqrhSeyLi3cgbXoSoK5iKMtWp7gk1pumpXYZ123
Rug Probability: 87.4%
Risk Level: ☠️ CRITICAL

Metrics:
  🔴 Mint Concentration: 0.92 (2 whales control 92%)
  🔴 Unique Minters: 0.08 (almost no participants)
  🔴 Sell Suppression: 0.98 (almost no selling allowed)
  🔴 Seller Concentration: 0.95 (1 wallet controls sells)

Decision: DO NOT BUY
Reason: Textbook rug setup - whales control supply and liquidity
```

## 🔍 Query Commands

### See All Tokens
```bash
python3 query_token_analysis.py
```
Output:
```
📊 ANALYZED TOKENS (8 total)

MINT                                        RISK         RUG %    ANALYZED
DPGsga4z7jKJqrhSeyLi3cgbXoSoK5iKMtWp7gk1... 🟡 MEDIUM    59.2%    10:45:32
5GabsSPpAwouAwtoJfXKbzQeyFUX4PUeLdFdTjxz... 🟢 LOW       15.3%    10:42:10
JKqrhSeyLi3cgbXoSoK5iKMtWp7gk1pumpXYZ123... ☠️ CRITICAL   87.4%    10:38:45
```

### See Only Safe Tokens (< 25% rug)
```bash
python3 query_token_analysis.py --safe-only
```

### See Only Risky Tokens (> 75% rug)
```bash
python3 query_token_analysis.py --risk-only
```

### Sort All by Risk (Highest First)
```bash
python3 query_token_analysis.py --sort-by-rug
```

### Get Full Details for One Token
```bash
python3 query_token_analysis.py DPGsga4z7jKJqrhSeyLi3cgbXoSoK5iKMtWp7gk1pump
```
Output:
```
================================================================================
📊 TOKEN: DPGsga4z7jKJqrhSeyLi3cgbXoSoK5iKMtWp7gk1pump
================================================================================

⏰ Analyzed: 2026-01-09 10:45:32

🔴 RISK ASSESSMENT:
  • AMM Risk Level: 🟡 MEDIUM
  • Rug Probability: 59.20%

💰 BONDING CURVE METRICS (208 events):
  • Mint Concentration: 0.760
  • Unique Minters Ratio: 0.310
  • Sell Suppression: 0.890
  • Creator Activity Ratio: 0.420

📈 ACTIVITY METRICS:
  • Mint Velocity: 2.45 mints/sec
  • Buy Size Variance: 1250
  • Sell Volume Concentration: 0.680

🎯 PRE-MIGRATION RISK: 🟡 MEDIUM
  • Pre-Migration Rug Probability: 42.3%

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

## 🤖 Automated Integration

### Python Integration
```python
from query_token_analysis import TokenAnalysisQuery
import your_trading_bot

query = TokenAnalysisQuery()

def execute_trading_strategy(token_mint):
    """Execute buy order based on stored analysis"""
    analysis = query.get_token_analysis(token_mint)

    if not analysis:
        return False, "Token not analyzed"

    rug_prob = analysis['amm_rug_probability']

    # Determine position size based on risk
    if rug_prob <= 0.25:
        position_usd = 1000  # Full allocation
    elif rug_prob <= 0.50:
        position_usd = 500   # 50% allocation
    elif rug_prob <= 0.75:
        position_usd = 100   # 10% allocation
    else:
        return False, "Too risky - skip"

    # Execute buy
    return your_trading_bot.buy(
        token=token_mint,
        amount_usd=position_usd,
        metadata=analysis  # Store metadata for later analysis
    )

# Use on AMM migration
if token_migrated_to_pumpswap:
    success, msg = execute_trading_strategy(token_mint)
```

## 📊 Tracking Outcomes

Over time, you can correlate:
- **Actual rug probability** vs predicted `amm_rug_probability`
- **Profitable tokens** vs risk metrics
- **Win rate** by risk level

This data improves the model:
```bash
# Find tokens that matched predictions
SELECT mint, amm_rug_probability, actual_outcome
FROM token_analysis ta
JOIN trading_results tr ON ta.mint = tr.mint
WHERE tr.outcome = 'rug' AND ta.amm_rug_probability > 0.75
```

## 🔄 Workflow Summary

```
┌─────────────────────────────────────────────────────┐
│ 1. START LISTENER                                   │
│    python3 pumpfun_curve_listener.py               │
│    └─ Monitors $50k-$80k tokens                    │
└────────────────┬──────────────────────────────────┘
                 │
┌────────────────▼──────────────────────────────────┐
│ 2. AUTOMATIC ANALYSIS & STORAGE                    │
│    • Fetch 200 bonding curve transactions          │
│    • Calculate 14 risk metrics                     │
│    • Store in SQLite database                      │
└────────────────┬──────────────────────────────────┘
                 │
┌────────────────▼──────────────────────────────────┐
│ 3. QUERY & REVIEW                                  │
│    python3 query_token_analysis.py                │
│    └─ View all tokens, filter by risk             │
└────────────────┬──────────────────────────────────┘
                 │
┌────────────────▼──────────────────────────────────┐
│ 4. MIGRATION MONITORING                            │
│    • Watch for PumpSwap pool creation             │
│    • Token price available on DexScreener         │
└────────────────┬──────────────────────────────────┘
                 │
┌────────────────▼──────────────────────────────────┐
│ 5. EXECUTE PURCHASE STRATEGY                       │
│    • Look up stored analysis                       │
│    • Determine position size based on risk        │
│    • Execute buy order with confidence            │
└─────────────────────────────────────────────────────┘
```

## 📚 Documentation

- **TOKEN_ANALYSIS_STORAGE.md** - Detailed metric explanations
- **PUMPFUN_DATABASE_FIX.md** - Database locking solution
- **PUMPFUN_LISTENER_STATUS.md** - Listener overview
- **query_token_analysis.py** - Inline documentation

## ✅ Status

- ✅ Real-time token detection ($50k-$80k range)
- ✅ 14-metric pre-migration analysis
- ✅ Automatic database storage
- ✅ Query and filtering tool
- ✅ Purchase strategy framework
- ✅ Production ready

---

**Last Updated**: January 9, 2026
**Status**: Production Ready
