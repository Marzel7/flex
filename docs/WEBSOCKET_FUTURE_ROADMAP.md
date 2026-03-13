# WebSocket Pool Subscriptions — Future Enhancements Roadmap

**Date:** March 14, 2026
**Status:** Roadmap for Q2-Q4 2026
**Current Phase:** Phase 1 Complete (Basic WebSocket subscriptions)

---

## Overview

The current implementation provides a solid foundation for event-driven pool pricing. This roadmap outlines planned enhancements to improve resilience, reduce manipulation risk, and provide better observability.

---

## Phase 2: Multi-Pool Aggregation (Q2 2026)

### Problem
- Currently: 1 token = 1 pool price
- Risk: Single pool manipulation, low liquidity pumps
- Solution: Aggregate multiple pools using liquidity-weighted median

### Implementation

**New Class: `PoolAggregator`** (pool_price_engine.py)

```python
class PoolAggregator:
    """
    Aggregate prices from multiple pools for same token.
    Uses liquidity-weighted median to prevent outliers.
    """

    MIN_POOLS_FOR_AGGREGATION = 2
    LIQUIDITY_WEIGHT_MIN = 1_000.0  # $1K minimum

    @staticmethod
    def aggregate_prices(
        prices: List[TokenPrice],
        liquidity_values: List[float],
    ) -> TokenPrice:
        """
        Compute liquidity-weighted median price.
        Returns aggregated price with combined liquidity.
        """
        # Filter out low-liquidity outliers
        filtered = [
            (p, l) for p, l in zip(prices, liquidity_values)
            if l >= PoolAggregator.LIQUIDITY_WEIGHT_MIN
        ]

        if len(filtered) < PoolAggregator.MIN_POOLS_FOR_AGGREGATION:
            return None  # Fall back to single pool or API

        # Calculate liquidity-weighted median
        total_liquidity = sum(l for _, l in filtered)
        weights = [l / total_liquidity for _, l in filtered]

        # Sort by price, find weighted median
        sorted_prices = sorted(
            [(p, w) for p, w in zip(prices, weights)],
            key=lambda x: x[0].price_usd
        )

        cumulative_weight = 0.0
        for price, weight in sorted_prices:
            cumulative_weight += weight
            if cumulative_weight >= 0.5:
                return price  # Use closest pool to median

        return prices[0]
```

**Database Changes:**

```sql
-- Map mints to multiple pools
CREATE TABLE token_pool_mapping (
    mint TEXT PRIMARY KEY,
    pool_accounts JSON,  -- [{base, quote, program, weight}, ...]
    created_at INTEGER,
    updated_at INTEGER
);
```

**API Endpoint:**

```python
@price_api.route('/pool/register-multiple', methods=['POST'])
def register_multiple_pools():
    """Register multiple pools for same token."""
    # body: {
    #   "mint": "...",
    #   "pools": [
    #     {"base_account": "...", "quote_account": "...", ...},
    #     {"base_account": "...", "quote_account": "...", ...}
    #   ]
    # }
    # Returns aggregated price from all pools
```

**Benefits:**
- ✅ Prevents single-pool manipulation
- ✅ Better price discovery (median is robust)
- ✅ Detects rug pools (low liquidity excluded)
- ✅ Increases confidence in price signal

**Testing:**
```bash
# Register multiple pools for same token
curl -X POST http://localhost:5002/api/pool/register-multiple \
  -d '{"mint": "...", "pools": [pool1, pool2, pool3]}'

# Verify aggregated price
curl http://localhost:5002/api/price/{MINT} | jq '{source, price_usd, liquidity_usd}'
# → Aggregated from all 3 pools
```

---

## Phase 3: WebSocket Provider Failover (Q2-Q3 2026)

### Problem
- Currently: Single Helius endpoint
- Risk: Helius outage → no prices
- Solution: Round-robin failover to QuickNode, Triton, etc.

### Implementation

**New Class: `PoolWebSocketPool`** (pool_price_engine.py)

```python
class PoolWebSocketPool:
    """
    Manage pool of WebSocket connections to multiple RPC providers.
    Automatically switches to backup if primary stalls.
    """

    PROVIDERS = [
        {"name": "helius", "url": "wss://mainnet.helius-rpc.com/"},
        {"name": "quicknode", "url": "wss://mainnet.quiknode.pro/"},  # with API key
        {"name": "triton", "url": "wss://mainnet.triton-rpc.com/"},
    ]

    def __init__(self, state_store: PoolStateStore, db_path: str):
        self.state_store = state_store
        self.clients: Dict[str, PoolWebSocketClient] = {}
        self.active_client: Optional[PoolWebSocketClient] = None
        self.provider_index = 0
        self.stats = {
            "active_provider": None,
            "provider_switches": 0,
            "providers_available": 0,
        }

    async def start(self, pools: List[Dict]) -> None:
        """Start primary client, queue backups."""
        # Start primary
        primary = self.PROVIDERS[0]
        self.clients[primary["name"]] = PoolWebSocketClient(
            self.state_store, url=primary["url"]
        )
        await self.clients[primary["name"]].start(pools)
        self.active_client = self.clients[primary["name"]]
        self.stats["active_provider"] = primary["name"]

        # Start backups asynchronously (don't connect unless needed)
        for provider in self.PROVIDERS[1:]:
            self.clients[provider["name"]] = PoolWebSocketClient(
                self.state_store, url=provider["url"]
            )

    async def monitor_health(self) -> None:
        """Check if active client is healthy, switch if needed."""
        while True:
            await asyncio.sleep(10)

            if not self.active_client or self.active_client.stats["is_stale"]:
                logger.warning(f"Active provider {self.stats['active_provider']} stale, switching...")
                await self._switch_provider()

    async def _switch_provider(self) -> None:
        """Switch to next available provider."""
        for provider in self.PROVIDERS:
            if provider["name"] == self.stats["active_provider"]:
                continue  # Skip current

            try:
                client = self.clients[provider["name"]]
                await client.start(self.active_pools)
                self.active_client = client
                self.stats["active_provider"] = provider["name"]
                self.stats["provider_switches"] += 1
                logger.info(f"Switched to {provider['name']}")
                return
            except Exception as e:
                logger.warning(f"Provider {provider['name']} unavailable: {e}")
                continue

        logger.error("All WebSocket providers unavailable, using RPC fallback")
```

**Benefits:**
- ✅ Automatic failover (no manual intervention)
- ✅ Helius outage doesn't stop pricing
- ✅ Seamless provider switching
- ✅ Metrics on provider health

**Monitoring:**
```bash
curl http://localhost:5002/api/price/health | jq '.pool_stats.ws.active_provider'
# → "helius" | "quicknode" | "triton"

curl http://localhost:5002/api/price/health | jq '.pool_stats.ws.provider_switches'
# → N (should be 0 under normal conditions)
```

---

## Phase 4: Auto Pool Discovery (Q3 2026)

### Problem
- Currently: Manual pool registration required
- Limitation: New pools on-chain aren't auto-detected
- Solution: Subscribe to Raydium/Orca program accounts, detect new pools

### Implementation

**New Class: `PoolDiscoveryClient`** (pool_price_engine.py)

```python
class PoolDiscoveryClient:
    """
    Auto-detect new pools from on-chain program events.
    Uses programSubscribe to monitor Raydium AMM, Orca, Meteora programs.
    """

    KNOWN_PROGRAMS = {
        "raydium_amm": "675kPX9MHTjS2zt1qrVq4LaCRXXsSucc",
        "orca_whirlpool": "whirLbMiicVdio4KfUqKKvsLrZtSqwNAUafgJMYco",
        "meteora_dlmm": "LBUZKhRxPF3XQUaZo4XUKF2kBSJVPzqJHMxwmZj7pAi",
    }

    def __init__(self, state_store: PoolStateStore, db_path: str):
        self.state_store = state_store
        self.db_path = db_path
        self._ws_client: Optional[PoolWebSocketClient] = None
        self.stats = {
            "pools_discovered": 0,
            "pools_added": 0,
            "discovery_enabled": False,
        }

    async def start(self) -> None:
        """Start listening for new pool creation events."""
        self._ws_client = PoolWebSocketClient(self.state_store, self.db_path)

        # Subscribe to program accounts
        for program_name, program_id in self.KNOWN_PROGRAMS.items():
            await self._ws_client.subscribe_to_program(program_id)

        self.stats["discovery_enabled"] = True
        logger.info(f"Pool discovery enabled for {len(self.KNOWN_PROGRAMS)} programs")

    async def on_pool_created(self, pool_data: Dict) -> None:
        """Handle new pool creation event."""
        mint = self._extract_mint_from_pool_data(pool_data)

        if not mint:
            return

        # Auto-register pool
        await self._register_pool(
            mint=mint,
            base_account=pool_data["base_account"],
            quote_account=pool_data["quote_account"],
            program=pool_data["program"],
        )

        self.stats["pools_discovered"] += 1
        logger.info(f"Auto-discovered new pool for {mint}")

    async def _register_pool(self, mint: str, **kwargs) -> None:
        """Auto-register discovered pool."""
        # Save to token_pool_accounts table
        import sqlite3
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR IGNORE INTO token_pool_accounts
                (mint, base_account, quote_account, pool_program, ...)
                VALUES (?, ?, ?, ?, ...)
            """, (mint, kwargs["base_account"], kwargs["quote_account"], kwargs["program"]))
            conn.commit()

        self.stats["pools_added"] += 1
        logger.info(f"Registered discovered pool: {mint}")
```

**Benefits:**
- ✅ No manual registration needed
- ✅ Immediate pricing for new tokens
- ✅ Automatic pool tracking
- ✅ Scales with on-chain activity

**Monitoring:**
```bash
curl http://localhost:5002/api/price/health | jq '.pool_stats.discovery'
# → {
#   "enabled": true,
#   "pools_discovered": 42,
#   "pools_added": 41
# }
```

---

## Phase 5: Pool Health Dashboard (Q3-Q4 2026)

### Problem
- Currently: Metrics exist but not visualized
- Pain: Hard to understand WS/pool health at a glance
- Solution: Grafana dashboard with real-time metrics

### Grafana Panels

**1. WebSocket Connection Status**
```
Graph: ws.connected (boolean)
- Green: connected
- Red: disconnected
- Y-axis: time
- Alert threshold: < 100% uptime
```

**2. RPC Fallback Rate**
```
Graph: RPC calls in last 5 min / total API calls
- High line: fallback active (WS stale)
- Low line: WS primary active
- Shows when WS went down
```

**3. Pool Event Rate**
```
Graph: events_received per minute
- Should match trading volume
- Spikes = high activity
- Flat = low volume or WS stall
```

**4. Event Deduplication Rate**
```
Graph: events_deduplicated / events_received
- 5-20% is normal
- >50% = processing lag
- 0% = no block reorgs
```

**5. Stale Pools**
```
Table: List of pools with no updates >5 min
- mint | last_update_time | pool_url | action (deregister)
- Helps identify dead pools
```

**6. Price Source Distribution**
```
Pie chart: pool vs dexscreener vs jupiter vs birdeye vs stale
- Shows % of prices from each source
- Pool dominance = WS healthy
```

**7. Provider Health** (after Phase 3)
```
Graph: Active provider over time
- Helius | QuickNode | Triton | RPC Fallback
- Shows provider switches
- Alert if > 3 switches/hour
```

**8. Multi-Pool Aggregation** (after Phase 2)
```
Table: Tokens with multiple pools
- mint | pool_count | liquidity_usd | aggregated_price | last_update
- Shows confidence in price
```

### Metrics to Export (Prometheus)

```python
# In price_api.py or new prometheus_exporter.py

from prometheus_client import Counter, Gauge, Histogram

# Gauges (current state)
ws_connected = Gauge('pool_ws_connected', 'WebSocket connected')
ws_events_received = Counter('pool_ws_events_total', 'Events received')
ws_events_deduplicated = Counter('pool_ws_deduplicated_total', 'Deduplicated events')
stale_pools = Gauge('pool_stale_count', 'Number of stale pools')
active_pools = Gauge('pool_active_count', 'Number of active pools')

# Histograms (latency tracking)
price_latency = Histogram('pool_price_update_latency_ms', 'Time from event to cache update')
ws_event_rate = Histogram('pool_ws_event_rate_per_min', 'Events per minute')

# Rate metrics
rpc_fallback_rate = Gauge('pool_rpc_fallback_rate', 'Percentage using RPC fallback')
pool_source_ratio = Gauge('pool_source_ratio', 'Pool prices / total prices')
```

**Prometheus Config:**
```yaml
scrape_configs:
  - job_name: 'flex-pool-pricing'
    static_configs:
      - targets: ['localhost:5002']
    metrics_path: '/metrics'
```

**Benefits:**
- ✅ Real-time observability
- ✅ Historical trend analysis
- ✅ Alert automation (wake up on stale pools)
- ✅ Capacity planning (event rate forecasting)

---

## Implementation Timeline

| Phase | Name | Timeline | Effort | Impact |
|-------|------|----------|--------|--------|
| 1 | WebSocket Subscriptions | ✅ Complete | 475 LOC | 94% RPC reduction |
| 2 | Multi-Pool Aggregation | Q2 2026 | 300 LOC | Manipulation protection |
| 3 | Provider Failover | Q2-Q3 2026 | 400 LOC | 99.9% uptime |
| 4 | Auto Pool Discovery | Q3 2026 | 350 LOC | Zero-config scaling |
| 5 | Pool Health Dashboard | Q3-Q4 2026 | 200 LOC | Observability |

---

## Dependencies & Prerequisites

### Phase 2: Multi-Pool Aggregation
- None (builds on Phase 1)
- Database migration: Add `token_pool_mapping` table

### Phase 3: Provider Failover
- QuickNode API key
- Triton API key (optional)
- Connection pooling in PoolWebSocketClient

### Phase 4: Auto Pool Discovery
- Understand Raydium/Orca program IDLs
- Event filtering for pool creation events
- Auto-registration logic

### Phase 5: Pool Health Dashboard
- Prometheus client library (pip install prometheus-client)
- Grafana instance (Docker or standalone)
- Basic Prometheus knowledge

---

## Success Metrics

### Phase 2
- Multi-pool aggregation reduces price variance by 30-50%
- Manipulation attempts detected and rejected

### Phase 3
- Zero downtime during provider outages
- Auto-failover < 5 seconds
- <1 provider switch per week (normal)

### Phase 4
- New pools automatically detected within 1 block
- 100+ pools tracked without manual registration
- Scaling cost: O(events) not O(pools)

### Phase 5
- 95th percentile latency visible in dashboard
- Stale pools detected within 5 minutes
- Alerts fire before users notice

---

## Risk Mitigation

### Phase 2: Multi-Pool Aggregation
- **Risk:** Aggregated price could be worse than best pool
- **Mitigation:** Keep single-pool fallback, alert if variance > threshold

### Phase 3: Provider Failover
- **Risk:** Rapid switching between providers (flapping)
- **Mitigation:** Hysteresis — require 30s stale before switching, 2 min healthy before back-switching

### Phase 4: Auto Pool Discovery
- **Risk:** Register fake/scam pools automatically
- **Mitigation:** Filter by TVL minimum, verify token ownership, whitelist programs

### Phase 5: Pool Health Dashboard
- **Risk:** Dashboard becomes bottleneck (Prometheus scrape load)
- **Mitigation:** Scrape interval 30s, retention 15 days, use aggregation

---

## Notes for Future Implementation

1. **Start with Phase 2** — Multi-pool aggregation is quick win for robustness
2. **Phase 3 & 4 parallel** — No dependency between them
3. **Phase 5 after 2** — Dashboard more useful with aggregation metrics
4. **Keep Phase 1 fallback** — Don't remove RPC polling, only reduce frequency further
5. **Gradual rollout** — Each phase gets staging test before prod

---

## Long-term Vision (Q4 2026+)

Beyond Phase 5, consider:
- **Smart routing:** Predict best pool by liquidity & volatility
- **MEV-aware pricing:** Account for sandwich attacks in volatility
- **Cross-chain pools:** Aggregation across Solana, Ethereum, Base
- **Price feeds:** Make prices available to other protocols (Pyth-like)
- **Historical analysis:** ML models to detect manipulation patterns

---

**Status:** Ready for planning. First task: Create Phase 2 design doc.
**Effort Estimate:** 1,250 LOC total over 6 months
**Team Size:** 1-2 engineers
**Priority:** Medium (Phase 1 solves immediate problem, Phase 2-5 are enhancements)

