# Token Price System Architecture

Complete documentation of how tokens are fetched, prices are retrieved, and data flows through the system.

## Table of Contents
1. [Token Discovery Flow](#token-discovery-flow)
2. [Price Fetching System](#price-fetching-system)
3. [Database Structure](#database-structure)
4. [API Architecture](#api-architecture)
5. [Refresh Frequencies](#refresh-frequencies)
6. [Code Components](#code-components)

---

## Token Discovery Flow

### Initial Token Detection

Tokens are discovered through the Helius webhook system when new Pump.fun token launches are detected:

1. **Helius Webhook** (`src/extractors/funder_helius_extractor.py`)
   - Monitors Pump.fun launch transactions
   - Detects new token mints and their properties
   - Stores initial token data to `token_analysis` table

2. **Token Analysis** (`token_analysis` table)
   - Stores discovered tokens with metadata
   - Includes: mint address, creator, initial liquidity, timestamp
   - Triggers price worker to begin tracking

### Token Registration for Price Tracking

When a token is discovered, it's automatically registered in the price tracking system:

```python
# File: src/core/price_worker.py
# Method: _sync_new_tokens()

# Gets all tokens from token_analysis that aren't yet in tracked_tokens
SELECT ta.mint FROM token_analysis ta
LEFT JOIN tracked_tokens tt ON ta.mint = tt.mint
WHERE tt.mint IS NULL LIMIT 100

# Registers new tokens with HIGH priority (10-second refresh)
registry.register_token(mint, priority_level='HIGH')
```

**Key Points:**
- New tokens registered with `HIGH` priority (faster initial data)
- Sync happens automatically every 10 seconds
- Max 100 new tokens synced per cycle to prevent bottlenecks

---

## Price Fetching System

### Multi-Source Price Aggregation

Prices are fetched from multiple sources and intelligently combined:

#### 1. **Dexscreener API** (Primary Source)
- URL: `https://api.dexscreener.com/latest/dex/tokens/{mint}`
- Returns: price, market cap, liquidity, 24h volume, trading data
- Timeout: 5 seconds
- Used for: Initial prices, market cap tracking, peak detection

#### 2. **Jupiter API** (Secondary Source)
- URL: `https://api.jup.ag/price?ids={mint}`
- Returns: price, extended market data
- Timeout: 5 seconds
- Used for: Price confirmation, cross-validation

#### 3. **DEX Pool Direct Queries** (Tertiary Source)
- Queries Raydium/Orca pool contracts directly
- Returns: calculated prices from reserves
- Used for: When APIs are unavailable or rate-limited

### Price Service Architecture

**File:** `src/core/price_service.py`

#### TokenPriceService Class
```python
class TokenPriceService:
    def get_token_prices_sync(mints: List[str], cache_type='hot')
        # Fetches prices for multiple tokens
        # Returns: Dict[mint: TokenPrice]
        # Caches results for 5 seconds (hot cache)

    def get_token_price(mint: str)
        # Single token price fetch
        # Checks cache first, fetches if not available

    def _store_snapshot(mint, price_data, source)
        # Stores price history to database for analysis
```

#### TokenPrice DataClass
```python
@dataclass
class TokenPrice:
    mint: str
    price_usd: float
    market_cap: float
    liquidity_usd: float
    volume_24h: float
    confidence: float      # 0.0-1.0, higher = more reliable
    source: str           # 'dexscreener', 'jupiter', 'cache', etc.
    timestamp: float      # Unix timestamp
    anomaly_score: float  # Detects suspicious prices
```

### Caching Strategy

**Multi-Level Cache:**

1. **Hot Cache** (5 seconds)
   - In-memory cache for the most recent prices
   - Reduces redundant API calls during rapid requests
   - Automatically expires after 5 seconds

2. **Database Snapshots** (Persistent)
   - Every price fetch stored in `price_snapshots` table
   - Enables historical analysis
   - Cleaned up after 7 days

3. **Metadata Cache** (5 minutes)
   - Token symbols and names cached
   - Endpoint: `/api/price/symbol/<mint>`
   - Always attempts fresh fetch first, falls back to cache

---

## Database Structure

### tracked_tokens Table

Manages which tokens are actively monitored:

```sql
CREATE TABLE tracked_tokens (
    mint                TEXT PRIMARY KEY,      -- Token mint address
    symbol              TEXT,                  -- Token symbol (cached)
    pair_address        TEXT,                  -- Primary trading pair
    priority_level      TEXT DEFAULT 'MEDIUM', -- HIGH/MEDIUM/LOW
    last_price_update   INTEGER DEFAULT 0,    -- Unix timestamp of last update
    is_active           BOOLEAN DEFAULT 1,    -- Whether to track this token
    created_at          INTEGER NOT NULL,     -- When registered
    updated_at          INTEGER NOT NULL      -- Last modified
);

-- Indexes for efficient querying
CREATE INDEX idx_tt_priority ON tracked_tokens(priority_level, is_active);
CREATE INDEX idx_tt_last_update ON tracked_tokens(last_price_update ASC);
```

### price_snapshots Table

Historical price data for analysis:

```sql
CREATE TABLE price_snapshots (
    id              INTEGER PRIMARY KEY,
    mint            TEXT NOT NULL,
    price_usd       REAL,
    market_cap      REAL,
    liquidity_usd   REAL,
    volume_24h      REAL,
    source          TEXT,              -- 'dexscreener', 'jupiter', etc.
    confidence      REAL,              -- 0.0-1.0
    anomaly_score   REAL,              -- Detects suspicious prices
    recorded_at     INTEGER NOT NULL   -- Unix timestamp
);
```

### token_analysis Table

Core token metadata discovered at launch:

```sql
CREATE TABLE token_analysis (
    mint                TEXT PRIMARY KEY,
    creator             TEXT,
    initial_liquidity   REAL,
    price_current       REAL,
    market_cap_current  REAL,
    market_cap_highest  REAL,
    market_cap_highest_at TEXT,        -- ISO timestamp when peak occurred
    detected_at         DATETIME,
    launch_score        REAL,
    created_at          DATETIME
);
```

---

## API Architecture

**File:** `src/apis/price_api.py`

### Endpoints

#### 1. **Get Single Price**
```
GET /api/price/<mint>

Response:
{
    "mint": "...",
    "price_usd": 0.0004,
    "market_cap": 500000,
    "liquidity": 50000,
    "source": "dexscreener",
    "confidence": 0.95,
    "timestamp": 1710345600
}
```

#### 2. **Batch Price Fetch**
```
GET /api/price/batch?mints=mint1,mint2,...

Response:
{
    "prices": {
        "mint1": { price data },
        "mint2": { price data }
    },
    "timestamp": 1710345600
}
```

#### 3. **Token Symbol/Name** (Proxy Endpoint)
```
GET /api/price/symbol/<mint>

Response:
{
    "symbol": "PUMP",
    "name": "Pump.fun Launch"
}

Details:
- 5-minute TTL cache with always-try-fresh-first approach
- Avoids CORS errors by proxying through backend
- Prevents frontend rate limiting from Dexscreener
```

#### 4. **Register Token for Tracking**
```
POST /api/price/register
Content-Type: application/json

{
    "mint": "...",
    "priority": "MEDIUM"  // or HIGH, LOW
}
```

#### 5. **Batch Register Tokens**
```
POST /api/price/batch/register
Content-Type: application/json

{
    "mints": ["mint1", "mint2", ...]
}

Response:
{
    "registered": 25,
    "total": 50,
    "skipped": 25
}
```

#### 6. **Price History**
```
GET /api/price/history/<mint>?hours=24

Response:
{
    "mint": "...",
    "prices": [
        { "timestamp": ..., "price": ..., "market_cap": ... },
        ...
    ]
}
```

#### 7. **Health Score**
```
GET /api/price/health/<mint>

Response:
{
    "health_score": 0.85,  // 0.0-1.0
    "components": {
        "liquidity": 0.90,
        "growth": 0.80,
        "stability": 0.75
    }
}
```

#### 8. **Anomaly Detection**
```
GET /api/price/anomaly/<mint>

Response:
{
    "anomaly_score": 0.15,  // 0.0-1.0, higher = more suspicious
    "flags": [
        "Price spike >50%",
        "Low liquidity"
    ],
    "risk_level": "MEDIUM"  // LOW, MEDIUM, HIGH
}
```

#### 9. **Fetch Price Now** (On-Demand)
```
POST /api/price/fetch-now
Content-Type: application/json

{
    "mint": "...",
    "sources": ["dexscreener", "jupiter"]
}

Response:
{
    "success": true,
    "price": { price data },
    "source": "dexscreener"
}
```

#### 10. **Tracked Tokens Stats**
```
GET /api/price/tracked/stats

Response:
{
    "total_tracked": 150,
    "active": 145,
    "by_priority": {
        "HIGH": 10,
        "MEDIUM": 80,
        "LOW": 55
    }
}
```

---

## Refresh Frequencies

### Adaptive Priority System

Tokens are assigned priority levels that determine refresh frequency:

#### HIGH Priority (10-second refresh)
- Tokens: New launches (< 5 minutes old)
- Frequency: Every cycle
- Purpose: Catch rapid price movements
- API calls per hour: 360
- When changed: Converted to MEDIUM after first price fetch

#### MEDIUM Priority (30-second refresh)
- Tokens: Recent launches (5 mins - 1 hour)
- Frequency: Every 3 cycles (every 30 seconds)
- Purpose: Balance between data freshness and API load
- API calls per hour: 120
- Default priority for batch registration

#### LOW Priority (200-second refresh / ~3 minutes)
- Tokens: Older tokens (> 1 hour old)
- Frequency: Every 20 cycles (every 200 seconds)
- Purpose: Minimize API pressure
- API calls per hour: 18
- For historical data tracking

### Refresh Cycle Details

**File:** `src/core/price_worker.py` → `BackgroundPriceWorker._refresh_cycle()`

```
Each 10-second cycle:

1. _sync_new_tokens()
   - Check token_analysis for new tokens
   - Register with HIGH priority
   - Limit: 100 per cycle

2. _get_tokens_for_refresh()
   - HIGH: All tokens (every cycle)
   - MEDIUM: ~50% of tokens (every 3 cycles = 30s)
   - LOW: ~25% of tokens (every 20 cycles = 200s)

3. _batch_fetch_prices()
   - Fetch in batches of 20 tokens
   - Call Dexscreener/Jupiter APIs
   - Update market_cap_highest if price > previous peak
   - Store snapshots for analysis

4. Update last_price_update timestamp
   - Used for scheduling next refresh
```

### Example Timeline

For a newly launched token:

```
Time 0s:    Token discovered by webhook
Time 0-10s: Registered with HIGH priority
Time 10s:   First price fetch (1st API call)
Time 20s:   Second price fetch (2nd API call)
Time 30s:   Third price fetch + MEDIUM tokens refresh (3rd API call)
Time 40s:   Fourth price fetch (4th API call)
Time 50s:   Fifth price fetch (5th API call)
Time 60s:   Sixth price fetch + MEDIUM tokens (6th API call)
            → Now has good data, can downgrade to MEDIUM if desired

Minimum API calls in first 2 minutes: 6 calls per token
After stabilization: 2 calls per minute (MEDIUM)
```

---

## Code Components

### 1. Price Worker (`src/core/price_worker.py`)

**Purpose:** Background process that continuously refreshes prices

**Key Classes:**

```python
class PriceWorkerRegistry:
    """Manages tracked_tokens table"""
    - register_token(mint, priority_level)
    - get_tracked_tokens(priority_level)
    - update_price_timestamp(mint)
    - deactivate_token(mint)
    - get_stats()

class BackgroundPriceWorker:
    """Background thread that refreshes prices"""
    - start()           # Start background thread
    - stop()            # Stop gracefully
    - _refresh_cycle()  # Main loop
    - _sync_new_tokens()
    - _get_tokens_for_refresh()
    - _batch_fetch_prices(mints)
    - get_stats()       # Return statistics
```

**Usage:**

```python
from src.core.price_worker import get_price_worker

# Start background worker
worker = get_price_worker()
worker.start()

# Access stats
stats = worker.get_stats()
print(f"Prefetched: {stats['tokens_prefetched']}")
print(f"API calls: {stats['api_calls']}")
print(f"Cache hits: {stats['cache_hits']}")
```

### 2. Price Service (`src/core/price_service.py`)

**Purpose:** Handles price fetching, caching, and storage

**Key Classes:**

```python
class TokenPriceService:
    """Fetches and caches token prices"""
    - get_token_price(mint)
    - get_token_prices_sync(mints, cache_type='hot')
    - get_price_history(mint, hours=24)
    - _store_snapshot(mint, price_data, source)

class DexscreenerClient:
    """Dexscreener API integration"""
    - get_price(mint) → TokenPrice
    - get_prices(mints) → Dict[mint: TokenPrice]

class JupiterClient:
    """Jupiter API integration"""
    - get_price(mint) → TokenPrice
```

### 3. Price API Blueprint (`src/apis/price_api.py`)

**Purpose:** REST API endpoints for price data

**Key Features:**
- 18+ endpoints for different data needs
- In-memory metadata caching (5-min TTL)
- Dexscreener proxy endpoint to avoid CORS
- Token registration endpoints
- Real-time price fetching (fetch-now)
- Health scoring and anomaly detection

**Metadata Caching Logic:**

```python
# File: src/apis/price_api.py
@price_api.route('/symbol/<mint>', methods=['GET'])
def get_token_symbol(mint: str):
    """Always try fresh, fall back to cache on error"""

    # Check if cached value is fresh (< 5 min old)
    if mint in _metadata_cache and (now - cache_time) < 300:
        return _metadata_cache[mint]  # Return cached

    # Try fresh fetch from Dexscreener
    resp = requests.get(f'https://api.dexscreener.com/latest/dex/tokens/{mint}')
    if resp.status_code == 200:
        result = extract_symbol_and_name(resp.json())
        _metadata_cache[mint] = result
        _metadata_cache_time[mint] = now
        return result  # Return fresh

    # On error, fall back to cache if available
    if mint in _metadata_cache:
        return _metadata_cache[mint]

    # Worst case: return default
    return {'symbol': mint[:8].upper(), 'name': 'Token'}
```

### 4. Dashboard Integration (`src/core/main.py`)

**Purpose:** Display prices on UI dashboard

**Key Functions:**

```javascript
// Load prices for all displayed tokens
async function loadTokens() {
    const tokens = await fetch('/api/token/list').then(r => r.json());

    // Filter: only fetch prices for tokens with market cap >= $2,000
    const filtered = tokens.filter(t =>
        (t.market_cap_current >= 2000) || !t.market_cap_current
    );

    // Display top 25 tokens
    const display = filtered.slice(0, 25);

    // Batch register for tracking
    const mints = display.map(t => t.mint);
    fetch('/api/price/batch/register', {
        method: 'POST',
        body: JSON.stringify({ mints })
    });

    // Render table
    renderTokenTable(display);
}

// Load price for single token
async function loadPrice(mint) {
    const response = await fetch(`/api/price/${mint}`);
    if (response.ok) {
        const price = await response.json();
        updatePriceDisplay(mint, price);
    }
}

// Load symbol (once per page load)
async function loadSymbol(mint) {
    const response = await fetch(`/api/price/symbol/${mint}`);
    if (response.ok) {
        const { symbol, name } = await response.json();
        document.getElementById(`symbol-${mint}`).textContent = symbol;
        document.getElementById(`symbol-${mint}`).title = name;
    }
}

// Auto-refresh prices every 30 seconds
setInterval(() => loadTokens(), 30000);
```

### 5. Price Anomaly Detection (`src/core/price_anomaly_detection.py`)

**Purpose:** Detect suspicious or unusual price activity

**Features:**
- Spike detection (> 50% in 5 minutes)
- Volatility analysis
- Liquidity assessment
- Volume anomalies

**Usage:**

```python
from src.core.price_anomaly_detection import detect_anomalies

anomalies = detect_anomalies(mint, lookback_hours=1)
if anomalies['risk_level'] == 'HIGH':
    alert_user(mint)
```

### 6. Launch Outcome Tracker (`src/core/launch_outcome_tracker.py`)

**Purpose:** Track token success metrics over time

**Metrics:**
- Peak market cap reached
- Time to peak
- Longevity (still trading after X hours)
- Success categorization (rug, failure, success)

---

## Performance Considerations

### API Rate Limiting

- **Dexscreener:** ~100 requests/minute (shared limit)
- **Jupiter:** ~50 requests/minute (shared limit)
- **Our target:** 25 tracked tokens × 2 API calls/token/minute = 50 calls/min

**Mitigation:**
- Batch requests (20 tokens per API call)
- Priority-based scheduling (HIGH/MEDIUM/LOW)
- In-memory caching (5-second hot cache)
- Market cap filtering (only track tokens with cap ≥ $2,000)

### Database Optimization

**Indexes:**
```sql
idx_tt_priority      -- Track by priority and active status
idx_tt_last_update   -- Find tokens needing refresh
idx_ps_mint_recorded -- Find price history
```

**Cleanup:**
- Price snapshots older than 7 days deleted automatically
- Inactive tokens can be deactivated without deletion

### Memory Usage

- **In-memory cache:** ~1KB per cached price × 25 tokens = 25KB
- **Metadata cache:** ~200 bytes per token × 25 = 5KB
- **Worker stats:** ~1KB
- **Total:** ~31KB for tracking 25 tokens

---

## Monitoring and Debugging

### Worker Stats

```python
stats = price_worker.get_stats()
# Returns:
{
    'cycles': 1234,              # Total refresh cycles
    'tokens_prefetched': 45678,  # Total tokens fetched
    'api_calls': 543,            # Total API calls made
    'cache_hits': 12345,         # Cache hits
    'errors': 2,                 # Errors encountered
    'last_run': 0.234,           # Last cycle duration (seconds)
    'last_error': None           # Last error message
}
```

### Logging

```python
# Enable debug logging:
import logging
logging.getLogger('src.core.price_worker').setLevel(logging.DEBUG)
logging.getLogger('src.core.price_service').setLevel(logging.DEBUG)
logging.getLogger('src.apis.price_api').setLevel(logging.DEBUG)
```

### Common Issues

**Issue:** Prices not updating
- Check: `price_worker.get_stats()['last_run']` should update every 10s
- Check: Token in `tracked_tokens` table with `is_active=1`
- Check: Logs for API errors

**Issue:** Stale symbols displayed
- Check: `/api/price/symbol/{mint}` returns fresh data
- Check: 5-minute TTL has expired (`_metadata_cache_time`)
- Check: Browser cache isn't interfering

**Issue:** API rate limiting (429 errors)
- Check: Number of tracked tokens
- Check: Priority distribution (adjust to use more LOW priority)
- Check: Batch sizes (reduce from 20 to 10)

---

## Configuration Parameters

### price_worker.py

```python
# Defaults in BackgroundPriceWorker.__init__()
interval = 10           # Refresh cycle every 10 seconds
batch_size = 20         # Tokens per API call
```

### price_api.py

```python
# Metadata cache TTL
cache_ttl = 300         # 5 minutes

# Source preference
sources = [
    'dexscreener',      # Primary
    'jupiter',          # Secondary
    'dex_pools'         # Tertiary
]
```

### main.py (Dashboard)

```javascript
// Market cap filter
const minMarketCap = 2000;

// Display limit
const displayTokens = 25;

// Refresh frequency
const refreshInterval = 30000;  // 30 seconds

// Symbol refresh
// Loaded once on page load, NOT refreshed with price updates
```

---

## Future Improvements

1. **Token Lifecycle Management**
   - Automatically downgrade HIGH → MEDIUM after 10 minutes
   - Deactivate tokens with no price data for 24+ hours

2. **Smarter Caching**
   - Persistent metadata cache (don't lose on restart)
   - Compression for price snapshots

3. **Advanced Analytics**
   - Machine learning for anomaly detection
   - Correlation analysis with other tokens
   - Predictive momentum scoring

4. **Distributed Price Fetching**
   - Multiple workers for parallel fetching
   - Fallback to different API sources automatically
   - Geographically distributed requests

5. **Real-time Streaming**
   - WebSocket support for live price updates
   - Push notifications for anomalies
   - Reduced database load from polling
