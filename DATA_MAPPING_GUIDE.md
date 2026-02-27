# FLEX - Comprehensive Data Mapping Guide

## Executive Summary

**Flex** is a Solana token funding network analyzer that:
1. **Detects** new token launches on Pump.Fun
2. **Traces** funding sources (who funded creators, who funded funders, etc.)
3. **Identifies** coordinated funding networks and suspicious patterns
4. **Reports** risk scores and self-funding schemes

**Database**: `pumpswap_tokens.db` (SQLite)
**Report**: `COMPREHENSIVE_DATA_REPORT.xlsx` (8 sheets)

---

## Data Capture Flow

### PHASE 1: Token Launch Detection
**File**: `pumpfun_curve_listener.py` (line 1600+)
**Trigger**: Continuous WebSocket connection to Pump.Fun
**Function**: `websocket_listener_pump()`

```
Input:  Pump.Fun WebSocket events
↓
Detect: token.mint, token.created_tx_signature, token.creator
↓
Output: token_info, token_analysis tables
```

**Key Data Points**:
- `mint` - Token address
- `creator` - Creator wallet address
- `created_at` - Launch timestamp
- `create_tx_sig` - Transaction signature of token creation

---

### PHASE 2: Creator Funding Extraction
**File**: `realtime_creator_funding_extractor.py` (line 100+)
**Trigger**: Line 1728 of pumpfun_curve_listener.py (on new token detection)
**Function**: `extract_funding_for_new_token(creator_address, created_at, create_tx_sig, mint)`

```
Input:  creator_address, token mint
↓
Query:  All SOL inbound transfers to creator before token creation
        (using Helius API getSignaturesForAddress)
↓
Output: creator_funders table
        Rows: creator_address, funder_address, amount_sol, transaction_signature
```

**Key Relationships**:
- Links: **Creator ← Funder**
- Shows: Who gave SOL to the creator before they launched tokens

**Example**:
```
Creator: bwamJzztZsepfkteWRChggmXuiiCQvpLqPietdNfSXa
Funder #1: 5FxnJ2pDPaH4hzJ3qKX2mQkYx9wZqkQZxBoPMgJxRxK (0.5 SOL)
Funder #2: 7KmL9pQxRqY2wZjK4nP7mQrSt2xCbZvHxDeFqSvWxJ (0.3 SOL)
...942 total funders...
```

---

### PHASE 3: Funder Source Extraction
**File**: `funder_incoming_extractor.py` (line 200+)
**Trigger**: Line 1734 of pumpfun_curve_listener.py (after Phase 2 completes)
**Function**: `extract_for_creator()` / `extract_funder_transfers_async()`

```
Input:  Each funder_address from Phase 2
↓
Query:  All SOL inbound transfers to that funder
        (using Helius API getSignaturesForAddress)
↓
Output: funder_incoming_transfers table
        Rows: funder_address, sender_address, amount_sol, transaction_signature
```

**Key Relationships**:
- Links: **Funder ← Sender**
- Shows: Where each funder got their money from

**Example**:
```
Funder: 5FxnJ2pDPaH4hzJ3qKX2mQkYx9wZqkQZxBoPMgJxRxK
Sender #1: 3YzK7pLmQrS2tUvWxYzA9bC1dEfG3hIjKlMnOpQrSt (0.1 SOL)
Sender #2: 8NoPqRsT4uVwXyZaBcDeFgHiJkLmNoPqRsT2uVwXyZ (0.2 SOL)
```

---

### PHASE 4: Creator Outgoing Extraction
**File**: `creator_outgoing_extractor.py` (line 900+)
**Trigger**: Every 12 hours (via `run_forever(43200)` at line 1050)
**Schedule**: Runs continuously, scans 1000 creators per cycle
**Function**: `scan_once(concurrency=25)`

```
Input:  creator_address (from creator_funders)
↓
Query:  All SOL outbound transfers from creator
        (using Helius getSignaturesForAddress + enhanced parsing)
↓
Output: creator_outgoing_transfers table
        Rows: creator_address, recipient_address, amount_sol, transaction_signature
```

**Key Relationships**:
- Links: **Creator → Recipient**
- Shows: Who the creator sent SOL to after receiving funding

**Coverage**: 1413/1457 creators (97% coverage)
**Last Update**: Real-time (12-hour refresh cycle)

**Example**:
```
Creator: bwamJzztZsepfkteWRChggmXuiiCQvpLqPietdNfSXa
Recipient #1: 5FxnJ2pDPaH4hzJ3qKX2mQkYx9wZqkQZxBoPMgJxRxK (0.01 SOL)
Recipient #2: 7KmL9pQxRqY2wZjK4nP7mQrSt2xCbZvHxDeFqSvWxJ (0.02 SOL)
```

---

### PHASE 5: Funding Chain Building
**File**: `creator_outgoing_extractor.py` (line 400+)
**Trigger**: Auto-called after Phase 4 completes
**Functions**: 
  - `build_funding_chains_incremental()` - Links chains
  - `build_coordinated_edges_incremental()` - Identifies patterns

```
Input:  creator_outgoing_transfers + funder_incoming_transfers
↓
Link:   Creator → Recipient 
        Match: Recipient is a Funder in funding_chains
        So: Creator sent to Funder (who funds other creators)
↓
Output: funding_chains table
        Rows: source_creator, bridge_funder, target_creator, confidence
```

**Key Relationships**:
- Links: **Creator A → Funder → Creator B**
- Pattern: Creator A sends to a Funder, who then funds Creator B
- Confidence: 70-100 (higher = more likely coordinated)

**Example**:
```
Source Creator: 1stCreatorAddr (sends 0.01 SOL to Funder)
Bridge Funder: 5FxnJ2pDPaH4hzJ3qKX2mQkYx9wZqkQZxBoPMgJxRxK
Target Creator: 2ndCreatorAddr (receives 0.3 SOL from Funder)
Confidence: 85 (high confidence of coordination)
```

**Chain Types**:
- `CREATOR_TO_FUNDER_TO_CREATOR` - Creator sends to a funder who funds others
- `CREATOR_FUNDING_CHAIN` - Creator creates chains through intermediaries
- `CIRCULAR_FUNDING` - Money flows in circles (A→B→A pattern)

---

### PHASE 6: Network Clustering & Analysis
**File**: `cross_funding_network_analyzer.py` (line 500+)
**Trigger**: Line 1741 of pumpfun_curve_listener.py
**Function**: `analyze_atomic_networks()` / `update_network_clustering_async()`

```
Input:  creator_funders + funding_chains + creator_outgoing_transfers
↓
Analyze: Which creators consistently appear together?
         Which funders coordinate across creators?
         Which senders feed multiple funders?
↓
Output: creator_networks (atomic networks)
        creator_to_creator_networks (transfer chains)
        super_clusters (meta-level groups)
        creator_self_funding (self-funding detection)
```

**Key Outputs**:

1. **creator_networks** - Network membership
   ```
   network_name: "Network_1_5_creators"
   creator_address: Creator involved
   connected_creators: JSON list of other creators
   network_size: Number of creators
   network_risk_level: SUSPICIOUS / MODERATE / CLEAN
   ```

2. **funding_chains** - Coordinated patterns
   ```
   Confidence >= 70: High confidence coordination
   Confidence < 70: Weak signals
   ```

3. **super_clusters** - Cross-network groups
   ```
   super_cluster_id: Unique cluster ID
   network_count: How many networks in this cluster
   creator_count: Total creators involved
   risk_level: Overall risk assessment
   ```

4. **creator_self_funding** - Self-funding detection
   ```
   is_self_funding: 1 = Yes, 0 = No
   self_funding_percentage: % of funders that only fund this creator
   self_funding_intermediates: Count of intermediaries used
   ```

---

## Database Schema & Relationships

### Core Tables

#### 1. **creator_funders** (Direct Creator Funding)
```sql
creator_address        TEXT   - Token creator
funder_address         TEXT   - Who funded them
amount_sol             REAL   - Amount sent
transaction_signature  TEXT   - TX hash
block_time            INT    - When
first_detected_at     TIMESTAMP
is_cex                INT    - 0/1
fully_analyzed        INT    - 0/1
```
**Purpose**: Links creator ← funder
**Size**: ~300K rows
**Key Index**: creator_address, funder_address

#### 2. **funder_incoming_transfers** (Funder Sources)
```sql
funder_address        TEXT   - The funder
sender_address        TEXT   - Who sent to them
amount_sol            REAL   - Amount
transaction_signature TEXT   - TX hash
block_time           INT    - When
```
**Purpose**: Links funder ← sender
**Size**: ~400K rows
**Key Index**: funder_address, sender_address

#### 3. **creator_outgoing_transfers** (Creator Outputs)
```sql
creator_address       TEXT   - The creator
recipient_address     TEXT   - Who received
amount_sol            REAL   - Amount
transaction_signature TEXT   - TX hash (PK)
slot                  INT    - Blockchain slot
block_time           INT    - When
recipient_type        TEXT   - 'unknown', 'cex', 'infra'
is_cex               INT    - 0/1
```
**Purpose**: Links creator → recipient
**Size**: ~40K rows
**Key Index**: creator_address, recipient_address

#### 4. **funding_chains** (Coordinated Chains)
```sql
chain_id             INT         - PK
chain_type           TEXT        - Type of chain
source_creator       TEXT        - Creator A
bridge_funder        TEXT        - Funder in middle
target_creator       TEXT        - Creator B
source_tx            TEXT        - TX from A to Funder
bridge_to_target_amount_sol REAL - SOL from Funder to B
source_to_bridge_amount_sol REAL - SOL from A to Funder
confidence           INT         - 0-100
created_at          TIMESTAMP    - When discovered
```
**Purpose**: Shows coordinated funding patterns
**Size**: ~400 rows (confidence >= 70)
**Key Insight**: Creator A sends to Funder, Funder sends to Creator B

#### 5. **creator_networks** (Network Membership)
```sql
creator_address      TEXT   - Creator
network_name         TEXT   - Network identifier
connected_creators   JSON   - List of connected creators
network_size         INT    - Size
network_risk_level   TEXT   - Risk assessment
```
**Purpose**: Groups coordinated creators
**Size**: ~1400 rows
**Key Insight**: Creators in same network likely coordinated

#### 6. **coordinated_creator_edges** (Creator Relationships)
```sql
creator_a            TEXT - Creator 1
creator_b            TEXT - Creator 2
bridge_funder        TEXT - Funder linking them
confidence           INT  - 0-100
```
**Purpose**: Direct creator-to-creator coordination
**Size**: ~1000 rows

#### 7. **super_clusters** (Meta-Level Groups)
```sql
super_cluster_id     TEXT    - Unique ID
network_count        INT     - Networks in cluster
creator_count        INT     - Creators in cluster
root_addresses       TEXT    - Key wallets
risk_level          TEXT    - SUSPICIOUS / MODERATE / CLEAN
```
**Purpose**: Cross-network coordination detection
**Size**: ~50 rows
**Key Insight**: Multiple networks controlled by same actors

#### 8. **creator_self_funding** (Self-Funding Detection)
```sql
creator_address           TEXT - Creator
is_self_funding           INT  - 1=Yes, 0=No
self_funding_percentage   REAL - % of funders only fund this creator
self_funding_intermediates INT - Count of intermediary wallets
total_funders             INT  - Total unique funders
```
**Purpose**: Identify fake "support" schemes
**Example**: Creator sends to 942 intermediate addresses, who all send back to creator, who launches 23 tokens

---

## Data Relationships Map

```
SENDER
  ↓ (sends SOL to)
FUNDER
  ↓ (funds)
CREATOR
  ↓ (sends SOL to)
RECIPIENT

Key Insights:
1. If Recipient is a Funder: Creator → Funder → Creator (coordinated)
2. If Recipient is a Sender: Money cycles back (circular funding)
3. If Recipient is a CEX: Creator withdrawing to exchange (not suspicious)
4. If Recipient is Infrastructure: Creator paying fees (not suspicious)

Network Detection:
- Multiple Creators funded by same Funder = Network
- Multiple Funders funded by same Sender = Network
- Multiple Networks controlled by same Senders = Super Cluster
```

---

## Address Classifications

### By Role
- **Creator**: Launched at least one token (1457 total)
- **Funder**: Funded at least one creator (10K+)
- **Sender**: Sent SOL to at least one funder (10K+)
- **Recipient**: Received SOL from at least one creator (1000s)

### By Type
- **CEX**: Known exchange wallet (e.g., Coinbase, Kraken)
- **Infrastructure**: Known utility wallet (e.g., Padre Fee Wallet)
- **Unknown**: Regular wallet (needs further investigation)

### By Behavior
- **Self-Funding**: Creator who only receives from their own intermediaries
- **Coordinated**: Creator who appears in multiple networks
- **Independent**: Creator with unique funding sources
- **Suspicious**: High-risk patterns detected

---

## Key Statistics

| Metric | Value |
|--------|-------|
| Total Addresses | ~11K |
| Creators | 1,373 |
| Funders | 10K+ |
| Senders | 10K+ |
| Creator → Funder edges | 300K |
| Funder ← Sender edges | 400K |
| Creator → Recipient edges | 40K |
| Funding Chains (conf >= 70) | 204 |
| Creator Networks | 777 |
| Super Clusters | 500 |
| Total SOL Tracked | ~10K SOL |
| Scan Coverage | 97% (1413/1457 creators) |

---

## Usage Examples

### Example 1: Find if a Creator is Self-Funded
```
1. Look up creator_address in creator_self_funding
2. Check is_self_funding = 1
3. Check self_funding_intermediates count
4. If > 100: Likely fake support scheme
```

### Example 2: Find All Creators Funded by a Specific Funder
```sql
SELECT DISTINCT creator_address 
FROM creator_funders 
WHERE funder_address = '5FxnJ2pDPaH4hzJ3qKX2mQkYx9wZqkQZxBoPMgJxRxK'
ORDER BY amount_sol DESC
```

### Example 3: Find Coordinated Creator Pairs
```sql
SELECT creator_a, creator_b, bridge_funder, confidence
FROM coordinated_creator_edges
WHERE confidence >= 80
ORDER BY confidence DESC
```

### Example 4: Find Funding Chains
```sql
SELECT source_creator, bridge_funder, target_creator, confidence
FROM funding_chains
WHERE confidence >= 85
AND chain_type = 'CREATOR_TO_FUNDER_TO_CREATOR'
```

### Example 5: Analyze a Network
```sql
SELECT creator_address, network_name, network_size, network_risk_level
FROM creator_networks
WHERE network_name = 'Network_1_5_creators'
```

---

## Data Quality Notes

- **creator_funders**: Complete (Phase 2 extraction)
- **funder_incoming_transfers**: Mostly complete (Phase 3 extraction)
- **creator_outgoing_transfers**: 97% coverage (Phase 4 extraction)
  - Missing: 44 creators with no funding history
  - Update Frequency: Every 12 hours
  - Last Full Scan: 2 minutes after 100% creator coverage achieved

- **funding_chains**: Only high-confidence (70+) patterns included
- **Networks**: Updated incrementally as new data arrives
- **Super Clusters**: Updated after network changes

---

## Report Navigation

**COMPREHENSIVE_DATA_REPORT.xlsx** contains:

| Sheet | Contents |
|-------|----------|
| 00_Summary | Overview & statistics |
| 01_Data_Capture_Flow | Phase-by-phase explanation |
| 02_Creators | All creators with funding data |
| 03_Funders | All funders with targets |
| 04_Senders | All senders with reach |
| 05_Networks | Network membership |
| 06_Funding_Chains | High-confidence chains |
| 07_Super_Clusters | Meta-clusters |
| 08_Database_Schema | Table definitions |

---

**Generated**: February 26, 2026
**System**: Flex - Token Funding Network Analyzer
**Database**: pumpswap_tokens.db (SQLite)
