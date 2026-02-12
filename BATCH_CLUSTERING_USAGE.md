# Batch Wallet Clustering - Usage Guide

## Overview

The `batch_wallet_clustering.py` script analyzes wallet networks and identifies repeat funders in the system.

## Quick Start

### Find Repeat Funders (Database Query)

```bash
# Find all addresses that fund multiple creators
python3 batch_wallet_clustering.py --find-repeat-funders

# Output: 45 addresses funding 2+ creators, sorted by count
```

### Analyze Recent Tokens (RPC Clustering)

```bash
# Test on 1 recent token (identify hop 1 wallets)
python3 batch_wallet_clustering.py --limit 1

# Test on 10 recent tokens
python3 batch_wallet_clustering.py --limit 10

# Analyze all tokens and save results
python3 batch_wallet_clustering.py --limit 0 --save
```

## Modes

### Mode 1: Find Repeat Funders (Recommended First Step)

```bash
python3 batch_wallet_clustering.py --find-repeat-funders
```

**What it does:**
- Queries `creator_funders` table
- Identifies wallets funding 2+ creators
- Sorts by number of creators funded (descending)
- Shows all creators funded by each address

**Output:**
```
📊 5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9
   Funds 65 creators:
     • 22mRirAnEChQb9Mq33TS8W1yE9akouE6AjiTGknc4j3H
     • 2YGJkpuPRp3256LYJCqu6KxBd3Ba99t89WrbHKVfEvAs
     ...
```

**Use case:** Identify coordinated funding networks, potential pump & dump schemes

---

### Mode 2: RPC Wallet Clustering Analysis

```bash
python3 batch_wallet_clustering.py --limit 5 --save
```

**What it does:**
- Fetches N most recent tokens from database
- Gets creator's recent transaction history via Solana RPC
- Detects SOL transfers between creator and other wallets
- Identifies "hop 1" wallets (direct interactions)
- Saves clustering data to `wallet_cluster_nodes` table

**Output:**
```
[CLUSTERING] 🔍 Analyzing creator Ba78chYnvfxh...
[CLUSTERING]    Found 51 recent signatures
[CLUSTERING] ✅ Complete: 2 txs analyzed, 0 hop-1 wallets
```

**Use case:** Build out wallet network graph, analyze transaction patterns

---

## Common Queries

### Find Top 5 Repeat Funders

```bash
python3 batch_wallet_clustering.py --find-repeat-funders 2>&1 | grep -A 15 "📊" | head -40
```

### Count How Many Addresses Fund 10+ Creators

```bash
python3 batch_wallet_clustering.py --find-repeat-funders 2>&1 | grep "Funds" | grep -E "Funds [1-9][0-9]|Funds [0-9]{3}" | wc -l
```

### Export Repeat Funders to CSV

```bash
python3 << 'PYTHON'
import sqlite3

conn = sqlite3.connect("pumpswap_tokens.db")
cursor = conn.cursor()

# Get repeat funders
cursor.execute("""
    SELECT funder_address, COUNT(DISTINCT creator_address) as creator_count
    FROM creator_funders
    GROUP BY funder_address
    HAVING COUNT(DISTINCT creator_address) > 1
    ORDER BY creator_count DESC
""")

print("Funder Address,Creators Funded")
for funder, count in cursor.fetchall():
    print(f"{funder},{count}")

conn.close()
PYTHON
```

---

## Arguments

| Argument | Description | Default |
|----------|-------------|---------|
| `--limit N` | Analyze N most recent tokens (0=all) | 1 |
| `--save` | Save clustering results to database | False |
| `--find-repeat-funders` | Query database for repeat funders | False |

---

## Performance Notes

### Database Query Mode
- **Speed**: ~100ms (instant)
- **Data**: From `creator_funders` table
- **Coverage**: 432 creators, 100+ funders

### RPC Analysis Mode
- **Speed**: ~1-2 sec per creator
- **Data**: From Solana public RPC
- **Limit**: 1000 recent signatures per creator (public RPC limitation)
- **Recommended**: Test on 5-10 tokens first

---

## Interpreting Results

### Repeat Funders Types

1. **Network Hubs** (20+ creators)
   - Likely infrastructure or coordinated funding
   - Red flag for pump & dump
   - Require investigation

2. **Operational Wallets** (5-19 creators)
   - Could be legitimate multi-project backer
   - Or infrastructure provider
   - Need context check

3. **Connected Pairs** (2-4 creators)
   - May be legitimate funding relationships
   - Low risk unless part of larger network

---

## Next Steps

1. Use `--find-repeat-funders` to identify networks
2. Check top addresses against:
   - CEX mappings (infra_mapping.py)
   - Creator blocklist (creator_blocklist table)
   - Domain tags (address_tags table)
3. Analyze transaction patterns for suspicious behavior
4. Add identified networks to blocklist if needed

---

**Last Updated**: 2026-02-12
**Status**: Ready for Production ✅
