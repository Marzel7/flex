# FLEX: Solana Token Funding Network Analysis System

**Comprehensive technical documentation for the token analysis, funding extraction, and real-time monitoring system**

---

## 📋 Table of Contents

1. [Project Overview](#project-overview)
2. [System Architecture](#system-architecture)
3. [Token Detection & Processing Engine](#token-detection--processing-engine)
4. [Funding Extraction Framework](#funding-extraction-framework)
5. [Network Analysis & Clustering](#network-analysis--clustering)
6. [Real-Time Webhook System](#real-time-webhook-system)
7. [RPC Metrics & Optimization](#rpc-metrics--optimization)
8. [Database Layer & Schema](#database-layer--schema)
9. [Async Processing & Event Flow](#async-processing--event-flow)
10. [Configuration & Environment](#configuration--environment)
11. [Error Handling & Edge Cases](#error-handling--edge-cases)
12. [Performance Characteristics](#performance-characteristics)
13. [Extending the System](#extending-the-system)

---

## 1. Project Overview

### Purpose
**FLEX** is a Solana token analysis system that tracks funding networks, identifies coordinated funder relationships, and detects suspicious pump-and-dump schemes across Pump.Fun tokens. The system automates the detection of:

- **Funding networks** - Who funded token creators and where did they get the money
- **Coordinated funding** - Shared funders across multiple token creators
- **Self-funding schemes** - Creators redistributing their own money through intermediaries
- **Infrastructure connections** - Identifying CEX wallets, automation bots, and service integrations

### Key Statistics
- **41,734 funder networks** tracked across Solana
- **503 super clusters** identified (coordinated funding groups)
- **43 CEX wallets** mapped (20 major exchanges)
- **59 INFRA programs** tracked (8 categories: Jito, Axiom, DeBridge, Meteora, etc.)
- **150+ database tables** for comprehensive data storage
- **Real-time webhook integration** with Helius RPC provider

### Three-Layer Funding Model
```
SENDERS (Layer 1: Original Source)
    ↓ Distribute SOL
FUNDERS (Layer 2: Intermediaries)
    ↓ Send SOL
CREATORS (Layer 3: Token Launchers)
    ↓ Launch Token
TOKENS
```

---

## 2. System Architecture

### Core Components

#### 2.1 Flask Web Application
**File**: `src/core/main.py` (6,900+ lines)

The Flask app serves:
- **Web UI**: Dashboard, networks, clusters, funding hubs, analysis pages
- **REST API**: 60+ endpoints for data retrieval and system control
- **Real-time pages**: Auto-updating metrics and status dashboards
- **Interactive analysis**: Creator funding analysis, funder details, network visualization

**Key Routes**:
- `/` - Main migration dashboard
- `/networks` - Funding network visualization
- `/clusters` - Cross-funding cluster analysis
- `/coordinated-funders` - Funder relationship tracking
- `/funding-hub/<address>` - Individual funding hub details
- `/api/*` - REST endpoints for all data sources

#### 2.2 Token Detection & Listener
**File**: `src/core/pumpfun_curve_listener.py`

Real-time token detection engine that:
- Connects to Solana WebSocket for new Pump.Fun migrations
- Detects token creation events
- Triggers funding extraction pipeline
- Maintains price tracking and status monitoring

**Spawned Background Tasks**:
1. **Creator Watch Manager** - Polls every 30s for creator status updates
2. **Live Price Updater** - Continuous background price tracking (currently disabled)
3. **Webhook Handler** - Real-time SOL transfer monitoring (replaces 12-hour scan)
4. **WebSocket Listener** - Real-time token detection

**Note**: The former 12-hour background scan (`creator_outgoing_extractor.run_forever()`) has been replaced with real-time Helius webhook monitoring for better efficiency and real-time detection of creator outgoing transfers.

#### 2.3 Webhook Handler
**File**: `src/core/webhook_handler.py`

Helius webhook integration for processing:
- Native SOL transfer events
- Account activity monitoring
- Real-time funding detection
- Deduplication and dust filtering
- Background work queue management

#### 2.4 Webhook Worker
**File**: `src/core/webhook_worker.py`

Async worker that:
- Processes work queue items with priority scheduling
- Fetches next creator analysis tasks
- Processes creator funding analysis
- Manages RPC rate limiting and cooldown periods

### Data Flow Diagram

```
New Token Detected (WebSocket)
    ↓
Extract Creator Funding (realtime_creator_funding_extractor.py)
    ├─ Query: Who funded the creator?
    └─ Save to: creator_funders table
    ↓
Extract Funder Transfers (funder_incoming_extractor.py)
    ├─ Query: Where did funders get their money?
    └─ Save to: funder_incoming_transfers table
    ↓
Extract Creator Outgoing (creator_outgoing_extractor.py)
    ├─ Query: Where does creator send SOL?
    ├─ Frequency: Every 12 hours (background)
    └─ Save to: creator_outgoing_transfers table
    ↓
Build Network Clustering (cross_funding_network_analyzer.py)
    ├─ Create: Coordinated edges
    ├─ Identify: Clusters and networks
    └─ Save to: Multiple clustering tables
    ↓
Generate Findings (main.py API)
    ├─ Compute: Risk scores
    ├─ Tag: Suspicious patterns
    └─ Display: Web UI
```

---

## 3. Token Detection & Processing Engine

### 3.1 WebSocket Listener Architecture

**File**: `src/core/pumpfun_curve_listener.py:listen_websocket()`

The listener maintains a WebSocket connection to detect Pump.Fun token creation events. When a new token migration is detected:

```python
# New token migration detected
token_mint = extracted_from_websocket_event()
creator_address = extracted_from_onchain_data(token_mint)

# Immediately trigger extraction pipeline
asyncio.create_task(
    realtime_creator_funding_extractor.extract_funding_for_new_token(
        creator_address=creator_address,
        created_at=timestamp,
        create_tx_sig=signature,
        mint=token_mint
    )
)
```

### 3.2 Token Metadata Extraction

**File**: `src/core/main.py:process_new_token()`

Upon token detection, the system extracts:
- **Token mint address** - Unique identifier
- **Creator address** - Who created the token
- **Creation timestamp** - Block time of token launch
- **Creation transaction** - Signature for verification
- **Pool address** - Bonding curve PDA
- **Migrated status** - Whether token has moved to main market

### 3.3 Real-Time Status Updates

The dashboard (`/`) queries `token_analysis` table with:
```sql
SELECT * FROM token_analysis
ORDER BY created_at DESC
LIMIT 25
```

This provides:
- **Latest 25 tokens** on the main page (ordered newest first)
- **Funding progress** indicator (how many funders extracted)
- **Risk assessment** (probability of rug, risk level)
- **Network involvement** (cluster ID, coordinated funders)

### 3.4 Price Tracking

**Background Task**: `update_live_prices_background()`

Continuously updates token prices from:
- **Primary**: Solscan API
- **Fallback**: Jupiter API
- **Caching**: Database with `price_updated_at` timestamp

Updates `token_analysis` columns:
- `price_current` - Current market price
- `price_highest` - Historical peak price
- `market_cap_current` - Current valuation
- `market_cap_highest_at` - Peak timestamp

---

## 4. Funding Extraction Framework

### 4.1 Creator Funding Extraction

**File**: `src/extractors/realtime_creator_funding_extractor.py`

**Purpose**: Find all wallets that sent SOL directly to the token creator

**Process**:
1. Query all transaction signatures for the creator address
2. Filter for native SOL transfer instructions
3. Extract sender → creator → amount tuples
4. Filter by minimum SOL threshold (≥ 0.001 SOL)
5. Save to `creator_funders` table

**Key Classes**:
- `RealTimeCreatorFundingExtractor` - Main extraction engine
- `DomainResolver` - Caches address domain mappings
- `RequestRecord` - Tracks RPC request metrics

**RPC Optimization**:
- Caches creator signatures to avoid re-querying
- Uses batch RPC calls where possible
- Implements exponential backoff for rate limiting
- Records all RPC calls for metrics tracking

**Output Table**: `creator_funders`
```
creator_address    - Token creator
funder_address     - Who funded them
amount_sol         - Amount sent
transaction_signature - TX hash
first_detected_at  - When discovered
is_cex            - If funder is a known exchange
fully_analyzed    - 0=pending, 1=complete
```

### 4.2 Funder Transfer Extraction

**File**: `src/extractors/funder_incoming_extractor.py`

**Purpose**: For each funder, discover where they received their money

**Process**:
1. For each creator_funder, query their incoming transfers
2. Identify senders to the funder
3. Classify senders as:
   - **CEX accounts** - Known exchange wallets
   - **INFRA programs** - Automation bots (Jito, Axiom, etc.)
   - **Unknown** - Unclassified addresses
4. Save to `funder_incoming_transfers` table

**Cost Control Mechanisms**:
- `MAX_FRESH_FUNDERS_PER_CREATOR = 10` - Limit new funders processed per creator
- `MAX_TX_SIGS_PER_FUNDER = 100` - Limit transaction history depth
- `DEFAULT_CONCURRENCY = 2` - Controlled parallel processing
- `RPC_COOLDOWN_SECONDS = 1800` - 30-min cooldown between creator scans

**Smart Caching**:
- Caches previously extracted funders (marked with `fully_analyzed=1`)
- Short-circuits already-processed addresses
- Defers additional funder processing if hitting budget constraints

**Output Table**: `funder_incoming_transfers`
```
funder_address     - The funder
sender_address     - Who sent to the funder
amount_sol         - Amount received
transaction_signature - TX hash
block_time         - Blockchain timestamp
is_cex            - If sender is CEX
classification    - CEX/INFRA/Unknown
```

### 4.3 Creator Outgoing Transfer Extraction

**File**: `src/extractors/creator_outgoing_extractor.py`

**Purpose**: Track where creators send their SOL after launch

**Process**:
1. Background task runs every 12 hours
2. Scans all creators in `creator_funders`
3. Extracts outgoing SOL transfers from creator address
4. Identifies recipient patterns (self-funding, distribution, hoarding)

**Output Table**: `creator_outgoing_transfers`
```
creator_address    - Token creator
recipient_address  - Who they sent SOL to
amount_sol         - Amount sent
transaction_signature - TX hash
block_time         - Blockchain timestamp
is_self_address    - If recipient is creator-controlled
```

### 4.4 Infrastructure & CEX Detection

**Automatic Classification**:

**CEX Detection**: `automatic_cex_detection.py`
- Queries Solscan API for address labels
- Cross-references against known exchange wallets
- Marks `is_cex = 1` in database

**INFRA Detection**: Multiple extractors
- `check_transfers_for_jitotip()` - Detects Jito tip transfers
- `check_transfers_for_axiom()` - Identifies Axiom MEV-Share
- `check_transfers_for_debridge()` - Tracks DeBridge interactions
- `check_transfers_for_meteora()` - Monitors Meteora DLMM pool usage

**Storage**: `creator_tags` table
```
creator_address    - Address involved
tag                - uses_jitotip, uses_axiom, uses_debridge, uses_meteora
description        - Details about the usage
amount_sol         - Total amount involved
first_detected     - When first seen
updated_at         - Last update time
```

---

## 5. Network Analysis & Clustering

### 5.1 Atomic Funder Networks

**File**: `src/analysis/cross_funding_network_analyzer.py`

An **atomic funder network** is the minimal set of addresses needed to explain funding relationships:

```
Sender A → Funder 1 → Creator X
Sender A → Funder 2 → Creator X
Sender A → Funder 3 → Creator Y
```

These three funders form an atomic network because:
- All share the same sender
- Together they connect creators
- Cannot be subdivided without losing connection

**Detection Algorithm**:
```python
class CrossFundingClusterAnalyzer:
    def build_atomic_funder_networks(self):
        # 1. Group funders by shared senders
        # 2. Identify connected creators
        # 3. Create atomic network when:
        #    - MIN_CREATORS_FOR_ATOMIC_FUNDER_NETWORK met
        #    - Significant overlap exists
```

**Output Table**: `atomic_funder_networks`
```
network_id         - Unique identifier
sender_address     - Original source
funder_addresses   - Array of intermediaries
creator_addresses  - Array of creators funded
total_sol          - Total amount distributed
network_tier       - Risk classification
is_cex            - If sender is exchange
```

### 5.2 Cross-Funding Clusters

**Definition**: Groups of creators funded by significantly overlapping funder networks

**Minimum Overlap Criteria**:
- `MIN_CREATORS_FOR_RECIPIENT_HUB = 3` - At least 3 creators
- `MIN_OVERLAP_CREATORS = 2` - Share at least 2 common funders
- `MIN_JACCARD = 0.2` - Jaccard similarity threshold

**Detection**:
```python
# Jaccard Similarity = |Shared Funders| / |Union of Funders|
# Example: Creators X and Y
# - X funded by: [A, B, C]
# - Y funded by: [B, C, D]
# - Shared: [B, C] = 2
# - Union: [A, B, C, D] = 4
# - Jaccard = 2/4 = 0.5 ✓ (meets MIN_JACCARD = 0.2)
```

**Output Table**: `funder_clusters`
```
cluster_id         - Unique identifier
creator_list       - JSON array of creators
funder_count       - Number of shared funders
shared_funders     - Overlap details
risk_multiplier    - Coordinated risk factor (1.0 to 5.0)
network_tier       - Classification
detected_at        - When discovered
```

### 5.3 Network Coordinators

**Definition**: Individual funders who support multiple creators across different networks

**Metrics**:
- **Creator count** - How many different creators has this funder supported?
- **Network diversity** - Across how many atomic networks?
- **Risk multiplier** - Higher for CEX funders (due to coordination capability)

**Coordinator Classification**:
- `CRITICAL` - Funds 50+ creators
- `HIGH` - Funds 10-50 creators
- `MEDIUM` - Funds 3-10 creators
- `LOW` - Funds 1-2 creators

**Output Table**: `network_coordinators`
```
funder_address     - The coordinator
creator_count      - Total creators funded
network_count      - Atomic networks involved
shared_creators_with_others - Coordination metric
risk_multiplier    - CEX multiplier (2.0 if CEX, 1.0 otherwise)
detected_at        - When discovered
```

### 5.4 Coordinated Funder Detection

Real-time computation in `/api/creator-recent-checks`:

For each new token, the system identifies:

**Finding Type 1: Multi-Creator Funder**
```
Funder X also funds Y and Z creators
Risk: Coordination indicator
Tag: "coordinated_funders_count"
```

**Finding Type 2: Network Involvement**
```
Creator X is part of larger coordinated network
Creators in cluster: [A, B, C]
Risk: Part of organized scheme
Tag: "coordinated_creator_count"
```

**Finding Type 3: Self-Funding**
```
Funder A received money from Sender X
Funder A sends to Creator X
Creator X sends back to Sender X
Risk: Circular money flow
Tag: "self_funding_intermediates"
```

---

## 6. Real-Time Webhook System

### 6.1 Webhook Architecture

**File**: `src/core/webhook_handler.py`

Helius webhook endpoint that processes SOL transfer events in real-time:

```python
@app.route('/helius-webhook', methods=['POST'])
def handle_helius_webhook():
    """Process raw Helius webhook events"""
    1. Validate authentication header
    2. Extract native SOL transfers from accountData
    3. Deduplicate by transaction signature
    4. Filter dust (< 0.001 SOL)
    5. Queue for async processing
    6. Return 200 immediately
```

**Performance**:
- Processes **1,000+ transactions/second**
- Per-transaction overhead: **1-2ms**
- Deduplication: **O(1) instant lookup** via primary key

### 6.2 SOL Transfer Filtering

**Extraction Logic**:
```python
def extract_system_transfers(tx_metadata):
    """Extract native SOL transfers"""
    for instr in tx_metadata['instructions']:
        if instr['program'] == 'system':
            # Find transfer amount from preBalances/postBalances
            transfer_amount = preBalance[dest] - postBalance[dest]

            # Filter dust
            if transfer_amount >= 0.001:
                yield (source, dest, transfer_amount)
```

**Output Table**: `sol_transfers`
```
signature          - Transaction ID (PRIMARY KEY)
slot               - Block slot
block_time         - Timestamp
source             - Sender address
destination        - Receiver address
lamports           - Raw amount (1 SOL = 1e9 lamports)
amount_sol         - Converted amount
received_at        - When webhook received
processed          - 0=queued, 1=processed
```

### 6.3 Address Activity Tracking

**File**: `src/core/webhook_handler.py:update_address_activity()`

Maintains real-time activity metrics:

**Metrics Tracked**:
- **tx_5m, tx_1h, tx_24h** - Transaction counts by timeframe
- **sol_in_5m, sol_in_1h, sol_in_24h** - Incoming SOL by timeframe
- **sol_out_5m, sol_out_1h, sol_out_24h** - Outgoing SOL by timeframe
- **last_seen_at** - Most recent activity timestamp
- **last_rpc_fetch_at** - When address was scanned

**Output Table**: `address_activity`
```
address            - Wallet address (PRIMARY KEY)
last_seen_at       - Most recent transaction timestamp
tx_5m, tx_1h, tx_24h - Transaction counts
sol_in_5m, sol_in_1h, sol_in_24h - Incoming amounts
sol_out_5m, sol_out_1h, sol_out_24h - Outgoing amounts
last_processed_at  - When metrics were calculated
last_rpc_fetch_at  - When full history was pulled
updated_at         - Record update time
```

### 6.4 Work Queue & Priority Scheduling

**File**: `src/core/webhook_handler.py:enqueue_work()`

Webhook handler queues high-priority addresses for detailed analysis:

**Priority Calculation**:
```python
priority = 0
if tx_count_5m > threshold: priority += 10  # Very recent activity
if sol_amount_24h > threshold: priority += 5  # High volume
if address_is_creator: priority += 15       # Known creator
if address_is_malicious: priority += 20     # Flagged address
```

**Output Table**: `work_queue`
```
address            - Target address (PRIMARY KEY)
priority           - Calculated score (higher = earlier processing)
reason             - Why queued (high_activity, new_creator, etc.)
next_run_at        - When to process next
locked_until       - Prevents concurrent processing
completed_at       - When processing finished
```

### 6.5 Creator Analysis Queue

**File**: `src/core/webhook_handler.py:queue_for_creator_analysis()`

Webhook integration automatically queues new addresses for deep creator analysis:

```python
def queue_for_creator_analysis(source_addr, dest_addr):
    """Queue webhook addresses for analysis"""
    # Both source and destination get analyzed
    # Priority: 15.0 (high priority)
    # Analyzer discovers:
    #   - Self-funding patterns
    #   - Circular funding
    #   - Cross-funding networks
    #   - Risk scoring
```

**Worker**: `src/core/webhook_worker.py:process_creator_analysis()`

Background worker processes analysis:
- **Zero RPC calls** - Uses only database queries
- **7 analysis queries** - Multi-layer network analysis
- **Non-blocking** - Returns webhook response before analysis completes

---

## 7. RPC Metrics & Optimization

### 7.1 RPC Metrics Recording

**File**: `src/metrics/rpc_metrics_recorder.py`

Tracks all RPC usage across the system:

```python
class RPCMetricsRecorder:
    def record_request(self, method, credits, source_file, latency_ms):
        """Log RPC call for metrics"""
        # Stores in rpc_metrics table
        # Timestamps relative to UK midnight (daily reset)
        # Segments by: method, source_file, latency ranges

    def get_summary(self):
        """Returns aggregated daily metrics"""
        # Total credits used
        # Per-method breakdown
        # Per-source-file breakdown
        # Alert thresholds
```

**Output Table**: `rpc_metrics`
```
id                 - Row identifier
timestamp          - When request occurred
method             - RPC method called (getSignaturesForAddress, etc.)
credits            - Estimated credit cost
source_file        - Python file that made call
latency_ms         - Round-trip time
section            - Component area (extraction, analysis, webhook)
request_size_bytes - Request payload size
response_size_bytes - Response payload size
day_key            - UK date for daily aggregation
reset_baseline_ts  - Timestamp of last reset
```

### 7.2 UK Midnight Auto-Reset

**Timezone**: Europe/London

The system automatically resets daily metrics at **UK midnight (00:00 GMT)**:

```python
def _maybe_auto_reset_at_uk_midnight(self):
    """Check if day changed in UK time"""
    current_day = self._uk_day_key()
    last_day = self._get_state('last_reset_day_key')

    if current_day != last_day:
        # New day! Reset counters
        self.reset_daily()
        self._set_state('last_reset_day_key', current_day)
```

This enables:
- **Daily budget tracking** - Fresh RPC quota each day
- **Usage patterns** - Identify peak activity hours
- **Cost control** - Reset baseline to measure incremental usage

### 7.3 Credit Calculation

**Helius Credit System**:
- **Regular RPC**: Baseline cost (1 credit per call minimum)
- **Batch calls**: Multiplier for batch size
- **Streaming**: Per-byte cost for large responses

```python
def _compute_credits(self, method, request_size, response_size, latency):
    """Calculate estimated credit cost"""

    # Base credit for method
    base = HELIUS_METHOD_CREDITS.get(method, 1)

    # Streaming penalty (per 1KB of data)
    if response_size > 1000:
        streaming_credits = response_size / 1024 * STREAMING_CREDITS_PER_BYTE
        return base + streaming_credits

    return base
```

### 7.4 Optimization & Caching

**Smart Caching in Extractor**:
```python
# funder_incoming_extractor.py: Cost Control via Caching
cached_funders = {}  # Results from previous extractions
fresh_funders_processed = 0
fresh_funders_skipped_budget = 0

for funder in creator_funders:
    if funder in cached_funders:
        # Reuse: 0 RPC cost
        skip()
    elif rpc_budget_remaining > threshold:
        # Process: Incur RPC cost
        extract_and_cache()
        fresh_funders_processed += 1
    else:
        # Defer: Save for next extraction
        fresh_funders_skipped_budget += 1
```

**Component Breakdown**:
- **Creator Funding Extraction** - 40% of RPC usage
- **Funder Transfer Extraction** - 45% of RPC usage
- **Webhook Processing** - 5% of RPC usage
- **Price Updates** - 10% of RPC usage

**Optimization APIs**:
- `/api/rpc-savings/dashboard` - Full optimization view
- `/api/rpc-efficiency/health` - Current health metrics
- `/api/optimization/efficiency-24h` - 24-hour trends

---

## 8. Database Layer & Schema

### 8.1 Database Architecture

**File Location**: `database/flex_complete_database.db` (SQLite)

**Key Features**:
- **WAL Mode**: Write-Ahead Logging for concurrent access
- **Connection Pooling**: Thread-safe database connections
- **PRAGMA Optimization**: Async mode, journal caching
- **150+ Tables**: Comprehensive data model

**Primary Tables** (by category):

#### Token Tables
- `token_analysis` - Token metadata and analysis results (1,700+ records)
- `token_metadata` - Extended token information
- `token_movements` - Price and market cap history

#### Funding Tables
- `creator_funders` - Direct creator funding (43,000+ records)
- `funder_incoming_transfers` - Funder's funding sources (200,000+ records)
- `creator_outgoing_transfers` - Creator cash-outs (7,400+ records)
- `funder_networks` - Atomic funder networks (41,734 records)

#### Network Tables
- `atomic_funder_networks` - Minimal funding groups
- `funder_clusters` - Cross-funding clusters (503 records)
- `network_coordinators` - Individual funder metrics
- `funding_networks` - Complete network analysis (108 networks)
- `funding_networks_list` - Network index

#### Analysis Tables
- `creator_networks` - Coordinated creator detection
- `creator_self_funding` - Self-funding patterns
- `coordinated_funders` - Multi-creator funders
- `creator_tags` - Infrastructure usage markers

#### Webhook Tables
- `sol_transfers` - Raw webhook transfers
- `address_activity` - Real-time address metrics
- `work_queue` - Priority processing queue
- `creator_analysis_queue` - Background analysis tasks
- `webhook_seen_signatures` - Deduplication cache

#### Monitoring Tables
- `rpc_metrics` - RPC usage tracking (14,694+ records)
- `helius_credits_snapshot` - Helius billing snapshots
- `listener_stats` - Listener performance metrics

### 8.2 Key Tables In Detail

#### token_analysis (Core token data)
```sql
CREATE TABLE token_analysis (
    mint TEXT UNIQUE PRIMARY KEY,

    -- Basic Info
    created_at NUM NOT NULL,
    analyzed_at REAL,

    -- Analysis Results
    total_txs INT,
    total_events INT,
    events_parsed INT,

    -- Risk Metrics
    rug_probability REAL,
    risk_level TEXT,  -- CRITICAL, HIGH, MEDIUM, LOW
    rug_indicator TEXT,

    -- Network Involvement
    earliest_tx_creator TEXT,  -- Token creator address
    creator_is_blocked INT,
    network_risk INT,
    connected_malicious_count INT,
    cluster_id TEXT,
    cluster_name TEXT,
    cluster_risk_multiplier REAL,

    -- Price Data
    price_current REAL,
    price_highest REAL,
    market_cap_current REAL,
    market_cap_highest REAL,
    market_cap_highest_at NUM,
    price_updated_at NUM,
    price_source TEXT,

    -- On-Chain Data
    pool_address TEXT,
    bonding_curve_pda TEXT,
    create_tx_signature TEXT,

    -- Post-Migration Analysis
    post_migration_mint_concentration REAL,
    post_migration_unique_minters_ratio REAL,
    post_migration_sell_suppression_ratio REAL,
    post_migration_coverage REAL
);
```

#### creator_funders (Direct funding relationships)
```sql
CREATE TABLE creator_funders (
    creator_address TEXT NOT NULL,
    funder_address TEXT NOT NULL,

    amount_sol REAL,
    transaction_signature TEXT,
    first_detected_at TIMESTAMP,

    is_cex INT,  -- 1 if funder is known exchange
    fully_analyzed INT,  -- 0=pending, 1=complete extraction

    PRIMARY KEY (creator_address, funder_address)
);
```

#### funder_incoming_transfers (Funder's sources)
```sql
CREATE TABLE funder_incoming_transfers (
    funder_address TEXT NOT NULL,
    sender_address TEXT NOT NULL,

    amount_sol REAL,
    transaction_signature TEXT,
    block_time INT,

    is_cex INT,
    classification TEXT,  -- CEX, INFRA, Unknown

    PRIMARY KEY (funder_address, sender_address, transaction_signature)
);
```

#### funder_clusters (Cross-funding groups)
```sql
CREATE TABLE funder_clusters (
    cluster_id TEXT PRIMARY KEY,

    creator_list TEXT,  -- JSON array
    funder_count INT,
    shared_funders TEXT,  -- JSON details

    risk_multiplier REAL,  -- 1.0 to 5.0
    network_tier TEXT,  -- CRITICAL, HIGH, MEDIUM, LOW

    detected_at TIMESTAMP,
    updated_at TIMESTAMP
);
```

#### rpc_metrics (RPC usage tracking)
```sql
CREATE TABLE rpc_metrics (
    id INTEGER PRIMARY KEY,
    timestamp REAL NOT NULL,
    method TEXT NOT NULL,  -- getSignaturesForAddress, etc.
    credits INT NOT NULL,
    source_file TEXT NOT NULL,  -- Python file making call
    latency_ms INT,
    section TEXT,  -- extraction, analysis, webhook, price
    request_size_bytes INT,
    response_size_bytes INT,
    day_key TEXT,  -- UK date for daily aggregation
    reset_baseline_ts REAL
);
```

### 8.3 Indexing Strategy

**Primary Keys**: Fast lookup by main entity
```sql
PRIMARY KEY (creator_address, funder_address)
PRIMARY KEY (mint)
PRIMARY KEY (signature)
```

**Secondary Indexes**: Fast filtering
```sql
CREATE INDEX idx_creator_funders_funder
    ON creator_funders(funder_address);

CREATE INDEX idx_funder_incoming_sender
    ON funder_incoming_transfers(sender_address);

CREATE INDEX idx_rpc_metrics_day
    ON rpc_metrics(day_key, method);
```

### 8.4 Data Consistency

**Foreign Key Constraints**:
```sql
-- Atomic network references funding_networks
FOREIGN KEY (network_id) REFERENCES funding_networks(network_id)

-- Cluster references contain creator_address with token_analysis.earliest_tx_creator
-- No explicit FK due to flexibility, but enforced via application logic
```

**Transaction Integrity**:
- Insertion operations use transactions
- Deduplication via PRIMARY KEY constraints
- Concurrent access managed via WAL mode

---

## 9. Async Processing & Event Flow

### 9.1 Event-Driven Architecture

The system uses multiple async patterns:

#### Pattern 1: Background Tasks (Fire and Forget)
```python
# Triggered when listener starts
asyncio.create_task(self.creator_watch_manager.run_polling_loop())
asyncio.create_task(self.update_live_prices_background())
asyncio.create_task(run_outgoing_extractor(interval_seconds=43200))
```

#### Pattern 2: Webhook Queue (Event Processing)
```python
# Helius webhook arrives
handle_helius_webhook():
    # Immediately save to sol_transfers
    # Deduplicate
    # Enqueue work_queue with priority
    # Return 200 OK (non-blocking)

# Worker processes asynchronously
webhook_worker.run_worker():
    while True:
        work_item = fetch_next_work()
        if work_item:
            process_work_item(work_item)
        await asyncio.sleep(1)
```

#### Pattern 3: Creator Analysis Queue
```python
# Webhook identifies creator
queue_for_creator_analysis(creator_address):
    # Insert into creator_analysis_queue
    # Priority: 15.0
    # Status: 'queued'

# Worker processes
process_creator_analysis(creator_address):
    # Find: Self-funding patterns
    # Find: Coordinated funders
    # Find: Network involvement
    # Cache findings
    # Mark status: 'completed'
```

### 9.2 Creator Watch Manager

**File**: `src/core/pumpfun_curve_listener.py`

Periodically polls creator status:

```python
class CreatorWatchManager:
    async def run_polling_loop(self, poll_interval=30):
        while True:
            creators = get_watched_creators()
            for creator in creators:
                update_creator_status(creator)
                # Update: token_count, last_activity, outgoing_transfers
            await asyncio.sleep(poll_interval)
```

**Metrics Updated**:
- **Last activity** - Most recent transaction
- **Token count** - How many tokens created
- **Outgoing transfers** - Cash-out patterns
- **Malicious flagging** - If creator was reported

### 9.3 Price Update Loop

**File**: `src/core/pumpfun_curve_listener.py:update_live_prices_background()`

Continuous price tracking:

```python
async def update_live_prices_background(self):
    while True:
        # For each token in token_analysis
        tokens = get_recent_tokens(limit=50)
        for token in tokens:
            price = fetch_price(token.mint)

            # Update columns
            token_analysis.price_current = price
            token_analysis.price_updated_at = now()

            # Check if new peak
            if price > token_analysis.price_highest:
                token_analysis.price_highest = price
                token_analysis.market_cap_highest_at = now()
```

### 9.4 Creator Outgoing Extractor

**File**: `src/extractors/creator_outgoing_extractor.py`

The system previously used a background task to scan all creators every 12 hours. This has been **replaced with real-time Helius webhook monitoring** for better efficiency and immediate detection.

**Legacy Background Task** (formerly every 12 hours):
```python
# Location: creator_outgoing_extractor.py:run_forever(interval_seconds=43200)
async def run_forever(interval_seconds: int = 3600):
    ensure_tables()
    while True:
        t0 = time.time()
        try:
            await scan_once()  # Scan all creators once
        except Exception as e:
            print(f"[OUTGOING] ❌ Error: {e}")

        dt = time.time() - t0
        sleep_for = max(5, interval_seconds - dt)
        await asyncio.sleep(sleep_for)
```

**scan_once() Function** (still in code for on-demand scans):
- Gets all creators from `creator_funders` table
- For each creator, extracts transaction signatures via `getSignaturesForAddress`
- Parses outgoing SOL transfers using Helius enhanced API
- Saves to `creator_outgoing_transfers` table
- Max 1,000 creators per cycle with rate limiting (8 RPS)
- Supports concurrent processing (3 concurrent requests)

**Real-Time Replacement**:
Now handled by Helius webhook integration in `webhook_handler.py`:
- Receives SOL transfer events in real-time
- Immediately identifies creator outgoing transfers
- No polling delay (vs 12-hour lag)
- Saves to same `creator_outgoing_transfers` table
- Detects cash-outs instantly

**Identifies**:
- Where creators send their funds
- Self-funding loops (sends back to funder)
- Distribution to new wallets (coordination signal)
- Cash-outs to exchanges (profiteering signal)

### 9.5 Concurrency Management

**Rate Limiting**:
```python
# Webhook worker
RPC_MIN_PRIORITY = 80  # Skip low-priority items
RPC_COOLDOWN_SECONDS = 1800  # 30-min wait between creator scans
MAX_RPC_CALLS_PER_HOUR = 100  # Global cap

# Creator extraction
MAX_CONCURRENT_RPC = 4  # Parallel requests
MAX_RETRIES = 3  # Exponential backoff
RPC_TIMEOUT = 30  # Seconds per request
```

**Database Locking**:
```python
# Prevent duplicate processing
work_queue.locked_until > now()  # Skip if locked
work_queue.next_run_at < now()   # Only process due items

# Priority-based fairness
ORDER BY priority DESC, next_run_at ASC
```

---

## 10. Configuration & Environment

### 10.1 Environment Variables

**Required**:
```bash
# Database
DB_PATH=database/flex_complete_database.db
RPC_METRICS_DB=database/flex_complete_database.db

# Helius RPC
HELIUS_API_KEY=<api-key>
HELIUS_MONITORING_API_KEY=<api-key>
HELIUS_PROJECT_ID=<project-id>
HELIUS_WEBHOOK_AUTH=Bearer <webhook-token>

# Solana
SOLANA_RPC_ENDPOINT=https://api.helius.xyz/v0/access_token/...
RPC_URLS=https://api.helius.xyz/v0/access_token/...

# Optional
SOLSCAN_API_KEY=<key>
SNS_PRIMARY_ENDPOINT=<endpoint>
```

### 10.2 Configuration Files

**migration_settings.json** - Flask app settings
```json
{
    "listen_to_launches": true,
    "listen_to_price_updates": true,
    "auto_extract_funding": true
}
```

### 10.3 Key Constants

**Extraction Parameters**:
```python
MINIMUM_SOL = 0.001  # Filters 30-40% of micro-transactions
MAX_PAGES = 100      # Signature history depth
MAX_CONCURRENT_RPC = 4  # Parallel requests

# Extraction Cost Control
MAX_FRESH_FUNDERS_PER_CREATOR = 10
MAX_TX_SIGS_PER_FUNDER = 100
DEFAULT_CONCURRENCY = 2
RPC_COOLDOWN_SECONDS = 1800  # 30 minutes

# Clustering Thresholds
MIN_CREATORS_FOR_RECIPIENT_HUB = 3
MIN_CREATORS_FOR_ATOMIC_FUNDER_NETWORK = 3
MIN_JACCARD = 0.2  # Overlap threshold
MIN_OVERLAP_CREATORS = 2
```

**Risk Multipliers**:
```python
CEX_FUNDER_MULTIPLIER = 2.0  # CEX funders are 2x risk
EXCLUDE_CEX_FROM_CLUSTERING = False  # Include CEX in analysis
```

---

## 11. Error Handling & Edge Cases

### 11.1 RPC Failures

**Strategy**: Graceful degradation with retries

```python
def get_with_retry(func, max_retries=3, backoff=2):
    """Exponential backoff retry logic"""
    for attempt in range(max_retries):
        try:
            return func()
        except RpcException as e:
            if attempt == max_retries - 1:
                log_error(f"Failed after {max_retries} attempts")
                return None

            wait_time = backoff ** attempt
            time.sleep(wait_time)
            continue
```

**Outcomes**:
- **Success**: Data extracted and saved
- **Failure**: `fully_analyzed = 0`, available for retry
- **Timeout**: Deferred to next cycle

### 11.2 Incomplete Data

**Signature**: When a creator has 1,000+ transaction signatures

```python
# Only scan first 100 signatures (cost control)
signatures = get_signatures(creator, limit=100)
if len(signatures) == 100:
    # Mark: "Partial extraction - more signatures exist"
    log_partial_extraction(creator)
    # Next cycle can retry with different params
```

**Impact**:
- Captures major funders (they appear early)
- May miss small later-stage funders
- Still produces valid network analysis

### 11.3 Failed Transactions

**Handling**:
```python
# Transaction may have multiple instructions
# Some may fail, some may succeed

for instr in tx['transaction']['message']['instructions']:
    # Check instruction status in meta
    if instr_meta['error'] is None:
        # Process: This instruction succeeded
        if instr['program'] == 'system':
            extract_transfer()
    else:
        # Skip: This instruction failed
        continue
```

### 11.4 Dust & Spam Filtering

**Minimum Thresholds**:
```python
# Filter 1: Minimum SOL amount
if amount_sol < MINIMUM_SOL:  # 0.001
    skip()  # Filters ~30-40% of transactions

# Filter 2: Deduplication
if signature in webhook_seen_signatures:
    skip()  # Prevents duplicate processing

# Filter 3: Invalid addresses
if not is_valid_solana_address(address):
    skip()  # Prevents malformed data
```

### 11.5 Concurrent Processing Conflicts

**Problem**: Multiple processes updating same token

```python
# Solution: Primary key enforces uniqueness
creator_funders (creator_address, funder_address)
    → Duplicate insert throws error
    → Caller handles gracefully

# Solution: Row locking via locked_until
work_queue.locked_until = now() + 300
    → Only one worker processes simultaneously
    → Auto-release after timeout
```

---

## 12. Performance Characteristics

### 12.1 Throughput Metrics

**Token Detection**:
- **Latency**: <500ms from WebSocket event to database insert
- **Capacity**: Handles all new Pump.Fun launches (est. 500+ per day)

**Funding Extraction**:
- **RPC calls per token**: 5-50 (depending on funder count)
- **Cost per token**: 20-200 credits
- **Processing time**: 2-10 seconds per token

**Network Clustering**:
- **Computation time**: <1 second for 100,000 edges
- **Update frequency**: Real-time on new token
- **Memory**: <100MB for full network graph

### 12.2 Query Performance

**Database Query Times**:
```sql
-- Top Funding Hubs (complex join, 8 tables)
SELECT * FROM top_hubs
-- Time: ~500ms for 20 results
-- Uses: Indexes on creator_address, funder_address

-- Creator Details (deep funding analysis)
SELECT * FROM creator_funding_analysis
-- Time: ~200ms for single creator
-- Uses: Primary key lookup

-- Token Analysis (full scan)
SELECT * FROM token_analysis ORDER BY created_at DESC LIMIT 25
-- Time: ~50ms
-- Uses: created_at index
```

**Optimization Techniques**:
- **Indexing**: Primary + secondary indexes on hot columns
- **Filtering**: Push filters to WHERE clause
- **Joins**: Use primary key joins when possible
- **Pagination**: LIMIT 25-100 for UI pages
- **Caching**: Results cached in Python for 5-60 seconds

### 12.3 RPC Credit Efficiency

**Credit Consumption Breakdown**:
- **Creator funding extraction**: 40% (getSignaturesForAddress)
- **Funder transfer extraction**: 45% (getSignaturesForAddress x funder count)
- **Webhook processing**: 5% (real-time transfers)
- **Price updates**: 10% (various APIs)

**Optimization Savings**:
- **Creator funders cache**: Skip 60-70% of extractors (cached)
- **Batch calls**: 2-3 calls vs 10-20 individual
- **Smart defer**: Skip 80-90% of secondary funders
- **Result**: ~27K credits/month for 100 active creators

### 12.4 Memory Usage

**Per-Process**:
- **Flask app**: 150-200 MB (with route caches)
- **Listener**: 100-150 MB (with live prices)
- **Webhook worker**: 50-100 MB (minimal state)
- **Extractor**: 200-300 MB (paginated results)

**Database**:
- **File size**: 3-5 GB (150+ tables, 1M+ records)
- **Connection pool**: 5-10 active connections
- **Cache**: SQLite query plan cache (~10 MB)

---

## 13. Extending the System

### 13.1 Adding New Funding Sources

**Goal**: Track funding from a new program (e.g., Magic Eden Launchpad)

**Steps**:

1. **Identify the program**
   ```python
   # Find program ID
   MAGICEDEN_LAUNCHPAD = "magic4sxZXvDJIVHBzZMLmKqkW9gsx6gVWYVx3qKfv"
   ```

2. **Create extractor class**
   ```python
   # src/extractors/magiceden_launchpad_extractor.py
   class MagicEdenExtractor:
       def extract_for_creator(self, creator_address):
           # Query: Who funded via Magic Eden?
           # Save to: funder_incoming_transfers (classification="MAGICEDEN")
   ```

3. **Register in listener**
   ```python
   # src/core/pumpfun_curve_listener.py
   from src.extractors.magiceden_launchpad_extractor import MagicEdenExtractor

   # Call after creator_funders extraction
   magiceden_extractor.extract_for_creator(creator)
   ```

4. **Update UI**
   ```python
   # src/utils/infra_mapping.py
   INFRA_PROGRAMS['magic_eden'] = {
       'name': 'Magic Eden Launchpad',
       'color': '#FF6D00'
   }
   ```

### 13.2 Adding New Risk Detection Rules

**Goal**: Flag creators that sell their tokens immediately

**Steps**:

1. **Create detection function**
   ```python
   # src/analysis/risk_detection.py
   def detect_quick_selloff(creator_address, token_mint):
       """Check if creator dumped their tokens"""
       outgoing = get_creator_transfers(creator_address)
       token_balance = get_token_balance(creator_address, token_mint)

       if token_balance == 0 and len(outgoing) > 0:
           return {
               'finding': 'quick_selloff',
               'risk_level': 'CRITICAL',
               'description': 'Creator immediately sold token'
           }
   ```

2. **Integrate with findings**
   ```python
   # src/core/main.py:api_creator_recent_checks()
   findings = []

   # Existing findings...
   findings.extend(detect_coordinated_funders(...))

   # New findings
   findings.extend(detect_quick_selloff(...))

   return jsonify({'findings': findings})
   ```

3. **Display in UI**
   ```html
   <!-- src/core/main.py HTML_TEMPLATE -->
   <div class="finding" style="border-left: 3px solid #ef4444;">
       <strong>Quick Selloff</strong>
       Creator sold tokens immediately after launch
   </div>
   ```

### 13.3 Adding New Visualization Pages

**Goal**: Create a "Funder Network Graph" page

**Steps**:

1. **Create Flask route**
   ```python
   # src/core/main.py
   @app.route('/funder-graph/<funder_address>')
   def funder_graph_view(funder_address: str):
       """Visualize funder's network"""
       funder = get_funder_data(funder_address)
       creators = get_creators_funded_by(funder_address)
       senders = get_senders_to_funder(funder_address)

       return render_template('funder_graph.html', {
           'funder': funder,
           'creators': creators,
           'senders': senders
       })
   ```

2. **Create template**
   ```html
   <!-- templates/funder_graph.html -->
   <div id="graph-container"></div>

   <script src="/static/d3.min.js"></script>
   <script>
       // D3 visualization code
       const nodes = [
           {id: senders, type: 'sender'},
           {id: funder, type: 'funder'},
           {id: creators, type: 'creator'}
       ];

       // Render force-directed graph
       d3.force()...
   </script>
   ```

3. **Add navigation button**
   ```python
   # Update main.py HTML_TEMPLATE
   '<a href="/funder-graph/XXX">View Network Graph</a>'
   ```

### 13.4 Adding New Data Exports

**Goal**: Export funding analysis as CSV

**Steps**:

1. **Create export function**
   ```python
   # src/utils/export.py
   def export_creator_funding_csv(creator_address):
       """Export creator's funding network"""
       rows = []

       funders = get_creator_funders(creator_address)
       for funder in funders:
           senders = get_senders_to_funder(funder['funder_address'])
           for sender in senders:
               rows.append({
                   'creator': creator_address,
                   'funder': funder['funder_address'],
                   'sender': sender['sender_address'],
                   'amount_sol': sender['amount_sol'],
                   'timestamp': sender['block_time']
               })

       return to_csv(rows)
   ```

2. **Create endpoint**
   ```python
   # src/core/main.py
   @app.route('/api/creator-funding-export/<creator_address>')
   def api_creator_funding_export(creator_address: str):
       """Export funding as CSV"""
       csv_data = export_creator_funding_csv(creator_address)
       return Response(csv_data, mimetype='text/csv')
   ```

3. **Add UI button**
   ```html
   <a href="/api/creator-funding-export/ADDRESS" download>
       📥 Export as CSV
   </a>
   ```

### 13.5 Adding New Monitoring Alerts

**Goal**: Alert when a funder suddenly increases activity

**Steps**:

1. **Define alert condition**
   ```python
   # src/monitoring/alert_rules.py
   def alert_sudden_activity_spike(funder_address):
       """Check if funder's activity increased 10x"""
       activity_24h_old = get_activity_before(funder_address, days=1)
       activity_24h_new = get_activity_last_24h(funder_address)

       if activity_24h_new > activity_24h_old * 10:
           return {
               'alert_type': 'ACTIVITY_SPIKE',
               'funder': funder_address,
               'old_activity': activity_24h_old,
               'new_activity': activity_24h_new,
               'severity': 'HIGH'
           }
   ```

2. **Store alert**
   ```python
   # src/monitoring/alerts.py
   def create_alert(alert_dict):
       conn = sqlite3.connect(DB_PATH)
       conn.execute("""
           INSERT INTO monitoring_alerts
           (funder_address, alert_type, severity, data, created_at)
           VALUES (?, ?, ?, ?, ?)
       """, (alert_dict['funder'], alert_dict['alert_type'],
             alert_dict['severity'], json.dumps(alert_dict), time.time()))
       conn.commit()
   ```

3. **Display in UI**
   ```python
   # src/core/main.py
   @app.route('/api/alerts')
   def api_alerts():
       """Get active alerts"""
       alerts = get_recent_alerts(hours=24)
       return jsonify({'alerts': alerts})
   ```

---

## Summary

**FLEX** is a production-grade Solana token analysis system that combines:

- **Real-time detection** via WebSocket and Helius webhooks
- **Multi-layer funding extraction** (creator → funder → sender)
- **Advanced clustering** for coordinated funder detection
- **RPC optimization** with smart caching and cost control
- **Rich web UI** with 15+ analysis pages
- **60+ REST APIs** for data access
- **Comprehensive monitoring** of RPC usage and system health

The architecture is designed for **scalability**, **cost efficiency**, and **accuracy** in detecting suspicious funding patterns across Pump.Fun tokens on Solana.

