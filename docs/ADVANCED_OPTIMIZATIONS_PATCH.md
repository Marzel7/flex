# Advanced Token Price System Optimizations

Extends the core optimization patch with request queuing, activity-based scheduling, concurrency limits, multi-source aggregation, and persistence.

---

## Assumptions

1. **SQLite scalability acceptable** — Up to 1000 tracked tokens; if more, migrate to PostgreSQL later
2. **Async-compatible codebase** — Price service can use asyncio; existing code paths support it
3. **No Redis available initially** — Use SQLite for cache persistence; Redis optional upgrade
4. **Backwards compatibility required** — Existing API endpoints must remain unchanged
5. **Deployment capacity** — Can add one more background thread/process for queue worker
6. **Volume data available** — Token analysis table includes `volume_24h` and `volume_5m` fields
7. **Simple is better** — Prefer stdlib + asyncio over complex frameworks

---

## Architecture Improvements

### Current State (After Core Optimization)

```
Token Analysis (DB)
    ↓
Price Worker (1 thread, 10s cycles)
    ├─ Sync new tokens
    ├─ Batch tokens by priority
    ├─ Fetch prices (batch size 10)
    └─ Update DB
    ↓
Dashboard (polls every 15s)
```

**Problem:** Still bursts 10 tokens per API call; all sources are primary

### Improved State (After Advanced Optimization)

```
Token Analysis (DB) + Activity Signals
    ↓
Price Worker (1 thread, 10s cycles)
    ├─ Sync new tokens
    ├─ Compute activity scores
    ├─ Schedule by activity + priority
    └─ Enqueue to price fetch queue
    ↓
Price Fetch Queue (async, smoothed)
    ├─ Rate limiter (3 parallel, 200ms between)
    ├─ Multi-source aggregation (Dexscreener → Jupiter → Birdeye → cache)
    ├─ Fetch price + metadata
    └─ Update cache + DB
    ↓
Metadata Cache (Redis or SQLite, persistent)
    ├─ Symbol/name (5 min TTL, persistent)
    └─ Survives restarts
    ↓
Dashboard (polls every 15s OR WebSocket push)
```

**Benefits:**
- Smooth API traffic (3 parallel instead of 10 burst)
- Smart scheduling (dormant tokens refresh 5x less)
- Multi-source fallback (fewer timeouts)
- Persistent cache (no symbol storms on restart)
- Optional real-time updates (WebSocket)

---

## Implementation Plan

### Phase 1: Request Queue + Concurrency (4 hours)

**New file:** `src/core/price_fetch_queue.py`

**Changes to:** `src/core/price_worker.py`

**Goals:**
- Move from burst batch calls to smooth queue
- Add concurrency limiter (3 parallel max)
- Rate limit between requests (200ms)
- Track queue depth and throughput

**Impact:**
- API bursts eliminated
- Smoother upstream traffic
- 30% fewer 429 errors expected

---

### Phase 2: Activity-Based Scheduling (3 hours)

**Changes to:** `src/core/price_worker.py`

**Goals:**
- Compute activity score from volume, price change, liquidity
- Schedule refresh intervals dynamically
- Reduce API calls for dormant tokens by 5x

**Impact:**
- 20-30% fewer API calls
- Resources focused on active tokens
- Same data freshness for active tokens

---

### Phase 3: Multi-Source Aggregation (3 hours)

**Changes to:** `src/core/price_service.py`

**Goals:**
- Add Birdeye API client
- Implement fallback chain (Dex → Jupiter → Birdeye → cache)
- Use first successful source
- Track source reliability

**Impact:**
- 99%+ availability (was ~95%)
- Fewer timeouts and missing data
- Better resilience to single-source failures

---

### Phase 4: Metadata Cache Persistence (2 hours)

**Changes to:** `src/apis/price_api.py`, new SQLite table

**Goals:**
- Store symbol/name cache in SQLite with timestamps
- Survive server restarts
- Pre-warm from DB on startup

**Impact:**
- No symbol-fetch storms on restart
- Faster first dashboard load
- Cache survives process crashes

---

### Phase 5: Pre-Warm Cache on Register (1 hour)

**Changes to:** `src/apis/price_api.py`, `src/core/price_worker.py`

**Goals:**
- Queue metadata + price fetch when token registered
- Return immediately, fetch in background
- Reduce user-perceived latency

**Impact:**
- First dashboard load faster
- Better UX for new token launches

---

### Phase 6: Streaming Price Updates (Optional, 4 hours)

**New file:** `src/apis/price_streaming.py`

**Changes to:** `src/core/main.py`, `templates/flex_dashboard.html`

**Goals:**
- Add WebSocket or SSE endpoint
- Push prices when updated
- Optional: clients can poll or listen

**Impact:**
- Real-time updates (15s → <100ms)
- Reduced polling load
- Smoother UI

**Note:** Keep polling as fallback for compatibility

---

### Phase 7: Long-Term Architecture (Future)

**Separate service:** `price-collector-service`

**Goals:**
- Dedicated service for all price fetching
- API server reads from Redis only
- Horizontally scalable

**Note:** Out of scope for this patch; documented for roadmap

---

## Code Examples

### 1. Price Fetch Queue (`src/core/price_fetch_queue.py`)

```python
"""
Token price fetch queue with concurrency limits and rate limiting.

Replaces burst batch calls with smooth, rate-limited requests.

Architecture:
1. Worker enqueues tokens to fetch
2. Queue manager limits concurrency (default 3)
3. Rate limiter delays between requests (default 200ms)
4. Fetch workers pull from queue and call price service
5. Results stored in cache and DB
"""

import asyncio
import time
import logging
from typing import List, Dict, Optional, Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class FetchTask:
    """A single token to fetch."""
    mint: str
    priority: str  # HIGH, MEDIUM, LOW
    enqueued_at: float
    callback: Optional[Callable] = None  # Called with (mint, price) after fetch


class PriceFetchQueue:
    """
    Manages token price fetching with concurrency limits and rate limiting.

    Smooths API traffic: instead of fetching 10 tokens simultaneously (burst),
    fetches 3 at a time with 200ms delays between requests.
    """

    def __init__(self, max_concurrent: int = 3, request_delay_ms: int = 200):
        """
        Initialize queue.

        Args:
            max_concurrent: Max simultaneous fetches (default 3)
            request_delay_ms: Delay between requests (default 200ms)
        """
        self.max_concurrent = max_concurrent
        self.request_delay_ms = request_delay_ms / 1000.0  # Convert to seconds
        self.queue = asyncio.Queue()
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.stats = {
            'enqueued': 0,
            'processed': 0,
            'failed': 0,
            'queue_depth': 0,
            'last_request_at': 0
        }

    async def enqueue(self, task: FetchTask) -> None:
        """Add task to queue."""
        await self.queue.put(task)
        self.stats['enqueued'] += 1
        self.stats['queue_depth'] = self.queue.qsize()
        logger.debug(f"Enqueued {task.mint} (priority {task.priority})")

    async def enqueue_batch(self, tasks: List[FetchTask]) -> None:
        """Add multiple tasks to queue."""
        for task in tasks:
            await self.enqueue(task)

    async def worker(self, fetch_fn: Callable) -> None:
        """
        Worker coroutine that processes queue.

        Args:
            fetch_fn: Async function(mint: str) -> price: TokenPrice

        Should be run with: asyncio.create_task(queue.worker(fetch_price))
        """
        while True:
            try:
                # Get task from queue with timeout (prevent hanging)
                task = await asyncio.wait_for(self.queue.get(), timeout=1.0)

                # Acquire semaphore (limit concurrent fetches)
                async with self.semaphore:
                    # Rate limit: delay between requests
                    now = time.time()
                    time_since_last = now - self.stats['last_request_at']
                    if time_since_last < self.request_delay_ms:
                        await asyncio.sleep(self.request_delay_ms - time_since_last)

                    # Fetch price
                    try:
                        price = await fetch_fn(task.mint)
                        self.stats['processed'] += 1

                        # Call callback if provided
                        if task.callback:
                            task.callback(task.mint, price)

                        logger.debug(
                            f"Fetched {task.mint}: ${price.price_usd} "
                            f"(latency {time.time() - task.enqueued_at:.2f}s)"
                        )

                    except Exception as e:
                        logger.error(f"Error fetching {task.mint}: {e}")
                        self.stats['failed'] += 1

                    self.stats['last_request_at'] = time.time()
                    self.stats['queue_depth'] = self.queue.qsize()

                self.queue.task_done()

            except asyncio.TimeoutError:
                # No task available; keep waiting
                continue
            except Exception as e:
                logger.error(f"Worker error: {e}")
                await asyncio.sleep(1)

    def get_stats(self) -> Dict:
        """Return queue statistics."""
        return self.stats.copy()


# Global queue instance
_price_queue: Optional[PriceFetchQueue] = None


def get_price_queue() -> PriceFetchQueue:
    """Get or create global price fetch queue."""
    global _price_queue
    if _price_queue is None:
        _price_queue = PriceFetchQueue(max_concurrent=3, request_delay_ms=200)
    return _price_queue


async def start_price_queue_worker(fetch_fn: Callable) -> None:
    """Start the price fetch queue worker."""
    queue = get_price_queue()
    await queue.worker(fetch_fn)
```

---

### 2. Activity-Based Scheduling (Changes to `src/core/price_worker.py`)

**Add method to `BackgroundPriceWorker`:**

```python
def _compute_activity_score(self, token: Dict) -> str:
    """
    Compute activity score for a token.

    Returns: 'high', 'medium', 'low', 'dormant'

    Based on:
    - Volume (24h and 5m)
    - Price change (% since last check)
    - Liquidity changes
    """
    try:
        import sqlite3
        conn = sqlite3.connect(self.db_path, timeout=5)
        cursor = conn.cursor()

        mint = token['mint']

        # Get volume and recent price data
        cursor.execute("""
            SELECT volume_24h, price_current, market_cap_current,
                   market_cap_highest
            FROM token_analysis
            WHERE mint = ?
        """, (mint,))
        row = cursor.fetchone()
        conn.close()

        if not row:
            return 'medium'  # Default for unknown tokens

        volume_24h = row[0] or 0
        price_current = row[1] or 0
        market_cap = row[2] or 0
        peak_mc = row[3] or 0

        # Score based on multiple signals
        score = 0

        # Volume signal (50 points max)
        if volume_24h > 1000000:  # > $1M volume
            score += 50
        elif volume_24h > 100000:
            score += 30
        elif volume_24h > 10000:
            score += 15
        else:
            score += 5

        # Market cap signal (30 points max)
        if market_cap > peak_mc * 0.8:  # Still near peak
            score += 30
        elif market_cap > peak_mc * 0.5:
            score += 15
        else:
            score += 5

        # Recency signal (20 points max)
        now = int(time.time())
        created_at = token.get('created_at', 0)
        age_seconds = now - created_at
        if age_seconds < 300:  # < 5 min old (new launch)
            score += 20
        elif age_seconds < 3600:  # < 1 hour
            score += 10
        else:
            score += 2

        # Map score to activity level
        if score >= 80:
            return 'high'      # 5-10s refresh
        elif score >= 50:
            return 'medium'    # 20-30s refresh
        elif score >= 25:
            return 'low'       # 1-2 min refresh
        else:
            return 'dormant'   # 3-5 min refresh

    except Exception as e:
        logger.warning(f"Error computing activity for {token.get('mint')}: {e}")
        return 'medium'  # Safe default


def _get_tokens_for_refresh_activity_based(self) -> List[Dict]:
    """
    Get tokens for refresh with activity-based scheduling.

    Replace static HIGH/MEDIUM/LOW with dynamic activity-based intervals.

    Refresh intervals:
    - high activity: every cycle (10s)
    - medium activity: every 2-3 cycles (20-30s)
    - low activity: every 6-10 cycles (60-100s)
    - dormant: every 20-30 cycles (200-300s)
    """
    tokens_to_fetch = []

    # Get all active tokens
    all_tokens = self.registry.get_tracked_tokens(active_only=True)

    for token in all_tokens:
        mint = token['mint']
        activity = self._compute_activity_score(token)

        # Schedule based on activity
        should_refresh = False

        if activity == 'high':
            # Every cycle
            should_refresh = True
        elif activity == 'medium':
            # Every 2-3 cycles (20-30s)
            should_refresh = (self.stats['cycles'] % 3) == 0
        elif activity == 'low':
            # Every 6-10 cycles (60-100s)
            should_refresh = (self.stats['cycles'] % 8) == 0
        elif activity == 'dormant':
            # Every 20-30 cycles (200-300s)
            should_refresh = (self.stats['cycles'] % 25) == 0

        if should_refresh:
            tokens_to_fetch.append(token)

    return tokens_to_fetch[:20]  # Limit batch size
```

**Modify `_refresh_cycle()` to use activity-based scheduling:**

```python
def _refresh_cycle(self) -> None:
    """Refresh cycle with activity-based scheduling and queue."""
    cycle_start = time.time()
    self.stats['cycles'] += 1

    # Sync new tokens
    self._sync_new_tokens()

    # Get tokens for refresh (activity-based instead of priority-based)
    tokens_to_fetch = self._get_tokens_for_refresh_activity_based()

    if not tokens_to_fetch:
        logger.debug("No tokens to refresh")
        return

    # Instead of direct batch fetch, enqueue to price fetch queue
    queue = get_price_queue()
    for token in tokens_to_fetch:
        task = FetchTask(
            mint=token['mint'],
            priority=token['priority_level'],
            enqueued_at=time.time(),
            callback=self._on_price_fetched
        )
        asyncio.create_task(queue.enqueue(task))

    duration = time.time() - cycle_start
    self.stats['last_run'] = duration
    logger.debug(
        f"Cycle {self.stats['cycles']}: "
        f"enqueued {len(tokens_to_fetch)} tokens "
        f"(queue depth: {queue.get_stats()['queue_depth']})"
    )


def _on_price_fetched(self, mint: str, price: 'TokenPrice') -> None:
    """Callback when price is fetched from queue."""
    try:
        self.registry.update_price_timestamp(mint)
        self.stats['tokens_prefetched'] += 1
    except Exception as e:
        logger.error(f"Error in price fetch callback for {mint}: {e}")
```

---

### 3. Multi-Source Aggregation (Changes to `src/core/price_service.py`)

**Add Birdeye client:**

```python
class BirdeyeClient:
    """Fetches prices from Birdeye API (fallback source)."""

    BASE_URL = "https://public-api.birdeye.so/defi/token_price"

    @staticmethod
    async def get_price(mint: str) -> Optional[TokenPrice]:
        """Fetch token price from Birdeye."""
        try:
            params = {'address': mint}
            # Note: Birdeye requires API key; use public API if available
            # or skip if key not configured

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    BirdeyeClient.BASE_URL,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status != 200:
                        return None

                    data = await resp.json()
                    if not data.get('data'):
                        return None

                    token_data = data['data']
                    price_usd = float(token_data.get('value', 0))

                    if price_usd <= 0:
                        return None

                    return TokenPrice(
                        mint=mint,
                        price_usd=price_usd,
                        price_sol=0,  # Not provided by Birdeye
                        liquidity_usd=0,
                        volume_24h=0,
                        market_cap=0,
                        source='birdeye',
                        timestamp=int(time.time()),
                        is_stale=False
                    )

        except Exception as e:
            logger.debug(f"Birdeye fetch error for {mint}: {e}")
            return None
```

**Modify `get_token_prices_sync()` for fallback chain:**

```python
def get_token_prices_sync(self, mints: List[str], cache_type: str = 'hot') -> Dict[str, TokenPrice]:
    """
    Fetch prices with multi-source fallback chain.

    Priority order:
    1. Hot cache (< 10s for hot, < 30s for org)
    2. Dexscreener
    3. Jupiter
    4. Birdeye (if configured)
    5. Stale cache
    6. Mark unavailable
    """
    result = {}
    sources_used = {'dexscreener': 0, 'jupiter': 0, 'birdeye': 0, 'cached': 0}

    for mint in mints:
        price = None

        # 1. Try cache first
        cached = self._get_cached_price(mint, cache_type)
        if cached and not _is_source_backed_off('dexscreener'):
            result[mint] = cached
            sources_used['cached'] += 1
            continue

        # 2-4. Try sources in order (skip if in backoff)
        sources_to_try = []
        if not _is_source_backed_off('dexscreener'):
            sources_to_try.append(('dexscreener', DexscreenerClient.get_price))
        if not _is_source_backed_off('jupiter'):
            sources_to_try.append(('jupiter', JupiterClient.get_price))

        # Always include Birdeye as fallback (less aggressive API)
        sources_to_try.append(('birdeye', BirdeyeClient.get_price))

        for source_name, source_func in sources_to_try:
            try:
                price = source_func(mint)
                if price:
                    self.cache.set(mint, price)
                    self._store_snapshot(mint, price, source_name)
                    result[mint] = price
                    sources_used[source_name] += 1
                    break
            except Exception as e:
                logger.debug(f"Error fetching from {source_name} for {mint}: {e}")

        # 5. Fallback to stale cache if all sources failed
        if not price:
            if mint in self.cache.cache:
                old_price, _ = self.cache.cache[mint]
                old_price.is_stale = True
                old_price.source = 'cached'
                result[mint] = old_price
                sources_used['cached'] += 1
            else:
                # 6. Mark unavailable
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

    logger.debug(f"Batch fetch: {sources_used}")
    return result
```

---

### 4. Metadata Cache Persistence (Changes to `src/apis/price_api.py`)

**Add persistent cache table:**

```python
def _ensure_metadata_cache_table():
    """Create persistent metadata cache table in SQLite."""
    try:
        conn = sqlite3.connect('database/flex_complete_database.db')
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS metadata_cache (
                mint            TEXT PRIMARY KEY,
                symbol          TEXT NOT NULL,
                name            TEXT NOT NULL,
                cached_at       INTEGER NOT NULL,
                cached_source   TEXT
            )
        """)

        conn.commit()
        conn.close()
        logger.info("Metadata cache table initialized")
    except Exception as e:
        logger.warning(f"Error creating metadata cache table: {e}")


def _get_cached_metadata(mint: str, max_age_seconds: int = 300) -> Optional[Dict]:
    """Get metadata from persistent cache if fresh."""
    try:
        conn = sqlite3.connect('database/flex_complete_database.db')
        cursor = conn.cursor()

        cursor.execute(
            "SELECT symbol, name, cached_at FROM metadata_cache WHERE mint = ?",
            (mint,)
        )
        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        symbol, name, cached_at = row
        age = int(time.time()) - cached_at

        if age < max_age_seconds:
            return {
                'symbol': symbol,
                'name': name,
                'cached_at': cached_at,
                'source': 'persistent_cache'
            }

        return None
    except Exception as e:
        logger.debug(f"Error reading metadata cache for {mint}: {e}")
        return None


def _store_metadata_cache(mint: str, symbol: str, name: str, source: str = 'dexscreener'):
    """Store metadata in persistent cache."""
    try:
        conn = sqlite3.connect('database/flex_complete_database.db')
        cursor = conn.cursor()

        cursor.execute("""
            INSERT OR REPLACE INTO metadata_cache
            (mint, symbol, name, cached_at, cached_source)
            VALUES (?, ?, ?, ?, ?)
        """, (mint, symbol, name, int(time.time()), source))

        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug(f"Error storing metadata cache for {mint}: {e}")


@price_api.route('/symbol/<mint>', methods=['GET'])
def get_token_symbol(mint: str):
    """
    Get token symbol with persistent cache.

    Fallback chain:
    1. Memory cache (instant)
    2. Persistent cache (DB) (instant)
    3. Dexscreener fetch (slow)
    4. Stale cache
    5. Default
    """
    import requests

    now = time.time()

    # 1. Try in-memory cache (hot)
    if mint in _metadata_cache:
        cached_time = _metadata_cache_time.get(mint, 0)
        if (now - cached_time) < 300:
            result = _metadata_cache[mint].copy()
            result['source'] = 'hot_cache'
            return jsonify(result)

    # 2. Try persistent cache (DB)
    persistent = _get_cached_metadata(mint)
    if persistent:
        # Load into memory cache
        _metadata_cache[mint] = persistent
        _metadata_cache_time[mint] = persistent['cached_at']
        return jsonify(persistent)

    # 3. Fetch from Dexscreener
    try:
        resp = requests.get(
            f'https://api.dexscreener.com/latest/dex/tokens/{mint}',
            timeout=5
        )

        if resp.status_code == 200:
            data = resp.json()
            if data.get('pairs') and len(data['pairs']) > 0:
                base_token = data['pairs'][0].get('baseToken', {})
                symbol = base_token.get('symbol', mint[:8].upper())
                name = base_token.get('name', 'Token')

                result = {
                    'symbol': symbol,
                    'name': name,
                    'cached_at': now,
                    'source': 'dexscreener'
                }

                # Store in both caches
                _metadata_cache[mint] = result
                _metadata_cache_time[mint] = now
                _store_metadata_cache(mint, symbol, name, 'dexscreener')

                return jsonify(result)

    except Exception as e:
        logger.debug(f"Error fetching metadata for {mint}: {e}")

    # 4. Return stale cache + flag if available
    if mint in _metadata_cache:
        result = _metadata_cache[mint].copy()
        result['is_stale'] = True
        result['source'] = 'stale_cache'
        return jsonify(result)

    # 5. Return default
    result = {
        'symbol': mint[:8].upper(),
        'name': 'Token',
        'cached_at': now,
        'source': 'default'
    }

    # Store default in persistent cache (reduces future fetches)
    _store_metadata_cache(mint, result['symbol'], result['name'], 'default')

    return jsonify(result)


# Call on startup
_ensure_metadata_cache_table()
```

---

### 5. Pre-Warm Cache on Registration (Changes to `src/apis/price_api.py`)

```python
@price_api.route('/batch/register', methods=['POST'])
def register_tokens_batch():
    """
    Register tokens and pre-warm cache in background.

    Returns immediately; fetches symbol and price asynchronously.
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
        warm_up_queued = 0

        queue = get_price_queue()

        for mint in mints:
            if not mint:
                skipped += 1
                continue

            existing = registry.get_tracked_tokens()
            is_active = any(t['mint'] == mint and t['is_active'] for t in existing)

            if is_active:
                deduplicated += 1
            else:
                if registry.register_token(mint, priority_level='MEDIUM'):
                    registered += 1

                    # Pre-warm cache: enqueue fetch in background
                    task = FetchTask(
                        mint=mint,
                        priority='MEDIUM',
                        enqueued_at=time.time()
                    )
                    asyncio.create_task(queue.enqueue(task))
                    warm_up_queued += 1

                else:
                    skipped += 1

        logger.info(
            f"Batch register: registered={registered}, deduplicated={deduplicated}, "
            f"skipped={skipped}, warm_up_queued={warm_up_queued}"
        )

        return jsonify({
            'registered': registered,
            'deduplicated': deduplicated,
            'skipped': skipped,
            'warm_up_queued': warm_up_queued
        })

    except Exception as e:
        logger.error(f"Error in batch register: {e}")
        return jsonify({'error': str(e)}), 500
```

---

### 6. Streaming Price Updates (WebSocket, Optional)

**New file:** `src/apis/price_streaming.py`

```python
"""
Streaming price updates via WebSocket.

Allows dashboard to receive real-time price updates instead of polling.

Falls back to polling if WebSocket unavailable.
"""

import logging
from flask import Blueprint, request
from flask_sock import Sock

logger = logging.getLogger(__name__)

# Clients connected to WebSocket
_connected_clients = set()


def init_price_streaming(app, sock: Sock):
    """Initialize WebSocket streaming endpoints."""

    @sock.route('/api/price/stream')
    def stream_prices(ws):
        """WebSocket endpoint for real-time price updates."""
        client_id = id(ws)
        _connected_clients.add(ws)
        logger.info(f"Client {client_id} connected")

        try:
            # Wait for messages (client can send filters)
            while True:
                data = ws.receive()
                if data is None:
                    break

                # Client can send {"action": "subscribe", "mints": [...]}
                # For now, just confirm receipt
                ws.send(b'{"status": "subscribed"}')

        except Exception as e:
            logger.error(f"WebSocket error: {e}")
        finally:
            _connected_clients.discard(ws)
            logger.info(f"Client {client_id} disconnected")


def broadcast_price_update(mint: str, price: 'TokenPrice'):
    """
    Broadcast price update to all connected WebSocket clients.

    Called by price worker when price is fetched.
    """
    message = {
        'type': 'price_update',
        'mint': mint,
        'price_usd': price.price_usd,
        'market_cap': price.market_cap,
        'source': price.source,
        'timestamp': price.timestamp,
        'is_stale': price.is_stale
    }

    import json
    payload = json.dumps(message).encode()

    for ws in list(_connected_clients):
        try:
            ws.send(payload)
        except Exception as e:
            logger.debug(f"Error broadcasting to client: {e}")
            _connected_clients.discard(ws)
```

**Dashboard changes (fallback to WebSocket):**

```javascript
// Global WebSocket connection
let priceWebSocket = null;

function connectPriceStream() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/api/price/stream`;

    try {
        priceWebSocket = new WebSocket(wsUrl);

        priceWebSocket.onopen = () => {
            console.log("Price stream connected");
        };

        priceWebSocket.onmessage = (event) => {
            const data = JSON.parse(event.data);
            if (data.type === 'price_update') {
                patchTokenPrice(data.mint, data);
            }
        };

        priceWebSocket.onerror = (error) => {
            console.error("WebSocket error; falling back to polling", error);
            fallbackToPricePolling();
        };

        priceWebSocket.onclose = () => {
            console.log("Price stream closed; reconnecting...");
            setTimeout(connectPriceStream, 3000);
        };

    } catch (error) {
        console.error("WebSocket unavailable; using polling", error);
        fallbackToPricePolling();
    }
}

function fallbackToPricePolling() {
    // If WebSocket unavailable, fall back to polling
    if (!priceRefreshInterval) {
        priceRefreshInterval = setInterval(refreshVisiblePrices, 15000);
    }
}

// Try WebSocket first; falls back to polling if unavailable
connectPriceStream();
```

---

## Migration Steps

### Step 1: Deploy Request Queue (Phase 1)

```bash
# 1. Add new file
cp /dev/null src/core/price_fetch_queue.py
# (insert code above)

# 2. Modify price_worker.py
# - Import get_price_queue, FetchTask
# - Modify _refresh_cycle() to enqueue instead of batch fetch
# - Add _on_price_fetched() callback
# - Start queue worker on init

# 3. Test
python3 -m pytest tests/test_price_queue.py
./scripts/restart.sh

# 4. Monitor logs for queue stats
tail -f logs/price_worker.log | grep "queue depth"

# 5. Commit
git add -A && git commit -m "phase1: Add request queue with concurrency limits"
```

### Step 2: Deploy Activity-Based Scheduling (Phase 2)

```bash
# 1. Modify price_worker.py
# - Add _compute_activity_score() method
# - Replace _get_tokens_for_refresh() with activity-based version
# - Verify compatibility with queue system

# 2. Test
python3 -m pytest tests/test_activity_scheduling.py
./scripts/restart.sh

# 3. Monitor for activity score distribution
tail -f logs/price_worker.log | grep "activity"

# 4. Commit
git add -A && git commit -m "phase2: Add activity-based refresh scheduling"
```

### Step 3: Deploy Multi-Source Aggregation (Phase 3)

```bash
# 1. Modify price_service.py
# - Add BirdeyeClient class
# - Update get_token_prices_sync() for fallback chain
# - Test each source independently

# 2. Test
python3 -m pytest tests/test_price_service.py -k "multi_source"
./scripts/restart.sh

# 3. Monitor source usage
tail -f logs/price_service.log | grep "Batch fetch:"

# 4. Commit
git add -A && git commit -m "phase3: Add multi-source price aggregation"
```

### Step 4: Deploy Metadata Persistence (Phase 4)

```bash
# 1. Modify price_api.py
# - Add _ensure_metadata_cache_table()
# - Add _get_cached_metadata()
# - Add _store_metadata_cache()
# - Update get_token_symbol() endpoint

# 2. Test
python3 -m pytest tests/test_metadata_persistence.py
./scripts/restart.sh

# 3. Verify DB migration (check metadata_cache table exists)
sqlite3 database/flex_complete_database.db ".tables"

# 4. Commit
git add -A && git commit -m "phase4: Add persistent metadata cache"
```

### Step 5: Deploy Pre-Warm Cache (Phase 5)

```bash
# 1. Modify price_api.py batch/register endpoint
# - Add warm_up_queued field to response
# - Enqueue new tokens for immediate fetch

# 2. Test
python3 -m pytest tests/test_prewarm_cache.py
./scripts/restart.sh

# 3. Commit
git add -A && git commit -m "phase5: Add cache pre-warming on token registration"
```

### Step 6: Deploy WebSocket Streaming (Optional, Phase 6)

```bash
# 1. Add flask-sock to requirements.txt
echo "flask-sock>=0.6.0" >> requirements.txt
pip install flask-sock

# 2. Create src/apis/price_streaming.py
cp /dev/null src/apis/price_streaming.py
# (insert code above)

# 3. Modify app initialization
# - Call init_price_streaming(app, sock)
# - Import broadcast_price_update

# 4. Modify price_worker.py callback
# - Call broadcast_price_update() when price is fetched

# 5. Modify dashboard templates/flex_dashboard.html
# - Add connectPriceStream() on page load

# 6. Test
python3 -m pytest tests/test_price_streaming.py
./scripts/restart.sh

# 7. Commit
git add -A && git commit -m "phase6: Add WebSocket streaming price updates (optional)"
```

---

## Monitoring Metrics

### Request Queue

```
/api/price/health → worker_stats

{
  "queue_stats": {
    "enqueued": 1234,           # Total enqueued
    "processed": 1200,          # Successfully fetched
    "failed": 34,               # Fetch failures
    "queue_depth": 5,           # Pending tokens
    "last_request_at": 1710...  # Last fetch timestamp
  }
}
```

**Expected:** queue_depth ≤ 5, failed < 5% of processed

### Activity-Based Scheduling

```
Logs should show:

"Token ABC activity=high refresh_every=10s"
"Token DEF activity=medium refresh_every=30s"
"Token GHI activity=low refresh_every=100s"
"Token JKL activity=dormant refresh_every=300s"
```

**Expected:** Most tokens in medium/low, few in high

### Multi-Source Aggregation

```
Logs should show:

"Batch fetch: {'dexscreener': 15, 'jupiter': 3, 'birdeye': 2, 'cached': 5}"
```

**Expected:** Dexscreener used for 60-70%, fallbacks used 10-20%

### Metadata Persistence

```
SELECT COUNT(*) FROM metadata_cache;
```

**Expected:** Grows to ~25 (num displayed tokens), then stable

### WebSocket Streaming

```
Logs:

"Client {id} connected" → Dashboard connected
"Client {id} disconnected" → Client closed connection
"Broadcast to 5 clients" → Update sent to N connected clients
```

**Expected:** Constant connected clients during dashboard load

---

## Risks and Mitigation

### Risk 1: Asyncio Integration Issues

**Problem:** Mixing sync and async code can cause deadlocks

**Mitigation:**
- Keep price queue in separate async context
- Use `asyncio.create_task()` for non-blocking enqueue
- Don't await queue operations from sync code
- Test thoroughly before production

---

### Risk 2: Queue Overflow

**Problem:** If workers slow down, queue can grow unbounded

**Mitigation:**
- Monitor `queue_depth` continuously
- Set max_queue_size with warning at 80%
- Add circuit breaker (stop enqueueing if queue > 100)
- Increase max_concurrent from 3 to 5 if needed

---

### Risk 3: Activity Score Inaccuracy

**Problem:** Dormant tokens might still be important

**Mitigation:**
- Keep min refresh of 3-5 min (don't go dormant indefinitely)
- Allow manual priority override
- Monitor for missed important tokens
- Adjust thresholds based on real data

---

### Risk 4: Multi-Source Latency

**Problem:** Trying multiple sources sequentially can be slow

**Mitigation:**
- Run sources in parallel with `asyncio.gather(..., return_exceptions=True)`
- Return first successful result
- Use 5s timeout per source (fail fast)
- Keep cache hit ratio high (70%+)

---

### Risk 5: Metadata Cache Staleness

**Problem:** Persistent cache might not be cleared properly

**Mitigation:**
- Use 5-min TTL (same as memory)
- Delete entries if source returns error
- Provide `/api/price/cache/clear` admin endpoint
- Log cache size/age periodically

---

### Risk 6: Database Contention

**Problem:** Frequent metadata_cache writes can slow DB

**Mitigation:**
- Batch writes (write 10 at a time)
- Use background thread for persistence
- Monitor DB write latency
- Consider Redis if SQLite becomes bottleneck

---

## Success Metrics

### API Pressure (Phase 1-3)

| Metric | Before | Target | After |
|--------|--------|--------|-------|
| API calls/hour | 900-1200 | 500-800 | 600-800 |
| Burst size (max tokens in parallel) | 10 | 3 | 3 |
| 429 errors/day | 0-2 | 0 | 0 |
| Cache hit ratio | 70% | 80%+ | 80%+ |

### User Experience (Phase 3-6)

| Metric | Before | Target |
|--------|--------|--------|
| Symbol load latency | 1-5ms | <5ms |
| Price update frequency | 15s (poll) | <1s (WebSocket) or 15s (poll) |
| Dashboard responsiveness | Good | Excellent |
| First load time | 2-3s | <1s |

### System Health (Phase 1-2)

| Metric | Before | Target |
|--------|--------|--------|
| Queue depth | N/A | ≤5 (avg) |
| Activity-based downgrades | N/A | 70% of tokens in MEDIUM+ |
| Metadata cache size | N/A | ~25 entries |
| DB metadata_cache write latency | N/A | <10ms |

---

## Optional: Phase 7 Architecture (Future)

Once the above is stable, consider a dedicated service:

```
┌─────────────────────────────────┐
│  price-collector-service        │
│  (dedicated background process) │
├─────────────────────────────────┤
│ - Request queue                 │
│ - Multi-source aggregation      │
│ - Update Redis cache            │
│ - Emit WebSocket updates        │
└─────────────────────────────────┘
              ↓
        ┌─────────────┐
        │ Redis Cache │ ← All data here
        └─────────────┘
              ↓
       (API server reads only)
```

**Benefits:**
- API server never calls upstream APIs
- Horizontally scalable (run 2 collector services)
- Better observability
- Can run on different machines

**Implementation:** Create new `price-collector/main.py` microservice. API server becomes read-only client.

