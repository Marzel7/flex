# Webhook Creator Data Flow - Complete Technical Guide

**Status**: Production Ready
**Date**: 2026-03-03
**Author**: Claude Code

---

## Overview

This document explains **how creators flow through the webhook system** and are enriched with risk scores before being served to API endpoints.

The system has **3 main stages**:
1. **Ingestion** - Helius webhooks → SOL transfers
2. **Enrichment** - Activity stats + Risk scoring
3. **Serving** - API endpoints with rich creator data

---

## Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────────┐
│ STAGE 1: WEBHOOK INGESTION                                           │
├──────────────────────────────────────────────────────────────────────┤
│                                                                        │
│  Helius RAW Webhook (POST /helius/webhook)                          │
│         ↓                                                             │
│  Extract System Program Transfers                                    │
│  [webhook_handler.py:extract_system_transfers()]                     │
│         ↓                                                             │
│  Parse source, destination, amount (lamports)                        │
│         ↓                                                             │
│  Deduplicate by signature (INSERT OR IGNORE)                         │
│         ↓                                                             │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │ INSERT INTO sol_transfers (                                 │    │
│  │   signature, slot, block_time,                              │    │
│  │   source, destination, lamports, amount_sol                 │    │
│  │ )                                                            │    │
│  └─────────────────────────────────────────────────────────────┘    │
│         ↓                                                             │
│  UPDATE address_activity (rolling stats for source + dest)          │
│  [webhook_handler.py:update_activity_stats()]                        │
│         ↓                                                             │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │ UPDATE address_activity SET                                 │    │
│  │   tx_5m = ..., tx_1h = ..., sol_in_1h = ...               │    │
│  │ WHERE address = ?                                           │    │
│  └─────────────────────────────────────────────────────────────┘    │
│         ↓                                                             │
│  Enqueue both addresses to work_queue                                │
│  [webhook_handler.py:enqueue_addresses()]                            │
│         ↓                                                             │
│  Return 200 (< 50ms)                                                 │
│                                                                        │
└──────────────────────────────────────────────────────────────────────┘
         ↓
┌──────────────────────────────────────────────────────────────────────┐
│ STAGE 2: PRIORITY WORKER PROCESSING                                   │
├──────────────────────────────────────────────────────────────────────┤
│                                                                        │
│  Worker Loop (Background Thread)                                     │
│  [webhook_worker.py:run_worker()]                                    │
│         ↓                                                             │
│  Fetch highest priority addresses from work_queue                    │
│         ↓                                                             │
│  Lock rows (locked_until = now + 120s)                               │
│         ↓                                                             │
│  Compute Priority Score (DB-only signals)                            │
│  [webhook_worker.py:compute_priority()]                              │
│         ├─ Activity: tx_5m, tx_1h, sol_in/out_1h                    │
│         ├─ Tags: watchlist, suspicious (creator_tags)               │
│         ├─ Network: coordinated_funders, super_clusters             │
│         ├─ Multi-token: count from token_analysis                   │
│         └─ Cooldown penalty: time since last_processed_at           │
│         ↓                                                             │
│  Update work_queue with computed priority                            │
│         ↓                                                             │
│  RPC Check: priority >= 80 + cooldown + rate_limit?                 │
│  [webhook_worker.py:should_call_rpc()]                               │
│         ├─ NO → Skip RPC, mark processed                            │
│         └─ YES → Call RPC (getSignaturesForAddress)                 │
│         ↓                                                             │
│  Mark as processed (last_processed_at = now)                         │
│         ↓                                                             │
│  Requeue for next batch (next_run_at = now + 5min)                   │
│                                                                        │
└──────────────────────────────────────────────────────────────────────┘
         ↓
┌──────────────────────────────────────────────────────────────────────┐
│ STAGE 3: ENRICHMENT & SERVING                                         │
├──────────────────────────────────────────────────────────────────────┤
│                                                                        │
│  API Request: GET /api/creator-recent-checks/enriched               │
│  [webhook_api_enriched.py:get_creator_recent_checks_enriched()]     │
│         ↓                                                             │
│  Query sol_transfers for distinct creators                           │
│         ↓                                                             │
│  For each creator:                                                    │
│    [webhook_api_enriched.py:lines 59-170]                            │
│         ↓                                                             │
│    1. Get basic stats from sol_transfers                            │
│       - token_count (from token_analysis)                           │
│       - funder_count (from creator_funders)                         │
│       - outgoing_count (from sol_transfers WHERE source = creator) │
│       - last_scanned (timestamp)                                    │
│         ↓                                                             │
│    2. Get traditional findings                                       │
│       - Self-funding check (creator_self_funding)                   │
│       - Distribution pattern (sol_transfers recipients)             │
│       - Coordinated edges (coordinated_creator_edges)               │
│       - C2C network membership (creator_to_creator_networks)        │
│         ↓                                                             │
│    3. Compute Risk Score                                            │
│       [webhook_creator_ranker.py:compute_creator_risk_score()]     │
│         ├─ Activity scoring (from address_activity)                │
│         ├─ Pattern scoring (self-funding, distribution)            │
│         ├─ Network scoring (coordination, C2C)                     │
│         └─ Token behavior scoring (multi-token, rapid launch)      │
│         ↓                                                             │
│    4. Enrich with component_scores & reasons                       │
│       ┌──────────────────────────────────────────┐                  │
│       │ component_scores: {                      │                  │
│       │   activity: 40,                          │                  │
│       │   self_funding: 0,                       │                  │
│       │   distribution: -25,                     │                  │
│       │   concentration: 0,                      │                  │
│       │   network: 0,                            │                  │
│       │   token_behavior: 30                     │                  │
│       │ }                                        │                  │
│       │ risk_score: 45                           │                  │
│       │ risk_level: "moderate"                   │                  │
│       │ risk_reasons: [                          │                  │
│       │   "active_5m(3tx)",                      │                  │
│       │   "distribution(15recipients)"           │                  │
│       │ ]                                        │                  │
│       └──────────────────────────────────────────┘                  │
│         ↓                                                             │
│  Sort by risk_score DESC (highest risk first)                       │
│         ↓                                                             │
│  Return JSON response                                                │
│                                                                        │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Database Schema

### 1. sol_transfers (Webhook Ingestion)

**Purpose**: Stores deduplicated SOL transfers from Helius webhooks

**Location**: [webhook_handler.py:lines 50-63](webhook_handler.py#L50-L63)

```sql
CREATE TABLE sol_transfers (
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

CREATE INDEX idx_sol_transfers_source ON sol_transfers(source);
CREATE INDEX idx_sol_transfers_destination ON sol_transfers(destination);
CREATE INDEX idx_sol_transfers_block_time ON sol_transfers(block_time DESC);
```

**Columns**:
- `signature` (TEXT, PRIMARY KEY) - Unique transaction signature (prevents duplicates)
- `slot` (INTEGER) - Solana slot number
- `block_time` (INTEGER) - Unix timestamp
- `source` (TEXT) - Sender address (creator if outgoing)
- `destination` (TEXT) - Receiver address
- `lamports` (INTEGER) - Amount in lamports
- `amount_sol` (REAL) - Amount in SOL (cached for convenience)
- `received_at` (TIMESTAMP) - When webhook arrived
- `processed` (BOOLEAN) - Whether downstream processing completed

**Indexes**:
- `source` - For querying creator's outgoing transfers
- `destination` - For querying creator's incoming transfers
- `block_time DESC` - For recent transfer queries

**Example Row**:
```json
{
  "signature": "2NRmUxAn6QDrhm9PpXDfx4vKYDJgvkwxB...",
  "slot": 403966256,
  "block_time": 1772552611,
  "source": "5ZpgwwHAxs5kuer3dwwJQxjxvWaXHaLvchZJCRqigPtJ",
  "destination": "HZUZfV5SYyEtDvaSp6UTLmj9Vw628HEMgR489sGPJ23z",
  "lamports": 200000,
  "amount_sol": 0.0002,
  "received_at": "2026-03-03 15:40:40",
  "processed": 0
}
```

---

### 2. address_activity (Rolling Statistics)

**Purpose**: Tracks real-time activity stats for each address

**Location**: [webhook_handler.py:lines 69-95](webhook_handler.py#L69-L95)

```sql
CREATE TABLE address_activity (
    address TEXT PRIMARY KEY,
    last_seen_at INTEGER NOT NULL,

    -- Transaction counts
    tx_5m INTEGER DEFAULT 0,
    tx_1h INTEGER DEFAULT 0,
    tx_24h INTEGER DEFAULT 0,

    -- SOL inflows
    sol_in_5m REAL DEFAULT 0.0,
    sol_in_1h REAL DEFAULT 0.0,
    sol_in_24h REAL DEFAULT 0.0,

    -- SOL outflows
    sol_out_5m REAL DEFAULT 0.0,
    sol_out_1h REAL DEFAULT 0.0,
    sol_out_24h REAL DEFAULT 0.0,

    -- Processing metadata
    last_processed_at INTEGER,
    last_rpc_fetch_at INTEGER,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_address_activity_last_seen ON address_activity(last_seen_at DESC);
```

**Columns**:
- `address` (TEXT, PRIMARY KEY) - The wallet address
- `last_seen_at` (INTEGER) - Unix timestamp of most recent transfer
- `tx_5m`, `tx_1h`, `tx_24h` (INTEGER) - Transaction counts in windows
- `sol_in_5m`, `sol_in_1h`, `sol_in_24h` (REAL) - SOL received
- `sol_out_5m`, `sol_out_1h`, `sol_out_24h` (REAL) - SOL sent
- `last_processed_at` (INTEGER) - When worker last processed this
- `last_rpc_fetch_at` (INTEGER) - When last RPC call made
- `updated_at` (TIMESTAMP) - When stats last updated

**Example Row**:
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

---

### 3. work_queue (Priority Queue)

**Purpose**: Manages which addresses to process next based on priority

**Location**: [webhook_handler.py:lines 97-110](webhook_handler.py#L97-L110)

```sql
CREATE TABLE work_queue (
    address TEXT PRIMARY KEY,
    priority REAL DEFAULT 0.0,
    reason TEXT,
    next_run_at INTEGER DEFAULT 0,
    locked_until INTEGER DEFAULT 0,
    attempts INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_work_queue_priority ON work_queue(priority DESC);
CREATE INDEX idx_work_queue_next_run ON work_queue(next_run_at ASC);
```

**Columns**:
- `address` (TEXT, PRIMARY KEY) - The address to process
- `priority` (REAL) - Computed score (higher = more important)
- `reason` (TEXT) - Why it was queued (e.g., "new_transfer", "high_activity")
- `next_run_at` (INTEGER) - Unix timestamp, eligible for processing after this
- `locked_until` (INTEGER) - Prevents concurrent processing (set during processing)
- `attempts` (INTEGER) - How many times this address has been processed
- `updated_at` (TIMESTAMP) - When this row was last updated

**Example Row**:
```json
{
  "address": "5ZpgwwHAxs5kuer3dwwJQxjxvWaXHaLvchZJCRqigPtJ",
  "priority": 75.5,
  "reason": "new_transfer + active_1h",
  "next_run_at": 1772552910,
  "locked_until": 0,
  "attempts": 3,
  "updated_at": "2026-03-03 15:40:40"
}
```

---

### 4. Existing Tables (Integrated)

The webhook system **integrates with existing FLEX tables**:

#### creator_self_funding
```sql
-- Queried by enrich_creator_with_risk_score()
-- Used to detect self-funding patterns
SELECT
    is_self_funding,
    self_funding_intermediates,
    total_funders
FROM creator_self_funding
WHERE creator_address = ?
```

#### creator_funders
```sql
-- Queried to count unique funders
-- Used for distribution pattern analysis
SELECT COUNT(DISTINCT funder_address) as funder_count
FROM creator_funders
WHERE creator_address = ?
```

#### creator_tags
```sql
-- Optional: Tagging system for watchlist/suspicious creators
-- Checked in webhook_worker.py:compute_priority()
SELECT tag FROM creator_tags WHERE creator_address = ?
```

#### token_analysis
```sql
-- Token data associated with creators
-- Used for multi-token creator detection
SELECT
    COUNT(*) as token_count,
    SUM(CASE WHEN risk_level = 'critical' THEN 1 ELSE 0 END) as critical_tokens
FROM token_analysis
WHERE earliest_tx_creator = ?
```

#### coordinated_creator_edges
```sql
-- Network coordination signals
-- Used for network-based risk scoring
SELECT COUNT(*) as coordinated_count
FROM coordinated_creator_edges
WHERE creator_a = ? OR creator_b = ?
```

#### creator_to_creator_networks
```sql
-- C2C network membership
-- Used for network pattern detection
SELECT network_name
FROM creator_to_creator_networks
WHERE creator_address = ?
```

---

## Code References - Creator Data Flow

### Stage 1: Webhook Ingestion

**File**: `webhook_handler.py`

#### Extract Transfers
[Lines 118-160](webhook_handler.py#L118-L160)
```python
def extract_system_transfers(tx: Dict) -> List[Tuple[str, str, int, str, int, int]]:
    """
    Extract SOL transfers from System Program instructions.

    Returns list of:
    (source, destination, lamports, signature, slot, block_time)
    """
    transfers = []
    sig = tx.get("signature")
    slot = tx.get("slot", 0)
    block_time = tx.get("blockTime", 0)
    message = tx.get("transaction", {}).get("message", {})
    instructions = message.get("instructions", [])
    account_keys = message.get("accountKeys", [])

    for instr in instructions:
        # System Program = "11111111111111111111111111111111"
        program_idx = instr.get("programIdIndex")
        if program_idx < len(account_keys):
            program = account_keys[program_idx]
            if program not in ["11111111111111111111111111111111", "System"]:
                continue

        parsed = instr.get("parsed", {})
        if parsed and parsed.get("type") == "transfer":
            info = parsed.get("info", {})
            source = info.get("source")
            destination = info.get("destination")
            lamports = int(info.get("lamports", 0))

            if source and destination and lamports > 0:
                transfers.append((source, destination, lamports, sig, slot, block_time))

    return transfers
```

#### Insert to sol_transfers
[Lines 162-185](webhook_handler.py#L162-L185)
```python
def store_transfers(conn: sqlite3.Connection, transfers: List[Tuple]) -> int:
    """
    Insert transfers into sol_transfers table (deduplicates by signature).

    INSERT OR IGNORE handles the deduplication automatically.
    If signature already exists, the INSERT is ignored.
    """
    cur = conn.cursor()

    stored = 0
    duplicates = 0

    for source, dest, lamports, sig, slot, block_time in transfers:
        amount_sol = lamports / 1_000_000_000
        try:
            cur.execute("""
                INSERT OR IGNORE INTO sol_transfers
                (signature, slot, block_time, source, destination, lamports, amount_sol)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (sig, slot, block_time, source, dest, lamports, amount_sol))

            if cur.rowcount > 0:
                stored += 1
            else:
                duplicates += 1
        except Exception as e:
            print(f"[ERROR] Failed to insert transfer {sig}: {e}")

    conn.commit()
    return stored
```

#### Update Activity Stats
[Lines 187-235](webhook_handler.py#L187-L235)
```python
def update_activity_stats(conn: sqlite3.Connection, transfers: List[Tuple]) -> None:
    """
    Update address_activity with rolling statistics.

    For each transfer, updates both source (sender) and destination (receiver).
    Recalculates tx_5m, tx_1h, sol_in/out windows based on recent transfers.
    """
    cur = conn.cursor()
    now = int(time.time())

    for source, dest, lamports, sig, slot, block_time in transfers:
        amount_sol = lamports / 1_000_000_000

        # Update source (sender)
        cur.execute("SELECT last_processed_at FROM address_activity WHERE address = ?", (source,))
        row = cur.fetchone()
        exists = row is not None

        # Count transactions and sum SOL in time windows
        for address, is_receiver in [(source, False), (dest, True)]:
            # Get transfers in last 5m, 1h
            five_min_ago = block_time - 300
            one_hour_ago = block_time - 3600
            twenty_four_hours_ago = block_time - 86400

            if is_receiver:
                # Incoming transfers
                cur.execute("""
                    SELECT
                        COUNT(*) as tx_5m,
                        SUM(amount_sol) as sol_5m
                    FROM sol_transfers
                    WHERE destination = ? AND block_time > ?
                """, (address, five_min_ago))
            else:
                # Outgoing transfers
                cur.execute("""
                    SELECT
                        COUNT(*) as tx_5m,
                        SUM(amount_sol) as sol_5m
                    FROM sol_transfers
                    WHERE source = ? AND block_time > ?
                """, (address, five_min_ago))

            # Similar queries for 1h and 24h windows...
            # Then UPDATE address_activity with new counts
```

#### Enqueue to work_queue
[Lines 237-260](webhook_handler.py#L237-L260)
```python
def enqueue_addresses(conn: sqlite3.Connection, transfers: List[Tuple]) -> None:
    """
    Add source and destination addresses to work_queue for processing.

    Sets initial priority = 50.0 (moderate)
    Reason = "new_transfer"
    next_run_at = now (eligible immediately)
    """
    cur = conn.cursor()
    addresses_to_queue = set()

    for source, dest, _, _, _, _ in transfers:
        addresses_to_queue.add(source)
        addresses_to_queue.add(dest)

    for address in addresses_to_queue:
        cur.execute("""
            INSERT OR REPLACE INTO work_queue
            (address, priority, reason, next_run_at)
            VALUES (?, ?, ?, ?)
        """, (address, 50.0, "new_transfer", int(time.time())))

    conn.commit()
```

---

### Stage 2: Priority Worker

**File**: `webhook_worker.py`

#### Fetch and Lock Work
[Lines 211-256](webhook_worker.py#L211-L256)
```python
def fetch_next_work(conn: sqlite3.Connection, batch_size: int = BATCH_SIZE) -> list:
    """
    Fetch highest priority, unlocked work items.

    1. Select addresses from work_queue where:
       - next_run_at <= now (eligible for processing)
       - locked_until <= now (not currently being processed)
    2. Order by priority DESC (highest first)
    3. Lock them (set locked_until = now + 120 seconds)
    """
    cur = conn.cursor()
    now = int(time.time())

    cur.execute("""
        SELECT address, priority, reason
        FROM work_queue
        WHERE next_run_at <= ? AND locked_until <= ?
        ORDER BY priority DESC
        LIMIT ?
    """, (now, now, batch_size))

    work_items = cur.fetchall()

    # Lock these items
    for address, _, _ in work_items:
        cur.execute("""
            UPDATE work_queue
            SET locked_until = ?
            WHERE address = ?
        """, (now + 120, address))  # Lock for 2 minutes

    conn.commit()
    return work_items
```

#### Compute Priority
[Lines 53-210](webhook_worker.py#L53-L210)
```python
def compute_priority(conn: sqlite3.Connection, address: str) -> Tuple[float, str]:
    """
    Recompute priority using DB-only signals.

    Combines:
    1. Activity: recency + volume from address_activity
    2. Tags: watchlist/suspicious from creator_tags
    3. Network: coordinated_funders, super_clusters
    4. Multi-token: count from token_analysis
    5. Cooldown: penalty if processed recently
    """
    score = 0.0
    reasons = []

    cur = conn.cursor()
    now = int(time.time())

    # Get address activity
    cur.execute("""
        SELECT last_seen_at, tx_5m, tx_1h, sol_in_1h, sol_out_1h, last_processed_at
        FROM address_activity WHERE address = ?
    """, (address,))

    row = cur.fetchone()
    if row:
        last_seen, tx_5m, tx_1h, sol_in_1h, sol_out_1h, last_processed = row

        # Activity scoring
        if last_seen and (now - last_seen) < 300:  # 5 minutes
            score += 50
            reasons.append("active_5m")
        elif last_seen and (now - last_seen) < 3600:  # 1 hour
            score += 30
            reasons.append("active_1h")

        if tx_1h and tx_1h >= 5:
            score += 20
            reasons.append(f"high_volume_{tx_1h}tx")

        if (sol_in_1h + sol_out_1h) > 10.0:
            score += 15
            reasons.append("high_value")

        # Cooldown penalty (don't reprocess too quickly)
        if last_processed:
            time_since = now - last_processed
            if time_since < 120:  # 2 minutes
                score -= 50
            elif time_since < 600:  # 10 minutes
                score -= 20

    # Check for tags (watchlist, suspicious, etc.)
    try:
        cur.execute("""
            SELECT tag FROM creator_tags WHERE creator_address = ? LIMIT 1
        """, (address,))
        tag_row = cur.fetchone()
        if tag_row:
            tag = tag_row[0]
            if tag in ["watchlist", "known_malicious"]:
                score += 60
                reasons.append(f"tag_{tag}")
    except:
        pass  # Tags table may not exist

    # Check network membership
    try:
        cur.execute("""
            SELECT COUNT(*) FROM coordinated_creator_edges
            WHERE creator_a = ? OR creator_b = ?
        """, (address, address))
        count = cur.fetchone()[0]
        if count > 0:
            score += 35
            reasons.append(f"coordinated_{count}edges")
    except:
        pass

    # Check for multi-token creator
    try:
        cur.execute("""
            SELECT COUNT(DISTINCT mint) FROM token_analysis
            WHERE earliest_tx_creator = ?
        """, (address,))
        count = cur.fetchone()[0]
        if count >= 2:
            score += 20
            reasons.append(f"multi_token_{count}")
    except:
        pass

    return (score, " + ".join(reasons) if reasons else "baseline")
```

#### Mark as Processed
[Lines 357-400](webhook_worker.py#L357-L400)
```python
def process_work_item(conn: sqlite3.Connection, address: str, priority: float, reason: str) -> bool:
    """
    Mark address as processed and requeue for next batch.

    Updates:
    - last_processed_at = now
    - last_rpc_fetch_at = now (if RPC called)
    - next_run_at = now + 300 (5 minutes)
    - locked_until = 0 (unlock)
    """
    cur = conn.cursor()
    now = int(time.time())

    cur.execute("""
        UPDATE address_activity
        SET last_processed_at = ?
        WHERE address = ?
    """, (now, address))

    # Requeue for 5 minutes from now
    next_run = now + 300

    cur.execute("""
        UPDATE work_queue
        SET
            last_processed_at = ?,
            next_run_at = ?,
            locked_until = 0,
            attempts = attempts + 1
        WHERE address = ?
    """, (now, next_run, address))

    conn.commit()
    return True
```

---

### Stage 3: Serving Enriched Creator Data

**File**: `webhook_api_enriched.py`

#### Fetch Recent Creators
[Lines 29-75](webhook_api_enriched.py#L29-L75)
```python
def get_creator_recent_checks_enriched(limit: int = 15):
    """
    GET /api/creator-recent-checks/enriched

    Returns recent creators with risk scores, sorted by risk_score DESC.
    """
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Get creators from sol_transfers (most recently extracted)
    cursor.execute("""
        SELECT DISTINCT source as creator_address
        FROM sol_transfers
        ORDER BY received_at DESC
        UNION
        SELECT DISTINCT destination as creator_address
        FROM sol_transfers
        ORDER BY received_at DESC
    """)

    addresses = list(set([row[0] for row in cursor.fetchall()]))[:limit]

    recent_checks = []

    for creator in addresses:
        # Get token count
        cursor.execute("""
            SELECT COUNT(*) as token_count FROM token_analysis
            WHERE earliest_tx_creator = ?
        """, (creator,))
        token_count = cursor.fetchone()['token_count'] or 0

        # Get funder count
        cursor.execute("""
            SELECT COUNT(DISTINCT funder_address) as funder_count
            FROM creator_funders
            WHERE creator_address = ?
        """, (creator,))
        funder_count = cursor.fetchone()['funder_count'] or 0

        # ... more data gathering ...

        # Build base creator data
        creator_data = {
            'creator_address': creator,
            'token_count': token_count,
            'funder_count': funder_count,
            'chain_count': chain_count,
            'outgoing_count': outgoing_count,
            'last_scanned': last_scanned,
            'networks': [],
            'findings': findings
        }

        # ENRICH WITH RISK SCORES
        ranker_conn = get_ranker_db()
        creator_data = enrich_creator_with_risk_score(ranker_conn, creator_data)
        ranker_conn.close()

        recent_checks.append(creator_data)

    # Sort by risk score (highest/riskiest first)
    recent_checks.sort(key=lambda x: x.get('risk_score', 0), reverse=True)

    return jsonify({
        'recent_checks': recent_checks,
        'enriched': True,
        'sorted_by': 'risk_score DESC',
        'generated_at': datetime.utcnow().isoformat()
    }), 200
```

#### Enrich with Risk Score
**File**: `webhook_creator_ranker.py`

[Lines 400-500](webhook_creator_ranker.py#L400-L500)
```python
def enrich_creator_with_risk_score(conn: sqlite3.Connection, creator_data: Dict) -> Dict:
    """
    Add risk score and component breakdown to creator data.

    Calls compute_creator_risk_score() which scores:
    1. Activity (recency, volume)
    2. Self-funding patterns
    3. Distribution patterns
    4. Concentration risk
    5. Network membership
    6. Token behavior
    """
    creator_address = creator_data['creator_address']

    # Compute overall risk
    risk_data = compute_creator_risk_score(conn, creator_address)

    # Add to creator_data
    creator_data['risk_score'] = risk_data['score']
    creator_data['risk_level'] = risk_data['risk_level']
    creator_data['component_scores'] = risk_data['component_scores']
    creator_data['risk_reasons'] = risk_data['reasons']

    return creator_data
```

#### Compute Overall Risk Score
**File**: `webhook_creator_ranker.py`

[Lines 300-400](webhook_creator_ranker.py#L300-L400)
```python
def compute_creator_risk_score(conn: sqlite3.Connection, address: str) -> Dict:
    """
    Comprehensive risk scoring combining 6 components.

    Returns:
    {
        'score': 45,
        'risk_level': 'moderate',
        'component_scores': {
            'activity': 40,
            'self_funding': 0,
            'distribution': -25,
            'concentration': 0,
            'network': 0,
            'token_behavior': 30
        },
        'reasons': [
            'active_5m(3tx)',
            'distribution(15recipients/3transfers)'
        ]
    }
    """
    component_scores = {}
    reasons = []

    # Component 1: Activity
    activity_score, activity_reasons = score_creator_activity(conn, address)
    component_scores['activity'] = activity_score
    reasons.extend(activity_reasons)

    # Component 2: Self-funding risk
    self_fund_score, self_fund_reasons = score_self_funding_risk(conn, address)
    component_scores['self_funding'] = self_fund_score
    reasons.extend(self_fund_reasons)

    # Component 3: Distribution pattern
    distribution_score, distribution_reasons = score_distribution_pattern(conn, address)
    component_scores['distribution'] = distribution_score
    reasons.extend(distribution_reasons)

    # Component 4: Concentration risk
    concentration_score, concentration_reasons = score_concentration_risk(conn, address)
    component_scores['concentration'] = concentration_score
    reasons.extend(concentration_reasons)

    # Component 5: Network membership
    network_score, network_reasons = score_network_membership(conn, address)
    component_scores['network'] = network_score
    reasons.extend(network_reasons)

    # Component 6: Token behavior
    token_score, token_reasons = score_token_behavior(conn, address)
    component_scores['token_behavior'] = token_score
    reasons.extend(token_reasons)

    # Sum all components
    total_score = sum(component_scores.values())

    # Clamp to 0-100
    total_score = max(0, min(100, total_score))

    # Determine risk level
    if total_score >= 80:
        risk_level = 'critical'
    elif total_score >= 60:
        risk_level = 'elevated'
    elif total_score >= 40:
        risk_level = 'moderate'
    else:
        risk_level = 'low'

    return {
        'score': total_score,
        'risk_level': risk_level,
        'component_scores': component_scores,
        'reasons': reasons,
        'computed_at': datetime.utcnow().isoformat()
    }
```

---

## Activity Scoring Details

From `webhook_creator_ranker.py:score_creator_activity()`

**Data Source**: `address_activity` table

```python
def score_creator_activity(conn: sqlite3.Connection, address: str) -> Tuple[int, List[str]]:
    """
    Score based on webhook-extracted activity.

    Query address_activity for:
    - last_seen_at (recency)
    - tx_5m, tx_1h (frequency)
    - sol_in_1h, sol_out_1h (volume)
    """
    score = 0
    reasons = []

    cur = conn.cursor()
    now = int(time.time())

    cur.execute("""
        SELECT last_seen_at, tx_5m, tx_1h, sol_in_1h, sol_out_1h
        FROM address_activity
        WHERE address = ?
    """, (address,))

    row = cur.fetchone()
    if not row:
        return (0, ["no_recent_activity"])

    last_seen_at, tx_5m, tx_1h, sol_in_1h, sol_out_1h = row

    # Scoring weights
    if last_seen_at:
        time_since_last = now - last_seen_at

        if time_since_last < 300:  # 5 minutes
            score += 40
            reasons.append(f"active_5m({tx_5m}tx)")
        elif time_since_last < 3600:  # 1 hour
            score += 25
            reasons.append(f"active_1h({tx_1h}tx)")
        elif time_since_last < 86400:  # 24 hours
            score += 15
            reasons.append("active_24h")

    if tx_1h and tx_1h >= 5:
        score += 15
        reasons.append(f"high_volume({tx_1h}tx)")

    if (sol_in_1h + sol_out_1h) > 10.0:
        score += 20
        reasons.append(f"high_value({sol_in_1h + sol_out_1h}SOL)")

    return (score, reasons)
```

---

## Complete Request/Response Flow

### API Call
```bash
GET /api/creator-recent-checks/enriched
```

### Database Queries Executed (in order)

```sql
-- 1. Get recent creators from sol_transfers
SELECT DISTINCT source as creator_address
FROM sol_transfers
ORDER BY received_at DESC
UNION
SELECT DISTINCT destination as creator_address
FROM sol_transfers
ORDER BY received_at DESC

-- 2. For each creator, get token count
SELECT COUNT(*) FROM token_analysis
WHERE earliest_tx_creator = ?

-- 3. Get funder count
SELECT COUNT(DISTINCT funder_address)
FROM creator_funders
WHERE creator_address = ?

-- 4. Get outgoing transfers
SELECT COUNT(*) FROM sol_transfers
WHERE source = ?

-- 5. Get self-funding info
SELECT is_self_funding, self_funding_intermediates, total_funders
FROM creator_self_funding
WHERE creator_address = ?

-- 6. Get distribution pattern (recipients)
SELECT COUNT(DISTINCT recipient_address)
FROM sol_transfers
WHERE source = ?

-- 7. Get coordinated edges
SELECT COUNT(*) FROM coordinated_creator_edges
WHERE creator_a = ? OR creator_b = ?

-- 8. Get C2C networks
SELECT network_name FROM creator_to_creator_networks
WHERE creator_address = ?

-- 9. Activity data for scoring
SELECT last_seen_at, tx_5m, tx_1h, sol_in_1h, sol_out_1h
FROM address_activity
WHERE address = ?

-- 10. Token analysis
SELECT COUNT(DISTINCT mint) FROM token_analysis
WHERE earliest_tx_creator = ?
```

### Response
```json
{
  "recent_checks": [
    {
      "creator_address": "5ZpgwwHAxs5kuer3dwwJQxjxvWaXHaLvchZJCRqigPtJ",
      "token_count": 5,
      "funder_count": 42,
      "chain_count": 2,
      "outgoing_count": 8,
      "last_scanned": "2026-03-03 15:40:40",
      "networks": [],
      "findings": [
        "⚠️ DISTRIBUTION_PATTERN"
      ],
      "risk_score": 45,
      "risk_level": "moderate",
      "component_scores": {
        "activity": 40,
        "self_funding": 0,
        "distribution": -25,
        "concentration": 0,
        "network": 0,
        "token_behavior": 30
      },
      "risk_reasons": [
        "active_5m(3tx)",
        "distribution(15recipients/3transfers)"
      ]
    }
  ],
  "enriched": true,
  "sorted_by": "risk_score DESC",
  "generated_at": "2026-03-03T15:41:00.123456"
}
```

---

## Summary

**Creator data flows through 3 stages**:

1. **Ingestion**: Helius webhook → extract transfers → store in `sol_transfers` → update `address_activity` → enqueue to `work_queue`

2. **Processing**: Background worker fetches from `work_queue` → scores using activity + tags + network + multi-token → applies RPC guardrails → marks processed → requeues

3. **Serving**: API endpoint queries `sol_transfers` for creators → enriches with data from 6 existing tables → computes risk score using 6 components → returns sorted by risk_score DESC

The entire system is **event-driven** - no continuous polling, everything happens when webhooks arrive or workers process queued items.

---

*Generated: 2026-03-03*
*Claude Code*
