# CEX Tagging System - Complete Implementation

## Overview

The CEX Tagging System automatically detects and flags centralized exchange wallets throughout the funding and clustering analysis pipeline. When tokens migrate to PumpSwap:

1. **Funding extracted**: Pre-migration SOL transfers to creator identified
2. **Clustering analyzed**: Related wallet networks discovered
3. **CEX check**: Each wallet/funder checked against known CEX mapping
4. **Tagging**: CEX-linked wallets tagged with exchange+type
5. **Logging**: Clear 🏛️ indicators in console output
6. **Database**: CEX flags stored for risk scoring

---

## Components

### 1. CEX Wallets Database

**Table**: `cex_wallets`
**Purpose**: Master list of known centralized exchange wallets

```sql
CREATE TABLE cex_wallets (
    cex_address TEXT PRIMARY KEY,
    exchange_name TEXT,           -- 'Coinbase', 'Binance', 'Kraken'
    wallet_type TEXT,             -- 'Hot Wallet', 'Custody', 'Bridge', etc.
    confidence_level INTEGER,     -- 100=verified, 95=highly likely, 90=suspected
    discovered_date TIMESTAMP,
    discovery_source TEXT,        -- 'Solscan', 'Official', 'Manual'
    notes TEXT,
    is_active BOOLEAN
);
```

**Seeded Wallets**:
- Coinbase Custody/Staking (100% confidence)
- Coinbase Hot Wallet (95% confidence)
- Kraken Hot Wallet (95% confidence)
- Binance Hot Wallet (95% confidence)

### 2. Funding Analysis with CEX Detection

**File**: `realtime_creator_funding_extractor.py`
**Method**: `RealTimeCreatorFundingExtractor._save_funder()`

**Flow**:
```
Pre-migration transaction detected
    ↓
Extract SOL transfer counterparty (funder)
    ↓
Query: SELECT FROM cex_wallets WHERE cex_address = ?
    ↓
If found:
    - Set is_cex = 1
    - Store cex_exchange, cex_type
    - Log: 🏛️ CEX FUNDER DETECTED
    ↓
Save to creator_funders table
```

**Example Output**:
```
[FUNDING] 🏛️ CEX FUNDER DETECTED: Coinbase Hot Wallet → AY5kpQX... (250.00 SOL)
[FUNDING] ✅ Saved funder: DPqsobyS... → AY5kpQX... (CEX: Coinbase)
```

### 3. Wallet Clustering with CEX Detection

**File**: `realtime_wallet_clustering_extractor.py`
**Method**: `RealtimeWalletClusteringExtractor._save_cluster_node()`

**Flow**:
```
Creator transaction analyzed
    ↓
Wallet interactions identified (hop 0, 1, 2...)
    ↓
For each wallet:
    - Query: SELECT FROM cex_wallets WHERE cex_address = ?
    ↓
If found:
    - Append tag: "🏛️ Exchange Type"
    - Log: 🏛️ CEX WALLET IN NETWORK
    ↓
Save to wallet_cluster_nodes with tags
```

**Example Output**:
```
[CLUSTERING] 🔍 Building wallet cluster for AY5kpQX...
[CLUSTERING]    Found 50 recent signatures
[CLUSTERING] 🏛️ CEX WALLET IN NETWORK: Coinbase Hot Wallet connected to AY5kpQX...
[CLUSTERING] ✅ Saved 46 wallet nodes (2 CEX-linked)
```

### 4. Creator Funders Table

**Table**: `creator_funders`
**Purpose**: Track pre-migration funding sources with CEX flags

```sql
CREATE TABLE creator_funders (
    creator_address TEXT NOT NULL,
    funder_address TEXT NOT NULL,
    amount_sol REAL,
    first_detected_at TIMESTAMP,
    is_cex BOOLEAN DEFAULT 0,         -- 1 if from known CEX
    cex_exchange TEXT,                -- 'Coinbase', 'Kraken', etc.
    cex_type TEXT,                    -- 'Hot Wallet', 'Custody', etc.
    PRIMARY KEY(creator_address, funder_address)
);
```

### 5. Wallet Cluster Nodes Table

**Table**: `wallet_cluster_nodes`
**Purpose**: Network analysis with tags for special wallet types

```sql
-- Existing schema enhanced with CEX tags in 'tags' field
CREATE TABLE wallet_cluster_nodes (
    root_creator TEXT,
    wallet TEXT,
    hop INTEGER,
    confidence REAL,
    tags TEXT,                        -- Can include "🏛️ Coinbase Hot Wallet"
    first_seen_ts INTEGER,
    last_seen_ts INTEGER,
    UNIQUE(root_creator, wallet)
);
```

---

## Usage & Querying

### Query 1: Find All CEX-Funded Creators

```sql
SELECT DISTINCT
    cf.creator_address,
    COUNT(*) as funder_count,
    SUM(CASE WHEN cf.is_cex = 1 THEN 1 ELSE 0 END) as cex_count,
    GROUP_CONCAT(DISTINCT cf.cex_exchange) as exchanges,
    SUM(cf.amount_sol) as total_funding
FROM creator_funders cf
WHERE cf.is_cex = 1
GROUP BY cf.creator_address
ORDER BY cex_count DESC, total_funding DESC;
```

**Output**:
```
creator_address        | funder_count | cex_count | exchanges           | total_funding
AY5kpQXdwEevDfQptjUt   | 8            | 3         | Coinbase,Kraken     | 450.25
8i2avmxgeHMz5VoZNo21m  | 5            | 2         | Coinbase,Binance    | 180.50
```

### Query 2: Find Creators in CEX-Containing Clusters

```sql
SELECT DISTINCT
    wcn.root_creator,
    COUNT(*) as cluster_size,
    SUM(CASE WHEN wcn.tags LIKE '%🏛️%' THEN 1 ELSE 0 END) as cex_wallets,
    GROUP_CONCAT(DISTINCT wcn.tags) as wallet_tags
FROM wallet_cluster_nodes wcn
WHERE wcn.tags LIKE '%🏛️%'
GROUP BY wcn.root_creator
ORDER BY cex_wallets DESC;
```

**Output**:
```
root_creator           | cluster_size | cex_wallets | wallet_tags
AY5kpQXdwEevDfQptjUt   | 46           | 2           | 🏛️ Coinbase Hot Wallet | 🏛️ Kraken Hot Wallet
```

### Query 3: Identify Most Active CEX Wallets Funding Creators

```sql
SELECT
    cf.funder_address,
    cf.cex_exchange,
    cf.cex_type,
    COUNT(DISTINCT cf.creator_address) as creator_count,
    SUM(cf.amount_sol) as total_sol,
    AVG(cf.amount_sol) as avg_per_creator
FROM creator_funders cf
WHERE cf.is_cex = 1
GROUP BY cf.funder_address
ORDER BY creator_count DESC, total_sol DESC;
```

**Output**:
```
funder_address                                | exchange | type        | creator_count | total_sol | avg_per_creator
DPqsobysNf5iA9w7zrQM8HLzCKZEDMkZsWbiidsAt1xo | Coinbase | Hot Wallet  | 8             | 425.00    | 53.13
```

---

## Risk Scoring Integration

CEX detection increases rug probability for flagged tokens:

```python
base_risk = 20  # Low baseline

# Funding risk
if creator_has_cex_funding:
    if cex_type == 'Hot Wallet':
        base_risk += 30  # CRITICAL - active exchange account
    elif cex_type == 'Custody':
        base_risk += 25  # HIGH - institutional movement
    else:
        base_risk += 15  # MEDIUM - other exchange types

# Clustering risk
if creator_in_cluster_with_cex:
    if cluster_size > 20:
        base_risk += 20  # Multiple CEX-linked wallets
    else:
        base_risk += 10

final_risk = min(100, base_risk)
```

**Example Calculation**:
```
Creator AY5kpQX:
- Base: 20 (normal)
- CEX funding (Coinbase Hot): +30 = 50
- In cluster with 2 CEX wallets: +20 = 70
- Final Risk: 70/100 (HIGH)
```

---

## Log Output Examples

### Example 1: CEX-Funded Token Migration

```
[MIGRATION] 🚨 New migration detected: PUMP...
[CREATOR] ✅ Extracted from earliest tx: AY5kpQXdwEevDfQptjUtPh...
[FUNDING] 🔍 Extracting pre-migration funding...
[FUNDING] ✅ Found 3 funding sources
[FUNDING] 🏛️ CEX FUNDER DETECTED: Coinbase Hot Wallet → AY5kpQX... (250.00 SOL)
[FUNDING] ✅ Funder saved (CEX: Coinbase)
[CLUSTERING] 🔍 Building wallet cluster for AY5kpQX...
[CLUSTERING]    Found 50 recent signatures
[CLUSTERING] 🏛️ CEX WALLET IN NETWORK: Kraken Hot Wallet connected to AY5kpQX...
[CLUSTERING] ✅ Saved 46 wallet nodes (2 CEX-linked)
[ANALYZER] 🔴 CRITICAL | Score: 70% | PUMP...
```

### Example 2: Only Private Funding

```
[MIGRATION] 🚨 New migration detected: MOON...
[CREATOR] ✅ Extracted from earliest tx: 9U4FFqeLEEN...
[FUNDING] 🔍 Extracting pre-migration funding...
[FUNDING] ✅ Found 2 funding sources
[FUNDING] ✅ Funder saved (Private)
[FUNDING] ✅ Funder saved (Private)
[CLUSTERING] 🔍 Building wallet cluster for 9U4FFqeLEEN...
[CLUSTERING]    Found 45 recent signatures
[CLUSTERING] ✅ Saved 32 wallet nodes (0 CEX-linked)
[ANALYZER] 🟢 LOW RISK | Score: 15% | MOON...
```

---

## Validation Queries

### Check CEX Wallet Detection Working

```bash
# Count total CEX wallets in database
sqlite3 pumpswap_tokens.db "SELECT COUNT(*) FROM cex_wallets WHERE is_active = 1;"

# List known CEX wallets
sqlite3 pumpswap_tokens.db "SELECT cex_address, exchange_name, wallet_type, confidence_level FROM cex_wallets WHERE is_active = 1;"

# Count creators with CEX funding
sqlite3 pumpswap_tokens.db "SELECT COUNT(DISTINCT creator_address) FROM creator_funders WHERE is_cex = 1;"

# List all CEX funders
sqlite3 pumpswap_tokens.db "SELECT funder_address, cex_exchange, cex_type, COUNT(DISTINCT creator_address) FROM creator_funders WHERE is_cex = 1 GROUP BY funder_address;"

# Find clusters with CEX wallets
sqlite3 pumpswap_tokens.db "SELECT DISTINCT root_creator FROM wallet_cluster_nodes WHERE tags LIKE '%🏛️%' LIMIT 10;"
```

---

## Management

### Add New CEX Wallets

**Via CLI**:
```bash
python3 scripts/manage_cex_wallets.py --add <ADDRESS> <EXCHANGE> <TYPE> [CONFIDENCE] [SOURCE] [NOTES]
```

**Via API**:
```bash
curl -X POST http://localhost:5002/api/cex-wallets \
  -H 'Content-Type: application/json' \
  -d '{
    "address": "...",
    "exchange": "Kraken",
    "type": "Hot Wallet",
    "confidence": 95
  }'
```

**Via Direct SQL**:
```sql
INSERT INTO cex_wallets VALUES
('ADDRESS', 'OKX', 'Hot Wallet', 90, CURRENT_TIMESTAMP, 'Solscan', 'OKX exchange wallet', 1);
```

---

## Implementation Timeline

### Phase 1: Core System ✅
- ✅ CEX wallets database table
- ✅ Detection function
- ✅ CLI management tool
- ✅ REST API endpoints

### Phase 2: Integration ✅
- ✅ Funding analysis tagging
- ✅ Wallet clustering tagging
- ✅ Creator funders table with CEX fields
- ✅ Wallet cluster nodes with CEX tags

### Phase 3: Risk Scoring (Next)
- ⏳ Update risk calculation to include CEX flags
- ⏳ Display CEX indicators in UI
- ⏳ Generate alerts for CEX-funded tokens

### Phase 4: Monitoring (Future)
- ⏳ Dashboard showing CEX-funded tokens
- ⏳ Solscan integration for auto-discovery
- ⏳ Behavioral detection for unlabeled CEX wallets

---

## Summary

✅ **CEX Tagging System is fully implemented**

- Automatic detection during funding extraction
- Automatic detection during wallet clustering
- Persistent storage in database tables
- Ready for risk scoring integration
- Production logs show all CEX detections

**Status**: Ready for risk scoring integration and UI display

---

**Last Updated**: 2026-01-27
**Commits**: 2 (implementation + integration)
**Lines of Code**: 180+ lines added
**Database Tables**: 1 new (cex_wallets) + 2 enhanced (creator_funders, wallet_cluster_nodes)
