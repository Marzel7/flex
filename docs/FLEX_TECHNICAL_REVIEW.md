# FLEX System - Technical Architecture Review

**Professional technical documentation for the Solana token funding network analysis system**

---

## 1. System Overview

### Purpose
FLEX is a production-grade Solana token analysis system that detects, analyzes, and monitors funding networks for Pump.Fun tokens. The system identifies suspicious funding patterns, coordinated funder relationships, and potential pump-and-dump schemes through multi-layer funding extraction and network clustering analysis.

### Key Responsibilities
1. **Real-time token detection** - Monitor new Pump.Fun token launches via WebSocket
2. **Funding relationship extraction** - Trace the complete flow of money from senders through funders to token creators
3. **Network analysis** - Identify coordinated funding groups and cluster relationships
4. **Risk assessment** - Calculate rug probability, risk levels, and coordination metrics
5. **RPC optimization** - Manage Helius API credits efficiently with caching and smart rate limiting
6. **Real-time webhooks** - Ingest Helius webhook events for instant transfer detection
7. **Web dashboard** - Provide visual analysis through 15+ web pages and 60+ REST APIs

### Core Statistics
- **150+ database tables** for comprehensive data storage
- **41,734 funder networks** tracked across Solana
- **503 super clusters** (coordinated funding groups)
- **43 CEX wallets** mapped (20 exchanges)
- **59 INFRA programs** tracked (Jito, Axiom, DeBridge, Meteora, etc.)

---

## 2. Architecture Overview

### Major Components

```
┌─────────────────────────────────────────────────────────────┐
│                     FLEX System Architecture                │
└─────────────────────────────────────────────────────────────┘

┌────────────────────────────┐
│   Event Sources            │
├────────────────────────────┤
│ • Solana WebSocket         │  Detects new tokens
│ • Helius Webhooks          │  Real-time transfers
│ • Price APIs               │  Market data
└────────────┬───────────────┘
             │
             ▼
┌────────────────────────────────────┐
│   Core Processing Layer            │
├────────────────────────────────────┤
│ • Token Detection (pumpfun_curve_  │
│   listener.py)                     │
│ • Funding Extraction (realtime_    │
│   creator_funding_extractor.py)    │
│ • Network Analysis (cross_funding_ │
│   network_analyzer.py)             │
│ • Webhook Handler (webhook_        │
│   handler.py)                      │
└────────────┬───────────────────────┘
             │
             ▼
┌────────────────────────────────────┐
│   Background Processing            │
├────────────────────────────────────┤
│ • Creator Watch Manager            │
│ • Webhook Worker                   │
│ • Creator Analysis Queue Processor │
└────────────┬───────────────────────┘
             │
             ▼
┌────────────────────────────────────┐
│   Data Storage Layer               │
├────────────────────────────────────┤
│ • SQLite Database (150+ tables)    │
│ • RPC Metrics Table                │
│ • Creator Analysis Queue           │
│ • Work Queue for Async Tasks       │
└────────────┬───────────────────────┘
             │
             ▼
┌────────────────────────────────────┐
│   Flask Web Application            │
├────────────────────────────────────┤
│ • Web UI (15+ pages)               │
│ • REST APIs (60+ endpoints)        │
│ • Real-time Dashboards             │
└────────────────────────────────────┘
```

### Component Interaction Model

**Token Lifecycle in FLEX**:
```
WebSocket Event
    │
    ├─→ Create token_analysis record
    │
    ├─→ Extract creator funders
    │   └─→ Query RPC for transfers to creator
    │   └─→ Save to creator_funders table
    │
    ├─→ Queue funder transfer extraction
    │   └─→ Background worker extracts each funder's sources
    │   └─→ Save to funder_incoming_transfers table
    │
    ├─→ Build network clusters
    │   └─→ Identify shared funders across creators
    │   └─→ Create coordinated network edges
    │
    ├─→ Real-time webhook monitoring
    │   └─→ Track creator outgoing transfers instantly
    │   └─→ Detect cash-outs and distribution patterns
    │
    └─→ Display in web UI
        └─→ Show funding progress
        └─→ Display risk metrics
        └─→ Visualize network relationships
```

---

## 3. Module Breakdown

### 3.1 Core Token Detection

**Module**: `src/core/pumpfun_curve_listener.py`

**Purpose**: Monitor Solana blockchain for new Pump.Fun token launches and trigger the analysis pipeline.

**Key Classes**:
- `PumpFunCurveListener` - Main WebSocket listener that detects token creation events
- `CreatorWatchManager` - Background polling manager for creator status updates

**Key Responsibilities**:
1. Establish WebSocket connection to Solana RPC
2. Filter for Pump.Fun token migration transactions
3. Extract creator address from migration data
4. Create initial token_analysis record
5. Trigger funding extraction pipeline
6. Maintain live price tracking
7. Monitor for rug indicators (quick peaks, low market caps)

**Concurrency Model**:
- AsyncIO for non-blocking WebSocket operations
- Spawns background tasks for price updates and creator monitoring
- 30-second polling interval for creator watch manager

**RPC Interaction**:
- WebSocket for real-time event streaming
- Regular RPC calls for transaction details
- Metrics recorded via `record_request()` for cost tracking

### 3.2 Funding Extraction Pipeline

**Module**: `src/extractors/realtime_creator_funding_extractor.py`

**Purpose**: Extract all wallet addresses that funded a token creator, establishing the direct funding relationship.

**Key Classes**:
- `RealTimeCreatorFundingExtractor` - Main extraction engine
- `DomainResolver` - Caches domain names and address mappings
- `RequestRecord` - Tracks RPC metrics for each request

**Key Responsibilities**:
1. Query all transaction signatures for creator address
2. Filter for native SOL transfer instructions
3. Extract sender → creator → amount relationships
4. Identify dust transfers and filter them (< 0.001 SOL)
5. Classify senders as CEX, INFRA, or Unknown
6. Cache results to avoid re-processing
7. Record RPC metrics for cost tracking

**Data Processing**:
- Paginated signature fetching (100 signatures per page)
- Batch transaction parsing (100 sigs per batch RPC call)
- Exponential backoff retry on RPC failures
- Automatic CEX detection via Solscan API lookups

**Performance Optimizations**:
- Caches extracted creators to avoid repeated queries
- Uses `fully_analyzed` flag to skip already-processed funders
- Implements domain name caching with TTL
- Batches RPC calls for efficiency

### 3.3 Funder Transfer Extraction

**Module**: `src/extractors/funder_incoming_extractor.py`

**Purpose**: For each creator funder, extract where they received their money (identifying senders).

**Key Classes**:
- `FunderIncomingExtractor` - Secondary extraction engine
- Cost control and budget management system

**Key Responsibilities**:
1. For each creator_funder record, query their incoming transfers
2. Identify all addresses that sent SOL to the funder
3. Classify senders by source (CEX, INFRA, Unknown)
4. Implement cost control to avoid excessive RPC spending
5. Cache previously extracted funders
6. Defer low-priority funders when budget constrained

**Cost Control Mechanisms**:
```
MAX_FRESH_FUNDERS_PER_CREATOR = 10    # Limit new funders processed
MAX_TX_SIGS_PER_FUNDER = 100          # Limit transaction history depth
DEFAULT_CONCURRENCY = 2               # Parallel request limit
RPC_COOLDOWN_SECONDS = 1800           # 30-min wait between rescans
```

**Smart Deferral Logic**:
- Maintains `fresh_funders_processed` and `fresh_funders_skipped_budget` counters
- Prioritizes high-value funders (large SOL amounts)
- Defers processing when RPC budget nearly exhausted
- Resumable on next extraction cycle

### 3.4 Real-Time Webhook Handler

**Module**: `src/core/webhook_handler.py`

**Purpose**: Receive and process Helius webhook events for real-time SOL transfer monitoring.

**Key Functions**:
- `handle_helius_webhook()` - Webhook endpoint that processes raw events
- `extract_system_transfers()` - Extracts native SOL transfers from transaction
- `update_address_activity()` - Updates real-time activity metrics
- `enqueue_work()` - Queues high-priority addresses for analysis
- `queue_for_creator_analysis()` - Queues new addresses for deep analysis

**Key Responsibilities**:
1. Validate webhook authentication headers
2. Extract native SOL transfers from account data
3. Deduplicate transactions by signature
4. Filter dust transfers (< 0.001 SOL)
5. Update address activity metrics
6. Calculate priority scores for processing
7. Queue addresses for background analysis
8. Return 200 immediately (non-blocking)

**Performance Characteristics**:
- Throughput: 1,000+ transactions/second
- Per-transaction overhead: 1-2ms
- Deduplication: O(1) instant lookup via primary key

**Output Tables**:
- `sol_transfers` - Raw webhook transfers
- `address_activity` - Real-time metrics
- `work_queue` - Queued addresses by priority
- `creator_analysis_queue` - Deep analysis queue

### 3.5 Network Analysis & Clustering

**Module**: `src/analysis/cross_funding_network_analyzer.py`

**Purpose**: Build network relationships from extracted funding data and identify coordinated funding groups.

**Key Classes**:
- `CrossFundingClusterAnalyzer` - Main clustering engine
- `UnionFind` - Disjoint set data structure for clustering
- `NetworkCoordinator` - Funder risk metrics
- `FunderCluster` - Cross-funding group

**Key Algorithms**:

1. **Atomic Funder Networks**
   - Groups funders that share the same sender
   - Identifies minimal connected sets
   - Threshold: 3+ creators funded together

2. **Funder Clusters**
   - Identifies creators with significantly overlapping funders
   - Uses Jaccard similarity: `|Shared Funders| / |Union of Funders|`
   - Threshold: 0.2 (20% minimum overlap)

3. **Network Coordinators**
   - Individual funders supporting multiple creators
   - Risk multiplier: 2.0x for CEX funders
   - Classification: CRITICAL (50+ creators), HIGH (10-50), MEDIUM (3-10), LOW (1-2)

**Data Outputs**:
- `atomic_funder_networks` - Minimal funding groups
- `funder_clusters` - Cross-funding clusters
- `network_coordinators` - Individual coordinator metrics
- `creator_networks` - Coordinated creator relationships

### 3.6 Background Worker System

**Module**: `src/core/webhook_worker.py`

**Purpose**: Process queued work items with priority scheduling and RPC rate limiting.

**Key Functions**:
- `run_worker()` - Main worker loop
- `fetch_next_work()` - Get next queued item
- `process_work_item()` - Execute work with rate limiting
- `fetch_next_creator_analysis()` - Get analysis tasks
- `process_creator_analysis()` - Run creator analysis

**Key Responsibilities**:
1. Fetch high-priority work items from queue
2. Lock items to prevent duplicate processing
3. Execute work with RPC rate limiting
4. Process creator analysis queue items
5. Update metrics and completion status
6. Implement priority-based scheduling

**Rate Limiting Strategy**:
```
RPC_MIN_PRIORITY = 80         # Skip items below this priority
RPC_COOLDOWN_SECONDS = 1800   # 30-min wait between rescans
MAX_RPC_CALLS_PER_HOUR = 100  # Global cap
```

**Creator Analysis Processing**:
- Zero RPC calls (database-only analysis)
- 7 database queries for multi-layer network detection
- Non-blocking (webhook returns before analysis completes)
- Caches findings for API retrieval

### 3.7 RPC Metrics & Cost Tracking

**Module**: `src/metrics/rpc_metrics_recorder.py`

**Purpose**: Track RPC usage across all system components for cost optimization and billing.

**Key Classes**:
- `RPCMetricsRecorder` - Records and aggregates RPC metrics
- `RequestRecord` - Individual RPC request metadata
- `SectionStats` - Per-section aggregations

**Key Responsibilities**:
1. Record every RPC call with method, credits, latency
2. Segment metrics by source file and component section
3. Track UK midnight daily resets automatically
4. Calculate credit estimates based on request/response size
5. Provide per-section and per-method breakdowns
6. Generate alerts for budget overruns
7. Support reset baselines for comparison

**UK Midnight Auto-Reset**:
- Daily metrics reset at 00:00 GMT
- Enables per-day budget tracking
- Automatic timezone handling

**Output Data**:
- `rpc_metrics` table - Individual request logs
- Dashboard views - Aggregated metrics by section
- Alert system - High-usage notifications

### 3.8 Infrastructure Detection

**Module**: `src/analysis/automatic_cex_detection.py`

**Purpose**: Identify and classify wallet addresses as CEX, INFRA, or Unknown sources.

**Detection Methods**:
1. **CEX Detection** - Solscan API lookups for exchange labels
2. **INFRA Detection** - Program interaction analysis
   - Jito MEV-Share (tip payments)
   - Axiom MEV-Share
   - DeBridge transfers
   - Meteora DLMM pools

**Output Table**: `creator_tags`
```
creator_address - Address involved
tag             - uses_jitotip, uses_axiom, uses_debridge, uses_meteora
description     - Details about usage
amount_sol      - Total amount involved
```

---

## 4. Data Flow

### End-to-End Token Analysis Flow

```
1. TOKEN DETECTION
   WebSocket Event → Extract mint + creator → Create token_analysis record

2. INITIAL FUNDING EXTRACTION
   For creator: Query getSignaturesForAddress
   → Extract native SOL transfers
   → Filter dust (< 0.001 SOL)
   → Save to creator_funders table
   → Mark creator_funders.fully_analyzed = 0 (pending secondary extraction)

3. SECONDARY FUNDING EXTRACTION (Background Worker)
   For each creator_funder:
      If fresh_funders < MAX_FRESH_FUNDERS_PER_CREATOR:
         Query getSignaturesForAddress for funder
         → Extract incoming transfers
         → Classify source (CEX/INFRA/Unknown)
         → Save to funder_incoming_transfers
         → Mark creator_funders.fully_analyzed = 1
      Else:
         Defer to next cycle

4. NETWORK CLUSTERING
   Analyze funding relationships:
   → Find atomic funder networks (shared senders)
   → Identify funder clusters (overlapping creators)
   → Calculate network risk multipliers
   → Save to network tables

5. REAL-TIME MONITORING (Webhook)
   SOL transfer event → Extract system transfers
   → Identify creator outgoing transfers
   → Update address_activity metrics
   → Queue for creator analysis if high-priority
   → Save to sol_transfers + creator_outgoing_transfers

6. CREATOR ANALYSIS (Background Worker)
   For queued creator:
      Find self-funding patterns
      → Find coordinated funders
      → Find network involvement
      → Cache findings in database
      → Mark creator_analysis_queue.status = 'completed'

7. API RESPONSE & DISPLAY
   Flask endpoint queries:
   → token_analysis (token metrics)
   → creator_funders (who funded)
   → funder_incoming_transfers (funding sources)
   → creator_networks (coordination)
   → funder_clusters (cluster involvement)
   → Returns combined findings to web UI
```

### Webhook Event Processing Flow

```
Helius Webhook
    ↓
validate_auth_header(x-signature)
    ↓
extract_system_transfers(tx_metadata)
    ├─→ For each instruction:
    │   └─→ If program == 'system':
    │       └─→ Extract source, destination, amount
    │       └─→ Filter dust (< 0.001 SOL)
    │
    ├─→ Deduplicate by signature (sol_transfers.signature PRIMARY KEY)
    │
    ├─→ Save to sol_transfers table
    │
    ├─→ update_address_activity()
    │   ├─→ Update tx_5m, tx_1h, tx_24h counters
    │   ├─→ Update sol_in_* and sol_out_* metrics
    │   └─→ Track last_seen_at timestamp
    │
    ├─→ enqueue_work()
    │   ├─→ Calculate priority score
    │   ├─→ Insert into work_queue if high priority
    │   └─→ Set next_run_at timestamp
    │
    ├─→ queue_for_creator_analysis()
    │   ├─→ Identify if source/dest is creator
    │   ├─→ Insert into creator_analysis_queue
    │   └─→ Priority: 15.0 (high)
    │
    └─→ Return 200 OK (non-blocking)

Background Worker (async):
    └─→ fetch_next_work()
        └─→ process_work_item() with RPC rate limiting
            └─→ Process creator analysis
            └─→ Mark work_queue.completed_at
```

---

## 5. Background Workers & Async Processing

### 5.1 Architecture Model

The system uses multiple concurrent async patterns:

**Pattern 1: Fire-and-Forget Background Tasks**
- Spawned when listener starts
- Run indefinitely in background
- Examples: CreatorWatchManager, WebSocket listener

**Pattern 2: Event-Driven Work Queue**
- Events trigger work insertion
- Background worker processes with priority
- Rate limiting prevents RPC overuse
- Examples: Funder transfer extraction

**Pattern 3: Queued Analysis Tasks**
- Webhook identifies analysis candidates
- Creator analysis queue holds tasks
- Worker processes with database-only queries
- Non-blocking (webhook returns immediately)

### 5.2 Worker Implementation Details

**Creator Watch Manager**:
```
Interval: Every 30 seconds
- Get all watched creators
- Update creator status (token count, last activity)
- Track outgoing transfer patterns
- Flag malicious creators if reported
```

**Webhook Worker**:
```
Loop: Continuous
- fetch_next_work() from priority queue
- Check if work priority >= RPC_MIN_PRIORITY (80)
- Check if RPC cooldown elapsed
- Process work item with rate limiting
- Lock work item during processing
- Mark completed with timestamp
- Sleep 1 second between iterations
```

**Creator Analysis Queue Processor**:
```
Loop: Part of webhook worker
- fetch_next_creator_analysis() with status='queued'
- Update status to 'processing'
- Execute 7 database queries (zero RPC calls):
  1. Find self-funding patterns
  2. Find coordinated funders
  3. Find network involvement
  4. Calculate risk scores
  5. Tag infrastructure usage
  6. Identify malicious connections
  7. Cache findings
- Mark status='completed'
- Return results via API
```

### 5.3 Concurrency & Rate Limiting

**Global RPC Rate Limiting**:
```python
MAX_RPC_CALLS_PER_HOUR = 100
RPC_COOLDOWN_SECONDS = 1800  # 30 minutes between rescans
RPC_MIN_PRIORITY = 80        # Skip low-priority items
```

**Per-Creator Cost Control**:
```python
MAX_FRESH_FUNDERS_PER_CREATOR = 10    # Limit new funders processed
MAX_TX_SIGS_PER_FUNDER = 100          # Limit transaction history
DEFAULT_CONCURRENCY = 2               # Parallel requests
```

**Database Locking for Concurrency**:
```sql
-- Work queue item locking
work_queue.locked_until > now()  # Skip if currently processing
work_queue.next_run_at < now()   # Only process due items

-- Priority-based fairness
ORDER BY priority DESC, next_run_at ASC
```

---

## 6. Database Layer

### 6.1 Database Architecture

**File**: `database/flex_complete_database.db` (SQLite)

**Configuration**:
- **Mode**: WAL (Write-Ahead Logging) for concurrent access
- **Synchronous**: NORMAL (balance between safety and speed)
- **Timeout**: 30 seconds for lock acquisition
- **Row Factory**: Row objects for named column access

**Key Characteristics**:
- 150+ tables organized by functional area
- Compound primary keys for uniqueness
- Strategic indexes on hot columns
- Proper foreign key relationships

### 6.2 Core Table Relationships

```
token_analysis (mint)
├─ earliest_tx_creator
│  └─ creator_funders (creator_address)
│     ├─ funder_address
│     │  └─ funder_incoming_transfers (funder_address)
│     │     └─ sender_address
│     │        └─ funder_networks (funder_address)
│     │
│     └─ creator_outgoing_transfers (creator_address)
│
└─ funder_clusters (cluster_id)
   ├─ creator_list (JSON array)
   └─ network_risk

creator_networks
├─ creator_address
├─ connected_creators
└─ shared_destinations
```

### 6.3 Critical Tables

**token_analysis** - Core token data
```
mint (PRIMARY KEY)
├─ Basic: created_at, analyzed_at
├─ Risk: rug_probability, risk_level, rug_indicator
├─ Network: earliest_tx_creator, cluster_id, cluster_name
├─ Price: price_current, price_highest, market_cap_highest
└─ On-chain: pool_address, create_tx_signature
```

**creator_funders** - Direct funding relationships
```
(creator_address, funder_address) PRIMARY KEY
├─ amount_sol
├─ transaction_signature
├─ is_cex (1 if exchange, 0 otherwise)
├─ fully_analyzed (0=pending, 1=complete)
└─ first_detected_at
```

**funder_incoming_transfers** - Funder's funding sources
```
(funder_address, sender_address, transaction_signature) PRIMARY KEY
├─ amount_sol
├─ block_time
├─ is_cex
└─ classification (CEX, INFRA, Unknown)
```

**creator_outgoing_transfers** - Where creators send funds
```
transaction_signature PRIMARY KEY
├─ creator_address
├─ recipient_address
├─ amount_sol
├─ block_time
├─ recipient_type
└─ is_cex
```

**funder_clusters** - Cross-funding groups
```
cluster_id PRIMARY KEY
├─ creator_list (JSON array)
├─ funder_count
├─ risk_multiplier (1.0 to 5.0)
└─ network_tier (CRITICAL, HIGH, MEDIUM, LOW)
```

**rpc_metrics** - RPC usage tracking
```
id PRIMARY KEY
├─ timestamp
├─ method (RPC method called)
├─ credits (estimated cost)
├─ source_file (Python file making call)
├─ latency_ms
├─ section (component area)
└─ day_key (UK date for aggregation)
```

**work_queue** - Priority-based work scheduling
```
address PRIMARY KEY
├─ priority (calculated score)
├─ reason (why queued)
├─ next_run_at
├─ locked_until
└─ completed_at
```

**creator_analysis_queue** - Background analysis tasks
```
id PRIMARY KEY
├─ creator_address
├─ status (queued, processing, completed)
├─ findings (JSON cache)
├─ attempted_at
└─ completed_at
```

### 6.4 Indexing Strategy

**Primary Key Indexes** (automatic):
- Fast lookup by main entity
- Enforces uniqueness
- Used for deduplication

**Secondary Indexes**:
```sql
CREATE INDEX idx_creator_funders_funder
    ON creator_funders(funder_address);

CREATE INDEX idx_funder_incoming_sender
    ON funder_incoming_transfers(sender_address);

CREATE INDEX idx_rpc_metrics_day
    ON rpc_metrics(day_key, method);

CREATE INDEX idx_sol_transfers_block_time
    ON sol_transfers(block_time DESC);
```

### 6.5 Data Consistency Patterns

**Deduplication**:
- Primary key constraints prevent duplicates
- Webhook uses signature as primary key
- Creator funders use (creator, funder) pair
- Application-level dedup checks before insert

**Transaction Integrity**:
- Multi-step operations wrapped in transactions
- Foreign key constraints enforce referential integrity
- WAL mode prevents data corruption
- Busy timeout handles concurrent access

---

## 7. External Services & APIs

### 7.1 Helius RPC Provider

**Integration Points**:
1. **Helius HTTP RPC**
   - `getSignaturesForAddress` - Fetch transaction signatures
   - `getTransaction` - Get transaction details
   - Batch calls for efficiency

2. **Helius Enhanced API**
   - `/v0/transactions` endpoint with Helius enhancements
   - Parses transaction metadata
   - Returns instruction details

3. **Helius Webhooks**
   - Real-time SOL transfer events
   - Configurable account subscriptions
   - Authentication via signature validation

**Authentication**:
```
HELIUS_API_KEY=<api-key>                    # Main RPC key
HELIUS_MONITORING_API_KEY=<monitoring-key>  # Billing tracking key
HELIUS_WEBHOOK_AUTH=Bearer <token>          # Webhook signature
```

**Credit Model**:
- Base: 1 credit per call minimum
- Streaming: Per-byte cost for large responses
- Batch multiplier for batch calls
- Helius monitoring API tracks actual usage

### 7.2 Solscan API

**Integration**: `src/analysis/automatic_cex_detection.py`

**Purpose**: Identify known exchange wallets and labeled addresses

**Queries**:
- `GET /api/v2/search?query=<address>` - Look up address labels
- Returns: account type, labels, exchange name

**Caching**:
- Caches results to avoid repeated queries
- TTL-based expiration
- Stores in database for persistence

### 7.3 Solana RPC APIs

**Primary Endpoint**: Helius (via `HELIUS_API_KEY`)

**Fallback Endpoints**:
```python
RPC_URLS = [
    "https://api.helius.xyz/v0/access_token/...",
    "https://api.mainnet-beta.solana.com",
]
```

**Methods Used**:
- `getSignaturesForAddress` - Transaction history
- `getTransaction` - Transaction details
- `getBalance` - Account balance
- `getTokenSupply` - Token metrics

### 7.4 Price Data Sources

**Primary**: Solscan API
**Fallback**: Jupiter API
**Update Frequency**: Real-time (webhook-triggered) or periodic

**Data Tracked**:
- Current price
- 24h high/low
- Market cap
- Volume

---

## 8. Configuration

### 8.1 Environment Variables

**Required**:
```bash
# Database
DB_PATH=database/flex_complete_database.db
RPC_METRICS_DB=database/flex_complete_database.db

# Helius RPC
HELIUS_API_KEY=<api-key>
HELIUS_MONITORING_API_KEY=<api-key>
HELIUS_WEBHOOK_AUTH=Bearer <webhook-token>

# Solana RPC
SOLANA_RPC_ENDPOINT=https://api.helius.xyz/v0/access_token/...
RPC_URLS=https://api.helius.xyz/v0/access_token/...
```

**Optional**:
```bash
SOLSCAN_API_KEY=<key>
SNS_PRIMARY_ENDPOINT=<endpoint>
```

### 8.2 Key Constants

**Extraction Parameters**:
```python
MINIMUM_SOL = 0.001          # Filters 30-40% of transactions
MAX_PAGES = 100              # Signature history depth
MAX_CONCURRENT_RPC = 4       # Parallel RPC requests

# Cost Control
MAX_FRESH_FUNDERS_PER_CREATOR = 10
MAX_TX_SIGS_PER_FUNDER = 100
DEFAULT_CONCURRENCY = 2
RPC_COOLDOWN_SECONDS = 1800  # 30 minutes
```

**Clustering Thresholds**:
```python
MIN_CREATORS_FOR_RECIPIENT_HUB = 3      # Min creators for network
MIN_CREATORS_FOR_ATOMIC_FUNDER_NETWORK = 3
MIN_JACCARD = 0.2                       # Overlap threshold (20%)
MIN_OVERLAP_CREATORS = 2
```

**Risk Multipliers**:
```python
CEX_FUNDER_MULTIPLIER = 2.0             # CEX funders are 2x risk
EXCLUDE_CEX_FROM_CLUSTERING = False     # Include CEX in analysis
```

### 8.3 Flask Application Settings

**File**: `migration_settings.json`

```json
{
    "listen_to_launches": true,
    "listen_to_price_updates": true,
    "auto_extract_funding": true
}
```

**Runtime Configuration**:
- Port: 5002
- Debug mode: False
- Hot reload: Disabled for production

---

## 9. Error Handling & Resilience

### 9.1 RPC Failure Recovery

**Strategy**: Exponential backoff with retry limits

```python
def get_with_retry(func, max_retries=3, backoff=2):
    for attempt in range(max_retries):
        try:
            return func()
        except RpcException:
            if attempt == max_retries - 1:
                return None  # Give up after max retries

            wait_time = backoff ** attempt
            time.sleep(wait_time)  # Exponential backoff
```

**Outcomes**:
- Success: Data extracted and saved
- Failure: `fully_analyzed = 0`, available for retry
- Timeout: Deferred to next cycle

### 9.2 Incomplete Data Handling

**Signature Pagination**:
- Only scan first 100 signatures (cost control)
- If 100 signatures returned, more exist
- Log partial extraction for later retry
- Still captures major funders (appear early)

**Impact**: May miss small later-stage funders but captures primary funding network

### 9.3 Failed Transaction Filtering

**Multi-Instruction Handling**:
```python
for instr in tx['transaction']['message']['instructions']:
    # Check instruction status in metadata
    if instr_meta['error'] is None:
        # This instruction succeeded
        if instr['program'] == 'system':
            extract_transfer()
    else:
        # Skip failed instructions
        continue
```

### 9.4 Dust & Spam Filtering

**Multi-Layer Filtering**:
```
1. Minimum amount check (< 0.001 SOL → Skip)
2. Deduplication (signature already seen → Skip)
3. Valid address check (invalid format → Skip)
4. Whitelist check (known spam addresses → Skip)
```

**Result**: Filters 30-40% of micro-transactions without data loss

### 9.5 Concurrent Processing Conflicts

**Row-Level Locking**:
```sql
-- Prevent duplicate processing
UPDATE work_queue SET locked_until = now() + 300
WHERE address = ? AND locked_until <= now()
```

**Primary Key Enforcement**:
- Duplicate inserts throw error
- Caller handles gracefully
- Second attempt skipped

### 9.6 Monitoring & Alerting

**Metrics Tracked**:
- RPC call counts by method
- Success/failure rates
- Latency percentiles (p95, p99)
- Cost overruns
- Queue depths

**Alert Thresholds**:
- Daily RPC cost > $10
- Error rate > 5%
- Queue depth > 1,000 items
- Worker lag > 1 hour

---

## 10. Extending the System

### 10.1 Adding New Funding Sources

**Goal**: Monitor transfers from a new program (e.g., new launchpad)

**Steps**:

1. **Identify the program**
   ```python
   # Find program ID
   NEW_PROGRAM_ID = "prog4...xyz"
   ```

2. **Extend webhook handler**
   ```python
   # src/core/webhook_handler.py
   def extract_system_transfers(tx_metadata):
       for instr in tx_metadata['instructions']:
           if instr['program'] == 'system':
               # Existing: native SOL
               yield (source, dest, amount)
           elif instr['program'] == NEW_PROGRAM_ID:
               # New: custom transfers
               yield parse_custom_transfer(instr)
   ```

3. **Classify the new source**
   ```python
   def classify_transfer(sender, recipient):
       if is_from_program(sender, NEW_PROGRAM_ID):
           return ('NEWPROGRAM', 'Program Name')
   ```

4. **Update UI mapping**
   ```python
   # src/utils/infra_mapping.py
   INFRA_PROGRAMS['new_program'] = {
       'name': 'Program Name',
       'color': '#FF6D00'
   }
   ```

### 10.2 Adding New Risk Detection Rules

**Goal**: Flag creators with suspicious patterns (e.g., quick dumps)

**Steps**:

1. **Create detection function**
   ```python
   # src/analysis/risk_detection.py
   def detect_quick_selloff(creator_address, token_mint):
       outgoing = get_creator_transfers(creator_address)
       token_balance = get_token_balance(creator_address, token_mint)

       if token_balance == 0 and len(outgoing) > 0:
           return {'finding': 'quick_selloff', 'risk_level': 'CRITICAL'}
   ```

2. **Integrate with findings**
   ```python
   # src/core/main.py - api_creator_recent_checks()
   findings = detect_coordinated_funders(...)
   findings.extend(detect_quick_selloff(...))
   return jsonify({'findings': findings})
   ```

3. **Display in UI**
   ```html
   <!-- HTML_TEMPLATE in main.py -->
   <div class="finding critical">
       <strong>Quick Selloff</strong>
       Creator sold tokens immediately after launch
   </div>
   ```

### 10.3 Adding New Visualization Pages

**Goal**: Create new analysis dashboard (e.g., Funder Network Graph)

**Steps**:

1. **Create Flask route**
   ```python
   @app.route('/funder-graph/<funder_address>')
   def funder_graph_view(funder_address: str):
       funder = get_funder_data(funder_address)
       creators = get_creators_funded_by(funder_address)
       senders = get_senders_to_funder(funder_address)
       return render_template('funder_graph.html', {
           'funder': funder,
           'creators': creators,
           'senders': senders
       })
   ```

2. **Create HTML template**
   ```html
   <!-- templates/funder_graph.html -->
   <div id="graph-container"></div>
   <script src="/static/d3.min.js"></script>
   <script>
       // D3 visualization
       const nodes = [
           {id: senders, type: 'sender'},
           {id: funder, type: 'funder'},
           {id: creators, type: 'creator'}
       ];
       // Render force-directed graph...
   </script>
   ```

3. **Add navigation**
   ```python
   # Update HTML_TEMPLATE in main.py
   '<a href="/funder-graph/XXX">View Network Graph</a>'
   ```

### 10.4 Adding New APIs

**Goal**: Expose new data endpoint for dashboards

**Steps**:

1. **Create API function**
   ```python
   @app.route('/api/creator-funding-export/<creator_address>')
   def api_creator_funding_export(creator_address: str):
       rows = []
       funders = get_creator_funders(creator_address)
       for funder in funders:
           senders = get_senders_to_funder(funder['funder_address'])
           for sender in senders:
               rows.append({
                   'creator': creator_address,
                   'funder': funder['funder_address'],
                   'sender': sender['sender_address'],
                   'amount_sol': sender['amount_sol']
               })
       return Response(to_csv(rows), mimetype='text/csv')
   ```

2. **Add documentation**
   - Document parameters and response format
   - Add to API reference
   - Include example requests/responses

### 10.5 Adding New Background Tasks

**Goal**: Run periodic scan (e.g., every 6 hours)

**Steps**:

1. **Create async function**
   ```python
   async def run_periodic_scan(interval_seconds=21600):  # 6 hours
       while True:
           try:
               await scan_all_creators()
           except Exception as e:
               print(f"Error: {e}")

           await asyncio.sleep(interval_seconds)
   ```

2. **Register with listener**
   ```python
   # src/core/pumpfun_curve_listener.py
   asyncio.create_task(run_periodic_scan(interval_seconds=21600))
   ```

3. **Add metrics**
   ```python
   # Track in database
   INSERT INTO task_metrics (task_name, started_at, completed_at)
   VALUES ('periodic_scan', now(), now())
   ```

---

## 11. Key Design Decisions

### 11.1 Real-Time vs Polling Architecture

**Decision**: Hybrid approach with webhook-first design

**Rationale**:
- **Webhook handler** captures events instantly (0-1 second latency)
- **Background workers** handle heavy analysis (deferred processing)
- **Webhook returns 200 immediately** (non-blocking)
- **Analysis happens asynchronously** (prevents timeout)

**Benefits**:
- Immediate visibility of new transfers
- Reduced RPC costs (event-driven vs polling)
- Better scalability (handles traffic spikes)
- Flexible prioritization (high-priority items processed first)

### 11.2 Cost Control via Budgeting

**Decision**: Implement multi-tiered budget system

```
Global: MAX_RPC_CALLS_PER_HOUR = 100
Per-Creator: MAX_FRESH_FUNDERS_PER_CREATOR = 10
Per-Funder: MAX_TX_SIGS_PER_FUNDER = 100
Smart Defer: Pause processing when budget nearly exhausted
```

**Rationale**:
- RPC provider charges per call
- Need to bound costs while maintaining data quality
- Fresh extraction (new data) prioritized over re-scans
- Deferral allows resumption next cycle without loss

**Benefits**:
- Predictable monthly costs
- Continued data collection (prioritized)
- No service disruption on budget hit

### 11.3 Database Indexing Strategy

**Decision**: Selective indexing on hot columns only

**Primary Key Indexes** (automatic):
- creator_funders: (creator, funder)
- funder_incoming_transfers: (funder, sender, signature)
- token_analysis: (mint)

**Secondary Indexes**:
- creator_funders.funder_address (lookup all funders)
- funder_incoming_transfers.sender_address (lookup all senders)
- rpc_metrics.day_key (daily aggregation)

**Rationale**:
- Too many indexes slow writes
- Focus on read-heavy query patterns
- UK midnight reset requires day_key aggregation
- Creator/funder lookups are frequent

### 11.4 Clustering Algorithm Choice

**Decision**: Jaccard similarity with union-find algorithm

```python
Similarity = |Shared Funders| / |Union of Funders|
Threshold: 0.2 (20% minimum overlap)
```

**Rationale**:
- Simple to understand and implement
- Works well for partially overlapping sets
- Threshold (20%) avoids spurious clusters
- Union-find efficiently handles large graph clustering

**Trade-offs**:
- May miss weak relationships (< 20% overlap)
- Includes some false positives (20% is low threshold)
- Computationally efficient (O(n log n))

### 11.5 UK Midnight Reset Timezone Choice

**Decision**: Use Europe/London timezone for daily resets

**Rationale**:
- Marketing team operates in UK timezone
- Aligns reporting with business hours
- Consistent international reference point
- Easier debugging (matches team time)

**Implementation**:
- All timestamps use UTC internally
- Convert to UK time for day key calculation
- Auto-reset at 00:00 GMT/BST
- Dashboard displays UK time

### 11.6 Priority Queue for Work Items

**Decision**: Numeric priority score with age-based fairness

```python
Priority = (activity_volume * 10) + (creator_flagged * 20) + ...
ORDER BY priority DESC, next_run_at ASC
```

**Rationale**:
- High-volume addresses processed first
- Flagged creators get priority
- Older items don't starve (age breaks ties)
- Simple linear scoring (no complex ML)

**Benefits**:
- Captures important activity immediately
- Fair scheduling prevents starvation
- Transparent scoring (easy to debug)
- Adjustable thresholds

### 11.7 Creator Analysis Queue Non-Blocking Design

**Decision**: Return webhook 200 immediately, analyze asynchronously

**Rationale**:
- Webhook must respond within 5 seconds (provider timeout)
- Deep analysis takes 5-10+ seconds
- Queueing allows deferred processing
- User gets response confirmation immediately

**Implementation**:
```python
# Webhook returns quickly
@app.route('/helius-webhook', methods=['POST'])
def handle_helius_webhook():
    save_to_queue()
    return 200, 'OK'  # Return immediately

# Worker processes asynchronously
async def process_creator_analysis(creator):
    # 7 database queries
    findings = deep_analysis()
    cache_findings(findings)
```

**Benefits**:
- Never times out (no 5-second limit)
- Handles traffic spikes
- Results available via API when ready
- No blocking on slow operations

---

## Summary

**FLEX** is a production-grade system combining:

✅ **Real-time detection** (WebSocket + webhooks)
✅ **Multi-layer analysis** (3-tier funding extraction)
✅ **Advanced clustering** (coordinated funder networks)
✅ **Cost optimization** (smart RPC budgeting)
✅ **Rich dashboarding** (15+ pages, 60+ APIs)
✅ **Resilient architecture** (error handling, retries)
✅ **Extensible design** (modular components)

The system is architected for **scalability, efficiency, and accuracy** in detecting suspicious funding patterns across Pump.Fun tokens on Solana.

