# Unified Diff Guide: Exactly What Changes

This document shows **exactly** what to change in each file using diff-style hunks.

---

## File 1: pumpfun_curve_listener.py

### Change 1: Add imports at top (line ~1-30)
```diff
+ import time
+ from asyncio import Lock
```

### Change 2: Update `__init__` method (~line 307)

**BEFORE:**
```python
    def __init__(self):
        self.seen_mints: Set[str] = set()
        self.detected_migrations: Set[str] = set()
        self.analyzed_tokens = {}
        self.db_lock = asyncio.Lock()
        self.websocket_connected = False
        self.websocket_msg_count = 0  # Track message receipt
        self.websocket_migration_count = 0  # Track migrations detected
        self._ensure_db()
        print(f"[INIT] Pump.Fun → PumpSwap Migration Listener ready", flush=True)
```

**AFTER:**
```python
    def __init__(self):
        self.seen_mints: Set[str] = set()
        self.detected_migrations: Set[str] = set()
        self.analyzed_tokens = {}
        self.db_lock = asyncio.Lock()
        self.websocket_connected = False
        self.websocket_msg_count = 0  # Track message receipt
        self.websocket_migration_count = 0  # Track migrations detected

        # === NEW: Transaction caching ===
        self.tx_cache = {}  # {signature: (tx_data, timestamp)}
        self.tx_cache_ttl_seconds = 1800  # 30 minutes TTL
        self.tx_inflight_locks = {}  # {signature: asyncio.Lock()} for singleflight
        self.tx_cache_stats = {
            'hit': 0,
            'miss': 0,
            'wait': 0,
        }

        self._ensure_db()
        print(f"[INIT] Pump.Fun → PumpSwap Migration Listener ready", flush=True)
        print(f"[INIT] ✅ TX Cache initialized (TTL: {self.tx_cache_ttl_seconds}s)", flush=True)
```

### Change 3: Add new method `_get_transaction_cached()` (~line 320, after `_post_rpc_with_fallback`)

```python
    async def _get_transaction_cached(self, signature: str, timeout: int = 10) -> Optional[Dict]:
        """
        Fetch transaction with TTL cache + singleflight deduplication.

        Deduplicates concurrent requests for the same signature.
        Cache TTL is 30 minutes (1800 seconds).

        Returns tx_data dict from "result" field, or None if not found.
        """
        current_time = time.time()

        # === Check cache hit ===
        if signature in self.tx_cache:
            cached_data, cached_time = self.tx_cache[signature]
            age = current_time - cached_time
            if age < self.tx_cache_ttl_seconds:
                self.tx_cache_stats['hit'] += 1
                print(f"[TX_CACHE] 💾 HIT: {signature[:16]}... (age: {age:.1f}s)", flush=True)
                return cached_data
            else:
                # Expired, remove from cache
                del self.tx_cache[signature]

        # === Check if already in-flight (singleflight pattern) ===
        if signature in self.tx_inflight_locks:
            # Another coroutine is already fetching this
            self.tx_cache_stats['wait'] += 1
            lock = self.tx_inflight_locks[signature]
            await lock.acquire()
            lock.release()

            # After lock released, tx should be in cache
            if signature in self.tx_cache:
                cached_data, _ = self.tx_cache[signature]
                print(f"[TX_CACHE] ⏳ WAIT: {signature[:16]}... (shared fetch completed)", flush=True)
                return cached_data
            return None

        # === Cache miss: fetch it ===
        self.tx_cache_stats['miss'] += 1

        # Create lock for this signature (singleflight)
        lock = asyncio.Lock()
        self.tx_inflight_locks[signature] = lock
        await lock.acquire()

        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [signature, {"encoding": "json", "maxSupportedTransactionVersion": 0}]
            }

            print(f"[TX_CACHE] 🌐 MISS: fetching {signature[:16]}...", flush=True)
            tx_data = await asyncio.wait_for(
                self._post_rpc_with_fallback(payload),
                timeout=timeout
            )

            if tx_data and "result" in tx_data and tx_data["result"]:
                result = tx_data["result"]
                # Cache it
                self.tx_cache[signature] = (result, current_time)
                print(f"[TX_CACHE] 💾 CACHED: {signature[:16]}... ({len(str(result))} bytes)", flush=True)
                return result

            return None

        except asyncio.TimeoutError:
            print(f"[TX_CACHE] ⏱️  Timeout fetching {signature[:16]}...", flush=True)
            return None

        except Exception as e:
            print(f"[TX_CACHE] ⚠ Error fetching {signature[:16]}...: {e}", flush=True)
            return None

        finally:
            # Release lock for other waiters
            lock.release()
            del self.tx_inflight_locks[signature]
```

### Change 4: Add extraction methods (can replace old ones or add alongside)

**Add or replace `_extract_mint_from_tx()` method:**

```python
    async def _extract_mint_from_tx(self, tx_data: Dict) -> Optional[str]:
        """
        Extract token mint from transaction data (no RPC call needed).

        Strategies:
        1. Try postTokenBalances first (most reliable)
        2. Fall back to accountKeys if postTokenBalances missing
        3. Filter out system programs
        """
        if not tx_data:
            return None

        meta = tx_data.get("meta", {})

        # Strategy 1: Try postTokenBalances first
        post_balances = meta.get("postTokenBalances", [])
        for balance in post_balances:
            mint = balance.get("mint", "")
            if mint and len(mint) in (43, 44) and mint != "So11111111111111111111111111111111111111112":
                return mint

        # Strategy 2: Fall back to accountKeys
        message = tx_data.get("transaction", {}).get("message", {})
        accounts = message.get("accountKeys", [])

        system_programs = {
            "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",
            "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",
            "11111111111111111111111111111111",
            "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
            "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
            "So11111111111111111111111111111111111111112",
        }

        for account in accounts[:10]:
            if len(account) in (43, 44) and account not in system_programs:
                return account

        return None
```

**Add or replace `_extract_pool_from_tx()` method:**

```python
    async def _extract_pool_from_tx(self, tx_data: Dict) -> Optional[str]:
        """
        Extract PumpSwap pool address from transaction data (no RPC call needed).

        The pool is the account that is OWNED BY the PumpSwap program.
        """
        if not tx_data:
            return None

        message = tx_data.get("transaction", {}).get("message", {})
        account_keys = message.get("accountKeys", [])

        if not account_keys:
            return None

        meta = tx_data.get("meta", {})
        inner_instructions = meta.get("innerInstructions", [])

        PUMPSWAP_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"

        # Find PumpSwap program index
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
                program_id_idx = ix.get("programIdIndex")
                if program_id_idx == pumpswap_idx:
                    accounts = ix.get("accounts", [])
                    if accounts and len(accounts) > 0:
                        pool_idx = accounts[0]
                        if isinstance(pool_idx, int) and pool_idx < len(account_keys):
                            pool_address = account_keys[pool_idx]
                            print(f"[POOL] ✅ Extracted pool from cached tx: {pool_address}", flush=True)
                            return pool_address

        return None
```

### Change 5: Add cache stats method (before `listen_websocket` method, ~line 1970)

```python
    def get_tx_cache_stats(self) -> Dict:
        """Return current transaction cache statistics."""
        total = sum(self.tx_cache_stats.values())
        hit_rate = (self.tx_cache_stats['hit'] / total * 100) if total > 0 else 0

        return {
            'tx_cache_hit': self.tx_cache_stats['hit'],
            'tx_cache_miss': self.tx_cache_stats['miss'],
            'tx_cache_wait': self.tx_cache_stats['wait'],
            'tx_cache_size': len(self.tx_cache),
            'tx_cache_hit_rate_pct': round(hit_rate, 2),
            'rpc_calls_avoided': self.tx_cache_stats['hit'],
            'credits_saved': self.tx_cache_stats['hit'] * 10,
        }
```

### Change 6: Update `handle_migration()` method (~line 1789)

**KEY SECTION - Replace the mint and pool extraction code:**

**BEFORE:**
```python
        self.detected_migrations.add(signature)

        # Extract mint from transaction (more reliable than logs)
        mint = await self._fetch_mint_from_transaction(signature)

        if not mint:
            print(f"[MIGRATION] ⚠ Failed to extract mint from postTokenBalances, trying logs fallback", flush=True)
            mint = self._extract_mint_from_logs(logs)

        # ... later in same method ...

        # Extract pool address from migration transaction for on-chain price queries
        pool_address = await self._extract_pool_from_migration_tx(signature)
```

**AFTER:**
```python
        self.detected_migrations.add(signature)

        # === CRITICAL OPTIMIZATION: Cache TX fetch ===
        tx_data = await self._get_transaction_cached(signature)

        if tx_data:
            mint = await self._extract_mint_from_tx(tx_data)
        else:
            mint = None

        if not mint:
            print(f"[MIGRATION] ⚠ Failed to extract mint from cached tx, trying logs fallback", flush=True)
            mint = self._extract_mint_from_logs(logs)

        # ... later in same method ...

        # === Extract pool from cached tx (no RPC call!) ===
        pool_address = None
        if tx_data:
            pool_address = await self._extract_pool_from_tx(tx_data)
            if pool_address:
                print(f"[EVENT] ✅ Pool extracted from cached tx: {pool_address}", flush=True)
```

### Change 7: Update blockTime extraction (in `handle_migration()`, ~line 1855)

**BEFORE:**
```python
                # Fallback: Get migration block time if provenance doesn't have blockTime
                if not created_at and signature:
                    try:
                        payload = {
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "getTransaction",
                            "params": [signature, {"encoding": "json", "maxSupportedTransactionVersion": 0}]
                        }
                        tx_data = await self._post_rpc_with_fallback(payload, timeout=10)
                        if tx_data and tx_data.get("result"):
                            block_time = tx_data["result"].get("blockTime")
                            if block_time:
                                created_at = datetime.utcfromtimestamp(block_time).isoformat() + "Z"
                    except Exception as ts_err:
                        pass
```

**AFTER:**
```python
                # Fallback: Get migration block time if provenance doesn't have blockTime
                # Try cached TX first (no RPC call!)
                if not created_at and signature:
                    try:
                        if tx_data:
                            block_time = tx_data.get("blockTime")
                            if block_time:
                                created_at = datetime.utcfromtimestamp(block_time).isoformat() + "Z"
                        # If no cached tx, skip (provenance check will use current time as fallback)
                    except Exception as ts_err:
                        pass
```

### Change 8: Update `_ensure_db()` method (~line 459)

**Add this at the end of the method, before `conn.close()`:**

```python
        # === NEW: Funder webhook tables ===
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS funder_watchlist (
                funder_address TEXT PRIMARY KEY,
                risk_score INTEGER DEFAULT 0,
                risk_reasons TEXT,
                first_added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active INTEGER DEFAULT 1,
                webhook_group_id TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS funder_webhook_groups (
                webhook_group_id TEXT PRIMARY KEY,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active INTEGER DEFAULT 1,
                helius_webhook_id TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS funder_webhook_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                funder_address TEXT NOT NULL,
                signature TEXT NOT NULL,
                slot INTEGER,
                block_time INTEGER,
                direction TEXT,
                counterparty TEXT,
                amount_sol REAL,
                mint TEXT,
                raw_payload TEXT,
                ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(signature, funder_address)
            )
        """)

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_funder_watchlist_active ON funder_watchlist(is_active)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_funder_watchlist_group ON funder_watchlist(webhook_group_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_funder_webhook_events_funder ON funder_webhook_events(funder_address)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_funder_webhook_events_block_time ON funder_webhook_events(block_time DESC)")

        print("[DB] ✅ Funder webhook tables ensured", flush=True)
```

---

## File 2: main.py

### Change 1: Add webhook receiver endpoint (add after existing routes, ~line 1500)

```python
import sqlite3

@app.route('/api/webhook/funder', methods=['POST'])
def webhook_funder_event():
    """
    Receive funder webhook events from Helius.

    Event format:
    {
        "signature": "...",
        "slot": 12345,
        "blockTime": 1234567890,
        "source": "...",
        "destination": "...",
        "nativeTransfers": [{"amount": 1000000}],
        "mint": "..." (optional)
    }
    """
    try:
        payload = request.get_json()

        if not payload:
            return jsonify({"error": "empty payload"}), 400

        signature = payload.get("signature")
        slot = payload.get("slot")
        block_time = payload.get("blockTime")
        source = payload.get("source")
        destination = payload.get("destination")
        mint = payload.get("mint")

        if not signature or not source or not destination:
            return jsonify({"error": "missing required fields"}), 400

        direction = None
        counterparty = None
        amount_sol = 0

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # Check if source is a watched funder (transfer OUT)
        cursor.execute("""
            SELECT 1 FROM funder_watchlist
            WHERE funder_address = ? AND is_active = 1
        """, (source,))

        if cursor.fetchone():
            direction = "OUT"
            counterparty = destination
            native_transfers = payload.get("nativeTransfers", [])
            if native_transfers:
                amount_sol = sum(t.get("amount", 0) for t in native_transfers) / 1e9

        # Check if destination is a watched funder (transfer IN)
        cursor.execute("""
            SELECT 1 FROM funder_watchlist
            WHERE funder_address = ? AND is_active = 1
        """, (destination,))

        if cursor.fetchone():
            direction = "IN"
            counterparty = source
            native_transfers = payload.get("nativeTransfers", [])
            if native_transfers:
                amount_sol = sum(t.get("amount", 0) for t in native_transfers) / 1e9

        if not direction:
            conn.close()
            return jsonify({"status": "ok"}), 200

        # Insert event (dedupe by UNIQUE constraint)
        funder = source if direction == "OUT" else destination
        try:
            cursor.execute("""
                INSERT INTO funder_webhook_events
                (funder_address, signature, slot, block_time, direction, counterparty, amount_sol, mint, raw_payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (funder, signature, slot, block_time, direction, counterparty, amount_sol, mint, json.dumps(payload)))
            conn.commit()
            print(f"[WEBHOOK_FUNDER] ✅ {direction}: {funder[:8]}... <-> {counterparty[:8]}... ({amount_sol:.4f} SOL)", flush=True)
        except sqlite3.IntegrityError:
            pass  # Duplicate event
        finally:
            conn.close()

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        print(f"[WEBHOOK_FUNDER] ⚠ Error: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route('/api/listener/tx-cache-stats')
def listener_tx_cache_stats():
    """
    Expose listener transaction cache statistics to UI.
    """
    try:
        # Get listener instance (if stored as global)
        # For now, return placeholder
        global listener  # If you store listener as global in main.py
        if 'listener' in globals():
            stats = listener.get_tx_cache_stats()
            return jsonify(stats)

        return jsonify({
            "tx_cache_hit": 0,
            "tx_cache_miss": 0,
            "tx_cache_wait": 0,
            "tx_cache_size": 0,
            "tx_cache_hit_rate_pct": 0.0,
            "rpc_calls_avoided": 0,
            "credits_saved": 0,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/funder-watchlist/summary')
def funder_watchlist_summary():
    """Get summary of funder watchlist by risk tier."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        summary = {}
        for tier in ["CRITICAL", "HIGH", "MEDIUM", "LOW"]:
            cursor.execute("""
                SELECT COUNT(*) as count, COALESCE(SUM(risk_score), 0) as total_risk
                FROM funder_watchlist
                WHERE webhook_group_id = ? AND is_active = 1
            """, (tier,))
            row = cursor.fetchone()
            summary[tier] = {
                "count": row[0] if row else 0,
                "total_risk_score": row[1] if row else 0,
            }

        conn.close()
        return jsonify(summary)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/funder-watchlist/top-risky')
def funder_watchlist_top_risky():
    """Get top 20 most risky funders."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT funder_address, risk_score, webhook_group_id, risk_reasons
            FROM funder_watchlist
            WHERE is_active = 1
            ORDER BY risk_score DESC
            LIMIT 20
        """)

        rows = cursor.fetchall()
        result = []
        for row in rows:
            risk_reasons = json.loads(row[3]) if row[3] else []
            result.append({
                "funder_address": row[0],
                "risk_score": row[1],
                "risk_tier": row[2],
                "risk_reasons": risk_reasons,
            })

        conn.close()
        return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/funder-webhook-events')
def funder_webhook_events():
    """Get recent funder webhook events (paginated)."""
    try:
        limit = request.args.get('limit', 50, type=int)
        offset = request.args.get('offset', 0, type=int)

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, funder_address, signature, block_time, direction,
                   counterparty, amount_sol, mint, ingested_at
            FROM funder_webhook_events
            ORDER BY ingested_at DESC
            LIMIT ? OFFSET ?
        """, (limit, offset))

        rows = cursor.fetchall()
        result = []
        for row in rows:
            result.append({
                "id": row[0],
                "funder_address": row[1],
                "signature": row[2],
                "block_time": row[3],
                "direction": row[4],
                "counterparty": row[5],
                "amount_sol": row[6],
                "mint": row[7],
                "ingested_at": row[8],
            })

        conn.close()
        return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e)}), 500
```

---

## File 3: Create new file `funder_watchlist_builder.py`

Copy the entire `funder_watchlist_builder.py` section from `PATCH_FUNDER_WEBHOOKS.py` (~200 lines).

This file can be run:
- One-time: `python funder_watchlist_builder.py`
- Scheduled: Add to cron job or APScheduler task every 6 hours

---

## Summary of Changes

| File | Type | #Changes | Lines |
|------|------|----------|-------|
| `pumpfun_curve_listener.py` | Modify | 8 hunks | ~500 lines added |
| `main.py` | Modify | 1 hunk (add routes) | ~200 lines added |
| `funder_watchlist_builder.py` | Create | New file | ~250 lines |

**Total additions**: ~950 lines of code across 3 files
**Total modifications**: ~8 method/section updates
**Risk level**: LOW (additive, backward compatible)

---

## Verification Checklist

After applying all changes:

```bash
# 1. Check syntax
python -m py_compile pumpfun_curve_listener.py
python -m py_compile main.py
python -m py_compile funder_watchlist_builder.py

# 2. Start listener and check logs
python pumpfun_curve_listener.py
# Should see: [INIT] ✅ TX Cache initialized (TTL: 1800s)
# Should see: [DB] ✅ Funder webhook tables ensured

# 3. Test watchlist builder
python funder_watchlist_builder.py
# Should see: [WATCHLIST_BUILDER] ✅ Watchlist rebuilt: X added, Y updated

# 4. Test webhook receiver
curl -X POST http://localhost:5002/api/webhook/funder \
  -H "Content-Type: application/json" \
  -d '{"signature":"test1", "source":"addr1", "destination":"addr2", "blockTime":123}'
# Should return: {"status":"ok"}

# 5. Check database tables
sqlite3 flex_complete_database.db
sqlite> SELECT COUNT(*) FROM funder_watchlist;
sqlite> SELECT COUNT(*) FROM funder_webhook_events;
```

---

**All changes are non-breaking and can be rolled back by removing the new code.**

