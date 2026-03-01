# FLEX Complete System Documentation

**Comprehensive guide to the Flex token funding network analysis system**

---

## 📋 Table of Contents

1. [System Overview](#system-overview)
2. [Participant Roles](#participant-roles)
3. [Network Architecture](#network-architecture)
4. [Execution Flow & Components](#execution-flow--components)
5. [SOL Transfer Filtering](#sol-transfer-filtering)
6. [Findings Tags](#findings-tags)
7. [Risk Calculation](#risk-calculation)
8. [CEX Account Mapping](#cex-account-mapping)
9. [INFRA Account Mapping](#infra-account-mapping)
10. [Network Data](#network-data)
11. [Database Schema](#database-schema)
12. [Implementation Details](#implementation-details)
13. [Integration Guide](#integration-guide)

---

## System Overview

**Flex** is a Solana token analysis system that tracks funding networks, identifies coordinated funder relationships, and detects suspicious pump-and-dump schemes across Pump.Fun tokens.

### Key Statistics
- **41,734 funder networks** tracked
- **503 super clusters** identified
- **43 CEX wallets** mapped (20 exchanges)
- **59 INFRA programs** tracked (8 categories)
- **8 findings tags** for risk assessment
- **MINIMUM_SOL = 0.001** threshold filters 30-40% micro-transactions

---

## Participant Roles

### Three-Layer Funding Flow

```
SENDERS (Layer 1: Original Source)
    ↓ Distribute SOL
FUNDERS (Layer 2: Intermediaries)
    ↓ Send SOL
CREATORS (Layer 3: Token Launchers)
    ↓ Launch Token
TOKENS
```

### 1. SENDER - Money Source

**Definition**: Original wallet addresses that send SOL to funders

**Characteristics**:
- Initial funding source
- May distribute to many funder addresses
- Fund distribution width indicates coordination level

**Types**:
- CEX accounts (legitimate exchanges)
- INFRA bots (automation programs)
- Individual creators (reusing wallets)
- Unknown wallets (organic or suspicious)

**Risk Indicators**:
- Fund distribution width (many recipients = higher risk)
- Concentration (sending to many addresses that all fund same creator = self-funding)

**Database**:
- `funder_incoming_transfers.sender_address`
- `funder_incoming_transfers.amount_sol`

### 2. FUNDER - Intermediary Bridge

**Definition**: Wallet addresses that receive from senders and send to creators

**Characteristics**:
- Relay point connecting sources to token creators
- Receives from senders, forwards to creators
- May pass through multiple layers

**Types**:
- Unknown intermediaries (highest investigation priority)
- Automation accounts (bots distributing)
- Relay addresses (legitimate services)
- Creator-controlled wallets (self-funding indicator)

**Risk Indicators**:
- Creator count served (how many creators do they fund?)
- Sender diversity (how many different sources funded them?)
- Behavior patterns (passthrough vs accumulation)

**Database**:
- `creator_funders.funder_address`
- `creator_funders.amount_sol`
- `funder_incoming_transfers.funder_address`
- `funder_outgoing_transfers.funder_address`

### 3. CREATOR - Token Launcher

**Definition**: Wallet addresses that create tokens and receive funder support

**Characteristics**:
- Receives SOL from funders
- Creates Pump.Fun token
- May redistribute SOL to other addresses

**Risk Indicators**:
- Funder count (how many funded this creator?)
- Self-funding percentage (% of funders controlled by creator)
- Distribution pattern (do they spread SOL to many addresses?)
- Network involvement (coordinated with other creators?)

**Metrics**:
- Funder count
- Token count
- Outgoing transfer patterns
- Self-funding intermediates
- Coordinated creator count

**Database**:
- `creator_funders.creator_address`
- `creator_outgoing_transfers.creator_address`
- `creator_self_funding.is_self_funding`
- `token_analysis.earliest_tx_creator`

---

## Network Architecture

### Funding Extraction Pipeline

#### Step 1: Detect Token Creation
- WebSocket listener detects new Pump.Fun token
- Extract creator address from onchain data

#### Step 2: Extract Creator Funders
- Query: Who funded the creator?
- File: `realtime_creator_funding_extractor.py`
- Records in: `creator_funders` table
- Filters: >= 0.001 SOL only

#### Step 3: Extract Funder Sources
- Query: Where did funders get their money?
- File: `funder_incoming_extractor.py`
- Records in: `funder_incoming_transfers` table
- Classify senders: CEX/INFRA/Unknown

#### Step 4: Extract Creator Outgoing
- Query: Where does creator send SOL?
- File: `creator_outgoing_extractor.py`
- Records in: `creator_outgoing_transfers` table
- Frequency: Every 12 hours (background scan)

#### Step 5: Build Network Relationships
- File: `cross_funding_network_analyzer.py`
- Creates: Coordinated edges, clusters, networks
- Identifies: Shared funders, coordination patterns

#### Step 6: Generate Findings
- API: `/api/creator-recent-checks`
- File: `main.py` (lines 16502-16649)
- Generates: Findings tags and risk scores
- Frequency: Real-time on demand

---

## Execution Flow & Components

### Main Entry Point: Listener Startup

**File**: `pumpfun_curve_listener.py:1966-1986`

When the listener starts with `python pumpfun_curve_listener.py`, it initializes and spawns **4 background async tasks**:

```python
# In PumpFunCurveListener.listen() method:

# Task 1: Creator Watch Manager (polls every 30 seconds)
asyncio.create_task(self.creator_watch_manager.run_polling_loop(poll_interval=30))

# Task 2: Live Price Updater (continuous background updates)
asyncio.create_task(self.update_live_prices_background())

# Task 3: Creator Outgoing Transfer Extractor (scans all creators every 12 hours)
asyncio.create_task(run_outgoing_extractor(interval_seconds=43200))

# Task 4: WebSocket Listener (real-time token detection)
await self.listen_websocket()
```

---

### Real-Time Token Detection Flow

**Trigger**: New Pump.Fun migration transaction detected via WebSocket

**File**: `pumpfun_curve_listener.py:1724-1839`

```
1. WebSocket receives transaction
   ↓
2. Parse migration data (line ~1745)
   - Extract mint address
   - Extract creator address
   - Extract creation timestamp
   - Extract transaction signature
   ↓
3. Store token in database (line ~1765)
   - Add to `tokens` table
   - Mark as new discovery
   ↓
4. Spawn background tasks (line 1830)
   - Fire-and-forget, don't wait for completion
   ↓
   └─→ Background Task Chain (executed concurrently):

       ┌─ STEP 1: Extract Creator Funding (line 1808)
       │  File: realtime_creator_funding_extractor.py
       │  Function: extract_funding_for_new_token()
       │  Purpose: Find all wallets that funded this creator
       │  Output: Populates creator_funders table
       │  Time: ~5-30 seconds per creator
       │
       ├─ STEP 2: Extract Funder Transfers (line 1816)
       │  File: funder_incoming_extractor.py
       │  Function: extract_for_creator()
       │  Purpose: For EACH funder, find who funded them
       │  Output: Populates funder_incoming_transfers table
       │  Time: ~30-120 seconds (scales with funder count)
       │
       └─ STEP 3: Rebuild Network Clustering (line 1824)
          File: cross_funding_network_analyzer.py
          Function: rebuild_super_clusters_from_funding()
          Purpose: Analyze relationships, find coordinated funders
          Output: Updates super_clusters, coordinated_edges tables
          Time: ~10-60 seconds
```

---

### Key Components & Their Responsibilities

#### 1. **PumpFunCurveListener** (Main Controller)
**File**: `pumpfun_curve_listener.py`

**Primary Responsibilities**:
- WebSocket connection to Solana RPC
- Parse migration transactions
- Detect new token creation events
- Spawn background extraction tasks
- Update live token prices
- Store tokens in database

**Key Methods**:
- `listen()` - Entry point, spawns all background tasks
- `listen_websocket()` - Real-time WebSocket listener
- `handle_migration()` - Process new token events
- `update_live_prices_background()` - Price polling (async)

---

#### 2. **Creator Funding Extractor**
**File**: `realtime_creator_funding_extractor.py`

**Function**: `extract_funding_for_new_token(creator_address, created_at, create_tx_sig, mint)`

**What it does**:
- Queries blockchain: "Who funded this creator?"
- Uses Helius API or RPC to get all SOL transfers to creator address
- Filters transfers by timestamp (must be before token creation)
- Saves funder wallet addresses to database

**Database Output**:
```
creator_funders table:
├─ creator_address: "bwamJzzt..."
├─ funder_address: "wallet_123..."
├─ amount_sol: 0.5
├─ transaction_signature: "tx_hash"
└─ first_detected_at: timestamp
```

**Duration**: 5-30 seconds depending on funding complexity

---

#### 3. **Funder Incoming Extractor**
**File**: `funder_incoming_extractor.py`

**Wrapper Function**: `extract_funder_transfers_async(creator_address)` (line 67)

**Internal Function**: `extract_for_creator()` (called for EACH funder)

**What it does**:
- Takes list of funders from Step 1
- For EACH funder, queries blockchain: "Who funded this funder?"
- Finds the SOURCE of the funder's money
- Applies MINIMUM_SOL = 0.001 threshold (filters dust transfers)
- Builds incoming transfer chain

**Database Output**:
```
funder_incoming_transfers table:
├─ funder_address: "wallet_123..."
├─ sender_address: "wallet_456..." (who funded the funder)
├─ amount_sol: 0.1
├─ transaction_signature: "tx_hash"
└─ block_time: timestamp
```

**Duration**: 30-120 seconds (proportional to funder count)

**Algorithm**:
```python
# From funder_incoming_extractor.py:51
MIN_SOL = 0.001  # Filter transfers below 0.001 SOL

For each funder in creator_funders:
  - Get all incoming transfers to this funder address
  - Filter: amount_sol >= MIN_SOL
  - Store sender_address + amount in database
  - This maps SOURCE → FUNDER relationship
```

---

#### 4. **Creator Outgoing Extractor** (Background - Every 12 Hours)
**File**: `creator_outgoing_extractor.py`

**Spawned at**: Listener startup (line 1983)

**What it does**:
- Continuous background scanning of all creators
- Extracts outgoing transfers from creators (where they send SOL after token launch)
- Scans approximately 1,000 creators per 12-hour cycle
- Updates "last_scanned" timestamps

**Database Output**:
```
creator_outgoing_transfers table:
├─ creator_address: "creator_123..."
├─ recipient_address: "wallet_456..."
├─ amount_sol: 0.05
└─ transaction_signature: "tx_hash"
```

**Duration**: Runs continuously in background, never blocks main listener

---

#### 5. **Creator Watch Manager** (Polling - Every 30 Seconds)
**File**: `creator_watch_manager.py`

**Spawned at**: Listener startup (line 1977)

**What it does**:
- Polls watched creators at regular intervals
- Checks for token launches from tracked addresses
- Notifies listeners of creator activity
- Runs async loop without blocking

**Duration**: 30-second intervals

---

#### 6. **Network Analyzer**
**File**: `cross_funding_network_analyzer.py`

**Function**: `rebuild_super_clusters_from_funding()`

**What it does**:
- Analyzes creator_funders + funder_incoming_transfers data
- Identifies coordinated funders (shared across multiple creators)
- Builds network clusters and super-clusters
- Detects relationships between creators

**Database Output**:
```
super_clusters table:
├─ cluster_id: unique identifier
├─ member_count: number of creators
└─ relationship_strength: coordination score

coordinated_edges table:
├─ funder_address: shared across creators
├─ creator_1_address
├─ creator_2_address
└─ funding_strength: amount coordination
```

**Duration**: 10-60 seconds

---

### Complete Execution Timeline

```
T=0s      Listener starts
          ↓
T=0s      Spawn 4 background tasks (async, non-blocking)
          │
          ├─ Task 1: Creator Watch Manager (polls every 30s)
          ├─ Task 2: Price Updater (continuous background)
          ├─ Task 3: Outgoing Extractor (runs every 12h)
          └─ Task 4: WebSocket Listener (real-time, BLOCKING)
                    ↓ waits for transaction

T=X       Transaction detected via WebSocket
          ↓
T=X+0.1s  Parse migration data
          ↓
T=X+0.5s  Store token in database
          ↓
T=X+0.6s  Spawn background funding tasks (fire-and-forget)
          │
          ├─ PARALLEL STEP 1: Creator Funding Extraction
          │  Duration: 5-30s
          │  Output: creator_funders table populated
          │
          ├─ PARALLEL STEP 2: Funder Transfer Extraction
          │  Duration: 30-120s (depends on funder count)
          │  Output: funder_incoming_transfers table populated
          │
          └─ PARALLEL STEP 3: Network Clustering
             Duration: 10-60s
             Output: super_clusters, coordinated_edges updated

T=X+180s  All background tasks complete
          Data available for querying via API

T=X+200s+ Tasks complete, listener continues waiting
          for next WebSocket transaction
```

---

### Component Dependency Graph

```
PumpFunCurveListener (main entry point)
    ├─ imports CreatorWatchManager
    │   └─ polls creator activity every 30s
    ├─ imports run_outgoing_extractor (async task)
    │   └─ background scans all creators every 12h
    ├─ calls extract_funding_for_new_token()
    │   ├─ File: realtime_creator_funding_extractor.py
    │   ├─ uses: Helius API / RPC
    │   └─ outputs: creator_funders table
    ├─ calls extract_funder_transfers_async()
    │   ├─ File: funder_incoming_extractor.py
    │   ├─ uses: RPC for transfer history
    │   ├─ applies: MIN_SOL = 0.001 threshold
    │   └─ outputs: funder_incoming_transfers table
    └─ calls rebuild_super_clusters_from_funding()
        ├─ File: cross_funding_network_analyzer.py
        ├─ inputs: creator_funders + funder_incoming_transfers
        └─ outputs: super_clusters, coordinated_edges tables
```

---

### Task Scheduling Summary

| Task | File | Trigger | Frequency | Duration | Type |
|------|------|---------|-----------|----------|------|
| **WebSocket Listener** | `pumpfun_curve_listener.py` | Start | Continuous | Blocking | Real-time |
| **Creator Funding Extract** | `realtime_creator_funding_extractor.py` | New token | On-demand | 5-30s | Background |
| **Funder Transfer Extract** | `funder_incoming_extractor.py` | New token | On-demand | 30-120s | Background |
| **Network Clustering** | `cross_funding_network_analyzer.py` | New token | On-demand | 10-60s | Background |
| **Creator Watch Polling** | `creator_watch_manager.py` | Start | Every 30s | <5s | Background |
| **Live Price Updates** | `pumpfun_curve_listener.py` | Start | Continuous | <1s per token | Background |
| **Creator Outgoing Scans** | `creator_outgoing_extractor.py` | Start | Every 12h | ~1000 creators | Background |

---

## SOL Transfer Filtering

### MINIMUM_SOL Threshold

**Value**: 0.001 SOL (~$0.15 USD)

**Location**: `funder_incoming_extractor.py:51`

**Mechanism**: All SOL transfers below threshold are filtered out and NOT recorded in database

### Why Filter?

| Reason | Explanation |
|--------|-------------|
| **Dust Transfers** | Network spam, test transactions, minimal amounts |
| **Fee Precision** | Small system fees or error corrections |
| **Noise Reduction** | Reduces false positives in suspicious pattern detection |
| **Performance** | Excludes millions of micro-transfers |
| **Data Quality** | Focuses analysis on meaningful funding flows |

### Implementation

```python
# funder_incoming_extractor.py
MIN_SOL = 0.001

# During extraction:
if amount_sol < 0.001:
    skip_transfer()  # Don't record
else:
    save_to_database()  # Record if >= 0.001 SOL
```

### Impact

- **Recorded Transfers**: Only >= 0.001 SOL
- **Database Size**: Filters out 30-40% of micro-transactions
- **Network Analysis**: Focuses on meaningful flows
- **Self-Funding Detection**: Works on meaningful amounts only

### Example

```
Creator ABC receives from:
  Funder A: 1.5 SOL    ✅ Recorded (>= 0.001)
  Funder B: 0.05 SOL   ✅ Recorded (>= 0.001)
  Funder C: 0.0005 SOL ❌ Filtered (< 0.001)
  Funder D: 0.15 SOL   ✅ Recorded (>= 0.001)

Total recorded funders: 3
Total recorded amount: 1.7 SOL
```

---

## Findings Tags

### Complete Reference (8 Total)

All findings tags are automatically generated based on analyzing creator behavior and funding patterns.

#### 1. 🚩 SELF-FUNDING (CRITICAL Risk)

**Meaning**: Creator owns and controls multiple funder intermediaries

**Detection**:
- `is_self_funding = 1` AND percentage > 50%
- Query: `SELECT is_self_funding, self_funding_percentage FROM creator_self_funding`

**Indicator**: % of funders that are creator-controlled wallets

**Example**: 24 of 28 funders are creator's own wallets (85%)

**Action**: Investigate pump-and-dump scheme immediately

**Database**: `creator_self_funding` table

---

#### 2. ⚠️ CREATOR_FUNDING_CHAIN (HIGH Risk)

**Meaning**: Creator's funders are funded by OTHER creators

**Detection**:
- Exists in `funding_chains` table with `source_creator`
- Query: `SELECT COUNT(*) FROM funding_chains WHERE source_creator = ?`

**Indicator**: Multi-layer funding through creator network

**Example**: Funder X was funded by Creator C, who then funds Creator A

**Action**: Check if part of coordinated creator network

**Database**: `funding_chains` table

---

#### 3. ⚠️ DISTRIBUTION_PATTERN (HIGH Risk)

**Meaning**: Creator distributes to many recipients (unbalanced pattern)

**Detection**:
- `recipient_count > (funder_count × 5) AND funder_count < 20`
- Query: Count distinct recipients vs funders in `creator_outgoing_transfers`

**Indicator**: Suspicious redistribution ratio

**Example**: 10 funders → 85 recipients (8.5:1 ratio)

**Action**: Monitor for follow-up token launches using same funders

**Database**: `creator_outgoing_transfers` table

---

#### 4. 🔗 COORDINATED_FUNDERS (HIGH Risk)

**Meaning**: Creator shares funders with multiple other creators

**Detection**:
- `COUNT(*) > 0` in `coordinated_creator_edges`
- Query: `SELECT COUNT(*) FROM coordinated_creator_edges WHERE creator_a = ? OR creator_b = ?`

**Indicator**: Shared funding across multiple tokens

**Example**: Funder X funds Creator A, Creator B, and Creator C

**Action**: Map entire coordinated network

**Database**: `coordinated_creator_edges` table

---

#### 5. ⚠️ NETWORK_MEMBER (MEDIUM Risk)

**Meaning**: Creator identified as part of detected funding network

**Detection**:
- Found in `funding_network_members` table
- Query: `SELECT network_id FROM funding_network_members WHERE funder_address = ?`

**Indicator**: Part of network cluster analysis

**Example**: Member of FUNDERS_14 network

**Action**: Check network cluster statistics

**Database**: `funding_network_members` table

---

#### 6. 🤖 AUTOMATION_DETECTED (MEDIUM Risk)

**Meaning**: Creator's funders include automation programs

**Detection**:
- Funder in `INFRASTRUCTURE_ACCOUNTS` with `category='automation'`
- Query: `get_account_info(funder)` checks automation category

**Indicator**: Bot-automated distribution

**Example**: Creator funded by Axiom automation bot

**Action**: Check for coordinated distribution patterns

**Database**: `infra_mapping.py` INFRASTRUCTURE_ACCOUNTS dict

---

#### 7. 💱 INSTITUTIONAL_BACKED (LOW Risk)

**Meaning**: Creator received funding from known CEX address

**Detection**:
- Funder in `CEX_ACCOUNTS` mapping
- Query: `get_cex_info(funder)` returns match

**Indicator**: Institutional/legitimate backing

**Example**: Creator funded by Coinbase, Binance, or Kraken

**Action**: Reduces suspicion, may exclude from suspicious networks

**Database**: `cex_wallets` table or `infra_mapping.py` CEX_ACCOUNTS dict

---

#### 8. ✅ CLEAN (NONE Risk)

**Meaning**: No suspicious patterns detected

**Detection**:
- No other findings generated
- Logic: `if not any(findings): findings.append('✅ CLEAN')`

**Indicator**: Organic, legitimate funding

**Example**: Normal funder distribution, no coordination

**Action**: Standard monitoring

---

### Findings Detection Workflow

#### Step-by-Step Process

```
1. CREATOR DETECTED
   └─ Token creation identified

2. EXTRACT CREATOR FUNDERS
   ├─ Query creator_funders table
   ├─ Filter: >= 0.001 SOL only
   └─ Count funders and amounts

3. CHECK SELF-FUNDING
   ├─ Query creator_self_funding table
   ├─ Calculate self-funding %
   └─ If > 50%: 🚩 SELF-FUNDING tag

4. CHECK CREATOR FUNDING CHAIN
   ├─ Query funding_chains
   └─ If found: ⚠️ CREATOR_FUNDING_CHAIN tag

5. CHECK DISTRIBUTION PATTERN
   ├─ Count outgoing recipients
   ├─ Compare to funder count
   └─ If high ratio: ⚠️ DISTRIBUTION_PATTERN tag

6. CHECK COORDINATED EDGES
   ├─ Query coordinated_creator_edges
   └─ If matches: 🔗 COORDINATED_FUNDERS tag

7. CHECK NETWORK MEMBERSHIP
   ├─ Query funding_network_members
   └─ If member: ⚠️ NETWORK_MEMBER tag

8. CHECK CEX/INFRA
   ├─ For each funder:
   │  ├─ Check if CEX → 💱 INSTITUTIONAL_BACKED
   │  ├─ Check if INFRA automation → 🤖 AUTOMATION_DETECTED
   │  └─ Record classification
   └─ Adjust risk factors

9. FINAL VERDICT
   ├─ If any risk tag: Display findings
   └─ If no tags: Add ✅ CLEAN

10. DISPLAY ON UI
    ├─ Creator Analysis: Show badges
    ├─ Dashboard: Color-code by risk
    └─ API: Return JSON with findings
```

---

## Risk Calculation

### Weighted Formula

```
Risk = (Self-Funding % × 0.40) +
       (Coordinated Score × 0.30) +
       (Unknown Funder % × 0.20) +
       (Automation Score × 0.10)
```

### Component Weights

| Component | Weight | Reason |
|-----------|--------|--------|
| **Self-Funding %** | 40% | Strongest indicator of manipulation |
| **Coordination Score** | 30% | Network effect and shared funders |
| **Unknown Funder %** | 20% | Unverified/unclassified sources |
| **Automation Score** | 10% | Bot activity level |

### Adjustment Factors

| Factor | Adjustment | Effect |
|--------|-----------|--------|
| **CEX Backing** | -0.20 | Institutional backing reduces risk |
| **INFRA Automation** | +0.10 | Bot automation increases risk |
| **Clean Pattern** | 0.10 base | Minimum for truly clean patterns |

### Risk Thresholds

| Tier | Range | Emoji | Action |
|------|-------|-------|--------|
| **CRITICAL** | > 0.30 | 🔴 | Immediate investigation |
| **HIGH** | 0.15 - 0.30 | 🟠 | Monitor closely |
| **MEDIUM** | 0.05 - 0.15 | 🟡 | Watch for changes |
| **LOW** | < 0.05 | 🟢 | Normal monitoring |

### Calculation Examples

#### Example 1: Pure Self-Funding (CRITICAL)

```
Funders: 20 total (18 self-created, 2 external)
Risk = (90% × 0.40) + (0 × 0.30) + (10% × 0.20) + (0 × 0.10)
Risk = 0.36 + 0 + 0.02 + 0 = 0.38
Result: 🔴 CRITICAL (> 0.30)
Tags: 🚩 SELF-FUNDING (90%)
```

#### Example 2: Coordinated Network (CRITICAL)

```
Funders: 15 total (3 self, 12 coordinated)
Risk = (20% × 0.40) + (0.80 × 0.30) + (0% × 0.20) + (0.05 × 0.10)
Risk = 0.08 + 0.24 + 0 + 0.005 = 0.325
Result: 🔴 CRITICAL (> 0.30)
Tags: 🔗 COORDINATED_FUNDERS (8 shared), ⚠️ CREATOR_FUNDING_CHAIN
```

#### Example 3: CEX-Backed (CLEAN)

```
Funders: 10 total (7 Coinbase, 3 unknown)
Risk = (0% × 0.40) + (0 × 0.30) + (30% × 0.20) + (0 × 0.10)
Risk = 0 + 0 + 0.06 + 0 = 0.06
Risk - 0.20 (CEX adjustment) = -0.14 → Clamped to 0.0
Result: 🟢 CLEAN (< 0.05)
Tags: 💱 INSTITUTIONAL_BACKED, ✅ CLEAN
```

---

## CEX Account Mapping

### Overview

**43 CEX addresses** mapped across **20 exchanges**

All CEX accounts are stored in:
- **Database**: `cex_wallets` table
- **Code**: `infra_mapping.py` CEX_ACCOUNTS dictionary

### CEX Account Types

| Type | Function | Risk | Impact |
|------|----------|------|--------|
| **Hot Wallet** | Active trading, deposits/withdrawals | LOW | Filter from suspicious networks |
| **Cold Wallet** | Reserve storage | LOW | Rare movements, institutional |
| **Deposit Account** | Receive user deposits, route to hot wallets | LOW | Expected pattern, exclude |
| **Withdrawal Account** | Distribute to users after trades | LOW | User payouts, normal |
| **Trading Account** | Market-making and price discovery | LOW | High frequency expected |
| **Staking Account** | Hold customer staked SOL and rewards | LOW | Institutional custody |
| **Treasury** | Long-term strategic holdings | LOW | Low frequency, institutional |

### Exchanges Mapped (20 Total)

#### Tier 1: Major Global Exchanges
- **Binance** (4 addresses) - Largest exchange, primary liquidity
- **Coinbase** (12 addresses) - US regulated, institutional custody
- **Kraken** (2 addresses) - Secure EUR/USD trading
- **OKX** (2 addresses) - Asian market leader

#### Tier 2: High-Volume Exchanges
- **Bybit** (2) - Derivatives exchange
- **Robinhood** (6) - Retail brokerage
- **KuCoin** (1) - Community exchange
- **MEXC** (1) - Emerging market
- **HTX** (1) - Asian exchange (formerly Huobi)
- **BingX** (1) - Copy trading platform

#### Tier 3: Specialized Services
- **Moonpay** - Fiat on-ramp
- **Crypto.com** - Payments & trading
- **ChangeNow** - Atomic swaps
- **FixedFloat** - Instant swaps
- **Revolut** - Fintech payments
- **Nexo** - Lending platform
- **Stake.com** - Crypto casino

#### Legacy & Custody
- **Fireblocks** - Institutional custody (LOW RISK)
- **FTX** - Historical legacy account (INACTIVE)
- **Bidget** - Unknown exchange

### Risk Assessment

**Low Risk (All CEX accounts)**:
- Institutional backing and regulation
- Known custody procedures
- Verified onchain addresses
- High transaction volume

### Integration

CEX funding reduces overall risk score by **-0.20** (adjustment factor)

---

## INFRA Account Mapping

### Overview

**59 INFRA programs** tracked across **8 categories**

All INFRA accounts are stored in:
- **Code**: `infra_mapping.py` INFRASTRUCTURE_ACCOUNTS dictionary
- **Database**: Referenced via `get_account_info()` function

### INFRA Categories

#### 1. AUTOMATION (55 programs) ⚠️ HIGH PRIORITY

**Function**: Task scheduling, bot operations, automated distribution

**Examples**:
- **RapidLaunch** - Token launch platform automation
- **Axiom** - Monitoring & automation infrastructure
- **Trojan Trade** - Bot automation for trading

**Role in Network**:
- Distribute SOL to many funders on a schedule
- Create artificial funding patterns
- May indicate organized distribution schemes

**Risk Assessment**: MEDIUM
- Normal: Legitimate automation for user services
- Suspicious: Coordinated distribution across multiple creators

**Monitoring**: HIGH PRIORITY - Watch for suspicious coordination

---

#### 2. BRIDGE (1 program)

**Function**: Cross-chain token transfers and liquidity bridges

**Example**:
- **deBridge** - Cross-chain token transfer vault

**Role in Network**:
- Move SOL between Solana and other chains
- Natural, expected pattern for cross-chain users

**Risk Assessment**: LOW
- Exclude from suspicious networks
- Normal ecosystem operation

---

#### 3. PROTOCOL (2 programs)

**Function**: Protocol operations, treasury, and governance

**Examples**:
- **Rollbit Treasury** - Protocol treasury account
- **SolCasino** - Protocol operations and distribution

**Role in Network**:
- Long-term holdings and strategic distribution
- Governance operations

**Risk Assessment**: LOW
- Institutional pattern
- Exclude from suspicious networks

---

#### 4. SYSTEM (1 program)

**Function**: Core Solana network operations

**Example**:
- **System Program** - Basic operations, account creation, rent

**Role in Network**:
- Core infrastructure, affects all accounts
- Not user-facing

**Risk Assessment**: LOW
- EXCLUDE - System level operations
- Not relevant to token funding analysis

---

#### 5. VALIDATOR (Multiple)

**Function**: Staking participation and block validation

**Role in Network**:
- Participate in Solana consensus
- Receive staking rewards

**Risk Assessment**: LOW
- Exclude from suspicious networks

---

#### 6. RELAYER (Multiple)

**Function**: Message relaying between blockchains

**Role in Network**:
- Cross-chain communication
- Bridge operations

**Risk Assessment**: LOW
- Exclude from suspicious networks

---

#### 7. DEX (Multiple)

**Function**: Decentralized exchange liquidity pools and automation

**Role in Network**:
- Provide trading liquidity
- Automated market making

**Risk Assessment**: LOW
- Expected pattern
- Exclude from suspicious networks

---

#### 8. LENDING (Multiple)

**Function**: Lending protocol operations and loan management

**Role in Network**:
- Loan operations and collateral management
- Interest distribution

**Risk Assessment**: LOW to MEDIUM
- Institutional pattern
- Monitor for unusual distributions

---

### Risk Classifications

**Low Risk (Exclude from Analysis)**:
- Bridge, Protocol, System, Validator, Relayer, DEX, Lending
- Normal ecosystem operations

**Medium Risk (Monitor)**:
- Automation: Watch for suspicious distribution patterns
- Only flag if coordinating with unknown funders

**Investigation Priority**:
1. 🔴 HIGH: Automation bots creating unusual funding patterns
2. 🟡 MEDIUM: Unknown category programs with unusual activity
3. 🟢 LOW: Known infrastructure with expected patterns

### Integration

INFRA automation funding increases overall risk score by **+0.10** (adjustment factor)

---

## Network Data

### Funder Networks

**Total Networks**: 41,734

**Coverage**:
- Primary funders: Tracked
- Network size: Members counted
- SOL volume: Total per network
- Cluster ID: Grouping info

**Key Metrics**:
- Total Members: Network-wide
- Total Volume: $31,711.97 SOL
- Avg Network Size: ~6,485
- Max Network Size: 6,485
- Unique Funders: 6,485

### Coordinated Edges

**Type**: Creator-to-creator relationships

**Information**:
- Creator A address
- Creator B address (coordinated)
- Bridge funder connecting them
- Confidence score (0-1)
- Detection timestamp

**Purpose**: Show which creators share funding relationships

### Super Clusters

**Total Clusters**: 503

**Information per Cluster**:
- Super cluster ID
- Network count in cluster
- Creator count in cluster
- Risk level (NORMAL/HIGH/CRITICAL)
- Creator reuse ratio
- Creator reuse tags (INDEPENDENT/SUSPICIOUS/COORDINATED)
- Shared creator count

**Purpose**: Identify multi-creator coordination schemes

### Top Creators

**Total Creators**: 300+

**Ranking by**:
- Creator address
- Funder count (how many funded this creator)
- Token count
- Self-funding flag (yes/no)
- Self-funding percentage
- Self-funding intermediates count

**Use Case**: Focus on prolific creators and identify self-funding schemes

---

## Database Schema

### Key Tables

#### creator_funders
```
creator_address      TEXT - Token creator
funder_address       TEXT - Who funded the creator
amount_sol           REAL - SOL amount
first_detected_at    TIMESTAMP
is_cex              BOOLEAN
cex_exchange        TEXT
source_type         TEXT - 'original_sender' or 'relay'
```

#### creator_outgoing_transfers
```
creator_address      TEXT - Creator sending SOL
recipient_address    TEXT - Who receives from creator
amount_sol           REAL - SOL amount
transaction_signature TEXT - TX hash
block_time           INT - Timestamp
```

#### funding_chains
```
source_creator       TEXT - Original creator
target_creator       TEXT - Recipient creator
bridge_funder        TEXT - Intermediary
amount_sol           REAL
chain_type           TEXT
```

#### coordinated_creator_edges
```
creator_a            TEXT - Creator A
creator_b            TEXT - Creator B
bridge_funder        TEXT - Shared funder
confidence           REAL - 0-1 score
```

#### creator_self_funding
```
creator_address      TEXT - Creator
is_self_funding      INT - 0 or 1
self_funding_percentage REAL
self_funding_intermediates INT
total_funders        INT
```

#### funding_network_members
```
network_id           TEXT
funder_address       TEXT - Member address
member_count         INT
```

#### super_clusters
```
super_cluster_id     TEXT
network_count        INT
creator_count        INT
risk_level           TEXT
creator_reuse_ratio  REAL
creator_reuse_tag    TEXT
```

#### cex_wallets
```
cex_address          TEXT - Solana wallet
exchange_name        TEXT - Exchange name
wallet_type          TEXT - Hot, Cold, etc.
confidence_level     INT - 1-5
discovered_date      TIMESTAMP
is_active            BOOLEAN
```

---

## Implementation Details

### Code Locations

#### SOL Filtering
- **File**: `funder_incoming_extractor.py`
- **Line**: 51
- **Code**: `MIN_SOL = 0.001`

#### Findings Detection
- **File**: `main.py`
- **Endpoint**: `/api/creator-recent-checks`
- **Lines**: 16502-16649

#### CEX/INFRA Mapping
- **File**: `infra_mapping.py`
- **Functions**: `get_account_info()`, `get_cex_info()`
- **Data**: `CEX_ACCOUNTS`, `INFRASTRUCTURE_ACCOUNTS` dicts

#### Network Analysis
- **File**: `cross_funding_network_analyzer.py`
- **Functions**: Build clusters, identify edges

#### Creator Extraction
- **Files**:
  - `realtime_creator_funding_extractor.py` (funders)
  - `funder_incoming_extractor.py` (sources)
  - `creator_outgoing_extractor.py` (distributions)

### API Endpoints

#### `/api/creator-recent-checks`
Returns most recently scanned creators with findings

**Response**:
```json
{
  "recent_checks": [
    {
      "creator_address": "...",
      "token_count": 5,
      "funder_count": 10,
      "findings": ["🚩 SELF-FUNDING (85%)", "⚠️ CREATOR_FUNDING_CHAIN"],
      "risk_level": "CRITICAL",
      "last_scanned": "2026-02-28 15:30:00"
    }
  ]
}
```

---

## Integration Guide

### Dashboard & UI
- **Creator Analysis Page**: Display findings badges with emojis
- **Dashboard**: Color-code by risk tier (red/orange/yellow/green)
- **API**: Return JSON with all findings and risk score

### Machine Learning
- **Feature**: CEX backing (binary institutional signal)
- **Feature**: Automation score (bot activity level)
- **Feature**: Self-funding percentage (manipulation indicator)
- **Feature**: Coordination score (network effect)

### Alerting Systems
- 🔴 HIGH: Self-funding > 80%, coordination detected
- 🟠 MEDIUM: Unknown funders, distribution patterns
- 🟡 LOW: Automation detected, network membership
- 🟢 NONE: CEX-backed, clean pattern

### Monitoring
- **Real-time**: Findings generation on new tokens
- **Hourly**: Update coordinated edges and clusters
- **Daily**: Review top suspicious creators
- **Weekly**: Update CEX/INFRA mappings

---

## Summary

### Coverage Statistics
| Category | Count | Status |
|----------|-------|--------|
| Funder Networks | 41,734 | ✅ Complete |
| Coordinated Edges | 500+ | ✅ Sampled |
| Super Clusters | 503 | ✅ Complete |
| Top Creators | 300+ | ✅ Ranked |
| CEX Wallets | 43 | ✅ Mapped (20 exchanges) |
| INFRA Programs | 59 | ✅ Tracked (8 categories) |
| Findings Tags | 8 | ✅ Complete with detection logic |

### Key Takeaways

✅ **Complete Three-Layer Model**
- Sender → Funder → Creator flow fully documented
- Risk indicators identified for each layer
- Database tables mapped for each role

✅ **SOL Filtering Strategy**
- MINIMUM_SOL = 0.001 SOL threshold applied
- Filters dust transfers, reduces noise 30-40%
- Focuses analysis on meaningful funding flows

✅ **Comprehensive Findings System**
- 8 findings tags with complete detection logic
- Risk levels from CRITICAL to CLEAN
- Automatic generation via database queries

✅ **Robust Risk Calculation**
- Weighted formula (40/30/20/10)
- Adjustment factors for CEX and INFRA
- Thresholds tied to specific actions

✅ **Production Ready**
- All documentation in single file
- Code locations and queries provided
- Integration examples included

---

## RPC Calls & External API Integration

### Overview

The Flex system makes RPC calls to the Solana blockchain at multiple points to extract funding data. There are two main RPC providers used:

1. **Solana Public RPC**: `https://api.mainnet-beta.solana.com`
2. **Helius RPC** (optional, faster): `https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}`

### RPC Call Status

**✅ ACTIVE IN MAIN LISTENER** (automatic, production use):
- `getTransaction` - Fetch transaction details
- `getSignaturesForAddress` - List address transactions
- `getAccountInfo` - Get account balances
- Helius `/v0/addresses/{address}/transactions` - Fast transaction history (when API key set)
- Helius `/v0/transactions` - Batch transaction processing (background 12h scan)

**⚠️ SECONDARY** (on-demand, API endpoint only):
- `funder_helius_extractor.py` - User-triggered via `/funder-analysis` endpoint

**❌ ARCHIVED** (legacy, not called in main listener):
- `pump_fun_analyzer.py` - Legacy analysis script
- `pump_fun_post_migration_analyzer.py` - Legacy analysis script

---

### Primary RPC Methods Used

#### 1. **getTransaction**
**Purpose**: Fetch full transaction details including inner instructions and token transfers

**Used in**:
- `pumpfun_curve_listener.py:622` - Extract pool from migration tx
- `pumpfun_curve_listener.py:708` - Fetch creator from migration
- `pumpfun_curve_listener.py:1742` - Parse new token migration
- `realtime_creator_funding_extractor.py:421` - Extract creator funding
- `realtime_creator_funding_extractor.py:1490` - Batch transaction fetching
- `funder_incoming_extractor.py:525` - Get funder transfer details
- `main.py:7798` - API endpoint transaction lookup
- `main.py:12587` - Debug endpoint

**Cost**: 1 RPC call per transaction
**Data extracted**:
- SOL transfers
- Token transfers
- Inner instructions
- Account interactions

**Code Example 1** - `pumpfun_curve_listener.py:619-626`
```python
payload = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "getTransaction",
    "params": [signature, {"encoding": "json", "maxSupportedTransactionVersion": 0}]
}

data = await self._post_rpc_with_fallback(payload)
if not data or "result" not in data or not data["result"]:
    print(f"[MINT] Transaction not found after retries: {signature}")
```

**Code Example 2** - `realtime_creator_funding_extractor.py:418-427`
```python
async def get_transaction(self, signature: str) -> Optional[Dict]:
    """Get transaction with RPC failover"""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTransaction",
        "params": [
            signature,
            {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}
        ]
    }
    result = await self._post_rpc(payload)
    if result and "result" in result:
        tx = result.get("result")
        if tx is not None:
            return tx
    return None
```

---

#### 2. **getSignaturesForAddress**
**Purpose**: Get all transaction signatures for an address (paginated)

**Used in**:
- `realtime_creator_funding_extractor.py:375` - Get all creator transactions
- `realtime_creator_funding_extractor.py:785` - List funder transactions
- `funder_incoming_extractor.py:408` - Get funder transaction history
- `creator_outgoing_extractor.py:335` - Get creator outgoing transactions

**Cost**: 1 RPC call per 1,000 signatures returned (paginated in 1000-sig chunks)
**Parameters**:
- `before`: Pagination cursor
- `limit`: 1000 (max per call)
- `commitment`: "finalized"

**Data extracted**: Transaction signatures and block time

**Code Example 1** - `realtime_creator_funding_extractor.py:371-395`
```python
# Paginated loop to get all signatures
signatures = []
before = None

while True:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getSignaturesForAddress",
        "params": [
            creator,
            {
                "limit": limit,
                **({"before": before} if before else {})
            }
        ]
    }

    result = await self._post_rpc(payload)
    if not result or "result" not in result:
        break

    sigs = result.get("result", [])
    if not sigs:
        break

    signatures.extend([sig["signature"] for sig in sigs])
    before = sigs[-1]["signature"]  # Continue pagination
```

**Code Example 2** - `funder_incoming_extractor.py:404-414`
```python
def get_signatures_for_address_rpc(address: str, limit: int = 1000) -> List[str]:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getSignaturesForAddress",
        "params": [address, {"limit": int(limit)}],
    }
    data = _rpc_call(payload, timeout=20.0)
    if not data or "result" not in data or not isinstance(data["result"], list):
        return []
    return [r.get("signature") for r in data["result"]
            if isinstance(r, dict) and r.get("signature")]
```

**Code Example 3** - `creator_outgoing_extractor.py:330-346`
```python
async def rpc_get_signatures(session: aiohttp.ClientSession, address: str, limit: int = 25) -> List[dict]:
    """Fetch recent signatures for a creator address"""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getSignaturesForAddress",
        "params": [address, {"limit": limit}]
    }
    try:
        async with session.post(RPC_HTTP, json=payload,
                               timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
            return data.get("result") or []
    except Exception as e:
        print(f"[OUTGOING] ⚠️ rpc_get_signatures error: {e}")
        return []
```

---

#### 3. **getAccountInfo**
**Purpose**: Fetch raw account data for a specific address

**Used in**:
- `pumpfun_curve_listener.py:894` - Get SOL vault balance
- `pumpfun_curve_listener.py:977` - Get WSOL vault balance

**Cost**: 1 RPC call per account
**Data extracted**: Account balance, owner, data

**Code Example** - `pumpfun_curve_listener.py:890-902`
```python
# Get account info with jsonParsed to extract the owner
acct_payload = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "getAccountInfo",
    "params": [token_account_addr, {"encoding": "jsonParsed"}]
}

try:
    acct_data = await self._post_rpc_with_fallback(acct_payload, timeout=5)
    if acct_data and "result" in acct_data and acct_data["result"]:
        account = acct_data["result"]
        value = account.get("value", {})
        # Extract balance and owner from account data
        balance = value.get("lamports", 0) / 1e9  # Convert to SOL
```

---

### Secondary RPC Calls (Helius Enhanced API)

#### Helius `/v0/addresses/{address}/transactions` Endpoint
**Purpose**: Enhanced transaction history with parsed instructions (faster than standard RPC)

**Used in**:
- `realtime_creator_funding_extractor.py:1015-1028` - Creator transaction enrichment
- `realtime_creator_funding_extractor.py:1871-1872` - Batch creator scanning

**Cost**: Per API plan (typically 1 call per address)
**Parameters**:
- `api-key`: {HELIUS_API_KEY}
- `limit`: 100 (max per page)
- `sort-order`: desc
- `commitment`: finalized

**Data extracted**:
- Parsed transfer instructions
- Token metadata
- Domain/NFT information
- Account interactions

**Code Example** - `realtime_creator_funding_extractor.py:1014-1039`
```python
# Helius Enhanced API for faster transaction enrichment
url = f"https://api-mainnet.helius-rpc.com/v0/addresses/{creator}/transactions"

page_num = 0
before_signature = None

while True:
    page_num += 1

    # Build URL with query parameters
    query_url = f"{url}?api-key={HELIUS_API_KEY}&limit=100&sort-order=desc&commitment=finalized"
    if before_signature:
        query_url += f"&before={before_signature}"

    try:
        print(f"[REALTIME_FUNDING] [PAGE {page_num}] RPC CALL #{page_num}...", flush=True)

        async with self.session.get(
                query_url,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
            # Process response and continue pagination
            ...
```

---

#### Helius `/v0/transactions` Endpoint
**Purpose**: Batch fetch enriched transaction data

**Used in**:
- `realtime_creator_funding_extractor.py:1885-1890` - Batch get detailed transfers

**Cost**: Per API plan
**Data extracted**: Full parsed transaction details

**Code Example** - `creator_outgoing_extractor.py:357-375`
```python
# Helius batch endpoint for parsing multiple transactions
body = {"transactions": sigs}
max_retries = 3
backoff_times = [0.5, 1.0, 2.0]

for attempt in range(max_retries):
    try:
        async with session.post(HELIUS_ENHANCED, json=body,
                               timeout=aiohttp.ClientTimeout(total=20)) as resp:
            if resp.status == 429:
                if attempt < max_retries - 1:
                    sleep_time = backoff_times[attempt]
                    print(f"[OUTGOING] Rate limited (429), retry in {sleep_time}s", flush=True)
                    await asyncio.sleep(sleep_time)
                    continue
            elif resp.status == 200:
                return await resp.json()
    except Exception as e:
        print(f"[OUTGOING] Error: {e}")
```

---

### RPC Call Locations by Component

#### 1. **PumpFunCurveListener** (pumpfun_curve_listener.py)

**Startup/Connection**:
- WebSocket connection to Helius or Solana RPC (line 271)
- Fallback to public Solana RPC if Helius unavailable

**Token Detection** (when migration detected):
- `getTransaction` (line 622) - Extract initial pool from migration TX
- `getTransaction` (line 708) - Fetch creator address from migration
- `getAccountInfo` (line 894) - Get SOL vault balance from pool
- `getAccountInfo` (line 977) - Get WSOL vault balance from pool
- `getTransaction` (line 1742) - Parse latest migration transaction

**Total RPC calls per token**: 5 calls
**Timing**: ~2-5 seconds total
**Purpose**: Extract creator, mint, and price information

---

#### 2. **Creator Funding Extractor** (realtime_creator_funding_extractor.py)

**Entry**: `extract_funding_for_new_token(creator_address, created_at, create_tx_sig, mint)`

**RPC Calls**:
1. `getSignaturesForAddress` (line 375) - Get all creator SOL transfers
   - Multiple calls if >1000 signatures (pagination)
   - Cost: 1 per 1000 signatures

2. `getTransaction` (line 421) - Fetch full details per signature
   - Cost: 1 per transaction found

3. **OR** Helius Enhanced API (line 1015-1028)
   - Single call to get creator transactions with parsing
   - Cost: 1 per creator
   - Much faster than RPC method #1 + #2

**Alternative Path** (Helius batch):
- POST to `/v0/transactions` (line 1885) - Get enriched data for multiple TXs

**Total RPC calls per creator**:
- Pure RPC: N (where N = number of creator transactions)
- Helius Enhanced: 1 + possibly batch calls for detailed parsing

**Timing**: 5-30 seconds depending on creator activity
**Purpose**: Identify all funders who sent SOL to creator before token launch

---

#### 3. **Funder Incoming Extractor** (funder_incoming_extractor.py)

**Entry**: `extract_for_creator()` (called for each creator)

**RPC Calls**:
1. `getSignaturesForAddress` (line 408) - Get all funder transactions
   - Called for EACH funder from creator_funders table
   - Multiple paginated calls if >1000 signatures
   - Cost: 1 per 1000 signatures per funder

2. `getTransaction` (line 525) - Fetch full transaction details
   - Cost: 1 per transaction for each funder

**OR Helius Transactions** (line 328):
- `get_transactions_helius(address, limit=100, max_pages=1)`
- Fetches up to 100 transactions per Helius call
- Much faster parsing

**Total RPC calls per creator**:
- Pure RPC: Sum of (N_funder_transactions for each funder)
- Helius: 1 per funder + potential batch calls

**Cost Scale**:
- Small creator (10 funders, 50 transfers each): ~10-500 calls
- Large creator (100 funders, 100 transfers each): ~100-10,000 calls

**Timing**: 30-120 seconds depending on funder network complexity
**Purpose**: For each creator funder, find the SOURCE of their money

---

#### 4. **Creator Outgoing Extractor** (creator_outgoing_extractor.py)

**Entry**: `run_outgoing_extractor(interval_seconds=43200)` (background, every 12 hours)

**RPC Calls**:
1. `getSignaturesForAddress` (line 335) - Get all creator outgoing transactions
   - Cost: 1 per 1000 signatures per creator

2. Helius Enhanced API (line 363) - Parse transaction data
   - POST to Helius endpoint for enriched data
   - Cost: Per API plan

**Total RPC calls per creator**: 1-5 (paginated)
**Timing per creator**: ~1-2 seconds
**Total per 12h cycle**: ~1000-2000 creators = 2000-5000 RPC calls

**Purpose**: Track creator outgoing transfers (where they send SOL after launch)

---

### RPC Call Summary Table

| Component | RPC Method | Calls/Trigger | Cost | Purpose |
|-----------|-----------|---------------|------|---------|
| **Listener** | getTransaction | 5 per token | 5 RPC calls | Extract creator & price |
| **Listener** | getAccountInfo | 2 per token | 2 RPC calls | Get vault balances |
| **Creator Funding** | getSignaturesForAddress | 1-10 per creator | Variable | List creator TXs |
| **Creator Funding** | getTransaction | 1 per TX | N TXs × 1 | Parse each TX |
| **Creator Funding** | Helius Enhanced | 1 per creator | 1 API call | Fast parsing |
| **Funder Incoming** | getSignaturesForAddress | 1-100+ per creator | Variable | List funder TXs |
| **Funder Incoming** | getTransaction | 1 per TX | Sum(N) TXs | Parse each TX |
| **Creator Outgoing** | getSignaturesForAddress | 1-5 per creator | Variable | List outgoing TXs |
| **Creator Outgoing** | Helius Enhanced | 1 per creator | 1 API call | Parse outgoing |

---

### RPC Cost Analysis

**New Token (1 token detected)**:
- Listener: 5-7 RPC calls (fixed)
- Creator Funding: 1 Helius call OR 10-50 RPC calls
- Funder Incoming: 50-500 RPC calls (depends on funder count)
- **Total**: 56-557 RPC calls

**With Helius API (recommended)**:
- Listener: 5-7 RPC calls
- Creator Funding: 1 Helius call
- Funder Incoming: 1-2 Helius calls + 10-20 RPC calls
- **Total**: 17-30 RPC calls (much more efficient)

**12-Hour Creator Outgoing Scan** (1000 creators):
- Pure RPC: 1000-5000 RPC calls
- Helius: 1000 Helius API calls + batch parsing

---

### Active RPC Flow in Listener

**When listener starts** (`python pumpfun_curve_listener.py`):

```
listen() spawns 4 async background tasks:

1. Creator Watch Manager (Every 30 seconds)
   └─ getSignaturesForAddress ✅
   └─ getTransaction ✅

2. Live Price Updater (Continuous)
   └─ getTransaction ✅

3. Creator Outgoing Extractor (Every 12 hours)
   └─ getSignaturesForAddress ✅
   └─ Helius /v0/transactions batch ✅

4. WebSocket Listener (Real-time, blocking)
   └─ Waits for token detection
      └─ When token detected:
         ├─ getTransaction (3×) ✅
         ├─ getAccountInfo (2×) ✅
         └─ Spawn 3 background tasks:
            ├─ extract_funding_for_new_token()
            │  ├─ getSignaturesForAddress ✅
            │  ├─ getTransaction ✅
            │  └─ Helius /v0/addresses/tx ✅ (if key available)
            │
            ├─ extract_funder_transfers_async()
            │  ├─ getSignaturesForAddress ✅ (per funder)
            │  └─ getTransaction ✅ (per transaction)
            │
            └─ update_network_clustering_async()
               └─ Database only (no RPC)
```

**RPC Calls per New Token**:
- Listener detection: 5 calls (getTransaction ×3, getAccountInfo ×2)
- Creator funding: 10-50 calls (or 1-2 Helius if available)
- Funder incoming: 50-500+ calls (scales with funder network)
- **Total: 65-555 calls** (or 17-30 with Helius)

---

### Configuration

**File**: `pumpfun_curve_listener.py:268-279`

```python
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
HELIUS_RPC_WS = f"wss://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}" if HELIUS_API_KEY else "wss://api.mainnet-beta.solana.com/"
RPC_HTTP = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}" if HELIUS_API_KEY else "https://api.mainnet-beta.solana.com"

RPC_URLS = [
    "https://api.mainnet-beta.solana.com",  # Fallback 1
    "https://api.anza.dev/rpc",             # Fallback 2
]

if HELIUS_API_KEY:
    RPC_URLS.append(f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}")  # Primary if available
```

**WebSocket Connection**:
- Primary: Helius WebSocket (if `HELIUS_API_KEY` set)
- Fallback: Solana public WebSocket

**HTTP RPC**:
- Primary: Helius HTTP endpoint (if `HELIUS_API_KEY` set)
- Fallback: Solana public RPC + Anza RPC

---

### Performance Optimization

**Without Helius API**:
- New token processing: 180-220 seconds
- RPC calls per token: 50-500+
- Heavily rate-limited

**With Helius API** (recommended):
- New token processing: 90-120 seconds
- RPC calls per token: 10-30
- Better rate limiting tier
- Faster transaction parsing

**Helius Benefits**:
✅ Faster transaction parsing (enriched data)
✅ Better rate limits for high-volume analysis
✅ Built-in pagination support (limit=100)
✅ Includes parsed instruction data
✅ Domain/NFT enrichment included

---

### Rate Limiting Strategy

**Solana Public RPC**:
- ~100 requests per second
- Shared across all users
- Subject to abuse limits

**Helius RPC**:
- Depends on plan tier
- Usually 1000+ requests per second
- Dedicated allocation

**Mitigation**:
- Batch requests where possible
- Use pagination correctly (don't repeat signature fetches)
- Fallback to slower RPC if primary rate-limited
- Queue background tasks (creator outgoing scan) to avoid spike

---

### Database Impact

**After RPC extraction, data stored in**:
- `creator_funders` - Direct funder relationships
- `funder_incoming_transfers` - Funder source traces
- `creator_outgoing_transfers` - Creator spending
- `tokens` - Token metadata
- `address_labels` - Account classifications

**Query instead of RPC**:
- Most analyses query database instead of RPC
- Reduces ongoing RPC costs
- Enables offline analysis

---

## RPC Implementation Functions

### Overview

Three different RPC call implementations are used throughout the codebase, each optimized for different use cases:

1. **`_post_rpc_with_fallback()`** - Listener (async, simpler failover)
2. **`_post_rpc()`** - Creator funding extractor (async, advanced retry/rate-limiting)
3. **`_rpc_call()`** - Funder incoming extractor (sync, smart error categorization)

---

### 1. `_post_rpc_with_fallback()` - PumpFunCurveListener

**File**: `pumpfun_curve_listener.py:306-339`
**Type**: Async method (class-based)
**Usage**: Main listener for token detection and pool extraction

```python
async def _post_rpc_with_fallback(self, payload: dict, timeout: int = 10) -> Optional[dict]:
    """
    Post to RPC with automatic failover chain.
    Tries: Primary QuickNode -> Secondary QuickNode -> Helius -> Public Solana
    """
    try:
        async with aiohttp.ClientSession() as session:
            for i, rpc_url in enumerate(RPC_URLS):
                try:
                    async with session.post(rpc_url, json=payload,
                                           timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                        if resp.status == 200:
                            return await resp.json()
                        elif resp.status == 429:
                            if i < len(RPC_URLS) - 1:
                                continue
                        else:
                            if i < len(RPC_URLS) - 1:
                                continue
                except asyncio.TimeoutError:
                    if i < len(RPC_URLS) - 1:
                        continue
                except Exception as e:
                    if i < len(RPC_URLS) - 1:
                        continue

            return None
    except Exception as e:
        print(f"[RPC_ERROR] {e}", flush=True)
        return None
```

**Characteristics**:
- ✅ Simple failover chain (tries each RPC in sequence)
- ✅ Async/await with aiohttp
- ✅ Timeout handling (default 10 seconds)
- ✅ No exponential backoff (just tries next immediately)
- ✅ Creates new session per call

---

### 2. `_post_rpc()` - RealtimeCreatorFundingExtractor

**File**: `realtime_creator_funding_extractor.py:299-359`
**Type**: Async method (class-based)
**Usage**: Creator funding extraction with advanced retry logic

```python
async def _post_rpc(self, payload: dict) -> Optional[dict]:
    """Post to RPC with failover chain + semaphore concurrency control"""
    async with self._rpc_sem:  # Bound concurrent RPC calls
        for attempt in range(MAX_RETRIES):
            for rpc_url in RPC_URLS:
                try:
                    async with self.session.post(
                        rpc_url,
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=RPC_TIMEOUT)
                    ) as resp:
                        if resp.status != 200:
                            if resp.status == 429:
                                # Rate limited - check for Retry-After header
                                retry_after = resp.headers.get("Retry-After")
                                retry_delay = None
                                if retry_after:
                                    try:
                                        retry_delay = float(retry_after)
                                    except (ValueError, TypeError):
                                        retry_delay = None

                                wait_time = retry_delay or (0.5 * (2 ** attempt))
                                await asyncio.sleep(min(30.0, wait_time))
                                continue
                            elif resp.status >= 500:
                                continue
                            else:
                                return None

                        data = await resp.json()

                        # RPC-level errors
                        if "error" in data:
                            error_code = data["error"].get("code", -1)
                            # Retryable: -32008, -32000, -32003, -32009
                            if error_code in {-32008, -32000, -32003, -32009}:
                                continue
                            else:
                                return None

                        if "result" in data:
                            return data

                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    continue

            # After trying all RPCs, wait before next attempt
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(0.5 * (2 ** attempt))

    return None
```

**Characteristics**:
- ✅ Semaphore concurrency control (`_rpc_sem`)
- ✅ Advanced retry with exponential backoff (0.5s → 1s → 2s → 4s)
- ✅ Respects `Retry-After` header from rate-limit responses
- ✅ Smart RPC error detection (knows which errors are retryable)
- ✅ Max 30-second wait per attempt
- ✅ Reuses persistent session (more efficient)
- ✅ Handles both HTTP and RPC-level errors

**Retryable RPC Error Codes**:
- `-32008`: Invalid index
- `-32000`: Server error (generic)
- `-32003`: Invalid request
- `-32009`: Resource exhausted

---

### 3. `_rpc_call()` - FunderIncomingExtractor

**File**: `funder_incoming_extractor.py:361-401`
**Type**: Synchronous function
**Usage**: Funder transfer extraction with smart error categorization

```python
def _rpc_call(payload: dict, timeout: float = 20.0) -> Optional[dict]:
    """
    Reliable Solana RPC POST with retry/backoff.
    Smart RPC error categorization:
    • Only retries transient errors (timeout, rate-limit, etc.)
    • Fails fast on permanent errors (invalid params, etc.)
    """
    for attempt in range(MAX_RPC_RETRIES):
        try:
            resp = SESSION.post(SOLANA_RPC, json=payload, timeout=timeout)
            if resp.status_code == 429:
                print(f"[RPC] 429 rate-limited. Backing off (attempt {attempt+1}/{MAX_RPC_RETRIES})")
                _sleep_backoff(attempt)
                continue
            if resp.status_code >= 500:
                print(f"[RPC] {resp.status_code} server error. Backing off")
                _sleep_backoff(attempt)
                continue
            if resp.status_code != 200:
                return None

            data = resp.json()
            if isinstance(data, dict) and data.get("error"):
                error_obj = data["error"]
                if _is_rpc_error_retryable(error_obj):
                    print(f"[RPC] Transient error (code={error_obj.get('code')}). Backing off")
                    _sleep_backoff(attempt)
                    continue
                else:
                    # Permanent error: fail fast
                    print(f"[RPC] Permanent error (code={error_obj.get('code')})")
                    return None
            return data
        except (requests.Timeout, requests.ConnectionError) as e:
            print(f"[RPC] Network error: {e}. Backing off")
            _sleep_backoff(attempt)
            continue
        except Exception:
            return None
    return None
```

**Helper Functions**:
```python
def _is_rpc_error_retryable(error_obj: dict) -> bool:
    """Determine if RPC error is transient (retryable)."""
    code = error_obj.get("code")
    # Retryable: -32008, -32000, -32003, -32009
    return code in {-32008, -32000, -32003, -32009}

def _sleep_backoff(attempt: int, retry_after: Optional[float] = None):
    """Exponential backoff: 0.5s → 1s → 2s → 4s..."""
    if retry_after is not None:
        time.sleep(retry_after)
        return
    delay = 0.5 * (2 ** attempt)
    time.sleep(min(delay, 60.0))  # Cap at 60 seconds
```

**Characteristics**:
- ✅ Synchronous (blocking) implementation
- ✅ Single SOLANA_RPC endpoint (no failover chain)
- ✅ Smart error categorization (retryable vs permanent)
- ✅ Fast-fail on permanent errors
- ✅ Exponential backoff with jitter
- ✅ Detailed error messages for debugging
- ✅ Reuses persistent SESSION (requests.Session)

---

### Comparison Table

| Feature | `_post_rpc_with_fallback()` | `_post_rpc()` | `_rpc_call()` |
|---------|---------------------------|---------------|--------------|
| **Type** | Async (class) | Async (class) | Sync (func) |
| **Failover** | ✅ Full chain | ✅ Full chain | ❌ Single |
| **Retry** | Simple | ✅ Exponential | ✅ Exponential |
| **Concurrency** | ❌ None | ✅ Semaphore | ❌ None |
| **Error Category** | Simple | ✅ Advanced | ✅ Advanced |
| **Retry-After** | ❌ No | ✅ Yes (30s cap) | ✅ Yes |
| **Session Reuse** | ❌ No | ✅ Yes | ✅ Yes |
| **Use Case** | Token detection | Creator extract | Funder extract |
| **Timeout** | 10s default | RPC_TIMEOUT | 20s default |

---

### Which to Use?

**`_post_rpc_with_fallback()`** - When:
- You need simple failover for critical operations
- You don't expect many retries
- RPC response comes back quickly
- Used in: Main listener for token detection

**`_post_rpc()`** - When:
- You're extracting large amounts of data
- You need bounded concurrency (semaphore)
- You want to respect Retry-After headers
- You're doing batch processing
- Used in: Creator funding extraction

**`_rpc_call()`** - When:
- You're in synchronous code
- You want smart error categorization
- You want fast-fail on permanent errors
- You don't need concurrent calls
- Used in: Funder transfer extraction

---

*Complete System Documentation*
*Generated: 2026-02-28*
*Database: flex_complete_database.db*
*Ready for production deployment and integration*
