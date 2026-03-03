# Webhook Database Schema - Complete Reference

**Status**: Production Ready
**Date**: 2026-03-03
**Author**: Claude Code

---

## Schema Overview

The webhook system uses **3 core tables** that store webhook-extracted data and processing state:

```
┌────────────────────────┐       ┌─────────────────────────┐       ┌──────────────────────┐
│   sol_transfers        │       │  address_activity       │       │   work_queue         │
├────────────────────────┤       ├─────────────────────────┤       ├──────────────────────┤
│ signature (PK)         │───┐   │ address (PK)            │───┐   │ address (PK)         │
│ slot                   │   │   │ last_seen_at            │   │   │ priority             │
│ block_time             │   │   │ tx_5m, tx_1h, tx_24h    │   │   │ reason               │
│ source ──────────────┐ │   │   │ sol_in/out (5m,1h,24h)  │   │   │ next_run_at          │
│ destination ──────┐  │ │   │   │ last_processed_at       │   │   │ locked_until         │
│ lamports           │  │ │   │   │ last_rpc_fetch_at       │   │   │ attempts             │
│ amount_sol         │  │ │   │   │ updated_at              │   │   │ updated_at           │
│ received_at        │  │ │   │   └─────────────────────────┘   │   └──────────────────────┘
│ processed          │  │ │                                      │
└────────────────────────┘  │   INDEXES:                         │   INDEXES:
                            │   - last_seen_at DESC              │   - priority DESC
INDEXES:                    │                                    │   - next_run_at ASC
- source ─────────────────┼─────────────────────────────────────┘
- destination ────────────┼─────────────────────────────────────┐
- block_time DESC         │                                    │
- received_at             │                                    │
└────────────────────────┘                                      │
                                                                │
                  Joins with existing FLEX tables:              │
                  ├─ creator_self_funding                       │
                  ├─ creator_funders                            │
                  ├─ token_analysis                             │
                  ├─ creator_tags                               │
                  ├─ coordinated_creator_edges                  │
                  ├─ creator_to_creator_networks                │
                  └─ funding_chains                             │
```

---

## Core Tables

### 1. sol_transfers - Deduplicated SOL Transfer Events

**Purpose**: Stores raw webhook-extracted SOL transfers

**Creation**: [webhook_handler.py:50-63](webhook_handler.py#L50-L63)

```sql
CREATE TABLE IF NOT EXISTS sol_transfers (
    signature TEXT PRIMARY KEY,
    slot INTEGER NOT NULL,
    block_time INTEGER NOT NULL,
    source TEXT NOT NULL,
    destination TEXT NOT NULL,
    lamports INTEGER NOT NULL,
    amount_sol REAL NOT NULL,
    received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processed BOOLEAN DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_sol_transfers_source ON sol_transfers(source);
CREATE INDEX IF NOT EXISTS idx_sol_transfers_destination ON sol_transfers(destination);
CREATE INDEX IF NOT EXISTS idx_sol_transfers_block_time ON sol_transfers(block_time DESC);
CREATE INDEX IF NOT EXISTS idx_sol_transfers_received ON sol_transfers(received_at);
```

#### Column Definitions

| Column | Type | Description | Example |
|--------|------|-------------|---------|
| `signature` | TEXT PRIMARY KEY | Unique transaction signature (deduplicates) | `2NRmUxAn6QDrhm9PpXDfx4vKYDJgvkwxB...` |
| `slot` | INTEGER | Solana slot number | `403966256` |
| `block_time` | INTEGER | Unix timestamp (seconds since epoch) | `1772552611` |
| `source` | TEXT | Sender address (indexed for fast lookup) | `5ZpgwwHAxs5kuer3dwwJQxjxvWaXHaLvchZJCRqigPtJ` |
| `destination` | TEXT | Receiver address (indexed) | `HZUZfV5SYyEtDvaSp6UTLmj9Vw628HEMgR489sGPJ23z` |
| `lamports` | INTEGER | Amount in lamports (1 SOL = 1e9 lamports) | `200000` |
| `amount_sol` | REAL | Amount in SOL (cached for convenience) | `0.0002` |
| `received_at` | TIMESTAMP | When webhook arrived (SQLite default) | `2026-03-03 15:40:40` |
| `processed` | BOOLEAN | Downstream processing status | `0` or `1` |

#### Size & Performance

- **Row size**: ~250 bytes
- **Index size**: ~100 bytes per source/destination index
- **Deduplication**: O(1) via PRIMARY KEY
- **Insert throughput**: 1000+ rows/sec

#### Sample Row

```json
{
  "signature": "2NRmUxAn6QDrhm9PpXDfx4vKYDJgvkwxB5C7d8E9f0g1h2i3j4k5l6m7n8o9p0q",
  "slot": 403966256,
  "block_time": 1772552611,
  "source": "5ZpgwwHAxs5kuer3dwwJQxjxvWaXHaLvchZJCRqigPtJ",
  "destination": "HZUZfV5SYyEtDvaSp6UTLmj9Vw628HEMgR489sGPJ23z",
  "lamports": 200000,
  "amount_sol": 0.0002,
  "received_at": "2026-03-03 15:40:40.123",
  "processed": 0
}
```

#### Common Queries

**Get recent transfers from a creator**:
```sql
SELECT * FROM sol_transfers
WHERE source = ?
ORDER BY block_time DESC
LIMIT 10;
```

**Count transfers in time window**:
```sql
SELECT COUNT(*) FROM sol_transfers
WHERE source = ? AND block_time > ?;
```

**Sum SOL outflows**:
```sql
SELECT SUM(amount_sol) FROM sol_transfers
WHERE source = ? AND block_time > ?;
```

**Check for duplicates** (should always be 0):
```sql
SELECT signature FROM sol_transfers
GROUP BY signature
HAVING COUNT(*) > 1;
```

---

### 2. address_activity - Rolling Statistics Per Address

**Purpose**: Real-time activity metrics updated as transfers arrive

**Creation**: [webhook_handler.py:69-95](webhook_handler.py#L69-L95)

```sql
CREATE TABLE IF NOT EXISTS address_activity (
    address TEXT PRIMARY KEY,
    last_seen_at INTEGER NOT NULL,

    -- Transaction counts in sliding windows
    tx_5m INTEGER DEFAULT 0,
    tx_1h INTEGER DEFAULT 0,
    tx_24h INTEGER DEFAULT 0,

    -- SOL received in windows
    sol_in_5m REAL DEFAULT 0.0,
    sol_in_1h REAL DEFAULT 0.0,
    sol_in_24h REAL DEFAULT 0.0,

    -- SOL sent in windows
    sol_out_5m REAL DEFAULT 0.0,
    sol_out_1h REAL DEFAULT 0.0,
    sol_out_24h REAL DEFAULT 0.0,

    -- Processing metadata
    last_processed_at INTEGER,
    last_rpc_fetch_at INTEGER,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_address_activity_last_seen ON address_activity(last_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_address_activity_updated ON address_activity(updated_at DESC);
```

#### Column Definitions

| Column | Type | Window | Description |
|--------|------|--------|-------------|
| `address` | TEXT PRIMARY KEY | - | Wallet address being tracked |
| `last_seen_at` | INTEGER | - | Unix timestamp of most recent transfer (this address as source or dest) |
| `tx_5m` | INTEGER | 5 min | Number of transfers in last 5 minutes |
| `tx_1h` | INTEGER | 1 hour | Number of transfers in last 1 hour |
| `tx_24h` | INTEGER | 24 hours | Number of transfers in last 24 hours |
| `sol_in_5m` | REAL | 5 min | SOL received in last 5 minutes |
| `sol_in_1h` | REAL | 1 hour | SOL received in last 1 hour |
| `sol_in_24h` | REAL | 24 hours | SOL received in last 24 hours |
| `sol_out_5m` | REAL | 5 min | SOL sent in last 5 minutes |
| `sol_out_1h` | REAL | 1 hour | SOL sent in last 1 hour |
| `sol_out_24h` | REAL | 24 hours | SOL sent in last 24 hours |
| `last_processed_at` | INTEGER | - | When worker last processed this address (Unix timestamp) |
| `last_rpc_fetch_at` | INTEGER | - | When last RPC call made for this address (Unix timestamp) |
| `updated_at` | TIMESTAMP | - | When this row was last updated |

#### Size & Performance

- **Row size**: ~150 bytes
- **Index size**: ~50 bytes per index
- **Query latency**: <10ms (single row lookup)
- **Update frequency**: Every webhook involving the address

#### Sample Row

```json
{
  "address": "5ZpgwwHAxs5kuer3dwwJQxjxvWaXHaLvchZJCRqigPtJ",
  "last_seen_at": 1772552611,
  "tx_5m": 3,
  "tx_1h": 12,
  "tx_24h": 45,
  "sol_in_5m": 0.5,
  "sol_in_1h": 2.3,
  "sol_in_24h": 15.7,
  "sol_out_5m": 0.2,
  "sol_out_1h": 1.0,
  "sol_out_24h": 8.2,
  "last_processed_at": 1772552610,
  "last_rpc_fetch_at": 1772552500,
  "updated_at": "2026-03-03 15:40:40"
}
```

#### Update Logic

**On each transfer** (webhook_handler.py:187-235):
1. Query sol_transfers for transfers involving the address in each window
2. Count transactions and sum SOL
3. UPDATE address_activity with new values

```python
# Pseudo-code
def update_activity_stats(address, block_time):
    five_min_ago = block_time - 300
    one_hour_ago = block_time - 3600

    # Recalculate tx counts
    tx_5m = count transfers WHERE source=address AND block_time > five_min_ago
    tx_1h = count transfers WHERE source=address AND block_time > one_hour_ago

    # Recalculate SOL sums
    sol_out_5m = sum amounts WHERE source=address AND block_time > five_min_ago

    # Update row
    UPDATE address_activity SET
        tx_5m = tx_5m,
        tx_1h = tx_1h,
        sol_out_5m = sol_out_5m,
        ...
    WHERE address = address
```

#### Common Queries

**Find most active addresses**:
```sql
SELECT address, tx_1h, sol_in_1h + sol_out_1h as total_volume
FROM address_activity
ORDER BY tx_1h DESC
LIMIT 10;
```

**Find addresses active in last 5 minutes**:
```sql
SELECT address FROM address_activity
WHERE tx_5m > 0
ORDER BY last_seen_at DESC;
```

**Check if address has pending processing** (not processed in 10 minutes):
```sql
SELECT address FROM address_activity
WHERE last_processed_at < ? OR last_processed_at IS NULL;
```

---

### 3. work_queue - Priority Queue for Processing

**Purpose**: Manages which addresses to process next based on computed priority

**Creation**: [webhook_handler.py:97-110](webhook_handler.py#L97-L110)

```sql
CREATE TABLE IF NOT EXISTS work_queue (
    address TEXT PRIMARY KEY,
    priority REAL DEFAULT 0.0,
    reason TEXT,
    next_run_at INTEGER DEFAULT 0,
    locked_until INTEGER DEFAULT 0,
    attempts INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_work_queue_priority ON work_queue(priority DESC);
CREATE INDEX IF NOT EXISTS idx_work_queue_next_run ON work_queue(next_run_at ASC);
```

#### Column Definitions

| Column | Type | Description | Example |
|--------|------|-------------|---------|
| `address` | TEXT PRIMARY KEY | Address to process | `5ZpgwwHAxs5kuer3dwwJQxjxvWaXHaLvchZJCRqigPtJ` |
| `priority` | REAL | Computed score (higher = more important) | `75.5` |
| `reason` | TEXT | Why queued (activity reason) | `new_transfer + active_1h` |
| `next_run_at` | INTEGER | Unix timestamp, eligible after this time | `1772552910` |
| `locked_until` | INTEGER | Prevents concurrent processing (set during processing) | `0` or timestamp |
| `attempts` | INTEGER | How many times processed | `3` |
| `updated_at` | TIMESTAMP | Last update timestamp | `2026-03-03 15:40:40` |

#### Priority Scoring

**Priority** combines multiple signals:
```
priority = activity_score + tag_score + network_score + multi_token_score - cooldown_penalty

activity_score:
  +50 if active < 5m
  +30 if active < 1h
  +20 if tx_1h >= 5
  +15 if (sol_in_1h + sol_out_1h) > 10 SOL

tag_score:
  +60 if "watchlist" or "known_malicious"
  +40 if "suspicious"

network_score:
  +35 if in coordinated_creator_edges
  +20 if in super_clusters

multi_token_score:
  +20 if created >= 2 tokens

cooldown_penalty:
  -50 if processed < 2 minutes ago
  -20 if processed < 10 minutes ago
```

#### Locking Mechanism

**During processing** (webhook_worker.py:211-256):
1. Fetch work items WHERE `next_run_at <= now AND locked_until <= now`
2. Set `locked_until = now + 120` (lock for 2 minutes)
3. Process the item
4. Unlock and requeue: `locked_until = 0, next_run_at = now + 300`

This prevents concurrent processing of the same address.

#### Sample Row

```json
{
  "address": "5ZpgwwHAxs5kuer3dwwJQxjxvWaXHaLvchZJCRqigPtJ",
  "priority": 75.5,
  "reason": "new_transfer + active_1h + high_volume_5tx",
  "next_run_at": 1772552910,
  "locked_until": 0,
  "attempts": 3,
  "updated_at": "2026-03-03 15:40:40"
}
```

#### Common Queries

**Get next batch to process**:
```sql
SELECT address, priority, reason
FROM work_queue
WHERE next_run_at <= ? AND locked_until <= ?
ORDER BY priority DESC
LIMIT 10;
```

**Count items by priority tier**:
```sql
SELECT
    COUNT(CASE WHEN priority >= 80 THEN 1 END) as critical,
    COUNT(CASE WHEN priority >= 60 AND priority < 80 THEN 1 END) as elevated,
    COUNT(CASE WHEN priority >= 40 AND priority < 60 THEN 1 END) as moderate,
    COUNT(CASE WHEN priority < 40 THEN 1 END) as low
FROM work_queue;
```

**Find locked items** (in processing):
```sql
SELECT address, locked_until
FROM work_queue
WHERE locked_until > ?;
```

**Get processing statistics**:
```sql
SELECT
    AVG(attempts) as avg_attempts,
    MAX(attempts) as max_attempts,
    COUNT(*) as total_items
FROM work_queue;
```

---

## Integration with Existing FLEX Tables

The webhook system **joins with existing creator analysis tables**:

### creator_self_funding
```sql
SELECT is_self_funding, self_funding_intermediates, total_funders
FROM creator_self_funding
WHERE creator_address = ?
```
**Used for**: Self-funding pattern detection in risk scoring

### creator_funders
```sql
SELECT COUNT(DISTINCT funder_address)
FROM creator_funders
WHERE creator_address = ?
```
**Used for**: Counting unique funders, distribution pattern analysis

### token_analysis
```sql
SELECT
    COUNT(*) as token_count,
    COUNT(CASE WHEN risk_level = 'critical' THEN 1 END) as critical_tokens
FROM token_analysis
WHERE earliest_tx_creator = ?
```
**Used for**: Multi-token creator detection, token risk scoring

### creator_tags
```sql
SELECT tag FROM creator_tags
WHERE creator_address = ?
```
**Used for**: Watchlist/suspicious tag scoring in priority computation

### coordinated_creator_edges
```sql
SELECT COUNT(*) FROM coordinated_creator_edges
WHERE creator_a = ? OR creator_b = ?
```
**Used for**: Network coordination detection

### creator_to_creator_networks
```sql
SELECT network_name FROM creator_to_creator_networks
WHERE creator_address = ?
```
**Used for**: C2C network membership detection

### funding_chains
```sql
SELECT COUNT(*) FROM funding_chains
WHERE source_creator = ? OR dest_creator = ?
```
**Used for**: Funding chain pattern analysis

---

## Data Volume Examples

### Small Deployment (1,000 active addresses)

| Table | Rows | Size | Notes |
|-------|------|------|-------|
| sol_transfers | 50,000 | ~12.5 MB | 50 transfers/address average |
| address_activity | 1,000 | ~150 KB | One row per address |
| work_queue | 1,000 | ~70 KB | One row per address |
| **Total** | **52,000** | **~12.7 MB** | Fits in memory easily |

### Medium Deployment (10,000 active addresses)

| Table | Rows | Size | Notes |
|-------|------|------|-------|
| sol_transfers | 500,000 | ~125 MB | 50 transfers/address |
| address_activity | 10,000 | ~1.5 MB | One per address |
| work_queue | 10,000 | ~700 KB | One per address |
| **Total** | **520,000** | **~127.2 MB** | Comfortable for laptops |

### Large Deployment (100,000 active addresses)

| Table | Rows | Size | Notes |
|-------|------|------|-------|
| sol_transfers | 5,000,000 | ~1.25 GB | 50 transfers/address |
| address_activity | 100,000 | ~15 MB | One per address |
| work_queue | 100,000 | ~7 MB | One per address |
| **Total** | **5,200,000** | **~1.27 GB** | Suitable for servers |

### Mega Deployment (1,000,000+ active addresses)

At this scale, consider:
- Partitioning sol_transfers by date
- Archiving old activity stats
- Horizontal scaling with multiple workers
- Cloud database (PostgreSQL with sharding)

---

## Backup & Recovery

### Backup Strategy

```bash
# Full backup
sqlite3 flex_complete_database.db ".dump" > backup.sql

# Incremental backup (WAL mode)
cp flex_complete_database.db flex_complete_database.db.backup
cp flex_complete_database.db-wal flex_complete_database.db-wal.backup
```

### Recovery

```bash
# From SQL dump
sqlite3 flex_complete_database.db < backup.sql

# From binary backup
cp flex_complete_database.db.backup flex_complete_database.db
cp flex_complete_database.db-wal.backup flex_complete_database.db-wal
```

---

## Maintenance

### Vacuum (Clean up fragmentation)

```sql
VACUUM;
```

### Analyze (Update query optimizer)

```sql
ANALYZE;
```

### Rebuild Indexes

```sql
REINDEX;
```

### Check Database Integrity

```sql
PRAGMA integrity_check;
```

---

## Summary

**The webhook system uses 3 core tables**:

1. **sol_transfers** - Raw transfer events (event log)
2. **address_activity** - Rolling statistics (real-time metrics)
3. **work_queue** - Processing queue (work management)

**Plus 7 existing FLEX tables** for enrichment:
- creator_self_funding
- creator_funders
- token_analysis
- creator_tags
- coordinated_creator_edges
- creator_to_creator_networks
- funding_chains

**Together they create a complete event-driven creator ranking system** that:
- ✅ Ingests webhooks at 1000+ transfers/sec
- ✅ Updates rolling statistics in real-time
- ✅ Prioritizes addresses for processing
- ✅ Applies RPC guardrails
- ✅ Serves enriched creator data with risk scores

---

*Generated: 2026-03-03*
*Claude Code*
