# Phase 2: Critical-Path Protection Architecture

**Date:** March 20, 2026
**Status:** DESIGN READY FOR IMPLEMENTATION
**Scope:** Pool discovery isolation, RPC budget protection, per-attempt telemetry
**Expected Improvement:** 80-90s → 15-25s (5-6x further improvement beyond Phase 1)

---

## 1. Root Cause Analysis

### Why Phase 1 Alone Is Insufficient

Phase 1 (retry densification) improved retry timing but revealed a deeper problem:

**The system is not timing-constrained. It is data-ready-constrained and RPC-contention-constrained.**

#### Why TX Parsing Returns "not indexed yet"

1. **Transaction indexing lag (Solana network):** 1-3 seconds from broadcast
2. **Our polling starts immediately:** Attempts at 0.5s, 1s, 1.5s all hit pre-indexed state
3. **TX indexing is all-or-nothing:** Either the TX exists or it doesn't (no partial state)
4. **We handle this correctly:** Densified retries catch the window at 2-3s
5. **But...** Early RPC calls during pre-indexed window still consume RPC quota

**Evidence from logs:**
```
[POOL_RETRY] attempt=1 strategy=tx_parsing candidates=0 (not indexed yet)
[POOL_RETRY] attempt=2 strategy=tx_parsing candidates=0 (not indexed yet)
[POOL_RETRY] attempt=3 strategy=tx_parsing candidates=2 ← Success window opens
```

#### Why RPC Fallback Returns "vaults_not_ready"

1. **Vault account creation lag:** Often created in same TX as migration
2. **RPC node indexing of new accounts:** 2-5 seconds
3. **getTokenLargestAccounts RPC call:** Must scan indexed state
4. **We call this at wrong time:** Attempts 1-3 are probing before indexing complete
5. **Success window:** Usually opens at retry 6-8 (T=5-8 seconds)

**Evidence from logs:**
```
[POOL_RETRY] attempt=1 strategy=rpc_fallback rejected=vaults_not_ready
[POOL_RETRY] attempt=2 strategy=rpc_fallback rejected=vaults_not_ready
[POOL_RETRY] attempt=3 strategy=rpc_fallback rejected=vaults_not_ready
...
[POOL_RETRY] attempt=6 strategy=rpc_fallback accepted ← Success window opens
```

#### The Real Bottleneck: RPC Contention During Critical Window

**Simultaneous activities at T=0 (migration detected):**

1. **Discovery RPC calls** (what we want):
   - getTransaction (TX parsing)
   - getTokenLargestAccounts (RPC fallback)
   - getAccountInfo (owner validation)
   - Total: 3-5 RPC calls per retry × 3 retries = 9-15 calls in first 2s

2. **Unrelated background RPC calls** (what we don't want, happening now):
   - Creator extraction: getTransaction for entire creator history
   - Funding extraction: getTokenAccountsByOwner (multiple calls)
   - Clustering: Historical graph queries
   - WebSocket fallback: Repeated getTransaction calls
   - Price worker fallback: getAccountInfo polling
   - Block labeling: getBlock calls for enrichment
   - Total: 20-50 unrelated RPC calls in first 30s

**Total RPC load during discovery window: 30-65 calls while success threshold needs ~5 critical calls**

**Result:** RPC rate limiting, timeouts, slower responses, delayed discovery

### Why The Pattern Emerges

```
T=0s:   Migration detected
        ├─ Discovery RPC starts (9-15 calls for retries 1-3)
        └─ Background jobs start immediately (20-50 calls)
T=0-2s: TX not indexed → retries fail
        But RPC contention from background jobs delays response
T=2-5s: TX indexed → TX parsing can work
        But RPC contention still high
T=5-8s: Vaults ready → RPC fallback can work
        But RPC contention even higher now (creator extraction ramped)
T=8-30s: Discovery finally succeeds
        After multiple false attempts due to data unavailability + RPC contention
```

### The Fix Strategy

Instead of more retry timing tweaks, we need:

1. **Priority access:** Discovery gets dedicated RPC budget
2. **Early window protection:** Delay background jobs 30-60s
3. **TX-focused early phase:** Poll exact migration TX directly, no RPC fallback noise
4. **Visibility:** Track which retry succeeds and why it succeeds
5. **Feedback loop:** See if the bottleneck was data-ready or RPC-contention

---

## 2. Phase 2 Architecture Changes

### Architecture Principle: Critical-Path First

```
Migration Detected (T=0)
│
├─ CRITICAL PATH (30-60s)
│  ├─ Detect migration TX signature
│  ├─ Poll migration TX (TX parsing)
│  ├─ Extract pool address from TX
│  ├─ Validate pool (RPC owner check)
│  └─ Register pool + trigger WebSocket
│
└─ BACKGROUND PATH (deferred until critical path succeeds or times out)
   ├─ Creator extraction
   ├─ Funding extraction
   ├─ Clustering analysis
   ├─ Block labeling
   └─ Historical enrichment
```

### Key Constants

```python
# Critical window timing
DISCOVERY_CRITICAL_WINDOW_SECONDS = 45  # Delay background jobs for this long
DISCOVERY_HARD_TIMEOUT_SECONDS = 60     # Force success/failure decision at this time

# RPC budget isolation
DISCOVERY_RPC_CONCURRENT_LIMIT = 8      # Discovery can use up to 8 concurrent RPC slots
BACKGROUND_RPC_CONCURRENT_LIMIT = 2     # Background work gets only 2 slots during critical window

# Retry strategy tiers
TX_ONLY_RETRIES = [0.5, 1, 1.5, 2, 3]                      # Retries 1-5: TX only
TX_PLUS_LIGHT_RPC_RETRIES = [5, 8]                         # Retries 6-7: TX + light RPC
TX_PLUS_FULL_RPC_RETRIES = [12, 18, 25, 35, 50]           # Retries 8-12: TX + full RPC
```

### Component Changes Required

#### A. RPC Client Separation

**Current (mixed RPC client):**
```python
self.rpc_client = SomeRPCClient(RPC_URL)
# All calls (discovery, enrichment, price) use same client
```

**Phase 2 (isolated):**
```python
self.discovery_rpc = SomeRPCClient(
    RPC_URL,
    semaphore=asyncio.Semaphore(DISCOVERY_RPC_CONCURRENT_LIMIT)
)
self.background_rpc = SomeRPCClient(
    RPC_URL,
    semaphore=asyncio.Semaphore(BACKGROUND_RPC_CONCURRENT_LIMIT)
)
# During critical window, background_rpc is used for queued (async) work only
```

#### B. Background Job Queue

**Current (immediate execution):**
```python
asyncio.create_task(extract_creator_funding(mint))
asyncio.create_task(extract_funder_transfers(creator))
asyncio.create_task(rebuild_clusters())
```

**Phase 2 (deferred execution):**
```python
class BackgroundJobQueue:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.critical_window_active = True

    async def queue_job(self, job_coro, mint=None, priority="normal"):
        """Queue a job for execution after critical window"""
        item = {
            'coro': job_coro,
            'mint': mint,
            'priority': priority,
            'queued_at': time.time()
        }
        await self.queue.put(item)

    async def process_queue(self):
        """Process jobs after critical window"""
        while True:
            if not self.critical_window_active:
                # Critical window expired, start processing
                while not self.queue.empty():
                    item = await self.queue.get()
                    try:
                        await item['coro']
                    except Exception as e:
                        logger.error(f"Background job failed: {e}")
            await asyncio.sleep(0.1)

# Usage during critical window
background_queue = BackgroundJobQueue()
background_queue.queue_job(extract_creator_funding(mint), mint=mint, priority="high")

# After critical window expires (in main listener loop)
critical_window_timer = asyncio.create_task(critical_window_timer_task(45))
```

#### C. Discovery Attempt Throttling

**Current (no protection):**
```python
for attempt, delay in enumerate(delays, 1):
    await asyncio.sleep(delay)
    # Fire all discovery strategies immediately
    await _retry_pool_discovery(mint, signature, delays)
```

**Phase 2 (protected):**
```python
async def _retry_pool_discovery_protected(self, mint, signature, delays):
    """Protected discovery with RPC isolation and early-window strategy choice"""

    for attempt, delay in enumerate(delays, 1):
        await asyncio.sleep(delay)

        elapsed = time.time() - self.token_discovery_times[mint]["detected"]

        # Choose strategy based on elapsed time
        if elapsed < 5:
            # TX-only window: exact TX polling
            strategy = await self._run_tx_parsing_focused(mint, signature)
        elif elapsed < 15:
            # TX + light RPC window
            strategy = await self._run_tx_plus_light_rpc(mint, signature)
        else:
            # TX + full RPC window: everything enabled
            strategy = await self._run_tx_plus_full_rpc(mint, signature)

        if strategy == "success":
            return

        # After critical window, allow background jobs to process
        if elapsed > DISCOVERY_CRITICAL_WINDOW_SECONDS:
            await background_queue.process_queue()
```

---

## 3. Critical-Path Scheduling Strategy

### Timeline

```
T=0s:   Migration detected
        ├─ token_states[mint] = "pending_discovery"
        ├─ token_discovery_times[mint]["detected"] = now
        ├─ critical_window_expiry = now + 45s
        └─ Queue background jobs (DO NOT START YET)

T=0.5s: Attempt 1 (TX only)
        ├─ Use discovery_rpc (dedicated quota)
        ├─ Run: getTransaction (exact migration TX)
        ├─ Run: Extract pool candidates from TX accounts
        ├─ Run: getAccountInfo (owner validation only)
        └─ Result: Success or retry

T=1s:   Attempt 2 (TX only)
        └─ Same as attempt 1

T=1.5s: Attempt 3 (TX only)
        └─ Same as attempt 1

T=2s:   Attempt 4 (TX only)
        └─ Same as attempt 1
        └─ If still failing: Log "tx_still_not_indexed"

T=3s:   Attempt 5 (TX only)
        └─ Same as attempt 1
        └─ TX should be indexed by now

T=5s:   Attempt 6 (TX + light RPC fallback)
        ├─ Try TX parsing first
        ├─ If TX fails: Try light RPC (just getTokenLargestAccounts, no enrichment)
        └─ Single RPC call for fallback (minimal probing)

T=8s:   Attempt 7 (TX + light RPC fallback)
        └─ Same as attempt 6

T=12s:  Attempt 8 (TX + full RPC fallback)
        ├─ Try TX parsing
        ├─ If TX fails: Try full RPC vault discovery
        └─ May use multiple RPC calls

... Attempts 9-12 continue with full strategy ...

T=45s:  CRITICAL WINDOW EXPIRES
        ├─ If discovery succeeded: Continue normally
        ├─ If discovery still pending: Start processing background queue
        └─ From now on, background jobs run as queued

T=60s:  DISCOVERY HARD TIMEOUT
        ├─ If still not resolved: Mark as unresolved
        ├─ Stop retries
        └─ Log final telemetry
```

### Semaphore Pattern

```python
class DiscoveryContext:
    def __init__(self):
        self.discovery_semaphore = asyncio.Semaphore(8)
        self.background_semaphore = asyncio.Semaphore(2)
        self.critical_window_active = True

    async def call_discovery_rpc(self, method, params):
        """Discovery RPC calls get priority"""
        async with self.discovery_semaphore:
            return await self.rpc_client.call(method, params)

    async def call_background_rpc(self, method, params):
        """Background RPC calls are throttled during critical window"""
        if self.critical_window_active:
            # During critical window, wait for discovery to finish
            async with self.background_semaphore:
                return await self.rpc_client.call(method, params)
        else:
            # After critical window, no throttling needed
            return await self.rpc_client.call(method, params)
```

### Background Job Delay Pattern

```python
async def _process_migration_with_mint(self, signature, logs, mint, tx_data):
    """Critical path first"""

    # Immediate: Minimal token entry
    await self._create_minimal_token_entry(mint)

    # Immediate: Start discovery retries (protected)
    discovery_task = asyncio.create_task(
        self._retry_pool_discovery_protected(mint, signature, delays)
    )

    # QUEUE (not execute): Creator extraction
    self.background_queue.queue_job(
        self._extract_creator_async(mint, signature, tx_data),
        mint=mint,
        priority="high"
    )

    # QUEUE (not execute): Funding extraction
    self.background_queue.queue_job(
        extract_funder_transfers_async(creator),
        mint=mint,
        priority="normal"
    )

    # QUEUE (not execute): Clustering
    self.background_queue.queue_job(
        rebuild_clusters_async(),
        mint=mint,
        priority="low"
    )

    # Wait for discovery to complete (or timeout)
    try:
        await asyncio.wait_for(discovery_task, timeout=DISCOVERY_HARD_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        logger.warning(f"Discovery timeout for {mint}")
```

---

## 4. Retry Execution Matrix

### Strategy Selection by Retry Number

| Retry | Delay | Elapsed | Strategy | TX Parse | RPC Light | RPC Full | Notes |
|-------|-------|---------|----------|----------|-----------|----------|-------|
| 1 | 0.5s | 0.5s | TX only | ✓ | ✗ | ✗ | Early: TX not indexed yet |
| 2 | 1s | 1.5s | TX only | ✓ | ✗ | ✗ | Still likely pre-indexed |
| 3 | 1.5s | 3s | TX only | ✓ | ✗ | ✗ | TX should be indexed by now |
| 4 | 2s | 5s | TX only | ✓ | ✗ | ✗ | TX definitely indexed |
| 5 | 3s | 8s | TX only | ✓ | ✗ | ✗ | Final TX-only attempt |
| 6 | 5s | 13s | TX + light RPC | ✓ | ✓ | ✗ | Single getTokenLargestAccounts |
| 7 | 8s | 21s | TX + light RPC | ✓ | ✓ | ✗ | Vaults should be ready |
| 8 | 12s | 33s | TX + full RPC | ✓ | ✓ | ✓ | Full discovery, still in critical window |
| 9 | 18s | 51s | TX + full RPC | ✓ | ✓ | ✓ | Past critical window, but still trying |
| 10 | 25s | 76s | TX + full RPC | ✓ | ✓ | ✓ | Late attempt |
| 11 | 35s | 111s | TX + full RPC | ✓ | ✓ | ✓ | Very late |
| 12 | 50s | 161s | TX + full RPC | ✓ | ✓ | ✓ | Final attempt |

### Implementation

```python
async def _retry_pool_discovery_protected(self, mint, signature, delays):
    """Protected discovery with strategy tier selection"""

    for attempt, delay in enumerate(delays, 1):
        await asyncio.sleep(delay)

        elapsed = time.time() - self.token_discovery_times[mint]["detected"]

        # Determine strategy tier
        if attempt <= 5:
            # Tier 1: TX only (0.5-8s window)
            success = await self._attempt_tx_parsing_only(
                mint, signature, attempt, elapsed
            )
        elif attempt <= 7:
            # Tier 2: TX + light RPC fallback (13-21s window)
            success = await self._attempt_tx_plus_light_rpc(
                mint, signature, attempt, elapsed
            )
        else:
            # Tier 3: TX + full RPC fallback (33s+ window)
            success = await self._attempt_tx_plus_full_rpc(
                mint, signature, attempt, elapsed
            )

        if success:
            return

        # Check if critical window expired
        if elapsed > DISCOVERY_CRITICAL_WINDOW_SECONDS:
            await self.background_queue.process_pending()

async def _attempt_tx_parsing_only(self, mint, signature, attempt, elapsed):
    """Tier 1: TX parsing only - direct migration TX polling"""

    log_print(
        f"[DISCOVERY_T1] attempt={attempt} elapsed={elapsed:.1f}s tx_parse_only",
        flush=True
    )

    try:
        # Fetch exact migration transaction
        tx_data = await self._get_transaction_with_timeout(signature, timeout=3)

        if not tx_data:
            self._log_attempt(
                mint, attempt, elapsed,
                strategy="tx_parsing",
                status="tx_not_fetched",
                reason="rpc_error"
            )
            return False

        # Extract pool candidates
        candidates = await self._extract_pool_candidates_from_tx(tx_data)

        if not candidates:
            self._log_attempt(
                mint, attempt, elapsed,
                strategy="tx_parsing",
                status="no_candidates",
                reason="candidates_not_in_tx"
            )
            return False

        # Try each candidate with owner validation only
        for candidate in candidates:
            owner = await self._check_owner_only(candidate)

            if owner not in AMMPrograms.ALL:
                self._log_rejection(
                    mint, attempt, candidate,
                    reason="owner_mismatch"
                )
                continue

            # Try to register
            if await self._register_pool(candidate, mint):
                self._log_success(
                    mint, attempt, elapsed,
                    strategy="tx_parsing",
                    pool=candidate
                )
                return True
            else:
                self._log_rejection(
                    mint, attempt, candidate,
                    reason="registration_failed"
                )

        return False

    except Exception as e:
        self._log_attempt(
            mint, attempt, elapsed,
            strategy="tx_parsing",
            status="exception",
            reason=str(e)[:50]
        )
        return False
```

---

## 5. RPC Isolation Design

### Semaphore-Based Isolation

```python
class IsolatedRPCClient:
    """RPC client with isolated quota for discovery vs background"""

    def __init__(self, rpc_url):
        self.rpc_url = rpc_url
        self.base_client = RPCClient(rpc_url)

        # Semaphores: discovery has 8 slots, background has 2
        self.discovery_sem = asyncio.Semaphore(8)
        self.background_sem = asyncio.Semaphore(2)

        # Track quota usage
        self.discovery_calls_inflight = 0
        self.background_calls_inflight = 0

    async def call_discovery(self, method, params):
        """Prioritized RPC call for pool discovery"""
        async with self.discovery_sem:
            self.discovery_calls_inflight += 1
            try:
                result = await asyncio.wait_for(
                    self.base_client.call(method, params),
                    timeout=5.0  # 5s timeout
                )
                return result
            except asyncio.TimeoutError:
                logger.warning(f"Discovery RPC timeout: {method}")
                return None
            finally:
                self.discovery_calls_inflight -= 1

    async def call_background(self, method, params):
        """Throttled RPC call for background enrichment"""
        async with self.background_sem:
            self.background_calls_inflight += 1
            try:
                result = await asyncio.wait_for(
                    self.base_client.call(method, params),
                    timeout=10.0  # 10s timeout for background
                )
                return result
            except asyncio.TimeoutError:
                logger.warning(f"Background RPC timeout: {method}")
                return None
            finally:
                self.background_calls_inflight -= 1

    def get_discovery_quota_available(self):
        """How many concurrent discovery slots available?"""
        return 8 - self.discovery_calls_inflight

    def get_background_quota_available(self):
        """How many concurrent background slots available?"""
        return 2 - self.background_calls_inflight

# Usage in listener
self.isolated_rpc = IsolatedRPCClient(RPC_HTTP)

# Discovery calls
owner = await self.isolated_rpc.call_discovery(
    "getAccountInfo",
    [pool_address, {"encoding": "base64"}]
)

# Background calls (throttled)
balance = await self.isolated_rpc.call_background(
    "getTokenAccountBalance",
    [vault_account]
)
```

### Queue-Based Isolation (Alternative)

```python
class DiscoveryRPCQueue:
    """Priority queue: discovery calls jump ahead of background"""

    def __init__(self, rpc_url, discovery_workers=8, background_workers=2):
        self.rpc_url = rpc_url
        self.discovery_queue = asyncio.PriorityQueue()
        self.background_queue = asyncio.Queue()

        # Start worker tasks
        for i in range(discovery_workers):
            asyncio.create_task(self._discovery_worker(i))
        for i in range(background_workers):
            asyncio.create_task(self._background_worker(i))

    async def call_discovery(self, method, params, priority=0):
        """Queue discovery call with priority"""
        future = asyncio.Future()
        await self.discovery_queue.put((priority, {
            'method': method,
            'params': params,
            'future': future
        }))
        return await future

    async def call_background(self, method, params):
        """Queue background call (normal priority)"""
        future = asyncio.Future()
        await self.background_queue.put({
            'method': method,
            'params': params,
            'future': future
        })
        return await future

    async def _discovery_worker(self, worker_id):
        """Process discovery queue (high priority)"""
        while True:
            priority, item = await self.discovery_queue.get()
            try:
                result = await asyncio.wait_for(
                    RPCClient(self.rpc_url).call(
                        item['method'],
                        item['params']
                    ),
                    timeout=5.0
                )
                item['future'].set_result(result)
            except Exception as e:
                item['future'].set_exception(e)

    async def _background_worker(self, worker_id):
        """Process background queue (low priority)"""
        while True:
            item = await self.background_queue.get()
            try:
                result = await asyncio.wait_for(
                    RPCClient(self.rpc_url).call(
                        item['method'],
                        item['params']
                    ),
                    timeout=10.0
                )
                item['future'].set_result(result)
            except Exception as e:
                item['future'].set_exception(e)
```

### Recommendation

**Use semaphores for simplicity, queues for fine-grained control:**

- **Semaphores (recommended for Phase 2):** Simple to implement, works well with existing code
- **Queues (future optimization):** More control over retry priority, useful if discovery needs emergency bumps

---

## 6. Structured Logging & Telemetry Design

### Per-Attempt Structured Logging

Every discovery attempt now emits structured data:

```python
@dataclass
class DiscoveryAttempt:
    mint: str
    attempt_number: int
    elapsed_seconds: float
    strategy: str  # "tx_parsing", "rpc_light", "rpc_full"
    tx_fetch_status: str  # "success", "not_indexed", "rpc_error", "timeout"
    candidate_count: int
    rejections: List[str]  # ["owner_mismatch", "registration_failed"]
    success: bool
    winning_pool: Optional[str]
    rpc_quota_available: int
    background_jobs_queued: int
    timestamp: float

def _log_attempt(self, attempt: DiscoveryAttempt):
    """Emit structured discovery attempt log"""
    log_print(
        f"[DISCOVERY_ATTEMPT] "
        f"mint={attempt.mint[:16]} "
        f"attempt={attempt.attempt_number} "
        f"elapsed={attempt.elapsed_seconds:.1f}s "
        f"strategy={attempt.strategy} "
        f"tx_status={attempt.tx_fetch_status} "
        f"candidates={attempt.candidate_count} "
        f"rejections={','.join(attempt.rejections)} "
        f"success={attempt.success} "
        f"rpc_available={attempt.rpc_quota_available} "
        f"bg_queued={attempt.background_jobs_queued}",
        flush=True
    )

    # Also store in database for later analysis
    self._write_discovery_attempt_telemetry(attempt)
```

### Example Log Output

```
[DISCOVERY_ATTEMPT] mint=5cDhM4vHfqQ... attempt=1 elapsed=0.5s strategy=tx_parsing tx_status=not_indexed candidates=0 rejections=tx_not_indexed success=False rpc_available=8 bg_queued=3

[DISCOVERY_ATTEMPT] mint=5cDhM4vHfqQ... attempt=2 elapsed=1.5s strategy=tx_parsing tx_status=not_indexed candidates=0 rejections=tx_not_indexed success=False rpc_available=8 bg_queued=3

[DISCOVERY_ATTEMPT] mint=5cDhM4vHfqQ... attempt=3 elapsed=3s strategy=tx_parsing tx_status=success candidates=2 rejections=owner_mismatch success=False rpc_available=7 bg_queued=3

[DISCOVERY_ATTEMPT] mint=5cDhM4vHfqQ... attempt=4 elapsed=5s strategy=tx_parsing tx_status=success candidates=2 rejections=owner_mismatch success=False rpc_available=8 bg_queued=3

[DISCOVERY_ATTEMPT] mint=5cDhM4vHfqQ... attempt=5 elapsed=8s strategy=tx_plus_light_rpc tx_status=success candidates=2 rejections=owner_mismatch success=True winning_pool=9XaBfT... rpc_available=6 bg_queued=3
```

### Rejection Reason Codes

```python
class RejectionReason:
    """Standardized rejection reason codes"""

    # TX parsing reasons
    TX_NOT_INDEXED = "tx_not_indexed"  # TX not yet in index
    TX_FETCH_FAILED = "tx_fetch_failed"  # RPC error getting TX
    TX_TIMEOUT = "tx_timeout"  # Timeout waiting for TX
    CANDIDATES_NOT_IN_TX = "candidates_not_in_tx"  # No AMM accounts in TX
    CANDIDATE_COUNT_ZERO = "candidate_count_zero"  # No candidates extracted

    # Validation reasons
    OWNER_MISMATCH = "owner_mismatch"  # Pool owner not in AMMPrograms
    MINT_MISMATCH = "mint_mismatch"  # Mint doesn't match token
    INVALID_PROGRAM = "invalid_program"  # Program ID not recognized
    IDENTICAL_ACCOUNTS = "identical_accounts"  # base == quote (invalid)
    ZERO_RESERVES = "zero_reserves"  # No liquidity

    # RPC fallback reasons
    VAULTS_NOT_READY = "vaults_not_ready"  # Vault accounts not indexed
    RPC_ERROR = "rpc_error"  # RPC call failed
    RPC_TIMEOUT = "rpc_timeout"  # RPC call timed out
    NO_LARGEST_ACCOUNTS = "no_largest_accounts"  # getTokenLargestAccounts returned empty

    # Registration reasons
    REGISTRATION_FAILED = "registration_failed"  # Registration returned False
    REGISTRATION_ERROR = "registration_error"  # Registration threw exception

    # Other
    UNKNOWN_ERROR = "unknown_error"
```

### Database Schema for Telemetry

```sql
CREATE TABLE discovery_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mint TEXT NOT NULL,
    attempt_number INTEGER NOT NULL,
    elapsed_seconds REAL NOT NULL,
    strategy TEXT NOT NULL,  -- tx_parsing, rpc_light, rpc_full
    tx_fetch_status TEXT,
    candidate_count INTEGER,
    rejection_reasons TEXT,  -- JSON list or CSV
    success BOOLEAN,
    winning_pool TEXT,
    rpc_quota_available INTEGER,
    background_jobs_queued INTEGER,
    timestamp INTEGER NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_mint (mint),
    INDEX idx_attempt (attempt_number),
    INDEX idx_strategy (strategy),
    INDEX idx_created (created_at)
);

CREATE TABLE discovery_summary (
    mint TEXT PRIMARY KEY,
    first_success_attempt INTEGER,
    first_success_elapsed_seconds REAL,
    final_success_attempt INTEGER,
    final_success_elapsed_seconds REAL,
    winning_strategy TEXT,
    total_attempts INTEGER,
    total_rejections INTEGER,
    rejection_reasons TEXT,  -- JSON counts
    rpc_quota_min INTEGER,
    background_jobs_max_queued INTEGER,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_created (created_at)
);
```

---

## 7. Metrics and SLO Targets

### Key Metrics to Track

#### Latency Metrics

```sql
-- Median resolve time (what matters most)
SELECT
    percentile_cont(0.5) WITHIN GROUP (ORDER BY final_success_elapsed_seconds)
        as median_resolve_seconds,
    percentile_cont(0.75) WITHIN GROUP (ORDER BY final_success_elapsed_seconds)
        as p75_resolve_seconds,
    percentile_cont(0.90) WITHIN GROUP (ORDER BY final_success_elapsed_seconds)
        as p90_resolve_seconds,
    percentile_cont(0.99) WITHIN GROUP (ORDER BY final_success_elapsed_seconds)
        as p99_resolve_seconds
FROM discovery_summary
WHERE created_at > datetime('now', '-1 hour');
```

**Expected progression:**
- **Before Phase 1:** Median 82-87s
- **After Phase 1:** Median 8-12s (8-10x improvement)
- **After Phase 2:** Median 5-15s (additional 1.5-2.5x improvement, total 10-20x)

#### Availability Metrics (What's blocking?)

```sql
-- Why are we failing early?
SELECT
    tx_fetch_status,
    COUNT(*) as count,
    AVG(first_success_attempt) as avg_success_attempt
FROM discovery_attempts
WHERE strategy = 'tx_parsing'
GROUP BY tx_fetch_status
ORDER BY count DESC;
```

**Expected:**
- `success` → 85%+ (TX is available and parsed correctly)
- `not_indexed` → 5-10% (TX exists but not yet indexed)
- `rpc_error` → <5% (RPC issues)

```sql
-- Are vaults ready when we probe them?
SELECT
    rejection_reasons,
    COUNT(*) as count
FROM discovery_attempts
WHERE strategy LIKE '%rpc%'
GROUP BY rejection_reasons
ORDER BY count DESC;
```

**Expected:**
- `vaults_not_ready` → Should decrease with Phase 2 (because we only probe at T=5s+)
- `owner_mismatch` → 10-20% (bad candidates extracted)
- `success` → 70%+ of RPC attempts (vaults are ready)

#### Strategy Metrics

```sql
-- Which strategy wins?
SELECT
    winning_strategy,
    COUNT(*) as count,
    ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM discovery_summary), 1) as pct
FROM discovery_summary
GROUP BY winning_strategy
ORDER BY count DESC;
```

**Expected:**
- `tx_parsing` → 75-85% (primary strategy should dominate)
- `rpc_light` → 10-15% (light RPC should catch some)
- `rpc_full` → <5% (full RPC only for rare cases)

#### Retry Number Distribution

```sql
-- How many retries until success?
SELECT
    first_success_attempt,
    COUNT(*) as count,
    ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM discovery_summary), 1) as pct
FROM discovery_summary
GROUP BY first_success_attempt
ORDER BY first_success_attempt;
```

**Expected:**
- Attempt 1: <5% (very rare)
- Attempt 2: <5% (still early)
- Attempt 3-4: 20-30% (TX indexed by now)
- Attempt 5: 15-25% (TX-only window closing)
- Attempt 6-7: 15-25% (light RPC kicks in)
- Attempt 8+: <10% (rare, indicates deeper issue)

### SLO Targets

#### Gold SLO (95th percentile)
- **Latency:** P95 < 15 seconds
- **Success:** 95%+ resolved (vs 100% target)
- **Strategy:** 80%+ tx_parsing wins

#### Silver SLO (50th percentile)
- **Latency:** Median < 10 seconds
- **Success:** 90%+ resolved
- **Strategy:** 70%+ tx_parsing wins

#### Bronze SLO (just working)
- **Latency:** P90 < 30 seconds
- **Success:** 80%+ resolved
- **Strategy:** 50%+ tx_parsing wins

#### Health Thresholds

```python
HEALTH_CHECKS = {
    "median_resolve_seconds": {
        "green": (0, 10),
        "yellow": (10, 15),
        "red": (15, float('inf'))
    },
    "p90_resolve_seconds": {
        "green": (0, 20),
        "yellow": (20, 30),
        "red": (30, float('inf'))
    },
    "tx_parsing_success_rate": {
        "green": (0.75, 1.0),
        "yellow": (0.60, 0.75),
        "red": (0, 0.60)
    },
    "vaults_not_ready_pct": {
        "green": (0, 0.05),  # Should be rare
        "yellow": (0.05, 0.10),
        "red": (0.10, 1.0)
    },
}
```

### Queries to Run Weekly

```sql
-- 1. Overall health snapshot
SELECT
    COUNT(*) as total_resolutions,
    ROUND(AVG(final_success_elapsed_seconds), 1) as median_seconds,
    ROUND(MAX(final_success_elapsed_seconds), 1) as max_seconds,
    ROUND(100.0 * SUM(CASE WHEN final_success_elapsed_seconds < 10 THEN 1 ELSE 0 END) / COUNT(*), 1) as pct_under_10s
FROM discovery_summary
WHERE created_at > datetime('now', '-7 days');

-- 2. Bottleneck analysis (what's slowing us down?)
SELECT
    rejection_reasons,
    COUNT(*) as frequency,
    ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM discovery_attempts), 1) as pct_of_all_attempts
FROM discovery_attempts
WHERE created_at > datetime('now', '-7 days')
GROUP BY rejection_reasons
ORDER BY frequency DESC;

-- 3. Strategy effectiveness
SELECT
    winning_strategy,
    COUNT(*) as wins,
    ROUND(AVG(final_success_elapsed_seconds), 1) as avg_time_to_win,
    ROUND(MIN(final_success_elapsed_seconds), 1) as min_time,
    ROUND(MAX(final_success_elapsed_seconds), 1) as max_time
FROM discovery_summary
WHERE created_at > datetime('now', '-7 days')
GROUP BY winning_strategy
ORDER BY wins DESC;

-- 4. Retry number distribution
SELECT
    first_success_attempt as attempt,
    COUNT(*) as count,
    ROUND(AVG(final_success_elapsed_seconds), 1) as avg_elapsed
FROM discovery_summary
WHERE created_at > datetime('now', '-7 days')
GROUP BY first_success_attempt
ORDER BY attempt;
```

---

## 8. Expected Performance Improvement

### Projected Results After Phase 2

#### Latency Improvements

| Metric | Phase 1 Only | Phase 2 Added | Combined | Target |
|--------|---|---|---|---|
| **Median** | 8-12s | 5-8s reduction | **3-8s** | <10s ✅ |
| **P75** | 15-20s | 5-10s reduction | **10-15s** | <15s ✅ |
| **P90** | 25-30s | 5-15s reduction | **15-25s** | <25s ✅ |
| **P99** | >40s | Varies | **25-40s** | <60s ✅ |

#### Strategy Shift

**Phase 1 behavior:**
- TX parsing mixed with RPC fallback from retry 1
- RPC contention from background jobs immediate
- Many early RPC failures (`vaults_not_ready`)
- Late success windows (retries 6-8)

**Phase 2 behavior:**
- TX parsing retries 1-5 only (6 focused attempts)
- RPC fallback deferred until T=5s (when vaults likely ready)
- Background jobs queued, not executed (RPC clear)
- Early success windows (retries 3-5)

#### Success Rate by Attempt

**Phase 1 distribution:**
```
Attempt 1: 0% (TX not indexed, RPC contention high)
Attempt 2: 0% (TX still indexing, RPC contention high)
Attempt 3: 5% (TX indexed, RPC contention still high)
Attempt 4: 10%
Attempt 5: 10%
Attempt 6: 25% (RPC vaults finally ready, less contention)
Attempt 7: 20%
Attempt 8: 15%
Attempt 9+: 15% (late successes)
```

**Phase 2 expected distribution:**
```
Attempt 1: 0% (TX still indexing, as before)
Attempt 2: 0% (TX indexing in progress)
Attempt 3: 20% (TX indexed, no RPC contention!)
Attempt 4: 25% (TX ready, clear RPC path)
Attempt 5: 20% (Final TX-only attempt)
Attempt 6: 15% (Light RPC kicks in, vaults likely ready)
Attempt 7: 10% (Full RPC available)
Attempt 8+: 10% (Late attempts, rare)
```

**Median shifts from attempt 6 to attempt 3-4 (2-3x fewer retries)**

### Why Phase 2 Reduces Latency

1. **Earlier TX success:** Retries 3-5 succeed instead of 6-8
   - Saves: 5-8 seconds (fewer retries + faster RPC)

2. **Reduced RPC timeouts:** Background jobs don't contend
   - Saves: 1-3 seconds (faster RPC responses)

3. **Smarter RPC fallback:** Only probe at T=5s+ when ready
   - Saves: 1-2 seconds (fewer wasted RPC calls)

4. **No registration errors:** Validation is focused, not competing
   - Saves: 0.5-1 second (fewer re-validations)

**Total expected savings: 7.5-14 seconds per token**
**From Phase 1's 8-12s down to Phase 2's 3-8s (additional 1.5-2.5x improvement)**

### Combined Phase 1 + Phase 2 Result

```
Before any optimization:  82-87s median ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Phase 1 (retry timing):   8-12s median  ━━━━━━  (8-10x faster) ✅
Phase 2 (critical path):  3-8s median   ━━      (additional 1.5-2.5x) ✅
Combined improvement:     10-20x faster ✅
```

**User experience:**
- **Before:** Pool appears in UI in 80-90 seconds (feels broken)
- **After Phase 1:** Pool appears in 8-12 seconds (feels responsive)
- **After Phase 2:** Pool appears in 3-8 seconds (feels real-time)

---

## Summary: Phase 2 Implementation Roadmap

### Changes Required

1. **RPC Isolation** (1-2 hours)
   - Add semaphore-based quota separation
   - 8 slots for discovery, 2 for background
   - Implement fallback timeout handling

2. **Background Job Queue** (1 hour)
   - Create background job queue datastructure
   - Defer all background tasks during critical window (45s)
   - Process queue after critical window expires

3. **Retry Strategy Tiers** (2 hours)
   - Implement 3-tier strategy selection (tx-only, light, full)
   - Guard RPC fallback by elapsed time
   - Ensure TX polling is focused on exact migration TX

4. **Structured Telemetry** (2 hours)
   - Add per-attempt structured logging
   - Implement DiscoveryAttempt dataclass
   - Add database schema for telemetry
   - Insert attempt records

5. **Monitoring Queries** (1 hour)
   - Implement SLO dashboard queries
   - Set up health check thresholds
   - Create weekly analysis reports

**Total: 7-8 hours implementation + testing**

### Expected Outcome

✅ Median discovery latency: 3-8 seconds (vs 8-12s after Phase 1)
✅ TX parsing dominates (75-85% success rate)
✅ RPC contention eliminated during critical window
✅ Background jobs deferred properly
✅ Full visibility into discovery bottlenecks

**This moves pool discovery from "fast enough" to "real-time."**

