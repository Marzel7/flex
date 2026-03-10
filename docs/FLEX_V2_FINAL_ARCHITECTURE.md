# FLEX V2 — Final Production Architecture

**Status**: Production-Ready Design Document
**Date**: March 10, 2026
**Version**: 1.0 (Final)

---

## SECTION 1 — Final FLEX V2 Architecture Overview

### 1.1 System Philosophy

FLEX V2 is designed around three core principles:

1. **Simplicity** - Only include infrastructure that directly solves a problem
2. **Efficiency** - Minimize RPC calls through cursors, caching, and intelligent scheduling
3. **Observability** - Track costs and performance at every layer

The system maintains real-time token detection via WebSocket while building comprehensive funding networks through intelligent background processing.

### 1.2 System Components

```
┌─────────────────────────────────────────────────────────────┐
│ FLEX V2 SYSTEM COMPONENTS                                   │
└─────────────────────────────────────────────────────────────┘

LAYER 1: Event Sources
├─ Solana WebSocket (real-time token creation)
└─ Helius Webhooks (transfer events)

LAYER 2: Event Ingestion (non-blocking)
├─ Dedupe handler (signature dedup via Redis)
├─ Transfer parser (extract SOL transfers)
└─ Return 200 immediately

LAYER 3: State Management (persistent cursors)
├─ address_scan_state (where we left off)
├─ work_items (job queue with SKIP LOCKED)
└─ address_activity (for due-time scheduling)

LAYER 4: Caching (RPC efficiency)
├─ Redis RPC cache (signatures, transactions, labels)
├─ Redis dedup cache (recent signatures)
└─ 40-60% cache hit target

LAYER 5: Worker System (async processing)
├─ Due-time scheduler (query only overdue items)
├─ Extraction workers (get signatures, parse transfers)
├─ Analysis workers (incremental clustering, risk scoring)
└─ SKIP LOCKED queue fetching (safe concurrent access)

LAYER 6: Data Storage (optimized PostgreSQL)
├─ Operational schema (high write, short lifetime)
│  ├─ address_scan_state (4 columns, indexed for scheduling)
│  ├─ work_items (job queue, SKIP LOCKED friendly)
│  ├─ address_transfers (denormalized transfer index)
│  └─ sol_transfers (webhook events, partitioned by date)
│
└─ Analytical schema (read-heavy, long lifetime)
   ├─ creator_funders (funding edges)
   ├─ funder_incoming_transfers (source edges)
   ├─ cluster_assignments (address → cluster mapping)
   └─ token_analysis (token metadata)

LAYER 7: Graph Analysis (database-native)
├─ Incremental clustering on each new edge
├─ Jaccard similarity matching
└─ Materialized view for fast queries

LAYER 8: API & Dashboard (Flask)
└─ Query analytical schema (read-only, no locks)
```

### 1.3 Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| **Single PostgreSQL (Phase 1)** | 1000 QPS easily handled, avoid replication complexity |
| **No Kafka** | PostgreSQL replication sufficient, zero operational overhead |
| **Redis single instance** | 16GB cache sufficient, LRU eviction policy, no cluster management |
| **SKIP LOCKED queuing** | Safe concurrent worker access, no deadlocks, native PostgreSQL |
| **Address cursors** | 60% RPC reduction, foundation for everything else |
| **Materialized views** | Instant cluster lookups, refresh only when needed |
| **Partitioned tables** | sol_transfers and rpc_metrics by date, easy cleanup |

---

## SECTION 2 — Final Infrastructure Stack

### 2.1 Compute and Storage

```
PRODUCTION INFRASTRUCTURE - FLEX V2

┌─────────────────────────────────────────────────────────────┐
│ PostgreSQL (Primary Database)                               │
├─────────────────────────────────────────────────────────────┤
│ Instance:    r6i.xlarge (4 CPU, 32 GB RAM)                 │
│ Storage:     500 GB SSD (gp3, grows ~5 GB/month)           │
│ Backup:      Daily snapshots, 30-day retention             │
│ Replication: Automatic backups (WAL archiving)             │
│ Cost:        ~$600/month                                    │
│ Capacity:    1000+ QPS, 50+ concurrent connections         │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ PostgreSQL (Read Replica) — Phase 2 Only                   │
├─────────────────────────────────────────────────────────────┤
│ Instance:    r6i.large (2 CPU, 16 GB RAM)                  │
│ Storage:     500 GB SSD (replica of primary)               │
│ Purpose:     Offload analytical reads (dashboards)         │
│ Replication: Automatic from primary                        │
│ Cost:        ~$300/month (activate when primary > 70% CPU) │
│ Capacity:    500+ QPS read-only                            │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ Redis (Caching & Dedup)                                    │
├─────────────────────────────────────────────────────────────┤
│ Instance:    cache.r6g.large (2 CPU, 16 GB)               │
│ Memory:      16 GB (supports ~100M cached items)           │
│ Eviction:    LRU (least recently used)                     │
│ TTL:         1h (signatures), 24h (transactions)           │
│ Cost:        ~$150/month                                   │
│ Hit Rate:    Target 45% (saves 45% of RPC)                │
│ Replication: Single instance (acceptable for cache)        │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ Application Server(s)                                       │
├─────────────────────────────────────────────────────────────┤
│ Instance:    t4g.large (2 CPU, 8 GB RAM)                   │
│ Quantity:    1 primary + 1 backup initially                │
│ Containers:  Docker with Python 3.10+                      │
│ Services:    Flask API, Webhook Handler, Workers           │
│ Cost:        ~$50/month each (~$100 total)                 │
└─────────────────────────────────────────────────────────────┘

TOTAL INFRASTRUCTURE COST: ~$1,250/month
├─ PostgreSQL primary: $600
├─ PostgreSQL replica (Phase 2): $300
├─ Redis: $150
├─ Application servers: $100
└─ Networking/misc: $100
```

### 2.2 Network Architecture

```
Internet (Solana Network)
    │
    ├─→ WebSocket (pumpfun events)
    │
    └─→ Helius Webhook Endpoint
            │
            ▼
┌─────────────────────────────────────┐
│ Application Server                  │
│ (Flask + WebSocket listener)         │
├─────────────────────────────────────┤
│ • Parse token/transfer events       │
│ • Dedupe signatures (Redis)         │
│ • Enqueue work items                │
│ • Return 200 immediately            │
└─────────────────────────────────────┘
    │        │        │
    │        │        └──→ PostgreSQL (writes)
    │        │
    │        └──→ Redis (cache + dedup)
    │
    └──→ Background Workers (3-5 instances)
         • Extraction workers
         • Analysis workers
         • Scheduler
```

---

## SECTION 3 — Final Database Schema

### 3.1 Operational Schema (High Write, Short Lifetime)

**Note**: These tables have high write volume and frequent updates. Optimize for concurrent access.

```sql
-- Core cursor state for incremental extraction
CREATE TABLE address_scan_state (
    address TEXT PRIMARY KEY,
    last_signature TEXT,           -- Last signature we processed
    last_scan_at TIMESTAMP,        -- When we last scanned
    next_scan_at TIMESTAMP,        -- When to scan next (for due-time scheduler)
    status TEXT DEFAULT 'active'   -- active, paused, failed
);

-- Index for due-time scheduler (most critical query)
CREATE INDEX idx_address_scan_state_due_time
ON address_scan_state(next_scan_at, status)
WHERE status = 'active';

-- Index for cursor lookups
CREATE INDEX idx_address_scan_state_address
ON address_scan_state(address);


-- Work queue for job distribution with SKIP LOCKED
CREATE TABLE work_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    address TEXT NOT NULL,
    work_type TEXT NOT NULL,           -- 'extract_creator', 'extract_funder', etc.
    priority REAL DEFAULT 0.0,         -- ROI-based priority
    status TEXT DEFAULT 'queued',      -- queued, processing, completed, failed
    locked_until TIMESTAMP,            -- For SKIP LOCKED locking
    retries_remaining INT DEFAULT 3,
    last_error TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    deadline TIMESTAMP                 -- SLA deadline
);

-- Index for worker fetch (SKIP LOCKED query)
CREATE INDEX idx_work_items_fetch
ON work_items(status, locked_until, priority DESC, created_at ASC)
WHERE status = 'queued';

-- Index for cleanup
CREATE INDEX idx_work_items_deadline
ON work_items(deadline)
WHERE status != 'completed';


-- Denormalized transfer index for fast lookups
CREATE TABLE address_transfers (
    id BIGSERIAL PRIMARY KEY,
    source_address TEXT NOT NULL,
    destination_address TEXT NOT NULL,
    amount_sol REAL NOT NULL,
    signature TEXT NOT NULL,
    block_time INT NOT NULL,
    first_seen_at TIMESTAMP DEFAULT NOW()
);

-- Indexes for transfer queries
CREATE INDEX idx_address_transfers_source
ON address_transfers(source_address, block_time DESC);

CREATE INDEX idx_address_transfers_destination
ON address_transfers(destination_address, block_time DESC);

CREATE INDEX idx_address_transfers_signature
ON address_transfers(signature UNIQUE);  -- Dedup by signature

-- Partitioning by date (optional, but recommended for 100M+ rows)
CREATE TABLE address_transfers_2026_03 PARTITION OF address_transfers
FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');


-- Webhook events (high volume, partitioned by date)
CREATE TABLE sol_transfers (
    id BIGSERIAL PRIMARY KEY,
    source_address TEXT NOT NULL,
    destination_address TEXT NOT NULL,
    amount_sol REAL NOT NULL,
    signature TEXT NOT NULL UNIQUE,
    block_time INT NOT NULL,
    mint TEXT,
    source_owner TEXT,
    instruction_type TEXT,
    ingested_at TIMESTAMP DEFAULT NOW()
);

-- Partition by month
CREATE TABLE sol_transfers_2026_03 PARTITION OF sol_transfers
FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');

-- Index for recent lookups
CREATE INDEX idx_sol_transfers_signature
ON sol_transfers(signature);

CREATE INDEX idx_sol_transfers_address
ON sol_transfers(destination_address, block_time DESC);


-- Real-time activity tracking for due-time scheduling heuristic
CREATE TABLE address_activity (
    address TEXT PRIMARY KEY,
    last_activity_at TIMESTAMP,        -- Last time we saw activity
    activity_count_24h INT DEFAULT 0,  -- Transfers in last 24h
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_address_activity_updated
ON address_activity(updated_at DESC);
```

### 3.2 Analytical Schema (Read-Heavy, Long Lifetime)

```sql
-- Creator funding edges (canonical)
CREATE TABLE creator_funders (
    creator_address TEXT NOT NULL,
    funder_address TEXT NOT NULL,
    amount_sol REAL NOT NULL,
    first_seen_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (creator_address, funder_address)
);

CREATE INDEX idx_creator_funders_creator
ON creator_funders(creator_address);

CREATE INDEX idx_creator_funders_funder
ON creator_funders(funder_address);


-- Funder incoming sources (canonical)
CREATE TABLE funder_incoming_transfers (
    funder_address TEXT NOT NULL,
    sender_address TEXT NOT NULL,
    amount_sol REAL NOT NULL,
    first_seen_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (funder_address, sender_address)
);

CREATE INDEX idx_funder_incoming_funder
ON funder_incoming_transfers(funder_address);

CREATE INDEX idx_funder_incoming_sender
ON funder_incoming_transfers(sender_address);


-- Simplified cluster assignments (denormalized for speed)
CREATE TABLE cluster_assignments (
    address TEXT PRIMARY KEY,
    cluster_id UUID NOT NULL,
    cluster_generation INT DEFAULT 1,
    computed_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_cluster_assignments_cluster
ON cluster_assignments(cluster_id);

CREATE INDEX idx_cluster_assignments_generation
ON cluster_assignments(cluster_generation DESC);


-- Materialized view for fast cluster lookups (refresh when updated)
CREATE MATERIALIZED VIEW cluster_summary AS
SELECT
    cluster_id,
    COUNT(DISTINCT address) as member_count,
    ARRAY_AGG(DISTINCT address ORDER BY address) as members,
    MAX(computed_at) as last_updated
FROM cluster_assignments
GROUP BY cluster_id;

CREATE INDEX idx_cluster_summary_id ON cluster_summary(cluster_id);


-- Token metadata
CREATE TABLE token_analysis (
    mint TEXT PRIMARY KEY,
    creator_address TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    earliest_tx_creator TEXT,
    funders_count INT DEFAULT 0,
    super_cluster_id UUID,
    analyzed_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_token_analysis_creator
ON token_analysis(creator_address);

CREATE INDEX idx_token_analysis_created
ON token_analysis(created_at DESC);
```

### 3.3 Monitoring Schema (Cost Tracking)

```sql
-- RPC metrics for cost visibility (partitioned by date)
CREATE TABLE rpc_metrics (
    id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMP NOT NULL,
    method TEXT NOT NULL,              -- 'getSignatures', 'getTransaction', etc.
    request_address TEXT,
    cache_hit BOOLEAN,
    credits_used INT DEFAULT 0,
    error_occurred BOOLEAN DEFAULT FALSE,
    ingested_at TIMESTAMP DEFAULT NOW()
);

-- Partition by date
CREATE TABLE rpc_metrics_2026_03 PARTITION OF rpc_metrics
FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');

-- Index for analysis
CREATE INDEX idx_rpc_metrics_method
ON rpc_metrics(method, timestamp DESC);

CREATE INDEX idx_rpc_metrics_cache
ON rpc_metrics(cache_hit, timestamp DESC);


-- Worker health tracking
CREATE TABLE worker_health (
    worker_id TEXT PRIMARY KEY,
    last_heartbeat TIMESTAMP,
    items_processed INT DEFAULT 0,
    items_failed INT DEFAULT 0,
    avg_latency_ms REAL DEFAULT 0,
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_worker_health_heartbeat
ON worker_health(last_heartbeat DESC);
```

### 3.4 Index Strategy

**Hot indexes** (created immediately):
```sql
-- Due-time scheduler (runs every 60s)
CREATE INDEX idx_address_scan_state_due_time
ON address_scan_state(next_scan_at, status);

-- Worker queue fetch (SKIP LOCKED)
CREATE INDEX idx_work_items_fetch
ON work_items(status, locked_until, priority DESC, created_at ASC);

-- Transfer lookups
CREATE INDEX idx_address_transfers_source
ON address_transfers(source_address, block_time DESC);

CREATE INDEX idx_address_transfers_destination
ON address_transfers(destination_address, block_time DESC);

-- Analytical queries
CREATE INDEX idx_creator_funders_creator
ON creator_funders(creator_address);

CREATE INDEX idx_cluster_assignments_cluster
ON cluster_assignments(cluster_id);
```

**Cold indexes** (created after data loads):
```sql
-- These can be added later as needed
CREATE INDEX idx_creator_funders_funder
ON creator_funders(funder_address);

CREATE INDEX idx_address_transfers_amount
ON address_transfers(amount_sol DESC);
```

---

## SECTION 4 — Worker System Design

### 4.1 Worker Architecture Overview

```
Due-Time Scheduler (1 instance)
    │
    ├─→ Query: SELECT * FROM address_scan_state
    │          WHERE next_scan_at <= NOW()
    │          ORDER BY priority DESC
    │          LIMIT 100
    │
    └─→ Insert into work_items table
            │
            ▼
    Worker Pool (3-5 instances)
    ├─ Fetch with SKIP LOCKED
    ├─ Process work item
    ├─ Update state
    └─ Mark as completed
```

### 4.2 Worker Fetch with SKIP LOCKED

The work queue uses PostgreSQL's SKIP LOCKED feature for safe concurrent access without deadlocks.

```python
# Safe concurrent worker fetch - no deadlocks
async def fetch_work_batch(worker_id: str, batch_size: int = 5) -> List[WorkItem]:
    """
    Fetch next work items using SKIP LOCKED.

    SKIP LOCKED skips rows that are locked by other transactions,
    eliminating deadlocks and contention.
    """
    query = """
    SELECT id, address, work_type, priority, retries_remaining
    FROM work_items
    WHERE status = 'queued'
    AND locked_until <= NOW()
    ORDER BY priority DESC, created_at ASC
    LIMIT $1
    FOR UPDATE SKIP LOCKED
    """

    items = await db.fetch(query, batch_size)

    if items:
        # Lock items atomically
        item_ids = [item['id'] for item in items]
        await db.execute("""
            UPDATE work_items
            SET status = 'processing',
                locked_until = NOW() + INTERVAL '5 minutes'
            WHERE id = ANY($1)
        """, item_ids)

    return items
```

**Why SKIP LOCKED**:
- ✅ Zero deadlocks (skips locked rows, doesn't wait)
- ✅ Safe with multiple workers
- ✅ Native PostgreSQL (no Redis needed)
- ✅ Simple to implement

### 4.3 Due-Time Scheduler

The scheduler queries only addresses that are "due" (next_scan_at <= NOW()), eliminating polling of dormant addresses.

```python
async def run_due_time_scheduler():
    """
    Run every 60 seconds.
    Find addresses due for scanning and enqueue them.
    """
    while True:
        # Calculate ROI-based priority
        due_items = await db.fetch("""
        SELECT
            ass.address,
            ass.last_signature,
            COALESCE(aa.activity_count_24h, 0) as activity,
            COALESCE(ca.funders_count, 0) as funders
        FROM address_scan_state ass
        LEFT JOIN address_activity aa ON ass.address = aa.address
        LEFT JOIN (
            SELECT creator_address, COUNT(*) as funders_count
            FROM creator_funders
            GROUP BY creator_address
        ) ca ON ass.address = ca.creator_address
        WHERE ass.next_scan_at <= NOW()
        AND ass.status = 'active'
        ORDER BY (activity * 10 + funders * 2) DESC
        LIMIT 100
        """)

        # Enqueue as work items
        for item in due_items:
            priority = (item['activity'] * 10.0) + (item['funders'] * 2.0)

            await db.execute("""
            INSERT INTO work_items
                (address, work_type, priority, status, locked_until)
            VALUES ($1, 'extract_creator', $2, 'queued', NOW() - INTERVAL '1s')
            ON CONFLICT DO NOTHING
            """, item['address'], priority)

        # Sleep until next scheduled check
        await asyncio.sleep(60)
```

### 4.4 Worker Process Loop

```python
async def worker_process_loop(worker_id: str):
    """
    Main worker loop: fetch → process → update.
    """
    while True:
        try:
            # Fetch batch with SKIP LOCKED (safe, concurrent)
            items = await fetch_work_batch(worker_id, batch_size=5)

            if not items:
                # No work available, sleep briefly
                await asyncio.sleep(5)
                continue

            # Process each item
            for work_item in items:
                try:
                    await process_work_item(work_item)

                    # Mark completed
                    await db.execute("""
                    UPDATE work_items
                    SET status = 'completed', locked_until = NOW()
                    WHERE id = $1
                    """, work_item['id'])

                except Exception as e:
                    # Retry with exponential backoff
                    await handle_work_failure(work_item['id'], e)

        except Exception as e:
            logger.error(f"Worker {worker_id} error: {e}")
            await asyncio.sleep(10)
```

### 4.5 Exponential Backoff

```python
def calculate_backoff(retries_remaining: int, max_retries: int = 3) -> timedelta:
    """
    Exponential backoff: 2^(max_retries - retries_remaining) minutes

    Example with max_retries=3:
    - Retry 0: 2^3 = 8 minutes
    - Retry 1: 2^2 = 4 minutes
    - Retry 2: 2^1 = 2 minutes
    - Retry 3: 2^0 = 1 minute
    """
    attempts = max_retries - retries_remaining
    minutes = min(2 ** attempts, 120)  # Cap at 2 hours
    return timedelta(minutes=minutes)

async def handle_work_failure(work_item_id: UUID, error: Exception):
    """
    Handle work item failure with retry logic.
    """
    retries = await db.fetchval(
        "SELECT retries_remaining FROM work_items WHERE id = $1",
        work_item_id
    )

    if retries > 0:
        # Retry with backoff
        backoff = calculate_backoff(retries)
        await db.execute("""
        UPDATE work_items
        SET status = 'queued',
            retries_remaining = retries_remaining - 1,
            locked_until = NOW() + $1,
            last_error = $2
        WHERE id = $3
        """, backoff, str(error), work_item_id)
    else:
        # Max retries exceeded
        await db.execute("""
        UPDATE work_items
        SET status = 'failed',
            locked_until = NOW(),
            last_error = $1
        WHERE id = $2
        """, str(error), work_item_id)
```

---

## SECTION 5 — Transfer Indexing Strategy

### 5.1 Problem: Current System Lacks Fast Transfer Lookups

Current FLEX scans creator/funder signatures from RPC repeatedly. With cursors, we reduce rescans, but we still lack a fast way to query:
- "What transfers sent to address X?"
- "What transfers received by address X?"
- "How much SOL flowed between X and Y?"

### 5.2 Solution: Denormalized address_transfers Table

Instead of scanning signatures every time, maintain a denormalized index of all transfers.

```sql
-- Single source of truth for transfer data
CREATE TABLE address_transfers (
    id BIGSERIAL PRIMARY KEY,
    source_address TEXT NOT NULL,
    destination_address TEXT NOT NULL,
    amount_sol REAL NOT NULL,
    signature TEXT NOT NULL UNIQUE,
    block_time INT NOT NULL,
    first_seen_at TIMESTAMP DEFAULT NOW()
);

-- Index for "what did X send?"
CREATE INDEX idx_address_transfers_source
ON address_transfers(source_address, block_time DESC);

-- Index for "who sent to X?"
CREATE INDEX idx_address_transfers_destination
ON address_transfers(destination_address, block_time DESC);
```

### 5.3 Population Strategy

The `address_transfers` table is populated from two sources:

**Source 1: Webhook Events** (sol_transfers)
```python
# When webhook handler receives a transfer
async def handle_transfer_webhook(tx_data):
    """Ingest webhook transfer into both sol_transfers and address_transfers."""

    for transfer in extract_sol_transfers(tx_data):
        # Insert into both tables (one transaction)
        await db.execute("""
        BEGIN;

        INSERT INTO sol_transfers
            (source, destination, amount_sol, signature, block_time, ...)
        VALUES ($1, $2, $3, $4, $5, ...)
        ON CONFLICT (signature) DO NOTHING;

        INSERT INTO address_transfers
            (source_address, destination_address, amount_sol, signature, block_time)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (signature) DO NOTHING;

        COMMIT;
        """, ...)
```

**Source 2: Signature Extraction** (from RPC scanning)
```python
# When worker extracts signatures for an address
async def extract_transfers_from_signature(address: str, signature: str, tx_data: dict):
    """Parse signature and save transfers."""

    for transfer in parse_transfers_from_tx(tx_data):
        await db.execute("""
        INSERT INTO address_transfers
            (source_address, destination_address, amount_sol, signature, block_time)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (signature) DO NOTHING
        """,
        transfer['source'], transfer['destination'],
        transfer['amount'], signature, transfer['block_time'])
```

### 5.4 Query Examples

```python
# Fast: "Who funded creator X?"
async def get_creators_funders(creator_address: str) -> List[Transfer]:
    return await db.fetch("""
    SELECT source_address, amount_sol, block_time
    FROM address_transfers
    WHERE destination_address = $1
    ORDER BY block_time DESC
    """, creator_address)

# Fast: "What did funder X send out?"
async def get_funder_outgoing(funder_address: str) -> List[Transfer]:
    return await db.fetch("""
    SELECT destination_address, amount_sol, block_time
    FROM address_transfers
    WHERE source_address = $1
    ORDER BY block_time DESC
    LIMIT 1000
    """, funder_address)

# Fast: "How much flowed from X to Y?"
async def get_flow_between(sender: str, receiver: str) -> float:
    total = await db.fetchval("""
    SELECT COALESCE(SUM(amount_sol), 0)
    FROM address_transfers
    WHERE source_address = $1 AND destination_address = $2
    """, sender, receiver)
    return total
```

### 5.5 Partitioning Strategy (Phase 2)

Once `address_transfers` grows beyond 100M rows, partition by date:

```sql
-- Partition parent table
CREATE TABLE address_transfers (
    id BIGSERIAL,
    source_address TEXT NOT NULL,
    destination_address TEXT NOT NULL,
    amount_sol REAL NOT NULL,
    signature TEXT NOT NULL,
    block_time INT NOT NULL,
    first_seen_at TIMESTAMP NOT NULL
) PARTITION BY RANGE (EXTRACT(EPOCH FROM first_seen_at)::bigint / 2592000);

-- Partition for March 2026
CREATE TABLE address_transfers_2026_03
PARTITION OF address_transfers
FOR VALUES FROM (1772083200) TO (1774761600);

-- Queries automatically route to correct partition
SELECT * FROM address_transfers
WHERE destination_address = $1
AND first_seen_at > NOW() - INTERVAL '30 days';
```

---

## SECTION 6 — Graph Clustering Model

### 6.1 Simplified Cluster Schema

Instead of the original 3-table model, use 2 tables + 1 materialized view:

```sql
-- Table 1: Cluster assignments (denormalized for speed)
CREATE TABLE cluster_assignments (
    address TEXT PRIMARY KEY,
    cluster_id UUID NOT NULL,
    cluster_generation INT DEFAULT 1,
    computed_at TIMESTAMP DEFAULT NOW()
);

-- Table 2: Cluster metadata (for sorting/filtering)
CREATE TABLE cluster_metadata (
    cluster_id UUID PRIMARY KEY,
    member_count INT,
    density REAL,
    last_updated TIMESTAMP
);

-- View: Pre-computed cluster summaries (materialized, refresh on demand)
CREATE MATERIALIZED VIEW cluster_summary AS
SELECT
    cluster_id,
    COUNT(DISTINCT address) as member_count,
    ARRAY_AGG(DISTINCT address ORDER BY address) as members,
    MAX(computed_at) as last_updated
FROM cluster_assignments
GROUP BY cluster_id;
```

### 6.2 Incremental Clustering Algorithm

```python
async def on_new_creator_funder_edge(creator: str, funder: str):
    """
    When a new creator-funder edge is discovered, update clusters.

    Goal: Find all creators funded by the same funder,
    then merge their clusters if they have high Jaccard similarity.
    """

    # Step 1: Find other creators funded by this funder
    other_creators = await db.fetch("""
    SELECT DISTINCT creator_address
    FROM creator_funders
    WHERE funder_address = $1
    """, funder)

    # Step 2: For each other creator, check cluster similarity
    clusters_to_merge = set()

    for other_creator in other_creators:
        # Get creator's funders
        creator_funders_set = await get_creator_funders_set(creator)
        other_funders_set = await get_creator_funders_set(other_creator['creator_address'])

        # Calculate Jaccard similarity
        intersection = len(creator_funders_set & other_funders_set)
        union = len(creator_funders_set | other_funders_set)
        jaccard = intersection / union if union > 0 else 0

        # If similar enough, they should be in same cluster
        if jaccard > 0.3:  # Threshold
            clusters_to_merge.add(other_creator['creator_address'])

    # Step 3: Merge clusters
    if clusters_to_merge:
        # Get cluster IDs
        cluster_ids = await db.fetch("""
        SELECT DISTINCT cluster_id
        FROM cluster_assignments
        WHERE address = ANY($1)
        """, list(clusters_to_merge) + [creator])

        # Pick minimum cluster_id (to merge into)
        target_cluster = min([row['cluster_id'] for row in cluster_ids])

        # Update all addresses to target cluster
        all_addresses = [creator] + [c['creator_address'] for c in clusters_to_merge]
        await db.execute("""
        UPDATE cluster_assignments
        SET cluster_id = $1,
            cluster_generation = cluster_generation + 1
        WHERE address = ANY($2)
        """, target_cluster, all_addresses)

        # Refresh materialized view
        await db.execute("REFRESH MATERIALIZED VIEW cluster_summary")
```

### 6.3 Cluster Query Interface

```python
# Get cluster members
async def get_cluster_members(cluster_id: UUID) -> List[str]:
    row = await db.fetchrow("""
    SELECT members FROM cluster_summary WHERE cluster_id = $1
    """, cluster_id)
    return row['members'] if row else []

# Get address's cluster
async def get_address_cluster(address: str) -> UUID:
    cluster_id = await db.fetchval("""
    SELECT cluster_id FROM cluster_assignments WHERE address = $1
    """, address)
    return cluster_id

# Get all clusters with size
async def list_clusters(min_size: int = 2) -> List[Dict]:
    return await db.fetch("""
    SELECT cluster_id, member_count
    FROM cluster_summary
    WHERE member_count >= $1
    ORDER BY member_count DESC
    """, min_size)
```

---

## SECTION 7 — RPC Optimization Strategy

### 7.1 Caching Architecture

```
RPC Call
    │
    ├─ Check Redis cache (10ms)
    │  ├─ HIT (40-60%) → Return cached result
    │  │
    │  └─ MISS (40-60%) → Continue
    │
    ├─ Check address_scan_state cursor
    │  ├─ Have cursor? → Fetch only NEW signatures (60% RPC reduction)
    │  │
    │  └─ No cursor? → First-time scan
    │
    ├─ Call RPC API (Helius)
    │  ├─ getSignatures (with before=last_signature)
    │  ├─ getTransaction (for each signature)
    │  └─ getTransactionWithOptions (with encoding)
    │
    └─ Cache result in Redis
       ├─ Signatures: 1-hour TTL
       ├─ Transactions: 24-hour TTL
       └─ Labels: 24-hour TTL
```

### 7.2 Redis Caching Implementation

```python
class CachedRPCClient:
    """Wraps RPC client with Redis caching layer."""

    def __init__(self, rpc_client, redis_client):
        self.rpc = rpc_client
        self.redis = redis_client

    async def get_signatures(self, address: str, before: str = None, limit: int = 100):
        """
        Get signatures with caching.
        Hit rate target: 50% (same address checked multiple times)
        """
        key = f"rpc:sigs:{address}:{before}:{limit}"

        # Check cache first
        cached = await self.redis.get(key)
        if cached:
            logger.debug(f"Cache HIT: {key}")
            return json.loads(cached)

        logger.debug(f"Cache MISS: {key}")

        # Fetch from RPC
        result = await self.rpc.get_signatures(
            address=address,
            before=before,
            limit=limit
        )

        # Cache for 1 hour (signatures are immutable once confirmed)
        await self.redis.setex(
            key,
            3600,  # 1 hour TTL
            json.dumps(result)
        )

        return result

    async def get_transaction(self, signature: str, encoding: str = "jsonParsed"):
        """
        Get transaction with caching.
        Hit rate target: 70% (transactions are heavily reused)
        """
        key = f"rpc:tx:{signature}:{encoding}"

        cached = await self.redis.get(key)
        if cached:
            return json.loads(cached)

        result = await self.rpc.get_transaction(
            signature=signature,
            encoding=encoding
        )

        # Cache for 24 hours (transactions never change)
        await self.redis.setex(
            key,
            86400,  # 24 hours
            json.dumps(result)
        )

        return result

    async def get_address_info(self, address: str):
        """Get address metadata with caching."""
        key = f"rpc:addr:{address}"

        cached = await self.redis.get(key)
        if cached:
            return json.loads(cached)

        result = await self.rpc.get_account_info(address)

        # Cache for 24 hours
        await self.redis.setex(key, 86400, json.dumps(result))

        return result
```

### 7.3 RPC Cost Tracking

```python
class RPCMetricsRecorder:
    """Track RPC calls and costs for observability."""

    async def record_call(
        self,
        method: str,
        address: str = None,
        cache_hit: bool = False,
        credits_used: int = 1
    ):
        """Log RPC call to metrics table."""
        await db.execute("""
        INSERT INTO rpc_metrics
            (timestamp, method, request_address, cache_hit, credits_used)
        VALUES (NOW(), $1, $2, $3, $4)
        """, method, address, cache_hit, credits_used)

    async def get_daily_cost(self, date: date) -> Dict:
        """Get daily RPC cost breakdown."""
        return await db.fetch("""
        SELECT
            method,
            COUNT(*) as call_count,
            SUM(CASE WHEN cache_hit THEN 1 ELSE 0 END) as cache_hits,
            SUM(CASE WHEN NOT cache_hit THEN 1 ELSE 0 END) as cache_misses,
            SUM(credits_used) as total_credits,
            SUM(credits_used) * 0.0001 as estimated_cost_usd
        FROM rpc_metrics
        WHERE DATE(timestamp) = $1
        GROUP BY method
        ORDER BY total_credits DESC
        """, date)
```

### 7.4 RPC Reduction Targets

| Strategy | Reduction | Implementation Time |
|----------|-----------|-------------------|
| Address cursors | 60% | Week 1-2 |
| Response caching | 35% additional | Week 3-4 |
| Signature dedup | 5-10% additional | Week 4-5 |
| Due-time scheduling | DB load (not RPC) | Week 5-6 |
| **Total** | **70-80%** | **6 weeks** |

---

## SECTION 8 — Operational Monitoring and Cost Control

### 8.1 Key Metrics Dashboard

```
FLEX V2 OPERATIONAL DASHBOARD

┌─────────────────────────────────────────────────────┐
│ RPC EFFICIENCY                                      │
├─────────────────────────────────────────────────────┤
│ Daily Cost:          ~$50 (baseline → $10-15 target)
│ Cache Hit Rate:      45% (target: 40-60%)
│ Signatures Cached:   2.5M in Redis
│ Transactions Cached: 500K in Redis
│ Cache Evictions:     150/day (healthy, LRU working)
│
│ Method Breakdown:
│  • getSignatures:    40% of calls (cached at 50%)
│  • getTransaction:   35% of calls (cached at 70%)
│  • getAccountInfo:   20% of calls (cached at 80%)
│  • Other:            5% of calls
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│ WORKER HEALTH                                       │
├─────────────────────────────────────────────────────┤
│ Active Workers:      4 extraction + 2 analysis
│ Avg Latency:         250ms per item
│ Throughput:          500 items/hour
│ Error Rate:          0.1% (1 in 1000)
│ Queue Depth:         150 items (healthy)
│ Processing Time:     2 hours to clear queue
│
│ Worker Distribution:
│  • Worker-1: 125 items/hour, 0% error
│  • Worker-2: 130 items/hour, 0.2% error
│  • Worker-3: 115 items/hour, 0% error
│  • Worker-4: 130 items/hour, 0% error
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│ DATABASE PERFORMANCE                                │
├─────────────────────────────────────────────────────┤
│ Primary CPU:         35% (healthy)
│ Memory:              18 GB / 32 GB (56%)
│ Disk I/O:            8000 IOPS / 16000 max
│ Query Latency P95:   45ms
│ Index Hit Ratio:     98% (excellent)
│
│ Largest Tables:
│  • address_transfers: 45M rows, 25 GB
│  • sol_transfers:     12M rows, 8 GB
│  • creator_funders:   2M rows, 1.5 GB
│  • cluster_summary:   500K rows (materialized view)
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│ PROCESSING METRICS                                  │
├─────────────────────────────────────────────────────┤
│ Tokens Analyzed:     45K total
│ New Tokens/Day:      1200 (on pump.fun)
│ Creator Funders:     2M relationships
│ Funder Sources:      8M relationships
│ Active Addresses:    200K unique addresses
│ Clusters:            15K coordinated networks
│
│ Processing Delay:
│  • WebSocket → DB:   100ms (real-time)
│  • Extraction start:  ~5 minutes (due-time queue)
│  • Full analysis:     30 minutes average
└─────────────────────────────────────────────────────┘
```

### 8.2 Alerting Rules

```python
# Alert if cache hit rate drops
alert_cache_hit_low = """
IF cache_hit_rate < 0.35 THEN alert "Cache performance degraded"
"""

# Alert if worker error rate spikes
alert_worker_errors_high = """
IF worker_error_rate > 0.01 THEN alert "Worker failure rate high"
"""

# Alert if queue depth is growing
alert_queue_depth_growing = """
IF queue_depth > 1000 AND queue_depth_trend = "increasing"
THEN alert "Work queue backlog growing"
"""

# Alert if RPC cost is high
alert_rpc_cost_high = """
IF daily_rpc_cost > $100 THEN alert "RPC costs elevated"
"""

# Alert if database CPU is high
alert_db_cpu_high = """
IF db_cpu > 0.80 THEN alert "Database CPU high - consider read replica"
"""
```

### 8.3 Cost Tracking

```python
class CostTracker:
    """Track system costs by component."""

    async def get_cost_breakdown(self, date: date) -> Dict:
        """Get costs by category."""
        return {
            'rpc': await self._rpc_cost(date),
            'database': await self._db_cost(date),
            'cache': await self._cache_cost(date),
            'compute': await self._compute_cost(date),
        }

    async def _rpc_cost(self, date: date) -> float:
        """Calculate RPC costs from metrics."""
        total_credits = await db.fetchval("""
        SELECT COALESCE(SUM(credits_used), 0)
        FROM rpc_metrics
        WHERE DATE(timestamp) = $1
        """, date)
        # Helius: $0.0001 per credit
        return total_credits * 0.0001

    async def _db_cost(self, date: date) -> float:
        """Estimate database costs."""
        # ~$600/month ÷ 30 days = $20/day
        return 20.0

    async def _cache_cost(self, date: date) -> float:
        """Estimate cache costs."""
        # ~$150/month ÷ 30 days = $5/day
        return 5.0

    async def _compute_cost(self, date: date) -> float:
        """Estimate compute costs."""
        # ~$100/month application ÷ 30 days = $3.33/day
        return 3.33
```

---

## SECTION 9 — Final Implementation Roadmap

### 9.1 Phase Overview

```
FLEX V2 DEPLOYMENT — 12-WEEK ZERO-DOWNTIME ROLLOUT

Phase 1: Cursors & Caching (Week 1-4)
├─ Week 1-2: Deploy address_scan_state, populate cursors
├─ Week 3-4: Deploy Redis caching layer
└─ Result: 60% RPC reduction + 35% additional (total 70%)

Phase 2: Due-Time Scheduling (Week 5-6)
├─ Deploy due-time scheduler alongside polling
├─ Run in parallel, validate results match
└─ Result: 40% DB load reduction

Phase 3: Work Queue & Dedup (Week 7-8)
├─ Deploy work_items table and SKIP LOCKED workers
├─ Signature deduplication system
└─ Result: Better observability + 5% RPC

Phase 4: Cluster Optimization (Week 9-10)
├─ New cluster_assignments schema
├─ Materialized view refresh
└─ Result: 10× faster cluster queries

Phase 5: Transfer Indexing (Week 11)
├─ Deploy address_transfers table
├─ Populate from webhooks and extraction
└─ Result: Instant transfer lookups

Phase 6: Cleanup & Monitoring (Week 12)
├─ Remove legacy extraction code
├─ Deploy comprehensive monitoring
├─ Handle edge cases from production
└─ Result: Clean, observable system
```

### 9.2 Week-by-Week Execution

#### Week 1-2: Address Cursors

**Deliverables**:
- Create `address_scan_state` table
- Modify extraction to use cursors
- Run old code in parallel to validate

**Code Changes**:
- `realtime_creator_funding_extractor.py` - Add cursor loading/saving
- `funder_incoming_extractor.py` - Add cursor loading/saving
- New: `cursor_manager.py` - Cursor state management

**Validation**:
- Extract 100 test addresses with and without cursors
- Compare results (should be identical)
- Measure RPC calls (should be 60% lower with cursors)

---

#### Week 3-4: RPC Caching

**Deliverables**:
- Deploy Redis instance
- Implement `CachedRPCClient`
- Route all RPC calls through cache

**Code Changes**:
- New: `cached_rpc_client.py` - Caching wrapper
- `main.py` - Initialize Redis connection
- All extraction modules - Use cached client

**Validation**:
- Monitor cache hit rate (target: 40-60%)
- Measure RPC reduction (target: 35%)
- Check for stale data issues (shouldn't occur)

---

#### Week 5-6: Due-Time Scheduling

**Deliverables**:
- Implement due-time scheduler
- Deploy work_items table
- Switch workers from polling to work queue

**Code Changes**:
- New: `due_time_scheduler.py`
- Modify: `worker_process_loop.py` - Use work queue
- New: `work_queue_manager.py`

**Validation**:
- Run scheduler and polling in parallel for 1 week
- Compare addresses processed (should match)
- Measure DB load (target: 40% reduction)

---

#### Week 7-8: Work Queue & Dedup

**Deliverables**:
- Finalize work_items with retries
- Deploy signature deduplication
- Cost tracking infrastructure

**Code Changes**:
- Enhance: `work_queue_manager.py` - Retry logic
- New: `signature_dedup.py`
- New: `rpc_metrics_recorder.py`

**Validation**:
- Monitor retry rates
- Check dedup effectiveness
- Verify cost tracking accuracy

---

#### Week 9-10: Cluster Optimization

**Deliverables**:
- Migrate to new cluster schema
- Deploy materialized view
- Incremental clustering algorithm

**Code Changes**:
- New: `cluster_manager.py` - Simplified clustering
- Modify: `cross_funding_network_analyzer.py`

**Validation**:
- Compare old vs new cluster assignments
- Measure query speed improvement
- Check cluster stability

---

#### Week 11: Transfer Indexing

**Deliverables**:
- Deploy address_transfers table
- Populate from webhook + extraction data
- Switch queries to use index

**Code Changes**:
- New: `transfer_indexer.py`
- Modify: Webhook handler, extraction modules

**Validation**:
- Verify all transfers recorded
- Measure query performance (should be <10ms)
- Check for duplicates

---

#### Week 12: Cleanup & Monitoring

**Deliverables**:
- Remove legacy code
- Deploy comprehensive monitoring
- Finalize documentation

**Code Changes**:
- Delete: Old extraction code
- New: `monitoring_dashboard.py`

**Validation**:
- Smoke test all endpoints
- Verify all metrics working
- Check for any production issues

---

### 9.3 Risk Mitigation

| Risk | Mitigation |
|------|-----------|
| **Cursor bugs break extraction** | Run old code in parallel for 2 weeks, validate identical results |
| **Cache staleness issues** | Monitor for stale data complaints, implement cache invalidation |
| **Work queue deadlocks** | Use SKIP LOCKED, test with multiple workers before production |
| **Cluster migration data loss** | Full backup before migration, validate all clusters match |
| **Transfer index inconsistency** | Populate from two sources, implement daily reconciliation check |
| **RPC billing spike** | Monitor daily costs, alert if exceeds $100 |

### 9.4 Rollback Plan

If critical issues arise:

**Week 1-4 (Cursors/Caching)**:
- Disable cache, disable cursors → back to baseline (5 minutes)
- No data loss, no state changes

**Week 5-6 (Scheduling)**:
- Switch workers back to polling loop (1 minute)
- Work queue data can be safely abandoned

**Week 7+ (Indexing/Clustering)**:
- Keep old tables alongside new ones
- Switch queries back to old tables (1 minute)
- Run reconciliation on new tables to debug

---

## Summary

This architecture achieves the 10× scaling goal while maintaining simplicity:

✅ **Infrastructure**: 4 services ($1,250/month)
✅ **Database**: Single PostgreSQL, no Kafka
✅ **RPC**: 70-80% cost reduction
✅ **Workers**: SKIP LOCKED safe concurrency
✅ **Caching**: Redis for critical paths
✅ **Clustering**: Simplified 2-table model
✅ **Monitoring**: Comprehensive cost/performance tracking
✅ **Migration**: 12-week zero-downtime rollout

The system is designed to handle **100K+ creators**, **50M+ transfers**, and **1000+ requests/second** while maintaining sub-100ms query latency and <$50/day RPC costs.

---

**Next Step**: Begin Phase 1 implementation (address cursors) in week 1.
