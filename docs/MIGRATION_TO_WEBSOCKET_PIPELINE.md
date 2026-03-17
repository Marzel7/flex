# Migration Detection to WebSocket Price Pipeline

## Overview

This document describes the complete end-to-end pipeline for detecting Pumpfun token migrations, discovering on-chain pool vault accounts, subscribing to real-time updates via WebSocket, and displaying live prices in the UI.

**Flow Summary:**
```
Token Migration (Pumpfun)
  → Detect via WebSocket listener
  → RPC-based vault discovery
  → Store pool vault accounts
  → WebSocket subscription
  → Real-time price computation
  → Database storage
  → UI display with price source indicator
```

---

## Part 1: Migration Detection

### 1.1 WebSocket Listener Setup

The system monitors Pumpfun program events via WebSocket to detect when tokens transition from bonding curve (Pumpfun) to open trading (migrated).

**File:** `src/core/pumpfun_curve_listener.py`

**Key Components:**

```python
class PumpfunCurveListener:
    def __init__(self):
        # WebSocket connection to Solana cluster
        self.ws_url = os.getenv('HELIUS_WS_URL')
        self.ws_manager = WebSocketManager(self.ws_url)

        # Track token states: pending → resolving → resolved
        self.token_states = {}  # token_mint → state
        self.token_discovery_times = {}  # token_mint → {detected, resolving, resolved}

        # Price worker for WebSocket subscriptions
        self.price_worker = get_price_worker(self.db_path)

    async def start(self):
        """Main listener loop"""
        while self.running:
            # Subscribe to Pumpfun program account changes
            await self.ws_manager.subscribe(
                program_id=PUMPFUN_PROGRAM_ID,
                commitment='confirmed'
            )
```

### 1.2 Migration Event Detection

When a Pumpfun token's bonding curve closes and migrates to AMM (Automated Market Maker), it generates a characteristic transaction pattern.

```python
async def on_account_update(self, update):
    """Handle WebSocket account update from Pumpfun program"""

    # Decode the account data
    mint, curve_state = decode_pumpfun_curve(update.data)

    if not mint:
        return

    # Check if this is a MIGRATION event
    # (bonding curve supply exhausted or explicitly closed)
    if is_migration_event(curve_state):
        log_print(
            f"[MIGRATION] 🚀 Token detected: {mint[:8]}..."
        )

        # Record initial detection
        self.token_states[mint] = "pending"
        self.token_discovery_times[mint] = {
            "detected": time.time()
        }
```

**Migration Indicators:**
- Bonding curve supply reaches max (1 billion tokens)
- Bonding curve account marked as closed/migrated
- Transaction includes AMM initialization (Raydium/Pumpswap)
- Creator initiates migration

---

## Part 2: RPC-Based Vault Discovery

After migration detection, the system uses RPC calls to discover the token's liquidity pool vault accounts on-chain.

### 2.1 Vault Discovery Process

**File:** `src/core/vault_discovery.py`

```python
async def discover_and_register_vaults_rpc(
    token_mint: str,
    rpc_client,
    db,
    price_worker=None,
    max_retries: int = 3
) -> bool:
    """
    Main RPC vault discovery entry point.

    Steps:
    1. Find largest token account (base vault)
    2. Resolve quote vault via account owner chain
    3. Validate both vaults
    4. Register in database
    5. Trigger WebSocket subscription
    """

    # Step 1: Discover vaults
    vault_pair = await discover_vaults_rpc(
        token_mint=token_mint,
        rpc_client=rpc_client,
        max_retries=max_retries
    )

    if not vault_pair:
        return False

    # Step 2: Register and trigger WebSocket
    success = await register_vault_pair(
        token_mint=token_mint,
        vault_pair=vault_pair,
        db=db,
        price_worker=price_worker
    )

    return success
```

### 2.2 Finding the Base Vault (Token Account)

The base vault is the token account holding the largest amount of the token's supply.

```python
async def get_token_largest_accounts(
    token_mint: str,
    rpc_client,
    limit: int = 20
) -> List[Dict]:
    """Query RPC for token's largest accounts"""

    # Use getTokenLargestAccounts RPC method
    response = await rpc_client.call_async(
        'getTokenLargestAccounts',
        [token_mint]
    )

    accounts = response.get('value', [])

    # Filter and validate accounts
    validated = await validate_token_accounts(
        accounts=accounts,
        token_mint=token_mint,
        rpc_client=rpc_client
    )

    return validated
```

**Response Format:**
```python
{
    'address': 'DAyFNxYpjPBJoZG6Vriue2X8GHPejKCvw1pADqCC7BfG',  # Base vault
    'mint': '6WUh7irJ8PxXYyys3E2f2sunLfQFjBXZ4PaU8uzjpump',
    'owner': 'TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb',   # SPL token owner
    'amount': 123456789,  # Tokens in account
    'decimals': 6
}
```

### 2.3 Finding the Quote Vault (SOL Account)

The quote vault is the token account holding wrapped SOL (wSOL) or native SOL, paired with the token in a liquidity pool.

**Method 1: Owner Chaining (Raydium/Orca)**

```python
async def resolve_quote_vault_from_base(
    base_vault: ValidatedTokenAccount,
    token_mint: str,
    rpc_client
) -> Optional[str]:
    """
    Follow the owner chain to find paired quote vault.

    For Raydium/Orca pools:
    - Base vault owner → Pool state account
    - Decode pool state to find quote vault address
    """

    owner_pubkey = base_vault.decoded.owner

    # Fetch pool state account
    pool_account = await rpc_client.get_account_info(
        owner_pubkey,
        encoding='base64'
    )

    if not pool_account:
        return None

    program_id = pool_account.owner

    # Decode based on program (Raydium, Orca, PumpSwap, etc.)
    if program_id == RAYDIUM_PROGRAM_ID:
        quote_vault = await _decode_raydium_pool(
            pool_account.data,
            rpc_client
        )
    elif program_id == ORCA_PROGRAM_ID:
        quote_vault = await _decode_orca_pool(
            pool_account.data,
            rpc_client
        )

    return quote_vault
```

**Method 2: Fallback - Query wSOL Accounts (PumpSwap)**

When owner chaining fails (PumpSwap doesn't expose readable pool state), query for wSOL token accounts.

```python
async def resolve_quote_vault_fallback(
    base_vault_address: str,
    token_mint: str,
    rpc_client
) -> Optional[str]:
    """
    Fallback: Find quote vault by querying for wSOL accounts
    owned by the pool authority.
    """

    # Get base vault to extract owner
    base_acct = await rpc_client.get_account_info(
        base_vault_address,
        encoding='base64'
    )

    # Decode to extract owner field (pool authority)
    data_bytes = base64.b64decode(base_acct.data)
    pool_owner = base58.b58encode(data_bytes[32:64]).decode()

    # Query for wSOL token accounts owned by pool
    result = await rpc_client.call_async(
        'getTokenAccountsByOwner',
        [
            pool_owner,
            {'mint': WRAPPED_SOL_MINT},
            {'encoding': 'base64'}
        ]
    )

    if result and result['value']:
        # First wSOL account is the quote vault
        quote_vault = result['value'][0]['pubkey']
        return quote_vault

    return None
```

**Example Discovery Result:**
```python
vault_pair = VaultPair(
    base_vault=ValidatedTokenAccount(
        address='DAyFNxYpjPBJoZG6Vriue2X8GHPejKCvw1pADqCC7BfG',
        decoded=DecodedTokenAccount(
            mint='6WUh7irJ8PxXYyys3E2f2sunLfQFjBXZ4PaU8uzjpump',
            amount=123456789,
            owner='TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb'
        )
    ),
    quote_vault={
        'address': 'BKSLs6kAtrkxdiSnD4WRwoeViUSfQZRTMSM94HdSEVDD',
        'decoded': DecodedTokenAccount(
            mint='So11111111111111111111111111111111111111112',  # wSOL
            amount=987654321,
            owner='TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb'
        )
    }
)
```

---

## Part 3: Pool Account Storage

Discovered vault accounts are stored in the database for persistent tracking and WebSocket subscription.

### 3.1 Database Schema

**File:** `src/core/vault_discovery.py` → `register_vault_pair()`

```sql
CREATE TABLE token_pool_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Token identification
    mint TEXT NOT NULL UNIQUE,

    -- Vault accounts (where liquidity is held)
    base_account TEXT NOT NULL,      -- Token vault account
    quote_account TEXT NOT NULL,     -- SOL/quote vault account

    -- Pool metadata
    pool_program TEXT,               -- 'raydium', 'orca', 'pumpswap', etc.
    base_token TEXT,                 -- Token mint (same as mint)
    base_decimals INTEGER,           -- Token decimals (usually 6)
    quote_decimals INTEGER,          -- Quote decimals (9 for wSOL)
    quote_token TEXT,                -- Quote mint (wSOL address)

    -- Validation status
    vault_validation_status TEXT,    -- 'pending', 'validated', 'failed'
    discovery_method TEXT,           -- 'rpc_authoritative', 'tx_based', etc.

    -- Timestamps
    created_at TIMESTAMP,
    updated_at TIMESTAMP,
    last_vault_validation_at TIMESTAMP,

    -- Indexed for quick lookup
    UNIQUE(mint, base_account)
);

CREATE INDEX idx_mint ON token_pool_accounts(mint);
CREATE INDEX idx_validation_status ON token_pool_accounts(vault_validation_status);
```

### 3.2 Vault Registration

When vaults are discovered and validated, they're registered in the database.

```python
async def register_vault_pair(
    token_mint: str,
    vault_pair: VaultPair,
    db,
    price_worker=None
) -> bool:
    """Register discovered vault pair in database"""

    base_vault = vault_pair.base_vault
    quote_vault = vault_pair.quote_vault

    # Validation checks
    if not base_vault or not quote_vault:
        logger.error("Missing vault")
        return False

    if base_vault.address == quote_vault['address']:
        logger.error("Base and quote are same address")
        return False

    # Insert into database
    cursor.execute("""
        INSERT INTO token_pool_accounts
        (mint, base_account, quote_account, pool_program,
         base_token, base_decimals, quote_decimals, quote_token,
         vault_validation_status, discovery_method, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(mint, base_account) DO NOTHING
    """, (
        token_mint,
        base_vault.address,
        quote_vault['address'],
        vault_pair.pool_program or 'unknown',
        token_mint,  # base_token is the token mint itself
        6,  # base_decimals
        9 if quote_vault.get('decoded') and quote_vault['decoded'].mint == WRAPPED_SOL_MINT else 6,
        quote_vault.get('decoded', {}).mint or WRAPPED_SOL_MINT,
        'validated',  # Mark RPC-discovered vaults as validated
        'rpc_authoritative',
        int(time.time()),
        int(time.time())
    ))
    conn.commit()

    logger.info(f"✅ Registered vault pair:")
    logger.info(f"   Token: {token_mint}")
    logger.info(f"   Base:  {base_vault.address}")
    logger.info(f"   Quote: {quote_vault['address']}")

    # Trigger WebSocket refresh to subscribe to new pool
    if price_worker:
        try:
            price_worker.trigger_pool_refresh()
            logger.info("✅ WebSocket client refreshing with new vaults")
        except Exception as e:
            logger.warning(f"WebSocket refresh failed: {e}")

    return True
```

**Database Record Example:**
```
mint: 6WUh7irJ8PxXYyys3E2f2sunLfQFjBXZ4PaU8uzjpump
base_account: DAyFNxYpjPBJoZG6Vriue2X8GHPejKCvw1pADqCC7BfG
quote_account: BKSLs6kAtrkxdiSnD4WRwoeViUSfQZRTMSM94HdSEVDD
pool_program: unknown
base_decimals: 6
quote_decimals: 9
vault_validation_status: validated
discovery_method: rpc_authoritative
```

---

## Part 4: WebSocket Subscription & Real-Time Updates

Once vaults are registered, the system subscribes to them via WebSocket to receive real-time account updates.

### 4.1 WebSocket Client Architecture

**File:** `src/core/pool_price_engine.py`

```python
class PoolWebSocketClient:
    """Subscribe to pool vault accounts and process updates"""

    def __init__(self, pool_state: PoolStateStore, db_path: str):
        self.pool_state = pool_state  # In-memory state store
        self.db_path = db_path
        self.ws_url = os.getenv('HELIUS_WS_URL')  # e.g., wss://helius-ws.com?api-key=...

        self._subscriptions = {}  # pubkey → subscription_id
        self._ws = None
        self._thread = None
        self.running = False

    def start(self, pools: List[Dict]):
        """Start WebSocket and subscribe to all pool accounts"""

        if self.running:
            return

        # Build account map: all base + quote accounts
        self._account_map = {}  # pubkey → pool
        for pool in pools:
            mint = pool['mint']
            base_account = pool['base_account']
            quote_account = pool['quote_account']

            # Map both vault accounts to the pool
            self._account_map[base_account] = {
                'mint': mint,
                'base_account': base_account,
                'account_type': 'base'
            }
            self._account_map[quote_account] = {
                'mint': mint,
                'base_account': base_account,
                'account_type': 'quote'
            }

        logger.info(f"🗺️  Built account map: {len(self._account_map)} accounts → pools")

        # Start subscription thread
        self._thread = threading.Thread(target=self._run_ws_loop, daemon=True)
        self.running = True
        self._thread.start()

        logger.info(f"🚀 Starting WebSocket client to subscribe to {len(self._account_map)} pool accounts")
```

### 4.2 Subscription & Event Processing

```python
def _run_ws_loop(self):
    """Main WebSocket loop"""

    try:
        import websockets
        import asyncio

        loop = asyncio.new_event_loop()
        loop.run_until_complete(self._subscribe_and_listen())
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        self.running = False

async def _subscribe_and_listen(self):
    """Subscribe to accounts and listen for updates"""

    async with websockets.connect(self.ws_url) as ws:
        self._ws = ws

        # Subscribe to each pool account
        for account_address in self._account_map.keys():
            # Send subscribe request
            await self._send_subscribe(ws, account_address)

        logger.info(f"✅ Subscribed to {len(self._account_map)} pool accounts")

        # Listen for incoming updates
        async for message in ws:
            await self._handle_message(message)

async def _send_subscribe(self, ws, account_address: str):
    """Subscribe to a specific account"""

    # Helius subscription format
    subscription = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "accountSubscribe",
        "params": [
            account_address,
            {
                "encoding": "base64",
                "commitment": "confirmed"
            }
        ]
    }

    await ws.send(json.dumps(subscription))
```

### 4.3 Message Processing

When an account (token vault) receives an update (balance change due to trades), the system decodes it and extracts reserve information.

```python
async def _handle_message(self, message: str):
    """Process WebSocket account update message"""

    try:
        data = json.loads(message)

        if 'params' not in data:
            return

        params = data['params']

        if 'result' not in params:
            return

        result = params['result']

        if 'value' not in result:
            return

        # Extract account info
        account = result['value']['data']  # base64-encoded
        account_address = result['value']['pubkey']
        slot = result['context']['slot']

        # Look up which pool this account belongs to
        pool_info = self._account_map.get(account_address)
        if not pool_info:
            return

        mint = pool_info['mint']
        base_account = pool_info['base_account']
        account_type = pool_info['account_type']  # 'base' or 'quote'

        # Decode the balance from account data
        decoded_balance = self._decode_spl_token_balance(
            account,
            token_program=SPL_TOKEN_PROGRAM_ID
        )

        if decoded_balance is None:
            return

        # Update in-memory pool state
        updated = self.pool_state.update_reserve(
            mint=mint,
            base_account=base_account,
            account_type=account_type,
            raw_balance=decoded_balance,
            slot=slot
        )

        if updated:
            logger.debug(f"[POOL_STATE] Updated {account_type} reserve for {mint[:8]}...")

    except Exception as e:
        logger.debug(f"Message processing error: {e}")

def _decode_spl_token_balance(self, data: bytes, token_program) -> Optional[int]:
    """
    Decode SPL token account to extract balance.

    SPL token account structure:
    - 0-32: mint (pubkey)
    - 32-64: owner (pubkey)
    - 64-72: amount (u64, little-endian)
    - ...
    """

    # Decode from base64 if needed
    if isinstance(data, str):
        data = base64.b64decode(data)

    # Must be at least 72 bytes (mint + owner + amount)
    if len(data) < 72:
        return None

    # Extract amount at offset 64
    amount_bytes = data[64:72]
    amount = int.from_bytes(amount_bytes, byteorder='little')

    return amount
```

### 4.4 Pool State Store

The in-memory state store tracks the most recent reserves for each pool, deduplicating duplicate slot updates.

```python
class PoolStateStore:
    """
    In-memory store for pool reserve states.
    Keyed by (mint, base_account) to support multiple pools per token.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._state: Dict[Tuple[str, str], Dict] = {}
        self._was_ready = set()  # Track pools that have already logged as READY

    def update_reserve(
        self,
        mint: str,
        base_account: str,
        account_type: str,  # 'base' or 'quote'
        raw_balance: int,
        slot: Optional[int] = None
    ) -> bool:
        """
        Update a reserve balance. Returns True if updated, False if duplicate.
        Uses per-account deduplication (different slots for base vs quote).
        """

        pool_id = (mint, base_account)

        with self._lock:
            if pool_id not in self._state:
                self._state[pool_id] = {
                    'base_reserve': None,
                    'base_last_slot': None,
                    'quote_reserve': None,
                    'quote_last_slot': None,
                    'last_update': 0,
                    'is_stale': False
                }

            entry = self._state[pool_id]

            # Check for duplicate by account type
            if account_type == 'base':
                if slot and entry['base_last_slot'] == slot:
                    return False  # Duplicate
                entry['base_reserve'] = raw_balance
                entry['base_last_slot'] = slot
            else:  # quote
                if slot and entry['quote_last_slot'] == slot:
                    return False  # Duplicate
                entry['quote_reserve'] = raw_balance
                entry['quote_last_slot'] = slot

            entry['last_update'] = time.time()
            entry['is_stale'] = False

            # Log pool as READY only once (when both reserves available)
            if (entry['base_reserve'] is not None and
                entry['quote_reserve'] is not None and
                pool_id not in self._was_ready):
                logger.info(f"[POOL_STATE] ✅ READY: {mint[:8]}... both reserves!")
                self._was_ready.add(pool_id)

            return True

    def get_reserves(self, mint: str, base_account: str) -> Optional[Tuple[int, int]]:
        """Get current reserves for a pool"""

        pool_id = (mint, base_account)
        with self._lock:
            s = self._state.get(pool_id)
            if s and s['base_reserve'] is not None and s['quote_reserve'] is not None and not s['is_stale']:
                return (s['base_reserve'], s['quote_reserve'])
        return None

    def get_pools_for_mint(self, mint: str) -> List[Tuple[str, int, int]]:
        """Get all pools and their reserves for a token"""

        results = []
        with self._lock:
            for (m, base_account), s in self._state.items():
                if m == mint and not s['is_stale']:
                    if s['base_reserve'] is not None and s['quote_reserve'] is not None:
                        results.append((base_account, s['base_reserve'], s['quote_reserve']))
        return results
```

---

## Part 5: Price Computation

With reserve data flowing in, prices are computed and stored in the database.

### 5.1 Price Calculation

**File:** `src/core/pool_price_engine.py`

```python
class PoolPriceCalculator:
    """Calculate token price from pool reserves"""

    @staticmethod
    async def compute_price_from_reserves(
        mint: str,
        base_reserve: int,
        quote_reserve: int,
        base_decimals: int,
        quote_decimals: int,
        quote_token: str
    ) -> Optional['TokenPrice']:
        """
        Compute price: quote_reserve / base_reserve
        Adjusting for decimal differences.
        """

        if base_reserve <= 0 or quote_reserve <= 0:
            return None

        # Normalize decimals
        # price_usd = (quote_amount * quote_decimals) / (base_amount * base_decimals)
        decimal_adjustment = 10 ** (quote_decimals - base_decimals)

        price_usd = (quote_reserve / base_reserve) * decimal_adjustment

        # Fetch SOL price if quote is wSOL
        sol_price_usd = 1.0
        if quote_token == WRAPPED_SOL_MINT:
            sol_price_usd = await PoolPriceCalculator.fetch_sol_price_usd()
            price_usd *= sol_price_usd

        # Calculate market cap
        # market_cap = price * supply
        # (supply is base_reserve)
        market_cap = price_usd * base_reserve / (10 ** base_decimals)

        return TokenPrice(
            mint=mint,
            price_usd=price_usd,
            price_sol=price_usd / sol_price_usd if sol_price_usd > 0 else 0,
            liquidity_usd=quote_reserve / (10 ** quote_decimals) * sol_price_usd,
            market_cap=market_cap,
            source='pool',
            is_stale=False
        )

    @staticmethod
    async def fetch_sol_price_usd() -> float:
        """Fetch SOL/USD price from Jupiter API"""

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    'https://api.jup.ag/price?ids=So11111111111111111111111111111111111111112',
                    timeout=5
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        price = data['data']['So11111111111111111111111111111111111111112']['price']
                        return float(price)
        except Exception as e:
            logger.debug(f"SOL price fetch failed: {e}")

        # Fallback price
        return 94.0
```

### 5.2 Price Storage

Computed prices are stored in the database for historical tracking and UI display.

**File:** `src/core/price_worker.py`

```python
def _store_snapshot(self, price: TokenPrice):
    """Store computed price in database"""

    try:
        conn = sqlite3.connect(self.db_path, timeout=10)
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO token_price_snapshots
            (mint, price_usd, price_sol, liquidity_usd, market_cap,
             volume_24h, source, pair_address, captured_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            price.mint,
            price.price_usd,
            price.price_sol,
            price.liquidity_usd,
            price.market_cap,
            price.volume_24h or 0,
            price.source,  # 'pool', 'dexscreener', etc.
            price.pair_address,
            int(time.time()),
            int(time.time())
        ))

        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Failed to store price snapshot: {e}")
```

**Database Schema:**
```sql
CREATE TABLE token_price_snapshots (
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    mint TEXT NOT NULL,
    price_usd REAL NOT NULL,
    price_sol REAL,
    liquidity_usd REAL,
    market_cap REAL,
    volume_24h REAL,
    source TEXT,           -- 'pool', 'dexscreener', 'jupiter', etc.
    pair_address TEXT,
    captured_at INTEGER,
    created_at INTEGER,

    INDEX idx_mint ON token_price_snapshots(mint),
    INDEX idx_source ON token_price_snapshots(source),
    INDEX idx_created_at ON token_price_snapshots(created_at)
);
```

---

## Part 6: Price Worker Loop

The price worker orchestrates everything: checks for new pools, manages WebSocket, computes prices, and stores them.

### 6.1 Worker Initialization

**File:** `src/core/price_worker.py`

```python
class PriceWorker:
    """
    Background worker that:
    1. Monitors for newly registered pools
    2. Starts/refreshes WebSocket subscriptions
    3. Computes prices from WebSocket reserves
    4. Stores prices in database
    """

    def __init__(self, db_path: str = 'database/flex_complete_database.db'):
        self.db_path = db_path
        self.running = True
        self.interval = 5  # seconds between cycles

        # WebSocket state
        self._ws_client: Optional[PoolWebSocketClient] = None
        self._ws_started = False
        self._pool_state = PoolStateStore()

        # Stats
        self.stats = {
            'cycles': 0,
            'prices_computed': 0,
            'prices_stored': 0,
            'ws_stats': {}
        }

        # Start the worker loop in background thread
        self.thread = threading.Thread(
            target=self._run_loop,
            daemon=True
        )
        self.thread.start()

        # Try to start WebSocket immediately
        self._start_ws_client()
```

### 6.2 Automatic WebSocket Startup

When the price_worker starts, if no pools exist yet, WebSocket doesn't start. But when pools are registered later, the worker automatically starts it.

```python
def _refresh_cycle(self) -> None:
    """
    Main worker cycle - runs every 5 seconds
    """

    cycle_start = time.time()
    self.stats['cycles'] += 1

    # AUTO-START WebSocket if not running but pools exist
    if not self._ws_started:
        fetcher = get_pool_fetcher(self.db_path)
        pools = fetcher.get_active_pools()
        if pools:
            logger.info(f"🔄 Pools detected ({len(pools)}) - starting WebSocket...")
            self._start_ws_client()

    # Refresh subscriptions periodically
    if self.stats['cycles'] % 30 == 1 and self._ws_client:
        try:
            fetcher = get_pool_fetcher(self.db_path)
            pools = fetcher.get_active_pools()
            if pools:
                self._ws_client.refresh_pools(pools)
        except Exception as e:
            logger.debug(f"Error refreshing WebSocket pools: {e}")

    # Compute prices from WebSocket state
    self._recompute_prices_from_ws_state()

    cycle_end = time.time()
    cycle_time = cycle_end - cycle_start

    if cycle_time < self.interval:
        time.sleep(self.interval - cycle_time)
```

### 6.3 Price Computation from WebSocket Data

```python
def _recompute_prices_from_ws_state(self):
    """
    Compute prices from current WebSocket pool state.
    Gets all mints with active reserves, computes price per pool,
    aggregates if multiple pools per token.
    """

    try:
        fetcher = get_pool_fetcher(self.db_path)
        all_mints = self._pool_state.get_all_mints()

        for mint in all_mints:
            # Get all pools for this token
            pools = self._pool_state.get_pools_for_mint(mint)

            if not pools:
                continue

            prices = []

            for base_account, base_reserve, quote_reserve in pools:
                # Get pool metadata
                pool_meta = fetcher.get_pool_metadata(mint, base_account)

                if not pool_meta:
                    continue

                # Compute price for this pool
                price = await PoolPriceCalculator.compute_price_from_reserves(
                    mint=mint,
                    base_reserve=base_reserve,
                    quote_reserve=quote_reserve,
                    base_decimals=pool_meta['base_decimals'],
                    quote_decimals=pool_meta['quote_decimals'],
                    quote_token=pool_meta['quote_token']
                )

                if price:
                    prices.append(price)

            # Aggregate multiple prices
            if prices:
                final_price = PoolAggregator.aggregate(prices)
                self._store_snapshot(final_price)
                self.stats['prices_stored'] += 1

    except Exception as e:
        logger.error(f"Price computation error: {e}")
```

---

## Part 7: UI Display

Prices are displayed in the UI with source indicators showing whether they're from WebSocket or other sources.

### 7.1 API Endpoint - Get Token Metrics

**File:** `src/core/main.py`

```python
@app.route('/api/token-metrics/<token_mint>')
def api_token_metrics(token_mint: str):
    """Get detailed metrics including price and source"""

    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Get token analysis
        cursor.execute("""
            SELECT
                mint, price_current, price_highest,
                market_cap_current, market_cap_highest,
                risk_level, rug_probability
            FROM token_analysis
            WHERE mint = ?
        """, (token_mint,))

        row = cursor.fetchone()
        if not row:
            conn.close()
            return jsonify({'error': 'Token not found'}), 404

        # Get most recent price source
        cursor.execute("""
            SELECT source
            FROM token_price_snapshots
            WHERE mint = ?
            ORDER BY created_at DESC
            LIMIT 1
        """, (token_mint,))

        price_source_row = cursor.fetchone()
        price_source = price_source_row['source'] if price_source_row else 'unknown'
        conn.close()

        # Build response
        response = {
            'mint': row['mint'],
            'price': {
                'current': row['price_current'] or 0,
                'highest': row['price_highest'] or 0,
                'source': price_source  # ← Price source indicator
            },
            'market_cap': {
                'current': row['market_cap_current'] or 0,
                'highest': row['market_cap_highest'] or 0
            },
            'risk': {
                'rug_probability': row['rug_probability'] or 0,
                'risk_level': row['risk_level']
            }
        }

        return jsonify(response)

    except Exception as e:
        return jsonify({'error': str(e)}), 500
```

### 7.2 Main Page Display

**File:** `src/core/main.py` (HTML/JavaScript)

```html
<!-- Price column with source indicator -->
<td id="price-${token.mint}" style="min-width: 120px;">
    <div>
        ${token.price_current && token.price_current > 0
            ? `$${token.price_current.toFixed(8)}`
            : '<span style="opacity: 0.5;">...</span>'}
    </div>
    <div id="source-${token.mint}" style="font-size: 10px; opacity: 0.7; margin-top: 2px;">
        <span style="opacity: 0.5;">-</span>
    </div>
</td>
```

```javascript
// Fetch and display price sources
displayTokens.forEach(token => {
    fetch(`/api/token-metrics/${token.mint}`)
        .then(r => r.json())
        .then(data => {
            if (data.price && data.price.source) {
                const source = data.price.source;
                let sourceIcon = '';
                let sourceLabel = source;

                if (source === 'pool') {
                    sourceIcon = '📡';
                    sourceLabel = 'WebSocket';
                } else if (source === 'dexscreener') {
                    sourceIcon = '🔗';
                    sourceLabel = 'DexScreener';
                } else {
                    sourceIcon = '⚪';
                }

                const sourceEl = document.getElementById(`source-${token.mint}`);
                if (sourceEl) {
                    sourceEl.innerHTML = `<span style="opacity: 0.7;">${sourceIcon} ${sourceLabel}</span>`;
                }
            }
        })
        .catch(e => console.debug('Price source fetch error:', e));
});
```

### 7.3 Modal Display

**File:** `src/core/main.py` (in `showTokenMetrics()`)

```javascript
async function showTokenMetrics(mint) {
    try {
        const response = await fetch(`/api/token-metrics/${mint}`);
        const data = await response.json();

        // ... build metrics grid ...

        // Add price source indicator
        if (data.price && data.price.source) {
            const source = data.price.source;
            let sourceIcon = '', sourceLabel = source;

            if (source === 'pool') {
                sourceIcon = '📡';
                sourceLabel = 'WebSocket (Real-time)';
            } else if (source.includes('pool')) {
                sourceIcon = '📊';
                sourceLabel = source.charAt(0).toUpperCase() + source.slice(1);
            } else {
                sourceIcon = '⚪';
            }

            metricsHTML += `
                <div class="metric">
                    <label>Price Source</label>
                    <span>${sourceIcon} ${sourceLabel}</span>
                </div>
            `;
        }

        metricsGrid.innerHTML = metricsHTML;
        modal.style.display = 'block';
    } catch (error) {
        console.error('Error loading metrics:', error);
        alert('Failed to load token metrics');
    }
}
```

---

## Complete Flow Example

### Scenario: Token `6WUh7irJ8PxXYyys3E2f2sunLfQFjBXZ4PaU8uzjpump` Launches

**Timeline:**

```
[T=0s] Migration detected
-------
- WebSocket listener receives account update from Pumpfun program
- Bonding curve state shows migration
- Token state: pending
- Token: 6WUh7irJ8PxXYyys3E2f2sunLfQFjBXZ4PaU8uzjpump

[T=3s] RPC Vault Discovery (retry #1)
-------
- Call getTokenLargestAccounts for token
- Find: DAyFNxYpjPBJoZG6Vriue2X8GHPejKCvw1pADqCC7BfG (123M tokens)
- Follow owner chain to find quote vault
- Resolve: BKSLs6kAtrkxdiSnD4WRwoeViUSfQZRTMSM94HdSEVDD (wSOL)
- Token state: resolving

[T=3.5s] Register Pools
-------
- INSERT into token_pool_accounts:
  - mint: 6WUh7irJ8PxXYyys3E2f2sunLfQFjBXZ4PaU8uzjpump
  - base_account: DAyFNxYpjPBJoZG6Vriue2X8GHPejKCvw1pADqCC7BfG
  - quote_account: BKSLs6kAtrkxdiSnD4WRwoeViUSfQZRTMSM94HdSEVDD
  - vault_validation_status: validated
  - discovery_method: rpc_authoritative

[T=3.6s] WebSocket Startup
-------
- price_worker.trigger_pool_refresh() called
- _ws_client = PoolWebSocketClient(...)
- Build account map: 100+ accounts
- Subscribe via WebSocket to both vaults

[T=3.7s] First WebSocket Event
-------
- Account update received: DAyFNxYpjPBJoZG6Vriue2X8GHPejKCvw1pADqCC7BfG
- Decode balance: 123,456,789 (base reserve)
- Update pool state: base_reserve = 123,456,789

[T=3.8s] Second WebSocket Event
-------
- Account update received: BKSLs6kAtrkxdiSnD4WRwoeViUSfQZRTMSM94HdSEVDD
- Decode balance: 987,654 (quote reserve in lamports ≈ 0.987 SOL)
- Update pool state: quote_reserve = 987,654
- BOTH RESERVES READY!

[T=4.0s] Price Computation (worker cycle)
-------
- Reserves available: base=123,456,789, quote=987,654
- SOL price: $94
- Calculation:
  - price_usd = (987,654 / 123,456,789) * 10^3 * 94 = $0.0000584
  - market_cap = 0.0000584 * 123,456,789 = $7,214
- Store snapshot:
  - INSERT token_price_snapshots (mint, price_usd, market_cap, source='pool')

[T=5-10s] UI Update
-------
- User loads dashboard
- loadTokens() fetches token list
- fetchTokenMetrics() called for each token
- API returns: source: 'pool'
- Display: 📡 WebSocket (Real-time)
- Price updates continue in real-time
```

---

## Key Design Decisions

### 1. **Per-Account Deduplication**
- Tracks separate slots for base and quote accounts
- Allows both to update on the same block without duplicate rejection
- Prevents missing price updates

### 2. **RPC-Authoritative Discovery**
- Uses on-chain RPC to find definitive vault accounts
- More reliable than transaction parsing
- Validated status ensures quality

### 3. **In-Memory Pool State**
- Fast price computation without database queries
- WebSocket events processed in <1ms
- Real-time responsiveness

### 4. **Automatic WebSocket Startup**
- Worker checks each cycle if new pools exist
- Starts WebSocket automatically when first pool registered
- No manual intervention needed on restart

### 5. **Source Indicator in UI**
- Shows which pools have real-time (WebSocket) vs cached (DexScreener) prices
- Users can distinguish live vs stale data
- 📡 WebSocket = real-time, ⚪ Unknown = no recent data

---

## Troubleshooting

### Prices showing "unknown"
- Pool registered but WebSocket not subscribed
- Check: `ps aux | grep price_worker`
- If stopped, restart main.py

### No new price snapshots for 5+ minutes
- WebSocket connection may be stale
- Restart main.py to reset connection
- Check WebSocket URL and API key in .env

### High WebSocket latency
- Check Helius RPC health
- Verify account subscription count < 1000
- Consider load balancing across multiple WebSocket connections

---

## Summary

The complete pipeline:
1. **Detect** migrations via WebSocket listener
2. **Discover** vault accounts via RPC
3. **Store** vault addresses in database
4. **Subscribe** to vaults via WebSocket
5. **Compute** prices from live reserves
6. **Store** prices in database
7. **Display** prices with source indicators in UI

All components work together to provide **real-time token prices immediately after migration**, with automatic recovery on restarts and clear UI indicators of data freshness.
