# Token Price System Optimization Patch

## Assumptions

1. **Database availability**: `tracked_tokens` table is always accessible with expected schema
2. **Source stability**: Dexscreener/Jupiter may return 429s under load; graceful degradation is acceptable
3. **Cache freshness**: Token symbols are static; 5-minute cache is acceptable for metadata
4. **Frontend persistence**: Frontend can maintain a `Set` of registered mints per session
5. **UI correctness**: Patching rows in-place doesn't break existing event listeners
6. **Backwards compatibility**: Existing API consumers expect `is_stale` and `freshness` fields in responses

---

## Patch Plan

### File: `src/apis/price_api.py`

**Changes:**

1. **Make `/symbol/<mint>` true cache-first**
   - If fresh cache hit (< 5 min), return immediately
   - Only fetch upstream if cache miss or expired
   - On upstream failure, return stale cache + flag
   - Only default to `mint[:8].upper()` if no cache entry exists

2. **Add response metadata for debugging**
   - Include `cached_at`, `fetched_at`, `rate_limited` fields
   - Help frontend distinguish between stale/slow/broken

3. **Make batch registration idempotent**
   - If mint already tracked and active, skip INSERT/UPDATE
   - Log deduped mints for observability
   - Return both `registered` and `deduplicated` counts

---

### File: `src/core/price_worker.py`

**Changes:**

1. **Reduce batch size from 20 to 10**
   - Lower per-call API pressure
   - Allow faster backoff if 429 is triggered

2. **Add HIGH → MEDIUM downgrade logic**
   - Track `first_fetch_at` timestamp
   - Downgrade HIGH to MEDIUM after first successful fetch or after 30–60 seconds max
   - Prevents long-lived HIGH tokens

3. **Cap HIGH priority tokens per cycle**
   - Process max 5 HIGH tokens per cycle
   - Prevent spikes when many launches appear

4. **Add per-source backoff/circuit breaker**
   - Track 429 responses per source (Dexscreener, Jupiter)
   - Exponential backoff: 1s → 2s → 4s → 8s max
   - During backoff: skip source in retry, prefer cache
   - Reset backoff after 5 minutes of clean operation

---

### File: `src/core/price_service.py`

**Changes:**

1. **Prefer stale cache over empty/error**
   - On network error, return last known good value + `is_stale=true`
   - Mark source as `'cached'` for stale responses
   - Include freshness metadata in response

2. **Add source backoff integration**
   - Check backoff state before attempting fetch
   - Skip source if in backoff window
   - Rotate to alternate sources

---

### File: `src/core/main.py`

**Changes:**

1. **Stop re-registering tokens every refresh**
   - Add frontend `registeredMints` Set
   - Only batch-register new mints on first appearance
   - Keep set across price refresh loops

2. **Patch rows in-place instead of full rebuild**
   - Render table once on initial load
   - Update only changed cells: price, market cap, peak, timestamp
   - Preserve scroll position and focus

3. **Split list refresh from price refresh**
   - Token list refresh: 60 seconds
   - Visible price refresh: 15 seconds
   - Symbol load: once per new row

4. **Add loading state discipline**
   - Keep old value visible while fetching new data
   - Show stale badge when `is_stale=true`
   - Avoid blanking cells during update

---

## Code Changes

### 1. `src/apis/price_api.py` — Symbol Endpoint (Cache-First)

**Location: Lines 29–81**

**Before:**
```python
@price_api.route('/symbol/<mint>', methods=['GET'])
def get_token_symbol(mint: str):
    """
    Get token symbol and name via proxy to Dexscreener.
    Avoids CORS issues and rate limiting.

    Returns: {symbol, name}
    """
    import requests

    try:
        # Check cache validity (5 minute TTL)
        cache_ttl = 300
        now = time.time()
        if mint in _metadata_cache and (now - _metadata_cache_time.get(mint, 0)) < cache_ttl:
            return jsonify(_metadata_cache[mint])

        # Always try to fetch fresh data from Dexscreener
        resp = requests.get(
            f'https://api.dexscreener.com/latest/dex/tokens/{mint}',
            timeout=5
        )

        if resp.status_code == 200:
            data = resp.json()
            if data.get('pairs') and len(data['pairs']) > 0:
                base_token = data['pairs'][0].get('baseToken', {})
                result = {
                    'symbol': base_token.get('symbol', mint[:8].upper()),
                    'name': base_token.get('name', 'Token')
                }
                _metadata_cache[mint] = result
                _metadata_cache_time[mint] = now
                return jsonify(result)

        # Fallback: use cached value if available, or default
        if mint in _metadata_cache:
            return jsonify(_metadata_cache[mint])

        result = {'symbol': mint[:8].upper(), 'name': 'Token'}
        _metadata_cache[mint] = result
        _metadata_cache_time[mint] = now
        return jsonify(result), 200

    except Exception as e:
        logger.debug(f"Error fetching metadata for {mint}: {e}")
        # Use cached value if available on error
        if mint in _metadata_cache:
            return jsonify(_metadata_cache[mint])
        result = {'symbol': mint[:8].upper(), 'name': 'Token'}
        _metadata_cache[mint] = result
        _metadata_cache_time[mint] = time.time()
        return jsonify(result), 200
```

**After:**
```python
# Metadata cache timestamps (for staleness detection)
_metadata_cache = {}
_metadata_cache_time = {}
_metadata_fetch_in_progress = set()  # Prevent thundering herd


@price_api.route('/symbol/<mint>', methods=['GET'])
def get_token_symbol(mint: str):
    """
    Get token symbol and name via proxy to Dexscreener.

    Behavior:
    1. Return fresh cache if TTL < 5 min
    2. Fetch fresh if cache miss/expired (non-blocking on miss)
    3. Return stale cache on upstream error
    4. Only default to mint prefix if never cached

    Returns: {symbol, name, cached_at, fetched_at}
    """
    import requests

    cache_ttl = 300  # 5 minutes
    now = time.time()

    # Cache-first: return fresh cached value immediately
    if mint in _metadata_cache:
        cached_time = _metadata_cache_time.get(mint, 0)
        if (now - cached_time) < cache_ttl:
            result = _metadata_cache[mint]
            result['cached_at'] = cached_time
            result['fetched_at'] = cached_time
            result['source'] = 'cache'
            return jsonify(result)

    # Cache miss or expired: attempt fresh fetch (non-blocking)
    # If fetch already in progress for this mint, skip to avoid thundering herd
    if mint not in _metadata_fetch_in_progress:
        _metadata_fetch_in_progress.add(mint)
        try:
            resp = requests.get(
                f'https://api.dexscreener.com/latest/dex/tokens/{mint}',
                timeout=5
            )

            if resp.status_code == 200:
                data = resp.json()
                if data.get('pairs') and len(data['pairs']) > 0:
                    base_token = data['pairs'][0].get('baseToken', {})
                    result = {
                        'symbol': base_token.get('symbol', mint[:8].upper()),
                        'name': base_token.get('name', 'Token')
                    }
                    # Update cache with fresh data
                    _metadata_cache[mint] = result
                    _metadata_cache_time[mint] = now
                    result['cached_at'] = now
                    result['fetched_at'] = now
                    result['source'] = 'dexscreener'
                    return jsonify(result)

            # Upstream returned error but we have stale cache: use it
            if mint in _metadata_cache:
                result = _metadata_cache[mint]
                cached_time = _metadata_cache_time.get(mint, 0)
                result['cached_at'] = cached_time
                result['fetched_at'] = cached_time
                result['source'] = 'cache'
                result['is_stale'] = True
                logger.debug(f"Upstream {resp.status_code} for {mint}; using stale cache")
                return jsonify(result)

        except Exception as e:
            logger.debug(f"Error fetching metadata for {mint}: {e}")
            # On exception, use stale cache if available
            if mint in _metadata_cache:
                result = _metadata_cache[mint]
                cached_time = _metadata_cache_time.get(mint, 0)
                result['cached_at'] = cached_time
                result['fetched_at'] = cached_time
                result['source'] = 'cache'
                result['is_stale'] = True
                return jsonify(result)

        finally:
            _metadata_fetch_in_progress.discard(mint)
    else:
        # Fetch already in progress; if we have stale cache, return it
        if mint in _metadata_cache:
            result = _metadata_cache[mint]
            cached_time = _metadata_cache_time.get(mint, 0)
            result['cached_at'] = cached_time
            result['fetched_at'] = cached_time
            result['source'] = 'cache'
            result['is_stale'] = True
            return jsonify(result)

    # No cache, no fetch available: return default
    result = {
        'symbol': mint[:8].upper(),
        'name': 'Token',
        'cached_at': now,
        'fetched_at': now,
        'source': 'default'
    }
    return jsonify(result)
```

---

### 2. `src/apis/price_api.py` — Batch Register (Idempotent)

**Location: Lines 649–685**

**Before:**
```python
@price_api.route('/batch/register', methods=['POST'])
def register_tokens_batch():
    """
    Register multiple tokens for immediate price tracking.

    Body: {"mints": ["mint1", "mint2", ...]}

    Returns: {"registered": count, "total": count}
    """
    try:
        data = request.get_json()
        mints = data.get('mints', [])

        if not mints or not isinstance(mints, list):
            return jsonify({'error': 'mints must be a non-empty list'}), 400

        if len(mints) > 500:
            return jsonify({'error': 'Maximum 500 mints per request'}), 400

        registry = PriceWorkerRegistry()
        registered = 0

        for mint in mints:
            # Use MEDIUM priority (30s refresh) to avoid rate limiting with large token sets
            # This still provides frequent updates while respecting API rate limits
            if mint and registry.register_token(mint, priority_level='MEDIUM'):
                registered += 1

        return jsonify({
            'registered': registered,
```

**After:**
```python
@price_api.route('/batch/register', methods=['POST'])
def register_tokens_batch():
    """
    Register multiple tokens for price tracking (idempotent).

    If token already tracked and active, skip without error.
    Allows safe repeated calls from frontend without churn.

    Body: {"mints": ["mint1", "mint2", ...]}

    Returns: {"registered": count, "deduplicated": count, "total": count, "skipped": count}
    """
    try:
        data = request.get_json()
        mints = data.get('mints', [])

        if not mints or not isinstance(mints, list):
            return jsonify({'error': 'mints must be a non-empty list'}), 400

        if len(mints) > 500:
            return jsonify({'error': 'Maximum 500 mints per request'}), 400

        registry = PriceWorkerRegistry()
        registered = 0
        deduplicated = 0
        skipped = 0

        for mint in mints:
            if not mint:
                skipped += 1
                continue

            # Check if already registered and active
            existing = registry.get_tracked_tokens()
            is_active = any(t['mint'] == mint and t['is_active'] for t in existing)

            if is_active:
                # Already tracked: no-op
                deduplicated += 1
            else:
                # New token: register with MEDIUM priority (30s refresh)
                # This avoids rate limiting while providing frequent updates
                if registry.register_token(mint, priority_level='MEDIUM'):
                    registered += 1
                else:
                    skipped += 1

        if registered > 0 or deduplicated > 0:
            logger.info(
                f"Batch register: registered={registered}, deduplicated={deduplicated}, "
                f"skipped={skipped}, total={len(mints)}"
            )

        return jsonify({
            'registered': registered,
            'deduplicated': deduplicated,
            'skipped': skipped,
```
**Note**: The rest of the response remains the same (add `deduplicated` and `skipped` to existing response dict).

---

### 3. `src/core/price_worker.py` — Registry and Worker Changes

**Location: Lines 26–210 (Registry and Worker init)**

**After Line 29, modify `__init__` and `_ensure_tables`:**

```python
class PriceWorkerRegistry:
    """Manages the tracked tokens registry."""

    def __init__(self, db_path: str = 'database/flex_complete_database.db'):
        self.db_path = db_path
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        """Create tracked_tokens table with new columns for optimization."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tracked_tokens (
                mint                TEXT PRIMARY KEY,
                symbol              TEXT,
                pair_address        TEXT,
                priority_level      TEXT DEFAULT 'MEDIUM',
                last_price_update   INTEGER DEFAULT 0,
                is_active           BOOLEAN DEFAULT 1,
                created_at          INTEGER NOT NULL,
                updated_at          INTEGER NOT NULL,
                first_fetch_at      INTEGER,
                last_fetch_success_at INTEGER
            )
        """)

        # ... rest of indexes ...
```

**Modify `register_token` method to be idempotent:**

```python
def register_token(self, mint: str, symbol: str = None,
                  pair_address: str = None, priority_level: str = 'MEDIUM') -> bool:
    """Register a token for price tracking (idempotent)."""
    try:
        conn = self._get_conn()
        cursor = conn.cursor()
        now = int(time.time())

        # Check if already registered and active
        cursor.execute("SELECT mint, is_active FROM tracked_tokens WHERE mint = ?", (mint,))
        existing = cursor.fetchone()

        if existing and existing[1]:  # Already active
            # Idempotent: skip update unless priority is being upgraded
            conn.close()
            return True

        # Insert or update (not active → active)
        cursor.execute("""
            INSERT OR REPLACE INTO tracked_tokens
            (mint, symbol, pair_address, priority_level, created_at, updated_at, first_fetch_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (mint, symbol, pair_address, priority_level, now, now, None))

        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Error registering token {mint}: {e}")
        return False
```

**Modify `BackgroundPriceWorker.__init__` to reduce batch size and add backoff tracking:**

```python
class BackgroundPriceWorker:
    """Background worker that continuously refreshes prices with optimizations."""

    def __init__(self, db_path: str = 'database/flex_complete_database.db',
                 interval: int = 10, batch_size: int = 10):  # CHANGED: 20 → 10
        """
        Initialize worker.

        Args:
            db_path: Path to database
            interval: Refresh interval in seconds (default 10)
            batch_size: Tokens per API call (default 10, reduced from 20)
        """
        self.db_path = db_path
        self.interval = interval
        self.batch_size = batch_size
        self.price_service = get_price_service(db_path)
        self.registry = PriceWorkerRegistry(db_path)
        self.running = False
        self.thread = None

        # Per-source backoff tracking
        self.source_backoff = {
            'dexscreener': {'until': 0, 'wait_seconds': 1},
            'jupiter': {'until': 0, 'wait_seconds': 1}
        }

        self.stats = {
            'cycles': 0,
            'tokens_prefetched': 0,
            'api_calls': 0,
            'cache_hits': 0,
            'errors': 0,
            'last_run': None,
            'last_error': None,
            'high_priority_downgrades': 0,  # NEW: track downgrades
            'backoff_events': 0  # NEW: track backoff triggers
        }
```

**Add helper method for HIGH → MEDIUM downgrade:**

```python
def _should_downgrade_to_medium(self, token: Dict) -> bool:
    """Check if HIGH priority token should be downgraded to MEDIUM."""
    now = int(time.time())
    first_fetch_at = token.get('first_fetch_at')
    last_success_at = token.get('last_fetch_success_at')

    # Downgrade if:
    # 1. First fetch was successful (last_fetch_success_at set), OR
    # 2. Token registered > 60 seconds ago (warm-up window passed)
    if last_success_at:
        return True  # Successful fetch: downgrade immediately

    created_at = token.get('created_at', 0)
    if (now - created_at) > 60:
        return True  # 60-second warm-up window passed

    return False

def _downgrade_high_to_medium(self, mint: str) -> bool:
    """Downgrade HIGH → MEDIUM priority token."""
    try:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE tracked_tokens SET priority_level = 'MEDIUM', updated_at = ? WHERE mint = ?",
            (int(time.time()), mint)
        )
        conn.commit()
        conn.close()
        self.stats['high_priority_downgrades'] += 1
        return True
    except Exception as e:
        logger.warning(f"Error downgrading {mint}: {e}")
        return False
```

**Modify `_refresh_cycle` to handle HIGH downgrade and source backoff:**

```python
def _refresh_cycle(self) -> None:
    """One complete refresh cycle with adaptive scheduling and load management."""
    cycle_start = time.time()
    self.stats['cycles'] += 1

    # First, sync new tokens from token_analysis to tracked_tokens
    self._sync_new_tokens()

    # Get tokens to refresh based on adaptive scheduling
    tokens_to_fetch = self._get_tokens_for_refresh()

    # Check and downgrade HIGH → MEDIUM tokens
    high_tokens = self.registry.get_tracked_tokens('HIGH')
    for token in high_tokens:
        if self._should_downgrade_to_medium(token):
            self._downgrade_high_to_medium(token['mint'])

    if not tokens_to_fetch:
        logger.debug("No tracked tokens to refresh")
        return

    # Update source backoff states (decay exponentially)
    now = time.time()
    for source in self.source_backoff:
        if self.source_backoff[source]['until'] > 0:
            if now >= self.source_backoff[source]['until']:
                # Backoff window expired: reset
                self.source_backoff[source]['until'] = 0
                self.source_backoff[source]['wait_seconds'] = 1
                logger.debug(f"Source {source} backoff expired; resuming")

    # Batch fetch prices
    mints = [t['mint'] for t in tokens_to_fetch]
    self._batch_fetch_prices(mints)

    # Update timestamps
    for mint in mints:
        self.registry.update_price_timestamp(mint)

    duration = time.time() - cycle_start
    self.stats['last_run'] = duration
    logger.debug(
        f"Prefetch cycle {self.stats['cycles']}: "
        f"{len(mints)} tokens, {duration:.2f}s, "
        f"{self.stats['api_calls']} API calls, "
        f"downgrades={self.stats['high_priority_downgrades']}"
    )
```

**Modify `_get_tokens_for_refresh` to cap HIGH tokens:**

```python
def _get_tokens_for_refresh(self) -> List[Dict]:
    """
    Get tokens for refresh with capped HIGH priority (max 5 per cycle).

    Schedule:
    - HIGH: every cycle, max 5 tokens (burst protection)
    - MEDIUM: every 3 cycles (30s)
    - LOW: every 20 cycles (200s)
    """
    tokens_to_fetch = []

    # HIGH priority: every cycle but capped at 5 to prevent spikes
    high_priority = self.registry.get_tracked_tokens('HIGH')
    tokens_to_fetch.extend(high_priority[:5])  # CAP: 5 max

    # MEDIUM priority: every 3 cycles (30s)
    if self.stats['cycles'] % 3 == 0:
        medium_priority = self.registry.get_tracked_tokens('MEDIUM')
        tokens_to_fetch.extend(medium_priority[:len(medium_priority)//2])

    # LOW priority: every 20 cycles (200s)
    if self.stats['cycles'] % 20 == 0:
        low_priority = self.registry.get_tracked_tokens('LOW')
        tokens_to_fetch.extend(low_priority[:len(low_priority)//4])

    return tokens_to_fetch
```

**Modify `_batch_fetch_prices` to track first fetch and handle 429s:**

```python
def _batch_fetch_prices(self, mints: List[str]) -> None:
    """Fetch prices in batches with 429 backoff and first-fetch tracking."""
    import sqlite3
    from datetime import datetime

    for i in range(0, len(mints), self.batch_size):
        batch = mints[i:i + self.batch_size]
        try:
            prices = self.price_service.get_token_prices_sync(batch, cache_type='hot')
            self.stats['tokens_prefetched'] += len(prices)
            self.stats['api_calls'] += 1

            # Update peak market cap and track first fetch
            try:
                conn = sqlite3.connect(self.db_path, timeout=5)
                cursor = conn.cursor()

                for mint, price in prices.items():
                    if price.source == 'cached':
                        self.stats['cache_hits'] += 1

                    # Mark successful fetch
                    cursor.execute(
                        "UPDATE tracked_tokens SET last_fetch_success_at = ? WHERE mint = ?",
                        (int(time.time()), mint)
                    )

                    # Rest of existing market cap logic...
                    market_cap = price.market_cap if price.market_cap else 0

                    cursor.execute(
                        "SELECT market_cap_highest, market_cap_highest_at, created_at FROM token_analysis WHERE mint = ?",
                        (mint,)
                    )
                    row = cursor.fetchone()
                    peak_mc = row[0] if row and row[0] else None
                    peak_mc_at = row[1] if row and row[1] else None

                    now = datetime.now().isoformat(sep=' ')

                    if market_cap > 0:
                        if peak_mc is None:
                            cursor.execute(
                                """UPDATE token_analysis
                                   SET price_current = ?, market_cap_current = ?,
                                       market_cap_highest = ?, market_cap_highest_at = ?
                                   WHERE mint = ?""",
                                (price.price_usd, market_cap, market_cap, now, mint)
                            )
                        elif market_cap > peak_mc:
                            cursor.execute(
                                """UPDATE token_analysis
                                   SET price_current = ?, market_cap_current = ?,
                                       market_cap_highest = ?, market_cap_highest_at = ?
                                   WHERE mint = ?""",
                                (price.price_usd, market_cap, market_cap, now, mint)
                            )
                        else:
                            cursor.execute(
                                """UPDATE token_analysis
                                   SET price_current = ?, market_cap_current = ?
                                   WHERE mint = ?""",
                                (price.price_usd, market_cap, mint)
                            )

                conn.commit()
                conn.close()
            except Exception as e:
                logger.warning(f"Error updating peak market cap: {e}")

        except Exception as e:
            logger.error(f"Batch fetch error: {e}")

            # Check if 429 (rate limited) - if so, trigger backoff
            if '429' in str(e) or 'rate' in str(e).lower():
                logger.warning(f"Rate limit triggered; activating backoff")
                self.stats['backoff_events'] += 1

                # Activate backoff for both sources (be conservative)
                now = time.time()
                for source in self.source_backoff:
                    wait = self.source_backoff[source]['wait_seconds']
                    self.source_backoff[source]['until'] = now + wait
                    # Exponential backoff: 1s → 2s → 4s → 8s (cap at 8)
                    self.source_backoff[source]['wait_seconds'] = min(
                        wait * 2, 8
                    )

            self.stats['errors'] += 1
            self.stats['last_error'] = str(e)
```

---

### 4. `src/core/price_service.py` — Backoff-Aware Fetching

**Add source backoff tracking near top of file (after imports):**

```python
# Per-source backoff state (shared with price_worker)
_source_backoff = {
    'dexscreener': {'until': 0, 'wait_seconds': 1},
    'jupiter': {'until': 0, 'wait_seconds': 1}
}

def _get_source_backoff():
    """Get shared backoff state (called by worker and service)."""
    return _source_backoff

def _is_source_backed_off(source: str) -> bool:
    """Check if source is currently in backoff window."""
    backoff = _source_backoff.get(source, {})
    until = backoff.get('until', 0)
    return time.time() < until
```

**Modify `get_token_prices_sync` to respect backoff:**

```python
def get_token_prices_sync(self, mints: List[str], cache_type: str = 'hot') -> Dict[str, TokenPrice]:
    """
    Fetch prices for multiple tokens synchronously.

    Respects per-source backoff: if source is in backoff, skip it and
    prefer cached prices or other sources.
    """
    result = {}

    for mint in mints:
        # Try cache first
        cached = self._get_cached_price(mint, cache_type)
        if cached:
            result[mint] = cached
            continue

        # Cache miss: try to fetch
        # Prefer sources not in backoff
        sources_to_try = ['dexscreener', 'jupiter']
        price = None

        for source in sources_to_try:
            if _is_source_backed_off(source):
                logger.debug(f"Skipping {source} for {mint} (in backoff)")
                continue

            try:
                if source == 'dexscreener':
                    price = DexscreenerClient.get_price(mint)
                elif source == 'jupiter':
                    price = JupiterClient.get_price(mint)

                if price:
                    self.cache.set(mint, price)
                    self._store_snapshot(mint, price, source)
                    result[mint] = price
                    break
            except Exception as e:
                logger.debug(f"Error fetching from {source} for {mint}: {e}")

        # If fetch failed, use very old cache if available (mark stale)
        if not price:
            # Try to get oldest cached version
            if mint in self.cache.cache:
                old_price, _ = self.cache.cache[mint]
                old_price.is_stale = True
                old_price.source = 'cached'
                result[mint] = old_price
            else:
                # No data at all: mark unavailable
                result[mint] = TokenPrice(
                    mint=mint,
                    price_usd=0,
                    price_sol=0,
                    liquidity_usd=0,
                    volume_24h=0,
                    market_cap=0,
                    source='unavailable',
                    is_stale=True
                )

    return result
```

---

### 5. `src/core/main.py` — Dashboard Optimization

**Add global tracking for registered mints (near top of price-related JS):**

```javascript
// Global token tracking
const registeredMints = new Set();  // Tracks already-registered tokens
let tokenListRefreshInterval = null;
let priceRefreshInterval = null;

// Cache for row elements (for in-place updates)
const rowByMint = new Map();
```

**Modify `loadTokens()` to not re-register and to build row map:**

```javascript
async function loadTokens() {
    try {
        const response = await fetch('/api/token/list');
        if (!response.ok) return;

        const tokens = await response.json();
        const minMarketCap = 2000;

        // Filter to displayed tokens
        const filtered = tokens.filter(t =>
            (t.market_cap_current >= minMarketCap) || !t.market_cap_current
        );
        const display = filtered.slice(0, 25);

        // Render table (once on initial load)
        if (rowByMint.size === 0) {
            const html = `
                <table class="token-table">
                    <thead>
                        <tr>
                            <th>Name</th>
                            <th>Price</th>
                            <th>Market Cap</th>
                            <th>Peak Market Cap</th>
                            <th>Liquidity</th>
                        </tr>
                    </thead>
                    <tbody id="tokens-tbody">
                    </tbody>
                </table>
            `;
            document.getElementById('tokens-container').innerHTML = html;
        }

        // Register new mints only (not already registered)
        const newMints = display
            .map(t => t.mint)
            .filter(mint => !registeredMints.has(mint));

        if (newMints.length > 0) {
            const regResponse = await fetch('/api/price/batch/register', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ mints: newMints })
            });
            if (regResponse.ok) {
                newMints.forEach(mint => registeredMints.add(mint));
                logger.debug(`Registered ${newMints.length} new tokens`);
            }
        }

        // Upsert rows (render new ones, keep existing)
        const tbody = document.getElementById('tokens-tbody');
        display.forEach(token => {
            let row = rowByMint.get(token.mint);
            if (!row) {
                // New row: create and insert
                row = document.createElement('tr');
                row.id = `token-row-${token.mint}`;
                row.innerHTML = `
                    <td id="symbol-${token.mint}">...</td>
                    <td id="price-${token.mint}">$0</td>
                    <td id="mc-${token.mint}">$0</td>
                    <td id="peak-mc-${token.mint}">$0</td>
                    <td id="liquidity-${token.mint}">$0</td>
                `;
                tbody.appendChild(row);
                rowByMint.set(token.mint, row);

                // Load symbol once for new row
                loadSymbol(token.mint);
            }
        });

    } catch (error) {
        logger.error(`Error loading tokens: ${error}`);
    }
}
```

**Add price patching function:**

```javascript
function patchTokenPrice(mint, priceData) {
    """Update a token row in place with new price data."""
    const row = rowByMint.get(mint);
    if (!row) return;

    // Update price with fade effect
    const priceEl = row.querySelector(`#price-${mint}`);
    if (priceEl && priceData.price_usd) {
        const newText = `$${priceData.price_usd.toFixed(8)}`;
        if (priceEl.textContent !== newText) {
            priceEl.style.opacity = '0.5';
            priceEl.textContent = newText;
            setTimeout(() => { priceEl.style.opacity = '1'; }, 10);
        }
    }

    // Update market cap
    const mcEl = row.querySelector(`#mc-${mint}`);
    if (mcEl && priceData.market_cap) {
        const newText = '$' + formatMarketCap(priceData.market_cap);
        if (mcEl.textContent !== newText) {
            mcEl.style.opacity = '0.5';
            mcEl.textContent = newText;
            setTimeout(() => { mcEl.style.opacity = '1'; }, 10);
        }
    }

    // Mark if stale
    if (priceData.is_stale) {
        row.classList.add('stale');
    } else {
        row.classList.remove('stale');
    }
}
```

**Replace price refresh loop:**

```javascript
// Separate loops: list refresh (60s) and price refresh (15s)

if (tokenListRefreshInterval) clearInterval(tokenListRefreshInterval);
tokenListRefreshInterval = setInterval(loadTokens, 60000);  // 60 seconds

if (priceRefreshInterval) clearInterval(priceRefreshInterval);
priceRefreshInterval = setInterval(refreshVisiblePrices, 15000);  // 15 seconds

async function refreshVisiblePrices() {
    """Refresh prices only for visible rows (not list structure)."""
    const mints = Array.from(rowByMint.keys());
    if (!mints.length) return;

    try {
        const response = await fetch('/api/price/batch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mints, cache_type: 'hot' })
        });

        if (response.ok) {
            const prices = await response.json();
            for (const [mint, priceData] of Object.entries(prices)) {
                patchTokenPrice(mint, priceData);
            }
        }
    } catch (error) {
        logger.error(`Error refreshing prices: ${error}`);
    }
}

// Initial load
loadTokens();
```

**Add CSS for stale indicator:**

```javascript
// Add to page CSS or inline style tag
const styleEl = document.createElement('style');
styleEl.textContent = `
    tr.stale {
        opacity: 0.8;
        background-color: rgba(255, 200, 0, 0.05);
    }
    tr.stale::after {
        content: ' [stale]';
        font-size: 0.8em;
        color: #ff9800;
        margin-left: 0.5em;
    }
`;
document.head.appendChild(styleEl);
```

---

## Why This Fixes the 429s and UI Slowness

### 429 Errors
1. **Batch size reduced (20 → 10)**: Smaller bursts mean less API pressure per call
2. **HIGH priority downgraded faster**: Warm-up window (60s max) + first-fetch trigger means tokens don't stay in heavy refresh forever
3. **Source backoff on 429**: Once triggered, the system backs off exponentially (1s → 2s → 4s → 8s) and skips that source, preferring cache
4. **No repeated batch registrations**: Frontend only registers new mints once, eliminating duplicate registration traffic

### UI Slowness
1. **Cache-first metadata**: Symbol endpoint returns immediately from cache instead of always fetching upstream
2. **Patch rows instead of rebuild**: Updating only changed cells instead of re-rendering the whole table
3. **Separate list/price refresh**: Token list refreshes slowly (60s); prices refresh frequently (15s) without list churn
4. **Stale-but-visible**: During backoff or network issues, old values stay visible + stale badge instead of blanking
5. **One-time symbol load**: Symbols load once per new row; not reloaded every 30 seconds

### Additional Benefits
- **Idempotent registration**: Frontend can safely re-call batch/register without harm
- **Better observability**: Response includes `cached_at`, `fetched_at`, `source`, `is_stale` fields
- **Safer degradation**: System prefers old data over blank cells when upstream fails
- **Reduced memory churn**: Fewer full table rebuilds = less DOM work

---

## Optional Next Steps

1. **Persistent metadata cache**: Write metadata to indexeddb in browser (survives page reload)
2. **Adaptive refresh intervals**: If 429 detected, reduce refresh frequency for 5 minutes
3. **Multi-page token list**: Paginate displayed tokens to reduce initial load
4. **Server-side deduplication**: Track request fingerprints to detect/throttle client hammering
5. **Stale-while-revalidate pattern**: Return stale data immediately, fetch fresh in background

