# Performance Optimization Plan

## Current Bottlenecks (Priority Order)

### CRITICAL (2-10x impact)
1. **SQLite open/close per transaction** - Currently opens connection for EACH funder/transfer write
   - File: `realtime_creator_funding_extractor.py` and `funder_incoming_extractor.py`
   - Impact: Massive overhead, should use single connection + executemany()
   - Fix: Buffer writes, use WAL mode, bulk insert

2. **Multiple aiohttp sessions** - Creates new ClientSession inside loops
   - File: `realtime_creator_funding_extractor.py`
   - Impact: Loses connection pooling, costly init overhead
   - Fix: Reuse self.session everywhere, never create new sessions

3. **Synchronous RPC calls one-by-one** - `getTransaction` called per signature
   - File: `funder_incoming_extractor.py`
   - Impact: N requests instead of batched
   - Fix: Use Helius nativeTransfers (already parsed), batch RPC calls

4. **Serial funder extraction** - Processes funders sequentially
   - File: `funder_incoming_extractor.py`
   - Impact: Minutes vs seconds for large funder lists
   - Fix: Convert to async with bounded concurrency (Semaphore)

### HIGH (2-5x impact)
5. **Per-transfer logging in tight loops** - Print on every transfer
   - Files: Both extractors
   - Impact: String formatting + I/O throttles extraction
   - Fix: Counter-based logging, print summaries only

6. **Excessive pagination** - Up to 100 pages × 100 tx = 10k tx per creator
   - File: `realtime_creator_funding_extractor.py`
   - Impact: Most pages after first funding discovery are wasted
   - Fix: Early stopping (5 empty pages, N funders found, X SOL collected, 30 days old)

### MEDIUM (1.5-3x impact)
7. **Missing classification cache** - Calls get_cex_info() + get_account_info() repeatedly
   - File: `funder_incoming_extractor.py`
   - Impact: Repeated lookups for same addresses
   - Fix: @lru_cache on classify_sender()

8. **Unnecessary token operation filtering** - Skips based on token program checks
   - File: `realtime_creator_funding_extractor.py`
   - Impact: Extra parsing on every Helius transaction
   - Fix: Push to background queue if not critical

## Implementation Priority

### Phase 1 (Immediate - 2-10x win)
- [ ] Add SQLite optimization pragmas + buffer writes in realtime extractor
- [ ] Add SQLite optimization pragmas + executemany in funder extractor
- [ ] Reuse self.session (remove extra ClientSession creation)
- [ ] Add early stopping logic to pagination

### Phase 2 (High - 2-5x additional)
- [ ] Convert funder_incoming_extractor.py to async with Semaphore
- [ ] Replace per-transfer logging with counter summaries
- [ ] Add @lru_cache to classify_sender()

### Phase 3 (Medium - 1.5-3x additional)
- [ ] Implement batch RPC calls for getTransaction
- [ ] Push token operation filtering to background queue

## Expected Results

**Before**:
- Creator extraction: 30-60 seconds
- 100 funders extraction: 2-5 minutes
- Total for new token: 3-6 minutes

**After Phase 1**:
- Creator extraction: 5-10 seconds (80% improvement)
- 100 funders extraction: 20-40 seconds (90% improvement)
- Total for new token: 30-60 seconds

**After Phase 2**:
- Creator extraction: 3-5 seconds
- 100 funders extraction: 5-10 seconds (95% improvement)
- Total for new token: 10-20 seconds

## Critical Code Patterns

### SQLite Setup (one-time)
```python
def _setup_db(self):
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA temp_store=MEMORY;")
    conn.execute("PRAGMA cache_size=-200000;")
    return conn
```

### Bulk Insert Pattern
```python
conn = self._setup_db()
cur = conn.cursor()
rows = []  # buffer

# inside loop
rows.append((creator, funder, amount, is_cex, exchange, cex_type, is_classified, fully_analyzed))

# after loop
cur.executemany("INSERT OR IGNORE INTO creator_funders (...) VALUES (...)", rows)
conn.commit()
conn.close()
```

### Async Bounded Concurrency
```python
sem = asyncio.Semaphore(8)

async def process_funder(funder):
    async with sem:
        # do work
        pass

await asyncio.gather(*(process_funder(f) for f in funders))
```

### Early Stopping
```python
empty_pages = 0
total_inbound_funders = set()

for page in pages:
    if page_inbound_count == 0:
        empty_pages += 1
    else:
        empty_pages = 0

    total_inbound_funders.update(page_funders)

    if empty_pages >= 5 or len(total_inbound_funders) >= 50:
        break
```

### Classification Caching
```python
from functools import lru_cache

@lru_cache(maxsize=50000)
def classify_sender(sender_address: str):
    # existing logic
    pass
```
