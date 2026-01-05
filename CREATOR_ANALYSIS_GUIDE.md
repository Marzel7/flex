# Creator Analysis Guide

## Overview

You have a complete creator investigation toolkit that combines database analysis, token patterns, and on-chain wallet behavior - **all using free APIs**.

## The Complete Analysis Stack

### 1. Duplicate Creator Finder
**File**: `analyze_duplicate_creators.py`

Identifies creators with multiple tokens in your database.

```bash
python3 analyze_duplicate_creators.py
```

**Output**:
- Number of creators with multiple tokens
- Peak % for each token
- Time to reach peak
- Trading status and peak price

**Use Case**: Find creators who are rapidly launching tokens (potential pump & dump operations)

---

### 2. Creator Pattern Analysis
**File**: `analyze_creator_patterns.py`

Analyzes trading patterns across a creator's entire portfolio.

```bash
python3 analyze_creator_patterns.py <creator_address>
```

**Example**:
```bash
python3 analyze_creator_patterns.py 6FCpd6KMKKrPLzgtQEcT3YCzEpwykqT2a6pKp8RpFGaA
```

**Detects**:
- ✅ Pump & dump signatures (>100% peak + >50% decline within 2 hours)
- ✅ Token release frequency (rapid vs normal)
- ✅ Portfolio profitability (win rate, average ROI)
- ✅ Time to peak analysis (fast vs slow risers)
- ✅ Exit rates (how many tokens they actually sell)

**Risk Indicators**:
- High frequency token launches → Potential mass production operation
- 0% exit rate → Holding bags or testing
- >100% profit → Skilled trader or insider knowledge
- Consistent >50% dumps → Clear pump & dump pattern

---

### 3. Creator Wallet Analysis
**File**: `analyze_creator_wallet.py`

Analyzes the creator's on-chain wallet behavior using the **Helius API** (free).

```bash
export HELIUS_API_KEY="your_api_key"
python3 analyze_creator_wallet.py <creator_address>
```

**Data Collected**:
- ✅ Database statistics (tokens launched, profitability)
- ✅ Transaction history (100 most recent transactions)
- ✅ Swap activity (% of transactions that are swaps)
- ✅ Transfer patterns (fund distribution)
- ✅ Wallet interactions (unique counterparties)
- ✅ Transaction timestamps and types

**Activity Indicators**:
- **High Swap Activity** (>50 swaps in 100 tx): Active trader, likely swing trading
- **Moderate Swap Activity** (25-50 swaps): Regular token interactions
- **Multiple Wallet Interactions**: Potential multi-wallet control scheme
- **Rapid Fund Movements**: Market making or wash trading signature
- **Consistent Fund Extraction**: Moving profits to treasury wallet

---

## The Analysis Workflow

### Step 1: Identify Duplicate Creators
```bash
python3 analyze_duplicate_creators.py
```
This shows you which creators have multiple tokens - a red flag for pump & dump schemes.

### Step 2: Analyze Creator Patterns
```bash
python3 analyze_creator_patterns.py 6FCpd6KMKKrPLzgtQEcT3YCzEpwykqT2a6pKp8RpFGaA
```
Examine their trading behavior:
- Are they consistently profitable?
- Do tokens peak quickly (pump & dump) or gradually?
- What's their exit rate?
- How frequently do they launch new tokens?

### Step 3: Analyze On-Chain Behavior
```bash
export HELIUS_API_KEY="0ae07551-32df-4d9d-af2a-1925fb7f561f"
python3 analyze_creator_wallet.py 6FCpd6KMKKrPLzgtQEcT3YCzEpwykqT2a6pKp8RpFGaA
```
Check their actual wallet activity:
- How active are they (transaction frequency)?
- What percentage of transactions are swaps?
- Who do they interact with (unique wallets)?
- Recent transaction patterns (timestamps, types)

---

## What Gets Flagged as Suspicious

### Red Flags Detected

**Portfolio Level**:
- ⚠️ 0% exit rate (0.0%) - may be holding bags or testing
- ⚠️ <30% exit rate - selective exits suggest timing/insider knowledge
- ⚠️ >80% exit rate - selling everything (potential quick exit strategy)
- ⚠️ Negative average returns - poor timing or risk management
- ⚠️ Pump & dump signatures: >100% peak + >50% decline within 2 hours

**Wallet Level**:
- ⚠️ Recent wallet (created <1 month ago)
- ⚠️ Rapid fund movements (many transactions per hour)
- ⚠️ Multiple wallet connections (they control other wallets)
- ⚠️ Large SOL deposits before token launches
- ⚠️ Immediate profit extraction (buys, then sells within minutes)
- ⚠️ Consistent pump & dump timing
- ⚠️ Wash trading signatures (rapid buy-sell with same counterparty)

### Green Flags
- ✓ Wallet age >6 months
- ✓ Diverse token holdings (not just pumps)
- ✓ Holding periods (keeps tokens 1-7 days)
- ✓ Mixed results (some wins, some losses - shows random selection)
- ✓ Consistent SOL reserves (not depleting)
- ✓ Stable transaction patterns (predictable rhythm)
- ✓ High successful transaction rate

---

## API Setup (Free)

### Helius API
Your project already has Helius configured for RPC calls. To use the wallet analysis:

**Option 1: Set in .env file**
```bash
echo "HELIUS_API_KEY=0ae07551-32df-4d9d-af2a-1925fb7f561f" >> .env
export $(cat .env | xargs)
```

**Option 2: Set in terminal**
```bash
export HELIUS_API_KEY="0ae07551-32df-4d9d-af2a-1925fb7f561f"
```

**Free Tier**: 1M monthly credits (plenty for analysis)

### Get Your Own Free Key
Visit: https://www.helius.dev/

---

## Example Analysis

### Creator: 6FCpd6KMKKrPLzgtQEcT3YCzEpwykqT2a6pKp8RpFGaA

**Step 1: Portfolio Overview**
```
Total tokens launched: 2
Tokens traded: 0 (0.0%) ⚠️ Not actively trading
Tokens sold: 0 (0.0%) ⚠️ 0% exit rate
Average peak: None (no data yet)
```

**Step 2: Pattern Analysis**
```
Risk Indicators:
  ⚠️ Low exit rate (0.0%) - may be holding bags or testing
  ✓ No pump & dump signatures detected
  ✓ Normal token release pattern (not rapid)
```

**Step 3: On-Chain Behavior**
```
Transaction Activity:
  Total transactions: 100 (last 100 in history)
  Swaps detected: 26 (26.0%) ← MODERATE SWAP ACTIVITY
  Transfers detected: 56
  Unique wallet interactions: 5

Recent Activity (last 10 tx):
  1. ↔️ TRANSFER (2026-01-05 10:54:28)
  2. ↔️ TRANSFER (2026-01-05 10:15:37)
  ...
  7. 🔄 SWAP (2026-01-05 05:04:50)
  10. ⬇️ WITHDRAW (2026-01-05 04:55:19)
```

**Verdict**:
- ✓ Appears to be a legitimate trader
- ✓ Moderate swap activity (26% of transactions)
- ✓ Mix of different transaction types (not just dumps)
- ⚠️ Recent tokens not yet performing (no peak data)
- ⚠️ Not actively exiting positions (0% exit rate)

---

## Transaction Type Indicators

When analyzing wallet activity:

| Icon | Type | Indicator | Concern Level |
|------|------|-----------|---------------|
| 🔄 | SWAP | Token exchange (Jupiter, Orca, etc) | Low |
| ↔️ | TRANSFER | Token movement between wallets | Medium |
| ⬆️ | DEPOSIT | SOL/token inflow | Low |
| ⬇️ | WITHDRAW | SOL/token outflow (profit extraction) | Medium |
| 🔑 | CREATE | New token/mint creation | High |
| ❌ | FAILED | Failed transaction (slippage, errors) | Medium |

High frequency of specific types reveals behavior:
- **Many SWAPs**: Active trader, market maker, scalper
- **Many TRANSFERs**: Fund distribution, multi-wallet scheme
- **Many WITHDRAWALs**: Profit extraction, quick exit strategy
- **Few HOLDs**: Day trader, short-term trader

---

## Advanced Analysis Ideas

### 1. Multi-Creator Comparison
Analyze several creators to find the most trustworthy:
```bash
python3 analyze_creator_patterns.py creator1
python3 analyze_creator_patterns.py creator2
python3 analyze_creator_patterns.py creator3
```
Compare their profitability and trade exit rates.

### 2. Wallet Cluster Analysis
Use Helius to identify if multiple creators share wallet connections:
```bash
python3 analyze_creator_wallet.py creator1
python3 analyze_creator_wallet.py creator2
# Look for overlapping "wallet_interactions"
```

### 3. Time Series Pattern Detection
Track the same creator over time:
```bash
# Day 1
python3 analyze_creator_wallet.py creator_address > day1.txt

# Day 7
python3 analyze_creator_wallet.py creator_address > day7.txt

# Compare growth in transaction activity, swap patterns, etc.
```

### 4. Risk Scoring
Combine indicators to create a trust score:
- Exit rate: 0% = Risky, 50% = Neutral, 100% = Actively Trading
- Profitability: Negative = Risky, Positive = Trustworthy
- Swap activity: <10% = Conservative, >50% = Aggressive
- Wallet age: <1mo = Risky, >6mo = Trustworthy

---

## Integration with Your Trading Bot

You can integrate this analysis into your trading decisions:

1. **Pre-Token Purchase**: Analyze the creator before buying
   ```bash
   python3 analyze_creator_patterns.py $CREATOR_ADDRESS
   ```

2. **Filter by Creator Profile**: Only buy from high-trust creators
   - Exit rate >30%
   - Positive average returns
   - Wallet age >3 months
   - <50% swap activity (not just dumping)

3. **Risk Adjustment**: Adjust position sizing based on creator risk
   - High-trust creator: Normal position size
   - Medium-trust: 50% position size
   - Low-trust: Skip or minimal position

---

## Notes

- All analysis tools use **free APIs** (Helius, no paid Solscan required)
- Database statistics are **automatic** (no API needed)
- On-chain analysis requires **Helius API key** (1M monthly credits free)
- Tools are **production-ready** and handle errors gracefully
- Results are **human-readable** with visual indicators
