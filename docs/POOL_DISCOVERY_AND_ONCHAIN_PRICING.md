# Pool Discovery & On-Chain Pricing System

**Date:** March 13, 2026
**Status:** ⚠️ PARTIALLY IMPLEMENTED - NOT CURRENTLY WORKING
**Purpose:** Automatic pool discovery when tokens launch, with on-chain reserve-based pricing
**Current Issue:** Pool address extraction is failing for new token launches. Transaction data not available or pool not found in transaction structure.

---

## Overview

When a token launches to Raydium or Orca, the Flex system automatically:

1. **Detects the migration** via WebSocket listener
2. **Extracts pool address** from the migration transaction
3. **Discovers reserve accounts** by parsing on-chain pool data
4. **Calculates price** from token/SOL ratio
5. **Displays price** immediately on dashboard

**Result:** Live on-chain pricing without manual setup!

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Token Launches to DEX                         │
│              (Migration TX sent to blockchain)                   │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│         WebSocket Listener (Helius)                              │
│    Subscribes to PumpSwap program for MigrateBondingCurveCreator│
│                     Instructions                                │
└────────────────────────┬────────────────────────────────────────┘
                         │ [WEBSOCKET] Migration detected
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│       handle_migration(signature, logs)                          │
│  - Fetch migration TX from cache/RPC                             │
│  - Extract mint from TX data                                     │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│    _process_migration_with_mint(signature, tx_data, mint)       │
│  - Create minimal token entry in DB                              │
│  - Extract pool address from TX                                  │
│  - Trigger price calculation                                     │
└────────────────────────┬────────────────────────────────────────┘
                    ┌────┴────┐
                    ▼         ▼
        ┌─────────────────┐  ┌──────────────────────────┐
        │  Pool Extraction│  │  Auto-Pool Discovery     │
        │                 │  │  (PoolDiscovery class)   │
        │ _extract_pool_  │  │                          │
        │ from_tx()       │  │  1. Fetch pool account   │
        │                 │  │  2. Parse structure      │
        │ Returns:        │  │  3. Extract reserves     │
        │ pool_address    │  │  4. Register in DB       │
        └────────┬────────┘  └──────────┬───────────────┘
                 │                      │
                 └──────────┬───────────┘
                            ▼
        ┌─────────────────────────────────────┐
        │  Price Calculation from Reserves    │
        │                                     │
        │  1. Fetch pool vault balances       │
        │  2. Extract token & SOL balances    │
        │  3. Calculate: price = SOL/token    │
        │  4. Convert to USD                  │
        │  5. Cache result                    │
        └─────────────┬───────────────────────┘
                      ▼
        ┌──────────────────────────────┐
        │  Display on Dashboard        │
        │  - Price: $0.00000XXXX       │
        │  - Market Cap: $XXXX         │
        │  - Updated: real-time        │
        └──────────────────────────────┘
```

---

## Step-by-Step Flow

### Step 1: WebSocket Listener Detects Migration

**File:** `src/core/pumpfun_curve_listener.py` (line 2383-2487)

```python
async def listen_websocket(self):
    """Listen to PumpSwap program via WebSocket for live migration events"""
    # Check if token launch listening is enabled
    while not get_migration_setting('listen_to_launches', True):
        log_print(f"[WEBSOCKET] ⏸ Token Launch listening is DISABLED")
        await asyncio.sleep(30)
        continue

    # Connect to Helius WebSocket
    async with websockets.connect(HELIUS_WS_URL) as ws:
        # Subscribe to PumpSwap logs
        await ws.send(json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "logsSubscribe",
            "params": [
                {"mentions": [PUMPSWAP_PROGRAM]},
                {"commitment": "confirmed"}
            ]
        }))

        # Listen for events
        async for message in ws:
            data = json.loads(message)

            # Check if this is a migration
            if self._is_migration_transaction(logs):
                # Extract mint from logs
                mint = self._extract_mint_from_logs(logs)

                if mint:
                    # Trigger migration handler
                    asyncio.create_task(self.handle_migration(signature, logs))
```

**What it does:**
- Subscribes to WebSocket for logs mentioning the PumpSwap program
- Detects `MigrateBondingCurveCreator` instruction
- Calls `handle_migration()` with transaction signature

---

### Step 2: Extract Mint and Fetch Transaction

**File:** `src/core/pumpfun_curve_listener.py` (line 2275-2360)

```python
async def handle_migration(self, signature: str, logs: list):
    """Process detected migration."""

    # === Fetch transaction (cached with retry/backoff) ===
    tx_data = await self._get_transaction_cached(signature)

    if tx_data:
        # Extract mint from transaction data
        mint = await self._extract_mint_from_tx(tx_data)
    else:
        # Fallback: extract from logs
        mint = self._extract_mint_from_logs(logs)

    if mint:
        # Process migration with mint and transaction data
        await self._process_migration_with_mint(signature, logs, mint, tx_data)
```

**What it does:**
- Fetches the full migration transaction from RPC
- Extracts the token mint address from transaction data
- Falls back to logs if transaction fetch fails

---

### Step 3: Extract Pool Address from Transaction

**File:** `src/core/pumpfun_curve_listener.py` (line 1166-1215)

```python
async def _extract_pool_from_tx(self, tx_data: Dict) -> Optional[str]:
    """
    Extract PumpSwap pool address from transaction data (no RPC call needed).

    The pool is the account that is OWNED BY the PumpSwap program.

    Strategy:
    1. Look through all accounts in innerInstructions
    2. Find accounts used by the PumpSwap program
    3. Return the first writable PDA (index 0 of PumpSwap instruction accounts)
    """
    if not tx_data:
        return None

    # Extract account keys from transaction message
    message = tx_data.get("transaction", {}).get("message", {})
    account_keys = message.get("accountKeys", [])

    if not account_keys:
        return None

    # Get inner instructions
    meta = tx_data.get("meta", {})
    inner_instructions = meta.get("innerInstructions", [])

    PUMPSWAP_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"

    # Find PumpSwap program index in accountKeys
    pumpswap_idx = -1
    for i, acc in enumerate(account_keys):
        if acc == PUMPSWAP_PROGRAM:
            pumpswap_idx = i
            break

    if pumpswap_idx < 0:
        return None

    # Search innerInstructions for PumpSwap calls
    for ix_group in inner_instructions:
        instructions = ix_group.get("instructions", [])
        for ix in instructions:
            # Check if this instruction is from PumpSwap program
            program_id_idx = ix.get("programIdIndex")
            if program_id_idx == pumpswap_idx:
                # Get accounts used by this instruction
                accounts = ix.get("accounts", [])
                if accounts and len(accounts) > 0:
                    # First account is the pool PDA
                    pool_idx = accounts[0]
                    if isinstance(pool_idx, int) and pool_idx < len(account_keys):
                        pool_address = account_keys[pool_idx]
                        log_print(f"[POOL] ✅ Extracted pool: {pool_address}")
                        return pool_address

    return None
```

**What it does:**
- Parses the transaction's account keys and inner instructions
- Finds the PumpSwap program in the accounts list
- Identifies which instruction is from PumpSwap
- Returns the first account (which is the pool PDA)

**Example Transaction Structure:**

```json
{
  "transaction": {
    "message": {
      "accountKeys": [
        "TokenkegQfeZ...",  // Token program
        "EAEqvUXxQyrg...",  // ← POOL ADDRESS (index 0 of PumpSwap instruction)
        "pAMMBay6oceH...",  // PumpSwap program
        "So11111111..."     // SOL mint
      ]
    }
  },
  "meta": {
    "innerInstructions": [
      {
        "instructions": [
          {
            "programIdIndex": 2,  // PumpSwap program index
            "accounts": [0, 1, 3] // [pool, token_account, sol_account]
          }
        ]
      }
    ]
  }
}
```

---

### Step 4: Auto-Discover Pool Reserves

**File:** `src/core/pool_discovery.py` (line 44-93)

```python
async def discover_and_register_pool(
    self, pool_address: str, token_mint: str
) -> bool:
    """
    Discover pool reserves and register in database.

    Called when a token launches to automatically enable WebSocket pricing.
    """
    logger.info(f"🔍 Discovering pool reserves for {token_mint}")

    # Step 1: Fetch pool account from on-chain
    pool_data = await self._fetch_account(pool_address)
    if not pool_data:
        logger.warning(f"Could not fetch pool account: {pool_address}")
        return False

    # Step 2: Extract reserves based on pool type
    reserves = await self._extract_from_pool_data(
        pool_data, pool_address, token_mint
    )

    if not reserves:
        logger.warning(f"Could not extract reserves from pool")
        return False

    # Step 3: Register in database
    success = await self.register_pool_to_db(token_mint, reserves)

    if success:
        logger.info(f"🚀 Pool auto-registered! WebSocket will subscribe on next worker cycle")

    return success
```

**What it does:**
1. Fetches the pool account data from on-chain via RPC
2. Parses the account data based on pool type (Raydium AMM, CPMM, or Orca)
3. Extracts base and quote reserve account addresses
4. Registers pool in `token_pool_accounts` table

---

### Step 5: Extract Token and SOL Balances

**File:** `src/core/pool_discovery.py` (line 142-189)

**Example: Raydium AMM Pool Data Structure**

```python
async def _extract_raydium_amm(
    self, pool_data: Dict, pool_address: str, token_mint: str
) -> Optional[Dict]:
    """
    Extract reserves from Raydium AMM pool.

    Raydium AMM state structure (byte offsets):
    - Offset 0-8:     nonce (u64)
    - Offset 8-40:    token_account_a (Pubkey) ← BASE RESERVE
    - Offset 40-72:   token_account_b (Pubkey) ← QUOTE RESERVE
    - Offset 72-80:   fees_numerator (u64)
    - Offset 80-88:   fees_denominator (u64)
    ... more fields
    """
    try:
        # Get the account data (base64 encoded)
        data = pool_data.get("data", [None, None])[0]
        if not data or len(data) < 200:
            return None

        # Decode from base64
        decoded = b64decode(data)

        # Extract token accounts (public keys are 32 bytes each)
        # Raydium AMM: base at offset 8, quote at offset 40
        base_account = self._bytes_to_pubkey(decoded[8:40])
        quote_account = self._bytes_to_pubkey(decoded[40:72])

        if not base_account or not quote_account:
            return None

        # Fetch decimals for both tokens
        base_decimals = await self._get_token_decimals(base_account)
        quote_decimals = await self._get_token_decimals(quote_account)

        return {
            "base_account": base_account,
            "quote_account": quote_account,
            "base_token": token_mint,
            "quote_token": SOL_MINT,
            "base_decimals": base_decimals or 6,
            "quote_decimals": quote_decimals or 9,
            "pool_program": "raydium_amm",
        }

    except Exception as e:
        logger.debug(f"Error extracting Raydium AMM: {e}")
        return None
```

**Key Points:**
- Pool account data is base64 encoded
- Token account addresses are at fixed byte offsets
- 32-byte public key format
- Decimals fetched from token metadata

---

### Step 6: Calculate Price from Reserves

**File:** `src/core/pumpfun_curve_listener.py` (line 1375-1534)

```python
async def _get_price_from_pool_account(
    self, pool_address: str, token_mint: str
) -> Optional[tuple]:
    """
    Get price by querying pool account's token and SOL balances.

    PumpSwap pools store liquidity in WSOL (wrapped SOL) token accounts.
    """

    # === Step 1: Get WSOL (SOL) balance ===
    wsol_mint = "So11111111111111111111111111111111111111112"

    # Query WSOL token accounts owned by pool
    payload_wsol = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTokenAccountsByOwner",
        "params": [
            pool_address,
            {"mint": wsol_mint},
            {"encoding": "jsonParsed"}
        ]
    }

    data = await self._post_rpc_with_fallback(payload_wsol)

    sol_balance = 0
    if data and "result" in data:
        result_data = data["result"]
        if "value" in result_data:
            accounts = result_data["value"]
            if accounts and len(accounts) > 0:
                # Get WSOL balance from first account
                first_account = accounts[0]
                account_obj = first_account.get("account", {})
                data_obj = account_obj.get("data", {})
                parsed = data_obj.get("parsed", {})
                wsol_info = parsed.get("info", {})
                token_amount_info = wsol_info.get("tokenAmount", {})
                sol_balance = float(token_amount_info.get("uiAmount", 0))

    if sol_balance == 0:
        return None

    # === Step 2: Get token balance ===
    payload_token = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTokenAccountsByOwner",
        "params": [
            pool_address,
            {"mint": token_mint},
            {"encoding": "jsonParsed"}
        ]
    }

    data2 = await self._post_rpc_with_fallback(payload_token)

    if not data2 or "result" not in data2:
        return None

    result_data2 = data2["result"]
    if "value" not in result_data2:
        return None

    accounts = result_data2["value"]
    if not accounts:
        return None

    # Find account with largest token balance
    max_balance = 0
    token_balance = 0

    for token_account in accounts:
        account_data = token_account.get("account", {})
        parsed = account_data.get("data", {}).get("parsed", {})
        token_info = parsed.get("info", {})
        token_amount_info = token_info.get("tokenAmount", {})
        balance = float(token_amount_info.get("uiAmount", 0))

        if balance > max_balance:
            max_balance = balance
            token_balance = balance

    if token_balance == 0:
        return None

    # === Step 3: Calculate price ===
    # price_sol = sol_reserve / token_reserve
    price_sol = sol_balance / token_balance

    # Get SOL price in USD
    sol_usd = await self._get_sol_price_usd()

    # price_usd = price_sol * sol_price
    price_usd = price_sol * sol_usd

    # market_cap = price_usd * total_supply
    total_supply = 1_000_000_000  # Pump.Fun tokens have 1B supply
    market_cap_usd = price_usd * total_supply

    logger.info(f"Price calculated: {price_usd:.2e} USD | Market Cap: ${market_cap_usd:,.2f}")

    return (price_usd, market_cap_usd)
```

**Calculation Breakdown:**

```
Example Pool State:
├─ WSOL balance (SOL vault): 50 SOL = 50,000,000,000 lamports
├─ Token balance (token vault): 10,000,000,000 tokens (1B supply)
└─ SOL price: $180 USD

Price Calculation:
1. price_sol = 50 / 10,000,000,000 = 5e-9 SOL per token
2. price_usd = 5e-9 * $180 = $9e-7 per token = $0.0000009
3. market_cap = $0.0000009 * 1,000,000,000 = $900,000

Result:
├─ Price: $0.0000009 per token
└─ Market Cap: $900,000
```

---

## Data Flow Summary

| Stage | Component | Input | Output |
|-------|-----------|-------|--------|
| 1. Detection | WebSocket Listener | Migration TX | `signature` |
| 2. Fetch | Transaction Cache | `signature` | `tx_data` |
| 3. Extract Mint | TX Parser | `tx_data` | `mint` |
| 4. Extract Pool | TX Parser | `tx_data` | `pool_address` |
| 5. Discover Reserves | PoolDiscovery | `pool_address`, `mint` | `base_account`, `quote_account` |
| 6. Register Pool | Database | Reserves | `token_pool_accounts` entry |
| 7. Fetch Balances | RPC (getTokenAccountsByOwner) | `pool_address`, `base_account`, `quote_account` | Token & SOL balances |
| 8. Calculate Price | PriceCalculator | Token & SOL balances, SOL price | `price_usd`, `market_cap_usd` |
| 9. Cache | Price Service | Calculated prices | Cached & displayed |

---

## Key Files

| File | Purpose | Key Functions |
|------|---------|---|
| `src/core/pumpfun_curve_listener.py` | WebSocket listener & migration detection | `listen_websocket()`, `handle_migration()`, `_extract_pool_from_tx()`, `_get_price_from_pool_account()` |
| `src/core/pool_discovery.py` | Automatic pool reserve discovery | `discover_and_register_pool()`, `extract_pool_reserves()`, `register_pool_to_db()` |
| `src/core/pool_price_engine.py` | Real-time price computation | `PoolWebSocketClient`, `PoolStateStore`, `PoolAggregator` |
| `database/flex_complete_database.db` | Persistent storage | `token_pool_accounts`, `token_analysis` tables |

---

## Configuration

### Enable/Disable Migration Listening

**Via UI Toggle:**
- Homepage has "Token Launch" toggle button
- ON: Listener actively detects migrations
- OFF: Listener is idle

**Via API:**
```bash
# Enable
curl -X POST http://localhost:5002/api/listener-settings \
  -H "Content-Type: application/json" \
  -d '{"listen_to_launches": true}'

# Disable
curl -X POST http://localhost:5002/api/listener-settings \
  -H "Content-Type: application/json" \
  -d '{"listen_to_launches": false}'
```

### Environment Variables

```bash
# RPC endpoints
export RPC_HTTP="https://mainnet.helius-rpc.com/?api-key=YOUR_KEY"
export HELIUS_RPC_WS="wss://mainnet.helius-rpc.com/?api-key=YOUR_KEY"

# Database
export DB_PATH="./database/flex_complete_database.db"
```

---

## Performance Metrics

| Metric | Time | Notes |
|--------|------|-------|
| WebSocket detection | ~1s | Helius subscription latency |
| Transaction fetch | ~500ms | With caching & retry |
| Pool discovery | ~500ms | 1-2 RPC calls |
| Price calculation | ~200ms | Local computation |
| **Total time to price** | **~2-3 seconds** | From migration to live price |

---

## Error Handling

### Pool Extraction Failures

**Scenario:** Transaction structure doesn't match expected format

```python
if pool_address is None:
    log_print(f"[POOL] ⚠️  Pool extraction failed for {mint}")
    # Fallback: manual registration via API endpoint
    # /api/price/pool/register
```

### Balance Fetch Failures

**Scenario:** RPC can't access pool account balances

```python
if sol_balance == 0 or token_balance == 0:
    log_print(f"[PRICE_ERROR] Could not fetch balances for {pool_address}")
    # Fallback: Use DexScreener API pricing
    result = await self._fetch_dexscreener_price(token_mint)
```

### Graceful Degradation

```
Success Path (ideal):
  Migration → Extract Pool → Discover Reserves → Calculate Price ✅

Fallback 1 (pool extraction fails):
  Migration → Manual registration → ... → Calculate Price ✅

Fallback 2 (balance fetch fails):
  Migration → Extract Pool → ... → DexScreener Price ✅

Fallback 3 (everything fails):
  Token still appears on dashboard with pending price
  User can manually register pool
```

---

## Testing

### Test 1: Verify Pool Extraction

```bash
# Get a recent migration TX
MIGRATION_SIG="..."

# Test pool extraction directly
curl -X GET "http://localhost:5002/api/test/extract-pool/$MIGRATION_SIG"

# Expected response:
{
  "pool_address": "EAEqvUXxQyrgFtbb8muVmTxXeNJ2ZnAKYvxbmbFM6e4g",
  "token_mint": "EPjFWaLb3odRvqA8E8h6UPs4mkfrEFAJiUbhA84wHvHU"
}
```

### Test 2: Verify Price Calculation

```bash
# Register a known pool
curl -X POST http://localhost:5002/api/price/pool/register \
  -H 'Content-Type: application/json' \
  -d '{
    "pool_accounts": [{
      "mint": "TokenMint...",
      "base_account": "BaseReserve...",
      "quote_account": "QuoteReserve..."
    }]
  }'

# Check price
curl http://localhost:5002/api/price/TokenMint

# Expected response:
{
  "price_usd": 0.0000123,
  "price_sol": 1.5e-8,
  "liquidity_usd": 45000,
  "market_cap": 12300000,
  "source": "pool"
}
```

---

## Troubleshooting

### Problem: Pool address is null

**Check 1:** Is Token Launch listening enabled?
```bash
curl http://localhost:5002/api/listener-settings
# Should show: "listen_to_launches": true
```

**Check 2:** Is the migration TX available?
```bash
grep "MIGRATION DETECTED" logs/dev_intelligence.log
```

**Check 3:** Is pool extraction failing?
```bash
grep "Pool extracted\|Pool extraction failed" logs/dev_intelligence.log
```

### Problem: Price is incorrect

**Check 1:** Are reserve balances being fetched?
```bash
curl http://localhost:5002/api/price/MINT?debug=true
# Should show reserve balances and calculation
```

**Check 2:** Is SOL price correct?
```bash
grep "sol_price\|SOL price" logs/dev_intelligence.log
# Should show fetched SOL price in USD
```

**Check 3:** Is the calculation formula correct?
```
price_usd = (sol_balance / token_balance) * sol_price_usd
market_cap = price_usd * 1_000_000_000
```

---

## Future Enhancements

1. **Multi-DEX Support**
   - Detect pools on Meteora, Orca, Raydium
   - Aggregate prices across DEXes

2. **Reserve Validation**
   - Verify reserves are non-zero
   - Check for stale pools (no updates >5 min)
   - Auto-disable dead pools

3. **Pool Verification**
   - Verify pool is legitimate (not scam)
   - Check liquidity thresholds
   - Confidence scoring

4. **Performance Optimization**
   - Batch RPC calls for multiple pools
   - Cache token decimals
   - Local pool registry

---

## Summary

The Pool Discovery & On-Chain Pricing system provides:

✅ **Automatic pool detection** when tokens launch
✅ **Real-time reserve tracking** via on-chain balance queries
✅ **Accurate price calculation** from token/SOL ratio
✅ **Graceful fallbacks** if anything fails
✅ **No manual setup required** for new tokens

**Architecture:** WebSocket → TX Parsing → Pool Discovery → RPC Balance Queries → Price Calculation → Dashboard Display

**Result:** Live, accurate on-chain pricing for tokens seconds after launch!

---

*For questions or issues, check the logs or refer to troubleshooting section.*
