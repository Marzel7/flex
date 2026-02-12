# Funder Testing & Analysis Guide

**Date**: 2026-02-12
**Status**: ✅ Complete

---

## Quick Start

### Test ALL Funders for a Creator

```bash
# Analyze all funders for a creator (no limit)
python3 test_funder_network.py <creator_address> --all

# Example:
python3 test_funder_network.py "8ghYW6ftL5kUemfsoA9X37rz3ZnvyMSZRAx1kt1CxpoS" --all
```

### Test Top N Funders (Default)

```bash
# Analyze top 20 funders (default)
python3 test_funder_network.py <creator_address>

# Analyze top 50 funders
python3 test_funder_network.py <creator_address> --limit 50

# Analyze top 100 funders
python3 test_funder_network.py <creator_address> --limit 100
```

---

## The Three Tools

### 1. test_funder_network.py - Quick Funder Analysis

**Purpose**: For a given creator, identify which of their funders also fund OTHER creators

**Usage**:
```bash
# Default: top 20 funders
python3 test_funder_network.py <creator_address>

# All funders
python3 test_funder_network.py <creator_address> --all

# Top N funders
python3 test_funder_network.py <creator_address> --limit 100
```

**Output**:
```
[TEST] Analyzing funder network for creator:
[TEST] 8ghYW6ftL5kUemfsoA9X37rz3ZnvyMSZRAx1kt1CxpoS

[DB] ✅ Found 859 total funders

[ANALYSIS] Analyzing ALL 859 funders:

[ 1]    BDcQH8KXuxFc... |   22.153 SOL | Funds 1 creators
[ 2] 🔗 Fsss6uvqNeap... |    0.700 SOL | Funds 2 creators | 🎯 PUMPFUN: PumpFun Token Creator
           └─ ALSO FUNDS:
              • whamNNP9tHox... (3.600 SOL)

...

================================================================================
SUMMARY
================================================================================
Total funders analyzed: 859 / 859
Repeat funders (fund 2+ creators): 1
Other creators funded by these repeats: 1

🔗 NETWORK DETECTED: 1 funders (0.1%) are part of larger network!
```

**Key Indicators**:
- 🔗 = Repeat funder (funds 2+ creators)
- ✅ CEX: [exchange] = Known exchange account
- ✅ INFRA: [name] = Infrastructure account
- 🎯 PUMPFUN: [name] = PumpFun token creator
- ⚠️ SUSPICIOUS: [name] = Unknown suspicious wallet

---

### 2. analyze_repeat_funder.py - Network Deep Dive

**Purpose**: For a given funder, show ALL creators it funds and identify coordination patterns

**Usage**:
```bash
# Default: top 10 creators funded by this funder
python3 analyze_repeat_funder.py <funder_address>

# Analyze top N creators
python3 analyze_repeat_funder.py <funder_address> --limit 20
```

**Output**:
```
[TEST] Analyzing funder network for repeat funder:
[TEST] G2YxRa6wt1qePMwfJzdXZG62ej4qaTC7YURzuh2Lwd3t

[DB] ✅ This funder supports 27 creators

[ANALYSIS] Top 3 creators by funding amount:

[ 1] Creator: 5t9xBNuDdGTG... | 82.546 SOL
      └─ This creator has 153 funders
         🔗 G2YxRa6wt1qePMwfJzdXZG62ej4qaTC7YURzuh2Lwd3t (82.546 SOL) - funds 27 creators! [✅ CEX: ChangeNow]

[ 2] Creator: A8Z1ejQGk45E... | 30.561 SOL
      └─ This creator has 361 funders
         🔗 AxiomRXZAq1Jgjj9pHmNqVP7Lhu67wLXZJZbaK87TTSk (62.900 SOL) - funds 33 creators! [✅ INFRA: Axiom]

...

================================================================================
NETWORK ANALYSIS SUMMARY
================================================================================
Primary funder: G2YxRa6wt1qe... (funds 27 creators)
Repeat funders in this network: 2

🔗 COORDINATION PATTERN DETECTED!
   The following 2 repeat funders also appear across creators funded by G2YxRa6wt1qe:
   • AxiomRXZAq1Jgjj9pHmNqVP7Lhu67wLXZJZbaK87TTSk (funds 33 creators)
   • G2YxRa6wt1qePMwfJzdXZG62ej4qaTC7YURzuh2Lwd3t (funds 27 creators)
```

---

### 3. analyze_funder_networks.py - RPC SOL History

**Purpose**: Detailed Solana RPC analysis of SOL transfer patterns for each funder

**RPC Endpoint**: `https://api.mainnet-beta.solana.com` (Public Solana RPC)

**Usage**:
```bash
# Default: analyze top 20 funders via Solana RPC
python3 analyze_funder_networks.py <creator_address>

# Analyze top N funders via Solana RPC
python3 analyze_funder_networks.py <creator_address> --limit 50
```

**Note**: Uses public Solana RPC which has rate limits (~30 requests/min). For large-scale analysis, the database-based tools (test_funder_network.py, analyze_repeat_funder.py) are faster.

**Output**:
```
[ANALYSIS] Starting funder network analysis for creator
[ANALYSIS] Creator: 8ghYW6ftL5kUemfsoA9X37rz3ZnvyMSZRAx1kt1CxpoS

[1/20] Funder: BDcQH8KXuxFc... (22.15 SOL to this creator)
[FUNDER] 🔍 Analyzing BDcQH8KXuxFc...
[FUNDER]    Found 10 recent signatures
[FUNDER] ✅ Complete: 2 txs analyzed, 0 unique counterparties

[2/20] Funder: Fsss6uvqNeap... (0.70 SOL to this creator) [🎯 PUMPFUN: PumpFun Token Creator]
[FUNDER] 🔍 Analyzing Fsss6uvqNeap...
[NETWORK] 🔗 This funder ALSO funds 1 other creators!
           • whamNNP9tHox... (3.60 SOL)
```

---

## Common Analysis Workflows

### Workflow 1: Quick Funder Check

Goal: Check if a creator has suspicious funding patterns

```bash
# Step 1: Analyze all funders
python3 test_funder_network.py <creator> --all

# Look for:
# - Funders marked as 🔗 (repeat funders)
# - NOT marked as ✅ CEX or ✅ INFRA
# - Multiple repeat funders across same creators

# If found: proceed to step 2
```

### Workflow 2: Investigate Suspicious Repeat Funder

Goal: Understand a repeat funder's activities

```bash
# Step 1: Run test on creator
python3 test_funder_network.py <creator> --all

# Step 2: Found a suspicious repeat funder?
# Get its address and analyze it:
python3 analyze_repeat_funder.py <suspicious_funder_address> --limit 20

# Look for:
# - How many creators does it fund?
# - Are other repeat funders coordinating with it?
# - Is it marked as CEX/INFRA? (if yes, it's legitimate)
# - If >20 creators funded and NOT CEX/INFRA: HIGH RISK
```

### Workflow 3: Deep Network Analysis

Goal: Detailed analysis of funder's SOL transfer patterns

```bash
# Step 1: Run funder network analysis
python3 analyze_funder_networks.py <creator> --limit 50

# Examines RPC transaction history for:
# - SOL transfer patterns
# - Counterparties and amounts
# - Evidence of coordination
# - Account type classification
```

---

## Interpreting Results

### Account Type Flags

| Flag | Meaning | Risk | Action |
|------|---------|------|--------|
| ✅ CEX: [name] | Known exchange | NEUTRAL | Ignore for blocklist |
| ✅ INFRA: [name] | Infrastructure account | NEUTRAL | Ignore for blocklist |
| 🎯 PUMPFUN: [name] | PumpFun token creator | LOW | Part of platform |
| ⚠️ SUSPICIOUS: [name] | Unknown suspicious wallet | MEDIUM | Monitor |
| (no flag) | Unknown account | HIGH | Investigate |

### Network Detection

| Indicator | Meaning |
|-----------|---------|
| 🔗 marker | Repeat funder (funds 2+ creators) |
| 1 repeat funder (0.1%) | Very low coordination |
| 5 repeat funders (5%) | Some coordination detected |
| 50+ repeat funders (50%) | Heavy network activity |

### Creator Count

| Count | Risk Level | Action |
|-------|-----------|--------|
| 1-5 creators | LOW | Likely legitimate |
| 5-20 creators | MEDIUM | Monitor and investigate |
| 20-50 creators | HIGH | Flag for review |
| 50+ creators | CRITICAL | Consider for blocklist |

**Exception**: If flagged as ✅ CEX or ✅ INFRA, risk is NEUTRAL regardless of creator count

---

## Example Test Results

### Safe Creator (Low Risk)
```
Creator: 8ghYW6ftL5kUemfsoA9X37rz3ZnvyMSZRAx1kt1CxpoS
Funders: 859 total
Repeat funders: 1 (0.1%)
Repeat funder: Fsss6uvqNeap... [🎯 PUMPFUN]

✅ VERDICT: Safe - Only platform account is repeat funder
```

### Suspicious Creator (High Risk)
```
Creator: 22mRirAnEChQb9Mq33TS8W1yE9akouE6AjiTGknc4j3H
Funders: 153 total
Repeat funders: 5 (3.3%)

Repeat funders:
  🔗 G2YxRa6wt1qePMwfJzdXZG62ej4qaTC7YURzuh2Lwd3t (27 creators) [✅ CEX: ChangeNow]
  🔗 5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9 (65 creators) [✅ CEX: Binance 2]
  🔗 UnknownAddress1... (12 creators) (⚠️ NO CEX/INFRA FLAG)
  🔗 UnknownAddress2... (8 creators) (⚠️ NO CEX/INFRA FLAG)
  🔗 UnknownAddress3... (5 creators) (⚠️ NO CEX/INFRA FLAG)

⚠️ VERDICT: Investigate - Multiple unknown repeat funders
           (CEX accounts are legitimate, but 3 unknown repeats worth checking)
```

---

## Command Reference

```bash
# Test creator's funders - all
python3 test_funder_network.py <creator> --all

# Test creator's funders - limited
python3 test_funder_network.py <creator> --limit 50

# Analyze repeat funder's network
python3 analyze_repeat_funder.py <funder> --limit 20

# RPC SOL transfer analysis
python3 analyze_funder_networks.py <creator> --limit 100

# Find all repeat funders in database
python3 batch_wallet_clustering.py --find-repeat-funders
```

---

## RPC Information

### Current Configuration

**analyze_funder_networks.py**:
- **RPC Endpoint**: Public Solana RPC (`https://api.mainnet-beta.solana.com`)
- **Rate Limit**: ~30 requests/minute
- **History**: ~300-500 recent signatures per address
- **Cost**: Free (public endpoint)

### Speed Comparison

| Tool | Data Source | Speed | Coverage |
|------|-------------|-------|----------|
| test_funder_network.py | SQLite Database | ✅ INSTANT | 100% (all pre-migration transfers) |
| analyze_repeat_funder.py | SQLite Database | ✅ INSTANT | 100% (all creators/funders) |
| analyze_funder_networks.py | Solana RPC | ⚠️ SLOW (rate limited) | ~300-500 recent signatures |

**Recommendation**: Use database tools (test_funder_network.py, analyze_repeat_funder.py) for comprehensive analysis. Use RPC tool (analyze_funder_networks.py) only when transaction details are needed.

---

## Status: Ready for Production ✅

All three tools are fully functional and ready for comprehensive funder network analysis. The `--all` flag on test_funder_network.py enables thorough analysis of every funder without limits.

**Tools use**:
- ✅ **Database queries** (SQLite) for instant funder relationship analysis
- ✅ **Public Solana RPC** for detailed transaction history (when needed)
- ✅ **Account detection system** to identify CEX/INFRA accounts
- ✅ **Network coordination detection** to find suspicious patterns

