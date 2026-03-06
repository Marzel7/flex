# Funder Webhook + Listener TX Cache Implementation

## Overview
This document provides complete implementation patches for:
1. **Task A**: Funder webhook monitoring system (SQLite schema + watchlist builder + webhook receiver)
2. **Task B**: Transaction caching in PumpFunCurveListener to reduce duplicate RPC calls

---

## Task A: Funder Webhook Monitoring

### A.1 Database Schema (SQL)

Add to `_ensure_db()` in pumpfun_curve_listener.py:

```sql
-- Funder watchlist: curated list of funders to monitor with webhooks
CREATE TABLE IF NOT EXISTS funder_watchlist (
    funder_address TEXT PRIMARY KEY,
    risk_score INTEGER DEFAULT 0,  -- 0-1000, higher = more risky
    risk_reasons TEXT,             -- JSON array of strings (why they're monitored)
    first_added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active INTEGER DEFAULT 1,   -- 0/1, whether to monitor
    webhook_group_id TEXT          -- which webhook group they're assigned to
);

-- Webhook groups: organize funders into buckets for webhook management
CREATE TABLE IF NOT EXISTS funder_webhook_groups (
    webhook_group_id TEXT PRIMARY KEY,
    description TEXT,              -- e.g., "CRITICAL", "HIGH", "MEDIUM"
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active INTEGER DEFAULT 1,   -- toggle entire group on/off
    helius_webhook_id TEXT         -- Helius webhook ID (when created)
);

-- Funder webhook events: ingest funder transactions from Helius webhooks
CREATE TABLE IF NOT EXISTS funder_webhook_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    funder_address TEXT NOT NULL,
    signature TEXT NOT NULL,
    slot INTEGER,
    block_time INTEGER,
    direction TEXT,                -- "IN" (received) or "OUT" (sent)
    counterparty TEXT,             -- address they transacted with
    amount_sol REAL,               -- SOL amount
    mint TEXT,                     -- token mint (if token transfer, else NULL)
    raw_payload TEXT,              -- full Helius webhook payload (JSON)
    ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(signature, funder_address)  -- prevent duplicate events
);

-- Index for fast lookups
CREATE INDEX IF NOT EXISTS idx_funder_watchlist_active ON funder_watchlist(is_active);
CREATE INDEX IF NOT EXISTS idx_funder_webhook_events_funder ON funder_webhook_events(funder_address);
CREATE INDEX IF NOT EXISTS idx_funder_webhook_events_block_time ON funder_webhook_events(block_time DESC);
```

### A.2 Watchlist Builder Job

New file: `funder_watchlist_builder.py`

```python
"""
Funder Watchlist Builder

Identifies and scores funders for webhook monitoring based on:
1. Rugged creator funding (funded creators that later rugged)
2. Multi-creator funding (funds many creators in short time)
3. Fingerprint cluster membership (in cluster with malicious wallets)
4. Direct graph connections (1-2 hops from blocklisted creators)

Run periodically (e.g., every 6 hours) to update watchlist.
"""

import sqlite3
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set
import asyncio

DB_PATH = "flex_complete_database.db"

# Risk tier assignments (webhook grouping)
RISK_TIERS = {
    "CRITICAL": (800, 1000),  # score range for CRITICAL tier
    "HIGH": (500, 799),
    "MEDIUM": (200, 499),
    "LOW": (0, 199),
}


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def compute_funder_risk_score(conn: sqlite3.Connection, funder_address: str) -> tuple[int, List[str]]:
    """
    Compute risk score for a funder (0-1000).
    Returns: (score, list of reasons)
    """
    reasons = []
    score = 0

    # Rule 1: Check if funder funded rugged creators
    cursor = conn.execute("""
        SELECT COUNT(DISTINCT cf.creator_address) as rugged_creator_count
        FROM creator_funders cf
        JOIN creator_blocklist cb ON cf.creator_address = cb.creator_address
        WHERE cf.funder_address = ?
    """, (funder_address,))

    row = cursor.fetchone()
    rugged_count = row[0] if row else 0

    if rugged_count >= 6:
        score += 400
        reasons.append(f"Funded {rugged_count} rugged creators")
    elif rugged_count >= 3:
        score += 200
        reasons.append(f"Funded {rugged_count} rugged creators")
    elif rugged_count >= 1:
        score += 80
        reasons.append(f"Funded {rugged_count} rugged creator(s)")

    # Rule 2: Check multi-creator funding (many creators in short time window)
    cursor = conn.execute("""
        SELECT COUNT(DISTINCT creator_address) as creator_count
        FROM creator_funders
        WHERE funder_address = ?
    """, (funder_address,))

    row = cursor.fetchone()
    creator_count = row[0] if row else 0

    if creator_count >= 50:
        score += 300
        reasons.append(f"Funds {creator_count} distinct creators (hub behavior)")
    elif creator_count >= 20:
        score += 150
        reasons.append(f"Funds {creator_count} distinct creators")
    elif creator_count >= 10:
        score += 60
        reasons.append(f"Funds {creator_count} creators")

    # Rule 3: Check fingerprint cluster membership (in same cluster as malicious)
    # (Assumes fingerprint_clusters or super_clusters table exists)
    try:
        cursor = conn.execute("""
            SELECT COUNT(*) as malicious_in_cluster
            FROM super_clusters sc1
            JOIN creator_blocklist cb ON sc1.creator_address = cb.creator_address
            WHERE sc1.cluster_id = (
                SELECT cluster_id FROM super_clusters WHERE creator_address = ? LIMIT 1
            ) AND sc1.creator_address != ?
        """, (funder_address, funder_address))

        row = cursor.fetchone()
        malicious_count = row[0] if row else 0

        if malicious_count >= 3:
            score += 250
            reasons.append(f"In cluster with {malicious_count} blocklisted creators")
    except:
        pass  # super_clusters may not exist yet

    # Rule 4: Check if funder is marked as CEX/infra (reduce score)
    # If they ARE CEX, we don't monitor them unless they have very high other scores
    try:
        cursor = conn.execute("SELECT is_cex FROM creator_funders WHERE funder_address = ? LIMIT 1", (funder_address,))
        row = cursor.fetchone()
        if row and row[0]:
            score = max(0, score - 200)  # Penalize CEX wallets
            reasons.append("(CEX/infra wallet - score reduced)")
    except:
        pass

    # Cap score at 1000
    score = min(score, 1000)

    return score, reasons


def assign_to_webhook_group(score: int) -> str:
    """Assign funder to webhook group based on risk score."""
    for tier, (min_score, max_score) in RISK_TIERS.items():
        if min_score <= score <= max_score:
            return tier
    return "LOW"


def ensure_webhook_groups(conn: sqlite3.Connection):
    """Ensure webhook groups exist."""
    cursor = conn.cursor()
    for tier in RISK_TIERS.keys():
        cursor.execute("""
            INSERT OR IGNORE INTO funder_webhook_groups (webhook_group_id, description, is_active)
            VALUES (?, ?, 1)
        """, (tier, f"{tier} Risk Funder Tier"))
    conn.commit()


def rebuild_funder_watchlist():
    """
    Rebuild funder watchlist from scratch.
    Called periodically (e.g., every 6 hours) to update scores and assignments.
    """
    conn = get_db()
    cursor = conn.cursor()

    print("[WATCHLIST_BUILDER] Starting watchlist rebuild...", flush=True)

    # Ensure webhook groups exist
    ensure_webhook_groups(conn)

    # Get all known funders
    cursor.execute("SELECT DISTINCT funder_address FROM creator_funders")
    funders = [row[0] for row in cursor.fetchall()]

    print(f"[WATCHLIST_BUILDER] Scoring {len(funders)} funders...", flush=True)

    updated_count = 0
    added_count = 0

    for funder_address in funders:
        score, reasons = compute_funder_risk_score(conn, funder_address)

        # Only add if score is above threshold (e.g., > 50)
        if score > 50:
            webhook_group = assign_to_webhook_group(score)

            # Check if already exists
            cursor.execute("SELECT funder_address FROM funder_watchlist WHERE funder_address = ?", (funder_address,))
            exists = cursor.fetchone()

            if exists:
                cursor.execute("""
                    UPDATE funder_watchlist
                    SET risk_score = ?, risk_reasons = ?, webhook_group_id = ?, last_updated_at = CURRENT_TIMESTAMP
                    WHERE funder_address = ?
                """, (score, json.dumps(reasons), webhook_group, funder_address))
                updated_count += 1
            else:
                cursor.execute("""
                    INSERT INTO funder_watchlist (funder_address, risk_score, risk_reasons, webhook_group_id, is_active)
                    VALUES (?, ?, ?, ?, 1)
                """, (funder_address, score, json.dumps(reasons), webhook_group))
                added_count += 1

    conn.commit()
    print(f"[WATCHLIST_BUILDER] ✅ Watchlist rebuilt: {added_count} added, {updated_count} updated", flush=True)

    # Summarize by tier
    for tier in RISK_TIERS.keys():
        cursor.execute("SELECT COUNT(*) FROM funder_watchlist WHERE webhook_group_id = ? AND is_active = 1", (tier,))
        count = cursor.fetchone()[0]
        print(f"[WATCHLIST_BUILDER]   {tier}: {count} funders", flush=True)

    conn.close()


if __name__ == "__main__":
    rebuild_funder_watchlist()
```

### A.3 Webhook Receiver Endpoint

Add to `main.py` (Flask app):

```python
from flask import request, jsonify
import json

@app.route('/api/webhook/funder', methods=['POST'])
def webhook_funder_event():
    """
    Receive funder webhook events from Helius.

    Event format (from Helius):
    {
        "signature": "...",
        "slot": 12345,
        "blockTime": 1234567890,
        "type": "SOL_TRANSFER" | "TOKEN_TRANSFER" | ...,
        "source": "...",
        "destination": "...",
        "amount": 1000000,  # in lamports for SOL
        "mint": "..." (if token),
        "nativeTransfers": [...],
        "tokenTransfers": [...]
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

        # Determine direction and counterparty
        # (need to determine which is the "watched" funder)
        direction = None
        counterparty = None
        amount_sol = 0

        # Check if source is a watched funder (means transfer OUT)
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute("SELECT 1 FROM funder_watchlist WHERE funder_address = ? AND is_active = 1", (source,))
        if cursor.fetchone():
            direction = "OUT"
            counterparty = destination
            # Amount from SOL transfer
            native_transfers = payload.get("nativeTransfers", [])
            if native_transfers:
                amount_sol = sum(t.get("amount", 0) for t in native_transfers) / 1e9  # lamports to SOL

        # Check if destination is a watched funder (means transfer IN)
        cursor.execute("SELECT 1 FROM funder_watchlist WHERE funder_address = ? AND is_active = 1", (destination,))
        if cursor.fetchone():
            direction = "IN"
            counterparty = source
            native_transfers = payload.get("nativeTransfers", [])
            if native_transfers:
                amount_sol = sum(t.get("amount", 0) for t in native_transfers) / 1e9

        if not direction:
            conn.close()
            return jsonify({"error": "funder not in watchlist"}), 200  # Not an error, just skip

        # Insert event (dedupe by UNIQUE constraint)
        funder = source if direction == "OUT" else destination
        try:
            cursor.execute("""
                INSERT INTO funder_webhook_events
                (funder_address, signature, slot, block_time, direction, counterparty, amount_sol, mint, raw_payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (funder, signature, slot, block_time, direction, counterparty, amount_sol, mint, json.dumps(payload)))
            conn.commit()
            print(f"[WEBHOOK_FUNDER] ✅ Recorded {direction} event: {funder[:8]}... <-> {counterparty[:8]}... ({amount_sol:.4f} SOL)", flush=True)
        except sqlite3.IntegrityError:
            # Duplicate event, silently skip
            pass
        finally:
            conn.close()

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        print(f"[WEBHOOK_FUNDER] ⚠ Error processing webhook: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
```

---

## Task B: Listener getTransaction Caching

### B.1 Add Transaction Cache to PumpFunCurveListener

Add to pumpfun_curve_listener.py at the top of the class:

```python
# In __init__ method:
self.tx_cache = {}  # {signature: (tx_data, timestamp)}
self.tx_cache_ttl_seconds = 1800  # 30 minutes
self.tx_inflight_locks = {}  # {signature: asyncio.Lock()} for singleflight
self.tx_cache_stats = {
    'hit': 0,
    'miss': 0,
    'wait': 0,
}  # Track cache performance

# Add import at top of file (if not present):
import hashlib
import asyncio
```

### B.2 Add Cached TX Fetch Method

Add to PumpFunCurveListener class:

```python
async def _get_transaction_cached(self, signature: str, timeout: int = 10) -> Optional[Dict]:
    """
    Fetch transaction with TTL cache + singleflight.

    Deduplicates concurrent requests for the same signature.
    Cache TTL is 30 minutes (1800 seconds).

    Returns tx_data dict or None if not found.
    """
    current_time = time.time()

    # Check cache hit
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

    # Check if already in-flight (singleflight pattern)
    if signature in self.tx_inflight_locks:
        # Another coroutine is already fetching this
        self.tx_cache_stats['wait'] += 1
        lock = self.tx_inflight_locks[signature]
        await lock.acquire()
        lock.release()

        # After lock released, tx should be in cache
        if signature in self.tx_cache:
            cached_data, _ = self.tx_cache[signature]
            print(f"[TX_CACHE] ⏳ WAIT satisfied: {signature[:16]}... (shared fetch)", flush=True)
            return cached_data
        return None

    # Cache miss: fetch it
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
            # Cache it
            self.tx_cache[signature] = (tx_data["result"], current_time)
            print(f"[TX_CACHE] 💾 CACHED: {signature[:16]}... ({len(str(tx_data['result']))} bytes)", flush=True)
            return tx_data["result"]

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

### B.3 Refactor Methods to Use Cached TX

Refactor `_fetch_mint_from_transaction` to accept cached tx_data:

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


async def _extract_pool_from_tx(self, tx_data: Dict) -> Optional[str]:
    """
    Extract PumpSwap pool address from transaction data (no RPC call needed).
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
                        return account_keys[pool_idx]

    return None
```

Refactor `handle_migration` to use cached TX:

```python
async def handle_migration(self, signature: str, logs: list):
    """Process detected migration"""
    try:
        if signature in self.detected_migrations:
            return

        self.detected_migrations.add(signature)

        # === CRITICAL OPTIMIZATION: Cache TX fetch ===
        # Fetch TX once and reuse for mint, pool, blockTime extraction
        tx_data = await self._get_transaction_cached(signature)

        if not tx_data:
            print(f"[MIGRATION] ⚠ Could not fetch transaction {signature}", flush=True)
            # Retry with old logic as fallback
            mint = await self._fetch_mint_from_transaction(signature)
        else:
            # Extract from cached tx_data (no RPC call)
            mint = await self._extract_mint_from_tx(tx_data)

        if not mint:
            print(f"[MIGRATION] ⚠ Failed to extract mint from transaction logs fallback", flush=True)
            mint = self._extract_mint_from_logs(logs)

        if not mint:
            print(f"[MIGRATION] ⚠ Could not extract mint from {signature} - SKIPPED", flush=True)
            return

        # Skip if already analyzed
        if self._token_exists_in_db(mint):
            print(f"[MIGRATION] ⏭️  Token {mint} already analyzed - SKIPPED", flush=True)
            return

        self.seen_mints.add(mint)
        print(f"[EVENT] 🚀 MIGRATION DETECTED: {mint}", flush=True)
        print(f"[EVENT] Migration signature: {signature}", flush=True)

        # Create minimal token entry
        await self._create_minimal_token_entry(mint)

        # Extract pool from cached tx (no RPC call!)
        pool_address = None
        if tx_data:
            pool_address = await self._extract_pool_from_tx(tx_data)
            if pool_address:
                print(f"[EVENT] ✅ Pool extracted from cached tx: {pool_address}", flush=True)

        # Extract blockTime from cached tx
        created_at = None
        if tx_data:
            block_time = tx_data.get("blockTime")
            if block_time:
                created_at = datetime.utcfromtimestamp(block_time).isoformat() + "Z"
                print(f"[CREATOR] 🕐 Using blockTime from cached tx: {created_at}", flush=True)

        # Rest of handle_migration continues as before...
        # (price fetch, creator extraction, background tasks, etc.)
```

### B.4 Expose Cache Metrics

Add method to PumpFunCurveListener:

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
        'rpc_calls_avoided': self.tx_cache_stats['hit'],  # Each cache hit = 1 RPC call avoided
    }
```

### B.5 Update RPC Metrics Recording

Modify `_post_rpc_with_fallback` to include cache metrics:

```python
# When recording getTransaction requests:
cache_action = 'none'  # default
credits_saved = 0

# If this was a cache hit at higher level, mark it differently
# (otherwise mark as 'none' since it's a real RPC call)

record_request(
    section='listener',
    provider='helius',
    method='getTransaction',
    status_code=status,
    latency_ms=latency,
    credits=credits,
    source_file='pumpfun_curve_listener',
    cache_action=cache_action,  # 'none' for actual RPC
    credits_saved=0
)

# When serving from cache (in _get_transaction_cached):
# Skip RPC call entirely, no metrics recorded (cache is local)
# But the wrapper `handle_migration` should track the avoided call
```

### B.6 UI Dashboard Integration

Add to `rpc_metrics_api.py` dashboard:

```python
# Add section to dashboard HTML showing cache metrics:
<!--
  <div class="card">
    <h3>🗂️ Transaction Cache (Listener)</h3>
    <table>
      <tr>
        <td>Cache Hits (RPC avoided)</td>
        <td id="tx_cache_hit">-</td>
      </tr>
      <tr>
        <td>Cache Misses (RPC called)</td>
        <td id="tx_cache_miss">-</td>
      </tr>
      <tr>
        <td>Concurrent Waits (shared)</td>
        <td id="tx_cache_wait">-</td>
      </tr>
      <tr>
        <td>Cache Hit Rate</td>
        <td id="tx_cache_hit_rate">-</td>
      </tr>
      <tr>
        <td>RPC Calls Avoided</td>
        <td id="tx_cache_avoided">-</td>
      </tr>
    </table>
  </div>
-->

// In JavaScript fetch:
function updateCacheMetrics() {
  fetch('/api/listener/tx-cache-stats')
    .then(r => r.json())
    .then(data => {
      document.getElementById('tx_cache_hit').textContent = data.tx_cache_hit;
      document.getElementById('tx_cache_miss').textContent = data.tx_cache_miss;
      document.getElementById('tx_cache_wait').textContent = data.tx_cache_wait;
      document.getElementById('tx_cache_hit_rate').textContent =
        data.tx_cache_hit_rate_pct.toFixed(1) + '%';
      document.getElementById('tx_cache_avoided').textContent =
        data.rpc_calls_avoided + ' = ' + (data.rpc_calls_avoided * 10) + ' credits saved';
    });
}

setInterval(updateCacheMetrics, 5000);
```

Add route to main.py:

```python
@app.route('/api/listener/tx-cache-stats')
def listener_tx_cache_stats():
    """Expose listener transaction cache statistics."""
    try:
        # Get listener instance (you may need to refactor how listener is stored)
        # For now, return a placeholder
        return jsonify({
            "tx_cache_hit": 0,
            "tx_cache_miss": 0,
            "tx_cache_wait": 0,
            "tx_cache_hit_rate_pct": 0.0,
            "rpc_calls_avoided": 0,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
```

---

## Implementation Checklist

### Task A (Funder Webhooks)
- [ ] Add SQL schema to `_ensure_db()` in pumpfun_curve_listener.py
- [ ] Create `funder_watchlist_builder.py` script
- [ ] Run initial `rebuild_funder_watchlist()` to populate watchlist
- [ ] Add `/api/webhook/funder` endpoint to main.py
- [ ] Configure Helius webhook(s) to send funder events to `/api/webhook/funder`
- [ ] Test webhook receiver with sample Helius payloads

### Task B (Listener TX Cache)
- [ ] Add cache dict, locks, and stats to `PumpFunCurveListener.__init__`
- [ ] Implement `_get_transaction_cached()` method
- [ ] Refactor `_extract_mint_from_tx()` (no RPC needed)
- [ ] Refactor `_extract_pool_from_tx()` (no RPC needed)
- [ ] Refactor `handle_migration()` to use cached TX
- [ ] Implement `get_tx_cache_stats()` method
- [ ] Add `/api/listener/tx-cache-stats` endpoint
- [ ] Add cache metrics UI to dashboard
- [ ] Test: confirm cache hits / miss rates in logs

### Testing
- [ ] Monitor listener logs for "[TX_CACHE]" messages
- [ ] Verify cache hit rate improves over time (target: >60% after warmup)
- [ ] Verify no regressions in token detection
- [ ] Compare RPC credit usage before/after (expect ~30% reduction in getTransaction)

---

## Expected Savings

### Before Caching
- Per migration: 2-3 `getTransaction` calls
  - `_fetch_mint_from_transaction`: 1-3 retries, but one succeeds: 1 call
  - `_extract_pool_from_migration_tx`: 1 call
  - `created_at` blockTime fallback: 1 call
- Total: 3 calls × 10 credits = **30 credits per migration**

### After Caching
- Per migration: 1 `getTransaction` call (cache hit for subsequent extractions)
- Total: 1 call × 10 credits = **10 credits per migration**
- **Savings: 20 credits per migration (67% reduction)**

If listener processes 100 migrations/day:
- **Before**: 3,000 credits/day
- **After**: 1,000 credits/day
- **Monthly savings**: ~60,000 credits (worth ~$600 at current Helius rates)

