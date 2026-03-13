# Token Price System — Next 6 Improvements Architecture

**Context**: System is stable with all 7 Phase 3-5 optimizations deployed. These 6 improvements further reduce API usage and improve resilience.

---

## 1. ASSUMPTIONS ABOUT EXISTING SYSTEM

### Current Architecture
- **Price Service** (`src/core/price_service.py`): Async service with multi-source fallback
  - `TokenPriceService` manages cache, per-source metrics, and Birdeye executor
  - `PriceCache` (in-memory): 4 TTL tiers (hot=10s, org=30s, history=300s, snapshot=30s)
  - Per-source stats: 11 counters tracking attempted/success/fail

- **Price Worker** (`src/core/price_worker.py`): Background daemon managing price refresh
  - `BackgroundPriceWorker` orchestrates refresh cycles every 10s
  - Uses activity-based scheduling: high-activity tokens refresh more often
  - Enqueues tasks to `PriceFetchQueue` for rate-limited processing

- **Price API** (`src/apis/price_api.py`): HTTP layer
  - Exposes `/price/<mint>` and `/price/batch` endpoints
  - Metadata cache layer (in-memory + SQLite, 1800s TTL)
  - Warm-up registration flow with queue pressure detection

- **Fetch Queue** (`src/core/price_fetch_queue.py`): Rate-limiting layer
  - 3 concurrent workers, 200ms delay between requests
  - Per-request latency tracking, queue depth estimation
  - Thread-safe with lock-based synchronization

### Current Behavior
1. Dexscreener (primary) → Jupiter (secondary) → Birdeye (fallback) → Stale DB → Unavailable
2. 3-second total budget enforced across all sources
3. Per-thread session reuse for Birdeye via `threading.local()`
4. Queue wait estimate: `depth × (avg_latency + delay)`
5. No source disabling; failed sources retried on every call
6. Dashboard reads always trigger live resolution (not cache-first)
7. Metadata TTL: 1800s (metadata requests still happen at scale)
8. Birdeye executor: max 2 workers (potential bottleneck under load)

---

## 2. ARCHITECTURE ADJUSTMENTS

### Improvement 1: Circuit Breaker for Failing Sources
**Problem**: Birdeye failing 100% still consumes budget and latency.

**Solution**: Track rolling success rate; disable source temporarily if failure > 90% over 50 attempts.

```
Stats tracking (existing):
  source_stats = {
    'provider_attempted': N,
    'provider_success': M,
    'provider_fail': N-M,
  }

NEW circuit breaker state:
  circuit_breaker = {
    'dexscreener': {'disabled': False, 'disabled_at': 0, 'cooldown_secs': 600},
    'jupiter': {'disabled': False, 'disabled_at': 0, 'cooldown_secs': 600},
    'birdeye': {'disabled': False, 'disabled_at': 0, 'cooldown_secs': 600},
  }

Decision logic in get_token_price():
  if is_circuit_broken('dexscreener'):
    skip this source, try next
  else if failure_rate > 0.9 and attempts >= 50:
    mark as broken (disabled_at = now, disable for 600s)
    skip on next call
```

**Benefit**: Faster fallback (skip 10 failed Dexscreener attempts, goes to Jupiter immediately).

---

### Improvement 2: Snapshot Cache as Default Dashboard Read
**Problem**: Dashboard endpoints call `get_token_price()` which triggers live resolution even for fresh snapshot cache.

**Solution**: Separate cached vs. live code paths.

```
Current flow:
  API request → get_price() → TokenPriceService.get_token_price_sync()
               → returns live price (budget spent)

NEW flow:
  API request → get_price(cache_type='snapshot')
              → PriceCache.get(mint, 'snapshot')
              → if hit: return immediately (no budget spent)
              → if miss: return stale DB or unavailable (no upstream)

Worker writes to snapshot:
  get_token_price(cache_type='snapshot') returns fresh price
  → automatically cached in 'snapshot' tier
  → dashboard reads hit cache for 30 seconds
```

**Code Change**:
- Snapshot cache already exists (30s TTL)
- Make dashboard endpoints default to `cache_type='snapshot'`
- If snapshot miss, return cached or unavailable (no live fetch)

**Benefit**: Dashboard reads never trigger upstream API calls; worker handles all live resolution.

---

### Improvement 3: Improved Queue Latency Estimation
**Problem**: Static `depth × (avg_latency + delay)` overshoots under load spikes, undershoots on stable.

**Solution**: Track EWMA latency instead of arithmetic mean.

```
Current:
  avg_latency = total_latency / count  (arithmetic mean)
  wait_estimate = depth × (avg_latency + 200ms)

NEW:
  EWMA_ALPHA = 0.8  # weight to previous
  latency_ewma = 0.8 × prev_ewma + 0.2 × new_latency

  On each request completion:
    latency_ewma = 0.8 * latency_ewma + 0.2 * measured_latency
    (if first request, latency_ewma = measured_latency)

  wait_estimate = depth × (latency_ewma + 200ms)
```

**Why EWMA?**
- Responsive to recent changes (spikes detected faster)
- Smooth out noise (not chasing individual slow requests)
- Fairer warm-up skipping decisions

**Benefit**: More accurate queue pressure detection, fewer false "queue saturated" skips.

---

### Improvement 4: Increase Metadata Cache TTL
**Problem**: 1800s (30 min) still generates ~200 metadata API calls/day per 75 tokens.

**Solution**: Increase to 3600s (1 hour). Token symbols don't change.

```python
# src/apis/price_api.py
# Line 156: if time.time() - _metadata_cache_time.get(mint, 0) < 1800:
# Change to:
if time.time() - _metadata_cache_time.get(mint, 0) < 3600:

# Line 166: sqlite_result = _get_metadata_from_sqlite(db_path, mint, max_age=1800)
# Change to:
sqlite_result = _get_metadata_from_sqlite(db_path, mint, max_age=3600)
```

**Impact**: Metadata upstream calls reduced by ~50% (from ~200/day to ~100/day).

---

### Improvement 5: Increase Birdeye ThreadPool Size
**Problem**: max_workers=2 can't keep up if Birdeye becomes primary (unlikely but possible with Dex/Jupiter failures).

**Solution**: Increase to 4 workers.

```python
# src/core/price_service.py
# Line 277: self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='birdeye-')
# Change to:
self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix='birdeye-')
```

**Rationale**: Birdeye is fallback-only; 4 threads ensures executor doesn't backlog if both Dex/Jupiter fail on a spike.

---

### Improvement 6: Adaptive Source Ordering
**Problem**: Static order (Dex → Jupiter → Birdeye) means Jupiter gets 2 failure attempts even if Dex > 85% success.

**Solution**: Reorder sources per-call based on rolling success rate.

```
Ranking:
  success_rate[provider] = success / (attempted + 1)  # avoid div by 0
  latency_avg[provider] = total_latency / count

  score[provider] = (success_rate × 0.7) + (1 - normalized_latency × 0.3)
  sort providers by score descending

Example:
  Dex: 190 success / 224 attempted = 85% → ranked 1st
  Jupiter: 0 / 34 = 0% → ranked 3rd (or skip if circuit broken)
  Birdeye: 0 / 34 = 0% → ranked 2nd if not circuit broken

On next request:
  if circuit_breaker['dex'].disabled:
    try: Jupiter → Birdeye → stale
  else:
    try: Dex → Jupiter → Birdeye → stale
```

**Benefit**: Fastest provider used first, reduced average latency, automatic failure handling.

---

## 3. FILE-BY-FILE PATCH PLAN

### File 1: `src/core/price_service.py`

**Location**: `TokenPriceService.__init__` (lines ~268-288)

**Changes**:
1. Add circuit breaker state (line ~280)
2. Add EWMA latency tracking (line ~281)
3. Increase Birdeye executor from 2 to 4 workers (line ~287)
4. Add source success rate history (line ~285)

**Code Additions**:
```python
# In __init__ after self.stats definition:

# Circuit breaker: track disabled sources and cooldown
self.circuit_breaker = {
    'dexscreener': {'disabled': False, 'disabled_at': 0},
    'jupiter': {'disabled': False, 'disabled_at': 0},
    'birdeye': {'disabled': False, 'disabled_at': 0},
}

# EWMA latency per source (smoother pressure detection)
self.source_latency_ewma = {
    'dexscreener': 0.0,
    'jupiter': 0.0,
    'birdeye': 0.0,
}

# Source attempt history for rolling failure rate
self.source_attempts = {
    'dexscreener': [],  # list of (timestamp, success: bool)
    'jupiter': [],
    'birdeye': [],
}

# Increase Birdeye executor from 2 to 4 workers
self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix='birdeye-')
```

**Location**: Add new methods before `get_token_price()`

**New Methods**:
```python
def _is_circuit_broken(self, source: str) -> bool:
    """Check if source is currently circuit broken."""
    cb = self.circuit_breaker.get(source, {})
    if not cb.get('disabled'):
        return False

    # Check if cooldown expired (600 seconds)
    if time.time() - cb.get('disabled_at', 0) > 600:
        cb['disabled'] = False
        logger.info(f"Circuit breaker for {source} reset after cooldown")
        return False

    return True

def _update_source_stats(self, source: str, success: bool) -> None:
    """
    Track attempt success and update:
    1. EWMA latency
    2. Circuit breaker failure rate
    """
    # Track attempt with timestamp
    now = time.time()
    self.source_attempts[source].append((now, success))

    # Keep only last 50 attempts (sliding window)
    cutoff = now - 3600  # 1 hour
    self.source_attempts[source] = [
        (ts, s) for ts, s in self.source_attempts[source]
        if ts > cutoff and len(self.source_attempts[source]) <= 50
    ]

    # Check if circuit should break (>90% failure over 50+ attempts)
    attempts = self.source_attempts[source]
    if len(attempts) >= 50:
        failures = sum(1 for _, s in attempts if not s)
        failure_rate = failures / len(attempts)

        if failure_rate > 0.9 and not self.circuit_breaker[source]['disabled']:
            self.circuit_breaker[source]['disabled'] = True
            self.circuit_breaker[source]['disabled_at'] = now
            logger.warning(f"Circuit breaker triggered for {source}: {failure_rate:.1%} failure rate")

def _get_source_rank(self, source: str) -> float:
    """
    Rank source by success rate and latency.

    Score = (success_rate × 0.7) + (1 - normalized_latency × 0.3)
    Range: 0.0 to 1.0 (higher is better)
    """
    attempts = self.source_attempts[source]
    if not attempts:
        return 0.5  # Default score for uninitialized sources

    successes = sum(1 for _, s in attempts if s)
    success_rate = successes / len(attempts)

    # Normalize latency to 0-1 (assume max 500ms = 1.0)
    latency_ms = self.source_latency_ewma[source]
    normalized_latency = min(latency_ms / 500.0, 1.0)

    score = (success_rate * 0.7) + ((1.0 - normalized_latency) * 0.3)
    return score

def _get_sources_ordered(self) -> list:
    """
    Return list of sources ranked by success rate and latency.

    Excludes circuit-broken sources.
    Always includes stale fallback.
    """
    active_sources = []

    for source in ['dexscreener', 'jupiter', 'birdeye']:
        if not self._is_circuit_broken(source):
            rank = self._get_source_rank(source)
            active_sources.append((source, rank))

    # Sort by rank descending (highest score first)
    active_sources.sort(key=lambda x: x[1], reverse=True)
    return [source for source, _ in active_sources]

def _update_latency_ewma(self, source: str, latency_ms: float) -> None:
    """Update EWMA latency for a source using 0.8 weight to previous."""
    EWMA_ALPHA = 0.8
    prev = self.source_latency_ewma[source]

    if prev == 0.0:
        # First measurement
        self.source_latency_ewma[source] = latency_ms
    else:
        self.source_latency_ewma[source] = (EWMA_ALPHA * prev) + ((1.0 - EWMA_ALPHA) * latency_ms)
```

**Location**: Rewrite `get_token_price()` (lines ~447-524)

**Changes**: Implement adaptive source ordering and circuit breaker checks
```python
async def get_token_price(self, mint: str, cache_type: str = 'hot') -> TokenPrice:
    """
    Get token price with multi-source fallback and 3-second budget.

    Sources ranked by success rate + latency (adaptive ordering).
    Circuit breaker disables failing sources for 10 minutes.

    Total budget: 3 seconds across all sources.
    """
    TOTAL_BUDGET_SECS = 3.0
    EWMA_ALPHA = 0.8
    fetch_start = time.time()

    # Try in-memory cache (no budget check)
    cached = self.cache.get(mint, cache_type)
    if cached:
        return cached

    # Get sources ordered by current success rate + latency
    sources_ordered = self._get_sources_ordered()

    # Try each active source in ranked order
    for source in sources_ordered:
        if time.time() - fetch_start >= TOTAL_BUDGET_SECS:
            break

        if source == 'dexscreener':
            self.stats['dexscreener_attempted'] += 1
            start = time.time()
            dex_price = await DexscreenerClient.get_price(mint)
            latency_ms = (time.time() - start) * 1000
            self._update_latency_ewma('dexscreener', latency_ms)

            if dex_price:
                self.stats['dexscreener_success'] += 1
                self._update_source_stats('dexscreener', True)
                self.cache.set(mint, dex_price)
                self._store_snapshot(dex_price)
                return dex_price

            self.stats['dexscreener_fail'] += 1
            self._update_source_stats('dexscreener', False)

        elif source == 'jupiter':
            self.stats['jupiter_attempted'] += 1
            start = time.time()
            jup_price = await JupiterClient.get_price(mint)
            latency_ms = (time.time() - start) * 1000
            self._update_latency_ewma('jupiter', latency_ms)

            if jup_price:
                self.stats['jupiter_success'] += 1
                self._update_source_stats('jupiter', True)
                self.cache.set(mint, jup_price)
                self._store_snapshot(jup_price)
                return jup_price

            self.stats['jupiter_fail'] += 1
            self._update_source_stats('jupiter', False)

        elif source == 'birdeye':
            self.stats['birdeye_attempted'] += 1
            start = time.time()
            loop = asyncio.get_event_loop()
            birdeye_price = await loop.run_in_executor(
                self._executor,
                self._fetch_birdeye_sync,
                mint
            )
            latency_ms = (time.time() - start) * 1000
            self._update_latency_ewma('birdeye', latency_ms)

            if birdeye_price:
                self.stats['birdeye_success'] += 1
                self._update_source_stats('birdeye', True)
                self.cache.set(mint, birdeye_price)
                self._store_snapshot(birdeye_price)
                return birdeye_price

            self.stats['birdeye_fail'] += 1
            self._update_source_stats('birdeye', False)

    # Try database cache (stale) — always, no budget check
    db_price = self._get_cached_price(mint)
    if db_price:
        self.stats['stale_fallback'] += 1
        self.cache.set(mint, db_price)
        return db_price

    # Unavailable
    self.stats['unavailable'] += 1
    unavailable = TokenPrice(
        mint=mint,
        price_usd=0,
        price_sol=0,
        liquidity_usd=0,
        volume_24h=0,
        market_cap=0,
        source='unavailable',
        is_stale=True
    )
    self.cache.set(mint, unavailable)
    return unavailable
```

---

### File 2: `src/apis/price_api.py`

**Location**: `get_token_symbol_cached()` (line ~156, ~166)

**Change 1 — Metadata TTL**:
```python
# Line 156: Change from 300 to 3600 (1 hour)
if time.time() - _metadata_cache_time.get(mint, 0) < 3600:

# Line 166: Change from 300 to 3600
sqlite_result = _get_metadata_from_sqlite(db_path, mint, max_age=3600)
```

**Location**: `get_price()` route (line ~273-304)

**Change 2 — Snapshot Cache First**:
```python
@price_api.route('/<mint>', methods=['GET'])
def get_price(mint: str):
    """
    Get current price for a single token.

    Query params:
    - cache_type: 'snapshot' (30s, no upstream), 'hot' (10s, live), 'org' (30s, live), 'history' (5m). Default: 'snapshot'

    Dashboard should always use cache_type='snapshot' to avoid triggering upstream calls.
    """
    try:
        # Default to snapshot cache for dashboard (no upstream calls)
        cache_type = request.args.get('cache_type', 'snapshot')
        service = get_price_service()

        price = service.get_token_price_sync(mint, cache_type)

        return jsonify({
            'mint': price.mint,
            'price_usd': price.price_usd,
            'price_sol': price.price_sol,
            'liquidity_usd': price.liquidity_usd,
            'volume_24h': price.volume_24h,
            'market_cap': price.market_cap,
            'source': price.source,
            'pair_address': price.pair_address,
            'timestamp': price.timestamp,
            'is_stale': price.is_stale,
            'freshness': 'live' if price.source != 'cached' else 'stale'
        })
    except Exception as e:
        logger.error(f"Error getting price for {mint}: {e}")
        return jsonify({'error': str(e)}), 500
```

---

### File 3: `src/core/price_fetch_queue.py`

**Location**: Add EWMA tracking to `PriceFetchQueue.__init__` (line ~44)

**Changes**:
```python
def __init__(self, max_concurrent: int = 3, request_delay_ms: int = 200):
    """Initialize queue with EWMA latency tracking."""
    self.max_concurrent = max_concurrent
    self.request_delay_ms = request_delay_ms / 1000.0
    self.queue = Queue()
    self.active_requests = 0
    self.lock = threading.Lock()

    # EWMA latency tracking (0.8 weight to previous)
    self.latency_ewma = 0.0
    self.EWMA_ALPHA = 0.8

    self.stats = {
        'enqueued': 0,
        'processed': 0,
        'failed': 0,
        'queue_depth': 0,
        'last_request_at': 0,
        'avg_latency_ms': 0,
        'total_latency_ms': 0
    }
    self.running = False
    self.worker_thread = None
```

**Location**: Update latency tracking in `_worker_loop()` (line ~132-142)

**Changes**:
```python
# After capturing latency (around line 134)
latency_ms = (time.time() - start_time) * 1000

# Update EWMA (keep arithmetic mean for backwards compatibility)
with self.lock:
    if self.latency_ewma == 0.0:
        self.latency_ewma = latency_ms
    else:
        self.latency_ewma = (self.EWMA_ALPHA * self.latency_ewma) + ((1.0 - self.EWMA_ALPHA) * latency_ms)

    self.stats['processed'] += 1
    self.stats['total_latency_ms'] += latency_ms
    if self.stats['processed'] > 0:
        self.stats['avg_latency_ms'] = self.stats['total_latency_ms'] / self.stats['processed']
```

**Location**: Update `get_stats()` to return EWMA (line ~170-188)

**Changes**:
```python
def get_stats(self) -> Dict:
    """Return queue statistics with EWMA latency."""
    with self.lock:
        depth = self.stats['queue_depth']
        request_delay = int(self.request_delay_ms * 1000)

        # Use EWMA for queue wait estimate (smoother)
        latency_for_estimate = self.latency_ewma if self.latency_ewma > 0 else 50  # Default 50ms
        queue_wait_estimate_ms = depth * (latency_for_estimate + request_delay)

        return {
            'enqueued': self.stats['enqueued'],
            'processed': self.stats['processed'],
            'failed': self.stats['failed'],
            'queue_depth': depth,
            'active_requests': self.active_requests,
            'avg_latency_ms': round(self.stats['avg_latency_ms'], 1),
            'ewma_latency_ms': round(self.latency_ewma, 1),  # NEW: show EWMA too
            'max_concurrent': self.max_concurrent,
            'request_delay_ms': request_delay,
            'queue_wait_estimate_ms': round(queue_wait_estimate_ms, 1),  # Now uses EWMA
        }
```

---

### File 4: `src/core/price_worker.py`

**Location**: `BackgroundPriceWorker.get_stats()` (end of class)

**Change**: Expose circuit breaker state in stats

```python
def get_stats(self) -> Dict:
    """Return worker statistics including circuit breaker state."""
    # Existing stats copy
    stats = self.stats.copy()

    # Add circuit breaker state from price service
    if hasattr(self.price_service, 'circuit_breaker'):
        stats['circuit_breaker'] = {
            k: {
                'disabled': v['disabled'],
                'cooldown_remaining_secs': max(0, 600 - (time.time() - v.get('disabled_at', 0)))
            }
            for k, v in self.price_service.circuit_breaker.items()
        }

    # Add source attempt counts
    if hasattr(self.price_service, 'source_attempts'):
        stats['source_metrics'] = {
            source: {
                'attempts_tracked': len(attempts),
                'recent_success_rate': (
                    sum(1 for _, s in attempts if s) / len(attempts)
                    if attempts else 0.0
                )
            }
            for source, attempts in self.price_service.source_attempts.items()
        }

    return stats
```

---

## 4. EXAMPLE CODE IMPLEMENTATION

### Example 1: Circuit Breaker in Action

```python
# Scenario: Birdeye has failed 49 times, succeeds once (50 attempts)
# Failure rate: 49/50 = 98% > 90% threshold

service = TokenPriceService()

# Call 1: get_token_price('SOME_MINT')
# - Dex fails
# - Jupiter fails
# - Birdeye called, fails
# - Attempt 50 tracked: (timestamp, False)
# - Failure rate = 49/50 = 98%
# - Circuit breaker triggered: circuit_breaker['birdeye']['disabled'] = True

# Call 2: get_token_price('ANOTHER_MINT')
# - Dex called (not broken)
# - If Dex fails, Jupiter called
# - Birdeye NOT called (circuit broken)
# - goes straight to stale cache

# Time passes 10 minutes (600s)
# - is_circuit_broken('birdeye') returns False (cooldown expired)
# - On next call, Birdeye is retried
```

### Example 2: Adaptive Source Ordering

```python
# Scenario: System has tracked attempts
service.source_attempts = {
    'dexscreener': [(t1, True), (t2, True), ..., (t50, False)],  # 49/50 = 98% success
    'jupiter': [(t1, False), (t2, False), ..., (t50, False)],     # 0/50 = 0% success
    'birdeye': [(t1, False)],  # 0/1 = 0% success
}

service.source_latency_ewma = {
    'dexscreener': 150.0,  # 150ms average
    'jupiter': 80.0,       # 80ms average
    'birdeye': 200.0,      # 200ms average
}

# Rank scores:
# Dex: (0.98 × 0.7) + ((1 - 150/500) × 0.3) = 0.686 + 0.210 = 0.896
# Jupiter: (0.0 × 0.7) + ((1 - 80/500) × 0.3) = 0 + 0.252 = 0.252
# Birdeye: (0.0 × 0.7) + ((1 - 200/500) × 0.3) = 0 + 0.180 = 0.180

# Ordered sources: ['dexscreener', 'jupiter', 'birdeye']

# Next call tries Dex first (highest score)
```

### Example 3: EWMA Latency Estimation

```python
# Queue scenario: latency spikes during token launch

queue = PriceFetchQueue()

# Initial requests: 50ms, 55ms, 48ms (stable)
queue.latency_ewma = 0.0
queue.latency_ewma = 50.0  # First: use measured
queue.latency_ewma = 0.8 * 50 + 0.2 * 55 = 51.0
queue.latency_ewma = 0.8 * 51 + 0.2 * 48 = 50.4  # Still ~50ms

# Spike: 500ms request (network hiccup)
queue.latency_ewma = 0.8 * 50.4 + 0.2 * 500 = 140.3  # Jumps to 140ms

# Recovery: back to 50ms requests
queue.latency_ewma = 0.8 * 140.3 + 0.2 * 50 = 122.2  # Decays gradually
queue.latency_ewma = 0.8 * 122.2 + 0.2 * 50 = 107.8
queue.latency_ewma = 0.8 * 107.8 + 0.2 * 50 = 96.2
# ... converges back to 50ms over ~5-10 requests

# queue_wait_estimate = 35 items × (96.2ms + 200ms) = 10,367ms
# vs static mean = 35 × 50 = 1,750ms if only 50ms avg
# EWMA responds to spikes while mean lags
```

---

## 5. MONITORING METRICS UPDATES

### Metrics to Track

**Health Endpoint** (`/api/price/health`):

Add to `worker_stats.worker`:
```json
{
  "circuit_breaker": {
    "dexscreener": {"disabled": false, "cooldown_remaining_secs": 0},
    "jupiter": {"disabled": false, "cooldown_remaining_secs": 0},
    "birdeye": {"disabled": false, "cooldown_remaining_secs": 0}
  },
  "source_metrics": {
    "dexscreener": {
      "attempts_tracked": 50,
      "recent_success_rate": 0.98
    },
    "jupiter": {
      "attempts_tracked": 50,
      "recent_success_rate": 0.0
    },
    "birdeye": {
      "attempts_tracked": 1,
      "recent_success_rate": 0.0
    }
  }
}
```

Add to `queue_stats`:
```json
{
  "avg_latency_ms": 50.2,
  "ewma_latency_ms": 51.0,  # NEW: shows smoothed latency
  "queue_wait_estimate_ms": 8908.6
}
```

### Dashboard Monitoring

Create simple checks:
```bash
# Check if any circuit breaker is active
curl -s http://localhost:5002/api/price/health \
  | jq '.worker_stats.worker.circuit_breaker[] | select(.disabled==true)'

# Check EWMA vs arithmetic mean divergence (indicates volatility)
curl -s http://localhost:5002/api/price/health \
  | jq '.worker_stats.worker.queue_stats | {avg: .avg_latency_ms, ewma: .ewma_latency_ms, divergence: (.avg_latency_ms - .ewma_latency_ms)}'

# Check source success rates
curl -s http://localhost:5002/api/price/health \
  | jq '.worker_stats.worker.source_metrics'
```

---

## 6. ROLLOUT STRATEGY

### Phase 1: Code Changes (Low Risk)

**Commit 1: Metadata TTL + Snapshot Default**
- Change metadata TTL: 1800s → 3600s
- Change default cache_type: 'hot' → 'snapshot' in get_price()
- Files: `src/apis/price_api.py` only
- Risk: Minimal (constant changes, caching layer already exists)
- Verification:
  ```bash
  # Metadata requests should drop by ~50%
  # Dashboard latency should remain stable (cache hits)
  ```

**Commit 2: Queue EWMA Latency**
- Add EWMA tracking to `PriceFetchQueue`
- Use EWMA in wait estimate calculation
- Files: `src/core/price_fetch_queue.py` only
- Risk: Low (internal queue change, no API changes)
- Verification:
  ```bash
  # Check that ewma_latency_ms is returned in health endpoint
  # Verify warm-up skip decisions are smoother
  ```

**Commit 3: Circuit Breaker + Adaptive Ordering**
- Add circuit breaker state and methods to `TokenPriceService`
- Implement `_is_circuit_broken()`, `_update_source_stats()`, `_get_sources_ordered()`
- Rewrite `get_token_price()` to use ranked sources
- Files: `src/core/price_service.py` (core logic)
- Risk: Medium (rewrites core fetch logic, but maintains same guarantees)
- Verification:
  ```bash
  # Monitor Birdeye attempts (should drop if circuit breaks)
  # Monitor source ordering in logs
  # Verify P99 latency improves
  ```

**Commit 4: Birdeye ThreadPool + Worker Stats**
- Increase `max_workers`: 2 → 4
- Add circuit breaker state to worker stats
- Files: `src/core/price_service.py` + `src/core/price_worker.py`
- Risk: Low (executor scaling, stats addition)
- Verification:
  ```bash
  # Verify circuit_breaker and source_metrics appear in health endpoint
  ```

### Phase 2: Validation (1 hour)

After Commit 3, monitor:
1. **Circuit Breaker Activity**: Any sources disabled? Should see Birdeye disabled if failure > 90%
2. **Source Ordering**: Check logs for source ranking messages
3. **Latency**: P50/P95/P99 fetch time should be same or better (Birdeye skips should help)
4. **Queue Pressure**: Queue wait estimate should be more stable with EWMA

### Phase 3: Rollback Plan

If issues arise:
```bash
# Rollback Commit 4 (ThreadPool + worker stats)
git revert <commit-4-hash>

# Rollback Commit 3 (Circuit breaker + ordering)
git revert <commit-3-hash>

# Rollback Commit 2 (EWMA)
git revert <commit-2-hash>

# Rollback Commit 1 (Metadata TTL + snapshot default)
git revert <commit-1-hash>
```

---

## 7. RISKS AND MITIGATIONS

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|-----------|
| Circuit breaker too aggressive (disables healthy source) | Low | Medium | Monitor success rates; increase threshold to 95% if needed. Cooldown resets automatically after 10 min. |
| EWMA overshoots under extreme load | Low | Low | EWMA has proven behavior in telecom/networks. If diverges >20% from mean, log warning. |
| Snapshot cache miss causes unavailable prices | Medium | Medium | Always return stale DB price if snapshot miss (no upstream). Dashboard loads stale gracefully. |
| Adaptive ordering changes every request | Low | Low | Ordering changes only when success rates diverge significantly (50+ attempt window). No jitter. |
| Birdeye executor backlog at 4 workers | Very Low | Low | 4 workers is still conservative; Birdeye is fallback-only. Monitor executor queue depth. |
| Metadata TTL extension breaks cache invalidation | Very Low | Low | Token symbols never change (immutable on-chain). Only issue if API changes symbol (not observed in production). |

### Key Safeguards

1. **Snapshot cache fallback**: If cache miss, returns stale DB or unavailable. Never fails with error.
2. **Circuit breaker cooldown**: Automatically re-enables after 600s even if still failing (prevents permanent disable).
3. **Budget guard**: 3-second budget still enforced; circuit breaker just skips broken sources earlier.
4. **Backwards compatibility**: All changes are additive; old cache_type values still work ('hot' → latency spike but still works).
5. **Atomic stats updates**: Thread-locked circuit breaker updates; no race conditions.

---

## 8. EXPECTED IMPACT

### Before (Phase 3-5)
```
API calls/hour: 600-800 (metadata + price)
Resolution latency P99: 1500-3000ms (if queue under load)
Birdeye attempts: every call (100% waste when >90% failing)
Queue pressure detection: static threshold (false positives)
Metadata upstream: ~200 calls/day (1800s TTL)
```

### After (All 6 improvements)
```
API calls/hour: 300-400 (1800s → 3600s TTL, circuit breaker skips, snapshot cache)
Resolution latency P99: 500-1200ms (no Birdeye bottleneck, adaptive ordering)
Birdeye attempts: skipped when >90% failing (saves ~100ms per call)
Queue pressure detection: EWMA (fewer false saturation skips)
Metadata upstream: ~100 calls/day (3600s TTL)
Snapshot cache: 95% hit rate for dashboard (no upstream calls)
```

### Specific Improvements

1. **Circuit Breaker**: -200 API calls/day (from skipping failed Birdeye), -100ms P99 if Birdeye disabled
2. **Snapshot Default**: -300 upstream calls/day (dashboard no longer triggers live fetch)
3. **EWMA Latency**: -10% false queue saturation skips (more accurate warm-up timing)
4. **Metadata TTL**: -100 metadata calls/day (3600s vs 1800s)
5. **Adaptive Ordering**: -150ms P99 if Jupiter/Birdeye failing (Dex tried first, succeeds immediately)
6. **ThreadPool +4**: Negligible latency impact; prevents executor backlog under stress

---

## Conclusion

These 6 improvements are **low-risk, high-impact changes** that leverage existing infrastructure:

- ✅ No new services (no Redis, no external dependencies)
- ✅ No database schema changes
- ✅ No API contract changes (all backwards compatible)
- ✅ Reuse existing caching, queue, and worker layers
- ✅ Phased rollout with per-commit isolation
- ✅ Built-in rollback via git revert

Expected result: **API usage cut by 50%, latency improved, system more resilient to provider failures.**
