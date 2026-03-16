# Vault Discovery Integration Guide

**Purpose**: Integrate RPC-authoritative vault discovery into existing pool detection and registration flow.

---

## Integration Points

### 1. Pool Detection (pumpfun_curve_listener.py)

**Current Flow**:
```python
async def on_migration_account_detected(migration_data):
    # Parse migration account with fixed offsets
    vaults = parse_migration_offsets(migration_data)  # ❌ Unreliable

    # Register immediately without validation
    await register_pool(vaults)  # ❌ No validation
```

**New Flow**:
```python
async def on_migration_account_detected(token_mint, migration_data):
    # Extract token mint from migration account
    token_mint = migration_data.get("token_mint")

    # Use RPC-authoritative discovery
    vault_pair = await discover_vaults_rpc(token_mint, rpc_client)  # ✅ Authoritative

    if vault_pair:
        # Register only after full validation
        await register_vault_pair(token_mint, vault_pair, db, price_worker)  # ✅ Validated
    else:
        # Schedule retry with backoff
        await schedule_vault_discovery_retry(token_mint, attempt=1)
```

**Code Changes**:

```python
# In pumpfun_curve_listener.py

from vault_discovery_implementation import discover_vaults_rpc, register_vault_pair

class PumpFunCurveListener:
    async def on_pool_detected(self, token_mint: str, migration_data: Dict):
        """
        New pool detected via migration account.
        Use RPC-authoritative vault discovery.
        """
        try:
            logger.info(f"[POOL_DETECT] New token detected: {token_mint[:16]}...")

            # RPC-based vault discovery (replaces fixed-offset parsing)
            vault_pair = await discover_vaults_rpc(
                token_mint=token_mint,
                rpc_client=self.rpc_client,
                ws_monitor=self.ws_monitor,
                max_retries=3
            )

            if vault_pair:
                # Register with full validation
                success = await register_vault_pair(
                    token_mint=token_mint,
                    vault_pair=vault_pair,
                    db=self.db,
                    price_worker=self.price_worker
                )

                if success:
                    logger.info(f"[POOL_DISCOVER_FALLBACK] ✅ Pool registered via RPC discovery: {token_mint[:16]}...")
                else:
                    logger.warning(f"[POOL_DISCOVER_FALLBACK] Database registration failed, retrying")
                    await self._schedule_retry(token_mint, attempt=1)
            else:
                # Discovery failed - schedule retry
                logger.warning(f"[POOL_DISCOVER_FALLBACK] Vault discovery failed, scheduling retry...")
                await self._schedule_retry(token_mint, attempt=1)

        except Exception as e:
            logger.error(f"[POOL_DETECT] Error during vault discovery: {e}")
            await self._schedule_retry(token_mint, attempt=1)

    async def _schedule_retry(self, token_mint: str, attempt: int):
        """Schedule vault discovery retry with exponential backoff."""
        max_attempts = 10

        if attempt >= max_attempts:
            logger.error(f"[VAULT_DISCOVERY] Giving up after {max_attempts} attempts for {token_mint[:16]}...")
            return

        # Exponential backoff: 30s, 45s, 67.5s, ..., capped at 10 minutes
        delay = min(30 * (1.5 ** (attempt - 1)), 600)

        logger.info(f"[VAULT_DISCOVERY] Scheduling retry {attempt}/{max_attempts} in {delay:.0f}s")

        # Schedule retry using asyncio.create_task or similar
        asyncio.create_task(asyncio.sleep(delay))
        asyncio.create_task(self.on_pool_detected(token_mint, {}))
```

---

### 2. Legacy Fallback Integration

**Handle pools that can't be discovered via RPC** (e.g., non-standard layouts):

```python
async def on_migration_account_detected(self, token_mint: str, migration_data: Dict):
    """
    Primary: RPC-authoritative discovery
    Fallback: Legacy fixed-offset parsing (for edge cases)
    """

    # Try RPC discovery first
    vault_pair = await discover_vaults_rpc(token_mint, self.rpc_client)

    if vault_pair:
        # Use RPC-discovered vaults (mark as "rpc_authoritative")
        await register_vault_pair(token_mint, vault_pair, self.db, self.price_worker)
    else:
        # Fallback to legacy parsing for edge cases
        logger.warning(f"[VAULT_DISCOVERY] RPC discovery failed, trying legacy parsing...")
        try:
            vaults = self._parse_migration_offsets_legacy(migration_data)
            if vaults:
                # Mark as "legacy_fallback" in database
                await register_vault_pair_legacy(token_mint, vaults, self.db, "legacy_fallback")
        except Exception as e:
            logger.error(f"[VAULT_DISCOVERY] Both RPC and legacy discovery failed: {e}")
```

---

### 3. Database Schema Update

Add `discovery_method` column to track which method was used:

```sql
-- Add column if not exists
ALTER TABLE token_pool_accounts ADD COLUMN discovery_method TEXT DEFAULT 'unknown';

-- Create index for discovery method
CREATE INDEX IF NOT EXISTS idx_discovery_method ON token_pool_accounts(discovery_method);

-- Update existing rows (mark as legacy if no discovery_method set)
UPDATE token_pool_accounts SET discovery_method = 'legacy_offset_parsing' WHERE discovery_method = 'unknown';

-- Track new RPC-discovered vaults
-- INSERT statements will use discovery_method = 'rpc_authoritative'
```

---

### 4. Price Worker Integration

**Trigger WebSocket refresh after vault registration**:

```python
# In price_worker.py

class BackgroundPriceWorker:
    async def trigger_pool_refresh(self):
        """
        Reload pools from database and refresh WebSocket subscriptions.
        Called after new vaults are registered.
        """
        try:
            from src.core.pool_price_engine import get_pool_fetcher

            fetcher = get_pool_fetcher(self.db_path)
            pools = fetcher.get_active_pools()

            if pools and self._ws_client:
                # Disconnect and reconnect with updated pool list
                self._ws_client.refresh_pools(pools)
                logger.info(f"[PRICE_WORKER] WebSocket refreshed with {len(pools)} pools")

        except Exception as e:
            logger.error(f"[PRICE_WORKER] WebSocket refresh failed: {e}")
```

---

### 5. Health Endpoint Update

Add vault discovery diagnostics to health check:

```python
# In price_api.py

@app.route('/api/price/health')
def health():
    """Extended health endpoint with vault diagnostics."""

    from vault_discovery_implementation import metrics

    return {
        "status": "ok",
        "pool_stats": {
            # ... existing fields ...
            "vault_discovery": {
                "attempts": metrics.discovery_attempts,
                "success_rate": metrics.get_success_rate(),
                "validation_failures": metrics.validation_failures,
                "quote_resolution_method": metrics.quote_resolution_method,
                "registration_success": metrics.registration_success,
            }
        }
    }
```

---

## Configuration & Tuning

### Environment Variables

```bash
# .env

# Vault discovery
VAULT_DISCOVERY_ENABLED=true
VAULT_DISCOVERY_MAX_RETRIES=10
VAULT_DISCOVERY_INITIAL_BACKOFF=30
VAULT_DISCOVERY_MAX_BACKOFF=600
VAULT_DISCOVERY_CANDIDATES_LIMIT=20
VAULT_DISCOVERY_USE_FALLBACK=true

# WebSocket
HELIUS_WS_URL="wss://mainnet.helius-rpc.com/?api-key=..."
HELIUS_RPC_URL="https://mainnet.helius-rpc.com/?api-key=..."
```

### Configuration Class

```python
# In vault_discovery_implementation.py or new config file

@dataclass
class VaultDiscoveryConfig:
    """Configuration for vault discovery."""

    enabled: bool = True
    max_retries: int = 10
    initial_backoff: float = 30.0  # seconds
    max_backoff: float = 600.0      # seconds
    backoff_multiplier: float = 1.5
    candidates_limit: int = 20      # max candidates from getTokenLargestAccounts
    use_fallback: bool = True       # fall back to legacy parsing if RPC fails
    validation_timeout: float = 30.0 # seconds for RPC calls

    @classmethod
    def from_env(cls):
        """Load configuration from environment variables."""
        import os
        return cls(
            enabled=os.getenv("VAULT_DISCOVERY_ENABLED", "true").lower() == "true",
            max_retries=int(os.getenv("VAULT_DISCOVERY_MAX_RETRIES", "10")),
            initial_backoff=float(os.getenv("VAULT_DISCOVERY_INITIAL_BACKOFF", "30")),
            max_backoff=float(os.getenv("VAULT_DISCOVERY_MAX_BACKOFF", "600")),
            candidates_limit=int(os.getenv("VAULT_DISCOVERY_CANDIDATES_LIMIT", "20")),
            use_fallback=os.getenv("VAULT_DISCOVERY_USE_FALLBACK", "true").lower() == "true",
        )
```

---

## Rollout Strategy

### Phase 1: Parallel Operation (No Risk)

Both methods run in parallel, only RPC-discovered vaults are registered:

```python
async def on_migration_account_detected(self, token_mint, migration_data):
    # Try RPC discovery (new method)
    vault_pair = await discover_vaults_rpc(token_mint, self.rpc_client)

    if vault_pair:
        # Register RPC-discovered vaults
        await register_vault_pair(token_mint, vault_pair, self.db)

    # Log legacy parsing for comparison (no registration)
    if config.enabled_legacy_logging:
        try:
            legacy_vaults = parse_migration_offsets(migration_data)
            log_comparison(token_mint, vault_pair, legacy_vaults)
        except: pass
```

**Duration**: 1 week
**Metrics to Track**: Success rate, latency, comparison with legacy method

### Phase 2: RPC Primary with Legacy Fallback

RPC is primary, fallback to legacy only if RPC fails:

```python
async def on_migration_account_detected(self, token_mint, migration_data):
    vault_pair = await discover_vaults_rpc(token_mint, self.rpc_client)

    if vault_pair:
        await register_vault_pair(token_mint, vault_pair, self.db)
    elif config.use_fallback:
        # Fallback only if RPC fails
        legacy_vaults = parse_migration_offsets(migration_data)
        if legacy_vaults:
            await register_vault_pair_legacy(token_mint, legacy_vaults, self.db, "legacy_fallback")
```

**Duration**: 1 week
**Metrics to Track**: Fallback frequency, which tokens need fallback

### Phase 3: RPC Only (Full Migration)

Remove legacy parsing entirely:

```python
async def on_migration_account_detected(self, token_mint, migration_data):
    vault_pair = await discover_vaults_rpc(token_mint, self.rpc_client)

    if vault_pair:
        await register_vault_pair(token_mint, vault_pair, self.db)
    else:
        # Manual intervention required
        logger.error(f"Vault discovery failed, manual mapping needed: {token_mint}")
        await alert_operator(token_mint)
```

**Duration**: Ongoing
**Metrics to Track**: Manual intervention frequency, operator response time

---

## Testing Plan

### Unit Tests

```python
# tests/test_vault_discovery.py

import pytest
from vault_discovery_implementation import *

class TestTokenAccountValidation:
    """Test SPL token account validation."""

    async def test_validate_token_accounts_success(self):
        """Valid token accounts pass all checks."""
        candidates = ["valid_token_account_1", "valid_token_account_2"]
        validated = await validate_token_accounts(candidates, TEST_TOKEN_MINT, mock_rpc)

        assert len(validated) == 2
        assert all(v.decoded.mint == TEST_TOKEN_MINT for v in validated)

    async def test_validate_token_accounts_owner_check(self):
        """Invalid owner fails validation."""
        candidates = ["wrong_owner_account"]
        validated = await validate_token_accounts(candidates, TEST_TOKEN_MINT, mock_rpc)

        assert len(validated) == 0

    async def test_validate_token_accounts_size_check(self):
        """Wrong size fails validation."""
        candidates = ["wrong_size_account"]
        validated = await validate_token_accounts(candidates, TEST_TOKEN_MINT, mock_rpc)

        assert len(validated) == 0


class TestBaseVaultIdentification:
    """Test base vault selection heuristics."""

    def test_identify_base_vault_highest_balance(self):
        """Highest balance account selected."""
        accounts = [
            ValidatedTokenAccount(address="low_balance", balance=100, ...),
            ValidatedTokenAccount(address="high_balance", balance=1000, ...),
        ]

        best = identify_base_vault(accounts)
        assert best.address == "high_balance"

    def test_identify_base_vault_with_owner(self):
        """Account with owner delegation scores higher."""
        accounts = [
            ValidatedTokenAccount(address="no_owner", decoded=...),  # owner="0"*44
            ValidatedTokenAccount(address="with_owner", decoded=...),  # owner=pool_addr
        ]

        best = identify_base_vault(accounts)
        assert best.address == "with_owner"


class TestQuoteVaultResolution:
    """Test quote vault resolution methods."""

    async def test_resolve_quote_vault_owner_chaining(self):
        """Quote resolved from base vault owner."""
        base = ValidatedTokenAccount(address="base", decoded=...)
        quote = await resolve_quote_vault_from_base(base, TOKEN_MINT, mock_rpc)

        assert quote is not None
        assert quote != base.address

    async def test_validate_quote_vault_spl_token(self):
        """SPL token quote vault validated."""
        quote = await validate_quote_vault(QUOTE_VAULT, mock_rpc)

        assert quote is not None
        assert quote["type"] == "spl_token"
        assert quote["decoded"].mint != TOKEN_MINT


class TestFullDiscovery:
    """Integration tests for full discovery pipeline."""

    async def test_discover_vaults_rpc_success(self):
        """Full discovery succeeds for known token."""
        vault_pair = await discover_vaults_rpc(CHIBIFY_MINT, mock_rpc)

        assert vault_pair is not None
        assert vault_pair.base_vault.address == EXPECTED_BASE_VAULT
        assert vault_pair.quote_vault["address"] == EXPECTED_QUOTE_VAULT

    async def test_discover_vaults_rpc_retry(self):
        """Failed discovery retries with backoff."""
        with patch('asyncio.sleep') as mock_sleep:
            vault_pair = await discover_vaults_rpc(INVALID_TOKEN, mock_rpc, max_retries=3)

        assert vault_pair is None
        assert mock_sleep.call_count == 2  # Two retries
```

### Integration Tests

```python
# tests/test_vault_discovery_integration.py

import pytest
from integration_test_harness import *

class TestRealRPCDiscovery:
    """Test against real Helius RPC (testnet or devnet)."""

    @pytest.mark.integration
    async def test_discover_known_token(self):
        """Discover vaults for known test token."""
        # Use test token with known vaults
        vault_pair = await discover_vaults_rpc(DEVNET_TEST_TOKEN, real_rpc_client)

        assert vault_pair is not None
        assert vault_pair.confidence_score > 0.8

    @pytest.mark.integration
    async def test_discover_chibify_on_mainnet(self):
        """Test vault discovery for real Chibify token on mainnet."""
        vault_pair = await discover_vaults_rpc(CHIBIFY_MAINNET_MINT, mainnet_rpc)

        assert vault_pair is not None
        # Verify vaults match expected
        assert vault_pair.base_vault.address in EXPECTED_CHIBIFY_BASES
        assert vault_pair.quote_vault["address"] in EXPECTED_CHIBIFY_QUOTES
```

---

## Success Metrics

After integration, measure:

| Metric | Target | Current | New |
|--------|--------|---------|-----|
| Vault existence rate | > 95% | 50% | ? |
| Discovery success rate | > 90% | 0% | ? |
| WebSocket events (valid vaults) | 100% | 0% | ? |
| Tokens with prices | > 95% registered | 0% | ? |
| RPC calls per token | < 25 | 0 | ? |
| Discovery latency | < 10s | N/A | ? |
| Operator intervention | < 5% | Unknown | ? |

---

## Rollback Plan

If RPC-based discovery causes issues:

```python
# Quick rollback to legacy (single config change)
VAULT_DISCOVERY_ENABLED = False

# This triggers fallback path:
async def on_migration_account_detected(self, token_mint, migration_data):
    if VAULT_DISCOVERY_ENABLED:
        vault_pair = await discover_vaults_rpc(...)
    else:
        # Roll back to legacy
        vault_pair = parse_migration_offsets(migration_data)
```

**Estimated downtime**: < 5 minutes (config change + restart)

---

## Support & Debugging

### Enable Debug Logging

```bash
export LOG_LEVEL=DEBUG

# Or in Python:
logging.getLogger("vault_discovery_implementation").setLevel(logging.DEBUG)
logging.getLogger("pumpfun_curve_listener").setLevel(logging.DEBUG)
```

### Diagnostic Script

```python
# scripts/debug_vault_discovery.py

async def debug_vault_discovery(token_mint: str):
    """Debug vault discovery for a specific token."""

    # Step 1: Get candidates
    print(f"Getting token largest accounts for {token_mint}...")
    candidates = await get_token_largest_accounts(token_mint, rpc_client)
    print(f"  → {len(candidates)} candidates")

    # Step 2: Validate
    print("Validating token accounts...")
    validated = await validate_token_accounts([c["address"] for c in candidates], token_mint, rpc_client)
    print(f"  → {len(validated)} passed validation")

    # Step 3: Identify base
    print("Identifying base vault...")
    base = identify_base_vault(validated)
    if base:
        print(f"  → {base.address} (balance={base.balance})")
    else:
        print("  → FAILED")
        return

    # Step 4: Resolve quote
    print("Resolving quote vault...")
    quote_addr = await resolve_quote_vault_from_base(base, token_mint, rpc_client)
    if quote_addr:
        print(f"  → {quote_addr}")
    else:
        print("  → FAILED")
        return

    # Step 5: Validate quote
    print("Validating quote vault...")
    quote = await validate_quote_vault(quote_addr, rpc_client)
    if quote:
        print(f"  → {quote['type']} (address={quote['address']})")
    else:
        print("  → FAILED")

    print("\n✅ Full discovery successful!")
```

Usage:
```bash
python scripts/debug_vault_discovery.py 5cDhM4yMKipQkjSGdvYnqPdiJz685Z96rbe6GSYppump
```

---

## Questions & Answers

**Q: What if `getTokenLargestAccounts` returns accounts that aren't the AMM vault?**

A: Validation phase filters to SPL token accounts owned by token program. The identify_base_vault heuristics score by delegation/owner (points to pool), WebSocket activity, and balance. Most real AMM vaults will score highest.

**Q: How do we handle pools with custom vault naming or non-standard layouts?**

A: The fallback query pool registry and owner-chaining methods handle most cases. For truly custom layouts, manual mapping with operator alert is required (< 5% of tokens).

**Q: Is there any security risk from relying on RPC for vault discovery?**

A: No. RPC validation just confirms vault accounts exist and have valid SPL token structure. We still validate against the blockchain state before registering. Worst case: we miss a vault (false negative), not discover a fake one (false positive).

**Q: How does this compare cost-wise to the legacy approach?**

A: Legacy approach: free but produces invalid vaults (then WebSocket gets zero events). RPC approach: ~15-20 credits per token (one-time cost), but produces valid vaults that generate prices. ROI is positive.
