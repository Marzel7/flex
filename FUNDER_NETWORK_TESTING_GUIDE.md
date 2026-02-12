# Funder Network Testing Guide

**Date**: 2026-02-12
**Status**: ✅ Complete
**Tools**: 3 analysis scripts

---

## Overview

Analyze how funders are coordinated across multiple creators to identify pump & dump schemes and coordinated funding networks.

---

## Three Testing Modes

### Mode 1: Test Creator's Funders (Most Common)

```bash
python3 test_funder_network.py <creator_address> --limit 20
```

**What it does:**
- Gets all funders for a specific creator
- For each funder, checks if they also fund OTHER creators
- Shows repeat funders (funders part of larger network)

**Example:**
```bash
python3 test_funder_network.py "8ghYW6ftL5kUemfsoA9X37rz3ZnvyMSZRAx1kt1CxpoS"
```

**Output:**
```
[TEST] Analyzing funder network for creator:
[TEST] 8ghYW6ftL5kUemfsoA9X37rz3ZnvyMSZRAx1kt1CxpoS

[DB] ✅ Found 859 total funders

[ANALYSIS] Top 20 funders by amount:

[ 1]    BDcQH8KXuxFc... |   22.153 SOL | Funds 1 creators
[ 2] 🔗 Fsss6uvqNeap... |    0.700 SOL | Funds 2 creators
           └─ ALSO FUNDS:
              • whamNNP9tHox... (3.600 SOL)
```

**Key Signals:**
- 🔗 marker = repeat funder (funds 2+ creators)
- Shows WHICH other creators they fund
- Total repeat funders at bottom

---

### Mode 2: Analyze a Repeat Funder (Network Deep Dive)

```bash
python3 analyze_repeat_funder.py <funder_address> --limit 10
```

**What it does:**
- Gets ALL creators funded by a specific funder
- For each creator, gets their other funders
- Shows if same funders appear across multiple creators

**Example:**
```bash
python3 analyze_repeat_funder.py "5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9"
```

**Output:**
```
[TEST] Analyzing funder network for repeat funder:
[TEST] 5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9

[DB] ✅ This funder supports 65 creators

[ANALYSIS] Top 10 creators by funding amount:

[ 1] Creator: J6TDXvarvpBd... | 514.996 SOL
      └─ This creator has 31 funders
            2Vu9ayKP5mvi... (2555.132 SOL) - single creator
         🔗 5tzFkiKscXHK... (514.996 SOL) - funds 65 creators!
            5265FEFwZyH6... (416.219 SOL) - single creator
```

**Key Signals:**
- **65 creators funded** = very high coordination
- **514.996, 281.651, 135.154, 87.999 SOL** = suspicious consistency
- Same funder appears across unrelated creators
- 🚩 MAJOR RED FLAG

---

### Mode 3: RPC Funder SOL History (Advanced)

```bash
python3 analyze_funder_networks.py <creator_address> --limit 100
```

**What it does:**
- Fetches each funder's transaction history via Solana RPC
- Extracts SOL transfer patterns
- Shows counterparties and amounts

**Note:** Limited by RPC history (~300-500 recent signatures), so mostly useful for recent activity.

---

## Key Findings from Testing

### Discovery 1: Top Network Hub Identified

**Address**: `5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9`

- **Funds**: 65 creators (EXTREMELY HIGH)
- **Pattern**: Consistent round SOL amounts
  - 514.996 SOL
  - 281.651 SOL
  - 135.154 SOL
  - 87.999 SOL
  - 54.999 SOL (repeated multiple times)
- **Confidence**: 🚩🚩🚩 **VERY HIGH** - Likely pump & dump coordinator

### Discovery 2: Secondary Network Hubs

**Address**: `AxiomRXZAq1Jgjj9pHmNqVP7Lhu67wLXZJZbaK87TTSk`

- **Funds**: 33 creators
- **Pattern**: Similar round amounts
- **Confidence**: 🚩🚩 **HIGH**

**Address**: `iGdFcQoyR2MwbXMHQskhmNsqddZ6rinsipHc4TNSdwu`

- **Funds**: 32 creators
- **Pattern**: Consistent amounts
- **Confidence**: 🚩🚩 **HIGH**

---

## How to Interpret Results

### Red Flag Indicators

```
🔗 marker in output
  = Repeat funder (funds 2+ creators)

ALSO FUNDS: 5+ creators
  = Significant network participation

Consistent round SOL amounts (40, 50, 100, 200, 500+ SOL)
  = Likely automated/coordinated

Multiple funders appearing across same creators
  = Cluster behavior
```

### Risk Scoring

| Indicator | Risk | Action |
|-----------|------|--------|
| Funds 2-5 creators | LOW | Monitor |
| Funds 5-20 creators | MEDIUM | Investigate |
| Funds 20-50 creators | HIGH | Flag for blocklist |
| Funds 50+ creators | CRITICAL | Immediate blocklist |

---

## Workflow Example

### Step 1: Find Suspicious Creator
```bash
# List all creators with most funding
sqlite3 pumpswap_tokens.db "SELECT creator_address, COUNT(*) FROM creator_funders GROUP BY creator_address ORDER BY COUNT(*) DESC LIMIT 5;"
```

### Step 2: Analyze Their Funders
```bash
python3 test_funder_network.py "8ghYW6ftL5kUemfsoA9X37rz3ZnvyMSZRAx1kt1CxpoS" --limit 30
```

### Step 3: Deep Dive on Repeat Funders
```bash
# If you found repeat funders in step 2:
python3 analyze_repeat_funder.py "5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9" --limit 20
```

### Step 4: Decision
- **If funds 50+ creators**: Add to blocklist ✓
- **If funds 20-49 creators**: Flag for review
- **If funds 5-19 creators**: Monitor further
- **If funds 1-4 creators**: Likely legitimate

---

## Database Context

All analysis uses `creator_funders` table:
```sql
SELECT creator_address, funder_address, amount_sol
FROM creator_funders
```

This table captures **pre-migration SOL transfers** to creators, revealing funding relationships before token launch.

---

## Integration Points

These findings should feed into:
1. **Risk scoring** - Flag tokens funded by network hubs
2. **Blocklist** - Add confirmed coordinators
3. **Alerts** - Monitor for new funders from same networks
4. **Dashboard** - Show network analysis for tokens

---

## Known Limitations

1. **Pre-migration only**: Captures funding before token launch
2. **Dust transfers**: Includes many dust amounts (<0.1 SOL)
3. **Old data**: Limited by database snapshot date
4. **No transaction details**: Just addresses and amounts

---

## Next Steps

1. **Immediate**: Add top 4 network hubs to blocklist
2. **This week**: Integrate risk scoring based on funder network size
3. **This month**: Automated alerts for new network hubs
4. **Future**: Real-time network pattern detection

---

**Status**: Ready for Production ✅
**Recommended Action**: Use these tools to identify and blocklist coordinated funding networks immediately.
