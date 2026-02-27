# Creator Outgoing Transfer Extractor - Production Deployment

## Overview

The Creator Outgoing Transfer Extractor is a standalone daemon that:
- Scans 1000 creators hourly for outgoing SOL transfers
- Detects creator→funder→creator coordination patterns
- Builds funding chains and coordinated edges
- Uses ~1000-1050 RPC calls/hour (realistic, sustainable)
- Coordinates safely with listener + clustering via cross-process locking

## Quick Start

### 1. Install Dependencies

```bash
pip install aiohttp
```

### 2. Create db_global_lock.py (already exists in repo)

This module provides the cross-process lock shared by listener, extractor, and clustering.

```python
from db_global_lock import db_write_lock_global
```

### 3. Update Your Listener/Clustering

**Before (listener + clustering):**
```python
with DB_WRITE_LOCK:  # Only works within same process!
    # DB writes
```

**After (listener + clustering):**
```python
from db_global_lock import db_write_lock_global

with db_write_lock_global():  # Works across all processes!
    # DB writes
```

### 4. Run the Extractor

```bash
# As a standalone service
python3 creator_outgoing_extractor.py

# Or from within your app (async)
import asyncio
from creator_outgoing_extractor import scan_once, run_forever

# Single scan
await scan_once()

# Or run hourly forever
asyncio.run(run_forever(interval_seconds=3600))
```

## Architecture

### Hourly Flow (scan_once)

```
1. Get 1000 active creators from token_analysis
2. Load all parsing cursors in ONE batch read (not 1000 individual reads)
3. Concurrent RPC calls:
   - 1 getSignaturesForAddress call per creator (1000 calls)
   - Semaphore(25) for rate limiting
   - Safe result merging (gather + zip, no race)
4. Batch Helius Enhanced parsing:
   - Max 100 sigs per request
   - Retry on 429 (rate limit) with exponential backoff
   - Coerce None fields to int (Helius quirk handling)
5. Extract outgoing transfers:
   - Filter to creator senders only
   - Skip non-dict items from API errors
6. Write rows with ONE transaction (not 1000 individual commits)
7. Update cursors with ONE batch upsert
8. Incremental chain building (split phases):
   - Read cursor (no lock)
   - Execute join (no lock)
   - Acquire lock for insert + cursor update (short hold)
9. Incremental edge building (same pattern):
   - Read cursor (no lock)
   - Execute SELECT (no lock)
   - Acquire lock for insert + cursor update (short hold)
```

### Cross-Process Lock

**Lock File:** `pumpswap_tokens.db.write.lock`

**Who Uses It:**
- creator_outgoing_extractor.py
- pumpfun_curve_listener.py (must be updated)
- clustering/network analysis (must be updated)
- Any other DB writer

**How It Works:**
```python
from db_global_lock import db_write_lock_global

# All processes wait for each other
with db_write_lock_global(timeout=30.0):  # Blocks up to 30s, then TimeoutError
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    # ... read/write operations ...
    conn.commit()
    conn.close()
```

**Timeout Behavior:**
- Prevents silent deadlock if one process hangs
- Raises TimeoutError after 30 seconds
- Extractor catches this and logs: `[OUTGOING] ❌ Error: TimeoutError(...)`

## Database Schema

### New Tables

**creator_sig_cursors** - Tracks parsing position per creator
```
creator_address TEXT PRIMARY KEY
last_signature  TEXT
last_slot       INTEGER
updated_at      TIMESTAMP
```

**creator_outgoing_transfers** - Extracted outgoing SOL transfers
```
creator_address TEXT (indexed)
recipient_address TEXT (indexed)
amount_sol REAL
transaction_signature TEXT PRIMARY KEY
slot INTEGER (indexed)
block_time INTEGER (indexed)
first_detected_at TIMESTAMP
recipient_type TEXT
is_cex INTEGER
cex_exchange TEXT
cex_type TEXT
```

**funding_chains** - Creator→funder→creator relationships
```
chain_id INTEGER PRIMARY KEY AUTOINCREMENT
chain_type TEXT
source_creator TEXT (indexed)
bridge_funder TEXT (indexed)
target_creator TEXT (indexed)
source_tx TEXT (UNIQUE with chain_type + target_creator)
source_to_bridge_amount_sol REAL
bridge_to_target_amount_sol REAL
source_block_time INTEGER
bridge_first_detected_at TIMESTAMP
bridge_is_cex INTEGER
confidence INTEGER (50-85 based on amount + CEX status)
created_at TIMESTAMP
```

**coordinated_creator_edges** - High-confidence network edges
```
creator_a TEXT (indexed)
creator_b TEXT (indexed)
bridge_funder TEXT
first_seen_block_time INTEGER
evidence_tx TEXT
confidence INTEGER (≥70)
created_at TIMESTAMP
PRIMARY KEY (creator_a, creator_b, bridge_funder)
```

**outgoing_chain_cursor** - Incremental chain building position
```
id INTEGER PRIMARY KEY (always 1)
last_block_time INTEGER (track processing progress)
```

**coordinated_edge_cursor** - Incremental edge building position
```
id INTEGER PRIMARY KEY (always 1)
last_chain_id INTEGER (track processing progress)
```

### Indexes (Performance Critical)

**creator_outgoing_transfers:**
- `idx_cot_creator` on creator_address (lookups)
- `idx_cot_recipient` on recipient_address (lookups)
- `idx_cot_block_time` on block_time (incremental queries, **NEW**)

**funding_chains:**
- `idx_fc_source` on source_creator
- `idx_fc_bridge` on bridge_funder
- `idx_fc_target` on target_creator

**coordinated_creator_edges:**
- `idx_coord_a` on creator_a
- `idx_coord_b` on creator_b

**creator_funders:** (must exist from creator funding extractor)
- `idx_cf_funder` on funder_address (**ADD THIS** - NEW)

## Performance Characteristics

### RPC Costs (Hourly Budget)

| Operation | Count | Cost |
|-----------|-------|------|
| getSignaturesForAddress | 1000 | 1000 calls |
| Helius Enhanced (avg) | 10-50 | 10-50 calls |
| **Total** | - | **~1000-1050 calls/hour** |

### Database Lock Times

| Operation | Phase | Lock Hold |
|-----------|-------|-----------|
| Cursor load | Read | No lock |
| Cursor update | Write | ~5ms |
| Transfer insert | Write | ~10-20ms |
| Chain building | Read | No lock |
| Chain building | Insert | ~5-10ms |
| Edge building | Read | No lock |
| Edge building | Insert | ~2-5ms |
| **Total per scan** | - | **~30-50ms** |

### Incremental Processing

As tables grow to millions of rows:
- Chain building: O(new transfers) not O(all transfers)
- Edge building: O(new chains) not O(all chains)
- Lock times stay constant (not linear with data growth)

## Monitoring & Logging

All operations log with `[OUTGOING]` prefix:

```
[OUTGOING] 🔍 Scanning 1000 creators...
[OUTGOING] ⏸️ Rate limited (429), retry in 0.5s (attempt 1/3)
[OUTGOING] 📋 Collected 2345 new signatures
[OUTGOING] ✍️ Extracted 567 outgoing transfers
[OUTGOING] ✅ Scan complete: creators=1000 new_sigs=2345 new_rows=567
[OUTGOING] ⏰ Next scan in 3451s
```

### Error Cases

**Rate Limit (429):**
```
[OUTGOING] ⏸️ Rate limited (429), retry in 0.5s (attempt 1/3)
[OUTGOING] ⏸️ Rate limited (429), retry in 1.0s (attempt 2/3)
[OUTGOING] ⚠️ Rate limited (429) after 3 retries, skipping batch
```

**Lock Timeout:**
```
[OUTGOING] ❌ Error: TimeoutError(...Timed out waiting for DB lock...)
```

**No Creators:**
```
[OUTGOING] ℹ️ No creators found
```

## Integration Checklist

- [ ] db_global_lock.py exists in repo root
- [ ] creator_outgoing_extractor.py exists
- [ ] HELIUS_API_KEY environment variable set
- [ ] DB_PATH environment variable set (defaults to pumpswap_tokens.db)
- [ ] Listener uses `from db_global_lock import db_write_lock_global`
- [ ] Clustering uses `from db_global_lock import db_write_lock_global`
- [ ] All DB writers wrapped with `with db_write_lock_global():`
- [ ] First run of extractor creates tables + indexes
- [ ] Monitoring configured for `[OUTGOING]` log lines
- [ ] Alerts set up for timeout errors

## Troubleshooting

### "database is locked" Errors

**Cause:** Another process is writing without using cross-process lock.

**Fix:** Update listener/clustering to use:
```python
from db_global_lock import db_write_lock_global
with db_write_lock_global():
    # DB writes
```

### Extractor Hangs

**Cause:** Listener/clustering holds lock for very long transaction.

**Expected:** Extractor waits up to 30s then raises TimeoutError.

**Fix:** Break long transactions into smaller ones, or increase timeout:
```python
with db_write_lock_global(timeout=60.0):  # 60 second timeout
    # operations
```

### Rate Limits (429 from Helius)

**Expected:** Occasional in high-volume scenarios.

**Behavior:** Automatic retry with exponential backoff (0.5s, 1s, 2s).

**Log:** `[OUTGOING] ⏸️ Rate limited (429), retry in 0.5s (attempt 1/3)`

**If Persistent:** May need to reduce creator count or increase interval.

### Missing Indexes

**Symptoms:** Slow incremental queries after tables grow large.

**Fix:** Run `ensure_tables()` again or manually:
```sql
CREATE INDEX IF NOT EXISTS idx_cot_block_time 
  ON creator_outgoing_transfers(block_time);
CREATE INDEX IF NOT EXISTS idx_cf_funder 
  ON creator_funders(funder_address);
```

## Advanced Options

### Reduce Creator Count (Lower RPC Cost)

```python
# In scan_once()
creators = get_creators(limit=500)  # Instead of 1000
```

### Increase Scan Interval (Reduce Frequency)

```bash
asyncio.run(run_forever(interval_seconds=7200))  # 2 hours instead of 1
```

### Adjust Concurrency (Rate Limiting)

```python
# In scan_once()
await scan_once(concurrency=10)  # Instead of 25
```

### Increase Lock Timeout (Wait Longer)

```python
# In db_global_lock.py or caller
with db_write_lock_global(timeout=60.0):  # 60s instead of 30s
    # operations
```

## Related Documentation

- CLAUDE.md - Project overview
- realtime_creator_funding_extractor.py - Creator funding extraction
- funder_incoming_extractor.py - Funder transfer extraction
- cross_funding_network_analyzer.py - Network relationship analysis

