# Phases 3-5 Implementation Plan: Multi-Source, Metadata Cache, Pre-Warming

**Target**: Production-ready code for Phases 3, 4, 5 of advanced optimizations
**Scope**: 3 files, ~400 lines of code
**Timeline**: 8-10 hours implementation + testing
**Risk**: Medium (Phase 3), Low (Phase 4-5)

---

## Assumptions

1. **Existing system state:**
   - Phase 1 (request queue) is stable and working
   - Phase 2 (activity-based scheduling) is deployed
   - Queue infrastructure (`PriceFetchQueue`) is available and used
   - Callback pattern (`_on_price_fetched`) is implemented
   - Activity scoring works correctly

2. **Third-party API availability:**
   - Dexscreener API remains primary source
   - Jupiter API is secondary fallback
   - Birdeye API is available as tertiary (new addition)
   - Per-source backoff mechanism can be extended to all 3 sources

3. **Database:**
   - SQLite at `database/flex_complete_database.db` is writable
   - `token_price_snapshots` table exists with price history
   - `token_analysis` table exists with current prices
   - No concurrent writes (single worker thread)

4. **Queue system:**
   - `FetchTask` dataclass with optional callback is available
   - Queue supports concurrent execution
   - Callback can be triggered with `(mint, price)` tuple

5. **Backward compatibility:**
   - Existing `/api/price/symbol/<mint>` must remain working
   - Existing `/api/price/batch/register` must remain working
   - Response payloads can be extended with new fields
   - Polling UI doesn't need changes (warm-up is background)

6. **Monitoring:**
   - `/api/price/health` endpoint exists and returns stats dict
   - Stats dict is mutable and can be extended with counters
   - No breaking changes to existing health endpoint format

---

## Architecture Overview

### Phase 3: Multi-Source Price Aggregation

```
get_token_prices_sync(mints)
  │
  ├─→ For each mint:
  │    ├─→ Check hot cache (in-memory, fresh)
  │    │    └─→ Found? Return from cache
  │    │
  │    ├─→ Try Dexscreener (not in backoff)
  │    │    └─→ Success? Store in cache, return
  │    │
  │    ├─→ Try Jupiter (not in backoff)
  │    │    └─→ Success? Store in cache, return
  │    │
  │    ├─→ Try Birdeye (not in backoff)
  │    │    └─→ Success? Store in cache, return
  │    │
  │    ├─→ Fall back to stale cache (any age)
  │    │    └─→ Has value? Return with is_stale=True
  │    │
  │    └─→ Return unavailable
  │
  └─→ Return batch of results with source attribution
```

**Key decisions:**
- Fail fast per source (1 sec timeout max)
- Skip backed-off sources entirely
- Prefer stale cache over empty response
- Track success/failure per source for metrics
- Store source in returned TokenPrice object

### Phase 4: Persistent Metadata Cache

```
get_token_symbol(mint)
  │
  ├─→ Check in-memory cache
  │    └─→ Fresh (<5 min)? Return immediately
  │
  ├─→ Check SQLite metadata_cache
  │    └─→ Fresh (<5 min)? Load to memory, return
  │
  ├─→ Fetch from Dexscreener (cached)
  │    ├─→ Success? Store in memory + SQLite, return
  │    └─→ Fail? Try stale cache
  │
  ├─→ Return stale cache if available
  │    └─→ Mark as stale
  │
  └─→ Return default/error value
```

**Key decisions:**
- SQLite persists across restarts
- Memory cache is primary (warm) access path
- TTL is 5 minutes (matches existing behavior)
- Upsert pattern (simple, no locking needed)
- Schema is minimal (4 fields)

### Phase 5: Cache Pre-Warming

```
POST /api/price/batch/register
  │
  ├─→ Parse request
  ├─→ Register tokens (idempotent)
  │
  ├─→ For each NEW token (not already in registry):
  │    ├─→ Enqueue metadata warm-up task
  │    └─→ Enqueue price warm-up task
  │
  ├─→ Return immediately (non-blocking)
  │    └─→ Include warm_up_queued count in response
  │
  └─→ Background: warm-up tasks process in queue
```

**Key decisions:**
- Return immediately (don't wait for warm-up)
- Only warm-up new tokens (not already registered)
- Reuse existing queue with `FetchTask`
- Queue depth > X: warm-up becomes best-effort (skip if queue full)
- Metrics track success/failure of warm-ups

---

## File-by-File Implementation

### File 1: `src/core/price_service.py`

**Changes**: Add Birdeye client, extend source fallback chain, add metrics

#### New class: `BirdeyeClient`

```python
class BirdeyeClient:
    """
    Birdeye token price API client.

    Fallback source for token prices. Only used if Dexscreener and Jupiter fail.
    """

    def __init__(self, base_url: str = "https://public-api.birdeye.so"):
        self.base_url = base_url
        self.timeout = 3  # More aggressive timeout for fallback

    def fetch_price(self, mint: str) -> Optional[TokenPrice]:
        """
        Fetch price from Birdeye for a single token.

        Returns None if not found, rate-limited, or error.
        """
        try:
            import requests

            url = f"{self.base_url}/defi/token_price?address={mint}"
            resp = requests.get(url, timeout=self.timeout)

            if resp.status_code == 404:
                return None  # Token not found
            if resp.status_code == 429:
                return None  # Rate limited
            if resp.status_code != 200:
                return None

            data = resp.json()
            value = data.get('data', {})

            if not value:
                return None

            price_usd = value.get('price')
            if not price_usd:
                return None

            return TokenPrice(
                mint=mint,
                price_usd=float(price_usd),
                price_sol=value.get('priceInSOL', 0),
                source='birdeye',
                fetched_at=int(time.time())
            )

        except Exception as e:
            logger.debug(f"Birdeye fetch failed for {mint}: {e}")
            return None
```

#### Modify: `get_token_prices_sync()` method

**Key changes:**
- Try sources in order: Dexscreener → Jupiter → Birdeye → stale cache
- Skip backed-off sources
- Track per-source success/failure
- Return first successful result
- Include `source` field in response

```python
def get_token_prices_sync(self, mints: List[str]) -> Dict[str, TokenPrice]:
    """
    Get prices for tokens, trying multiple sources.

    Tries in order:
    1. Hot cache (in-memory, fresh)
    2. Dexscreener (if not in backoff)
    3. Jupiter (if not in backoff)
    4. Birdeye (if not in backoff)
    5. Stale cache (any age)
    6. None (unavailable)
    """
    results = {}

    for mint in mints:
        # Try hot cache first
        cached = self.cache.get(mint)
        if cached and (time.time() - cached.fetched_at) < 30:  # 30s hot
            results[mint] = cached
            self.stats['cache_hits'] += 1
            continue

        price = None
        tried_sources = []

        # Try Dexscreener
        if not self.backoff_manager.is_backed_off('dexscreener'):
            try:
                price = self._fetch_dexscreener_price(mint)
                tried_sources.append('dexscreener')
                if price:
                    self.stats['dexscreener_success'] += 1
                    break
            except Exception as e:
                logger.debug(f"Dexscreener failed for {mint}: {e}")
                self.stats['dexscreener_fail'] += 1

        # Try Jupiter (if not already found)
        if not price and not self.backoff_manager.is_backed_off('jupiter'):
            try:
                price = self._fetch_jupiter_price(mint)
                tried_sources.append('jupiter')
                if price:
                    self.stats['jupiter_success'] += 1
                    break
            except Exception as e:
                logger.debug(f"Jupiter failed for {mint}: {e}")
                self.stats['jupiter_fail'] += 1

        # Try Birdeye (if not already found)
        if not price and not self.backoff_manager.is_backed_off('birdeye'):
            try:
                birdeye_client = BirdeyeClient()
                price = birdeye_client.fetch_price(mint)
                tried_sources.append('birdeye')
                if price:
                    self.stats['birdeye_success'] += 1
                    break
            except Exception as e:
                logger.debug(f"Birdeye failed for {mint}: {e}")
                self.stats['birdeye_fail'] += 1

        # Fall back to stale cache
        if not price:
            stale = self._get_stale_cache(mint)
            if stale:
                price = stale
                price.is_stale = True
                self.stats['stale_cache_fallback'] += 1

        if price:
            results[mint] = price
            # Store in hot cache for 30s
            self.cache[mint] = price

        logger.debug(
            f"Price for {mint}: source={tried_sources}, "
            f"found={price is not None}"
        )

    return results
```

#### Modify: `__init__()` method

Add source metrics to stats dict:

```python
self.stats = {
    'api_calls': 0,
    'cache_hits': 0,
    'stale_cache_fallback': 0,
    'dexscreener_success': 0,
    'dexscreener_fail': 0,
    'jupiter_success': 0,
    'jupiter_fail': 0,
    'birdeye_success': 0,
    'birdeye_fail': 0,
}
```

#### New method: `_get_stale_cache()`

```python
def _get_stale_cache(self, mint: str) -> Optional[TokenPrice]:
    """Get any available cached price regardless of age."""
    # From in-memory cache
    if mint in self.cache:
        return self.cache[mint]

    # From database (latest snapshot)
    try:
        conn = sqlite3.connect(self.db_path, timeout=2)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT price_usd, price_sol FROM token_price_snapshots
            WHERE mint = ?
            ORDER BY captured_at DESC
            LIMIT 1
        """, (mint,))
        row = cursor.fetchone()
        conn.close()

        if row:
            return TokenPrice(
                mint=mint,
                price_usd=row[0],
                price_sol=row[1],
                source='stale_cache',
                fetched_at=0
            )
    except Exception as e:
        logger.debug(f"Stale cache lookup failed for {mint}: {e}")

    return None
```

---

### File 2: `src/apis/price_api.py`

**Changes**: Add persistent metadata cache, pre-warming on registration

#### New table initialization

```python
def _ensure_metadata_cache_table(db_path: str) -> None:
    """Create metadata_cache table if not exists."""
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS metadata_cache (
                mint TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                name TEXT NOT NULL,
                cached_at INTEGER NOT NULL,
                cached_source TEXT
            )
        """)
        conn.commit()
        conn.close()
        logger.info("metadata_cache table ensured")
    except Exception as e:
        logger.error(f"Failed to create metadata_cache table: {e}")
```

#### New helper methods

```python
def _get_cached_metadata(db_path: str, mint: str, max_age_seconds: int = 300) -> Optional[Dict]:
    """
    Get metadata from SQLite cache if fresh.

    Returns dict with symbol, name, cached_at if fresh, None otherwise.
    """
    try:
        conn = sqlite3.connect(db_path, timeout=2)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT symbol, name, cached_at FROM metadata_cache
            WHERE mint = ?
        """, (mint,))
        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        symbol, name, cached_at = row
        age = int(time.time()) - cached_at

        if age <= max_age_seconds:
            return {
                'symbol': symbol,
                'name': name,
                'cached_at': cached_at,
                'age_seconds': age
            }

        return None  # Stale

    except Exception as e:
        logger.debug(f"metadata_cache lookup failed for {mint}: {e}")
        return None


def _store_metadata_cache(db_path: str, mint: str, symbol: str, name: str, source: str) -> None:
    """Store symbol/name in persistent cache."""
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO metadata_cache
            (mint, symbol, name, cached_at, cached_source)
            VALUES (?, ?, ?, ?, ?)
        """, (mint, symbol, name, int(time.time()), source))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"Failed to store metadata cache for {mint}: {e}")
```

#### Modify: `get_token_symbol()` endpoint

**New lookup order:**

```python
@price_api.route('/api/price/symbol/<mint>', methods=['GET'])
def get_token_symbol(mint: str):
    """
    Get token symbol/name with persistent cache.

    Lookup order:
    1. In-memory cache (fresh)
    2. SQLite metadata_cache (fresh)
    3. Upstream fetch (Dexscreener)
    4. Stale cache (any age)
    5. Default
    """
    # 1. Check in-memory cache
    if mint in _symbol_cache:
        cached = _symbol_cache[mint]
        if time.time() - cached['cached_at'] < 300:  # 5 min
            return {
                'mint': mint,
                'symbol': cached['symbol'],
                'name': cached['name'],
                'cached_at': cached['cached_at'],
                'source': 'memory_cache',
                'is_fresh': True
            }

    # 2. Check SQLite persistent cache
    persistent = _get_cached_metadata(db_path, mint, max_age_seconds=300)
    if persistent:
        # Hydrate memory cache
        _symbol_cache[mint] = {
            'symbol': persistent['symbol'],
            'name': persistent['name'],
            'cached_at': persistent['cached_at']
        }
        return {
            'mint': mint,
            'symbol': persistent['symbol'],
            'name': persistent['name'],
            'cached_at': persistent['cached_at'],
            'source': 'persistent_cache',
            'is_fresh': True
        }

    # 3. Fetch upstream
    try:
        symbol, name = _fetch_symbol_from_dexscreener(mint)

        # Store in both caches
        _symbol_cache[mint] = {
            'symbol': symbol,
            'name': name,
            'cached_at': int(time.time())
        }
        _store_metadata_cache(db_path, mint, symbol, name, 'dexscreener')

        return {
            'mint': mint,
            'symbol': symbol,
            'name': name,
            'cached_at': int(time.time()),
            'source': 'dexscreener',
            'is_fresh': True
        }
    except Exception as e:
        logger.debug(f"Upstream fetch failed for {mint}: {e}")

    # 4. Fall back to stale cache
    stale_persistent = _get_cached_metadata(db_path, mint, max_age_seconds=999999)
    if stale_persistent:
        return {
            'mint': mint,
            'symbol': stale_persistent['symbol'],
            'name': stale_persistent['name'],
            'cached_at': stale_persistent['cached_at'],
            'source': 'stale_persistent_cache',
            'is_fresh': False,
            'is_stale': True
        }

    # 5. Default
    return {
        'mint': mint,
        'symbol': 'UNKNOWN',
        'name': 'Unknown Token',
        'source': 'default',
        'is_fresh': False
    }, 404
```

#### Modify: `batch_register` endpoint

**Add warm-up enqueueing:**

```python
@price_api.route('/api/price/batch/register', methods=['POST'])
def batch_register():
    """
    Register tokens and warm-up prices/metadata in background.

    Returns immediately without blocking on warm-up.
    """
    data = request.json or {}
    mints = data.get('mints', [])

    if not mints:
        return {'registered': 0, 'warm_up_queued': 0}, 400

    # Register tokens (existing logic)
    registered = 0
    for mint in mints:
        if registry.register_token(mint):
            registered += 1

    # Enqueue warm-up for new tokens (non-blocking)
    warm_up_queued = 0
    queue = get_price_queue()

    # Check if queue is accepting new work
    queue_stats = queue.get_stats()
    if queue_stats['queue_depth'] < 50:  # Max queue depth threshold

        for mint in mints:
            # Enqueue metadata warm-up
            try:
                task = FetchTask(
                    mint=mint,
                    priority='LOW',  # Warm-up is low priority
                    enqueued_at=time.time(),
                    callback=lambda m, p: _on_warmup_price_fetched(m, p)
                )
                queue.enqueue(task)
                warm_up_queued += 1
            except Exception as e:
                logger.debug(f"Failed to enqueue warm-up for {mint}: {e}")

    return {
        'registered': registered,
        'warm_up_queued': warm_up_queued,
        'queue_depth': queue_stats.get('queue_depth', 0),
        'total_mints': len(mints)
    }


def _on_warmup_price_fetched(mint: str, price: TokenPrice) -> None:
    """Callback when warm-up price fetch completes."""
    global _warmup_stats

    if price:
        _warmup_stats['warm_up_completed'] += 1
        logger.debug(f"Warm-up price fetched for {mint}")
    else:
        _warmup_stats['warm_up_failed'] += 1
        logger.debug(f"Warm-up price failed for {mint}")
```

#### Add initialization call

At module load time:

```python
# At top level of price_api.py
_ensure_metadata_cache_table(db_path)

# In blueprint initialization
_warmup_stats = {
    'warm_up_queued': 0,
    'warm_up_completed': 0,
    'warm_up_failed': 0
}
```

---

### File 3: `src/core/price_worker.py`

**Changes**: Extend stats tracking with source and warm-up metrics

#### Modify: `__init__()` method

Add new metrics to stats dict:

```python
self.stats = {
    # Existing
    'cycles': 0,
    'tokens_prefetched': 0,
    'api_calls': 0,
    'cache_hits': 0,
    'errors': 0,
    'last_run': None,
    'last_error': None,
    'queue_stats': {},
    'activity_distribution': {
        'high': 0,
        'medium': 0,
        'low': 0,
        'dormant': 0
    },
    # New (Phase 3)
    'source_stats': {
        'dexscreener': {'success': 0, 'fail': 0},
        'jupiter': {'success': 0, 'fail': 0},
        'birdeye': {'success': 0, 'fail': 0},
        'cache': {'hits': 0, 'misses': 0},
        'stale_fallback': 0
    },
    # New (Phase 4)
    'metadata_stats': {
        'memory_hits': 0,
        'persistent_hits': 0,
        'upstream_fetches': 0,
        'misses': 0
    },
    # New (Phase 5)
    'warmup_stats': {
        'queued': 0,
        'completed': 0,
        'failed': 0
    }
}
```

#### Modify: `get_stats()` method

Include new fields in health endpoint:

```python
def get_stats(self) -> Dict:
    """Return worker statistics for health endpoint."""
    return {
        'cycles': self.stats['cycles'],
        'tokens_prefetched': self.stats['tokens_prefetched'],
        'api_calls': self.stats['api_calls'],
        'cache_hits': self.stats['cache_hits'],
        'errors': self.stats['errors'],
        'last_run': self.stats['last_run'],
        'last_error': self.stats['last_error'],
        'queue_stats': self.stats['queue_stats'],
        'activity_distribution': self.stats['activity_distribution'],
        # Phase 3
        'source_stats': self.stats['source_stats'],
        # Phase 4
        'metadata_stats': self.stats['metadata_stats'],
        # Phase 5
        'warmup_stats': self.stats['warmup_stats']
    }
```

#### Modify: `_on_price_fetched()` callback

Update source stats tracking:

```python
def _on_price_fetched(self, mint: str, price: TokenPrice) -> None:
    """
    Called when queue completes a price fetch.
    Updates database and stats.
    """
    if not price:
        self.stats['errors'] += 1
        return

    # Track by source
    if hasattr(price, 'source') and price.source:
        source = price.source
        if source in self.stats['source_stats']:
            self.stats['source_stats'][source]['success'] += 1

    # Update database
    try:
        conn = sqlite3.connect(self.db_path, timeout=5)
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE token_analysis
            SET price_current = ?,
                price_updated_at = ?,
                price_source = ?
            WHERE mint = ?
        """, (
            price.price_usd,
            int(time.time()),
            getattr(price, 'source', 'unknown'),
            mint
        ))
        conn.commit()
        conn.close()

        self.stats['api_calls'] += 1
    except Exception as e:
        logger.error(f"Failed to update price for {mint}: {e}")
        self.stats['errors'] += 1
```

---

## Monitoring Extensions

### Extended `/api/price/health` response

```json
{
  "worker_stats": {
    "cycles": 42,
    "tokens_prefetched": 840,
    "cache_hits": 500,
    "errors": 2,
    "activity_distribution": {
      "high": 2,
      "medium": 18,
      "low": 6,
      "dormant": 0
    },
    "source_stats": {
      "dexscreener": {"success": 320, "fail": 8},
      "jupiter": {"success": 45, "fail": 2},
      "birdeye": {"success": 8, "fail": 1},
      "stale_fallback": 25
    },
    "metadata_stats": {
      "memory_hits": 450,
      "persistent_hits": 120,
      "upstream_fetches": 15,
      "misses": 2
    },
    "warmup_stats": {
      "queued": 85,
      "completed": 82,
      "failed": 3
    },
    "queue_stats": {
      "queue_depth": 1,
      "processed": 840,
      "active_requests": 0,
      "avg_latency_ms": 27.4
    }
  }
}
```

### Dashboard monitoring targets

**After Phase 3 (Multi-Source):**
- `dexscreener_success / (dexscreener_success + fail)` should be > 95%
- `birdeye_fail` should be low (only fallback)
- `stale_fallback` should be < 5% of total requests

**After Phase 4 (Persistent Cache):**
- `persistent_hits` should grow to ~200+ after 4h
- `persistent_hits / (persistent_hits + upstream_fetches)` should be > 80%
- After restart: `persistent_hits` should spike initially

**After Phase 5 (Pre-Warming):**
- `warmup_completed / warmup_queued` should be > 95%
- New tokens should have metadata within 1-2 seconds

---

## Rollout Order & Testing

### Phase 3: Multi-Source (3 hours)

1. Add `BirdeyeClient` class
2. Modify `get_token_prices_sync()` with fallback chain
3. Add source metrics to stats
4. Test:
   ```bash
   curl http://localhost:5002/api/price/health | jq '.worker_stats.source_stats'
   ```
   Expected: dexscreener_success > 500, birdeye < 50 (fallback)

### Phase 4: Metadata Cache (2 hours)

1. Create `metadata_cache` table
2. Add `_get_cached_metadata()` and `_store_metadata_cache()`
3. Modify `get_token_symbol()` with multi-level lookup
4. Test:
   ```bash
   # First call
   curl http://localhost:5002/api/price/symbol/DxoTY4u...
   # Should fetch from upstream

   # Second call (within 5 min)
   curl http://localhost:5002/api/price/symbol/DxoTY4u...
   # Should return from cache

   # Restart server
   ./scripts/restart.sh

   # Third call
   curl http://localhost:5002/api/price/symbol/DxoTY4u...
   # Should return from persistent cache (no upstream call)
   ```

### Phase 5: Pre-Warming (1.5 hours)

1. Modify `/api/price/batch/register` to enqueue warm-up
2. Add `_on_warmup_price_fetched()` callback
3. Add warm-up metrics
4. Test:
   ```bash
   # Register new token
   curl -X POST http://localhost:5002/api/price/batch/register \
     -H "Content-Type: application/json" \
     -d '{"mints": ["NewMintAddress..."]}'

   # Check warm-up queued
   curl http://localhost:5002/api/price/health | jq '.worker_stats.warmup_stats'

   # Wait 2-3 seconds
   sleep 3

   # Should see warm_up_completed > 0
   ```

---

## Risks & Mitigations

### Risk 1: Birdeye API doesn't have all tokens
**Likelihood**: Medium
**Impact**: Falls back to Jupiter (as designed)
**Mitigation**:
- Birdeye is tested independently before rollout
- Stale cache fallback ensures no blank cells
- Monitor `birdeye_fail` in health endpoint
- If consistently high, reduce Birdeye to emergency-only

### Risk 2: SQLite metadata writes slow down registration
**Likelihood**: Low
**Impact**: `batch_register` endpoint latency increase
**Mitigation**:
- Metadata writes are fire-and-forget (not blocking)
- Single queue worker ensures no concurrency
- Monitor `get_token_symbol()` latency
- If problematic, batch writes or defer to async

### Risk 3: Queue fills up during token spikes with warm-up tasks
**Likelihood**: Low
**Impact**: Warm-up doesn't complete for all tokens
**Mitigation**:
- Check queue depth before enqueueing warm-up
- If depth > 50, skip warm-up (best-effort)
- Warm-up tasks are `LOW` priority
- Monitor `warmup_failed` count
- Queue auto-recovers when spike ends

### Risk 4: Persistent cache corrupts or becomes inconsistent
**Likelihood**: Very low (SQLite is robust)
**Impact**: Symbol display issues
**Mitigation**:
- Metadata writes are simple upserts (no transactions)
- Fallback to upstream if persistent read fails
- Periodic vacuum/integrity check (future)
- Human-readable schema (easy to debug)

### Risk 5: Source backoff mechanism needs extension
**Likelihood**: Low
**Impact**: Birdeye not skipped when in backoff
**Mitigation**:
- Existing backoff manager already handles multiple sources
- If not, extend `is_backed_off(source)` to accept source name
- Test backoff behavior: trigger 429, verify source skipped

---

## Commit Strategy

```bash
# Phase 3
git add src/core/price_service.py
git commit -m "optimization(Phase 3): Multi-source price aggregation

Add Birdeye as fallback source with chain: Dexscreener → Jupiter → Birdeye → stale cache

Changes:
- New BirdeyeClient class for token price lookups
- Modified get_token_prices_sync() to try sources in order
- Skip backed-off sources in fallback chain
- Track per-source success/fail metrics
- Include source attribution in TokenPrice

Expected 99%+ availability during partial upstream failures.

Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>"

# Phase 4
git add src/apis/price_api.py src/core/price_worker.py
git commit -m "optimization(Phase 4): Persistent metadata cache

Persist symbol/name cache to SQLite for restart resilience.

Changes:
- New metadata_cache table with 5-min TTL
- Modified get_token_symbol() with multi-level lookup:
  1. In-memory cache 2. SQLite cache 3. Upstream 4. Stale 5. Default
- On successful fetch, store in both memory and SQLite
- On restart, metadata loads from persistent cache (no storm)
- Add metadata_stats to health endpoint

Expected: No symbol-fetch storms on restart, 80%+ cache hit rate.

Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>"

# Phase 5
git add src/apis/price_api.py src/core/price_worker.py
git commit -m "optimization(Phase 5): Cache pre-warming on registration

Enqueue metadata and price warm-up in background when tokens registered.

Changes:
- Modified batch_register() to enqueue LOW-priority warm-up tasks
- Return immediately without blocking on warm-up
- Only warm-up new tokens (not already registered)
- Check queue depth threshold before enqueueing (best-effort)
- Track warm_up_queued/completed/failed metrics

Expected: New tokens have metadata/price within 1-2 seconds of registration.

Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>"
```

---

## Post-Deployment Checklist

### Phase 3
- [ ] Restart services: `./scripts/restart.sh`
- [ ] Monitor source_stats in health endpoint for 30 min
- [ ] Trigger Dexscreener outage (manual test): verify Jupiter used
- [ ] Verify Birdeye is NOT called when Dexscreener succeeds
- [ ] Check logs for "tried_sources" to validate fallback chain
- [ ] Run for 4+ hours, verify no 429 errors in source stats

### Phase 4
- [ ] Verify metadata_cache table created: `sqlite3 flex.db ".tables" | grep metadata`
- [ ] Test symbol endpoint returns `is_fresh: true` from memory cache
- [ ] After 5 min, test returns from persistent cache
- [ ] Restart service: `./scripts/restart.sh`
- [ ] Test symbol endpoint returns from persistent cache immediately
- [ ] Verify no upstream symbol fetches after restart (check logs)

### Phase 5
- [ ] Test batch_register with new tokens
- [ ] Verify warm_up_queued > 0 in response
- [ ] Wait 2-3 seconds, check warm_up_completed > 0
- [ ] Verify UI shows symbol/price within 2 seconds of registration
- [ ] Simulate queue depth > 50: verify warm-up is skipped (graceful)
- [ ] Monitor warm_up_failed: should be < 1% of warm_up_queued

---

## Code Review Checklist

- [ ] BirdeyeClient has 3-second timeout (faster fail)
- [ ] Fallback chain is exactly: Dex → Jupiter → Birdeye → stale
- [ ] No blocking calls in registration path
- [ ] Metadata writes are fire-and-forget (no `raise`)
- [ ] Queue depth check before warm-up (50 token threshold)
- [ ] All new metrics initialized in __init__
- [ ] Health endpoint includes all 3 new stat categories
- [ ] No changes to existing endpoint signatures
- [ ] Backward compatible response format (new fields are additive)
- [ ] Error handling: never crashes on source failure
- [ ] Logging: debug level for fallback chain, info for metrics

---

## Future Optimizations (Post-Phase 5)

1. **Batch metadata writes** — Combine SQLite writes if > 5 tokens
2. **Redis for metadata cache** — Upgrade from SQLite (optional)
3. **Warm-up prioritization** — Queue HIGH-activity tokens first
4. **Source health scoring** — Adjust fallback order based on success %
5. **Metrics export** — Prometheus endpoint for grafana
