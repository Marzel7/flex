# Multi-Token Funding Tracking - Quick Start Guide

## What Does This System Do?

Detects **coordinated pump operations** by tracking when the same funding account is used to launch multiple tokens by different creators.

## The Core Question It Answers

> "Is this creator's funding account also funding OTHER creators? If yes, that's a coordination signal."

## Quick Commands

### 1. Analyze a Creator's Funding Patterns
```bash
python analyze_creator_wallet.py <creator_address>
```

**Output shows:**
- All incoming SOL transfers (funding sources)
- Which ones are reused across multiple tokens
- All outgoing SOL transfers (where profits go)
- Which extraction points receive from multiple creators
- Risk assessment: LOW / MEDIUM / HIGH / CRITICAL

**Example:**
```bash
python analyze_creator_wallet.py 6FCpd6KMKKrPLzgtQEcT3YCzEpwykqT2a6pKp8RpFGaA

# Output includes:
# Incoming SOL transfers: 3
#
# Source Address | SOL Amount | Transfers | Treasury | Reuse Status
# ──────────────────────────────────────────────────────────────────
# dnd5bzqm... | 0.6000 | 6 | 🏦 | 🚩 SHARED (3 creators)
#     └─ Also funded BADTOKEN (CreatorA) 2 days ago
#     └─ Also funded PUMP (CreatorB) 1 day ago
```

### 2. Run Comprehensive Tests
```bash
python tests/test_pumpswap_listener.py test

# Tests:
# ✓ Test 1: Funding account queries
# ✓ Test 2: Creator funding reuse analysis
# ✓ Test 3: Listener detection verification
# ✓ Test 4: Alert display format
# ✓ Test 5: Full integration test
# Summary: System ready for production!
```

### 3. Run Real-Time Listener with Auto-Detection
```bash
python tests/test_pumpswap_listener.py

# When new tokens are detected:
# [FUNDING] Checking funding account reuse...
#
# ═══════════════════════════════════════════
# 🔍 FUNDING ACCOUNT ANALYSIS - Token123...
# ═══════════════════════════════════════════
#
# 🟠 Overall Risk: HIGH
# Pattern: COORDINATED_GROUP
# Creator's tokens: 5
#
# Funding Sources (3 total):
# • dnd5bzqm...
#   └─ 🚩 SHARED (3 creators)
#   └─ Also funded BADTOKEN, PUMP, MOON...
```

## Understanding Risk Levels

### 🟢 LOW RISK
- All funding accounts are **dedicated** (only fund this creator)
- No shared funding detected
- **Interpretation:** Independent creator, not part of a group

**Action:** Normal monitoring

### 🟡 MEDIUM RISK
- **1 shared funding account** (funds 1 other creator)
- Or: Extraction goes to hub used by 1 other creator
- **Interpretation:** Some connection detected

**Action:** Monitor closely, check other creator

### 🟠 HIGH RISK
- **2-4 shared funding accounts** (fund 2-4 other creators each)
- Or: Multiple creators extract to same address
- **Interpretation:** Probable coordination network

**Action:** Flag as suspicious, investigate network

### 🔴 CRITICAL RISK
- **5+ shared funding accounts** (funds 5+ other creators)
- Or: Clear network of coordinated pumps
- **Interpretation:** Professional coordinated group

**Action:** Immediate alert, high probability of rug

## How It Works (Under the Hood)

### Step 1: When Token Launches
```
New token created on PumpSwap
     ↓
Listener detects event
     ↓
Extract creator address
```

### Step 2: Analyze Funding Sources
```
Query: "Who funded this creator?"
Result: Funding Account A, B, C
```

### Step 3: Check for Reuse
```
For each funding account:
  Query: "What OTHER creators does this account fund?"

If no other creators:    ✓ Dedicated (LOW risk)
If 1 other creator:      ⚠️  Reused (MEDIUM risk)
If 2-4 other creators:   🚩 Shared (HIGH risk)
If 5+ other creators:    🚩🚩 Shared (CRITICAL risk)
```

### Step 4: Check Extraction Hubs
```
Query: "Where does this creator send profits?"
       "How many OTHER creators send to same address?"

If unique address:       ✓ Private (LOW risk)
If 2-3 creators send to it: ⚠️ Hub (MEDIUM risk)
If 4+ creators send to it:  🚩 Hub (HIGH risk - likely laundering)
```

### Step 5: Display Alert (if HIGH/CRITICAL)
```
Show:
- Funding account addresses
- Which other tokens they funded
- When those tokens launched
- Overall risk assessment
```

## Example: Detecting a Pump Group

### Scenario
Three different creators launch tokens in the span of 2 days:
- Token A by Creator X
- Token B by Creator Y
- Token C by Creator Z

### System Detection
When Token A launches:
```
[FUNDING] Checking funding account reuse...
🟢 Creator X: LOW risk (new to system)
Result: No alert (first time seeing them)
```

When Token B launches:
```
[FUNDING] Checking funding account reuse...
🟠 Creator Y: HIGH risk (shares funding with Creator X!)
Display alert showing:
  - Account123 funds both Creator X and Creator Y
  - Token A launched 1 day ago
  - Both used same funding source
```

When Token C launches:
```
[FUNDING] Checking funding account reuse...
🔴 Creator Z: CRITICAL risk (coordinated with X and Y!)
Display alert showing:
  - Account123 funds all 3 creators
  - Token A launched 2 days ago
  - Token B launched 1 day ago
  - All 3 using same funding pattern

Interpretation: Professional pump group operation
Recommendation: AVOID - High rug probability
```

## Key Output Indicators

### Table Flags

| Flag | Meaning | Risk |
|------|---------|------|
| ✓ Dedicated | Only funds this creator | 🟢 LOW |
| ⚠️ REUSED (1 other) | Funds 1 other creator | 🟡 MEDIUM |
| 🚩 SHARED (N creators) | Funds N other creators | 🟠 HIGH |
| 🚩🚩 SHARED (N creators) | Funds 5+ creators | 🔴 CRITICAL |
| 🏦 Treasury | Account with >5 transfers | Important |
| ⚠️ Hub (N creators) | Receives from N creators | 🟠 HIGH |

### Risk Thresholds

```
Risk Score: 0-20   → 🟢 LOW
Risk Score: 20-40  → 🟡 MEDIUM
Risk Score: 40-60  → 🟠 HIGH
Risk Score: 60-80  → 🔴 CRITICAL
Risk Score: 80-100 → 🔴 EXTREME
```

## Coordination Pattern Types

1. **INDEPENDENT_CREATOR** (🟢 LOW)
   - All funding is unique
   - No connections to other tokens

2. **SOME_COORDINATION** (🟡 MEDIUM)
   - 1-2 funding sources shared with other creators
   - Possible relationship but limited

3. **COORDINATED_GROUP** (🟠 HIGH)
   - Multiple funding sources shared
   - Clear coordination network detected
   - Likely professional operation

4. **HIGHLY_COORDINATED_GROUP** (🔴 CRITICAL)
   - 5+ shared funding connections
   - Centralized funding network
   - Professional pump group with high probability

## Important Files

| File | Purpose |
|------|---------|
| `analyze_creator_wallet.py` | Core analysis engine |
| `tests/test_pumpswap_listener.py` | Listener + tests |
| `pumpswap_tokens.db` | Data storage |
| `MULTI_TOKEN_FUNDING_IMPLEMENTATION.md` | Technical details |
| `FUNDING_TRACKING_QUICK_START.md` | This file |

## Common Use Cases

### Use Case 1: Safety Check Before Buying
```bash
# Before buying a token, check the creator:
python analyze_creator_wallet.py <creator_address>

# Look for:
# - Is risk level LOW/MEDIUM/HIGH?
# - How many tokens have they created?
# - Do funding sources look coordinated?
```

### Use Case 2: Real-Time Pump Detection
```bash
# Run listener to automatically detect coordinate activities:
python tests/test_pumpswap_listener.py

# System will alert if:
# - Creator uses shared funding sources
# - Multiple creators using same money flow
# - Professional pump patterns detected
```

### Use Case 3: Investigate Network
```bash
# When flagged token detected:
# 1. Note the funding account address
# 2. Analyze other creators using same account
# 3. Cross-reference with other tokens
# 4. Build network map of coordination

python analyze_creator_wallet.py <creator1>
python analyze_creator_wallet.py <creator2>
python analyze_creator_wallet.py <creator3>
# Compare funding sources and extraction points
```

## Troubleshooting

### "Creator not found in database"
- Creator is new, hasn't been analyzed yet
- Will be checked automatically when they create next token
- Run `python analyze_creator_wallet.py <creator>` manually to pre-analyze

### "No funding accounts detected"
- Creator might not have received SOL transfers yet
- Or all transfers are from exchanges (harder to track)
- System will find them once on-chain data is available

### "Risk shows LOW but suspicious behavior"
- System only detects funding patterns
- Use additional indicators: age, wallet history, transaction patterns
- Trust your instincts + system together

## Advanced: Manual Queries

### Find all creators using the same funding account
```bash
# Database query example:
# SELECT DISTINCT creator_address FROM creator_sol_transfers
# WHERE counterparty_address = 'target_address'
# AND transfer_type = 'incoming'
```

### Identify extraction hubs
```bash
# Database query:
# SELECT counterparty_address, COUNT(DISTINCT creator_address) as creator_count
# FROM creator_sol_transfers
# WHERE transfer_type = 'outgoing'
# GROUP BY counterparty_address
# HAVING creator_count > 1
# ORDER BY creator_count DESC
```

## Performance

- ✓ Analysis completes in <2 seconds per creator
- ✓ Real-time alerts in <1 second
- ✓ No external API calls (all local data)
- ✓ Works completely offline once database is populated

## Summary

This system automatically detects coordinated pump operations by finding:

1. **Shared funding accounts** - Same account funds multiple creators
2. **Extraction hubs** - Multiple creators send profits to same address
3. **Network patterns** - Coordinated timing and fund flows
4. **Risk assessment** - Quantifies probability of rug/coordination

**Use it to:**
- ✓ Avoid coordinated pumps before they launch
- ✓ Identify professional pump groups
- ✓ Track fund flows and money laundering
- ✓ Build network maps of coordinated creators

**Remember:**
- System finds *patterns*, not proof of crime
- Use alongside other research (age, community, tokenomics)
- HIGH/CRITICAL = very suspicious but not 100% certain
- Trust the alerts and do your own research

