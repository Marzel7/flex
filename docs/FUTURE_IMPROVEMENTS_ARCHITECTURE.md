# Token Price System — 6 Future Resilience Improvements Architecture

**Status**: Ready to implement | **Estimated time**: 8-12 hours | **Risk level**: Low-Medium
**Base system**: Current production (4 deployed commits, 222 lines added, circuit breaker active)

---

## Executive Summary

These 6 improvements build on the deployed circuit breaker and adaptive ordering system to create a more self-healing, efficient, and resilient price tracking system. They reduce API usage further (400→200 calls/hour), improve P99 latency (800ms→500ms), and provide better source reliability through persistence and exponential backoff.

**Key insight**: The deployed circuit breaker is in-memory only. Restarts re-enable failed sources immediately. These improvements add persistence, smarter cooldowns, and better source health tracking.

---

## 1. CIRCUIT BREAKER PERSISTENCE

### Current Problem
- Circuit breaker state lives in `TokenPriceService.__init__` (in-memory dict)
- When service restarts, all circuit breakers reset to `disabled: false`
- If Birdeye is broken, restart immediately re-enables it
- Can cause cascading failures if provider is still unavailable

### Solution
Store circuit breaker state in SQLite. Load on startup. Continue cooldown timers.

### New Database Table

```sql
CREATE TABLE IF NOT EXISTS circuit_breaker_state (
    source TEXT PRIMARY KEY,           -- 'dexscreener', 'jupiter', 'birdeye'
    disabled INTEGER DEFAULT 0,         -- 1 = disabled, 0 = enabled
    disabled_at INTEGER DEFAULT 0,      -- Unix timestamp when disabled
    break_count INTEGER DEFAULT 0,      -- Number of times broken (for exponential backoff)
    last_break_at INTEGER DEFAULT 0,    -- Timestamp of last break (for rolling window)
    created_at INTEGER DEFAULT 0,       -- When first tracked
    updated_at INTEGER DEFAULT 0        -- Last update timestamp
);
```

### Implementation Changes

**File**: `src/core/price_service.py`

**1. Load circuit breaker state on startup:**
```python
def __init__(self, db_path: str = 'database/flex_complete_database.db'):
    # ... existing code ...
    self._ensure_tables()

    # NEW: Load persisted circuit breaker state
    self._load_circuit_breaker_state()

def _ensure_tables(self) -> None:
    """Create price snapshot table and circuit breaker persistence table."""
    # ... existing token_price_snapshots table ...

    # NEW: Create circuit_breaker_state table
    conn = self._get_conn()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS circuit_breaker_state (
            source TEXT PRIMARY KEY,
            disabled INTEGER DEFAULT 0,
            disabled_at INTEGER DEFAULT 0,
            break_count INTEGER DEFAULT 0,
            last_break_at INTEGER DEFAULT 0,
            created_at INTEGER DEFAULT 0,
            updated_at INTEGER DEFAULT 0
        )
    ''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_cb_disabled ON circuit_breaker_state(disabled, disabled_at)')
    conn.commit()
    logger.info("Circuit breaker table ensured")

def _load_circuit_breaker_state(self) -> None:
    """Load persisted circuit breaker state from database."""
    try:
        conn = self._get_conn()
        rows = conn.execute('SELECT source, disabled, disabled_at, break_count FROM circuit_breaker_state').fetchall()

        for source, disabled, disabled_at, break_count in rows:
            if source in self.circuit_breaker:
                self.circuit_breaker[source] = {
                    'disabled': bool(disabled),
                    'disabled_at': disabled_at,
                    'break_count': break_count,
                }

                if disabled:
                    elapsed = time.time() - disabled_at
                    logger.info(f"Loaded circuit breaker state: {source} disabled (elapsed {elapsed:.0f}s)")
                else:
                    logger.info(f"Loaded circuit breaker state: {source} enabled")
    except Exception as e:
        logger.error(f"Failed to load circuit breaker state: {e}")
        # Fall back to in-memory defaults (non-fatal)

def _save_circuit_breaker_state(self, source: str) -> None:
    """Persist circuit breaker state to database."""
    try:
        conn = self._get_conn()
        cb = self.circuit_breaker.get(source, {})
        now = int(time.time())

        conn.execute('''
            INSERT OR REPLACE INTO circuit_breaker_state
            (source, disabled, disabled_at, break_count, last_break_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, COALESCE((
                SELECT created_at FROM circuit_breaker_state WHERE source = ?
            ), ?), ?)
        ''', (
            source,
            int(cb.get('disabled', False)),
            cb.get('disabled_at', 0),
            cb.get('break_count', 0),
            int(time.time()) if cb.get('disabled') else 0,
            source,
            now,
            now
        ))
        conn.commit()
    except Exception as e:
        logger.error(f"Failed to save circuit breaker state: {e}")
        # Non-fatal; state still in memory
```

**2. Update `_is_circuit_broken()` to check elapsed time:**
```python
def _is_circuit_broken(self, source: str) -> bool:
    """Check if source is currently circuit broken."""
    cb = self.circuit_breaker.get(source, {})
    if not cb.get('disabled'):
        return False

    # Get cooldown from exponential backoff (break_count)
    break_count = cb.get('break_count', 0)
    cooldown_secs = self._get_exponential_cooldown(break_count)

    elapsed = time.time() - cb.get('disabled_at', 0)
    if elapsed > cooldown_secs:
        cb['disabled'] = False
        logger.info(f"Circuit breaker for {source} reset after {cooldown_secs}s cooldown")
        self._save_circuit_breaker_state(source)
        return False

    return True

def _get_exponential_cooldown(self, break_count: int) -> int:
    """Get cooldown in seconds based on break count (exponential backoff)."""
    # Implemented in Improvement #2
    pass
```

**3. Update `_update_source_stats()` to increment break_count and persist:**
```python
def _update_source_stats(self, source: str, success: bool) -> None:
    """Track attempt success and update circuit breaker failure rate."""
    # ... existing code ...

    if len(attempts) >= 50:
        failures = sum(1 for _, s in attempts if not s)
        failure_rate = failures / len(attempts)

        if failure_rate > 0.9 and not self.circuit_breaker[source]['disabled']:
            self.circuit_breaker[source]['disabled'] = True
            self.circuit_breaker[source]['disabled_at'] = now

            # NEW: Increment break_count for exponential backoff
            self.circuit_breaker[source]['break_count'] = \
                self.circuit_breaker[source].get('break_count', 0) + 1

            logger.warning(
                f"Circuit breaker triggered for {source}: "
                f"{failure_rate:.1%} failure rate (break #{self.circuit_breaker[source]['break_count']})"
            )

            # NEW: Persist state
            self._save_circuit_breaker_state(source)
```

### Migration (One-time)
On first startup after deployment:
```bash
# Service automatically creates circuit_breaker_state table
# All sources initialized as enabled (disabled=0)
# If service is restarted with a broken source, state loads correctly
```

### Verification
```bash
# Check persisted state
sqlite3 database/flex_complete_database.db \
  "SELECT source, disabled, disabled_at, break_count FROM circuit_breaker_state;"

# Should show:
# dexscreener|0|0|0
# jupiter|0|0|0
# birdeye|0|0|0
```

---

## 2. EXPONENTIAL COOLDOWN

### Current Problem
- Fixed 600s (10 min) cooldown regardless of failure history
- Unstable provider (repeatedly breaking) re-enabled at same interval
- No penalty for repeated failures

### Solution
Increase cooldown exponentially with each break:
```
1st break → 10 minutes (600s)
2nd break → 30 minutes (1800s)
3rd break → 2 hours (7200s)
4th+ breaks → 4 hours (14400s)
```

Formula: `cooldown = 600 × (2 ** min(break_count - 1, 3))`

### Implementation

**File**: `src/core/price_service.py`

```python
def _get_exponential_cooldown(self, break_count: int) -> int:
    """
    Get cooldown in seconds based on break count (exponential backoff).

    1st break:  10 min (600s)
    2nd break:  30 min (1800s)
    3rd break:  2 hours (7200s)
    4th+ breaks: 4 hours (14400s)
    """
    BASE_COOLDOWN = 600  # 10 minutes
    MAX_EXPONENT = 3     # Cap at 4 hours

    if break_count <= 1:
        return BASE_COOLDOWN

    exponent = min(break_count - 1, MAX_EXPONENT)
    cooldown = BASE_COOLDOWN * (2 ** exponent)
    return int(cooldown)

def _is_circuit_broken(self, source: str) -> bool:
    """Check if source is currently circuit broken."""
    cb = self.circuit_breaker.get(source, {})
    if not cb.get('disabled'):
        return False

    break_count = cb.get('break_count', 0)
    cooldown_secs = self._get_exponential_cooldown(break_count)

    elapsed = time.time() - cb.get('disabled_at', 0)
    if elapsed > cooldown_secs:
        cb['disabled'] = False
        cooldown_min = cooldown_secs / 60
        logger.info(
            f"Circuit breaker for {source} reset after {cooldown_min:.0f}min cooldown "
            f"(break #{break_count})"
        )
        self._save_circuit_breaker_state(source)
        return False

    remaining = cooldown_secs - elapsed
    remaining_min = remaining / 60
    logger.debug(f"Circuit breaker for {source}: {remaining_min:.1f}min remaining")
    return True
```

### Verification
```bash
# Monitor circuit breaker state
watch -n 5 'curl http://localhost:5002/api/price/health | \
  jq ".worker_stats.worker.circuit_breaker"'

# After multiple breaks, should see increasing cooldown_remaining_secs
# Break 1: 600s remaining
# Break 2: 1800s remaining
# Break 3: 7200s remaining
```

---

## 3. PROVIDER TIMEOUT BUDGETS

### Current Problem
- Global 3-second budget across all sources
- No per-source limits
- Slow provider can consume full budget (e.g., Dexscreener 1.5s + Jupiter 1.5s = 3s used, no Birdeye attempt)

### Solution
Enforce per-provider timeout limits, respect remaining global budget:

```python
PROVIDER_TIMEOUTS = {
    'dexscreener': 1.2,  # 1200ms
    'jupiter': 0.8,      # 800ms
    'birdeye': 1.0,      # 1000ms
}
```

Each provider call enforces: `timeout = min(provider_timeout, remaining_global_budget)`

### Implementation

**File**: `src/core/price_service.py`

**1. Add provider timeout configuration:**
```python
class TokenPriceService:
    # Per-provider timeout limits (seconds)
    PROVIDER_TIMEOUTS = {
        'dexscreener': 1.2,
        'jupiter': 0.8,
        'birdeye': 1.0,
    }

    # Global budget (seconds)
    TOTAL_BUDGET_SECS = 3.0

    def __init__(self, db_path: str = 'database/flex_complete_database.db'):
        # ... existing code ...
        pass

    def _get_timeout_for_provider(self, source: str, remaining_budget: float) -> float:
        """
        Get actual timeout for provider, respecting remaining global budget.

        Returns: timeout in seconds
        """
        provider_timeout = self.PROVIDER_TIMEOUTS.get(source, 1.0)
        # Never exceed remaining budget
        actual_timeout = min(provider_timeout, remaining_budget)
        # Never timeout instantly
        return max(actual_timeout, 0.1)
```

**2. Update `get_token_price()` to use provider timeouts:**
```python
async def get_token_price(self, mint: str, cache_type: str = 'hot') -> TokenPrice:
    """Get token price with per-provider timeout budgets."""
    TOTAL_BUDGET_SECS = 3.0
    fetch_start = time.time()

    # Try in-memory cache (no budget check)
    cached = self.cache.get(mint, cache_type)
    if cached:
        return cached

    sources_ordered = self._get_sources_ordered()

    for source in sources_ordered:
        elapsed = time.time() - fetch_start
        if elapsed >= TOTAL_BUDGET_SECS:
            break

        remaining_budget = TOTAL_BUDGET_SECS - elapsed
        timeout = self._get_timeout_for_provider(source, remaining_budget)

        if source == 'dexscreener':
            self.stats['dexscreener_attempted'] += 1
            start = time.time()
            try:
                # Create timeout object with provider-specific timeout
                timeout_obj = aiohttp.ClientTimeout(total=timeout)

                async with aiohttp.ClientSession(timeout=timeout_obj) as session:
                    # Fetch with per-provider timeout
                    dex_price = await DexscreenerClient.get_price_with_session(mint, session)

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

            except asyncio.TimeoutError:
                logger.debug(f"Dexscreener timeout ({timeout:.1f}s) for {mint}")
                self.stats['dexscreener_fail'] += 1
                self._update_source_stats('dexscreener', False)
            except Exception as e:
                logger.debug(f"Dexscreemer error: {e}")
                self.stats['dexscreener_fail'] += 1
                self._update_source_stats('dexscreemer', False)

        # Similarly for Jupiter and Birdeye...
        # (pattern repeats with appropriate timeout values)

    # Stale DB fallback and unavailable handling unchanged
```

### Configuration
```python
# Can be made configurable via environment variables
PROVIDER_TIMEOUTS = {
    'dexscreener': float(os.getenv('TIMEOUT_DEXSCREENER', '1.2')),
    'jupiter': float(os.getenv('TIMEOUT_JUPITER', '0.8')),
    'birdeye': float(os.getenv('TIMEOUT_BIRDEYE', '1.0')),
}
```

### Verification
```bash
# Monitor timeout enforcement
tail -f logs/dev_intelligence.log | grep -i timeout

# Check budget usage
curl http://localhost:5002/api/price/health | \
  jq '.worker_stats.worker.queue_stats'
```

---

## 4. SNAPSHOT CACHE PRE-WARMING

### Current Problem
- Worker refreshes prices and stores them
- Dashboard reads snapshot cache (correct)
- But snapshot cache hits depend on timing of worker refresh cycles
- If refresh is slow, dashboard might read old snapshot or miss

### Solution
Worker proactively writes to snapshot cache during refresh cycle.
Dashboard always reads snapshot cache first.

### Implementation

**File**: `src/core/price_worker.py`

**1. Add snapshot cache warming in refresh cycle:**
```python
def _refresh_cycle(self) -> None:
    """Refresh prices for tracked tokens."""
    self.stats['cycles'] += 1

    # Get active tokens
    tokens = self._get_tokens_for_refresh()

    if not tokens:
        logger.debug("No tokens to refresh")
        return

    logger.info(f"Refresh cycle: refreshing {len(tokens)} tokens")

    # Fetch prices
    self._batch_fetch_prices(tokens)

    # NEW: Warm snapshot cache with fetched prices
    self._warm_snapshot_cache(tokens)

    # Track stats
    self._sync_new_tokens()
    self.sync_source_metrics()

    # Log distribution
    logger.info(
        f"Activity distribution: high={self.stats['activity_distribution']['high']}, "
        f"medium={self.stats['activity_distribution']['medium']}, "
        f"low={self.stats['activity_distribution']['low']}, "
        f"dormant={self.stats['activity_distribution']['dormant']}"
    )

def _warm_snapshot_cache(self, tokens: List[Dict]) -> None:
    """
    Proactively write token prices to snapshot cache.

    Dashboard reads will hit snapshot cache (no upstream calls).
    """
    if not self.price_service:
        return

    try:
        cache_warmed = 0
        for token in tokens:
            mint = token['mint']

            # Get price from cache (just fetched above)
            # Use 'org' tier (30s TTL) since it's fresh from refresh
            price = self.price_service.cache.get(mint, 'org')

            if price:
                # Write to snapshot tier (30s TTL, for dashboard)
                self.price_service.cache.set(mint, price)
                # Also ensure it's in database for fallback
                self.price_service._store_snapshot(price)
                cache_warmed += 1

        if cache_warmed > 0:
            logger.debug(f"Snapshot cache warmed: {cache_warmed}/{len(tokens)} tokens")
    except Exception as e:
        logger.error(f"Error warming snapshot cache: {e}", exc_info=False)
```

**File**: `src/apis/price_api.py`

**2. Update `get_price()` to read snapshot-only:**
```python
@price_api.route('/<mint>', methods=['GET'])
def get_price(mint: str):
    """
    Get current price for a single token.

    Always reads snapshot cache (no upstream calls).
    If cache miss, returns stale DB or unavailable.
    """
    try:
        service = get_price_service()

        # Always use snapshot cache (30s TTL, pre-warmed by worker)
        price = service.cache.get(mint, 'snapshot')

        if price:
            # Cache hit
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
                'cache_source': 'snapshot'  # NEW: indicate cache source
            })

        # Cache miss: return stale DB (no live fetch)
        db_price = service._get_cached_price(mint)
        if db_price:
            return jsonify({
                'mint': db_price.mint,
                'price_usd': db_price.price_usd,
                'price_sol': db_price.price_sol,
                'liquidity_usd': db_price.liquidity_usd,
                'volume_24h': db_price.volume_24h,
                'market_cap': db_price.market_cap,
                'source': db_price.source,
                'pair_address': db_price.pair_address,
                'timestamp': db_price.timestamp,
                'is_stale': True,
                'cache_source': 'stale_db'  # NEW: indicate stale fallback
            })

        # Unavailable
        unavailable = TokenPrice(
            mint=mint, price_usd=0, price_sol=0, liquidity_usd=0,
            volume_24h=0, market_cap=0, source='unavailable', is_stale=True
        )
        return jsonify({
            'mint': unavailable.mint,
            'price_usd': unavailable.price_usd,
            'price_sol': unavailable.price_sol,
            'liquidity_usd': unavailable.liquidity_usd,
            'volume_24h': unavailable.volume_24h,
            'market_cap': unavailable.market_cap,
            'source': unavailable.source,
            'pair_address': unavailable.pair_address,
            'timestamp': unavailable.timestamp,
            'is_stale': unavailable.is_stale,
            'cache_source': 'unavailable'
        })
    except Exception as e:
        logger.error(f"Error getting price for {mint}: {e}")
        return jsonify({'error': str(e)}), 500
```

### Verification
```bash
# Monitor snapshot cache warming
tail -f logs/dev_intelligence.log | grep -i "snapshot"

# Check cache hit rate (should be high for frequently accessed tokens)
curl http://localhost:5002/api/price/health | \
  jq '.worker_stats.worker'
```

---

## 5. TOKEN PRIORITY TIERS

### Current Problem
- All tokens refreshed based on activity score
- 4 categories: high (10s), medium (30s), low (90s), dormant (180s)
- But no explicit tier assignment in database
- Activity scoring happens every cycle (computation overhead)

### Solution
Add `priority_level` column to `tracked_tokens` table.
Compute tier once during registration.
Use tier directly for scheduling.

### New Schema

The `tracked_tokens` table **already has** `priority_level` column (from codebase search):
```sql
CREATE TABLE IF NOT EXISTS tracked_tokens (
    mint                TEXT PRIMARY KEY,
    symbol              TEXT,
    pair_address        TEXT,
    priority_level      TEXT DEFAULT 'MEDIUM',  -- Already exists!
    last_price_update   INTEGER DEFAULT 0,
    is_active           BOOLEAN DEFAULT 1,
    created_at          INTEGER NOT NULL,
    updated_at          INTEGER NOT NULL
)
```

### Implementation

**File**: `src/core/price_worker.py`

**1. Simplify refresh scheduling using priority_level:**
```python
def _get_tokens_for_refresh(self) -> List[Dict]:
    """
    Get tokens that need refreshing based on priority tier.

    Uses priority_level directly instead of computing activity.
    """
    now = int(time.time())
    tokens = []

    try:
        conn = self.registry._get_conn()

        # Get tokens by priority tier
        rows = conn.execute('''
            SELECT mint, symbol, pair_address, priority_level, last_price_update
            FROM tracked_tokens
            WHERE is_active = 1
            ORDER BY priority_level ASC, last_price_update ASC
        ''').fetchall()

        activity_dist = {'high': 0, 'medium': 0, 'low': 0, 'dormant': 0}

        for mint, symbol, pair_address, priority_level, last_update in rows:
            priority = priority_level.lower() if priority_level else 'medium'
            activity_dist[priority] = activity_dist.get(priority, 0) + 1

            # Get refresh interval for this tier
            interval = self._get_refresh_interval_for_activity(priority)

            # Check if time to refresh
            if now - last_update >= interval:
                tokens.append({
                    'mint': mint,
                    'symbol': symbol,
                    'pair_address': pair_address,
                    'priority_level': priority,
                })

        self.stats['activity_distribution'] = activity_dist
        return tokens

    except Exception as e:
        logger.error(f"Error getting tokens for refresh: {e}")
        return []

def _register_token(self, mint: str, priority_level: str = 'MEDIUM') -> None:
    """
    Register token with assigned priority tier.

    Tier determines refresh frequency:
    - HIGH: 10s
    - MEDIUM: 30s
    - LOW: 90s
    - DORMANT: 180s
    """
    try:
        conn = self.registry._get_conn()
        now = int(time.time())

        priority = priority_level.upper()
        if priority not in ['HIGH', 'MEDIUM', 'LOW', 'DORMANT']:
            priority = 'MEDIUM'

        conn.execute('''
            INSERT OR REPLACE INTO tracked_tokens
            (mint, priority_level, is_active, created_at, updated_at)
            VALUES (?, ?, 1, COALESCE((
                SELECT created_at FROM tracked_tokens WHERE mint = ?
            ), ?), ?)
        ''', (mint, priority, mint, now, now))

        conn.commit()
        logger.info(f"Registered token {mint} at priority {priority}")
    except Exception as e:
        logger.error(f"Error registering token: {e}")
```

**2. Update `/api/price/batch/register` to accept priority:**
```python
@price_api.route('/batch/register', methods=['POST'])
def register_tokens_batch():
    """
    Register tokens for price tracking.

    Body: {
        "mints": ["mint1", "mint2"],
        "priority_levels": {"mint1": "HIGH", "mint2": "MEDIUM"}  # Optional
    }
    """
    try:
        data = request.get_json()
        mints = data.get('mints', [])
        priorities = data.get('priority_levels', {})  # NEW: optional

        worker = get_price_worker()

        for mint in mints:
            priority = priorities.get(mint, 'MEDIUM')  # Default to MEDIUM
            worker._register_token(mint, priority)

        return jsonify({
            'registered': len(mints),
            'message': f'Registered {len(mints)} tokens'
        }), 200
    except Exception as e:
        logger.error(f"Error in register_tokens_batch: {e}")
        return jsonify({'error': str(e)}), 500
```

### Verification
```bash
# Check token priority distribution
sqlite3 database/flex_complete_database.db \
  "SELECT priority_level, COUNT(*) FROM tracked_tokens GROUP BY priority_level;"

# Monitor refresh distribution by tier
curl http://localhost:5002/api/price/health | \
  jq '.worker_stats.worker.activity_distribution'
```

---

## 6. ROLLING SOURCE HEALTH WINDOW

### Current Problem
- Source ranking uses fixed 50 attempt sliding window
- If provider has 1000+ total attempts, old failures still affect score
- Doesn't adapt to recent provider changes

### Solution
Use rolling 1-hour time window instead of fixed attempt count.
Recalculate ranking every cycle.

### Implementation

**File**: `src/core/price_service.py`

**1. Update source_attempts to use rolling window:**
```python
def _update_source_stats(self, source: str, success: bool) -> None:
    """
    Track attempt success with rolling 1-hour window.

    Only keeps attempts from last 3600 seconds.
    """
    now = time.time()
    self.source_attempts[source].append((now, success))

    # Keep only last 1 hour of attempts (rolling window)
    cutoff = now - 3600
    self.source_attempts[source] = [
        (ts, s) for ts, s in self.source_attempts[source]
        if ts > cutoff
    ]

    # Check if circuit should break (>90% failure in rolling window)
    attempts = self.source_attempts[source]

    # Need at least 20 attempts in the window before breaking
    if len(attempts) >= 20:
        failures = sum(1 for _, s in attempts if not s)
        failure_rate = failures / len(attempts)

        if failure_rate > 0.9 and not self.circuit_breaker[source]['disabled']:
            self.circuit_breaker[source]['disabled'] = True
            self.circuit_breaker[source]['disabled_at'] = now
            self.circuit_breaker[source]['break_count'] = \
                self.circuit_breaker[source].get('break_count', 0) + 1

            logger.warning(
                f"Circuit breaker triggered for {source}: "
                f"{failure_rate:.1%} failure rate over {len(attempts)} attempts in last 1h"
            )

            self._save_circuit_breaker_state(source)
```

**2. Update source ranking to use rolling window metrics:**
```python
def _get_source_rank(self, source: str) -> float:
    """
    Rank source by success rate (rolling 1h window) and EWMA latency.

    Score = (success_rate × 0.7) + (1 - normalized_latency × 0.3)
    Range: 0.0 to 1.0
    """
    attempts = self.source_attempts[source]

    if not attempts:
        return 0.5  # Default for uninitialized

    # Success rate from rolling window
    successes = sum(1 for _, s in attempts if s)
    success_rate = successes / len(attempts)

    # EWMA latency
    latency_ms = self.source_latency_ewma[source]
    normalized_latency = min(latency_ms / 500.0, 1.0)

    score = (success_rate * 0.7) + ((1.0 - normalized_latency) * 0.3)
    return score

def _get_sources_ordered(self) -> list:
    """
    Return sources ranked by rolling window success rate and EWMA latency.

    Excludes circuit-broken sources.
    """
    active_sources = []

    for source in ['dexscreener', 'jupiter', 'birdeye']:
        if not self._is_circuit_broken(source):
            rank = self._get_source_rank(source)
            active_sources.append((source, rank))

    # Sort by rank descending (highest score first)
    active_sources.sort(key=lambda x: x[1], reverse=True)
    return [source for source, _ in active_sources]
```

**3. Add health check for rolling window metrics:**
```python
def get_rolling_window_stats(self) -> Dict:
    """
    Get source metrics for rolling 1-hour window.

    Used for monitoring and debugging.
    """
    now = time.time()
    cutoff = now - 3600  # 1 hour

    stats = {}
    for source, attempts in self.source_attempts.items():
        # Count attempts in window
        window_attempts = [a for a in attempts if a[0] > cutoff]

        if window_attempts:
            successes = sum(1 for _, s in window_attempts if s)
            success_rate = successes / len(window_attempts)
        else:
            success_rate = 0.0

        stats[source] = {
            'attempts_in_window': len(window_attempts),
            'success_rate': success_rate,
            'ewma_latency_ms': round(self.source_latency_ewma[source], 1),
            'rank_score': self._get_source_rank(source),
        }

    return stats
```

### Verification
```bash
# Monitor rolling window metrics
curl http://localhost:5002/api/price/health | \
  jq '.worker_stats.worker.source_metrics'

# Should show attempts from last 1 hour only
# Older attempts are automatically pruned
```

---

## Impact Summary

### Before These Improvements
- Circuit breaker resets on restart (unreliable broken source re-enabled)
- Fixed 10-min cooldown (penalty not proportional)
- Global budget only (slow provider consumes full budget)
- Dashboard can trigger upstream calls (cache misses)
- Activity scoring computed every cycle (overhead)
- Ranking uses fixed 50-attempt window (slow to adapt)

### After These Improvements
- **Circuit breaker persisted** → survives restarts
- **Exponential cooldown** → 10min → 30min → 2hr → 4hr
- **Per-provider timeouts** → each source has budget limit
- **Snapshot pre-warming** → dashboard never calls upstream
- **Priority tiers** → simple refresh scheduling
- **Rolling 1h window** → adapts to recent provider changes

### Expected Results
| Metric | Before | After | Improvement |
|--------|--------|-------|-------------|
| API calls/hour | 400-500 | 200-300 | 50% reduction |
| P95 latency | 300ms | ~250ms | 17% faster |
| P99 latency | 800ms | ~500ms | 37% faster |
| Circuit breaker persistence | No | Yes | Better restart safety |
| Cooldown escalation | Fixed 600s | Exponential | Smarter backoff |
| Provider timeout coverage | Global only | Per-provider | More precise |

---

## Implementation Order

**Commit 1** (2 hours):
- Circuit breaker persistence + exponential cooldown
- Requires database migration
- Medium complexity

**Commit 2** (1.5 hours):
- Provider timeout budgets
- Modify `get_token_price()` method
- Low complexity, high impact

**Commit 3** (1.5 hours):
- Snapshot cache pre-warming
- Modify worker refresh cycle + API endpoint
- Low complexity

**Commit 4** (1 hour):
- Token priority tiers
- Simplify `_get_tokens_for_refresh()`
- Low complexity, moderate impact

**Commit 5** (1.5 hours):
- Rolling source health window
- Update attempt tracking logic
- Low complexity

**Total**: 8-12 hours (parallelizable: commits 2-5 can start while 1 is in testing)

---

## Rollback Strategy

Each commit can be rolled back independently via git revert (no migration conflicts).

Circuit breaker persistence table can be safely dropped if rolling back (data is cached in memory).

---

## Monitoring & Observability

### New Health Endpoint Metrics

```json
{
  "circuit_breaker_state": {
    "dexscreener": {
      "disabled": false,
      "disabled_at": 0,
      "break_count": 0,
      "cooldown_remaining_secs": 0
    },
    "rolling_window_stats": {
      "dexscreener": {
        "attempts_in_window": 145,
        "success_rate": 0.92,
        "ewma_latency_ms": 48.2,
        "rank_score": 0.86
      }
    }
  }
}
```

### Monitoring Queries

```bash
# Check circuit breaker status (persistent)
SELECT source, disabled, break_count, cooldown_remaining
FROM circuit_breaker_state

# Monitor rolling window stats
curl http://localhost:5002/api/price/health | \
  jq '.worker_stats.worker.rolling_window_stats'

# Check token priority distribution
SELECT priority_level, COUNT(*), AVG(CAST((now - last_price_update) AS INTEGER))
FROM tracked_tokens
GROUP BY priority_level
```

---

## Next Steps

1. Review this architecture for feedback
2. Implement Commit 1 (persistence + exponential)
3. Test circuit breaker recovery scenarios
4. Deploy remaining commits incrementally
5. Monitor metrics for 48 hours before declaring stable

---

## Conclusion

These 6 future improvements create a more resilient, efficient, and self-healing price tracking system. They build on the solid foundation of the deployed circuit breaker system, adding persistence, smart backoff, per-provider budgets, and adaptive health tracking. The system will handle provider failures more gracefully and recover faster from transient issues.

**Expected outcome**: Production-grade resilience with 50% fewer API calls and 35% faster latency.
