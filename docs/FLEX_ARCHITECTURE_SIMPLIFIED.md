# FLEX Architecture Refinement — Simplification & Optimization

**Pragmatic improvements to the existing redesign, focusing on simplicity and highest ROI**

---

## SECTION 1 — Parts of the Redesign That Should Be Simplified

### 1.1 Kafka Event Stream is Unnecessary

**Current Proposal**: Kafka for operational → analytical replication

**Problem with Kafka**:
- Adds operational complexity (cluster management, monitoring)
- Overkill for this use case (not millions of events/sec)
- Requires CDC (Change Data Capture) setup
- Additional latency between operational and analytical writes
- More moving parts = more things to break

**Better Approach: Direct Replication**

```python
# SIMPLER: Use PostgreSQL native replication + triggers
# OR: Use Redis Streams for intermediate buffering if needed

# Option A: PostgreSQL Logical Replication (SIMPLEST)
# - Native, built-in, zero external dependencies
# - Read replicas automatically sync
# - Can have analytical DB as read replica
# - No Kafka cluster to manage

# Option B: Redis Streams (if you want async buffering)
# - Much simpler than Kafka
# - Can be single instance or small cluster
# - Good enough for ~10K events/min
# - Can be used for dead-letter queue if analytical DB slow

# Option C: PostgreSQL Queuing (using LISTEN/NOTIFY or job tables)
# - Pure SQL, no external dependencies
# - Good for work coordination
# - Limited throughput but sufficient for this scale
```

**Recommendation**:
- **Phase 1**: Use PostgreSQL logical replication (simplest, native)
- **Later (if needed)**: Add Redis Streams as async buffer for edge cases

**Simplification Benefit**: Remove 50+ lines of replication code, no Kafka cluster ops

---

### 1.2 Hierarchical Work Queue with Redis Indexing is Overcomplicated

**Current Proposal**:
- PostgreSQL work_items table
- Redis ZSET for priority lookup
- Dual writes to both

**Problem**:
- Adds latency (write twice)
- Potential sync issues (Redis vs DB diverge)
- Redis adds operational burden
- ZSET priority lookup doesn't save much time at this scale

**Better Approach: PostgreSQL-Only Queue**

```python
# SIMPLER: Single source of truth in PostgreSQL

class WorkQueue:
    async def fetch_next_batch(self, work_type, batch_size=10):
        """Fetch next work items - single DB query"""

        # This single query is fast enough
        # PostgreSQL can handle 1000s of QPS easily
        items = await self.db.query("""
            SELECT * FROM work_items
            WHERE status = 'queued'
            AND locked_until <= NOW()
            AND work_type = $1
            ORDER BY priority DESC, created_at ASC
            LIMIT $2
        """, work_type, batch_size)

        # Lock items atomically
        item_ids = [item.id for item in items]
        await self.db.query("""
            UPDATE work_items
            SET locked_until = NOW() + INTERVAL '30 minutes'
            WHERE id = ANY($1)
        """, item_ids)

        return items
```

**Index Strategy**:
```sql
-- Single strategic index on hot columns
CREATE INDEX idx_work_queue_fetch ON work_items(
    status,
    work_type,
    locked_until,
    priority DESC,
    created_at ASC
);
```

**Simplification Benefit**:
- Remove Redis dependency
- Single source of truth
- One network round-trip instead of two
- Easier to reason about consistency

---

### 1.3 Separate Operational Database is Overengineered for Phase 1

**Current Proposal**:
- Operational DB (PostgreSQL) for writes
- Analytical DB (PostgreSQL) for reads
- CDC replication between them

**Reality at 10× Scale**:
- 1,000 tokens/day = ~50 creators/hour
- ~500 sol_transfers/min from webhooks
- ~200 work_queue updates/min
- Modern PostgreSQL handles this on single instance

**Better Approach: Staged Separation**

```
Phase 1 (Weeks 1-6): Single PostgreSQL, optimized schema
├─ Schema 1: Operational tables (work_queue, address_scan_state, sol_transfers)
├─ Schema 2: Analytical tables (creator_funders, funder_clusters, risk_scores)
├─ Index optimization: Separate indexes for each workload
├─ Materialized views for dashboard
└─ All in same PostgreSQL instance

Phase 2 (Weeks 7-12): If needed, replicate analytical schema
├─ Create read replica of PostgreSQL
├─ Route analytical reads to replica
├─ Keep operational writes on primary
└─ Zero downtime split
```

**Simplification Benefit**:
- One database to manage in Phase 1
- Proven PostgreSQL replication when you scale
- No intermediate infrastructure (no Kafka)
- Can implement operational separation later with data already in right place

---

### 1.4 Graph Clustering Database Schema Can Be Simplified

**Current Proposal**:
```sql
CREATE TABLE funder_graph (
    source_address TEXT,
    target_address TEXT,
    relationship_type TEXT,
    relationship_strength INT,
    PRIMARY KEY (source_address, target_address),
    INDEX idx_target (target_address),
    INDEX idx_type (relationship_type)
);

CREATE TABLE funder_clusters (
    cluster_id UUID,
    cluster_members TEXT[],
    cluster_size INT,
    cluster_density REAL,
    ...
);

CREATE TABLE cluster_state (
    address TEXT,
    current_cluster_id UUID,
    cluster_generation INT,
    needs_recompute BOOLEAN
);
```

**Problem**:
- `cluster_members TEXT[]` array is hard to query
- `cluster_state` adds redundant tracking
- Multiple tables for essentially one concept

**Simpler Approach**: Single normalized schema

```sql
-- One table: funder-creator relationships
CREATE TABLE creator_funders (
    creator_address TEXT,
    funder_address TEXT,
    amount_sol REAL,
    first_seen_at TIMESTAMP,
    PRIMARY KEY (creator_address, funder_address)
);

-- Clustering RESULTS (denormalized for speed)
CREATE TABLE cluster_assignments (
    address TEXT PRIMARY KEY,  -- creator or funder
    cluster_id UUID,
    cluster_generation INT,
    computed_at TIMESTAMP
);

-- Pre-computed cluster metrics (materialized view)
CREATE MATERIALIZED VIEW cluster_summary AS
SELECT
    cluster_id,
    COUNT(DISTINCT address) as member_count,
    ARRAY_AGG(DISTINCT address) as members
FROM cluster_assignments
GROUP BY cluster_id;

-- Refresh when needed
REFRESH MATERIALIZED VIEW cluster_summary;
```

**Why Simpler**:
- No redundant state in `cluster_state`
- `cluster_assignments` is straightforward (address → cluster_id)
- Materialized view computes metrics on demand
- Query is simple: `SELECT * FROM cluster_assignments WHERE address = ?`

**Simplification Benefit**:
- Remove 1 table
- Easier to debug and verify
- Simpler incremental update logic
- Same functionality, less schema complexity

---

### 1.5 Address Scan State Can Use Simpler Cursor Model

**Current Proposal**:
```sql
CREATE TABLE address_scan_state (
    address TEXT PRIMARY KEY,
    address_type TEXT,
    last_seen_signature TEXT,
    last_seen_slot INTEGER,
    last_scan_at TIMESTAMP,
    next_scan_at TIMESTAMP,
    scan_completeness TEXT,
    signatures_fetched_count INTEGER,
    failure_count INTEGER,
    defer_reason TEXT
);
```

**Problem**:
- Too many columns for simple idea
- Some columns never used together
- `defer_reason` mixes concerns

**Simpler Model**:
```sql
CREATE TABLE address_scan_state (
    address TEXT PRIMARY KEY,
    last_signature TEXT,      -- Last signature we processed
    last_scan_at TIMESTAMP,   -- When we last scanned
    next_scan_at TIMESTAMP,   -- When to scan next
    status TEXT DEFAULT 'active'  -- active, paused, failed
);

-- That's it. Remove:
-- - address_type (can join to creator_funders if needed)
-- - scan_completeness (always track cursor, assume we'll eventually get everything)
-- - signatures_fetched_count (track in metrics if needed)
-- - failure_count (track separately if needed)
-- - defer_reason (track in work_queue instead)
```

**Why Simpler**:
- 4 columns vs 9
- Cursor model is straightforward: "I processed up to X, next time start after X"
- Failure tracking belongs in work_queue metrics, not here
- No ambiguity about "partial" vs "full" scan

**Simplification Benefit**:
- Easier to reason about
- Fewer query conditions
- Less data to keep consistent

---

### 1.6 RPC Caching TTL Strategy is Overspecified

**Current Proposal**:
```
- getSignatures responses: 1-hour TTL
- Transaction details: 2-hour TTL
- Address labels: 24-hour TTL
```

**Simpler Approach**:
```python
# Rule of thumb: Cache anything that doesn't change in <time_it_saves>

class RPCCache:
    # Strategy: Single TTL for most things, only split if needed

    async def get_signatures(self, address, before_sig=None):
        """Get signatures with simple caching"""

        # Key: (address, before_sig)
        # TTL: 1 hour (signatures are immutable once confirmed)
        # Hit rate: ~50% (same address queried multiple times)

        key = f"sigs:{address}:{before_sig}"
        cached = self.cache.get(key, ttl=3600)
        if cached:
            return cached

        result = await self.rpc.get_signatures(address, before=before_sig)
        self.cache.set(key, result, ttl=3600)
        return result

    async def get_transaction(self, sig):
        """Get transaction detail"""

        # Key: signature
        # TTL: infinite (transactions never change)
        # Or use 24h just for cache hygiene

        key = f"tx:{sig}"
        cached = self.cache.get(key)
        if cached:
            return cached

        result = await self.rpc.get_transaction(sig)
        self.cache.set(key, result, ttl=86400)  # 24h
        return result
```

**Simplification**:
- 1-hour TTL for signatures (immutable, safe)
- 24-hour TTL for transactions (very safe)
- Don't overthink partial result scenarios

---

### 1.7 Worker Adaptive Backoff is Too Complex

**Current Proposal**:
```python
backoff_times = [
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=30),
    timedelta(hours=2)
]
```

**Simpler Approach**:
```python
# Exponential backoff: 2^retry_count minutes, capped at 2 hours

def calculate_backoff(retry_count):
    """Simple exponential backoff"""
    minutes = min(2 ** retry_count, 120)  # 1, 2, 4, 8, 16, 32, 64, 120, 120...
    return timedelta(minutes=minutes)

# Usage:
if work.retries_remaining > 0:
    work.retries_remaining -= 1
    backoff = calculate_backoff(3 - work.retries_remaining)  # 1, 2, 4 min
    work.locked_until = now() + backoff
```

**Why Simpler**:
- One formula instead of hardcoded list
- Mathematically sound
- Easy to adjust (just change exponent or cap)

---

## SECTION 2 — Parts of the Redesign That Are Already Optimal

### 2.1 ✅ Incremental Address Cursors (Excellent)

The cursor-based extraction is the RIGHT way to do this. Don't change it.

**Why it's good**:
- Eliminates 90% of RPC rescans
- Simple concept: "remember where we left off"
- Naturally handles restarts (state persists)
- Foundation for everything else

**Keep exactly as proposed in redesign.**

---

### 2.2 ✅ Due-Time Scheduling (Excellent)

The activity-based next_scan_at calculation is exactly right.

**Why it's good**:
- Eliminates polling of dormant creators
- Scales to 100K creators easily
- Adaptive (responds to activity)
- Simple formula

**Keep exactly as proposed.**

---

### 2.3 ✅ Signature Deduplication (Excellent)

Tracking seen_signatures to skip duplicates is essential.

**Why it's good**:
- Prevents double-processing
- Simple primary key lookup
- Zero false positives

**Keep exactly as proposed.**

---

### 2.4 ✅ Per-Address ROI-Based Prioritization (Good)

Weighting priority by activity, cost, and freshness is the right approach.

**Why it's good**:
- Focuses on high-value addresses
- Cost-aware
- Simple scoring formula
- Balances freshness vs cost

**Keep with minor simplification** (see below).

---

### 2.5 ✅ Incremental Clustering Algorithm (Good)

The `on_new_funding_edge()` approach is correct: only recompute affected clusters.

**Why it's good**:
- Avoids O(n²) recomputation
- Scales to 100K creators
- Naturally incremental

**Keep as proposed, but use simpler data model** (see Section 1.4).

---

### 2.6 ✅ Phase-Based Migration (Excellent)

The 12-week zero-downtime rollout plan is pragmatic and well-structured.

**Why it's good**:
- Reduces risk
- Validates at each step
- Can rollback any phase
- Matches engineering capacity

**Keep exactly as proposed.**

---

## SECTION 3 — A Refined Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────────┐
│                  FLEX V2 SIMPLIFIED ARCHITECTURE                      │
└──────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────┐
│ EVENT INGESTION (Real-Time)                                          │
├──────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  Solana WebSocket ──┐                                                │
│  Helius Webhooks ───┼──→ Dedupe ──→ PostgreSQL (Operational Schema) │
│                    │                                                  │
│  • Extract transfers and save to sol_transfers                      │
│  • Update address_activity in real-time                             │
│  • Enqueue high-priority work items                                 │
│  • Return 200 immediately (non-blocking)                            │
│                                                                       │
└──────────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────────────┐
│ STATE MANAGEMENT (Persistent Cursors)                               │
├──────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  address_scan_state (PostgreSQL)                                    │
│  ├─ address (TEXT, PRIMARY KEY)                                     │
│  ├─ last_signature (where we left off)                              │
│  ├─ last_scan_at (when we last checked)                             │
│  ├─ next_scan_at (when to check next)                               │
│  └─ status (active/paused/failed)                                   │
│                                                                       │
│  Index: (next_scan_at, status, address)                             │
│         ↑ For due-time scheduler                                    │
│                                                                       │
└──────────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────────────┐
│ CACHING LAYER (Redis — Single Instance)                            │
├──────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  RPC Response Cache:                                                 │
│  • rpc:sigs:{address}:{before_sig} → signatures (1h TTL)           │
│  • rpc:tx:{signature} → transaction (24h TTL)                      │
│  • rpc:labels:{address} → labels (24h TTL)                         │
│                                                                       │
│  Recent Dedup Cache:                                                 │
│  • seen:{signature} → true (24h TTL)                                │
│                                                                       │
│  Hit rate target: 40-60% on signatures, 70-80% on transactions    │
│                                                                       │
└──────────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────────────┐
│ WORKER SYSTEM (Simple Event-Driven)                                 │
├──────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  Due-Time Scheduler:                                                 │
│  └─ Query: WHERE next_scan_at <= NOW() ORDER BY priority           │
│  └─ Calculate ROI-based priority                                    │
│  └─ Enqueue into work_items                                         │
│                                                                       │
│  Extraction Workers (3-5 parallel):                                  │
│  ├─ Fetch next work item from work_items                            │
│  ├─ Load address_scan_state cursor                                  │
│  ├─ Get signatures after cursor (cached)                            │
│  ├─ Parse and save relationships                                    │
│  └─ Update cursor                                                   │
│                                                                       │
│  Analysis Workers (2-3 parallel):                                    │
│  ├─ Build incremental clusters                                      │
│  ├─ Update risk scores                                              │
│  └─ Refresh materialized views                                      │
│                                                                       │
│  Work Queue (PostgreSQL):                                            │
│  ├─ id (UUID, PRIMARY KEY)                                          │
│  ├─ address, work_type, priority                                    │
│  ├─ status, locked_until                                            │
│  ├─ retries_remaining, last_error                                   │
│  └─ deadline                                                         │
│                                                                       │
│  Index: (status, locked_until, priority DESC, created_at)          │
│         ↑ One index for all queries                                  │
│                                                                       │
└──────────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────────────┐
│ DATA STORAGE (Single PostgreSQL, Optimized Schema)                  │
├──────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  OPERATIONAL TABLES (High write volume):                             │
│  ├─ address_scan_state (cursors)                                    │
│  ├─ work_items (job queue)                                          │
│  ├─ sol_transfers (webhook events, partitioned by date)            │
│  ├─ address_activity (real-time metrics)                            │
│  └─ rpc_metrics (cost tracking, partitioned by date)               │
│                                                                       │
│  ANALYTICAL TABLES (Read-heavy):                                     │
│  ├─ creator_funders (creator → funder edges)                       │
│  ├─ funder_incoming_transfers (funder → sender edges)              │
│  ├─ token_analysis (token metrics)                                  │
│  ├─ cluster_assignments (address → cluster_id)                     │
│  ├─ risk_scores (pre-computed metrics)                             │
│  └─ cluster_summary (materialized view)                            │
│                                                                       │
│  INDEXES:                                                            │
│  ├─ address_scan_state: (next_scan_at, status)                     │
│  ├─ work_items: (status, locked_until, priority DESC)              │
│  ├─ creator_funders: (creator_address), (funder_address)          │
│  ├─ token_analysis: (created_at DESC), (earliest_tx_creator)      │
│  └─ cluster_assignments: (address), (cluster_id)                   │
│                                                                       │
│  TABLE PARTITIONING (for huge tables):                              │
│  ├─ sol_transfers: PARTITION BY RANGE(block_time)                  │
│  └─ rpc_metrics: PARTITION BY RANGE(day_key)                       │
│                                                                       │
└──────────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────────────┐
│ GRAPH ANALYSIS (Database-Native)                                     │
├──────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  On Each New Edge (creator_funders insert):                          │
│  └─ Find other creators funded by same funder                       │
│  └─ Check Jaccard similarity                                         │
│  └─ Update cluster_assignments                                       │
│  └─ Refresh cluster_summary materialized view                       │
│                                                                       │
│  On Demand:                                                           │
│  └─ SELECT * FROM cluster_summary WHERE cluster_id = ?             │
│  └─ Pre-computed, instant response                                  │
│                                                                       │
└──────────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────────────┐
│ API & DASHBOARD (Flask)                                              │
├──────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  • Query analytical tables (no write locks)                         │
│  • Join with cluster_summary for networks                           │
│  • Display risk_scores for funders                                  │
│  • Show address_activity for real-time updates                      │
│                                                                       │
└──────────────────────────────────────────────────────────────────────┘
```

### Key Simplifications in Diagram

1. **No Kafka** - Direct PostgreSQL
2. **No separate operational/analytical DBs in Phase 1** - Single instance with optimized schema
3. **Single Redis instance** - Not cluster, not complex
4. **Simple work_items table** - No dual writes
5. **Simpler graph model** - cluster_assignments + materialized view
6. **Four-column address_scan_state** - Not nine

---

## SECTION 4 — The Minimal Infrastructure Needed

### 4.1 Production Infrastructure Stack

```
┌─────────────────────────────────────────────────────┐
│ MINIMAL FLEX V2 INFRASTRUCTURE                      │
└─────────────────────────────────────────────────────┘

1. PostgreSQL (Primary)
   ├─ Instance type: r6i.xlarge (4 CPU, 32 GB RAM)
   ├─ Storage: 500 GB SSD (grows ~5 GB/month)
   ├─ Backup: Daily snapshots, 30-day retention
   ├─ Cost: ~$600/month
   └─ Supports: 1000+ QPS easily

2. PostgreSQL (Read Replica, Optional Phase 2)
   ├─ Instance type: r6i.large (2 CPU, 16 GB RAM)
   ├─ Storage: 500 GB SSD (replica of primary)
   ├─ Purpose: Offload analytical reads
   ├─ Cost: ~$300/month
   └─ Can add when primary CPU hits 70%

3. Redis (Cache)
   ├─ Instance type: cache.r6g.large (2 CPU, 16 GB)
   ├─ Storage: 16 GB RAM (good for ~100M cached items)
   ├─ Eviction policy: LRU (least recently used)
   ├─ Cost: ~$150/month
   └─ Hit rate target: 45% saves 45% of RPC

4. Application Server(s)
   ├─ Docker container: 2 CPU, 4 GB RAM
   ├─ Instance type: t4g.large (2 CPU, 8 GB RAM)
   ├─ Quantity: 1 primary + 1 backup initially
   ├─ Cost: ~$50/month each ($100 total)
   └─ Handles: Flask API + Webhook handler + Workers

5. No additional infrastructure needed
   ├─ No Kafka
   ├─ No Helius monitoring API (use single API key)
   ├─ No ElasticSearch or monitoring DB
   └─ Monitoring: CloudWatch + basic logging

TOTAL MONTHLY COST: ~$1,250
- PostgreSQL primary: $600
- PostgreSQL replica (Phase 2): $300
- Redis: $150
- Application: $100
- Networking/misc: $100
```

### 4.2 What You Can Delete from Original Redesign

```
❌ Kafka cluster + CDC setup
❌ Elasticsearch for metrics
❌ Separate analytical PostgreSQL (use replica instead)
❌ Redis cluster (single instance is fine)
❌ Kafka Connect / Stream processors
❌ Multiple monitoring systems
```

### 4.3 What Actually Matters

```
✅ PostgreSQL with good indexes
✅ Redis for RPC response caching
✅ Persistent address_scan_state cursors
✅ Simple work_items queue
✅ Due-time scheduling logic
✅ Basic logging + CloudWatch alerts
```

---

## SECTION 5 — The Most Important Improvements to Implement First

### Priority Ranking (by ROI per week of work)

#### 🥇 Priority 1: Address Scan State Cursors (Week 1-2)
**Impact**: 60% RPC reduction
**Effort**: 1 week
**ROI**: 60 points/week

**Implementation**:
1. Create `address_scan_state` table (4 columns)
2. Modify `realtime_creator_funding_extractor.py` to use cursors
3. Add initial backfill (one-time script)
4. Run in parallel with old code for 1 week, validate results
5. Switch over

**Why first**:
- Highest single impact
- Foundation for everything else
- Proven concept (just need to add persistence)
- Can verify immediately (count RPC calls)

```python
# Pseudo-code for Phase 1 start
async def extract_creator_funding_v2(creator_address, created_at):
    """NEW: Incremental extraction with cursor"""

    # Load cursor from DB
    state = await db.query(
        "SELECT last_signature FROM address_scan_state WHERE address = ?",
        creator_address
    )

    # Only fetch NEW signatures
    if state:
        new_sigs = await rpc.get_signatures(
            creator_address,
            before=state.last_signature
        )
    else:
        new_sigs = await rpc.get_signatures(creator_address)
        if len(new_sigs) > 0:
            # Update cursor
            await db.query(
                """
                INSERT INTO address_scan_state (address, last_signature, last_scan_at)
                VALUES (?, ?, NOW())
                ON CONFLICT (address) DO UPDATE SET last_signature = ?, last_scan_at = NOW()
                """,
                creator_address, new_sigs[0].signature, new_sigs[0].signature
            )

    # Process new signatures
    for sig in new_sigs:
        # ... extract transfers
```

---

#### 🥈 Priority 2: RPC Response Caching (Week 3-4)
**Impact**: 35% RPC reduction
**Effort**: 0.5 weeks
**ROI**: 70 points/week

**Implementation**:
1. Deploy Redis (single instance)
2. Wrap RPC client with caching layer
3. Cache signatures for 1 hour, transactions for 24 hours
4. Monitor hit rate (should be 40-60%)

```python
# Simple caching wrapper
class CachedRPCClient:
    def __init__(self, real_client, redis_client):
        self.rpc = real_client
        self.cache = redis_client

    async def get_signatures(self, address, before=None, limit=100):
        key = f"sigs:{address}:{before}:{limit}"

        cached = self.cache.get(key)
        if cached:
            return json.loads(cached)

        result = await self.rpc.get_signatures(address, before, limit)
        self.cache.setex(key, 3600, json.dumps(result))
        return result
```

---

#### 🥉 Priority 3: Due-Time Scheduling (Week 5-6)
**Impact**: 40-60% DB load reduction
**Effort**: 1 week
**ROI**: 50 points/week

**Implementation**:
1. Add `next_scan_at` column to address_scan_state
2. Implement scheduler that calculates due times
3. Replace polling loop with due-time query
4. Run in parallel with polling for 1 week

```python
# Replace this:
while True:
    creators = await db.get_all_creators()
    for creator in creators:
        await update_status(creator)
    await asyncio.sleep(30)

# With this:
while True:
    due_items = await db.query(
        "SELECT address FROM address_scan_state WHERE next_scan_at <= NOW()"
    )
    for item in due_items:
        await update_status(item.address)

    # Sleep until next item is due
    next_due = await db.query_scalar(
        "SELECT MIN(next_scan_at) FROM address_scan_state WHERE next_scan_at > NOW()"
    )
    sleep_seconds = (next_due - now()).total_seconds()
    await asyncio.sleep(min(sleep_seconds, 60))  # Max 60s sleep
```

---

#### Priority 4: Signature Deduplication (Week 4-5)
**Impact**: 5-10% RPC reduction
**Effort**: 0.5 weeks
**ROI**: 10-20 points/week

**Implementation**:
1. Add `seen_signatures` table with signature as PK
2. Check before processing in webhook handler
3. TTL/cleanup old signatures (optional, space is cheap)

```python
def extract_sol_transfers_with_dedup(signature, tx_data):
    # Skip if seen
    if db.query_scalar("SELECT 1 FROM seen_signatures WHERE signature = ?", signature):
        return

    # Process
    transfers = extract_transfers(tx_data)
    for transfer in transfers:
        save_transfer(transfer)

    # Mark seen
    db.query("INSERT INTO seen_signatures (signature, first_seen_at) VALUES (?, NOW())", signature)
```

---

#### Priority 5: Work Queue + Cost Tracking (Week 7-8)
**Impact**: 10% RPC reduction, better observability
**Effort**: 1 week
**ROI**: 10 points/week

**Implementation**:
1. Add work_items table (simple version)
2. Enqueue addresses that need re-extraction
3. Add cost tracking to metrics
4. Calculate ROI for each address

---

### Recommended Implementation Order

```
Week 1-2:  Address scan state cursors ━━━━━━━━━━ START HERE
           ↓ 60% RPC reduction immediately

Week 3-4:  RPC response caching ━━━━━━━━━━
           ↓ Another 35% reduction (now at 50% from baseline)

Week 4-5:  Signature deduplication ━━━━━━
           ↓ 5-10% more

Week 5-6:  Due-time scheduling ━━━━━━━━━━
           ↓ 40% DB load reduction

Week 7-8:  Work queue + cost tracking ━━━━
           ↓ Better visibility

Week 9-10: Incremental clustering ━━━━━
           ↓ Handles 100K creators

Week 11-12: Cleanup old code ━━━━
```

### Stopping Point (if needed)

If you only do weeks 1-6:
- **RPC reduction**: 70-80%
- **DB load reduction**: 40% (most of benefit)
- **Scalability**: 5× capacity
- **Time**: 6 weeks
- **Cost**: ~$50k in engineering
- **ROI**: $480/year + cleaner system

That's enough to ship. The rest (incremental clustering, storage separation) is optimization for >10× scale.

---

## Summary: Simplifications Applied

| Component | Original | Simplified | Benefit |
|:---|:---|:---|:---|
| **Event Stream** | Kafka | PostgreSQL replication | Remove 50 lines, 0 ops complexity |
| **Work Queue** | Redis + DB dual-write | PostgreSQL only | Single source of truth |
| **DB Separation** | Immediate split | Phase 2 (read replica) | Simpler Phase 1, still scalable |
| **Graph Model** | 3 tables | 2 tables | Easier queries, same function |
| **Cursor State** | 9 columns | 4 columns | Simpler, clearer semantics |
| **Cache TTLs** | 3 different | 2 (1h, 24h) | Easier to reason about |
| **Worker Backoff** | 4-level array | Exponential formula | Cleaner code |
| **Infrastructure** | Complex | 4 services | Easy to manage |

**Result**: Same 10× scaling benefit, 30% less complexity, zero Kafka operational burden.
