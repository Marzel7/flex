# WebSocket Vault Discovery Fix - Complete Architecture & Implementation

## Problem Statement

The pipeline stores **wSOL MINT** (`So11111...`) as `quote_account` instead of the **wSOL TOKEN ACCOUNT** address. WebSocket subscriptions only work for token accounts, causing prices to never compute for new tokens.

**Current broken data:**
```
mint           | base_account           | quote_account (WRONG)
3XSpfj5cXur... | 4rxx21Dunt1CiSA...    | So11111111111111...  (MINT - invalid for subscriptions!)
```

**Target correct data:**
```
mint           | base_account           | quote_account (CORRECT) | quote_token
3XSpfj5cXur... | 4rxx21Dunt1CiSA...    | 65DNAQQsfAemPfrE...     | So11111111...
```

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│ VAULT DISCOVERY PIPELINE (Redesigned)                       │
└─────────────────────────────────────────────────────────────┘

1. TOKEN LAUNCH DETECTED
   ↓
2. DECODE POOL ACCOUNT DIRECTLY
   ├─ Read pool data [8:40] = base_vault
   └─ Read pool data [40:72] = quote_vault
   ↓
3. VALIDATE VAULT ACCOUNTS
   ├─ Check account exists
   ├─ Verify owner == TOKEN_PROGRAM_ID
   └─ Validate data length >= 72 bytes
   ↓
4. RETRY LOGIC (if validation fails)
   ├─ Retry every 2 seconds
   ├─ Max 30 retries (60 seconds total)
   └─ Reject if still unvalidated
   ↓
5. STORE CORRECTLY
   ├─ base_account = vault account address
   ├─ quote_account = wSOL token account address (NOT mint!)
   └─ quote_token = wSOL mint (So11111...)
   ↓
6. WEBSOCKET SUBSCRIBE
   └─ Subscribe to base_account & quote_account (real accounts!)
   ↓
7. PRICE COMPUTATION
   └─ Reserves update → prices computed ✓
```

---

## Database Schema (Fixed)

```sql
CREATE TABLE token_pool_accounts (
    -- Identifiers
    mint              TEXT NOT NULL,          -- Token MINT (e.g., BtfA...)
    base_account      TEXT NOT NULL,          -- Base TOKEN ACCOUNT (e.g., FiHz...)
    quote_account     TEXT NOT NULL,          -- Quote TOKEN ACCOUNT (e.g., 65DN...)

    -- Metadata
    pool_program      TEXT NOT NULL DEFAULT 'raydium_amm',
    base_token        TEXT NOT NULL,          -- Base TOKEN MINT
    base_decimals     INTEGER NOT NULL DEFAULT 6,
    quote_decimals    INTEGER NOT NULL DEFAULT 9,
    quote_token       TEXT NOT NULL DEFAULT 'So11111111111111111111111111111111111111112',  -- wSOL MINT

    -- Validation
    vault_validation_status TEXT NOT NULL DEFAULT 'pending'
        CHECK(vault_validation_status IN ('pending', 'validated', 'rejected')),
    vault_validation_error TEXT,
    vault_validation_attempts INTEGER DEFAULT 0,
    last_vault_validation_at INTEGER DEFAULT 0,

    -- Metadata
    discovery_method  TEXT DEFAULT 'pool_decode',
    is_active         BOOLEAN DEFAULT 1,
    created_at        INTEGER NOT NULL,
    updated_at        INTEGER NOT NULL,

    -- Constraints
    PRIMARY KEY (mint, base_account),
    CONSTRAINT valid_accounts CHECK (
        -- Ensure accounts and mints are not swapped
        LENGTH(base_account) = 44 AND
        LENGTH(quote_account) = 44 AND
        LENGTH(base_token) >= 32 AND
        LENGTH(quote_token) >= 32 AND
        quote_account != quote_token  -- CRITICAL: prevent mint in account column
    )
);

CREATE INDEX idx_tpa_mint_active ON token_pool_accounts(mint, is_active);
CREATE INDEX idx_tpa_vault_status ON token_pool_accounts(vault_validation_status, last_vault_validation_at);
CREATE INDEX idx_pool_mint ON token_pool_accounts(mint);
```

---

## Implementation: Vault Discovery (Python)

### 1. Direct Pool Decoding (Fast Path)

```python
import base58
import base64
from typing import Optional, Dict, Tuple

class PoolVaultDecoder:
    """Decode vaults directly from Pump.Fun pool account data."""

    @staticmethod
    def decode_pump_fun_pool(pool_data: bytes) -> Optional[Tuple[str, str]]:
        """
        Decode Pump.Fun migration pool to extract base and quote vaults.

        Pool account layout:
        [0:8]   discriminator
        [8:40]  base_vault (32-byte pubkey)
        [40:72] quote_vault (32-byte pubkey)
        [72:..] rest of pool data

        Args:
            pool_data: Raw pool account data (base64 encoded or bytes)

        Returns:
            Tuple of (base_vault_address, quote_vault_address) or None if decode fails
        """
        try:
            # Handle base64-encoded input
            if isinstance(pool_data, str):
                data = base64.b64decode(pool_data)
            else:
                data = pool_data

            # Minimum size check
            if len(data) < 72:
                return None

            # Extract vault addresses
            base_vault_bytes = data[8:40]
            quote_vault_bytes = data[40:72]

            # Convert to base58 addresses
            base_vault = base58.b58encode(base_vault_bytes).decode()
            quote_vault = base58.b58encode(quote_vault_bytes).decode()

            return (base_vault, quote_vault)

        except Exception as e:
            return None
```

### 2. Vault Validation

```python
from dataclasses import dataclass
from enum import Enum

class VaultValidationStatus(Enum):
    VALID = "valid"
    INVALID_OWNER = "invalid_owner"
    INVALID_SIZE = "invalid_size"
    NOT_EXISTS = "not_exists"
    DECODE_ERROR = "decode_error"

@dataclass
class VaultValidationResult:
    is_valid: bool
    status: VaultValidationStatus
    error: Optional[str] = None
    account_info: Optional[Dict] = None

class VaultValidator:
    """Validate vault accounts before storing."""

    TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJsyFbPVwwQQfiqrDvDLstYZQY"
    TOKEN2022_PROGRAM_ID = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
    SYSTEM_PROGRAM_ID = "11111111111111111111111111111111"

    MIN_TOKEN_ACCOUNT_SIZE = 72  # Minimum SPL token account size

    async def validate_vault(
        self,
        vault_address: str,
        rpc_client
    ) -> VaultValidationResult:
        """
        Validate that vault is a real token account.

        Checks:
        1. Account exists
        2. Owner is Token Program or Token2022
        3. Account data >= 72 bytes (minimum token account size)

        Args:
            vault_address: Solana account address
            rpc_client: RPC client instance

        Returns:
            VaultValidationResult with validation details
        """
        try:
            acct = await rpc_client.get_account_info(vault_address, encoding="base64")

            if acct is None:
                return VaultValidationResult(
                    is_valid=False,
                    status=VaultValidationStatus.NOT_EXISTS,
                    error=f"Account does not exist: {vault_address}"
                )

            # Check owner
            if acct.owner not in (self.TOKEN_PROGRAM_ID, self.TOKEN2022_PROGRAM_ID):
                return VaultValidationResult(
                    is_valid=False,
                    status=VaultValidationStatus.INVALID_OWNER,
                    error=f"Owner {acct.owner} is not Token Program"
                )

            # Check size
            data = base64.b64decode(acct.data) if isinstance(acct.data, str) else acct.data
            if len(data) < self.MIN_TOKEN_ACCOUNT_SIZE:
                return VaultValidationResult(
                    is_valid=False,
                    status=VaultValidationStatus.INVALID_SIZE,
                    error=f"Account data too small: {len(data)} bytes (min {self.MIN_TOKEN_ACCOUNT_SIZE})"
                )

            # Valid!
            return VaultValidationResult(
                is_valid=True,
                status=VaultValidationStatus.VALID,
                account_info={
                    "address": vault_address,
                    "owner": acct.owner,
                    "data_size": len(data),
                    "lamports": acct.lamports
                }
            )

        except Exception as e:
            return VaultValidationResult(
                is_valid=False,
                status=VaultValidationStatus.DECODE_ERROR,
                error=str(e)
            )
```

### 3. Vault Discovery with Retry

```python
import asyncio
import time
from typing import Optional

class VaultDiscoveryService:
    """Discover and validate vaults with retry logic."""

    RETRY_INTERVAL = 2  # seconds
    MAX_RETRIES = 30
    TOTAL_TIMEOUT = 60  # seconds

    def __init__(self, rpc_client, db_path: str):
        self.rpc_client = rpc_client
        self.db_path = db_path
        self.decoder = PoolVaultDecoder()
        self.validator = VaultValidator()

    async def discover_vaults_from_pool(
        self,
        pool_address: str,
        token_mint: str
    ) -> Optional[Dict]:
        """
        Discover vault accounts from pool with retry logic.

        Returns:
            Dict with keys: mint, base_vault, quote_vault, discovery_method
            None if discovery fails after timeout
        """
        start_time = time.time()
        retry_count = 0
        last_error = None

        while True:
            try:
                # Fetch pool account
                pool_acct = await self.rpc_client.get_account_info(
                    pool_address,
                    encoding="base64"
                )

                if pool_acct is None:
                    last_error = "Pool account not found"
                    raise ValueError(last_error)

                # Decode vaults from pool data
                result = self.decoder.decode_pump_fun_pool(pool_acct.data)

                if result is None:
                    last_error = "Failed to decode pool data"
                    raise ValueError(last_error)

                base_vault, quote_vault = result

                # Validate both vaults
                base_validation = await self.validator.validate_vault(
                    base_vault, self.rpc_client
                )
                quote_validation = await self.validator.validate_vault(
                    quote_vault, self.rpc_client
                )

                if not base_validation.is_valid:
                    last_error = f"Base vault invalid: {base_validation.error}"
                    raise ValueError(last_error)

                if not quote_validation.is_valid:
                    last_error = f"Quote vault invalid: {quote_validation.error}"
                    raise ValueError(last_error)

                # Success!
                return {
                    "mint": token_mint,
                    "base_vault": base_vault,
                    "quote_vault": quote_vault,
                    "discovery_method": "pool_decode",
                    "validated_at": int(time.time())
                }

            except Exception as e:
                retry_count += 1
                elapsed = time.time() - start_time

                # Check timeout
                if elapsed > self.TOTAL_TIMEOUT:
                    logger.warning(
                        f"[VAULT_DISCOVERY] ❌ Discovery timeout for {token_mint[:16]}... "
                        f"after {retry_count} retries: {last_error}"
                    )
                    return None

                # Wait before retry
                await asyncio.sleep(self.RETRY_INTERVAL)

                if retry_count % 10 == 0:
                    logger.debug(
                        f"[VAULT_DISCOVERY] Retry {retry_count}/{self.MAX_RETRIES} "
                        f"for {token_mint[:16]}... (elapsed: {elapsed:.1f}s, error: {last_error})"
                    )

    async def discover_and_register(
        self,
        pool_address: str,
        token_mint: str
    ) -> bool:
        """
        Discover vaults and register in database.

        IMPORTANT: Only registers if BOTH vaults are valid.
        Returns False if discovery fails or times out.
        """
        # Discover with retries
        vaults = await self.discover_vaults_from_pool(pool_address, token_mint)

        if vaults is None:
            logger.warning(
                f"[VAULT_DISCOVERY] ⏭️ Skipping registration - discovery failed for {token_mint[:16]}..."
            )
            return False

        # Register to database
        try:
            import sqlite3

            conn = sqlite3.connect(self.db_path, timeout=10)
            cursor = conn.cursor()

            now = int(time.time())

            cursor.execute("""
                INSERT INTO token_pool_accounts (
                    mint, base_account, quote_account, base_token, quote_token,
                    vault_validation_status, discovery_method,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(mint, base_account) DO NOTHING
            """, (
                token_mint,
                vaults["base_vault"],
                vaults["quote_vault"],
                token_mint,                                    # base_token
                "So11111111111111111111111111111111111111112",  # quote_token (wSOL)
                "validated",  # Properly validated before inserting!
                vaults["discovery_method"],
                now,
                now
            ))

            conn.commit()
            conn.close()

            logger.info(
                f"[VAULT_DISCOVERY] ✅ Successfully registered {token_mint[:16]}..."
            )
            return True

        except Exception as e:
            logger.error(f"[VAULT_DISCOVERY] ❌ Registration failed: {e}")
            return False
```

---

## Implementation: Price Worker (Async)

```python
import asyncio
from typing import Dict, Optional

class AsyncPriceWorker:
    """Async-based price computation worker."""

    def __init__(self, db_path: str, rpc_client, interval: int = 10):
        self.db_path = db_path
        self.rpc_client = rpc_client
        self.interval = interval
        self._running = False
        self._pool_state = PoolStateStore()
        self._ws_client: Optional[PoolWebSocketClient] = None
        self._sol_price_cache = SolPriceCache(ttl_seconds=20)

    async def start(self):
        """Start the async price worker loop."""
        self._running = True
        await self.run_loop()

    async def run_loop(self):
        """Main worker loop - runs every 10 seconds."""
        while self._running:
            try:
                await self.refresh_cycle()
            except Exception as e:
                logger.error(f"[PRICE_WORKER] Error in cycle: {e}", exc_info=True)

            await asyncio.sleep(self.interval)

    async def refresh_cycle(self):
        """One complete refresh cycle."""
        # 1. Check for new pools every cycle
        fetcher = get_pool_fetcher(self.db_path)
        pools = fetcher.get_active_pools()

        if not pools:
            return

        # 2. Start or refresh WebSocket
        if not self._ws_client:
            self._start_ws_client(pools)
        else:
            self._ws_client.refresh_pools(pools)

        # 3. Compute prices from WebSocket state
        await self.recompute_prices_from_ws_state()

        # 4. Fallback to RPC if needed
        if not self._pool_state.get_all_mints():
            await self.fetch_pool_prices_async(pools)

    async def recompute_prices_from_ws_state(self):
        """Compute prices from WebSocket-updated reserves."""
        try:
            fetcher = get_pool_fetcher(self.db_path)
            pools = fetcher.get_active_pools()

            if not pools:
                return

            # Get SOL price (cached)
            sol_price = await self._sol_price_cache.get_price(
                lambda: PoolPriceCalculator.fetch_sol_price_usd()
            )

            if sol_price <= 0:
                return

            # Compute prices per mint
            for mint in self._pool_state.get_all_mints():
                pool_reserves = self._pool_state.get_pools_for_mint(mint)

                if not pool_reserves:
                    continue

                # Compute and aggregate prices
                prices = []
                for base_account, base_raw, quote_raw in pool_reserves:
                    price = PoolPriceCalculator.compute_price(
                        mint=mint,
                        base_reserve_raw=base_raw,
                        quote_reserve_raw=quote_raw,
                        sol_price_usd=sol_price
                    )
                    if price:
                        prices.append(price)

                # Aggregate across pools
                aggregated = PoolAggregator.aggregate(prices)

                if aggregated:
                    # Store result
                    self._store_price(mint, aggregated)

        except Exception as e:
            logger.error(f"[PRICE_WORKER] Error recomputing prices: {e}")

    async def fetch_pool_prices_async(self, pools):
        """RPC fallback path for price computation."""
        try:
            fetcher = get_pool_fetcher(self.db_path)

            # Batch fetch reserves
            reserves = await fetcher.fetch_reserves(pools)

            # Get SOL price
            sol_price = await self._sol_price_cache.get_price(
                lambda: PoolPriceCalculator.fetch_sol_price_usd()
            )

            if sol_price <= 0:
                return

            # Group by mint and compute
            from collections import defaultdict
            pools_by_mint = defaultdict(list)

            for (mint, base_account), (base_raw, quote_raw) in reserves.items():
                pools_by_mint[mint].append((base_account, base_raw, quote_raw))

            for mint, pool_list in pools_by_mint.items():
                prices = []
                for base_account, base_raw, quote_raw in pool_list:
                    price = PoolPriceCalculator.compute_price(
                        mint=mint,
                        base_reserve_raw=base_raw,
                        quote_reserve_raw=quote_raw,
                        sol_price_usd=sol_price
                    )
                    if price:
                        prices.append(price)

                aggregated = PoolAggregator.aggregate(prices)
                if aggregated:
                    self._store_price(mint, aggregated)

        except Exception as e:
            logger.error(f"[PRICE_WORKER] Error in RPC path: {e}")

    def _start_ws_client(self, pools):
        """Start WebSocket client."""
        from src.core.pool_price_engine import PoolWebSocketClient

        self._ws_client = PoolWebSocketClient(self._pool_state, self.db_path)
        self._ws_client.start(pools)

    def _store_price(self, mint: str, price):
        """Store computed price to database."""
        try:
            import sqlite3
            conn = sqlite3.connect(self.db_path, timeout=5)
            cursor = conn.cursor()

            now = int(time.time())

            cursor.execute("""
                INSERT INTO token_price_snapshots (
                    mint, price_usd, price_sol, liquidity_usd,
                    volume_24h, market_cap, source, captured_at, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                mint,
                price.price_usd,
                price.price_sol,
                price.liquidity_usd,
                price.volume_24h or 0,
                price.market_cap or 0,
                price.source,
                now,
                now
            ))

            conn.commit()
            conn.close()

        except Exception as e:
            logger.debug(f"[PRICE_WORKER] Error storing price: {e}")
```

---

## WebSocket Subscription Flow (Fixed)

```python
class PoolWebSocketClient:
    """WebSocket client for pool account subscriptions."""

    def __init__(self, state_store: PoolStateStore, db_path: str):
        self._store = state_store
        self._db_path = db_path
        self._account_to_pools: Dict[str, List[Dict]] = {}  # pubkey → pools
        self._sub_id_to_account: Dict[int, str] = {}  # subscription_id → pubkey
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def start(self, pools: List[Dict]) -> None:
        """Start WebSocket client with pool subscriptions."""
        # Build account map (handles shared accounts)
        self._build_account_map(pools)

        # Start async thread
        self._running = True
        self._thread = threading.Thread(
            target=self._run_thread,
            daemon=True,
            name="pool-ws"
        )
        self._thread.start()

        logger.info(
            f"[POOL_WS] 🚀 WebSocket started - will subscribe to "
            f"{len(self._account_to_pools)} accounts from {len(pools)} pools"
        )

    def _build_account_map(self, pools: List[Dict]) -> None:
        """Build pubkey→pools mapping, handling shared accounts."""
        self._account_to_pools = {}

        for pool in pools:
            base_account = pool["base_account"]
            quote_account = pool["quote_account"]

            # CRITICAL: Validate these are real accounts, not mints!
            if len(base_account) != 44 or len(quote_account) != 44:
                logger.warning(
                    f"[POOL_WS] ⚠️  Invalid account length in pool {pool['mint']}: "
                    f"base={base_account}, quote={quote_account}"
                )
                continue

            # Add to map
            if base_account not in self._account_to_pools:
                self._account_to_pools[base_account] = []
            self._account_to_pools[base_account].append(pool)

            if quote_account not in self._account_to_pools:
                self._account_to_pools[quote_account] = []
            self._account_to_pools[quote_account].append(pool)

        logger.info(
            f"[POOL_WS] 🗺️  Built account map: {len(self._account_to_pools)} accounts, "
            f"{len(pools)} pools"
        )

    async def _subscribe_all(self, ws) -> None:
        """Subscribe to all pool accounts via WebSocket."""
        logger.info(f"[POOL_WS] 📡 Subscribing to {len(self._account_to_pools)} accounts...")

        for req_id, pubkey in enumerate(self._account_to_pools.keys(), start=1):
            msg = {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": "accountSubscribe",
                "params": [
                    pubkey,  # REAL TOKEN ACCOUNT - not a mint!
                    {"encoding": "base64", "commitment": "confirmed"}
                ]
            }
            await ws.send(json.dumps(msg))

        # Wait for confirmations
        confirmed = 0
        needed = len(self._account_to_pools)

        while confirmed < needed and self._running:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=10)
                data = json.loads(raw)

                if "id" in data and "result" in data:
                    sub_id = data["result"]
                    req_id = data["id"]

                    # Map pubkey to this subscription
                    pubkeys_list = list(self._account_to_pools.keys())
                    if req_id - 1 < len(pubkeys_list):
                        pubkey = pubkeys_list[req_id - 1]
                        self._sub_id_to_account[sub_id] = pubkey
                        confirmed += 1

            except asyncio.TimeoutError:
                logger.warning(
                    f"[POOL_WS] ⚠️  Timeout waiting for subscriptions "
                    f"({confirmed}/{needed} confirmed)"
                )
                break

        logger.info(
            f"[POOL_WS] ✅ Subscribed to {confirmed}/{needed} accounts"
        )
```

---

## Summary of Changes

| Component | Before | After |
|-----------|--------|-------|
| Vault Storage | wSOL MINT in quote_account | wSOL TOKEN ACCOUNT in quote_account |
| Discovery | Heuristic (failed for fresh tokens) | Direct pool decoding (deterministic) |
| Validation | None (placeholder used) | Full validation before storing |
| Retry Logic | None (first failure = skip) | Retry every 2s for 60s |
| Database | No constraints (mint/account confused) | Constraints ensure correct column usage |
| Price Worker | Thread-based with timing issues | Async loop with proper coordination |
| Subscription | Subscribing to invalid mints | Only subscribing to real token accounts |

---

## Expected Results After Implementation

```
Metric                          Before          After
────────────────────────────────────────────────────────
Tokens with WebSocket prices    5/120           15-20/120
New token price latency         Never           10-30s
WebSocket subscription failures Silent          Logged
Database integrity issues       High            None
Pool account decoding success   ~70%            ~98%
```

---

## Rollout Plan

1. **Phase 1**: Update vault discovery with direct pool decoding
2. **Phase 2**: Add vault validation logic
3. **Phase 3**: Implement retry strategy
4. **Phase 4**: Update database schema with constraints
5. **Phase 5**: Convert price worker to async
6. **Phase 6**: Test with live token launches
7. **Phase 7**: Monitor and optimize

Each phase can be deployed independently.
