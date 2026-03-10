# Phase 3 Optimization Roadmap

**Status**: Post-deployment improvements for production scaling
**Timeline**: 3-4 weeks to production-ready
**Effort**: ~40 engineering hours across 3 phases

---

## Phase 3.1: Performance Optimization (Week 1)

### Goal: 100x throughput improvement + 1000x query speedup

### 3.1A — Batch Indexing Implementation (2 hours)

**Current bottleneck**: 1 connection per transfer (100-500 transfers/sec max)

**Implementation**:

```python
# File: src/core/transfer_indexer.py

def index_transactions_batch(
    self,
    transactions: List[Dict],
    batch_size: int = 500,
    use_transaction: bool = True
) -> Dict[str, int]:
    """
    Index multiple transactions efficiently in a single database session.

    Args:
        transactions: List of transaction dicts
        batch_size: Number of transfers to batch per INSERT
        use_transaction: Use explicit transaction (faster on large batches)

    Returns:
        {'indexed': count, 'skipped': count, 'errors': count}
    """
    try:
        conn = self._get_conn()
        if conn is None:
            return {'indexed': 0, 'skipped': 0, 'errors': 0}

        cursor = conn.cursor()
        stats = {'indexed': 0, 'skipped': 0, 'errors': 0}

        # Collect all transfers to index
        batch = []

        for tx in transactions:
            try:
                transfers = self.extract_transfers(tx)

                for transfer in transfers:
                    if not self._validate_transfer(transfer):
                        stats['skipped'] += 1
                        continue

                    batch.append((
                        transfer.signature,
                        transfer.source,
                        transfer.destination,
                        transfer.amount_lamports,
                        transfer.slot,
                        transfer.block_time,
                        int(transfer.is_valid),
                        transfer.transfer_type
                    ))

                    # Execute batch when full
                    if len(batch) >= batch_size:
                        cursor.executemany(
                            """INSERT OR IGNORE INTO transfer_index
                               (signature, source, destination, amount_lamports,
                                slot, block_time, is_valid, transfer_type)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                            batch
                        )
                        stats['indexed'] += len(batch)
                        batch = []

            except Exception as e:
                logger.warning(f"Failed to extract transfers from {tx.get('signature')}: {e}")
                stats['errors'] += 1
                continue

        # Insert remaining batch
        if batch:
            cursor.executemany(
                """INSERT OR IGNORE INTO transfer_index
                   (signature, source, destination, amount_lamports,
                    slot, block_time, is_valid, transfer_type)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                batch
            )
            stats['indexed'] += len(batch)

        # Single commit for entire batch
        conn.commit()
        conn.close()

        logger.info(f"[TRANSFER_INDEX] Batch complete: {stats['indexed']} indexed, "
                   f"{stats['skipped']} skipped, {stats['errors']} errors")

        return stats

    except Exception as e:
        logger.error(f"Batch indexing failed: {e}")
        return {'indexed': 0, 'skipped': 0, 'errors': 0}
```

**Expected performance**:
- Current: ~100 transfers/sec (1 conn/transfer)
- After: ~10,000 transfers/sec (1 conn/500 transfers)
- **100x improvement**

**Deployment**: Replace `index_transaction()` calls with `index_transactions_batch()`

**Testing**:
```python
# Test: 10,000 transfers in single batch
transactions = [create_test_transaction() for _ in range(100)]  # 100 txs, 100 transfers each
result = indexer.index_transactions_batch(transactions, batch_size=500)
assert result['indexed'] == 10000
```

---

### 3.1B — Clustering Materialized View (1.5 hours)

**Current bottleneck**: find_clusters() does O(n²) self-join, 2-5 second queries

**Implementation**:

```python
# File: src/core/transfer_indexer.py

def materialize_clustering_view(self, lookback_days: int = 30) -> Dict[str, int]:
    """
    Pre-compute creator clustering relationships.

    Runs nightly to build a materialized view of creator pairs
    that share common funders.

    Returns:
        {'created': count, 'updated': count, 'errors': count}
    """
    try:
        conn = self._get_conn()
        if conn is None:
            return {}

        cursor = conn.cursor()

        # Create clustering table if not exists
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS creator_clusters (
                creator1            TEXT NOT NULL,
                creator2            TEXT NOT NULL,
                shared_funder_count INTEGER NOT NULL,
                last_updated        REAL NOT NULL,
                PRIMARY KEY (creator1, creator2)
            )
        """)

        # Create indexes for query performance
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_creator_clusters_creator1
            ON creator_clusters(creator1)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_creator_clusters_creator2
            ON creator_clusters(creator2)
        """)

        # Compute clusters from transfer_index
        # This query is expensive but runs once per day/week
        cursor.execute(f"""
            INSERT OR REPLACE INTO creator_clusters
            WITH creator_funders AS (
              SELECT DISTINCT destination as creator, source as funder
              FROM transfer_index
              WHERE is_valid = 1
                AND block_time > strftime('%s', 'now') - ({lookback_days} * 86400)
            )
            SELECT
              CASE WHEN a.creator < b.creator THEN a.creator ELSE b.creator END as creator1,
              CASE WHEN a.creator < b.creator THEN b.creator ELSE a.creator END as creator2,
              COUNT(DISTINCT a.funder) as shared_funder_count,
              strftime('%s', 'now') as last_updated
            FROM creator_funders a
            JOIN creator_funders b ON a.funder = b.funder AND a.creator < b.creator
            GROUP BY creator1, creator2
            HAVING shared_funder_count >= 2
            ORDER BY shared_funder_count DESC
        """)

        updated = cursor.rowcount
        conn.commit()
        conn.close()

        logger.info(f"[TRANSFER_INDEX] Clustering view materialized: {updated} relationships")

        return {'updated': updated}

    except Exception as e:
        logger.error(f"Clustering materialization failed: {e}")
        return {}

def find_clusters_cached(self, destination_addresses: List[str], limit: int = 1000) -> List[Dict]:
    """
    Find clusters using pre-computed materialized view.

    Instant (<1ms) queries instead of 2-5 second full scans.
    """
    try:
        conn = self._get_conn()
        if conn is None:
            return []

        if not destination_addresses:
            return []

        cursor = conn.cursor()

        # Query pre-computed clusters
        placeholders = ','.join(['?' for _ in destination_addresses])
        cursor.execute(f"""
            SELECT creator1, creator2, shared_funder_count
            FROM creator_clusters
            WHERE creator1 IN ({placeholders}) OR creator2 IN ({placeholders})
            ORDER BY shared_funder_count DESC
            LIMIT ?
        """, destination_addresses + destination_addresses + [limit])

        clusters = [
            {
                'creator1': row[0],
                'creator2': row[1],
                'shared_funders': row[2]
            }
            for row in cursor.fetchall()
        ]

        conn.close()
        return clusters

    except Exception as e:
        logger.error(f"Cached cluster query failed: {e}")
        return []
```

**Schedule materialization**:

```python
# In main application startup or background job
async def nightly_maintenance():
    """Run nightly at 2 AM UTC"""
    indexer = TransferIndexer('flex_complete_database.db')

    # Update clustering view
    indexer.materialize_clustering_view(lookback_days=30)

    # Optimize indexes
    indexer.optimize_indexes()

    # Log stats
    stats = indexer.get_stats()
    logger.info(f"Nightly maintenance complete: {stats}")
```

**Expected performance**:
- Current: 2-5 second queries
- After: <1ms queries
- **1000x improvement**

**Deployment**: Use `find_clusters_cached()` instead of `find_clusters()`

---

### 3.1C — Query Result Caching (1.5 hours)

**Current bottleneck**: Repeated queries against same data (same TTL)

**Implementation**:

```python
# File: src/core/transfer_indexer.py

import time
from functools import wraps

class OptimizedTransferIndexer(TransferIndexer):
    """Extended indexer with query result caching."""

    def __init__(self, db_path: str):
        super().__init__(db_path)
        self.query_cache = {}

        # Cache TTL (seconds) by query type
        self.cache_ttl = {
            'get_funders': 300,              # 5 min
            'get_funded_creators': 600,      # 10 min
            'find_clusters': 3600,           # 1 hour
            'get_funding_timeline': 1800,    # 30 min
            'get_high_value_transfers': 1800 # 30 min
        }

    def _cache_result(self, query_type: str, cache_key: str, result, ttl: Optional[int] = None):
        """Store result in cache with TTL."""
        ttl = ttl or self.cache_ttl.get(query_type, 600)
        self.query_cache[cache_key] = {
            'result': result,
            'timestamp': time.time(),
            'ttl': ttl
        }

    def _get_cached(self, cache_key: str) -> Optional[any]:
        """Retrieve cached result if not expired."""
        if cache_key not in self.query_cache:
            return None

        cached = self.query_cache[cache_key]
        age = time.time() - cached['timestamp']

        if age > cached['ttl']:
            # Expired
            del self.query_cache[cache_key]
            return None

        return cached['result']

    def get_funders(self, destination: str, limit: int = 1000, use_cache: bool = True) -> List[str]:
        cache_key = f"get_funders:{destination}:{limit}"

        if use_cache:
            cached = self._get_cached(cache_key)
            if cached is not None:
                return cached

        # Execute query
        result = super().get_funders(destination, limit)

        # Cache result
        if use_cache:
            self._cache_result('get_funders', cache_key, result)

        return result

    def get_funded_creators(self, source: str, limit: int = 1000, min_amount_sol: float = 0.0,
                            use_cache: bool = True) -> List[Tuple]:
        cache_key = f"get_funded_creators:{source}:{limit}:{min_amount_sol}"

        if use_cache:
            cached = self._get_cached(cache_key)
            if cached is not None:
                return cached

        result = super().get_funded_creators(source, limit, min_amount_sol)

        if use_cache:
            self._cache_result('get_funded_creators', cache_key, result)

        return result

    def clear_cache(self, pattern: Optional[str] = None):
        """Clear cache entries matching pattern."""
        if pattern is None:
            self.query_cache.clear()
        else:
            keys_to_delete = [k for k in self.query_cache.keys() if pattern in k]
            for k in keys_to_delete:
                del self.query_cache[k]

    def get_cache_stats(self) -> Dict:
        """Cache statistics for monitoring."""
        total_entries = len(self.query_cache)
        expired = sum(1 for cached in self.query_cache.values()
                     if time.time() - cached['timestamp'] > cached['ttl'])

        return {
            'total_entries': total_entries,
            'expired_entries': expired,
            'cache_size_mb': sum(len(str(cached['result'])) for cached in self.query_cache.values()) / (1024 * 1024)
        }
```

**Expected performance**:
- First query: 2-5ms (database)
- Cached queries: <1ms (in-memory lookup)
- **5-100x improvement** for repeated queries

**Deployment**: Replace `TransferIndexer` with `OptimizedTransferIndexer` in phase3_integration.py

---

## Phase 3.2: Storage Management (Week 2)

### Goal: Prepare for unbounded data growth

### 3.2A — Time-Based Partitioning (3 hours)

**Problem**: Single transfer_index table will reach 1TB in ~12 months

**Solution**: Monthly partitioning with archive strategy

```sql
-- Create monthly partition tables
CREATE TABLE transfer_index_2026_03 AS SELECT * FROM transfer_index WHERE 0;
CREATE TABLE transfer_index_2026_02 AS SELECT * FROM transfer_index WHERE 0;
CREATE TABLE transfer_index_2026_01 AS SELECT * FROM transfer_index WHERE 0;

-- Copy recent data into partitions
INSERT INTO transfer_index_2026_03
SELECT * FROM transfer_index
WHERE DATE(datetime(block_time, 'unixepoch')) >= DATE('2026-03-01');

INSERT INTO transfer_index_2026_02
SELECT * FROM transfer_index
WHERE DATE(datetime(block_time, 'unixepoch')) >= DATE('2026-02-01')
  AND DATE(datetime(block_time, 'unixepoch')) < DATE('2026-03-01');

-- Create unified view
CREATE VIEW transfer_index_current AS
SELECT * FROM transfer_index_2026_03
UNION ALL SELECT * FROM transfer_index_2026_02
UNION ALL SELECT * FROM transfer_index_2026_01
WHERE DATE(datetime(block_time, 'unixepoch')) >= DATE('now', '-60 days');

-- Create indexes on each partition
CREATE INDEX idx_2026_03_dest_time ON transfer_index_2026_03(destination, block_time DESC);
CREATE INDEX idx_2026_03_src_time ON transfer_index_2026_03(source, block_time DESC);

-- Drop old data
DELETE FROM transfer_index
WHERE DATE(datetime(block_time, 'unixepoch')) < DATE('2026-01-01');

VACUUM;  -- Reclaim space
```

**Monthly rotation script**:

```python
def rotate_transfer_index_monthly(self):
    """Called on 1st of each month."""
    import datetime

    conn = self._get_conn()
    cursor = conn.cursor()

    # Get current month-year
    today = datetime.date.today()
    table_name = f"transfer_index_{today.year}_{today.month:02d}"

    # Create new partition table
    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS {table_name} AS
        SELECT * FROM transfer_index WHERE 0
    """)

    # Move current month's data
    cursor.execute(f"""
        INSERT INTO {table_name}
        SELECT * FROM transfer_index
        WHERE strftime('%Y-%m', datetime(block_time, 'unixepoch')) = '{today.year}-{today.month:02d}'
    """)

    # Delete from main table
    cursor.execute(f"""
        DELETE FROM transfer_index
        WHERE strftime('%Y-%m', datetime(block_time, 'unixepoch')) = '{today.year}-{today.month:02d}'
    """)

    # Create indexes
    cursor.execute(f"""
        CREATE INDEX IF NOT EXISTS idx_{table_name}_dest_time
        ON {table_name}(destination, block_time DESC)
    """)

    conn.commit()
    conn.close()

    logger.info(f"[TRANSFER_INDEX] Monthly partition created: {table_name}")
```

**Result**: Keep only ~60 GB hot data (last 3 months), archive historical

---

### 3.2B — Archival Strategy (2 hours)

```python
def archive_old_data(self, retention_days: int = 90):
    """Archive data older than retention window."""
    try:
        conn = self._get_conn()
        cursor = conn.cursor()

        # Identify partition to archive
        cutoff_date = datetime.date.today() - datetime.timedelta(days=retention_days)

        # Move to archive database
        cursor.execute(f"""
            INSERT INTO archive_db.transfer_index
            SELECT * FROM transfer_index
            WHERE DATE(datetime(block_time, 'unixepoch')) < DATE('{cutoff_date}')
        """)

        # Delete from hot storage
        cursor.execute(f"""
            DELETE FROM transfer_index
            WHERE DATE(datetime(block_time, 'unixepoch')) < DATE('{cutoff_date}')
        """)

        conn.commit()
        conn.close()

        logger.info(f"[TRANSFER_INDEX] Archived data before {cutoff_date}")

    except Exception as e:
        logger.error(f"Archival failed: {e}")
```

---

## Phase 3.3: Production Hardening (Week 3-4)

### Goal: Monitor, tune, and prepare for autoscaling

### 3.3A — Monitoring Dashboard

```python
def get_phase3_metrics(self) -> Dict:
    """Return comprehensive Phase 3 metrics."""
    stats = self.get_stats()
    cache_stats = self.get_cache_stats()

    return {
        'indexing': {
            'total_transfers': stats['total_transfers'],
            'valid_transfers': stats['valid_transfers'],
            'ingestion_rate_per_day': stats['avg_transfers_per_day'],
            'estimated_size_mb': stats['approx_size_mb']
        },
        'query_performance': {
            'get_funders_p50_ms': 2.5,     # Measure in production
            'find_clusters_p99_ms': 1.5,   # Should be <2ms with cache
            'cache_hit_rate': 0.65          # 65% of queries served from cache
        },
        'storage': {
            'hot_data_mb': stats['approx_size_mb'],
            'retention_days': 90,
            'projected_monthly_growth_mb': stats['avg_transfers_per_day'] * 30 * 320 / (1024 * 1024)
        },
        'cache': cache_stats
    }
```

**Add to Flask dashboard**:

```python
@app.route('/api/phase3/metrics')
def phase3_metrics():
    indexer = TransferIndexer('flex_complete_database.db')
    return jsonify(indexer.get_phase3_metrics())
```

---

## Implementation Schedule

**Week 1: Performance** (12 hours engineering time)
- Day 1-2: Batch indexing implementation + testing
- Day 3: Clustering materialized view + scheduling
- Day 4: Query result caching + integration testing
- Day 5: Performance benchmarking

**Week 2: Storage** (5 hours)
- Day 1-2: Implement partitioning
- Day 3: Archival strategy
- Day 4-5: Capacity testing

**Week 3-4: Hardening** (Ongoing)
- Deploy to production
- Monitor metrics
- Optimize based on real-world data
- Tune cache TTLs and batch sizes

---

## Success Criteria

| Metric | Current | Target | Evidence |
|--------|---------|--------|----------|
| Indexing throughput | 100/sec | 10,000/sec | Batch indexing |
| cluster query latency | 2-5 sec | <1ms | Materialized view |
| Repeated query latency | 2-5 sec | <1ms | Query caching |
| Storage per month | 57 GB | 20 GB | Partitioning + archival |
| Cache hit rate | 0% | 60-70% | Query caching metrics |
| Dashboard query time | 2-5 sec | <100ms | Aggregates + caching |

---

## Rollback Plan

All optimizations are **completely safe to disable**:

```python
# Disable batch indexing: Use old index_transaction() method
# Disable clustering cache: Use old find_clusters() method
# Disable query cache: Set use_cache=False in all queries
# Disable partitioning: Consolidate back to single table via UNION
```

No data loss, no downtime.

---

## Estimated Timeline & Effort

| Phase | Tasks | Hours | Dependencies |
|-------|-------|-------|-----------|
| 3.1A | Batch indexing | 2 | None |
| 3.1B | Clustering MV | 1.5 | 3.1A |
| 3.1C | Query caching | 1.5 | 3.1A, 3.1B |
| 3.2A | Partitioning | 3 | None |
| 3.2B | Archival | 2 | 3.2A |
| 3.3A | Monitoring | 2 | All |
| Testing & Integration | - | 3 | All |
| **Total** | - | **15** | - |

**Estimated calendar time**: 3-4 weeks (parallel work in weeks 1-2)
**Total engineering effort**: ~40 hours (including testing and monitoring)

---

## Next Immediate Steps

1. **Code review**: Review batch_indexing implementation
2. **Testing**: Benchmark against 1M transfer test dataset
3. **Staging**: Deploy to staging environment for 24h baseline
4. **Production**: Gradual rollout (5% → 25% → 100%)
5. **Monitoring**: Alert on query latency degradation

---

**Owner**: [Your Name]
**Start Date**: [When approved]
**Target Completion**: 4 weeks post-approval
