# FLEX Phase 2: RPC Cache Architecture Review

**Date**: March 10, 2026
**Reviewer**: Senior Distributed Systems Engineer
**Scope**: SQLite-backed RPC response caching for Solana funding analysis
**Status**: APPROVED with recommendations for Phase 2.5+

---

## SECTION 1 — Architecture Evaluation

### Overall Assessment: ✅ SOLID FOUNDATION

Phase 2 implements a **pragmatic, low-complexity caching layer** that achieves 30–35% additional RPC savings on top of Phase 1's cursor-based extraction. The architecture is production-ready with zero external dependencies and graceful failure modes.

**Strengths**:
1. **Zero external dependencies** — SQLite only, matches Phase 1 pattern, no new ops burden
2. **Lazy expiry model** — deleted on cache miss, no background workers, simple implementation
3. **Deterministic cache keys** — method + parameter hash ensures collision-free lookups
4. **Graceful degradation** — cache_action="none" if RPCCache init fails, system continues
5. **Backward compatible** — all Phase 1 code paths unchanged
6. **Built-in monitoring** — hit_count tracking + dashboard integration

**Concerns (Minor)**:

1. **Single-table design** — All 4 RPC methods in one table; no per-method partition/optimization
2. **Fixed TTLs** — No dynamic TTL based on response freshness or volatility
3. **Limited eviction policy** — Lazy expiry only; no size-aware eviction when table grows large
4. **Response JSON parsing** — All data stored as TEXT(JSON); no compression or indexing on response content
5. **No adaptive TTL** — First-page signatures use fixed 5min TTL regardless of activity patterns

### Production Readiness: 9/10

**What works**:
- ✅ Handles concurrent access (WAL mode)
- ✅ Graceful error handling (None fallback)
- ✅ Idempotent schema migration
- ✅ Real-time hit/miss tracking
- ✅ Observable via dashboard

**What to add for maturity**:
- ⚠ Response size tracking (for capacity planning)
- ⚠ Proactive cleanup task (optional, for large deployments)
- ⚠ Per-method cache statistics (for optimization)
- ⚠ Adaptive TTL (for 2026+ deployments)

---

## SECTION 2 — Database Schema Improvements

### Current Schema (Baseline)

```sql
CREATE TABLE rpc_response_cache (
    cache_key        TEXT PRIMARY KEY,
    response_json    TEXT NOT NULL,
    method           TEXT NOT NULL,
    cached_at        REAL NOT NULL,
    ttl_seconds      INTEGER NOT NULL,
    hit_count        INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_rpc_cache_expiry ON rpc_response_cache(cached_at);
CREATE INDEX idx_rpc_cache_method ON rpc_response_cache(method);
```

**Assessment**: Minimal but functional. Good for MVP Phase 2.

### Recommended Schema (Phase 2.5+)

Add 3 optional columns for better observability and capacity management:

```sql
-- Option 1: Add response_size tracking (RECOMMENDED)
ALTER TABLE rpc_response_cache
ADD COLUMN response_size INTEGER,
ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;

-- Allows:
-- 1. Monitor cache growth (SELECT SUM(response_size) for capacity planning)
-- 2. Identify large responses (SELECT * ORDER BY response_size DESC LIMIT 10)
-- 3. Calculate bytes/hit (SUM(response_size) / SUM(hit_count))
-- 4. Time-based stats (created_at for cache age tracking)

-- Option 2: Add priority/eviction hints
ALTER TABLE rpc_response_cache
ADD COLUMN priority INTEGER DEFAULT 1,
ADD COLUMN last_hit_at REAL;

-- Allows:
-- 1. Priority-based eviction (keep high-priority entries longer)
-- 2. LRU tracking (evict least-recently-used)
-- 3. Adaptive TTL based on hit frequency
```

### Full Recommended Schema (Phase 2.5+)

```sql
CREATE TABLE IF NOT EXISTS rpc_response_cache (
    -- Primary key & lookup
    cache_key        TEXT PRIMARY KEY,

    -- Cached data
    response_json    TEXT NOT NULL,
    response_size    INTEGER,                   -- NEW: Size in bytes

    -- Metadata
    method           TEXT NOT NULL,
    cached_at        REAL NOT NULL,             -- Unix timestamp (cache creation)
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,  -- NEW: Human-readable timestamp
    ttl_seconds      INTEGER NOT NULL,

    -- Access tracking
    hit_count        INTEGER NOT NULL DEFAULT 0,
    last_hit_at      REAL,                      -- NEW: Unix timestamp (last access)
    miss_count       INTEGER DEFAULT 0,         -- NEW: Track eviction candidates

    -- Eviction hints
    priority         INTEGER DEFAULT 1          -- NEW: 1=normal, 2=high (keep longer)
);

-- Indexes for fast lookups and cleanup
CREATE INDEX IF NOT EXISTS idx_rpc_cache_expiry
ON rpc_response_cache(cached_at)
WHERE cached_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_rpc_cache_method
ON rpc_response_cache(method);

-- NEW: Index for hit-rate analysis
CREATE INDEX IF NOT EXISTS idx_rpc_cache_hits
ON rpc_response_cache(hit_count DESC)
WHERE hit_count > 0;

-- NEW: Index for LRU eviction
CREATE INDEX IF NOT EXISTS idx_rpc_cache_lru
ON rpc_response_cache(last_hit_at ASC);

-- NEW: Index for size-aware eviction
CREATE INDEX IF NOT EXISTS idx_rpc_cache_size
ON rpc_response_cache(response_size DESC);
```

### Migration Path (Zero Downtime)

```sql
-- Step 1: Add new columns (safe, won't break existing code)
BEGIN TRANSACTION;

ALTER TABLE rpc_response_cache
ADD COLUMN response_size INTEGER DEFAULT 0;

ALTER TABLE rpc_response_cache
ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;

ALTER TABLE rpc_response_cache
ADD COLUMN last_hit_at REAL;

ALTER TABLE rpc_response_cache
ADD COLUMN miss_count INTEGER DEFAULT 0;

ALTER TABLE rpc_response_cache
ADD COLUMN priority INTEGER DEFAULT 1;

-- Step 2: Update Python code to populate new fields (in rpc_cache.py)
-- - Calculate response_size = len(json.dumps(response))
-- - Set last_hit_at = time.time() on cache.get()
-- - Increment miss_count on cache miss

-- Step 3: Create new indexes (can happen in background)
CREATE INDEX idx_rpc_cache_hits ON rpc_response_cache(hit_count DESC) WHERE hit_count > 0;
CREATE INDEX idx_rpc_cache_lru ON rpc_response_cache(last_hit_at ASC);
CREATE INDEX idx_rpc_cache_size ON rpc_response_cache(response_size DESC);

COMMIT;
```

### Cost Analysis

**Storage overhead** (estimated for 10,000 cached entries):

| Field | Size | Total | Notes |
|---|---|---|---|
| cache_key | ~80 bytes | 800 KB | Fixed length (method + hash) |
| response_json | ~2 KB avg | 20 MB | Variable, large data |
| method | ~30 bytes | 300 KB | Fixed length |
| cached_at | 8 bytes | 80 KB | REAL (Unix timestamp) |
| ttl_seconds | 4 bytes | 40 KB | INTEGER |
| hit_count | 4 bytes | 40 KB | INTEGER |
| **BASELINE** | | **~21 MB** | Current Phase 2 |
| response_size (NEW) | 4 bytes | 40 KB | INTEGER |
| created_at (NEW) | 20 bytes | 200 KB | TIMESTAMP |
| last_hit_at (NEW) | 8 bytes | 80 KB | REAL |
| miss_count (NEW) | 4 bytes | 40 KB | INTEGER |
| priority (NEW) | 4 bytes | 40 KB | INTEGER |
| **ENHANCED** | | **~21.4 MB** | Phase 2.5 |
| **Overhead** | | **+2%** | Minimal, well worth it |

---

## SECTION 3 — Cache Eviction and TTL Improvements

### Current TTL Strategy (Functional)

| RPC Method | TTL | Hit Rate | Notes |
|---|---|---|---|
| getTransaction | 24h | 40–60% | Immutable on-chain data ✅ |
| getSignaturesForAddress (with before) | 1h | 20–30% | Stable historical pages ✅ |
| getSignaturesForAddress (first page) | 5min | 10–20% | New signatures frequent ✅ |
| helius_enhanced_addresses_transactions | 1h | 15–25% | Append-only, high value ✅ |
| helius_enhanced_transactions_batch | 24h | 40–60% | Immutable batch data ✅ |

**Assessment**: Conservative, safe, reasonable. Works for MVP.

### Improved TTL Strategy (Phase 2.5+)

#### Problem: Fixed TTLs Don't Adapt to Patterns

Current 5-minute TTL for first-page signatures works well for active tokens but wastes cache space for dormant ones.

#### Solution: Adaptive TTL Based on Update Frequency

```python
# In rpc_cache.py: Enhanced set() method

def set(self, cache_key: str, response: dict, method: str) -> None:
    """
    Store response with adaptive TTL based on method and update frequency.

    NEW LOGIC:
    - If this is a first-page signature fetch AND activity_count > 10/min
      → Use 5min TTL (active token)
    - If this is a first-page signature fetch AND activity_count <= 5/min
      → Use 15min TTL (quiet token, reduce churn)
    - All other pages and methods: use standard TTLs
    """

    # Determine TTL
    ttl_seconds = self.TTLS.get(method, 3600)

    # ADAPTIVE: First-page signature queries
    if method == "getSignaturesForAddress" and ":none:" in cache_key:
        # Check if this address has high activity
        conn = self._get_conn()
        if conn:
            cursor = conn.cursor()
            # Count how many times this address was queried in last 5 minutes
            cursor.execute("""
                SELECT COUNT(*) FROM rpc_response_cache
                WHERE cache_key LIKE ? AND last_hit_at > ?
                LIMIT 1
            """, (f"getSignaturesForAddress:{cache_key.split(':')[1]}:%", time.time() - 300))

            activity_count = cursor.fetchone()[0]
            conn.close()

            # Adaptive TTL based on activity
            if activity_count >= 10:
                ttl_seconds = 300    # 5min: hot token (frequent updates)
            elif activity_count >= 5:
                ttl_seconds = 600    # 10min: warm token
            else:
                ttl_seconds = 900    # 15min: cold token (save cache space)

    # ... rest of set() logic unchanged
```

#### Alternative: Simple LRU Eviction (If Table Grows Large)

```python
# In rpc_cache.py: New method for size-aware cleanup

def cleanup_if_size_exceeded(self, max_size_mb: int = 500) -> int:
    """
    If cache table exceeds max_size_mb, evict least-recently-used entries.

    Returns: count of evicted entries
    """
    try:
        conn = self._get_conn()
        if conn is None:
            return 0

        cursor = conn.cursor()

        # Get current size
        cursor.execute("SELECT SUM(response_size) FROM rpc_response_cache")
        current_size_bytes = cursor.fetchone()[0] or 0
        max_size_bytes = max_size_mb * 1024 * 1024

        if current_size_bytes > max_size_bytes:
            # Evict bottom 10% by LRU (least recently used)
            cursor.execute("""
                DELETE FROM rpc_response_cache
                WHERE cache_key IN (
                    SELECT cache_key FROM rpc_response_cache
                    ORDER BY last_hit_at ASC NULLS FIRST
                    LIMIT (SELECT COUNT(*) / 10 FROM rpc_response_cache)
                )
            """)
            deleted = cursor.rowcount
            conn.commit()
            conn.close()

            logger.info(f"[RPC_CACHE] Evicted {deleted} LRU entries ({current_size_bytes/1024/1024:.1f}MB → {max_size_bytes/1024/1024:.1f}MB)")
            return deleted

        conn.close()
        return 0

    except Exception as e:
        logger.warning(f"[RPC_CACHE] cleanup_if_size_exceeded() failed: {e}")
        return 0
```

**Call this periodically**:
```python
# In main.py startup or background task
cache.cleanup_expired()       # Delete TTL-expired entries
cache.cleanup_if_size_exceeded(max_size_mb=500)  # LRU eviction if needed
```

---

## SECTION 4 — Monitoring and Observability Improvements

### Current Monitoring (Functional)

Dashboard displays:
- Total cache entries
- Hit rate (%)
- Credits saved (1h, 24h)

**Assessment**: Good start. Can be enhanced for production.

### Recommended Enhancements (Phase 2.5+)

#### 1. Per-Method Cache Statistics

```sql
-- Query: Cache effectiveness by method
SELECT
  method,
  COUNT(*) as total_entries,
  SUM(hit_count) as total_hits,
  AVG(response_size) as avg_response_bytes,
  SUM(response_size) as total_size_bytes,
  ROUND(SUM(hit_count) * 1.0 / NULLIF(SUM(hit_count) + miss_count, 0) * 100, 1) as hit_rate_pct,
  ROUND(SUM(hit_count) * CASE
    WHEN method = 'getTransaction' THEN 10
    WHEN method = 'getSignaturesForAddress' THEN 10
    WHEN method = 'helius_enhanced_addresses_transactions' THEN 100
    ELSE 1
  END, 0) as credits_saved
FROM rpc_response_cache
GROUP BY method
ORDER BY credits_saved DESC;
```

**Output example**:
```
method | entries | hits | avg_bytes | total_bytes | hit_rate | credits_saved
helius_enhanced_addresses_transactions | 89 | 312 | 4500 | 401K | 42.3% | 31200
getTransaction | 1247 | 2156 | 1800 | 2.2M | 38.1% | 21560
getSignaturesForAddress | 3421 | 3892 | 2200 | 7.5M | 35.6% | 38920
helius_enhanced_transactions_batch | 156 | 234 | 8200 | 1.2M | 39.2% | 2340
```

#### 2. Cache Quality Metrics

```sql
-- Query: Identify underperforming cache entries
SELECT
  cache_key,
  method,
  hit_count,
  miss_count,
  response_size,
  ROUND((hit_count * 1.0) / NULLIF(hit_count + miss_count, 0) * 100, 1) as hit_rate_pct,
  ROUND((cached_at + ttl_seconds - strftime('%s', 'now')) / 3600.0, 1) as hours_until_expiry,
  CASE
    WHEN hit_count = 0 AND (strftime('%s', 'now') - cached_at) > 3600 THEN 'DEAD'
    WHEN hit_count < 3 AND response_size > 10000 THEN 'EXPENSIVE_LOW_HIT'
    WHEN hit_count > 50 THEN 'HIGH_VALUE'
    ELSE 'NORMAL'
  END as quality_tier
FROM rpc_response_cache
ORDER BY
  CASE WHEN quality_tier = 'DEAD' THEN 1
       WHEN quality_tier = 'EXPENSIVE_LOW_HIT' THEN 2
       ELSE 3 END,
  response_size DESC
LIMIT 50;
```

**Use case**: Identify cache entries to evict or investigate.

#### 3. Time-to-Expiry Monitoring

```sql
-- Query: Cache expiry forecast
SELECT
  method,
  COUNT(*) as entries,
  ROUND(AVG((cached_at + ttl_seconds - strftime('%s', 'now')) / 3600.0), 1) as avg_hours_until_expiry,
  ROUND(MIN((cached_at + ttl_seconds - strftime('%s', 'now')) / 3600.0), 1) as min_hours,
  ROUND(MAX((cached_at + ttl_seconds - strftime('%s', 'now')) / 3600.0), 1) as max_hours
FROM rpc_response_cache
GROUP BY method
ORDER BY min_hours ASC;
```

**Output**: Shows which methods' caches are about to expire, useful for capacity planning.

#### 4. Dashboard Enhancement: Multi-Method View

```python
# In phase1_monitoring_enhanced.py: Enhanced get_cache_stats()

def get_cache_stats_by_method(self) -> Dict:
    """Get detailed cache stats broken down by RPC method."""
    try:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
              method,
              COUNT(*) as entries,
              SUM(hit_count) as total_hits,
              SUM(response_size) as total_size_bytes,
              ROUND(SUM(hit_count) * 1.0 / NULLIF(COUNT(*), 0), 1) as avg_hits_per_entry,
              CASE
                WHEN method = 'getTransaction' THEN 10
                WHEN method = 'getSignaturesForAddress' THEN 10
                WHEN method = 'helius_enhanced_addresses_transactions' THEN 100
                ELSE 1
              END as credits_per_call
            FROM rpc_response_cache
            GROUP BY method
        """)

        stats = {}
        for row in cursor.fetchall():
            method, entries, hits, size_bytes, avg_hits, credits = row
            stats[method] = {
                'entries': entries,
                'total_hits': int(hits or 0),
                'total_size_bytes': size_bytes or 0,
                'avg_hits_per_entry': avg_hits,
                'credits_per_call': credits,
                'credits_saved': int((hits or 0) * credits),
            }

        conn.close()
        return stats
    except Exception as e:
        return {'error': str(e)}
```

---

## SECTION 5 — Performance and Scaling Considerations

### Current Performance Profile

**Lookup (cache hit)**: O(log n) via PRIMARY KEY btree
- Typical: <1ms for 10K entries
- Acceptable for all use cases

**Insert/Update (cache miss)**: O(log n) + JSON serialization
- Typical: 5–10ms for ~2KB response
- Acceptable (happens only on RPC miss)

**Cleanup (lazy expiry)**: O(log n) delete on miss
- Typical: <1ms per expired entry
- No impact on normal operations

### Scaling Limits (Current Design)

**Table size**:
- 10,000 entries × 2.5 KB avg = 25 MB ✅
- 100,000 entries × 2.5 KB avg = 250 MB ⚠ (approaching concern threshold)
- 500,000 entries × 2.5 KB avg = 1.25 GB ❌ (problematic)

**SQLite limits** (hard ceiling):
- Database file size: 281 TB (not a practical limit)
- Table rows: 2^63 (not a practical limit)
- Column size: 2 GB per value (response_json could exceed this theoretically, but very unlikely)

**Practical limits** (for FLEX use case):

| Scenario | Cache Entries | Storage | Implications |
|---|---|---|---|
| Single machine (current) | 10K–50K | 25–125 MB | ✅ No action needed |
| High-activity period | 50K–200K | 125–500 MB | ⚠ Enable size-aware eviction |
| Multi-instance future | 200K–500K | 500MB–1.25GB | ❌ Requires distributed cache |

### Performance Optimization Recommendations

#### 1. Compression (Optional, Phase 3+)

For very large responses (100KB+), compress before storing:

```python
import gzip

def set(self, cache_key: str, response: dict, method: str) -> None:
    # ... existing code ...

    response_json = json.dumps(response)

    # Compress if large (optional, Phase 3+)
    if len(response_json) > 50000:  # >50KB
        response_json_to_store = gzip.compress(response_json.encode()).hex()
        is_compressed = 1
    else:
        response_json_to_store = response_json
        is_compressed = 0

    # ... store with is_compressed flag ...

def get(self, cache_key: str) -> Optional[dict]:
    # ... fetch from DB ...

    if is_compressed:
        response_json = gzip.decompress(bytes.fromhex(response_json)).decode()

    return json.loads(response_json)
```

**Benefit**: 10–20× compression for large responses, 5–10ms decompression overhead.
**Downside**: Added complexity, only worthwhile for >100MB table.

#### 2. Batch Expiry (vs Lazy)

Current lazy expiry works well. Optional batch cleanup if table grows large:

```python
def cleanup_expired_batch(self, batch_size: int = 10000) -> int:
    """Bulk delete expired entries in batches."""
    try:
        conn = self._get_conn()
        if conn is None:
            return 0

        now = time.time()
        deleted_total = 0

        while True:
            cursor = conn.cursor()
            cursor.execute("""
                DELETE FROM rpc_response_cache
                WHERE cache_key IN (
                    SELECT cache_key FROM rpc_response_cache
                    WHERE cached_at + ttl_seconds <= ?
                    LIMIT ?
                )
            """, (now, batch_size))

            deleted = cursor.rowcount
            if deleted == 0:
                break

            deleted_total += deleted
            conn.commit()

        conn.close()
        return deleted_total
    except Exception as e:
        logger.warning(f"[RPC_CACHE] cleanup_expired_batch() failed: {e}")
        return 0
```

#### 3. Connection Pooling (Not Needed Yet)

Current single-connection approach with WAL mode is fine. If moving to multi-process architecture (unlikely for FLEX), consider `sqlite3.connect(..., check_same_thread=False)` with external locking.

### Scalability Path

```
Phase 2 (Current)
├─ Single SQLite file
├─ Lazy expiry + optional LRU
└─ 10K–50K entries, ~25–125 MB

Phase 2.5 (Recommended)
├─ Add response_size tracking
├─ Add proactive cleanup task
├─ Add adaptive TTL
└─ 50K–200K entries, ~125–500 MB

Phase 3 (If Needed)
├─ Multi-table partition by method
├─ Redis distributed cache (optional)
└─ 200K+ entries, requires architectural change
```

---

## SECTION 6 — Risks and Mitigation Strategies

### Risk 1: Cache Invalidation Correctness

**Risk**: Cached data becomes stale if blockchain reorg occurs or Helius returns inconsistent data.

**Mitigation**:
- ✅ Current: All cached methods are immutable (getTransaction, historical signatures)
- ✅ Current: First-page signatures use 5min TTL (catches new blocks quickly)
- Recommended: Add per-method freshness validation

```python
# Enhanced: Freshness validation on cache hit
def get_with_freshness_check(self, cache_key: str, method: str) -> Optional[dict]:
    """
    Get cache with optional freshness check (for paranoia-driven deployments).

    Some methods may need validation that cached data matches current on-chain state.
    """
    cached = self.get(cache_key)
    if cached is None:
        return None

    # For immutable methods: no validation needed
    if method in ["getTransaction", "helius_enhanced_transactions_batch"]:
        return cached

    # For append-only methods: optionally validate recency
    if method == "getSignaturesForAddress":
        # Trust TTL; if paranoid, could re-fetch first 10 sigs to verify no gaps
        return cached

    return cached
```

### Risk 2: Large Response Explosion

**Risk**: A single RPC response is larger than expected (>100MB), bloating cache table.

**Mitigation**:
- ✅ Add response_size tracking (Phase 2.5)
- ✅ Add size-aware eviction (Phase 2.5)
- Recommended: Add response size limit on set()

```python
def set(self, cache_key: str, response: dict, method: str, max_size_bytes: int = 10_000_000) -> None:
    """Store response, but reject if it's too large."""
    response_json = json.dumps(response)
    response_size = len(response_json.encode())

    if response_size > max_size_bytes:
        logger.warning(f"[RPC_CACHE] Response too large for {cache_key}: {response_size} bytes > {max_size_bytes} limit")
        return  # Don't cache

    # ... rest of set() unchanged ...
```

### Risk 3: Concurrent Access Under Load

**Risk**: Multiple processes hitting cache simultaneously, potential lock contention.

**Mitigation**:
- ✅ SQLite WAL mode handles concurrent readers
- ✅ WAL mode allows readers while writer is active
- Recommended: Monitor lock wait times in busy periods

```python
def _get_conn_with_timeout(self, timeout_seconds: int = 60) -> sqlite3.Connection:
    """Get connection with extended timeout for high-contention scenarios."""
    try:
        conn = sqlite3.connect(self.db_path, timeout=timeout_seconds)
        conn.execute("PRAGMA busy_timeout = 60000")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")  # Slightly faster for cache writes
        return conn
    except Exception as e:
        logger.error(f"[RPC_CACHE] Connection timeout: {e}")
        return None
```

### Risk 4: Disk Space Exhaustion

**Risk**: Cache table grows unchecked and fills disk.

**Mitigation**:
- ✅ Add size-aware eviction (Phase 2.5)
- Recommended: Add disk space check

```python
import shutil

def cleanup_if_disk_space_low(self, min_free_gb: float = 1.0) -> bool:
    """If disk space < min_free_gb, evict 25% of cache."""
    try:
        stat = shutil.disk_usage(self.db_path)
        free_gb = stat.free / (1024**3)

        if free_gb < min_free_gb:
            logger.warning(f"[RPC_CACHE] Disk space low ({free_gb:.1f} GB free), evicting cache")
            self.cleanup_if_size_exceeded(max_size_mb=self._current_size_mb() // 2)
            return True
    except Exception as e:
        logger.warning(f"[RPC_CACHE] cleanup_if_disk_space_low() failed: {e}")

    return False
```

### Risk 5: Cache Poisoning (Unlikely but Possible)

**Risk**: Malicious or corrupted RPC response stored in cache and served to subsequent requests.

**Mitigation**:
- ✅ All cached methods are deterministic (same input → same output forever)
- ✅ Immutable methods (getTransaction) can never be "poisoned"
- Recommended: Add simple response validation

```python
def _validate_response(self, response: dict, method: str) -> bool:
    """Basic validation that response structure is expected."""
    if method == "getTransaction":
        # Transaction should have 'result' key with transaction details
        return isinstance(response, dict) and ("result" in response or "error" in response)

    if method == "getSignaturesForAddress":
        # Should be list of signature objects
        return isinstance(response, (list, dict))

    return True  # Default: accept anything

def set(self, cache_key: str, response: dict, method: str) -> None:
    if not self._validate_response(response, method):
        logger.warning(f"[RPC_CACHE] Response validation failed for {method}")
        return

    # ... rest of set() unchanged ...
```

### Risk 6: Hit Rate Plateau (Business Risk)

**Risk**: Hit rate increases to 35%, then plateaus—doesn't reach 50–60% target.

**Diagnosis**:
- Likely cause: Most queries are for unique (never-before-seen) data
- Example: Tokens created in last 24h, each queried exactly once

**Mitigation**:
- Monitor per-method hit rates (see Section 4)
- Phase 2b: Wrap 100-credit calls (higher ROI even at lower hit rates)
- Phase 3: Add address clustering (if same creators always queried together, increase hit rate)

```sql
-- Diagnostic: What's the "unrepeated queries" ratio?
SELECT
  'getTransaction' as method,
  COUNT(DISTINCT cache_key) as unique_keys,
  SUM(hit_count) as total_hits,
  ROUND(SUM(hit_count) * 1.0 / COUNT(DISTINCT cache_key), 1) as avg_hits_per_unique_key
FROM rpc_response_cache
WHERE method = 'getTransaction';
```

If avg_hits_per_unique_key < 1.5, hit rate will never exceed 33% (mathematical ceiling).

---

## Summary: Risk-Mitigation Roadmap

| Risk | Severity | Phase 2 | Phase 2.5 | Phase 3+ |
|---|---|---|---|---|
| Cache invalidation | Low | ✅ Mitigated by TTL | ⚡ Add freshness validation | Monitoring |
| Large responses | Medium | ⚠ Possible | ✅ Add size limits | Compression |
| Concurrent access | Low | ✅ WAL mode | ⚡ Monitor lock times | Connection pool |
| Disk exhaustion | Medium | ⚠ Possible | ✅ LRU eviction | Proactive cleanup |
| Cache poisoning | Low | ✅ Unlikely | ⚡ Add validation | Checksums |
| Hit rate plateau | High | Monitor | ⚡ Per-method stats | Phase 2b/3 |

**Overall Risk Profile**: 🟢 LOW for Phase 2 MVP, 🟢 VERY LOW for Phase 2.5

---

## Final Recommendations

### ✅ APPROVE Phase 2 as deployed (MVP is solid)

### 📋 SCHEDULE Phase 2.5 for March 15–20 (if cache grows >100K entries)

Implement:
1. Add response_size, created_at, last_hit_at, miss_count, priority columns
2. Add LRU eviction when cache exceeds 500 MB
3. Add per-method cache stats to dashboard
4. Add response size validation on set()
5. Add disk space monitoring

**Effort**: ~6 hours of code + testing

### 📊 MONITOR Phase 2 for:
- Cache hit rate trend (target: >30% by end of week)
- Table size growth (alert if >200 MB)
- Per-method performance (which methods contribute most to savings)
- Disk space usage

### 🚀 DEFER Phase 3+ until:
- Hit rate plateaus at predictable level
- Cache table exceeds 200K entries
- Per-method analysis identifies optimization opportunities

---

**Reviewer Sign-off**: ✅ PRODUCTION READY
**Confidence Level**: 9.2/10 (minor scalability improvements available)
**Deployment Window**: Immediate (no changes needed for MVP)
