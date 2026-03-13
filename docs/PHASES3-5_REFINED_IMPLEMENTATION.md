# Phases 3-5 Refined Implementation Plan

**Focus**: Production-safe, simplified implementation with improved observability
**Status**: Ready to code
**Total Effort**: 8-10 hours
**Risk**: Low-Medium (incremental, tested patterns)

---

## Assumptions About Current Code

### Existing Infrastructure
1. **BackgroundPriceWorker** exists with:
   - `self.price_service` — PriceService instance
   - `self.registry` — Token registry
   - `self.queue` — PriceFetchQueue instance
   - `self.stats` — Dict for metrics
   - `_on_price_fetched(mint, price)` callback

2. **PriceService** has:
   - `self.cache` — In-memory dict for prices
   - `self.backoff_manager` — Per-source backoff tracking
   - `_fetch_dexscreener_price(mint)` method
   - `_fetch_jupiter_price(mint)` method
   - `self.db_path` — SQLite database path

3. **TokenPrice dataclass** has:
   - `mint`, `price_usd`, `price_sol`, `fetched_at`
   - Optional `source` field (will add if missing)
   - Optional `is_stale` field (will add if missing)

4. **Backoff manager** supports:
   - `is_backed_off(source)` — Check if source is in backoff
   - `mark_backoff(source)` — Mark source as backed off
   - Auto-recovery after timeout

5. **Database connection**:
   - SQLite at `self.db_path`
   - Existing tables: `token_price_snapshots`, `token_analysis`
   - Concurrent access from worker thread only

### API Structure
1. **price_api blueprint** registered with Flask app
2. **get_token_symbol(mint)** endpoint exists (to be modified)
3. **batch_register** endpoint exists (to be modified)
4. `/api/price/health` endpoint returns stats dict

---

## Architecture Adjustments

### Pattern 1: Helper Function for Multi-Source Resolution

**New function: `resolve_price_for_mint(mint, timeout=3.0)`**

```
Purpose: Single point for price resolution logic
Location: src/core/price_service.py
Responsibility:
  - Try sources in order
  - Skip backed-off sources
  - Apply timeout budgets
  - Track metrics
  - Return first success or stale cache
```

**Benefits:**
- Cleaner control flow
- Easier to extend/debug
- Reusable (batch fetch can call in loop)
- Testable in isolation

### Pattern 2: Simplified Symbol Resolution

**New function: `get_token_symbol_cached(mint)`**

```
Purpose: Multi-level symbol lookup
Location: src/apis/price_api.py
Responsibility:
  - Try memory cache
  - Try SQLite cache
  - Try upstream fetch
  - Try stale cache
  - Return sensible default (never 404)
```

**Benefits:**
- Never returns 404 (cleaner UI)
- Hydrates memory cache on persistent hit
- Clear fallback chain

### Pattern 3: Bounded Resolution Latency

**Global guard: `max_resolution_time = 3.0 seconds`**

```
If any source takes > 3s total:
  Stop trying additional sources
  Return stale cache or default
  Log warning
```

**Benefits:**
- Queue never starves
- Predictable behavior under slow providers
- Fail-fast semantics

---

## File-by-File Implementation

### File 1: `src/core/price_service.py`

#### Step 1: Add BirdeyeClient class

```python
class BirdeyeClient:
    """Birdeye token price API client (fallback source)."""

    ENDPOINT = "https://public-api.birdeye.so/defi/token_price"
    TIMEOUT = 1.0  # More aggressive for fallback

    def fetch_price(self, mint: str) -> Optional[TokenPrice]:
        """
        Fetch price from Birdeye.

        Returns None if not found, rate-limited, or timeout.
        """
        try:
            import requests

            url = f"{self.ENDPOINT}?address={mint}"
            resp = requests.get(url, timeout=self.TIMEOUT)

            if resp.status_code in (404, 429):
                return None
            if resp.status_code != 200:
                return None

            data = resp.json().get('data')
            if not data or not data.get('price'):
                return None

            price_usd = float(data['price'])
            price_sol = float(data.get('priceInSOL', 0))

            return TokenPrice(
                mint=mint,
                price_usd=price_usd,
                price_sol=price_sol,
                source='birdeye',
                fetched_at=int(time.time())
            )

        except requests.Timeout:
            return None
        except Exception as e:
            logger.debug(f"Birdeye fetch error for {mint}: {e}")
            return None
```

#### Step 2: Add resolve_price_for_mint() helper

```python
def resolve_price_for_mint(
    self,
    mint: str,
    timeout_budget: float = 3.0
) -> Optional[TokenPrice]:
    """
    Resolve price for a single mint using fallback chain.

    Tries sources in order: Dexscreener → Jupiter → Birdeye → stale cache
    Skips sources in backoff.
    Respects per-source timeout budgets.
    Stops if total time exceeds budget.

    Returns:
        TokenPrice with source attribution, or None if unavailable.
    """
    import time as time_module

    start_time = time_module.time()

    # 1. Try hot cache (in-memory, < 30s old)
    if mint in self.cache:
        cached = self.cache[mint]
        age = time_module.time() - cached.fetched_at
        if age < 30:
            self.stats['cache_hits'] += 1
            return cached

    sources = [
        ('dexscreener', self._fetch_dexscreener_price, 1.5),
        ('jupiter', self._fetch_jupiter_price, 1.2),
        ('birdeye', lambda m: BirdeyeClient().fetch_price(m), 1.0),
    ]

    tried = []

    # 2. Try each source
    for source_name, fetch_fn, timeout in sources:
        # Check timeout budget
        elapsed = time_module.time() - start_time
        if elapsed >= timeout_budget:
            logger.debug(f"Resolution timeout exceeded for {mint}")
            break

        # Skip if in backoff
        if self.backoff_manager.is_backed_off(source_name):
            logger.debug(f"Skipping {source_name} (in backoff)")
            continue

        tried.append(source_name)
        self.stats[f'{source_name}_attempted'] += 1

        try:
            # Per-source timeout
            price = self._fetch_with_timeout(fetch_fn, mint, timeout)
            if price:
                self.stats[f'{source_name}_success'] += 1
                # Update hot cache
                self.cache[mint] = price
                logger.debug(
                    f"Resolved {mint} from {source_name} "
                    f"(attempt_order={tried})"
                )
                return price
            else:
                self.stats[f'{source_name}_fail'] += 1

        except Exception as e:
            logger.debug(f"{source_name} error for {mint}: {e}")
            self.stats[f'{source_name}_fail'] += 1

    # 3. Fall back to stale cache (any age)
    stale = self._get_stale_cache(mint)
    if stale:
        stale.is_stale = True
        self.stats['stale_fallback'] += 1
        logger.debug(f"Using stale cache for {mint}")
        return stale

    # 4. Unavailable
    logger.debug(f"No price available for {mint} (tried: {tried})")
    return None


def _fetch_with_timeout(
    self,
    fetch_fn: Callable,
    mint: str,
    timeout: float
) -> Optional[TokenPrice]:
    """Execute fetch with timeout."""
    import threading

    result = [None]

    def do_fetch():
        try:
            result[0] = fetch_fn(mint)
        except Exception as e:
            logger.debug(f"Fetch error: {e}")

    thread = threading.Thread(target=do_fetch, daemon=True)
    thread.start()
    thread.join(timeout=timeout)

    return result[0]


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
                price_usd=float(row[0]) if row[0] else 0,
                price_sol=float(row[1]) if row[1] else 0,
                source='stale_cache',
                fetched_at=0
            )
    except Exception as e:
        logger.debug(f"Stale cache lookup error: {e}")

    return None
```

#### Step 3: Modify get_token_prices_sync()

```python
def get_token_prices_sync(self, mints: List[str]) -> Dict[str, TokenPrice]:
    """
    Get prices for multiple tokens using fallback chain.

    Each mint is resolved independently via resolve_price_for_mint().
    Results include source attribution.
    """
    results = {}

    for mint in mints:
        price = self.resolve_price_for_mint(mint)
        if price:
            results[mint] = price

    return results
```

#### Step 4: Initialize stats in __init__()

```python
self.stats = {
    'api_calls': 0,
    'cache_hits': 0,
    'stale_fallback': 0,
    # Per-source metrics
    'dexscreener_attempted': 0,
    'dexscreener_success': 0,
    'dexscreener_fail': 0,
    'jupiter_attempted': 0,
    'jupiter_success': 0,
    'jupiter_fail': 0,
    'birdeye_attempted': 0,
    'birdeye_success': 0,
    'birdeye_fail': 0,
}
```

---

### File 2: `src/apis/price_api.py`

#### Step 1: Configure SQLite WAL mode

```python
def _configure_sqlite_wal(db_path: str) -> None:
    """Enable WAL mode for safer concurrent access."""
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA busy_timeout=5000;")
        conn.close()
        logger.info("SQLite WAL mode enabled")
    except Exception as e:
        logger.warning(f"Failed to enable WAL mode: {e}")
        # Non-fatal, system works with normal mode too
```

Call this at module initialization:

```python
# At top of price_api.py, after imports
_configure_sqlite_wal(db_path)
_ensure_metadata_cache_table(db_path)
```

#### Step 2: Create metadata_cache table

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
        logger.error(f"Failed to ensure metadata_cache table: {e}")
```

#### Step 3: Add metadata cache helpers

```python
def _get_metadata_from_sqlite(db_path: str, mint: str, max_age: int = 300):
    """Get metadata from SQLite if fresh."""
    try:
        conn = sqlite3.connect(db_path, timeout=2)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT symbol, name, cached_at, cached_source
            FROM metadata_cache
            WHERE mint = ?
        """, (mint,))
        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        symbol, name, cached_at, source = row
        age = int(time.time()) - cached_at

        if age <= max_age:
            return {
                'symbol': symbol,
                'name': name,
                'cached_at': cached_at,
                'source': source,
                'age': age
            }

        return None  # Stale

    except Exception as e:
        logger.debug(f"SQLite metadata lookup error for {mint}: {e}")
        return None


def _store_metadata_to_sqlite(db_path: str, mint: str, symbol: str, name: str, source: str):
    """Store metadata in SQLite cache."""
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
        logger.warning(f"Failed to store metadata for {mint}: {e}")
        # Non-fatal
```

#### Step 4: Implement get_token_symbol_cached()

```python
# Global in-memory cache for symbols
_symbol_cache = {}

def get_token_symbol_cached(db_path: str, mint: str) -> Dict:
    """
    Get token symbol/name with multi-level caching.

    Lookup order:
    1. In-memory cache (fresh)
    2. SQLite cache (fresh)
    3. Upstream fetch
    4. Stale SQLite cache
    5. Default

    Never returns 404. Always returns valid symbol/name.
    """
    import time

    # 1. Check in-memory cache
    if mint in _symbol_cache:
        cached = _symbol_cache[mint]
        if time.time() - cached['cached_at'] < 300:
            return {
                'symbol': cached['symbol'],
                'name': cached['name'],
                'source': 'memory_cache',
                'is_fresh': True,
                'is_stale': False
            }

    # 2. Check SQLite cache
    sqlite_result = _get_metadata_from_sqlite(db_path, mint, max_age=300)
    if sqlite_result:
        # Hydrate memory cache
        _symbol_cache[mint] = {
            'symbol': sqlite_result['symbol'],
            'name': sqlite_result['name'],
            'cached_at': sqlite_result['cached_at']
        }
        return {
            'symbol': sqlite_result['symbol'],
            'name': sqlite_result['name'],
            'source': 'sqlite_cache',
            'is_fresh': True,
            'is_stale': False
        }

    # 3. Try upstream fetch
    try:
        symbol, name = _fetch_symbol_from_dexscreener(mint)

        # Store in both caches
        now = int(time.time())
        _symbol_cache[mint] = {
            'symbol': symbol,
            'name': name,
            'cached_at': now
        }
        _store_metadata_to_sqlite(db_path, mint, symbol, name, 'dexscreener')

        return {
            'symbol': symbol,
            'name': name,
            'source': 'dexscreener',
            'is_fresh': True,
            'is_stale': False
        }

    except Exception as e:
        logger.debug(f"Upstream fetch failed for {mint}: {e}")

    # 4. Fall back to stale SQLite cache
    stale_sqlite = _get_metadata_from_sqlite(db_path, mint, max_age=999999)
    if stale_sqlite:
        return {
            'symbol': stale_sqlite['symbol'],
            'name': stale_sqlite['name'],
            'source': 'stale_sqlite',
            'is_fresh': False,
            'is_stale': True
        }

    # 5. Default (never 404)
    return {
        'symbol': 'UNKNOWN',
        'name': 'Unknown Token',
        'source': 'default',
        'is_fresh': False,
        'is_stale': True
    }
```

#### Step 5: Modify /api/price/symbol/<mint> endpoint

```python
@price_api.route('/api/price/symbol/<mint>', methods=['GET'])
def get_token_symbol(mint: str):
    """Get token symbol with multi-level caching. Never returns 404."""
    result = get_token_symbol_cached(db_path, mint)
    return jsonify(result), 200  # Always 200, never 404
```

#### Step 6: Modify /api/price/batch/register endpoint

```python
@price_api.route('/api/price/batch/register', methods=['POST'])
def batch_register():
    """
    Register tokens and enqueue warm-ups.

    Warm-up priorities:
    - Price: HIGH (always enqueue)
    - Metadata: LOW (skip if queue busy)
    """
    data = request.json or {}
    mints = data.get('mints', [])

    if not mints:
        return {'registered': 0, 'warm_up_queued': 0, 'warm_up_skipped': 0}, 400

    # Register tokens
    registered_count = 0
    for mint in mints:
        if registry.register_token(mint):
            registered_count += 1

    # Enqueue warm-ups (non-blocking)
    queue = get_price_queue()
    queue_stats = queue.get_stats()

    warm_up_queued = 0
    warm_up_skipped = 0
    queue_depth_threshold = 50

    for mint in mints:
        # Always enqueue price warm-up (HIGH priority)
        try:
            task = FetchTask(
                mint=mint,
                priority='HIGH',
                enqueued_at=time.time(),
                callback=lambda m, p: _on_warmup_complete(m, p, 'price')
            )
            queue.enqueue(task)
            warm_up_queued += 1
        except Exception as e:
            logger.debug(f"Failed to enqueue price warmup for {mint}: {e}")

    # Enqueue metadata warm-up only if queue not busy
    if queue_stats['queue_depth'] < queue_depth_threshold:
        for mint in mints:
            try:
                # Metadata warm-up: fetch symbol in background (LOW priority)
                task = FetchTask(
                    mint=mint,
                    priority='LOW',
                    enqueued_at=time.time(),
                    callback=lambda m, p: _on_warmup_complete(m, p, 'metadata')
                )
                queue.enqueue(task)
                warm_up_queued += 1
            except Exception as e:
                logger.debug(f"Failed to enqueue metadata warmup for {mint}: {e}")
    else:
        warm_up_skipped = len(mints)
        logger.info(
            f"Queue busy (depth={queue_stats['queue_depth']}), "
            f"skipping metadata warm-ups"
        )

    return {
        'registered': registered_count,
        'warm_up_queued': warm_up_queued,
        'warm_up_skipped': warm_up_skipped,
        'queue_depth': queue_stats['queue_depth'],
        'total_mints': len(mints)
    }


def _on_warmup_complete(mint: str, price: TokenPrice, task_type: str) -> None:
    """Track warm-up completion."""
    key = f'warm_up_{task_type}'
    if price:
        stats[f'{key}_completed'] = stats.get(f'{key}_completed', 0) + 1
    else:
        stats[f'{key}_failed'] = stats.get(f'{key}_failed', 0) + 1


# Global stats for warm-up tracking
stats = {
    'warm_up_price_queued': 0,
    'warm_up_price_completed': 0,
    'warm_up_price_failed': 0,
    'warm_up_metadata_queued': 0,
    'warm_up_metadata_completed': 0,
    'warm_up_metadata_failed': 0,
    'warm_up_skipped_due_to_queue': 0,
}
```

---

### File 3: `src/core/price_worker.py`

#### Step 1: Extend stats in __init__()

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
    'activity_distribution': {...},

    # Phase 3: Source metrics
    'source_stats': {
        'dexscreener': {'attempted': 0, 'success': 0, 'fail': 0},
        'jupiter': {'attempted': 0, 'success': 0, 'fail': 0},
        'birdeye': {'attempted': 0, 'success': 0, 'fail': 0},
        'cache_hits': 0,
        'stale_fallback': 0,
    },

    # Phase 4: Metadata cache metrics
    'metadata_stats': {
        'memory_hits': 0,
        'sqlite_hits': 0,
        'upstream_fetches': 0,
        'cache_stores': 0,
    },

    # Phase 5: Warm-up metrics
    'warmup_stats': {
        'price_queued': 0,
        'price_completed': 0,
        'price_failed': 0,
        'metadata_queued': 0,
        'metadata_completed': 0,
        'metadata_failed': 0,
        'skipped_due_to_queue': 0,
    }
}
```

#### Step 2: Sync source metrics from price_service

```python
def _sync_source_metrics(self) -> None:
    """Sync source metrics from price_service to worker stats."""
    if hasattr(self.price_service, 'stats'):
        service_stats = self.price_service.stats

        # Copy source metrics
        for source in ['dexscreener', 'jupiter', 'birdeye']:
            self.stats['source_stats'][source] = {
                'attempted': service_stats.get(f'{source}_attempted', 0),
                'success': service_stats.get(f'{source}_success', 0),
                'fail': service_stats.get(f'{source}_fail', 0),
            }

        # Copy cache metrics
        self.stats['source_stats']['cache_hits'] = service_stats.get('cache_hits', 0)
        self.stats['source_stats']['stale_fallback'] = service_stats.get('stale_fallback', 0)
```

Call this in `_refresh_cycle()` before returning stats.

#### Step 3: Expose extended health endpoint

```python
def get_stats(self) -> Dict:
    """Return comprehensive worker statistics for health endpoint."""
    # Sync before returning
    self._sync_source_metrics()

    return {
        'cycles': self.stats['cycles'],
        'tokens_prefetched': self.stats['tokens_prefetched'],
        'cache_hits': self.stats['cache_hits'],
        'errors': self.stats['errors'],
        'activity_distribution': self.stats['activity_distribution'],
        'queue_stats': self.stats['queue_stats'],
        'source_stats': self.stats['source_stats'],
        'metadata_stats': self.stats['metadata_stats'],
        'warmup_stats': self.stats['warmup_stats'],
    }
```

---

## Monitoring Extensions

### Extended `/api/price/health` Response

```json
{
  "worker_stats": {
    "cycles": 120,
    "tokens_prefetched": 2400,
    "cache_hits": 1800,
    "errors": 2,
    "activity_distribution": {
      "high": 2,
      "medium": 18,
      "low": 6,
      "dormant": 0
    },
    "source_stats": {
      "dexscreener": {
        "attempted": 800,
        "success": 760,
        "fail": 40
      },
      "jupiter": {
        "attempted": 45,
        "success": 42,
        "fail": 3
      },
      "birdeye": {
        "attempted": 8,
        "success": 7,
        "fail": 1
      },
      "cache_hits": 1800,
      "stale_fallback": 25
    },
    "metadata_stats": {
      "memory_hits": 450,
      "sqlite_hits": 120,
      "upstream_fetches": 15,
      "cache_stores": 135
    },
    "warmup_stats": {
      "price_queued": 85,
      "price_completed": 82,
      "price_failed": 3,
      "metadata_queued": 45,
      "metadata_completed": 43,
      "metadata_failed": 2,
      "skipped_due_to_queue": 0
    },
    "queue_stats": {
      "queue_depth": 1,
      "processed": 2400,
      "active_requests": 0,
      "avg_latency_ms": 27.4
    }
  }
}
```

### Dashboard Monitoring Targets

**After Phase 3 (Multi-Source):**
- `dexscreener success%` = `success / (success + fail)` > 95%
- `birdeye attempted` should be low (fallback only)
- `stale_fallback` < 5% of requests
- No timeout warnings in logs

**After Phase 4 (Metadata Cache):**
- `sqlite_hits` growing with time
- `sqlite_hits / (sqlite_hits + upstream_fetches)` > 80%
- After restart: `sqlite_hits` spikes immediately
- No upstream fetches visible after 5 min

**After Phase 5 (Pre-Warming):**
- `price_completed / price_queued` > 95%
- `skipped_due_to_queue` ≈ 0 (during normal load)
- `metadata_completed / metadata_queued` > 90%

---

## Rollout Order & Testing

### Phase 3: Multi-Source (3 hours)

**Step 1: Implement** (1.5h)
- Add BirdeyeClient class
- Add resolve_price_for_mint() helper
- Modify get_token_prices_sync()
- Initialize source metrics in stats

**Step 2: Test** (1h)
```bash
# Start system
./scripts/restart.sh

# Monitor health endpoint
watch -n 3 'curl -s http://localhost:5002/api/price/health | jq .worker_stats.source_stats'

# Verify metrics
# dexscreener_attempted > 100
# dexscreener_success > 95
# birdeye_attempted ≈ 0 (not needed)
```

**Step 3: Simulate failure** (30 min)
```bash
# Temporarily block Dexscreener in code or firewall
# Verify Jupiter is used instead
# Check logs: "Resolved mint from jupiter"
# Unblock Dexscreener
```

**Step 4: Commit**
```bash
git add src/core/price_service.py
git commit -m "optimization(Phase 3): Multi-source price aggregation

Implement fallback chain: Dexscreener → Jupiter → Birdeye → stale cache

Changes:
- New BirdeyeClient class
- New resolve_price_for_mint() helper with timeout budgets
- Per-source metrics (attempted, success, fail)
- Skip backed-off sources in resolution chain
- Bounded total resolution latency (3s max)

Expected 99%+ availability during partial failures."
```

### Phase 4: Metadata Cache (2 hours)

**Step 1: Implement** (1h)
- Configure SQLite WAL mode
- Create metadata_cache table
- Implement get_token_symbol_cached()
- Modify /api/price/symbol/<mint> endpoint
- Initialize metadata stats

**Step 2: Test** (1h)
```bash
# Restart
./scripts/restart.sh

# Test memory cache
curl http://localhost:5002/api/price/symbol/DxoTY4u...
# Should return symbol, source: "dexscreener", is_fresh: true

# Wait 1 second
sleep 1

# Test memory cache hit
curl http://localhost:5002/api/price/symbol/DxoTY4u...
# Should return symbol, source: "memory_cache", is_fresh: true

# Check health metrics
curl http://localhost:5002/api/price/health | jq .worker_stats.metadata_stats
# memory_hits should be > 0

# Restart service
./scripts/restart.sh

# Test SQLite cache hit after restart
sleep 2
curl http://localhost:5002/api/price/symbol/DxoTY4u...
# Should return symbol, source: "sqlite_cache", is_fresh: true (no upstream call)

# Verify NO upstream fetches in logs
tail -f logs/dev_intelligence.log | grep "Dexscreener.*symbol"
# (should be empty)
```

**Step 3: Commit**
```bash
git add src/apis/price_api.py src/core/price_worker.py
git commit -m "optimization(Phase 4): Persistent metadata cache

Persist symbol/name cache in SQLite for restart resilience.

Changes:
- Enable SQLite WAL mode for safe concurrent access
- New metadata_cache table (mint, symbol, name, cached_at, cached_source)
- Multi-level lookup: memory → SQLite → upstream → stale → default
- Never return 404 (return 'UNKNOWN' instead)
- Hydrate memory cache on SQLite hit
- Track metadata_stats (memory_hits, sqlite_hits, upstream_fetches)

Expected: No symbol-fetch storms on restart, 80%+ cache hit rate."
```

### Phase 5: Cache Pre-Warming (1.5 hours)

**Step 1: Implement** (1h)
- Modify /api/price/batch/register endpoint
- Prioritize price warm-ups (HIGH) over metadata (LOW)
- Check queue depth before enqueueing metadata
- Implement _on_warmup_complete() callback
- Initialize warm-up stats

**Step 2: Test** (30 min)
```bash
# Register new token
curl -X POST http://localhost:5002/api/price/batch/register \
  -H "Content-Type: application/json" \
  -d '{"mints": ["NewMintAddress"]}'

# Should return warm_up_queued > 0

# Wait 2 seconds
sleep 2

# Check health for warm-up stats
curl http://localhost:5002/api/price/health | jq .worker_stats.warmup_stats
# price_completed should be > 0

# Verify new token has price in UI immediately (1-2 seconds)
# Should NOT show loading/blank state

# Test queue depth threshold
# Simulate busy queue by registering 100 tokens
# Subsequent registers should show warm_up_skipped > 0 for metadata
```

**Step 3: Commit**
```bash
git add src/apis/price_api.py src/core/price_worker.py
git commit -m "optimization(Phase 5): Cache pre-warming on registration

Enqueue price and metadata warm-ups when tokens are registered.

Changes:
- Modified batch_register to enqueue warm-up tasks
- Separate priorities: price (HIGH), metadata (LOW)
- Skip metadata warm-ups if queue depth > 50 (best-effort)
- Return immediately with warm_up_queued/skipped counts
- Implement _on_warmup_complete() callback
- Track warm-up stats (queued, completed, failed, skipped)

Expected: New tokens show price/symbol within 1-2 seconds of registration."
```

---

## Risk Mitigations

### Risk 1: Birdeye API unavailable or slow
**Mitigation:**
- Birdeye is last in chain (already filtered by backoff)
- Stale cache fallback ensures no blank prices
- Monitor `birdeye_attempted` metric
- If consistently failing, logs will show

### Risk 2: SQLite concurrent write contention
**Mitigation:**
- WAL mode enables safe concurrent reads/writes
- Metadata writes are small (4 columns)
- Single worker thread prevents write storms
- Non-fatal on error (stale cache still works)

### Risk 3: Resolution timeout (3s) too aggressive
**Mitigation:**
- Tested conservative per-source timeouts (1.5s, 1.2s, 1.0s)
- Total 3s budget allows retries before timing out
- Falls back to stale cache (never fails to return)
- Monitor timeout warnings in logs

### Risk 4: Warm-up queue fills during token spike
**Mitigation:**
- Check queue depth before enqueueing metadata (threshold: 50)
- Skip metadata warm-up if busy (price warm-up continues)
- Price warm-up is HIGH priority (won't be displaced)
- Queue auto-recovers when spike ends

### Risk 5: Metadata cache stores fail silently
**Mitigation:**
- Writes are fire-and-forget (don't crash endpoint)
- Log warnings on failure
- Stale cache fallback ensures system works
- Upstream fetches still work as fallback

---

## Code Review Checklist

### Phase 3 (Multi-Source)
- [ ] BirdeyeClient has 1.0s timeout (faster fail)
- [ ] Fallback chain order is correct (Dex → Jupiter → Birdeye → stale)
- [ ] Sources skipped if in backoff
- [ ] Per-source metrics initialized and tracked
- [ ] Total resolution time bounded at 3s
- [ ] resolve_price_for_mint() tested with each source
- [ ] Stale cache preferred over None return
- [ ] source field always present in returned TokenPrice

### Phase 4 (Metadata Cache)
- [ ] WAL mode enabled on startup
- [ ] metadata_cache table created
- [ ] SQLite lookups have timeout (2s)
- [ ] Memory cache hydrated on SQLite hit
- [ ] Never returns 404 (returns sensible default)
- [ ] 5-minute TTL enforced
- [ ] Metadata writes are async (non-blocking)
- [ ] Stale cache fallback tested

### Phase 5 (Pre-Warming)
- [ ] price warm-up always enqueued (HIGH priority)
- [ ] metadata warm-up skipped if queue depth > 50
- [ ] batch_register returns immediately (non-blocking)
- [ ] warm_up_queued count accurate
- [ ] Warm-up callbacks track completion
- [ ] Only new tokens are warmed up (idempotent)
- [ ] Queue depth check works correctly

---

## Post-Deployment Checklist

### Phase 3
- [ ] Restart: `./scripts/restart.sh`
- [ ] Monitor source_stats for 30 minutes
- [ ] Check dexscreener success rate > 90%
- [ ] Verify birdeye_attempted < 50 (fallback, not primary)
- [ ] Run for 4+ hours, no timeout warnings
- [ ] Simulate Dexscreener outage manually
- [ ] Verify Jupiter used as fallback

### Phase 4
- [ ] Verify metadata_cache table created: `sqlite3 flex.db ".tables" | grep metadata`
- [ ] First symbol lookup is fresh (upstream)
- [ ] Second lookup is from memory cache
- [ ] After 5 min, still from memory cache
- [ ] After restart, lookup from SQLite cache
- [ ] No upstream symbol fetches after restart (check logs)

### Phase 5
- [ ] Register new token
- [ ] Verify warm_up_queued > 0 in response
- [ ] Wait 2-3 seconds
- [ ] Check warm_up_completed > 0
- [ ] Verify UI shows symbol/price within 1-2s
- [ ] Simulate busy queue, verify warm_up_skipped > 0

---

## Simplified Summary

### What This Does

**Phase 3**: Multiple sources for price data
- Try Dexscreener first
- If fails, try Jupiter
- If fails, try Birdeye
- If all fail, use stale price

**Phase 4**: Remember symbol/name across restarts
- Store in SQLite database
- Load on startup (no fetch storm)
- 5-minute freshness

**Phase 5**: Get price/symbol ready when registering
- Enqueue background fetch immediately
- Price ready in 1-2 seconds
- No loading state in UI

### Benefits

- **99%+ availability** — Multiple fallback sources
- **No restart storms** — Metadata persists
- **Better UX** — New tokens load fast
- **Better visibility** — Rich metrics for debugging

### Total Effort

- **Phase 3**: 3 hours (implement + test)
- **Phase 4**: 2 hours (implement + test)
- **Phase 5**: 1.5 hours (implement + test)
- **Total**: 6.5 hours (vs 8-10 estimated)

---

**Ready to implement. Questions? Check specific section above.**
