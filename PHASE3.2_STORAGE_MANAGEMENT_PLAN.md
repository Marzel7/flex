# Phase 3.2: Storage Management Implementation Plan

**Status**: Planning
**Timeline**: ~5 hours engineering time
**Date**: March 10, 2026

---

## Executive Summary

Phase 3.2 addresses storage growth by implementing time-based partitioning and archival strategy. The transfer_index table will grow ~57 GB/month unbounded, reaching 1.1 TB in 12 months. This phase keeps only 60 GB of hot data (last 3 months) while maintaining 90-day retention for analysis.

---

## Problem Statement

### Current State
- **Single table**: All transfers in `transfer_index`
- **Growth rate**: ~57 GB/month
- **Storage projection**: 1.1 TB in 12 months
- **Retention window**: Unlimited (no cleanup)
- **Query impact**: All indexing logic queries full table

### Impact
- Database file grows unbounded
- Initial index build slower as table grows
- Queries scan more data unnecessarily
- No archival or historical data separation
- Difficult to manage retention policies

---

## Solution Overview

### Phase 3.2A: Time-Based Partitioning (3 hours)

Create monthly partition tables to segregate data by month:
- `transfer_index_2026_03` (March 2026)
- `transfer_index_2026_02` (February 2026)
- `transfer_index_2026_01` (January 2026)
- etc.

**Benefits**:
- Faster initial indexing (smaller per-table scans)
- Easier archival (drop entire month vs. time-range DELETE)
- Smaller indexes per partition
- Easier to manage storage growth

### Phase 3.2B: Archival Strategy (2 hours)

Implement 90-day rolling retention window:
- **Hot data**: Last 3 months (queries include)
- **Warm data**: 3-12 months (archived to separate DB)
- **Cold data**: >12 months (delete)

**Benefits**:
- Bounded hot storage (~60 GB)
- Historical data preserved for compliance
- Query performance stable
- Cost predictable

---

## Implementation Details

### Step 1: Add Partitioning Methods to TransferIndexer

**File**: `src/core/transfer_indexer.py`

Add three new methods to the `TransferIndexer` class:

```python
def rotate_transfer_index_monthly(self, year: int, month: int) -> Dict[str, int]:
    """
    Rotate current month's data into partition table (called on 1st of next month).

    Args:
        year: Year of partition (e.g., 2026)
        month: Month of partition (e.g., 3)

    Returns:
        {'created': bool, 'inserted': count, 'deleted': count}
    """
    try:
        conn = self._get_conn()
        if conn is None:
            return {'created': False, 'inserted': 0, 'deleted': 0}

        cursor = conn.cursor()
        table_name = f"transfer_index_{year}_{month:02d}"

        # 1. Create empty partition table with same schema
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {table_name} (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                signature           TEXT NOT NULL UNIQUE,
                source              TEXT NOT NULL,
                destination         TEXT NOT NULL,
                amount_lamports      INTEGER NOT NULL,
                amount_sol           REAL GENERATED ALWAYS AS (amount_lamports / 1e9) STORED,
                slot                INTEGER NOT NULL,
                block_time          INTEGER NOT NULL,
                indexed_at          REAL NOT NULL,
                is_valid            BOOLEAN NOT NULL DEFAULT 1,
                transfer_type       TEXT DEFAULT 'standard',
                CHECK (amount_lamports > 0),
                CHECK (block_time > 0)
            )
        """)

        # 2. Copy data for this month from main table
        month_start = f"{year}-{month:02d}-01"
        if month == 12:
            month_end = f"{year+1}-01-01"
        else:
            month_end = f"{year}-{month+1:02d}-01"

        cursor.execute(f"""
            INSERT INTO {table_name}
            (signature, source, destination, amount_lamports, slot, block_time, indexed_at, is_valid, transfer_type)
            SELECT signature, source, destination, amount_lamports, slot, block_time, indexed_at, is_valid, transfer_type
            FROM transfer_index
            WHERE DATE(datetime(block_time, 'unixepoch')) >= '{month_start}'
            AND DATE(datetime(block_time, 'unixepoch')) < '{month_end}'
        """)
        inserted = cursor.rowcount

        # 3. Create indexes on partition (same as main table)
        cursor.execute(f"""CREATE INDEX IF NOT EXISTS idx_{table_name}_dest_time
                          ON {table_name}(destination, block_time DESC)""")
        cursor.execute(f"""CREATE INDEX IF NOT EXISTS idx_{table_name}_src_time
                          ON {table_name}(source, block_time DESC)""")
        cursor.execute(f"""CREATE INDEX IF NOT EXISTS idx_{table_name}_block_time
                          ON {table_name}(block_time DESC)""")

        # 4. Delete from main table
        cursor.execute(f"""
            DELETE FROM transfer_index
            WHERE DATE(datetime(block_time, 'unixepoch')) >= '{month_start}'
            AND DATE(datetime(block_time, 'unixepoch')) < '{month_end}'
        """)
        deleted = cursor.rowcount

        conn.commit()
        conn.close()

        logger.info(f"[TRANSFER_INDEX] Rotated {month_start}: created {inserted} rows, deleted {deleted}")
        return {'created': True, 'inserted': inserted, 'deleted': deleted}

    except Exception as e:
        logger.error(f"[TRANSFER_INDEX] Rotation failed: {e}")
        return {'created': False, 'inserted': 0, 'deleted': 0}


def get_transfer_index_partitions(self) -> List[str]:
    """
    List all active partition tables.

    Returns:
        ['transfer_index_2026_03', 'transfer_index_2026_02', ...]
    """
    try:
        conn = self._get_conn()
        if conn is None:
            return []

        cursor = conn.cursor()
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name LIKE 'transfer_index_%'
            ORDER BY name DESC
        """)
        tables = [row[0] for row in cursor.fetchall()]
        conn.close()
        return tables

    except Exception as e:
        logger.error(f"[TRANSFER_INDEX] Failed to list partitions: {e}")
        return []


def get_query_union_view(self, lookback_days: int = 90) -> str:
    """
    Build UNION ALL query for recent partitions + main table.

    Args:
        lookback_days: Number of days to include (default 90)

    Returns:
        SQL query string for union of recent partitions
    """
    try:
        import datetime

        # Get list of all partitions
        partitions = self.get_transfer_index_partitions()

        # Calculate cutoff date
        cutoff = datetime.datetime.now() - datetime.timedelta(days=lookback_days)
        cutoff_str = cutoff.strftime("%Y-%m-%d")

        # Build WHERE clause for each partition
        queries = []

        # Always include main table (current month data)
        queries.append(f"SELECT * FROM transfer_index WHERE block_time >= {int(cutoff.timestamp())}")

        # Include partitions within lookback window
        for partition in partitions:
            # Extract year-month from partition name (transfer_index_2026_03 -> 2026-03)
            parts = partition.split('_')
            if len(parts) >= 3:
                try:
                    year = int(parts[2])
                    month = int(parts[3])
                    part_date = datetime.datetime(year, month, 1)

                    if part_date >= cutoff:
                        queries.append(f"SELECT * FROM {partition} WHERE block_time >= {int(cutoff.timestamp())}")
                except (ValueError, IndexError):
                    continue

        return " UNION ALL ".join(queries) if queries else "SELECT * FROM transfer_index LIMIT 0"

    except Exception as e:
        logger.error(f"[TRANSFER_INDEX] Failed to build union view: {e}")
        return "SELECT * FROM transfer_index LIMIT 0"
```

---

### Step 2: Update Query Methods to Use Partitions

**File**: `src/core/transfer_indexer.py`

Update existing query methods to use the partition union query:

```python
def get_funders(self, destination: str, days: int = 90) -> List[Dict]:
    """
    Get all funders for a destination (updated to use partitions).
    """
    try:
        conn = self._get_conn()
        if conn is None:
            return []

        # Use union view instead of single table
        union_query = self.get_query_union_view(lookback_days=days)
        query = f"""
            SELECT source, destination, COUNT(*) as count, SUM(amount_sol) as total_sol
            FROM ({union_query})
            WHERE destination = ?
            GROUP BY source
            ORDER BY total_sol DESC
        """

        cursor = conn.cursor()
        cursor.execute(query, (destination,))
        results = cursor.fetchall()
        conn.close()

        return [{'funder': r[0], 'destination': r[1], 'count': r[2], 'total_sol': r[3]}
                for r in results]

    except Exception as e:
        logger.error(f"[TRANSFER_INDEX] Query failed: {e}")
        return []
```

---

### Step 3: Create Archival Job

**File**: `src/core/transfer_archiver.py` (new file)

```python
"""
Archive old partitions to separate database (warm storage).
"""

import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Dict

logger = logging.getLogger(__name__)


class TransferArchiver:
    """Archive transfer_index partitions older than retention window."""

    def __init__(self, hot_db_path: str, archive_db_path: str, retention_days: int = 90):
        self.hot_db = hot_db_path
        self.archive_db = archive_db_path
        self.retention_days = retention_days

    def archive_old_partitions(self) -> Dict[str, int]:
        """
        Move partitions older than retention_days to archive database.

        Returns:
            {'archived': count, 'deleted': count}
        """
        try:
            cutoff = datetime.now() - timedelta(days=self.retention_days)

            # Get all partitions from hot DB
            hot_conn = sqlite3.connect(self.hot_db)
            cursor = hot_conn.cursor()
            cursor.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name LIKE 'transfer_index_%'
            """)
            partitions = [row[0] for row in cursor.fetchall()]
            hot_conn.close()

            archived = 0
            deleted = 0

            # Ensure archive DB exists
            archive_conn = sqlite3.connect(self.archive_db)
            archive_cursor = archive_conn.cursor()
            archive_cursor.execute("""
                CREATE TABLE IF NOT EXISTS transfer_index_archive (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    signature           TEXT NOT NULL UNIQUE,
                    source              TEXT NOT NULL,
                    destination         TEXT NOT NULL,
                    amount_lamports      INTEGER NOT NULL,
                    amount_sol           REAL GENERATED ALWAYS AS (amount_lamports / 1e9) STORED,
                    slot                INTEGER NOT NULL,
                    block_time          INTEGER NOT NULL,
                    indexed_at          REAL NOT NULL,
                    is_valid            BOOLEAN NOT NULL DEFAULT 1,
                    transfer_type       TEXT DEFAULT 'standard'
                )
            """)
            archive_conn.commit()
            archive_conn.close()

            # Archive each old partition
            for partition in partitions:
                try:
                    # Extract date from partition name
                    parts = partition.split('_')
                    year = int(parts[2])
                    month = int(parts[3])
                    partition_date = datetime(year, month, 1)

                    if partition_date < cutoff:
                        # Copy to archive DB
                        hot_conn = sqlite3.connect(self.hot_db)
                        hot_cursor = hot_conn.cursor()
                        hot_cursor.execute(f"SELECT * FROM {partition}")
                        rows = hot_cursor.fetchall()
                        hot_conn.close()

                        if rows:
                            archive_conn = sqlite3.connect(self.archive_db)
                            archive_cursor = archive_conn.cursor()
                            archive_cursor.executemany(
                                """INSERT OR IGNORE INTO transfer_index_archive
                                   (signature, source, destination, amount_lamports, slot, block_time, indexed_at, is_valid, transfer_type)
                                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                                rows
                            )
                            archive_conn.commit()
                            archive_conn.close()
                            archived += len(rows)

                        # Delete from hot DB
                        hot_conn = sqlite3.connect(self.hot_db)
                        hot_cursor = hot_conn.cursor()
                        hot_cursor.execute(f"DROP TABLE {partition}")
                        hot_conn.commit()
                        hot_conn.close()
                        deleted += 1

                        logger.info(f"[ARCHIVER] Archived {partition}: {len(rows)} rows")

                except (ValueError, IndexError):
                    logger.warning(f"[ARCHIVER] Could not parse partition name: {partition}")

            return {'archived': archived, 'deleted': deleted}

        except Exception as e:
            logger.error(f"[ARCHIVER] Archival failed: {e}")
            return {'archived': 0, 'deleted': 0}
```

---

### Step 4: Create Migration SQL

**File**: `database/migrations/phase3_2_storage_partitioning.sql`

```sql
-- FLEX V2 Phase 3.2: Storage Management
-- Partitions existing transfer_index into monthly tables
-- Safe to run (uses CREATE TABLE IF NOT EXISTS)

-- Optional: Create metadata table for partition tracking
CREATE TABLE IF NOT EXISTS transfer_index_metadata (
    partition_name TEXT PRIMARY KEY,
    partition_month TEXT NOT NULL,
    row_count INTEGER,
    size_mb REAL,
    created_at REAL,
    archived_at REAL
);

-- Indexes for metadata
CREATE INDEX IF NOT EXISTS idx_partition_month ON transfer_index_metadata(partition_month);
CREATE INDEX IF NOT EXISTS idx_partition_archived ON transfer_index_metadata(archived_at);

-- NOTE: Monthly partition tables (transfer_index_2026_03, etc.) are created
-- dynamically by Python code in rotate_transfer_index_monthly()
-- This SQL provides the infrastructure; partitions are managed by the application.
```

---

### Step 5: Update Flask Dashboard

**File**: `main.py` (add new endpoint)

```python
@app.route('/api/phase3/storage')
def phase3_storage_metrics():
    """Get Phase 3.2 storage metrics."""
    try:
        from src.core.transfer_indexer import TransferIndexer
        indexer = TransferIndexer(DB_PATH)

        partitions = indexer.get_transfer_index_partitions()
        stats = indexer.get_stats()

        return jsonify({
            'partitions': {
                'count': len(partitions),
                'list': partitions
            },
            'storage': {
                'hot_transfers': stats.get('total_transfers', 0),
                'hot_size_mb': stats.get('approx_size_mb', 0),
                'monthly_growth_mb': 57,  # Approximate
                'retention_days': 90
            },
            'projection': {
                'months_until_full': 60 / 57,  # Assuming 60 GB hot limit
                'archive_size_mb': 0  # TODO: Query archive DB
            }
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
```

---

## Deployment Steps

### Pre-Deployment
1. [ ] Backup existing `flex_complete_database.db`
2. [ ] Run migration SQL (creates metadata table)
3. [ ] Verify no existing `transfer_index_*` tables
4. [ ] Update `src/core/transfer_indexer.py` with new methods
5. [ ] Create `src/core/transfer_archiver.py`
6. [ ] Update `main.py` with new endpoint

### Migration (One-Time)
1. [ ] Stop indexing processes
2. [ ] Run `indexer.rotate_transfer_index_monthly(2026, 3)` for current month
3. [ ] Run `indexer.rotate_transfer_index_monthly(2026, 2)` for previous months
4. [ ] Verify data integrity (row counts match)
5. [ ] Verify indexes created
6. [ ] Restart indexing

### Operational (Monthly)
1. [ ] Create cron job on 1st of each month:
   ```bash
   0 2 1 * * python3 /path/to/rotate_partition.py
   ```
2. [ ] Create weekly archival job:
   ```bash
   0 3 * * 0 python3 /path/to/archive_old_partitions.py
   ```
3. [ ] Monitor storage growth via `/api/phase3/storage`
4. [ ] Alert if hot storage exceeds 70 GB

---

## Testing

### Unit Tests

```python
def test_partition_creation():
    indexer = TransferIndexer(':memory:')
    result = indexer.rotate_transfer_index_monthly(2026, 3)
    assert result['created'] == True
    assert result['inserted'] >= 0

def test_partition_list():
    indexer = TransferIndexer(':memory:')
    partitions = indexer.get_transfer_index_partitions()
    assert isinstance(partitions, list)

def test_union_query():
    indexer = TransferIndexer(':memory:')
    union = indexer.get_query_union_view(lookback_days=90)
    assert 'UNION ALL' in union or 'SELECT' in union
```

### Integration Tests

1. Create test transfers across 3 months
2. Rotate each month into partition
3. Query across partitions
4. Verify query results match pre-partition queries
5. Archive oldest partition
6. Verify query still works

---

## Rollback Plan

**If partitioning fails**:
1. Stop indexing
2. Restore from backup
3. Continue with Phase 3.1 only (no partitioning)
4. Proceed to Phase 3.3 (monitoring without partitioning)

**If archival causes data loss**:
1. Restore archive database from backup
2. Restore hot database from backup
3. Disable archival, keep partitions in hot DB
4. Proceed with limited storage growth

---

## Success Criteria

| Metric | Target | Evidence |
|--------|--------|----------|
| Hot storage bounded | <70 GB | Storage metrics endpoint |
| Query performance stable | <5ms | Phase 3.3 monitoring |
| No data loss | 100% | Row count verification |
| Archival working | 90-day window | Archive DB populated |
| Partition rotation automated | Monthly | Cron job logs |

---

## Next Steps

1. **Implement**: Add methods to TransferIndexer class
2. **Test**: Run unit and integration tests
3. **Deploy**: One-time migration of current data
4. **Monitor**: Track storage metrics
5. **Tune**: Adjust retention window based on query patterns
6. **Archive**: Weekly archival to warm storage

After Phase 3.2 completion, proceed to **Phase 3.3: Production Hardening** (monitoring dashboard and autoscaling).

---

## References

- **Roadmap**: PHASE3_OPTIMIZATION_ROADMAP.md
- **Architecture Review**: PHASE3_TECHNICAL_ARCHITECTURE_REVIEW.md
- **Status**: PHASE3_OPTIMIZATION_IMPLEMENTATION_STATUS.md

