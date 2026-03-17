# PumpSwap Discovery Pipeline — Production Validation Strategy

## Executive Summary

This document defines the production validation framework for the PumpSwap discovery + pricing pipeline after recent critical fixes. The strategy validates end-to-end correctness through replay testing, database assertions, telemetry analysis, and WebSocket verification across three groups: historical known-good migrations, previously failing migrations, and live migrations.

---

## 1. Replay Test Strategy

### 1.1 Architecture

```
Migration Signature (TX Hash)
    ↓
[Replay Module]
    ├─ Fetch TX via Helius API
    ├─ Pass to discovery pipeline
    ├─ Capture discovery_method, pool_address, vaults
    ├─ Query database for registration
    ├─ Wait for WebSocket updates
    └─ Validate price snapshot
```

### 1.2 Test Groups

#### Group 1: Known-Good Historical Migrations (5–10 signatures)

**Purpose:** Establish baseline — these migrations should have been resolvable before fixes.

**Sources:**
- Tokens that successfully registered before the recent fixes
- Query: `SELECT DISTINCT migration_tx FROM token_pool_accounts WHERE discovery_method IS NOT NULL LIMIT 10`
- Manually curated list of 5 signatures from high-liquidity tokens (Chibify, MOG, FWOG, etc.)

**Expected outcome:**
- All 5–10 pass end-to-end
- Majority resolve via `tx_parsing` or `vault_inference`
- Zero unresolved after replay

#### Group 2: Previously Failing Migrations (3–5 signatures)

**Purpose:** Validate fixes for known bugs.

**Sources:**
- Tokens stuck in "pending" state before recent fixes
- Query: `SELECT DISTINCT migration_tx FROM token_pool_accounts WHERE vault_validation_status = 'pending' AND discovery_method = 'unknown' ORDER BY created_at DESC LIMIT 5`
- MOG pool (A1HFqQZF3t16RQ8ENV9NLkVXL6E5Fu31sWk5s33jH5wn) is the primary test case

**Expected outcome:**
- All 3–5 now resolve (previously failed due to offset layout bug)
- Vaults extracted correctly from offset 72/104 or 232/264
- Registration completes with `vault_validation_status = 'validated'`

#### Group 3: Fresh Live Migrations (5+ signatures)

**Purpose:** Validate real-time discovery during active trading.

**Sources:**
- New migrations as they occur during test run
- Configure listener to log all migration signatures to a file
- Sample 5 fresh signatures every hour

**Expected outcome:**
- Average resolve_seconds ≤ 10s (goal: ≤ 5s)
- ≥ 90% resolve via `tx_parsing` (fastest path)
- WebSocket subscriptions active within 3s of resolution
- Price snapshots written within 5s of subscription

### 1.3 Replay Test Harness Pseudocode

```python
class ReplayTestHarness:
    def __init__(self, db_path, rpc_url):
        self.db = Database(db_path)
        self.rpc = RpcClient(rpc_url)
        self.discovery = DiscoveryPipeline(rpc_url, db_path)
        self.results = []

    def replay_migration(self, tx_signature: str, test_group: str) -> ReplayResult:
        """Replay a single migration signature."""
        start_time = time.time()

        try:
            # 1. Fetch TX
            tx_data = self.rpc.get_transaction(tx_signature)
            if not tx_data:
                return ReplayResult(
                    signature=tx_signature,
                    group=test_group,
                    status="FAILED",
                    reason="TX not found",
                    duration_ms=int((time.time() - start_time) * 1000)
                )

            # 2. Extract migration details
            migration = self.discovery.extract_migration_details(tx_data)
            if not migration:
                return ReplayResult(signature, group, "FAILED", "No migration found")

            # 3. Run discovery pipeline
            discovery_start = time.time()
            result = self.discovery.discover_and_register_pool(
                pool_address=migration['pool_address'],
                token_mint=migration['token_mint'],
                tx_signature=tx_signature
            )
            discovery_ms = int((time.time() - discovery_start) * 1000)

            # 4. Query database for registration
            reg = self.db.query("""
                SELECT pool_address, base_account, quote_account,
                       discovery_method, vault_validation_status
                FROM token_pool_accounts
                WHERE mint = ?
            """, (migration['token_mint'],))

            if not reg:
                return ReplayResult(
                    signature, group, "FAILED",
                    "Not registered", discovery_ms
                )

            # 5. Wait for WebSocket updates (up to 5s)
            ws_ready = self._wait_for_websocket_state(
                mint=migration['token_mint'],
                base_account=reg['base_account'],
                timeout_ms=5000
            )

            # 6. Verify price snapshot
            snapshot = self.db.query_one("""
                SELECT price_usd, source FROM token_price_snapshots
                WHERE mint = ? AND source = 'pool'
                ORDER BY created_at DESC LIMIT 1
            """, (migration['token_mint'],))

            success = (
                reg and
                reg['vault_validation_status'] == 'validated' and
                ws_ready and
                snapshot is not None
            )

            return ReplayResult(
                signature=tx_signature,
                group=test_group,
                status="PASSED" if success else "FAILED",
                reason="end-to-end success" if success else "snapshot missing",
                discovery_method=reg.get('discovery_method'),
                vault_status=reg.get('vault_validation_status'),
                duration_ms=int((time.time() - start_time) * 1000),
                ws_ready=ws_ready,
                has_snapshot=snapshot is not None
            )

        except Exception as e:
            return ReplayResult(
                signature, group, "ERROR", str(e),
                duration_ms=int((time.time() - start_time) * 1000)
            )

    def run_all_groups(self) -> ReplayReport:
        """Run all three test groups."""
        groups = {
            'historical_good': self._get_known_good_signatures(10),
            'previously_failing': self._get_previously_failing_signatures(5),
            'live': self._get_fresh_live_signatures(5),
        }

        for group_name, signatures in groups.items():
            for sig in signatures:
                result = self.replay_migration(sig, group_name)
                self.results.append(result)

                # Log each result
                status_symbol = "✓" if result.status == "PASSED" else "✗"
                print(f"{status_symbol} [{group_name}] {sig[:16]}... "
                      f"({result.duration_ms}ms, {result.discovery_method})")

        return self._generate_report()

    def _wait_for_websocket_state(self, mint: str, base_account: str,
                                   timeout_ms: int) -> bool:
        """Poll PoolStateStore for reserve updates."""
        from src.core.pool_price_engine import PoolStateStore
        store = PoolStateStore()  # or get singleton

        start = time.time() * 1000
        while (time.time() * 1000 - start) < timeout_ms:
            reserves = store.get_reserves(mint, base_account)
            if reserves and reserves[0] > 0 and reserves[1] > 0:
                return True
            time.sleep(0.1)

        return False
```

### 1.4 Execution Plan

```bash
# 1. Extract known-good signatures
sqlite3 database/flex_complete_database.db \
  "SELECT DISTINCT migration_tx FROM token_pool_accounts \
   WHERE discovery_method IN ('tx_parsing', 'vault_inference') \
   ORDER BY created_at DESC LIMIT 10" \
  > /tmp/known_good_sigs.txt

# 2. Extract previously failing signatures
sqlite3 database/flex_complete_database.db \
  "SELECT DISTINCT migration_tx FROM token_pool_accounts \
   WHERE vault_validation_status = 'pending' \
   ORDER BY created_at DESC LIMIT 5" \
  > /tmp/failing_sigs.txt

# 3. Run replay harness
python3 << 'EOF'
from replay_test_harness import ReplayTestHarness

harness = ReplayTestHarness(
    db_path="database/flex_complete_database.db",
    rpc_url=os.getenv("HELIUS_RPC_URL")
)

# Load signatures
with open('/tmp/known_good_sigs.txt') as f:
    good_sigs = [line.strip() for line in f if line.strip()]

with open('/tmp/failing_sigs.txt') as f:
    failing_sigs = [line.strip() for line in f if line.strip()]

# Run replay
report = harness.run_all_groups()
print(report.summary())
report.to_json('/tmp/replay_results.json')
EOF

# 4. View results
python3 -m json.tool /tmp/replay_results.json
```

---

## 2. Discovery Validation

### 2.1 Validation Rules

```python
class DiscoveryValidator:
    """Validate discovery correctness for a token."""

    @staticmethod
    def validate_discovery(token_mint: str, db) -> ValidationResult:
        """Run all discovery assertions."""
        errors = []

        # 1. Fetch registration
        pool = db.query_one("""
            SELECT pool_address, base_account, quote_account,
                   pool_program, discovery_method
            FROM token_pool_accounts
            WHERE mint = ?
            ORDER BY created_at DESC LIMIT 1
        """, (token_mint,))

        if not pool:
            return ValidationResult(
                mint=token_mint,
                passed=False,
                errors=["No pool registered"]
            )

        # 2. Assertion: pool_address exists
        if not pool['pool_address']:
            errors.append("pool_address is NULL")

        # 3. Assertion: pool_address != base_vault
        if pool['pool_address'] == pool['base_account']:
            errors.append(
                f"pool_address == base_account: {pool['pool_address']} "
                "(invalid — same account cannot be pool and vault)"
            )

        # 4. Assertion: pool_address != quote_vault
        if pool['pool_address'] == pool['quote_account']:
            errors.append(
                f"pool_address == quote_account: {pool['pool_address']} "
                "(invalid — same account cannot be pool and vault)"
            )

        # 5. Assertion: base_vault != quote_vault
        if pool['base_account'] == pool['quote_account']:
            errors.append(
                f"base_account == quote_account: {pool['base_account']} "
                "(invalid — base and quote vaults must be different)"
            )

        # 6. Assertion: pool_program is valid
        valid_programs = {
            'pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA',  # PumpSwap
            '675kPX9MHTjS2zt1qrVrrVrZg1ankqqgoerEmJlwQ1K',  # Raydium AMM v4
            '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P',  # PumpFun V1
        }
        if pool['pool_program'] not in valid_programs:
            errors.append(
                f"pool_program unknown: {pool['pool_program']}"
            )

        # 7. Assertion: discovery_method recorded
        if not pool['discovery_method']:
            errors.append("discovery_method is NULL or 'unknown'")
        elif pool['discovery_method'] not in [
            'tx_parsing', 'vault_inference', 'rpc_multipool_discovery', 'unknown'
        ]:
            errors.append(
                f"discovery_method invalid: {pool['discovery_method']}"
            )

        return ValidationResult(
            mint=token_mint,
            pool_address=pool['pool_address'],
            base_account=pool['base_account'],
            quote_account=pool['quote_account'],
            passed=len(errors) == 0,
            errors=errors
        )
```

### 2.2 Batch Validation SQL

```sql
-- Check for invalid pools (any violations)
SELECT
    mint,
    pool_address,
    base_account,
    quote_account,
    pool_program,
    discovery_method,
    CASE
        WHEN pool_address IS NULL THEN 'pool_address NULL'
        WHEN pool_address = base_account THEN 'pool == base'
        WHEN pool_address = quote_account THEN 'pool == quote'
        WHEN base_account = quote_account THEN 'base == quote'
        WHEN pool_program NOT IN (
            'pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA',
            '675kPX9MHTjS2zt1qrVrrVrZg1ankqqgoerEmJlwQ1K',
            '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P'
        ) THEN 'pool_program invalid'
        WHEN discovery_method IS NULL OR discovery_method = 'unknown'
            THEN 'discovery_method missing'
        ELSE NULL
    END AS violation
FROM token_pool_accounts
WHERE violation IS NOT NULL;

-- Count violations by type
SELECT
    violation,
    COUNT(*) as count
FROM (
    SELECT CASE
        WHEN pool_address IS NULL THEN 'pool_address NULL'
        WHEN pool_address = base_account THEN 'pool == base'
        WHEN pool_address = quote_account THEN 'pool == quote'
        WHEN base_account = quote_account THEN 'base == quote'
        WHEN pool_program NOT IN (
            'pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA',
            '675kPX9MHTjS2zt1qrVrrVrZg1ankqqgoerEmJlwQ1K',
            '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P'
        ) THEN 'pool_program invalid'
        WHEN discovery_method IS NULL OR discovery_method = 'unknown'
            THEN 'discovery_method missing'
        ELSE NULL
    END AS violation
    FROM token_pool_accounts
)
WHERE violation IS NOT NULL
GROUP BY violation
ORDER BY count DESC;
```

---

## 3. Vault Validation

### 3.1 Vault Correctness Checks

```python
class VaultValidator:
    """Validate vault account correctness."""

    def __init__(self, rpc_client):
        self.rpc = rpc_client
        self.spl_token_program = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
        self.token2022_program = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"

    async def validate_vault_pair(self, base_vault: str, quote_vault: str,
                                   token_mint: str, quote_mint: str) -> VaultValidationResult:
        """Validate that vaults are correct."""
        errors = []

        # Fetch vault accounts
        base_info = await self.rpc.get_account_info(base_vault)
        quote_info = await self.rpc.get_account_info(quote_vault)

        if not base_info:
            errors.append(f"base_vault {base_vault} not found on chain")
        if not quote_info:
            errors.append(f"quote_vault {quote_vault} not found on chain")

        if errors:
            return VaultValidationResult(passed=False, errors=errors)

        # 1. Check owner: must be SPL Token or Token2022
        if base_info['owner'] not in [self.spl_token_program, self.token2022_program]:
            errors.append(
                f"base_vault owner {base_info['owner']} is neither SPL Token nor Token2022"
            )

        if quote_info['owner'] not in [self.spl_token_program, self.token2022_program]:
            errors.append(
                f"quote_vault owner {quote_info['owner']} is neither SPL Token nor Token2022"
            )

        # 2. Decode token account data
        base_decoded = self._decode_token_account(base_info['data'])
        quote_decoded = self._decode_token_account(quote_info['data'])

        if not base_decoded:
            errors.append(f"base_vault data invalid or too small")
        if not quote_decoded:
            errors.append(f"quote_vault data invalid or too small")

        if errors:
            return VaultValidationResult(passed=False, errors=errors)

        # 3. Verify mint associations
        if base_decoded['mint'] != token_mint:
            errors.append(
                f"base_vault mint {base_decoded['mint']} != token_mint {token_mint}"
            )

        if quote_decoded['mint'] != quote_mint:
            errors.append(
                f"quote_vault mint {quote_decoded['mint']} != quote_mint {quote_mint}"
            )

        # 4. Reject zero or placeholder addresses
        zero_address = "11111111111111111111111111111111"
        if base_vault == zero_address or quote_vault == zero_address:
            errors.append("Vault address is zero address")

        # 5. Check lamports (should have some rent exempt amount)
        min_rent = 2039280  # Typical rent-exempt for token account
        if base_info['lamports'] < min_rent:
            errors.append(
                f"base_vault lamports {base_info['lamports']} < minimum {min_rent}"
            )

        if quote_info['lamports'] < min_rent:
            errors.append(
                f"quote_vault lamports {quote_info['lamports']} < minimum {min_rent}"
            )

        return VaultValidationResult(
            passed=len(errors) == 0,
            errors=errors,
            base_mint=base_decoded.get('mint'),
            quote_mint=quote_decoded.get('mint'),
            base_amount=base_decoded.get('amount'),
            quote_amount=quote_decoded.get('amount'),
        )

    def _decode_token_account(self, data: bytes) -> Optional[dict]:
        """Decode SPL token account data."""
        if len(data) < 72:
            return None

        try:
            mint = data[0:32]
            owner = data[32:64]
            amount = int.from_bytes(data[64:72], 'little')

            return {
                'mint': bs58.encode(mint).decode(),
                'owner': bs58.encode(owner).decode(),
                'amount': amount,
            }
        except:
            return None
```

### 3.2 Batch Vault Validation SQL

```sql
-- Vaults that are zero addresses
SELECT mint, base_account, quote_account
FROM token_pool_accounts
WHERE base_account = '11111111111111111111111111111111'
   OR quote_account = '11111111111111111111111111111111';

-- Count vaults by validation status
SELECT vault_validation_status, COUNT(*) as count
FROM token_pool_accounts
GROUP BY vault_validation_status
ORDER BY count DESC;

-- Show unvalidated vaults with discovery source
SELECT mint, base_account, quote_account, discovery_method,
       vault_validation_status, created_at
FROM token_pool_accounts
WHERE vault_validation_status = 'pending'
ORDER BY created_at DESC
LIMIT 20;
```

---

## 4. Registration Validation

### 4.1 Database Row Verification

```python
class RegistrationValidator:
    """Validate pool registration in database."""

    @staticmethod
    def validate_registration(token_mint: str, db) -> RegistrationResult:
        """Check that registration row is complete and valid."""

        row = db.query_one("""
            SELECT
                mint, pool_address, base_account, quote_account,
                pool_program, discovery_method, vault_validation_status,
                pool_score, migration_tx, created_at
            FROM token_pool_accounts
            WHERE mint = ?
            ORDER BY created_at DESC
            LIMIT 1
        """, (token_mint,))

        if not row:
            return RegistrationResult(
                mint=token_mint,
                passed=False,
                errors=["No row in token_pool_accounts"]
            )

        required_fields = {
            'pool_address': ('not null', lambda x: x is not None and x != ''),
            'base_account': ('not null', lambda x: x is not None and x != ''),
            'quote_account': ('not null', lambda x: x is not None and x != ''),
            'pool_program': ('valid program',
                lambda x: x in [
                    'pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA',
                    '675kPX9MHTjS2zt1qrVrrVrZg1ankqqgoerEmJlwQ1K',
                    '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P',
                ]),
            'discovery_method': ('known method',
                lambda x: x in [
                    'tx_parsing', 'vault_inference',
                    'rpc_multipool_discovery', 'unknown'
                ]),
            'vault_validation_status': ('validated or pending',
                lambda x: x in ['validated', 'pending']),
        }

        errors = []
        for field, (desc, validator) in required_fields.items():
            value = row.get(field)
            if not validator(value):
                errors.append(f"{field}: {desc} (got {value})")

        # Pool score should be between 0.1 and 1.3
        pool_score = row.get('pool_score')
        if pool_score is None or not (0.0 <= pool_score <= 2.0):
            errors.append(f"pool_score out of range: {pool_score}")

        # migration_tx should reference the TX signature
        if not row.get('migration_tx'):
            errors.append("migration_tx is null")

        return RegistrationResult(
            mint=token_mint,
            pool_address=row['pool_address'],
            base_account=row['base_account'],
            quote_account=row['quote_account'],
            discovery_method=row['discovery_method'],
            vault_validation_status=row['vault_validation_status'],
            pool_score=pool_score,
            passed=len(errors) == 0,
            errors=errors,
            row=row
        )
```

### 4.2 Batch Registration Checks SQL

```sql
-- Show registration completeness
SELECT
    COUNT(*) as total,
    COUNT(pool_address) as has_pool_address,
    COUNT(base_account) as has_base_account,
    COUNT(quote_account) as has_quote_account,
    COUNT(pool_program) as has_pool_program,
    COUNT(discovery_method) as has_discovery_method,
    COUNT(vault_validation_status) as has_vault_status,
    ROUND(100.0 * COUNT(pool_address) / COUNT(*), 1) as pool_address_pct,
    ROUND(100.0 * COUNT(CASE WHEN discovery_method NOT IN ('unknown', NULL)
        THEN 1 END) / COUNT(*), 1) as known_discovery_pct
FROM token_pool_accounts;

-- Rows with missing required fields
SELECT mint, pool_address, base_account, quote_account,
       discovery_method, vault_validation_status
FROM token_pool_accounts
WHERE pool_address IS NULL
   OR base_account IS NULL
   OR quote_account IS NULL
   OR discovery_method IS NULL
   OR vault_validation_status IS NULL;

-- Distribution of pool programs
SELECT pool_program, COUNT(*) as count
FROM token_pool_accounts
GROUP BY pool_program
ORDER BY count DESC;

-- Distribution of discovery methods
SELECT discovery_method, COUNT(*) as count
FROM token_pool_accounts
GROUP BY discovery_method
ORDER BY count DESC;
```

---

## 5. Telemetry Validation

### 5.1 Telemetry Analysis

```python
class TelemetryAnalyzer:
    """Analyze resolution telemetry."""

    def __init__(self, db):
        self.db = db

    def analyze_resolution_performance(self) -> TelemetryReport:
        """Compute metrics from token_resolution_telemetry."""

        rows = self.db.query("""
            SELECT
                mint,
                detected_at,
                resolved_at,
                resolve_seconds,
                resolve_source,
                retry_count,
                pool_address
            FROM token_resolution_telemetry
            WHERE resolved_at IS NOT NULL
            ORDER BY resolved_at DESC
        """)

        if not rows:
            return TelemetryReport(
                total_resolved=0,
                errors=["No resolved tokens in telemetry"]
            )

        # Compute latency metrics
        resolve_times = [r['resolve_seconds'] for r in rows if r['resolve_seconds']]
        resolve_times.sort()

        median_ms = resolve_times[len(resolve_times) // 2] * 1000 if resolve_times else None
        p90_ms = resolve_times[int(len(resolve_times) * 0.9)] * 1000 if len(resolve_times) > 10 else None
        p99_ms = resolve_times[int(len(resolve_times) * 0.99)] * 1000 if len(resolve_times) > 100 else None

        # Distribution by resolve_source
        source_dist = {}
        for r in rows:
            src = r['resolve_source']
            source_dist[src] = source_dist.get(src, 0) + 1

        # Retry statistics
        retries_by_source = {}
        for r in rows:
            src = r['resolve_source']
            retries = r.get('retry_count', 0)
            if src not in retries_by_source:
                retries_by_source[src] = []
            retries_by_source[src].append(retries)

        avg_retries_by_source = {
            src: sum(retries) / len(retries)
            for src, retries in retries_by_source.items()
        }

        # Fast resolutions (< 2s)
        fast_count = len([t for t in resolve_times if t < 2.0])
        fast_pct = 100.0 * fast_count / len(resolve_times) if resolve_times else 0

        # Unresolved tokens
        unresolved = self.db.query_one("""
            SELECT COUNT(*) as count
            FROM token_resolution_telemetry
            WHERE resolved_at IS NULL
        """)

        return TelemetryReport(
            total_detected=self.db.query_one(
                "SELECT COUNT(*) as count FROM token_resolution_telemetry"
            )['count'],
            total_resolved=len(rows),
            total_unresolved=unresolved['count'],
            median_resolve_ms=median_ms,
            p90_resolve_ms=p90_ms,
            p99_resolve_ms=p99_ms,
            fast_resolutions_pct=fast_pct,
            resolve_source_distribution=source_dist,
            avg_retries_by_source=avg_retries_by_source,
            max_resolve_ms=max(resolve_times) * 1000 if resolve_times else None,
        )

    def get_unresolved_after_delay(self, delay_seconds: int) -> list:
        """Get tokens still unresolved after N seconds."""
        now = int(time.time())
        cutoff = now - delay_seconds

        return self.db.query("""
            SELECT mint, detected_at, retry_count, pool_address
            FROM token_resolution_telemetry
            WHERE resolved_at IS NULL
              AND detected_at < ?
            ORDER BY detected_at DESC
        """, (cutoff,))

    def get_slowest_resolutions(self, limit: int = 10) -> list:
        """Get slowest resolution times."""
        return self.db.query("""
            SELECT mint, resolve_seconds, resolve_source, retry_count
            FROM token_resolution_telemetry
            WHERE resolved_at IS NOT NULL
            ORDER BY resolve_seconds DESC
            LIMIT ?
        """, (limit,))

    def get_resolution_timeline(self) -> dict:
        """Breakdown of resolutions by hour."""
        return self.db.query("""
            SELECT
                strftime('%Y-%m-%d %H:00:00',
                    datetime(resolved_at, 'unixepoch')) as hour,
                COUNT(*) as count,
                COUNT(CASE WHEN resolve_source = 'tx_parsing' THEN 1 END) as tx_parsing,
                COUNT(CASE WHEN resolve_source = 'vault_inference' THEN 1 END) as vault_inference,
                COUNT(CASE WHEN resolve_source = 'rpc_multipool_discovery' THEN 1 END) as rpc_discovery,
                ROUND(AVG(resolve_seconds), 2) as avg_resolve_seconds
            FROM token_resolution_telemetry
            WHERE resolved_at IS NOT NULL
            GROUP BY hour
            ORDER BY hour DESC
            LIMIT 24
        """)
```

### 5.2 Telemetry SQL Queries

```sql
-- Resolution performance summary
SELECT
    COUNT(*) as total,
    COUNT(CASE WHEN resolved_at IS NOT NULL THEN 1 END) as resolved,
    COUNT(CASE WHEN resolved_at IS NULL THEN 1 END) as unresolved,
    ROUND(100.0 * COUNT(CASE WHEN resolved_at IS NOT NULL THEN 1 END)
        / COUNT(*), 1) as resolved_pct,
    ROUND(MIN(resolve_seconds), 2) as min_resolve_s,
    ROUND(AVG(resolve_seconds), 2) as avg_resolve_s,
    ROUND(MAX(resolve_seconds), 2) as max_resolve_s
FROM token_resolution_telemetry;

-- Resolve source distribution
SELECT
    resolve_source,
    COUNT(*) as count,
    ROUND(100.0 * COUNT(*) /
        (SELECT COUNT(*) FROM token_resolution_telemetry
         WHERE resolved_at IS NOT NULL), 1) as pct,
    ROUND(AVG(resolve_seconds), 2) as avg_seconds,
    ROUND(AVG(retry_count), 2) as avg_retries
FROM token_resolution_telemetry
WHERE resolved_at IS NOT NULL
GROUP BY resolve_source
ORDER BY count DESC;

-- Tokens unresolved for > 60 seconds
SELECT mint, detected_at, resolved_at, retry_count, pool_address,
       CAST((strftime('%s', 'now') - detected_at) as INTEGER) as seconds_elapsed
FROM token_resolution_telemetry
WHERE resolved_at IS NULL
  AND detected_at < strftime('%s', 'now') - 60
ORDER BY detected_at ASC;

-- Resolution latency percentiles
SELECT
    resolve_source,
    COUNT(*) as count,
    ROUND(MIN(resolve_seconds) * 1000, 0) as p0_ms,
    ROUND(AVG(CASE WHEN resolve_seconds <=
        (SELECT AVG(resolve_seconds) * 0.25
         FROM token_resolution_telemetry t2
         WHERE t2.resolve_source = t.resolve_source)
        THEN resolve_seconds END) * 1000, 0) as p25_ms,
    ROUND(AVG(CASE WHEN resolve_seconds <=
        (SELECT AVG(resolve_seconds) * 0.5
         FROM token_resolution_telemetry t2
         WHERE t2.resolve_source = t.resolve_source)
        THEN resolve_seconds END) * 1000, 0) as p50_ms,
    ROUND(AVG(CASE WHEN resolve_seconds <=
        (SELECT AVG(resolve_seconds) * 0.9
         FROM token_resolution_telemetry t2
         WHERE t2.resolve_source = t.resolve_source)
        THEN resolve_seconds END) * 1000, 0) as p90_ms,
    ROUND(MAX(resolve_seconds) * 1000, 0) as p100_ms
FROM token_resolution_telemetry t
WHERE resolved_at IS NOT NULL
GROUP BY resolve_source;

-- Retry distribution
SELECT
    retry_count,
    COUNT(*) as count,
    ROUND(100.0 * COUNT(*) /
        (SELECT COUNT(*) FROM token_resolution_telemetry
         WHERE resolved_at IS NOT NULL), 1) as pct
FROM token_resolution_telemetry
WHERE resolved_at IS NOT NULL
GROUP BY retry_count
ORDER BY retry_count ASC;
```

---

## 6. WebSocket + Pricing Validation

### 6.1 Full Pipeline Validation

```python
class WebSocketPricingValidator:
    """Validate WebSocket subscriptions and price snapshots."""

    def __init__(self, db_path, ws_state_store):
        self.db_path = db_path
        self.ws_store = ws_state_store  # PoolStateStore singleton
        self.db = Database(db_path)

    async def validate_pool_pipeline(self, token_mint: str,
                                      timeout_seconds: int = 10) -> PipelineValidationResult:
        """Validate full pipeline: subscription → update → snapshot."""

        start_time = time.time()
        errors = []

        # 1. Fetch registration
        pool = self.db.query_one("""
            SELECT mint, base_account, quote_account, pool_program
            FROM token_pool_accounts
            WHERE mint = ?
            ORDER BY created_at DESC
            LIMIT 1
        """, (token_mint,))

        if not pool:
            return PipelineValidationResult(
                mint=token_mint,
                passed=False,
                errors=["Pool not registered"]
            )

        # 2. Check WebSocket subscription
        base_account = pool['base_account']
        quote_account = pool['quote_account']

        # Wait for WebSocket updates
        ws_ready = False
        base_reserves = None
        quote_reserves = None

        for attempt in range(timeout_seconds * 10):  # Poll 10x per second
            base_reserves = self.ws_store.get_reserves(token_mint, base_account)
            quote_reserves = self.ws_store.get_reserves(token_mint, quote_account)

            if base_reserves and quote_reserves:
                ws_ready = True
                break

            await asyncio.sleep(0.1)

        if not ws_ready:
            errors.append(
                f"WebSocket: no reserve updates after {timeout_seconds}s"
            )
        else:
            elapsed_ms = int((time.time() - start_time) * 1000)
            if elapsed_ms > 5000:
                errors.append(
                    f"WebSocket: took {elapsed_ms}ms to receive updates (goal < 5s)"
                )

        # 3. Check price snapshot
        snapshot = self.db.query_one("""
            SELECT price_usd, source, created_at
            FROM token_price_snapshots
            WHERE mint = ? AND source = 'pool'
            ORDER BY created_at DESC
            LIMIT 1
        """, (token_mint,))

        if not snapshot:
            errors.append("Price snapshot: not found in token_price_snapshots")
        elif snapshot['source'] != 'pool':
            errors.append(f"Price snapshot: source is '{snapshot['source']}', expected 'pool'")
        elif not snapshot['price_usd'] or snapshot['price_usd'] <= 0:
            errors.append(f"Price snapshot: price_usd = {snapshot['price_usd']} (invalid)")

        if snapshot and snapshot['created_at']:
            snapshot_age_ms = int((time.time() - snapshot['created_at']) * 1000)
            if snapshot_age_ms > 10000:
                errors.append(
                    f"Price snapshot: stale ({snapshot_age_ms}ms old)"
                )

        return PipelineValidationResult(
            mint=token_mint,
            base_account=base_account,
            quote_account=quote_account,
            ws_ready=ws_ready,
            base_reserves=base_reserves,
            quote_reserves=quote_reserves,
            snapshot_source=snapshot.get('source') if snapshot else None,
            snapshot_price_usd=snapshot.get('price_usd') if snapshot else None,
            passed=len(errors) == 0,
            errors=errors,
            total_elapsed_ms=int((time.time() - start_time) * 1000)
        )

    async def validate_batch_pools(self, token_mints: List[str]) -> BatchValidationReport:
        """Validate multiple pools."""
        results = []
        for mint in token_mints:
            result = await self.validate_pool_pipeline(mint)
            results.append(result)

        passed = [r for r in results if r.passed]
        failed = [r for r in results if not r.passed]

        return BatchValidationReport(
            total=len(results),
            passed=len(passed),
            failed=len(failed),
            pass_rate_pct=100.0 * len(passed) / len(results),
            results=results,
            failed_details=[
                {
                    'mint': r.mint,
                    'errors': r.errors,
                }
                for r in failed
            ]
        )
```

### 6.2 WebSocket + Pricing SQL Queries

```sql
-- Price snapshot coverage
SELECT
    COUNT(DISTINCT tpa.mint) as total_pools,
    COUNT(DISTINCT tps.mint) as with_snapshots,
    COUNT(DISTINCT CASE WHEN tps.source = 'pool' THEN tps.mint END) as with_pool_source,
    ROUND(100.0 *
        COUNT(DISTINCT CASE WHEN tps.source = 'pool' THEN tps.mint END) /
        COUNT(DISTINCT tpa.mint), 1) as pool_source_pct
FROM token_pool_accounts tpa
LEFT JOIN token_price_snapshots tps USING (mint);

-- Latest price snapshot per pool
SELECT
    tpa.mint,
    tpa.base_account,
    tps.price_usd,
    tps.source,
    tps.created_at,
    CAST((strftime('%s', 'now') - tps.created_at) as INTEGER) as age_seconds
FROM token_pool_accounts tpa
LEFT JOIN token_price_snapshots tps ON (
    tpa.mint = tps.mint
    AND tps.created_at = (
        SELECT MAX(created_at) FROM token_price_snapshots t2 WHERE t2.mint = tpa.mint
    )
)
ORDER BY tps.created_at DESC NULLS LAST
LIMIT 20;

-- Pools without recent snapshots (> 60s old)
SELECT
    tpa.mint,
    MAX(tps.created_at) as last_snapshot,
    CAST((strftime('%s', 'now') - MAX(tps.created_at)) as INTEGER) as age_seconds,
    COUNT(tps.id) as snapshot_count
FROM token_pool_accounts tpa
LEFT JOIN token_price_snapshots tps ON tpa.mint = tps.mint
GROUP BY tpa.mint
HAVING age_seconds > 60
   OR COUNT(tps.id) = 0
ORDER BY age_seconds DESC;

-- Price snapshot sources breakdown
SELECT
    source,
    COUNT(DISTINCT mint) as pool_count,
    COUNT(*) as total_snapshots,
    ROUND(AVG(price_usd), 8) as avg_price_usd,
    ROUND(MIN(price_usd), 8) as min_price_usd,
    ROUND(MAX(price_usd), 8) as max_price_usd
FROM token_price_snapshots
WHERE created_at > strftime('%s', 'now') - 3600  -- Last hour
GROUP BY source
ORDER BY pool_count DESC;
```

---

## 7. Metrics Dashboard

### 7.1 Key Metrics to Track

| Metric | Query | Threshold | Alert |
|--------|-------|-----------|-------|
| **Resolution Rate** | resolved / detected | ≥ 95% | < 90% |
| **Median Resolve Time** | p50 of resolve_seconds | ≤ 5s | > 10s |
| **p90 Resolve Time** | p90 of resolve_seconds | ≤ 10s | > 20s |
| **TX Parsing %** | resolve_source='tx_parsing' / total | ≥ 80% | < 70% |
| **Vault Validation Rate** | validated / total | ≥ 95% | < 90% |
| **WebSocket Uptime** | pools with recent snapshots | ≥ 90% | < 80% |
| **Snapshot Freshness** | max age of latest snapshot | ≤ 10s | > 30s |
| **Unresolved After 60s** | count where detected > 60s ago | = 0 | > 5 |
| **Pool Count** | count(distinct mint) | trending up | stable for 1h |
| **Average Pool Score** | avg(pool_score) | ≥ 0.8 | < 0.6 |

### 7.2 SQL for Dashboard Metrics

```sql
-- Unified metrics snapshot
WITH resolution_stats AS (
    SELECT
        COUNT(*) as total_detected,
        COUNT(CASE WHEN resolved_at IS NOT NULL THEN 1 END) as total_resolved,
        ROUND(100.0 * COUNT(CASE WHEN resolved_at IS NOT NULL THEN 1 END) /
            NULLIF(COUNT(*), 0), 1) as resolution_rate_pct
    FROM token_resolution_telemetry
),
latency_stats AS (
    SELECT
        ROUND(AVG(CASE
            WHEN resolve_seconds <= 5 THEN resolve_seconds END) * 1000, 0) as median_ms,
        ROUND(MAX(CASE
            WHEN resolve_seconds <=
            (SELECT AVG(resolve_seconds) * 0.9 FROM token_resolution_telemetry
             WHERE resolved_at IS NOT NULL)
            THEN resolve_seconds END) * 1000, 0) as p90_ms
    FROM token_resolution_telemetry
    WHERE resolved_at IS NOT NULL
),
source_dist AS (
    SELECT
        ROUND(100.0 * SUM(CASE WHEN resolve_source = 'tx_parsing' THEN 1 END) /
            NULLIF(COUNT(*), 0), 1) as tx_parsing_pct
    FROM token_resolution_telemetry
    WHERE resolved_at IS NOT NULL
),
vault_stats AS (
    SELECT
        COUNT(*) as total_pools,
        COUNT(CASE WHEN vault_validation_status = 'validated' THEN 1 END) as validated,
        ROUND(100.0 * COUNT(CASE WHEN vault_validation_status = 'validated' THEN 1 END) /
            COUNT(*), 1) as validation_rate_pct
    FROM token_pool_accounts
),
snapshot_stats AS (
    SELECT
        COUNT(DISTINCT mint) as pools_with_snapshots,
        COUNT(DISTINCT CASE WHEN created_at >
            strftime('%s', 'now') - 10 THEN mint END) as fresh_snapshots_10s,
        ROUND(100.0 * COUNT(DISTINCT CASE WHEN created_at >
            strftime('%s', 'now') - 10 THEN mint END) /
            NULLIF(COUNT(DISTINCT mint), 0), 1) as freshness_pct
    FROM token_price_snapshots
    WHERE source = 'pool'
)
SELECT
    (SELECT * FROM resolution_stats) as resolution,
    (SELECT * FROM latency_stats) as latency,
    (SELECT * FROM source_dist) as source,
    (SELECT * FROM vault_stats) as vault,
    (SELECT * FROM snapshot_stats) as snapshot;
```

---

## 8. Production Readiness Criteria

### 8.1 Pass/Fail Thresholds

| Criterion | Condition | Pass |
|-----------|-----------|------|
| **Historical Good** | 10 signatures replay end-to-end | ≥ 9/10 pass |
| **Previously Failing** | 5 failing signatures now resolve | ≥ 4/5 pass |
| **Fresh Live** | 5 live migrations | ≥ 4/5 produce snapshots within 10s |
| **Discovery Validation** | All registered pools pass validation | 0 violations |
| **Vault Validation** | All vault pairs validate | 0 invalid vaults |
| **Registration Completeness** | Required fields populated | ≥ 99% |
| **Telemetry** | Telemetry records written | ≥ 95% of resolved tokens |
| **Resolution Rate** | Resolved / detected | ≥ 95% |
| **TX Parsing Success** | resolve_source='tx_parsing' / total | ≥ 80% |
| **Latency (p90)** | p90 resolve_seconds | ≤ 10 seconds |
| **Latency (p99)** | p99 resolve_seconds | ≤ 30 seconds |
| **WebSocket Coverage** | Pools with recent snapshots | ≥ 90% |
| **Snapshot Freshness** | Latest snapshot age | ≤ 10 seconds |
| **Unresolved Tokens** | Tokens unresolved > 60s | = 0 |
| **Pool Scores** | Average pool_score | ≥ 0.8 |
| **Vault Validation Rate** | vault_validation_status='validated' | ≥ 95% |

### 8.2 Rollout Stages

**Stage 1: Controlled Test (Current)**
- Run replay test harness with 20 signatures total
- All criteria must pass
- Monitor for 1 hour minimum

**Stage 2: Beta (Next 6 hours)**
- Deploy to beta channel
- Monitor live migrations (target: 50+ tokens)
- Track metrics continuously
- Alert on any criterion dropping below 90% of threshold

**Stage 3: Gradual Rollout (12–24 hours)**
- 25% traffic: Production instances with metrics monitoring
- 50% traffic: Confirm metrics remain stable
- 100% traffic: Full production deployment

**Stage 4: Monitoring (Ongoing)**
- Dashboard alerts on metric drops
- Weekly telemetry reports
- Monthly architecture review

---

## 9. Operational Dashboards

### 9.1 Real-Time Dashboard Queries

```bash
#!/bin/bash
# Real-time monitoring dashboard

while true; do
    clear
    echo "=== PumpSwap Discovery Pipeline — Real-Time Metrics ==="
    echo ""

    # Resolution rate
    sqlite3 database/flex_complete_database.db << 'SQL'
.header off
.mode line
SELECT
    'Resolution Rate' as metric,
    ROUND(100.0 *
        COUNT(CASE WHEN resolved_at IS NOT NULL THEN 1 END) /
        NULLIF(COUNT(*), 0), 1) || '%' as value
FROM token_resolution_telemetry;
SQL

    echo ""

    # Top resolve sources (last 100 resolutions)
    sqlite3 database/flex_complete_database.db << 'SQL'
SELECT
    'Resolve Source Distribution:' as label;
SELECT
    '  ' || resolve_source as source,
    COUNT(*) as count
FROM token_resolution_telemetry
WHERE resolved_at IS NOT NULL
ORDER BY count DESC
LIMIT 5;
SQL

    echo ""

    # Latency
    sqlite3 database/flex_complete_database.db << 'SQL'
SELECT
    'Median Resolve:' as label,
    ROUND(AVG(resolve_seconds), 2) || 's' as value
FROM token_resolution_telemetry
WHERE resolved_at IS NOT NULL
LIMIT 1;
SQL

    echo ""

    # Vault validation
    sqlite3 database/flex_complete_database.db << 'SQL'
SELECT
    'Vault Validation:' as label,
    COUNT(CASE WHEN vault_validation_status = 'validated' THEN 1 END) || '/' || COUNT(*) as ratio
FROM token_pool_accounts;
SQL

    echo ""

    # Unresolved
    sqlite3 database/flex_complete_database.db << 'SQL'
SELECT
    'Unresolved (> 60s):' as label,
    COUNT(*) as count
FROM token_resolution_telemetry
WHERE resolved_at IS NULL
  AND detected_at < strftime('%s', 'now') - 60;
SQL

    echo ""
    echo "Last updated: $(date)"
    sleep 30
done
```

### 9.2 Weekly Report Generator

```python
#!/usr/bin/env python3

import sqlite3
import json
from datetime import datetime, timedelta

def generate_weekly_report(db_path: str) -> dict:
    """Generate comprehensive weekly telemetry report."""

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    week_ago = int((datetime.now() - timedelta(days=7)).timestamp())

    # Resolution stats
    cursor.execute("""
        SELECT
            COUNT(*) as total_detected,
            COUNT(CASE WHEN resolved_at IS NOT NULL THEN 1 END) as resolved,
            COUNT(CASE WHEN resolved_at IS NULL THEN 1 END) as unresolved,
            ROUND(100.0 * COUNT(CASE WHEN resolved_at IS NOT NULL THEN 1 END) /
                COUNT(*), 1) as resolution_rate_pct,
            ROUND(AVG(resolve_seconds), 2) as avg_resolve_seconds,
            ROUND(MAX(resolve_seconds), 2) as max_resolve_seconds
        FROM token_resolution_telemetry
        WHERE created_at > ?
    """, (week_ago,))
    resolution_stats = dict(cursor.fetchone())

    # Source distribution
    cursor.execute("""
        SELECT resolve_source, COUNT(*) as count
        FROM token_resolution_telemetry
        WHERE resolved_at IS NOT NULL AND created_at > ?
        GROUP BY resolve_source
        ORDER BY count DESC
    """, (week_ago,))
    source_dist = {row['resolve_source']: row['count'] for row in cursor.fetchall()}

    # Vault stats
    cursor.execute("""
        SELECT
            COUNT(*) as total_pools,
            COUNT(CASE WHEN vault_validation_status = 'validated' THEN 1 END) as validated,
            ROUND(100.0 * COUNT(CASE WHEN vault_validation_status = 'validated' THEN 1 END) /
                COUNT(*), 1) as validation_rate_pct
        FROM token_pool_accounts
        WHERE created_at > ?
    """, (week_ago,))
    vault_stats = dict(cursor.fetchone())

    # Price snapshot coverage
    cursor.execute("""
        SELECT
            COUNT(DISTINCT mint) as pools_with_snapshots,
            COUNT(CASE WHEN source = 'pool' THEN 1 END) as pool_source_count,
            ROUND(100.0 * COUNT(CASE WHEN source = 'pool' THEN 1 END) /
                NULLIF(COUNT(*), 0), 1) as pool_source_pct
        FROM token_price_snapshots
        WHERE created_at > ?
    """, (week_ago,))
    snapshot_stats = dict(cursor.fetchone())

    conn.close()

    return {
        'period': {
            'start': (datetime.now() - timedelta(days=7)).isoformat(),
            'end': datetime.now().isoformat(),
        },
        'resolution': resolution_stats,
        'source_distribution': source_dist,
        'vault': vault_stats,
        'snapshots': snapshot_stats,
        'recommendations': _generate_recommendations(resolution_stats, vault_stats)
    }

def _generate_recommendations(resolution_stats: dict, vault_stats: dict) -> list:
    """Generate actionable recommendations."""
    recommendations = []

    if resolution_stats['resolution_rate_pct'] < 95:
        recommendations.append(
            f"⚠️  Resolution rate {resolution_stats['resolution_rate_pct']}% < 95% target. "
            f"Investigate {resolution_stats['unresolved']} unresolved tokens."
        )

    if resolution_stats['max_resolve_seconds'] > 30:
        recommendations.append(
            f"⚠️  Max resolve time {resolution_stats['max_resolve_seconds']}s > 30s target. "
            f"Check RPC latency and retry logic."
        )

    if vault_stats['validation_rate_pct'] < 95:
        recommendations.append(
            f"⚠️  Vault validation rate {vault_stats['validation_rate_pct']}% < 95% target. "
            f"Review {vault_stats['total_pools'] - vault_stats['validated']} pending vaults."
        )

    if not recommendations:
        recommendations.append("✅ All metrics within target thresholds.")

    return recommendations

if __name__ == "__main__":
    report = generate_weekly_report("database/flex_complete_database.db")
    print(json.dumps(report, indent=2))
```

---

## 10. Pseudocode Examples

### 10.1 Complete Replay Test Example

```python
async def run_full_replay_test():
    """Full end-to-end replay test."""

    db = Database("database/flex_complete_database.db")
    rpc = RpcClient(os.getenv("HELIUS_RPC_URL"))
    harness = ReplayTestHarness(db, rpc)

    # Group 1: Historical good
    print("\n=== GROUP 1: Historical Good Signatures ===\n")
    good_sigs = [
        "4P...abc",  # Known successful migration
        "5Q...def",  # Another successful migration
        # ... 10 total
    ]

    good_results = []
    for sig in good_sigs:
        result = harness.replay_migration(sig, "historical_good")
        good_results.append(result)
        print(f"{'✓' if result.status == 'PASSED' else '✗'} {sig[:16]}... "
              f"({result.duration_ms}ms, {result.discovery_method})")

    good_pass_rate = len([r for r in good_results if r.status == 'PASSED']) / len(good_results)
    print(f"\nGroup 1 Pass Rate: {good_pass_rate * 100:.1f}%")

    # Group 2: Previously failing
    print("\n=== GROUP 2: Previously Failing Signatures ===\n")
    failing_sigs = [
        "6R...ghi",  # MOG migration (offset layout bug)
        "7S...jkl",  # Another failing migration
        # ... 5 total
    ]

    failing_results = []
    for sig in failing_sigs:
        result = harness.replay_migration(sig, "previously_failing")
        failing_results.append(result)
        print(f"{'✓' if result.status == 'PASSED' else '✗'} {sig[:16]}... "
              f"({result.duration_ms}ms, {result.discovery_method})")

    failing_pass_rate = len([r for r in failing_results if r.status == 'PASSED']) / len(failing_results)
    print(f"\nGroup 2 Pass Rate: {failing_pass_rate * 100:.1f}%")

    # Group 3: Fresh live
    print("\n=== GROUP 3: Fresh Live Migrations ===\n")
    live_sigs = harness._get_fresh_live_signatures(5)

    live_results = []
    for sig in live_sigs:
        result = harness.replay_migration(sig, "live")
        live_results.append(result)
        print(f"{'✓' if result.status == 'PASSED' else '✗'} {sig[:16]}... "
              f"({result.duration_ms}ms, {result.discovery_method})")

    live_pass_rate = len([r for r in live_results if r.status == 'PASSED']) / len(live_results)
    print(f"\nGroup 3 Pass Rate: {live_pass_rate * 100:.1f}%")

    # Final verdict
    print("\n=== PRODUCTION READINESS VERDICT ===\n")

    all_pass = (
        good_pass_rate >= 0.9 and
        failing_pass_rate >= 0.8 and
        live_pass_rate >= 0.8
    )

    if all_pass:
        print("✅ PRODUCTION READY")
        print(f"  Historical good: {good_pass_rate * 100:.1f}%")
        print(f"  Previously failing: {failing_pass_rate * 100:.1f}%")
        print(f"  Fresh live: {live_pass_rate * 100:.1f}%")
    else:
        print("❌ PRODUCTION NOT READY")
        if good_pass_rate < 0.9:
            print(f"  ❌ Historical good: {good_pass_rate * 100:.1f}% (need ≥ 90%)")
        if failing_pass_rate < 0.8:
            print(f"  ❌ Previously failing: {failing_pass_rate * 100:.1f}% (need ≥ 80%)")
        if live_pass_rate < 0.8:
            print(f"  ❌ Fresh live: {live_pass_rate * 100:.1f}% (need ≥ 80%)")
```

---

## Summary

This production validation strategy provides:

1. **Replay Test Harness** — Deterministic replay of 20+ signatures across 3 groups
2. **Discovery Assertions** — 7 invariants that must hold for every pool
3. **Vault Validation** — Chain-based verification of vault ownership and mints
4. **Registration Checks** — Database schema completeness (all required fields)
5. **Telemetry Analysis** — Performance metrics (p50, p90, p99, source distribution)
6. **WebSocket Validation** — End-to-end subscription → snapshot pipeline
7. **Dashboard Metrics** — Real-time monitoring of 10 key indicators
8. **Readiness Criteria** — Clear pass/fail thresholds for 17 dimensions
9. **Operational Tools** — Dashboards, reports, and scripts for production monitoring

**Ready to proceed with validation run?**
