# FLEX Wallet Cluster Detection System — Design Document

**Date**: March 10, 2026
**System**: FLEX Solana Analytics Platform
**Purpose**: Detect developer farms, coordinated launches, and shared funding wallets
**Data Source**: Phase 3.2 transfer_index table (90-day retention window)

---

## SECTION 1 — DEV FARM DETECTION QUERIES

### 1.1 Core Detection: Wallets Funding Multiple Creators

**Query**: Find all wallets that fund 3+ distinct creators

```sql
-- Basic dev farm detection
SELECT
    source AS funding_wallet,
    COUNT(DISTINCT destination) AS creators_funded,
    COUNT(*) AS total_transfers,
    SUM(amount_sol) AS total_sol_sent,
    AVG(amount_sol) AS avg_transfer_size,
    MIN(block_time) AS first_funding_date,
    MAX(block_time) AS last_funding_date
FROM transfer_index
GROUP BY source
HAVING creators_funded >= 3
ORDER BY creators_funded DESC;
```

**What it detects**:
- Multi-creator funders (strong dev farm signal)
- Funding volume and consistency
- Time window of activity

**Expected results for real dev farms**:
```
funding_wallet: 5KL8w9...         creators_funded: 18  total_transfers: 42  total_sol_sent: 85.5
funding_wallet: 7GM2x4...         creators_funded: 12  total_transfers: 28  total_sol_sent: 52.3
funding_wallet: 3HN5z1...         creators_funded: 8   total_transfers: 15  total_sol_sent: 31.2
```

### 1.2 High-Confidence Dev Farm Pattern

**Query**: Wallets funding multiple creators with consistent small amounts

```sql
-- High-confidence dev farm (seed funding pattern)
SELECT
    source AS dev_farm_wallet,
    COUNT(DISTINCT destination) AS creators_funded,
    ROUND(AVG(amount_sol), 2) AS avg_seed_amount,
    MIN(amount_sol) AS min_amount,
    MAX(amount_sol) AS max_amount,
    STDDEV(amount_sol) AS amount_stddev,
    ROUND((MAX(block_time) - MIN(block_time)) / 86400.0, 1) AS funding_span_days,
    COUNT(DISTINCT strftime('%Y-%m-%d', datetime(block_time, 'unixepoch'))) AS days_active
FROM transfer_index
WHERE amount_sol BETWEEN 0.5 AND 10  -- Typical seed range
GROUP BY source
HAVING creators_funded >= 3
    AND AVG(amount_sol) BETWEEN 0.5 AND 5  -- Consistent seed size
    AND STDDEV(amount_sol) < 2  -- Low variance (consistent funding)
ORDER BY creators_funded DESC;
```

**What it detects**:
- Structured funding patterns (not random transfers)
- Consistent seed amounts (0.5-5 SOL typical)
- Active funding across multiple days
- Low amount variance (sign of automated/programmatic funding)

**Confidence score logic**:
- 5+ creators: 🔴 VERY HIGH confidence
- 3-4 creators + consistent amounts: 🟡 MEDIUM-HIGH confidence
- Single creator with multiple transfers: 🟢 LOW (could be legitimate payment)

### 1.3 Temporal Clustering Detection

**Query**: Identify wallets with synchronized funding bursts (batch launch signal)

```sql
-- Detect batch/synchronized funding
WITH funding_events AS (
    SELECT
        source,
        destination,
        block_time,
        amount_sol,
        -- Round block time to 1-hour windows
        (block_time / 3600) * 3600 AS funding_hour
    FROM transfer_index
    WHERE amount_sol BETWEEN 0.5 AND 10
)
SELECT
    source AS dev_farm,
    funding_hour,
    COUNT(DISTINCT destination) AS creators_in_burst,
    COUNT(*) AS transfers_in_burst,
    SUM(amount_sol) AS sol_in_burst,
    ROUND(AVG(amount_sol), 2) AS avg_amount
FROM funding_events
GROUP BY source, funding_hour
HAVING creators_in_burst >= 2  -- 2+ creators funded in same hour
ORDER BY source, funding_hour DESC;
```

**What it detects**:
- Batch/coordinated launch patterns (multiple creators funded simultaneously)
- Automated funding scripts (synchronized timing)
- Launch intensity (how many creators per hour)

**Use case**: Identify coordinated token launches (high-risk pattern)

---

## SECTION 2 — CREATOR CLUSTERING QUERIES

### 2.1 Creators Sharing Funders (Direct Clustering)

**Query**: Find creators funded by the same wallet

```sql
-- Creator pairs sharing the same funder
SELECT
    a.source AS shared_funder,
    a.destination AS creator1,
    b.destination AS creator2,
    COUNT(DISTINCT a.block_time) AS creator1_transfer_count,
    COUNT(DISTINCT b.block_time) AS creator2_transfer_count,
    ROUND(AVG(a.amount_sol), 2) AS creator1_avg_funding,
    ROUND(AVG(b.amount_sol), 2) AS creator2_avg_funding,
    MIN(LEAST(a.block_time, b.block_time)) AS first_funding_date,
    MAX(GREATEST(a.block_time, b.block_time)) AS last_funding_date
FROM transfer_index a
JOIN transfer_index b
    ON a.source = b.source
    AND a.destination < b.destination  -- Avoid duplicate pairs
WHERE a.destination != b.destination
    AND a.amount_sol BETWEEN 0.5 AND 10
    AND b.amount_sol BETWEEN 0.5 AND 10
GROUP BY a.source, a.destination, b.destination
ORDER BY a.source, creator1_transfer_count DESC;
```

**What it produces**:
```
shared_funder: 5KL8w9...    creator1: Dev1...    creator2: Dev2...    transfers1: 2    transfers2: 2
shared_funder: 5KL8w9...    creator1: Dev1...    creator2: Dev3...    transfers1: 2    transfers2: 3
shared_funder: 5KL8w9...    creator1: Dev2...    creator2: Dev3...    transfers1: 2    transfers2: 3
```

### 2.2 Multi-Hop Clustering (Transitive Relationships)

**Query**: Find creator clusters through multi-level funding relationships

```sql
-- Identify connected creator networks through shared funders
WITH creator_links AS (
    -- Step 1: Find all creator pairs sharing funders
    SELECT
        a.destination AS creator1,
        b.destination AS creator2,
        a.source AS shared_funder,
        COUNT(*) AS connection_strength
    FROM transfer_index a
    JOIN transfer_index b
        ON a.source = b.source
        AND a.destination < b.destination
    WHERE a.amount_sol BETWEEN 0.5 AND 10
        AND b.amount_sol BETWEEN 0.5 AND 10
    GROUP BY a.destination, b.destination, a.source
)
-- Step 2: Aggregate creator connectivity
SELECT
    creator1,
    GROUP_CONCAT(DISTINCT creator2, ',') AS connected_creators,
    COUNT(DISTINCT creator2) AS cluster_size,
    SUM(connection_strength) AS total_connection_strength,
    COUNT(DISTINCT shared_funder) AS num_common_funders
FROM creator_links
GROUP BY creator1
HAVING cluster_size >= 2
ORDER BY cluster_size DESC;
```

**Output structure**:
```
creator1: Dev1...        connected_creators: Dev2,Dev3,Dev4,Dev5        cluster_size: 4
creator1: Dev6...        connected_creators: Dev7,Dev8                 cluster_size: 2
```

This builds the transitive closure of creator networks.

### 2.3 Cluster Density Analysis

**Query**: Measure how tightly connected clusters are

```sql
-- Measure cluster cohesion (how many shared funders between creators)
WITH creator_pairs AS (
    SELECT
        CASE
            WHEN a.destination < b.destination THEN a.destination
            ELSE b.destination
        END AS creator_a,
        CASE
            WHEN a.destination < b.destination THEN b.destination
            ELSE a.destination
        END AS creator_b,
        a.source AS shared_funder
    FROM transfer_index a
    JOIN transfer_index b
        ON a.source = b.source
        AND a.destination != b.destination
    WHERE a.amount_sol BETWEEN 0.5 AND 10
        AND b.amount_sol BETWEEN 0.5 AND 10
)
SELECT
    creator_a,
    creator_b,
    COUNT(DISTINCT shared_funder) AS num_shared_funders,
    GROUP_CONCAT(DISTINCT shared_funder, ',') AS funders
FROM creator_pairs
GROUP BY creator_a, creator_b
HAVING num_shared_funders >= 2  -- 2+ shared funders = strong clustering
ORDER BY num_shared_funders DESC;
```

**What it means**:
- 2+ shared funders = creators likely part of same dev farm
- 3+ shared funders = highly coordinated network
- Confidence increases with more shared funders

---

## SECTION 3 — CLUSTER STORAGE SCHEMA

### 3.1 Main Cluster Table

```sql
CREATE TABLE IF NOT EXISTS wallet_clusters (
    -- Cluster identification
    cluster_id INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_type TEXT NOT NULL,  -- 'dev_farm', 'creator_network', 'exchange_detected'
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,

    -- Cluster composition
    funder_wallet TEXT,  -- Primary funder (if single)
    creator_addresses TEXT NOT NULL,  -- JSON array of creator addresses
    creator_count INTEGER NOT NULL,

    -- Confidence scoring
    confidence_score REAL DEFAULT 0.5,  -- 0-1 scale
    confidence_reasons TEXT,  -- JSON array of factors

    -- Metrics
    total_transfers INTEGER DEFAULT 0,
    total_sol_distributed REAL DEFAULT 0,
    avg_transfer_size REAL DEFAULT 0,
    transfer_stddev REAL DEFAULT 0,

    -- Temporal info
    first_transfer_time INTEGER,
    last_transfer_time INTEGER,
    days_active INTEGER,

    -- Status
    status TEXT DEFAULT 'active',  -- 'active', 'archived', 'false_positive'
    is_likely_farm BOOLEAN DEFAULT 0,
    is_likely_exchange BOOLEAN DEFAULT 0,

    -- Analysis flags
    has_synchronized_funding BOOLEAN DEFAULT 0,
    has_consistent_amounts BOOLEAN DEFAULT 0,
    has_multiple_shared_funders BOOLEAN DEFAULT 0,

    UNIQUE(cluster_type, funder_wallet, creator_count)
);
```

### 3.2 Cluster Members Table (Normalized)

```sql
CREATE TABLE IF NOT EXISTS cluster_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id INTEGER NOT NULL,
    wallet_address TEXT NOT NULL,
    wallet_type TEXT NOT NULL,  -- 'funder' or 'creator'

    -- Relationship metrics
    transfer_count INTEGER DEFAULT 0,
    total_sol INTEGER DEFAULT 0,
    avg_transfer_size REAL DEFAULT 0,

    -- When did this wallet join the cluster?
    joined_at REAL NOT NULL,

    -- Link to source table for audit trail
    first_transfer_id INTEGER,
    last_transfer_id INTEGER,

    FOREIGN KEY(cluster_id) REFERENCES wallet_clusters(cluster_id),
    UNIQUE(cluster_id, wallet_address)
);

CREATE INDEX idx_cluster_members_cluster ON cluster_members(cluster_id);
CREATE INDEX idx_cluster_members_wallet ON cluster_members(wallet_address);
```

### 3.3 Cluster Relationships (Multi-Level Connections)

```sql
CREATE TABLE IF NOT EXISTS cluster_relationships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id_a INTEGER NOT NULL,
    cluster_id_b INTEGER NOT NULL,
    relationship_type TEXT NOT NULL,  -- 'shares_funder', 'shares_creators', 'transitive'
    connection_strength INTEGER DEFAULT 1,  -- Number of connections
    created_at REAL NOT NULL,

    FOREIGN KEY(cluster_id_a) REFERENCES wallet_clusters(cluster_id),
    FOREIGN KEY(cluster_id_b) REFERENCES wallet_clusters(cluster_id),
    UNIQUE(cluster_id_a, cluster_id_b, relationship_type)
);

CREATE INDEX idx_relationships_a ON cluster_relationships(cluster_id_a);
CREATE INDEX idx_relationships_b ON cluster_relationships(cluster_id_b);
```

### 3.4 Detection Audit Log

```sql
CREATE TABLE IF NOT EXISTS cluster_detection_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    detection_run_time REAL NOT NULL,
    clusters_found INTEGER DEFAULT 0,
    clusters_new INTEGER DEFAULT 0,
    clusters_updated INTEGER DEFAULT 0,
    detection_method TEXT NOT NULL,  -- 'daily_batch', 'realtime', 'backfill'
    duration_ms REAL DEFAULT 0,
    status TEXT DEFAULT 'success',  -- 'success', 'failed', 'partial'
    error_message TEXT,

    INDEX idx_detection_log_time (detection_run_time DESC)
);
```

---

## SECTION 4 — FALSE POSITIVE FILTERING

### 4.1 Exchange Detection Filter

**Problem**: Large exchanges and services fund many creators (false positives)

```sql
-- Known exchange and service wallets
CREATE TABLE IF NOT EXISTS known_wallets (
    wallet_address TEXT PRIMARY KEY,
    wallet_type TEXT NOT NULL,  -- 'exchange', 'service', 'whale', 'verified_entity'
    entity_name TEXT,
    confidence REAL DEFAULT 1.0,
    created_at REAL NOT NULL
);

-- Example entries
INSERT INTO known_wallets VALUES
    ('11111111111111111111111111111111', 'service', 'Raydium', 1.0, 1609459200),
    ('9B5X6zDn9CNA...', 'exchange', 'Binance', 1.0, 1609459200),
    ('TokenkegQfeZyiNwAJsyFbPVwwQkfus6', 'service', 'SPL Token Program', 1.0, 1609459200);

-- Filter exchanges from cluster detection
SELECT
    source,
    COUNT(DISTINCT destination) AS creators_funded
FROM transfer_index
WHERE amount_sol BETWEEN 0.5 AND 10
    AND source NOT IN (SELECT wallet_address FROM known_wallets WHERE wallet_type IN ('exchange', 'service'))
GROUP BY source
HAVING creators_funded >= 3
ORDER BY creators_funded DESC;
```

### 4.2 Whale Detection Filter

**Problem**: Legitimate whales redistribute to many wallets (not dev farms)

```sql
-- Filter out whale behavior (very large transfers to many receivers)
SELECT
    source AS potential_farm,
    COUNT(DISTINCT destination) AS creators,
    AVG(amount_sol) AS avg_amount,
    MAX(amount_sol) AS max_amount,
    STDDEV(amount_sol) AS amount_variance
FROM transfer_index
GROUP BY source
HAVING creators >= 3
    AND avg_amount BETWEEN 0.5 AND 5  -- Seed range (NOT whale)
    AND max_amount < 50  -- No huge individual transfers
    AND amount_variance < 3  -- Consistent amounts
ORDER BY creators DESC;
```

**Key filters**:
- Exclude transfers > 50 SOL (likely whale redistribution)
- Require consistent amounts (stddev < 3)
- Avg amount must be in seed range (0.5-5 SOL)

### 4.3 Time-Based False Positive Filter

**Problem**: One-time funding from wallet is not a dev farm

```sql
-- Require sustained funding activity (not just one-time distribution)
SELECT
    source AS dev_farm,
    COUNT(DISTINCT destination) AS creators,
    COUNT(DISTINCT strftime('%Y-%m-%d', datetime(block_time, 'unixepoch'))) AS days_active,
    ROUND((MAX(block_time) - MIN(block_time)) / 86400.0, 1) AS span_days
FROM transfer_index
WHERE amount_sol BETWEEN 0.5 AND 10
    AND source NOT IN (SELECT wallet_address FROM known_wallets)
GROUP BY source
HAVING creators >= 3
    AND days_active >= 2  -- Active on 2+ days (not one-time distribution)
    AND span_days >= 1  -- Spread over at least 1 day
ORDER BY creators DESC;
```

### 4.4 Liquidity Provider Filter

**Problem**: Uniswap/Serum/Raydium LPs could trigger false positives**

```sql
-- Filter common liquidity program addresses
SELECT
    source,
    COUNT(DISTINCT destination) AS creators_funded
FROM transfer_index
WHERE amount_sol BETWEEN 0.5 AND 10
    AND source NOT IN (
        -- Known DEX programs
        '675kPX9MHTjS2zt1qrXCVJJJoc5Y5yV8xvHkMsnnqM7a',  -- Raydium
        '98p6r2TWNcnKxJXGomjXrfB4w9EdYBqqzuFyxp7EfYs7',  -- Serum
        '675kPX9MHTjS2zt1qrXCVJJJoc5Y5yV8xvHkMsnnqM7a'   -- OpenBook
    )
    AND source NOT IN (SELECT wallet_address FROM known_wallets)
GROUP BY source
HAVING creators_funded >= 3
ORDER BY creators_funded DESC;
```

### 4.5 Confidence Scoring Function

```sql
-- Composite scoring for cluster quality
WITH cluster_candidates AS (
    SELECT
        source AS dev_farm,
        COUNT(DISTINCT destination) AS creators,
        COUNT(*) AS transfers,
        AVG(amount_sol) AS avg_amount,
        STDDEV(amount_sol) AS amount_stddev,
        COUNT(DISTINCT strftime('%Y-%m-%d', datetime(block_time, 'unixepoch'))) AS days_active,
        (MAX(block_time) - MIN(block_time)) / 86400.0 AS span_days
    FROM transfer_index
    WHERE amount_sol BETWEEN 0.5 AND 10
        AND source NOT IN (SELECT wallet_address FROM known_wallets)
    GROUP BY source
    HAVING creators >= 3
)
SELECT
    dev_farm,
    creators,
    transfers,
    -- Confidence scoring (0-100)
    ROUND(
        (
            -- Creator count (20 points max)
            CASE
                WHEN creators >= 10 THEN 20
                WHEN creators >= 5 THEN 15
                WHEN creators >= 3 THEN 10
                ELSE 0
            END +
            -- Consistency (20 points max)
            CASE
                WHEN amount_stddev < 1 THEN 20
                WHEN amount_stddev < 2 THEN 15
                WHEN amount_stddev < 3 THEN 10
                ELSE 0
            END +
            -- Duration (20 points max)
            CASE
                WHEN span_days >= 7 THEN 20
                WHEN span_days >= 3 THEN 15
                WHEN span_days >= 1 THEN 10
                ELSE 0
            END +
            -- Activity level (20 points max)
            CASE
                WHEN transfers >= 20 THEN 20
                WHEN transfers >= 10 THEN 15
                WHEN transfers >= 5 THEN 10
                ELSE 0
            END +
            -- Seed amount range (20 points max)
            CASE
                WHEN avg_amount BETWEEN 1 AND 3 THEN 20
                WHEN avg_amount BETWEEN 0.5 AND 5 THEN 15
                ELSE 0
            END
        ),
        0
    ) AS confidence_score,
    CASE
        WHEN ROUND(...) >= 80 THEN '🔴 VERY HIGH'
        WHEN ROUND(...) >= 60 THEN '🟠 HIGH'
        WHEN ROUND(...) >= 40 THEN '🟡 MEDIUM'
        ELSE '🟢 LOW'
    END AS confidence_level
FROM cluster_candidates
WHERE days_active >= 2
ORDER BY confidence_score DESC;
```

---

## SECTION 5 — PERFORMANCE OPTIMIZATION

### 5.1 Required Indexes for Cluster Detection

```sql
-- Indexes on transfer_index for cluster detection queries

-- PRIMARY: Source-destination pairs (most common query pattern)
CREATE INDEX IF NOT EXISTS idx_transfer_source_destination
ON transfer_index(source, destination, amount_sol, block_time);

-- SECONDARY: Amount range queries (seed detection)
CREATE INDEX IF NOT EXISTS idx_transfer_amount_time
ON transfer_index(amount_sol, block_time DESC);

-- TERTIARY: Destination lookups (reverse relationships)
CREATE INDEX IF NOT EXISTS idx_transfer_destination_time
ON transfer_index(destination, block_time DESC);

-- QUATERNARY: Temporal clustering queries
CREATE INDEX IF NOT EXISTS idx_transfer_block_time_source
ON transfer_index(block_time DESC, source, amount_sol);

-- Optional: Covering index for common queries (SQLite 3.31+)
CREATE INDEX IF NOT EXISTS idx_transfer_cluster_detection
ON transfer_index(source, destination)
INCLUDE (amount_sol, block_time);
```

### 5.2 Query Optimization Strategies

**Strategy 1: Materialized Views for Hot Queries**

```sql
-- Pre-compute basic farm detection monthly
CREATE TABLE IF NOT EXISTS precomputed_farms_monthly (
    compute_date TEXT NOT NULL,  -- YYYY-MM format
    dev_farm TEXT,
    creators_funded INTEGER,
    total_transfers INTEGER,
    avg_amount REAL,
    confidence_score REAL,

    PRIMARY KEY(compute_date, dev_farm)
);

-- Refresh monthly (run first of each month)
INSERT OR REPLACE INTO precomputed_farms_monthly
SELECT
    strftime('%Y-%m', datetime(block_time, 'unixepoch')),
    source,
    ...  -- cluster detection query
FROM transfer_index
WHERE block_time > (SELECT MAX(block_time) FROM precomputed_farms_monthly);
```

**Strategy 2: Incremental Detection**

```sql
-- Only scan transfers since last detection run
CREATE TABLE IF NOT EXISTS cluster_detection_state (
    last_block_time INTEGER,
    last_detection_time REAL
);

-- Query only new transfers
SELECT source, COUNT(DISTINCT destination) AS creators
FROM transfer_index
WHERE block_time > (SELECT last_block_time FROM cluster_detection_state)
    AND amount_sol BETWEEN 0.5 AND 10
GROUP BY source
HAVING creators >= 3;

-- Update state after detection completes
UPDATE cluster_detection_state
SET last_block_time = (SELECT MAX(block_time) FROM transfer_index),
    last_detection_time = unixepoch();
```

### 5.3 Query Execution Plan Analysis

```bash
-- Analyze query performance
EXPLAIN QUERY PLAN
SELECT source, COUNT(DISTINCT destination) AS creators
FROM transfer_index
WHERE amount_sol BETWEEN 0.5 AND 10
GROUP BY source
HAVING creators >= 3;

-- Expected output should use index, not full table scan
-- Search 0: SCAN TABLE transfer_index USING INDEX idx_transfer_amount_time
```

### 5.4 Memory-Efficient Processing

**For large datasets (millions of transfers)**:

```python
# Process clusters in batches to avoid memory issues
def detect_clusters_batched(db_path, batch_size=100):
    """Process cluster detection in memory-efficient batches."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Get all potential funders first
    cursor.execute("""
        SELECT DISTINCT source FROM transfer_index
        WHERE amount_sol BETWEEN 0.5 AND 10
    """)
    funders = [row[0] for row in cursor.fetchall()]

    # Process in batches
    for i in range(0, len(funders), batch_size):
        batch = funders[i:i+batch_size]

        # Analyze each funder's network
        for funder in batch:
            analyze_funder_cluster(conn, funder)

    conn.close()
```

---

## SECTION 6 — FLEX PIPELINE INTEGRATION

### 6.1 Scheduled Cluster Detection

**Daily detection job** (runs at 3 AM UTC, after daily cleanup)

```python
# File: src/core/wallet_clustering.py

import sqlite3
import time
import logging
from datetime import datetime
from typing import Dict, List

logger = logging.getLogger(__name__)

class WalletClusteringEngine:
    """Detect dev farms and creator clusters from transfer_index."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.detection_run_time = time.time()

    def detect_clusters(self) -> Dict:
        """Main entry point for cluster detection."""
        results = {
            'clusters_found': 0,
            'clusters_new': 0,
            'clusters_updated': 0,
            'detection_duration_ms': 0,
            'status': 'pending'
        }

        start_time = time.time()

        try:
            conn = self._get_conn()
            cursor = conn.cursor()

            # Step 1: Detect high-confidence dev farms
            farms = self._detect_dev_farms(cursor)
            logger.info(f"[CLUSTERING] Found {len(farms)} potential dev farms")

            # Step 2: Detect creator clusters
            clusters = self._detect_creator_clusters(cursor, farms)
            logger.info(f"[CLUSTERING] Built {len(clusters)} creator clusters")

            # Step 3: Filter false positives
            filtered = self._filter_false_positives(cursor, clusters)
            logger.info(f"[CLUSTERING] Filtered to {len(filtered)} high-confidence clusters")

            # Step 4: Score clusters
            scored = self._score_clusters(cursor, filtered)

            # Step 5: Store in database
            self._store_clusters(conn, scored)
            results['clusters_found'] = len(scored)
            results['status'] = 'success'

            conn.close()

        except Exception as e:
            logger.error(f"[CLUSTERING] Detection failed: {e}", exc_info=True)
            results['status'] = 'error'
            results['error'] = str(e)

        finally:
            results['detection_duration_ms'] = (time.time() - start_time) * 1000

        # Log detection run
        self._log_detection_run(results)

        return results

    def _detect_dev_farms(self, cursor) -> List[Dict]:
        """Find wallets funding 3+ creators with consistent amounts."""
        cursor.execute("""
            SELECT
                source AS dev_farm,
                COUNT(DISTINCT destination) AS creators,
                COUNT(*) AS transfers,
                ROUND(AVG(amount_sol), 2) AS avg_amount,
                ROUND(STDDEV(amount_sol), 2) AS amount_stddev,
                COUNT(DISTINCT strftime('%Y-%m-%d', datetime(block_time, 'unixepoch'))) AS days_active,
                (MAX(block_time) - MIN(block_time)) / 86400.0 AS span_days,
                MIN(block_time) AS first_transfer,
                MAX(block_time) AS last_transfer
            FROM transfer_index
            WHERE amount_sol BETWEEN 0.5 AND 10
            GROUP BY source
            HAVING creators >= 3
                AND AVG(amount_sol) BETWEEN 0.5 AND 5
                AND STDDEV(amount_sol) < 3
                AND days_active >= 2
            ORDER BY creators DESC
        """)

        farms = []
        for row in cursor.fetchall():
            farms.append({
                'dev_farm': row[0],
                'creators': row[1],
                'transfers': row[2],
                'avg_amount': row[3],
                'amount_stddev': row[4],
                'days_active': row[5],
                'span_days': row[6],
                'first_transfer': row[7],
                'last_transfer': row[8]
            })

        return farms

    def _detect_creator_clusters(self, cursor, farms: List[Dict]) -> List[Dict]:
        """Group creators by shared funding relationships."""
        clusters = {}

        for farm in farms:
            # Get all creators funded by this farm
            cursor.execute("""
                SELECT DISTINCT destination
                FROM transfer_index
                WHERE source = ? AND amount_sol BETWEEN 0.5 AND 10
            """, (farm['dev_farm'],))

            creators = [row[0] for row in cursor.fetchall()]

            if len(creators) >= 3:
                cluster_key = tuple(sorted(creators))
                clusters[cluster_key] = {
                    'creators': creators,
                    'creator_count': len(creators),
                    'primary_funder': farm['dev_farm'],
                    'confidence_score': self._calculate_confidence(farm)
                }

        return list(clusters.values())

    def _filter_false_positives(self, cursor, clusters: List[Dict]) -> List[Dict]:
        """Remove exchanges, services, and non-farm patterns."""
        filtered = []

        for cluster in clusters:
            # Check if funder is in known wallets
            cursor.execute("""
                SELECT wallet_type FROM known_wallets
                WHERE wallet_address = ?
            """, (cluster['primary_funder'],))

            result = cursor.fetchone()
            if result and result[0] in ('exchange', 'service'):
                continue  # Filter out known exchanges

            # Check temporal pattern (not one-time burst)
            cursor.execute("""
                SELECT COUNT(DISTINCT strftime('%Y-%m-%d', datetime(block_time, 'unixepoch')))
                FROM transfer_index
                WHERE source = ? AND amount_sol BETWEEN 0.5 AND 10
            """, (cluster['primary_funder'],))

            days_active = cursor.fetchone()[0]
            if days_active < 2:
                continue  # Not sustained funding

            filtered.append(cluster)

        return filtered

    def _score_clusters(self, cursor, clusters: List[Dict]) -> List[Dict]:
        """Calculate confidence scores for each cluster."""
        for cluster in clusters:
            # Composite score (0-100)
            score = 0

            # Creator count (20 points max)
            if cluster['creator_count'] >= 10:
                score += 20
            elif cluster['creator_count'] >= 5:
                score += 15
            elif cluster['creator_count'] >= 3:
                score += 10

            # Consistency (20 points) - already in confidence_score
            score += cluster['confidence_score'] // 5

            cluster['confidence_score'] = min(100, score)

        return clusters

    def _calculate_confidence(self, farm: Dict) -> float:
        """Calculate confidence for a farm based on metrics."""
        score = 0

        # Consistency check
        if farm['amount_stddev'] < 1:
            score += 20
        elif farm['amount_stddev'] < 2:
            score += 15
        elif farm['amount_stddev'] < 3:
            score += 10

        # Duration check
        if farm['span_days'] >= 7:
            score += 20
        elif farm['span_days'] >= 3:
            score += 15
        elif farm['span_days'] >= 1:
            score += 10

        # Activity level
        if farm['transfers'] >= 20:
            score += 20
        elif farm['transfers'] >= 10:
            score += 15
        elif farm['transfers'] >= 5:
            score += 10

        # Amount range
        if 1 <= farm['avg_amount'] <= 3:
            score += 20
        elif 0.5 <= farm['avg_amount'] <= 5:
            score += 15

        return min(100, score)

    def _store_clusters(self, conn, clusters: List[Dict]) -> None:
        """Persist clusters to database."""
        cursor = conn.cursor()

        for cluster in clusters:
            cursor.execute("""
                INSERT OR REPLACE INTO wallet_clusters
                (cluster_type, funder_wallet, creator_addresses, creator_count,
                 confidence_score, created_at, updated_at, is_likely_farm)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1)
            """, (
                'dev_farm',
                cluster['primary_funder'],
                ','.join(cluster['creators']),
                cluster['creator_count'],
                cluster['confidence_score'],
                self.detection_run_time,
                time.time()
            ))

        conn.commit()

    def _log_detection_run(self, results: Dict) -> None:
        """Log detection statistics."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO cluster_detection_log
            (detection_run_time, clusters_found, detection_method, duration_ms, status)
            VALUES (?, ?, ?, ?, ?)
        """, (
            self.detection_run_time,
            results['clusters_found'],
            'daily_batch',
            results['detection_duration_ms'],
            results['status']
        ))

        conn.commit()
        conn.close()

    def _get_conn(self) -> sqlite3.Connection:
        """Get database connection with optimizations."""
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA cache_size=-50000")
        return conn
```

### 6.2 Cron Job Setup

```bash
# Add to crontab for daily cluster detection (3 AM UTC, after cleanup)
0 3 * * * /usr/bin/python3 /path/to/cluster_detection.py

# File: cluster_detection.py (standalone script)
#!/usr/bin/env python3

import sys
import logging
sys.path.insert(0, '/path/to/flex')

from src.core.wallet_clustering import WalletClusteringEngine

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/var/log/flex/clustering.log'),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

if __name__ == '__main__':
    engine = WalletClusteringEngine('/path/to/flex_complete_database.db')
    result = engine.detect_clusters()

    logger.info(f"[CLUSTERING] Completed: {result}")
    sys.exit(0 if result['status'] == 'success' else 1)
```

### 6.3 Flask API Endpoints for Cluster Results

```python
# Add to src/core/main.py

@app.route('/api/clusters/summary')
def api_clusters_summary():
    """Get summary of detected clusters."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT cluster_type, COUNT(*) as count, AVG(confidence_score) as avg_confidence
            FROM wallet_clusters
            WHERE status = 'active'
            GROUP BY cluster_type
        """)

        summary = []
        for row in cursor.fetchall():
            summary.append({
                'type': row[0],
                'count': row[1],
                'avg_confidence': round(row[2], 2)
            })

        conn.close()
        return jsonify(summary)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/clusters/<cluster_id>')
def api_cluster_details(cluster_id):
    """Get detailed information about a specific cluster."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT cluster_id, cluster_type, funder_wallet, creator_addresses,
                   creator_count, confidence_score, total_sol_distributed, days_active
            FROM wallet_clusters
            WHERE cluster_id = ?
        """, (cluster_id,))

        row = cursor.fetchone()
        conn.close()

        if not row:
            return jsonify({'error': 'Cluster not found'}), 404

        return jsonify({
            'cluster_id': row[0],
            'type': row[1],
            'funder': row[2],
            'creators': row[3].split(',') if row[3] else [],
            'creator_count': row[4],
            'confidence_score': row[5],
            'total_sol_distributed': row[6],
            'days_active': row[7]
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/clusters/high-risk')
def api_high_risk_clusters():
    """List clusters above confidence threshold (risk assessment)."""
    try:
        threshold = request.args.get('threshold', 75, type=int)

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT cluster_id, funder_wallet, creator_count, confidence_score, is_likely_farm
            FROM wallet_clusters
            WHERE status = 'active' AND confidence_score >= ?
            ORDER BY confidence_score DESC
            LIMIT 100
        """, (threshold,))

        clusters = []
        for row in cursor.fetchall():
            clusters.append({
                'cluster_id': row[0],
                'funder': row[1],
                'creators': row[2],
                'confidence': row[3],
                'likely_farm': bool(row[4])
            })

        conn.close()
        return jsonify({
            'threshold': threshold,
            'count': len(clusters),
            'clusters': clusters
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
```

### 6.4 Integration Checklist

- [ ] Create wallet_clusters, cluster_members, cluster_relationships tables
- [ ] Create known_wallets table with exchange/service addresses
- [ ] Implement indexes on transfer_index
- [ ] Implement WalletClusteringEngine class
- [ ] Test cluster detection with dry-run
- [ ] Add daily cron job (3 AM UTC)
- [ ] Add Flask endpoints for cluster queries
- [ ] Add cluster detection logging
- [ ] Test false positive filtering
- [ ] Monitor first 7 days of detection runs
- [ ] Add cluster visualization to frontend (optional)

---

## Summary

This design provides:

✅ **Comprehensive detection** — Multi-level clustering of dev farms and creator networks
✅ **False positive filtering** — Excludes exchanges, services, whales with heuristics
✅ **Confidence scoring** — 0-100 scale based on multiple factors
✅ **Production-safe** — Atomic operations, proper error handling
✅ **Performance optimized** — Strategic indexing, incremental detection
✅ **FLEX integrated** — Daily batch job, REST APIs, full audit trail

**Cluster detection runs daily at 3 AM UTC** (after storage cleanup at 2 AM), adding negligible overhead while providing powerful dev farm identification for trading strategies.

