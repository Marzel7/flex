#!/usr/bin/env python3
"""
Extract and save funder transfers (both incoming and outgoing) to database.

SAFEST/RELIABLE (but still fast) version:
- Prefer Helius enriched tx feed when available (nativeTransfers already parsed)
- Fall back to Solana RPC only if Helius unavailable/fails
- Shared HTTP session w/ retries + exponential backoff
- Bounded concurrency for multi-funder processing (default 2)
- Batch DB inserts + WAL pragmas + required indexes
- Async-safe entrypoints (no asyncio.run() inside running event loop)

For each funder for a creator:
1. Pull recent txs (Helius preferred)
2. Parse nativeTransfers for IN/OUT wrt funder
3. Classify counterparties (CEX/INFRA/unknown) with LRU cache
4. Batch insert into funder_incoming_transfers + funder_outgoing_transfers
"""

from __future__ import annotations

import os
import sys
import time
import sqlite3
import asyncio
import logging
from typing import Dict, List, Tuple, Optional
from functools import lru_cache
import requests

sys.path.insert(0, "/Users/kevinkeaveney/Dev/claude/flex")

# Configure logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


# Logging helper for flush compatibility
def log_print(*args, **kwargs):
    """Print with flush support across Python versions"""
    kwargs.pop('flush', None)  # Remove flush if present
    print(*args, **kwargs)
    sys.stdout.flush()

from src.utils.infra_mapping import get_account_info, get_cex_info  # type: ignore

# Import RPC metrics recorder for monitoring
try:
    from src.metrics.rpc_metrics_recorder import record_request, initialize_recorder
    initialize_recorder(plan_monthly_credits=50_000_000)
except ImportError:
    def record_request(*args, **kwargs):
        return 0  # No-op if metrics recorder not available

# Import wallet fingerprint clustering for cross-creator deduplication
try:
    from wallet_fingerprint_clustering import WalletFingerprintCluster, FingerprintAction
except ImportError:
    WalletFingerprintCluster = None
    FingerprintAction = None

# Env
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(os.path.join(os.path.dirname(__file__), '../../config/.env'))
except Exception:
    pass

DB_PATH = os.environ.get('DB_PATH', os.getenv('RPC_METRICS_DB', 'flex_complete_database.db'))
SOLANA_RPC = "https://api.mainnet-beta.solana.com"
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "").strip()

LAMPORTS_PER_SOL = 1_000_000_000
USE_HELIUS = bool(HELIUS_API_KEY)

# Initialize wallet fingerprint clustering (global, reused across all funders)
FINGERPRINT_ENABLED = os.getenv("FINGERPRINT_ENABLED", "1").strip() in ("1", "true", "yes")
FINGERPRINT_CLUSTER = None
if FINGERPRINT_ENABLED and WalletFingerprintCluster is not None:
    try:
        FINGERPRINT_CLUSTER = WalletFingerprintCluster(DB_PATH)
        logger.info("[FINGERPRINT] Clustering enabled and initialized")
    except Exception as e:
        logger.warning(f"[FINGERPRINT] Failed to initialize: {e}")

# Reliability defaults
MIN_SOL = 0.001
DEFAULT_HELIUS_LIMIT = 100        # Helius endpoint commonly maxes at 100
DEFAULT_RPC_SIG_LIMIT = 100       # Cap signatures per funder (was 200)
MAX_HTTP_RETRIES = 5
MAX_RPC_RETRIES = 4
BASE_BACKOFF_SECS = 0.5

# Cost control: fresh funder limits
MAX_FRESH_FUNDERS_PER_CREATOR = int(os.getenv("MAX_FRESH_FUNDERS_PER_CREATOR", "10"))
MAX_TX_SIGS_PER_FUNDER = int(os.getenv("MAX_TX_SIGS_PER_FUNDER", "100"))

# Concurrency: reduced for cost stability
DEFAULT_CONCURRENCY = 2  # Was 4, reduced to stabilize billing

# HARDENING FIX #2: Transient HTTP error codes (selective retry on 4xx)
# Note: 429 is handled separately with Retry-After header support in _request_json()
TRANSIENT_HTTP_CODES = {
    408,  # Request Timeout
    409,  # Conflict
    423,  # Locked
    425,  # Too Early
}

# Shared HTTP session (keep-alive)
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "funder-transfer-extractor/2.3"})

# Phase 2b: Initialize RPCCache for response-level caching
RPC_CACHE = None
try:
    from src.core.rpc_cache import RPCCache
    RPC_CACHE = RPCCache(DB_PATH)
    logger.info("[PHASE2B] RPCCache initialized for funder_incoming_extractor")
except Exception as e:
    logger.warning(f"[PHASE2B] RPCCache initialization failed: {e} (caching disabled)")

# -------------------------
# DB helpers
# -------------------------

def _open_db_optimized() -> sqlite3.Connection:
    """Open SQLite connection with pragmas that are safe-ish but faster for bulk.

    HARDENING FIX #5: Reduced cache_size from -200000 to -50000
    • -200000 (~200MB) × concurrency=4 = 800MB potential memory spike
    • -50000 (~50MB) × concurrency=4 = 200MB (safe, acceptable)
    """
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA temp_store=MEMORY;")
    conn.execute("PRAGMA cache_size=-50000;")  # ~50MB (was -200000, reduced for safety)
    return conn


def _ensure_tables_and_indexes() -> None:
    """Create tables/indexes if missing. Safe to call often."""
    conn = _open_db_optimized()
    cur = conn.cursor()

    # Incoming transfers
    cur.execute("""
        CREATE TABLE IF NOT EXISTS funder_incoming_transfers (
            sender_address TEXT NOT NULL,
            funder_address TEXT NOT NULL,
            amount_sol REAL,
            sender_type TEXT,
            transaction_signature TEXT,
            block_time INTEGER,
            is_cex INTEGER DEFAULT 0,
            cex_exchange TEXT,
            cex_type TEXT,
            PRIMARY KEY (sender_address, funder_address, transaction_signature)
        )
    """)

    # Outgoing transfers
    cur.execute("""
        CREATE TABLE IF NOT EXISTS funder_outgoing_transfers (
            funder_address TEXT NOT NULL,
            recipient_address TEXT NOT NULL,
            amount_sol REAL,
            recipient_type TEXT,
            transaction_signature TEXT,
            block_time INTEGER,
            is_cex INTEGER DEFAULT 0,
            cex_exchange TEXT,
            cex_type TEXT,
            PRIMARY KEY (funder_address, recipient_address, transaction_signature)
        )
    """)

    # Indexes for the “cache exists?” checks
    cur.execute("CREATE INDEX IF NOT EXISTS idx_fit_funder ON funder_incoming_transfers(funder_address)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_fot_funder ON funder_outgoing_transfers(funder_address)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_fit_sig ON funder_incoming_transfers(transaction_signature)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_fot_sig ON funder_outgoing_transfers(transaction_signature)")

    conn.commit()
    conn.close()


# -------------------------
# Classification (cached)
# -------------------------

def _fingerprint_wallet_type_and_confidence(wallet_address: str, txs: Optional[List[dict]] = None) -> Tuple[str, float]:
    """
    Classify wallet fingerprint and assign confidence score based on account metadata + transaction patterns.
    
    Confidence scoring (0.0-1.0):
    - 0.95: CEX address (high confidence)
    - 0.90: Infrastructure address (high confidence)
    - 0.75: Bot-like patterns (many transfers, no diversity)
    - 0.80: Hub-like patterns (concentrator/distributor)
    - 0.60: Unknown pattern (no clear signal)
    - 0.50: Minimal activity (low confidence)
    
    Returns: (wallet_type, confidence)
    """
    try:
        # Fast account-based check first (CEX/INFRA)
        cex_info = get_cex_info(wallet_address)
        if cex_info:
            return ("cex", 0.95)
        
        infra_info = get_account_info(wallet_address)
        if infra_info:
            return ("infra", 0.90)
    except Exception:
        pass
    
    # If unknown, analyze transactions for pattern-based classification
    if not txs:
        return ("unknown", 0.60)
    
    try:
        # Count transfer patterns from transaction list
        native_transfers = 0
        unique_counterparties = set()
        
        for tx in txs:
            if not isinstance(tx, dict):
                continue
            
            # Count native transfers from this transaction
            transfers = tx.get("nativeTransfers", [])
            if isinstance(transfers, list):
                native_transfers += len(transfers)
                
                # Track unique counterparties
                for nt in transfers:
                    if isinstance(nt, dict):
                        sender = nt.get("fromUserAccount")
                        recipient = nt.get("toUserAccount")
                        if isinstance(sender, str):
                            unique_counterparties.add(sender)
                        if isinstance(recipient, str):
                            unique_counterparties.add(recipient)
        
        # Pattern-based classification
        if native_transfers == 0:
            # No transfers = bot, inactive, or holding account
            return ("bot", 0.75)
        elif native_transfers > 50:
            # Many transfers = hub, aggregator, or distributor
            return ("hub", 0.80)
        elif len(unique_counterparties) > 30:
            # Many different counterparties = active trader
            return ("active_trader", 0.70)
        else:
            # Moderate activity = unknown
            return ("unknown", 0.60)
    
    except Exception as e:
        logger.debug(f"[FINGERPRINT] Pattern analysis failed for {wallet_address}: {e}")
        return ("unknown", 0.60)


@lru_cache(maxsize=50000)
def classify_sender(address: str) -> Tuple[str, Optional[str], Optional[str]]:
    """
    Return (type, exchange_name, exchange_type)
    type in {"cex","infra","unknown"}
    """
    cex_info = get_cex_info(address)
    if cex_info:
        return ("cex", cex_info.get("name"), cex_info.get("cex_type"))

    infra_info = get_account_info(address)
    if infra_info:
        return ("infra", infra_info.get("name"), None)

    return ("unknown", None, None)


# -------------------------
# DB queries
# -------------------------

def get_creator_funders(creator_address: str) -> List[Tuple[str, float]]:
    """Get all funders for a creator from database."""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            """
            SELECT funder_address, amount_sol
            FROM creator_funders
            WHERE creator_address = ?
            ORDER BY amount_sol DESC
            """,
            (creator_address,),
        )
        rows = cur.fetchall()
        conn.close()
        return [(r["funder_address"], float(r["amount_sol"] or 0.0)) for r in rows]
    except Exception as e:
        log_print(f"[DB] Error getting funders: {e}")
        return []


def _has_cached_funder_transfers(funder_address: str) -> Tuple[int, int, float]:
    """Return (incoming_count, outgoing_count, total_sol) from cache if any."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*) FROM funder_incoming_transfers WHERE funder_address = ?", (funder_address,))
        inc = int(cur.fetchone()[0] or 0)

        cur.execute("SELECT COUNT(*) FROM funder_outgoing_transfers WHERE funder_address = ?", (funder_address,))
        out = int(cur.fetchone()[0] or 0)

        if inc or out:
            cur.execute("SELECT COALESCE(SUM(amount_sol),0) FROM funder_incoming_transfers WHERE funder_address = ?", (funder_address,))
            inc_sum = float(cur.fetchone()[0] or 0.0)

            cur.execute("SELECT COALESCE(SUM(amount_sol),0) FROM funder_outgoing_transfers WHERE funder_address = ?", (funder_address,))
            out_sum = float(cur.fetchone()[0] or 0.0)

            return inc, out, inc_sum + out_sum

        return 0, 0, 0.0
    finally:
        conn.close()


# -------------------------
# HTTP helpers (reliable)
# -------------------------

def _sleep_backoff(attempt: int, retry_after: Optional[float] = None) -> None:
    if retry_after is not None and retry_after > 0:
        time.sleep(min(30.0, retry_after))
        return
    # exponential backoff with cap
    time.sleep(min(30.0, BASE_BACKOFF_SECS * (2 ** attempt)))


def _is_rpc_error_retryable(error_obj: dict) -> bool:
    """
    Determine if an RPC error is retryable or permanent.

    HARDENING FIX #3: Smart RPC error categorization
    • Transient (retryable): timeout, rate-limit, overloaded, slot errors
    • Permanent (fail-fast): invalid params, method not found, parse errors
    """
    if not isinstance(error_obj, dict):
        return False

    code = error_obj.get("code")
    msg = (error_obj.get("message") or "").lower()

    # Permanent errors (fail-fast) - safe to identify by code
    permanent_codes = {-32700, -32600, -32601, -32602, -32098}  # Parse, invalid request, method not found, invalid params, invalid account
    if code in permanent_codes:
        return False

    # Check message for transient patterns (most reliable across providers)
    transient_patterns = [
        "timeout", "rate", "overload", "busy", "congested",
        "block height", "skipped", "commitment violation",
        "slot has been skipped", "processed block"
    ]
    if any(pattern in msg for pattern in transient_patterns):
        return True

    # Transient codes (mostly reliable)
    transient_codes = {-32603, -32000}  # Internal error, Server error
    if code in transient_codes:
        return True

    # Default to transient (safer for operational stability)
    return True


def _request_json(method: str, url: str, *, json_body: Optional[dict] = None, timeout: float = 20.0, rpc_method: Optional[str] = None) -> Optional[object]:
    """
    Reliable HTTP call with retry/backoff on 429/5xx and network errors.

    HARDENING FIX #2: Selective transient 4xx retry
    • Retryable 4xx: 408, 409, 423, 425 (timeout, conflict, locked, too early)
    • 429 rate-limit: handled separately with Retry-After header support
    • Non-retryable 4xx: 400, 401, 403, 404, etc. (client responsibility)
    """
    for attempt in range(MAX_HTTP_RETRIES):
        try:
            # Determine RPC method from parameter or infer from URL/payload
            if rpc_method is None:
                method_inferred = "unknown"
                if "/v0/transactions" in url and method.upper() == "POST":
                    # Endpoint: POST /v0/transactions (enhanced tx batch details)
                    method_inferred = "helius_enhanced_transactions_batch"
                elif "/v0/addresses" in url and method.upper() == "GET":
                    # Endpoint: GET /v0/addresses/{addr}/transactions (enhanced address feed)
                    method_inferred = "helius_enhanced_addresses_transactions"
                elif "helius" in url:
                    method_inferred = "helius_rpc"
                elif method.upper() == "POST" and json_body:
                    method_inferred = json_body.get("method", "unknown")
                rpc_method = method_inferred

            start_time = time.time()
            if method.upper() == "GET":
                resp = SESSION.get(url, timeout=timeout)
            else:
                resp = SESSION.post(url, json=json_body, timeout=timeout)

            latency_ms = (time.time() - start_time) * 1000

            # Only record metrics for billable responses (not 429 rate-limited retries)
            if resp.status_code != 429:
                provider = "helius_rpc" if "helius" in url else "solana_rpc"
                credits = record_request(
                    section="funder_incoming",
                    provider=provider,
                    method=rpc_method,
                    status_code=resp.status_code,
                    latency_ms=latency_ms,
                    mode="realtime",
                    retries=attempt,
                    source_file="funder_incoming_extractor",
                )

                # Log the RPC call for debugging
                log_print(f"[FUNDER_INCOMING] RPC: {rpc_method} ({credits} credits) - Status: {resp.status_code} - {latency_ms:.0f}ms")
            else:
                # Log rate limit without recording as billable
                log_print(f"[FUNDER_INCOMING] RPC: {rpc_method} (retry) - Status: {resp.status_code} - {latency_ms:.0f}ms", flush=True)

            # Diagnostic logging for rate limits and errors
            if resp.status_code == 429:
                try:
                    body = resp.text[:200] if resp.text else "(empty)"
                    retry_after = resp.headers.get("Retry-After", "not set")
                    log_print(f"[FUNDER_INCOMING] 429 RATE-LIMITED | Retry-After: {retry_after} | Body: {body}", flush=True)
                except:
                    pass
            elif resp.status_code >= 400:
                try:
                    body = resp.text[:200] if resp.text else "(empty)"
                    log_print(f"[FUNDER_INCOMING] {resp.status_code} ERROR | Body: {body}", flush=True)
                except:
                    pass

            if resp.status_code == 429:
                ra = resp.headers.get("Retry-After")
                retry_after = None
                if ra:
                    try:
                        retry_after = float(ra)
                    except (ValueError, TypeError):
                        retry_after = None
                log_print(f"[HTTP] 429 rate-limited. Backing off (attempt {attempt+1}/{MAX_HTTP_RETRIES})")
                _sleep_backoff(attempt, retry_after=retry_after)
                continue

            if resp.status_code >= 500:
                log_print(f"[HTTP] {resp.status_code} server error. Backing off (attempt {attempt+1}/{MAX_HTTP_RETRIES})")
                _sleep_backoff(attempt)
                continue

            # Selective transient 4xx retry
            if resp.status_code in TRANSIENT_HTTP_CODES:
                log_print(f"[HTTP] {resp.status_code} transient error. Backing off (attempt {attempt+1}/{MAX_HTTP_RETRIES})")
                _sleep_backoff(attempt)
                continue

            if resp.status_code != 200:
                # Permanent client errors: don't retry
                try:
                    txt = resp.text[:300]
                except Exception:
                    txt = ""
                log_print(f"[HTTP] Non-200 ({resp.status_code}). Body: {txt}")
                return None

            return resp.json()

        except (requests.Timeout, requests.ConnectionError) as e:
            rpc_method = "unknown"
            if "helius" in url:
                rpc_method = "helius_enhanced_transactions_batch"
            elif method.upper() == "POST" and json_body:
                rpc_method = json_body.get("method", "unknown")
            provider = "helius_rpc" if "helius" in url else "solana_rpc"
            record_request(
                section="funder_incoming",
                provider=provider,
                method=rpc_method,
                status_code=0,
                latency_ms=(time.time() - start_time) * 1000,
                mode="realtime",
                retries=attempt,
                source_file="funder_incoming_extractor",
                error=str(e),
            )
            log_print(f"[HTTP] Network error: {e}. Backing off (attempt {attempt+1}/{MAX_HTTP_RETRIES})")
            _sleep_backoff(attempt)
            continue
        except Exception as e:
            log_print(f"[HTTP] Unexpected error: {e}")
            return None

    return None


# -------------------------
# Chain fetchers
# -------------------------

def get_transactions_helius(address: str, limit: int = DEFAULT_HELIUS_LIMIT, max_pages: int = 1) -> Optional[List[dict]]:
    """
    Helius enriched address transaction feed (fastest + most reliable parsing).

    Args:
        address: Wallet address to fetch transactions for
        limit: Transactions per page (max 100 per Helius limit)
        max_pages: Maximum number of pages to fetch (default 1 = 100 txs)
                  Set to 3 for ~300 txs (useful for active funders)

    Returns:
        List of transaction dicts, or None if unavailable
    """
    if not USE_HELIUS:
        return None

    lim = max(1, min(int(limit), DEFAULT_HELIUS_LIMIT))
    max_pages = max(1, int(max_pages))
    all_txs: List[dict] = []

    for page in range(max_pages):
        before = all_txs[-1].get('signature', '') if all_txs else ''
        
        # Phase 2b: Check cache for first page only (most valuable)
        cache_result = None
        cache_key = None
        if RPC_CACHE is not None and page == 0:  # Only cache first page
            cache_key = RPC_CACHE.make_key_helius_addr_txs(address, before, lim)
            cache_result = RPC_CACHE.get(cache_key)
        
        if cache_result is not None:
            # Cache hit
            record_request(
                section="creator_funding", provider="helius_rpc",
                method="helius_enhanced_addresses_transactions", status_code=200, latency_ms=0.1,
                mode="realtime", retries=0,
                source_file="funder_incoming_extractor",
                cache_action="hit", credits_saved=100,
            )
            all_txs.extend(cache_result)
            continue
        
        url = (
            f"https://api.helius.xyz/v0/addresses/{address}/transactions"
            f"?api-key={HELIUS_API_KEY}&limit={lim}&before={before}"
        )
        data = _request_json("GET", url, timeout=25.0, rpc_method="helius_enhanced_addresses_transactions")
        
        # Phase 2b: Cache first page result
        if data and isinstance(data, list) and cache_key and RPC_CACHE is not None and page == 0:
            RPC_CACHE.set(cache_key, data, "helius_enhanced_addresses_transactions")
        
        if not isinstance(data, list) or not data:
            break
        all_txs.extend(data)

    return all_txs if all_txs else None


def _rpc_call(payload: dict, timeout: float = 20.0) -> Optional[dict]:
    """
    Reliable Solana RPC POST with retry/backoff.

    HARDENING FIX #3: Smart RPC error categorization
    • Only retries transient errors (timeout, rate-limit, etc.)
    • Fails fast on permanent errors (invalid params, method not found, etc.)
    """
    for attempt in range(MAX_RPC_RETRIES):
        try:
            start_time = time.time()
            resp = SESSION.post(SOLANA_RPC, json=payload, timeout=timeout)
            latency_ms = (time.time() - start_time) * 1000
            rpc_method = payload.get("method", "unknown")
            # Record metric for all RPC responses
            record_request(
                section="funder_incoming",
                provider="solana_rpc",
                method=rpc_method,
                status_code=resp.status_code,
                latency_ms=latency_ms,
                mode="realtime",
                retries=attempt,
                source_file="funder_incoming_extractor",
            )

            if resp.status_code == 429:
                log_print(f"[RPC] 429 rate-limited. Backing off (attempt {attempt+1}/{MAX_RPC_RETRIES})")
                _sleep_backoff(attempt)
                continue
            if resp.status_code >= 500:
                log_print(f"[RPC] {resp.status_code} server error. Backing off (attempt {attempt+1}/{MAX_RPC_RETRIES})")
                _sleep_backoff(attempt)
                continue
            if resp.status_code != 200:
                return None

            data = resp.json()
            if isinstance(data, dict) and data.get("error"):
                error_obj = data["error"]
                if _is_rpc_error_retryable(error_obj):
                    log_print(f"[RPC] Transient error (code={error_obj.get('code')}). Backing off (attempt {attempt+1}/{MAX_RPC_RETRIES})")
                    _sleep_backoff(attempt)
                    continue
                else:
                    # Permanent error: fail fast
                    log_print(f"[RPC] Permanent error (code={error_obj.get('code')}): {error_obj.get('message')}")
                    return None
            return data
        except (requests.Timeout, requests.ConnectionError) as e:
            rpc_method = payload.get("method", "unknown")
            record_request(
                section="funder_incoming",
                provider="solana_rpc",
                method=rpc_method,
                status_code=0,
                latency_ms=(time.time() - start_time) * 1000,
                mode="realtime",
                retries=attempt,
                source_file="funder_incoming_extractor",
                error=str(e),
            )
            log_print(f"[RPC] Network error: {e}. Backing off (attempt {attempt+1}/{MAX_RPC_RETRIES})")
            _sleep_backoff(attempt)
            continue
        except Exception:
            return None
    return None


def get_signatures_for_address_rpc(address: str, limit: int = DEFAULT_RPC_SIG_LIMIT) -> List[str]:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getSignaturesForAddress",
        "params": [address, {"limit": int(limit)}],
    }
    data = _rpc_call(payload, timeout=20.0)
    if not data or "result" not in data or not isinstance(data["result"], list):
        return []
    return [r.get("signature") for r in data["result"] if isinstance(r, dict) and r.get("signature")]


def helius_batch_get_transactions(tx_sigs: List[str]) -> Dict[str, Optional[dict]]:
    """
    Helius batch tx details endpoint:
    - much faster than calling getTransaction N times
    - returns nativeTransfers (ideal) for most txs

    Returns map sig -> tx dict (or None).
    """
    out: Dict[str, Optional[dict]] = {}
    if not USE_HELIUS or not tx_sigs:
        return out

    url = f"https://api.helius.xyz/v0/transactions?api-key={HELIUS_API_KEY}"

    for i in range(0, len(tx_sigs), 100):
        batch = tx_sigs[i:i + 100]
        log_print(f"[FUNDER_INCOMING] Processing batch of {len(batch)} transactions", flush=True)
        # Diagnostic: log which API key and request details
        log_print(f"[FUNDER_INCOMING] Batch request | Key suffix: {HELIUS_API_KEY[-8:] if HELIUS_API_KEY else 'NONE'}", flush=True)

        # Phase 2b: Check cache for entire batch
        cache_result = None
        cache_key = None
        if RPC_CACHE is not None:
            cache_key = RPC_CACHE.make_key_helius_batch(batch)
            cache_result = RPC_CACHE.get(cache_key)
        
        if cache_result is not None:
            # Cache hit - reconstruct output from cached data
            log_print(f"[FUNDER_INCOMING] ✅ Batch cache hit - {len(batch)} transactions from cache", flush=True)
            record_request(
                section="creator_funding", provider="helius_rpc",
                method="helius_enhanced_transactions_batch", status_code=200, latency_ms=0.1,
                mode="realtime", retries=0,
                source_file="funder_incoming_extractor",
                cache_action="hit", credits_saved=10,
            )
            # Cache stores list of txs; reconstruct the signature map
            for tx in cache_result:
                if isinstance(tx, dict):
                    sig = tx.get("signature")
                    if isinstance(sig, str) and sig:
                        out[sig] = tx
            # Mark any missing in batch as None
            for s in batch:
                out.setdefault(s, None)
            continue

        data = _request_json("POST", url, json_body={"transactions": batch}, timeout=35.0, rpc_method="helius_enhanced_transactions_batch")

        # Phase 2b: Cache successful batch result
        if data and isinstance(data, list) and cache_key and RPC_CACHE is not None:
            RPC_CACHE.set(cache_key, data, "helius_enhanced_transactions_batch")

        # Diagnostic: log batch response
        if data is None:
            log_print(f"[FUNDER_INCOMING] ⚠️  Batch returned None (failed request)", flush=True)
        elif not isinstance(data, list):
            log_print(f"[FUNDER_INCOMING] ⚠️  Batch returned non-list: {type(data)}", flush=True)
            # mark batch unknown
            for s in batch:
                out[s] = None
            continue
        else:
            log_print(f"[FUNDER_INCOMING] ✅ Batch returned {len(data)} transactions", flush=True)

        for tx in data:
            if not isinstance(tx, dict):
                continue
            sig = tx.get("signature")
            if isinstance(sig, str) and sig:
                out[sig] = tx

        # any missing in this batch -> None
        for s in batch:
            out.setdefault(s, None)

    return out


# -------------------------
# Core extraction
# -------------------------

def _parse_native_transfers_from_helius_tx(tx: dict) -> Tuple[str, Optional[int], List[dict]]:
    """
    Returns (signature, timestamp, nativeTransfers list).
    """
    sig = tx.get("signature") if isinstance(tx.get("signature"), str) else ""
    ts = tx.get("timestamp")
    timestamp = int(ts) if isinstance(ts, int) else None
    native = tx.get("nativeTransfers") if isinstance(tx.get("nativeTransfers"), list) else []
    return sig, timestamp, native


def extract_transfers_for_funder(
    funder_address: str,
    *,
    helius_limit: int = DEFAULT_HELIUS_LIMIT,
    rpc_sig_limit: int = DEFAULT_RPC_SIG_LIMIT,
) -> Dict:
    """
    Extract incoming/outgoing transfers for a funder.
    - Uses cached DB results if present.
    - Otherwise fetches via Helius enriched feed (preferred).
    - Falls back to RPC signatures + Helius batch tx (if Helius available) or pure RPC (last resort).
    
    HARDENING FIX #6: Removed per-funder _ensure_tables_and_indexes call
    • Tables/indexes are now initialized at startup via extract_for_creator()
    • Eliminates redundant schema checks in hot loop (saves ~50-100ms per funder)
    """

    log_print(f"\n[EXTRACT] Analyzing funder: {funder_address}")

    # Cache check
    inc_count, out_count, total_sol_cached = _has_cached_funder_transfers(funder_address)
    if inc_count or out_count:
        log_print(f"[EXTRACT] ✅ Using cached DB data: {inc_count} IN, {out_count} OUT")
        return {
            "incoming_count": inc_count,
            "outgoing_count": out_count,
            "total_sol": total_sol_cached,
            "source": "database_cache",
            "funder": funder_address,
        }

    incoming_rows: List[Tuple] = []
    outgoing_rows: List[Tuple] = []

    # Initialize fingerprint tracking variables
    action = None
    cached_type = None
    cached_conf = None
    helius_pages = 1

    # Check fingerprint cache first (SKIP/REFRESH/FULL_SCAN decision)
    if FINGERPRINT_CLUSTER is not None:
        try:
            action, cached_type, cached_conf = FINGERPRINT_CLUSTER.lookup_wallet(funder_address)
            if action == FingerprintAction.SKIP:
                logger.info(f"[FINGERPRINT] ✅ SKIP {funder_address[:16]}... type={cached_type} conf={cached_conf:.2f}")
                # Check if we have DB cache first (from prior scan)
                inc_count, out_count, total_sol = _has_cached_funder_transfers(funder_address)
                if inc_count or out_count:
                    return {
                        "incoming_count": inc_count,
                        "outgoing_count": out_count,
                        "total_sol": total_sol,
                        "source": "fingerprint_skip_with_cache",
                        "funder": funder_address,
                    }
                else:
                    # No DB cache: only hard-skip known deferred wallets, otherwise refresh
                    if cached_type == "high_activity":
                        # Deferred large-history wallet - safe to return empty
                        logger.debug(f"[FINGERPRINT] Hard-skipping deferred high_activity wallet {funder_address[:16]}...")
                        return {
                            "incoming_count": 0,
                            "outgoing_count": 0,
                            "total_sol": 0.0,
                            "source": "fingerprint_skip_deferred",
                            "funder": funder_address,
                        }
                    else:
                        # SKIP without cache data is risky - downgrade to REFRESH
                        logger.info(f"[FINGERPRINT] No DB cache for SKIP wallet; downgrading to REFRESH")
                        action = FingerprintAction.REFRESH
                        helius_pages = 1
            elif action == FingerprintAction.REFRESH:
                logger.info(f"[FINGERPRINT] 🔄 REFRESH {funder_address[:16]}... type={cached_type} conf={cached_conf:.2f}")
                helius_pages = 1  # Light refresh: only 1 page
            else:  # FULL_SCAN
                logger.info(f"[FINGERPRINT] 🔍 FULL_SCAN {funder_address[:16]}... (confidence too low or unknown)")
                helius_pages = 1  # Standard scan
        except Exception as e:
            logger.warning(f"[FINGERPRINT] Lookup failed for {funder_address}: {e}")
            action = None
            helius_pages = 1

    # 1) Prefer Helius address tx feed
    txs = get_transactions_helius(funder_address, limit=helius_limit, max_pages=helius_pages) if USE_HELIUS else None
    source = "helius_address_feed"
    # Diagnostic logging
    log_print(f"[FUNDER_INCOMING] Address feed returned {len(txs) if txs else 0} transactions")

    # Cost control: defer large-history wallets to avoid expensive deep scans
    is_refresh = FINGERPRINT_CLUSTER is not None and action == FingerprintAction.REFRESH
    if txs and len(txs) >= helius_limit and not is_refresh:
        logger.info(f"[BUDGET] Deferring large-history wallet {funder_address[:16]}... ({len(txs)} txs, at page limit)")

        # Persist fingerprint so this wallet is not re-billed on next run
        if FINGERPRINT_CLUSTER is not None:
            try:
                FINGERPRINT_CLUSTER.save_fingerlog_print(
                    funder_address,
                    wallet_type="high_activity",
                    confidence=0.85,
                    pages_scanned=1,
                    skip_reason="deferred_large_history",
                )
                logger.debug(f"[FINGERPRINT] Marked {funder_address[:16]}... as deferred")
            except Exception as e:
                logger.warning(f"[FINGERPRINT] Save failed for deferred wallet: {e}")

        return {
            "incoming_count": 0,
            "outgoing_count": 0,
            "total_sol": 0.0,
            "source": "deferred_large_history",
            "funder": funder_address,
        }

    # 2) Fallback: RPC signatures → batch tx details (Helius) → last resort pure RPC getTransaction
    if not txs:
        try:
            # Cap signatures to control batch costs
            sig_limit = min(rpc_sig_limit, MAX_TX_SIGS_PER_FUNDER)
            sigs = get_signatures_for_address_rpc(funder_address, limit=sig_limit)
        except Exception as e:
            logger.warning(f"[HELIUS] Address feed failed: {e}")
            sigs = None

        if not sigs:
            return {"incoming_count": 0, "outgoing_count": 0, "total_sol": 0.0, "source": "no_data", "funder": funder_address}

        if USE_HELIUS:
            log_print(f"[FUNDER_INCOMING] About to call helius_batch for {len(sigs)} signatures")
            batch = helius_batch_get_transactions(sigs)
            txs = [t for t in batch.values() if isinstance(t, dict)]
            log_print(f"[FUNDER_INCOMING] Batch call completed, got {len(txs)} transactions from {len(batch)} signatures", flush=True)
            source = "helius_batch_from_rpc_sigs"
        else:
            # Pure RPC last resort: extremely slow + less accurate
            source = "rpc_only"
            txs = []
            for sig in sigs:
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTransaction",
                    "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
                }
                data = _rpc_call(payload, timeout=25.0)
                tx = data.get("result") if isinstance(data, dict) else None
                if isinstance(tx, dict):
                    # Adapt into a minimal structure we can read
                    # Note: rpc-only mode won't have nativeTransfers; we'll attempt meta-balance diffs (best-effort)
                    txs.append({"_rpc_raw": tx, "signature": sig})

    # Parse transfers
    for tx in txs:
        try:
            # Helius mode (nativeTransfers exists)
            if isinstance(tx, dict) and "nativeTransfers" in tx:
                sig, ts, native = _parse_native_transfers_from_helius_tx(tx)
                if not native:
                    continue

                for nt in native:
                    if not isinstance(nt, dict):
                        continue
                    frm = nt.get("fromUserAccount")
                    to = nt.get("toUserAccount")
                    lamports = nt.get("amount", 0)

                    if not isinstance(frm, str) or not isinstance(to, str):
                        continue
                    if not isinstance(lamports, int):
                        try:
                            lamports = int(lamports)
                        except Exception:
                            continue

                    amount_sol = lamports / LAMPORTS_PER_SOL
                    if amount_sol < MIN_SOL:
                        continue

                    # Incoming: someone -> funder
                    if to == funder_address:
                        sender_type, exch, exch_type = classify_sender(frm)
                        is_cex = 1 if sender_type == "cex" else 0
                        incoming_rows.append((frm, funder_address, amount_sol, sender_type, sig, ts, is_cex, exch, exch_type))

                    # Outgoing: funder -> someone
                    elif frm == funder_address:
                        recipient_type, exch, exch_type = classify_sender(to)
                        is_cex = 1 if recipient_type == "cex" else 0
                        outgoing_rows.append((funder_address, to, amount_sol, recipient_type, sig, ts, is_cex, exch, exch_type))

            # rpc-only mode (best-effort)
            elif isinstance(tx, dict) and tx.get("_rpc_raw"):
                raw = tx["_rpc_raw"]
                sig = tx.get("signature", "")
                block_time = raw.get("blockTime")
                ts = int(block_time) if isinstance(block_time, int) else None

                meta = raw.get("meta") or {}
                pre = meta.get("preBalances") or []
                post = meta.get("postBalances") or []
                keys = (raw.get("transaction") or {}).get("message", {}).get("accountKeys") or []

                # Find funder index
                funder_idx = None
                for i, k in enumerate(keys):
                    k_str = k.get("pubkey") if isinstance(k, dict) else str(k)
                    if k_str == funder_address:
                        funder_idx = i
                        break
                if funder_idx is None or funder_idx >= len(pre) or funder_idx >= len(post):
                    continue

                delta = int(post[funder_idx]) - int(pre[funder_idx])
                if abs(delta) < int(MIN_SOL * LAMPORTS_PER_SOL):
                    continue

                # Best-effort counterparty matching:
                # pick the largest opposite delta account (more reliable than closest-match here)
                best_idx = None
                best_amt = 0
                if delta > 0:
                    # funder gained -> sender lost most
                    for j in range(min(len(pre), len(post), len(keys))):
                        if j == funder_idx:
                            continue
                        d = int(post[j]) - int(pre[j])
                        if d < 0 and abs(d) > best_amt:
                            best_amt = abs(d)
                            best_idx = j
                    if best_idx is not None:
                        sender = keys[best_idx].get("pubkey") if isinstance(keys[best_idx], dict) else str(keys[best_idx])
                        amt = delta / LAMPORTS_PER_SOL
                        sender_type, exch, exch_type = classify_sender(sender)
                        is_cex = 1 if sender_type == "cex" else 0
                        incoming_rows.append((sender, funder_address, amt, sender_type, sig, ts, is_cex, exch, exch_type))

                else:
                    # funder lost -> recipient gained most
                    for j in range(min(len(pre), len(post), len(keys))):
                        if j == funder_idx:
                            continue
                        d = int(post[j]) - int(pre[j])
                        if d > 0 and d > best_amt:
                            best_amt = d
                            best_idx = j
                    if best_idx is not None:
                        recipient = keys[best_idx].get("pubkey") if isinstance(keys[best_idx], dict) else str(keys[best_idx])
                        amt = abs(delta) / LAMPORTS_PER_SOL
                        recipient_type, exch, exch_type = classify_sender(recipient)
                        is_cex = 1 if recipient_type == "cex" else 0
                        outgoing_rows.append((funder_address, recipient, amt, recipient_type, sig, ts, is_cex, exch, exch_type))

        except Exception:
            continue

    # Batch save (with deduplication by primary key)
    incoming_saved = 0
    outgoing_saved = 0

    if incoming_rows or outgoing_rows:
        # Deduplicate by primary key (sender_address, funder_address, transaction_signature)
        incoming_rows = list(set(incoming_rows))
        outgoing_rows = list(set(outgoing_rows))

        conn = _open_db_optimized()
        cur = conn.cursor()

        if incoming_rows:
            cur.executemany(
                """
                INSERT OR REPLACE INTO funder_incoming_transfers
                (sender_address, funder_address, amount_sol, sender_type, transaction_signature, block_time, is_cex, cex_exchange, cex_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                incoming_rows,
            )
            incoming_saved = len(incoming_rows)

        if outgoing_rows:
            cur.executemany(
                """
                INSERT OR REPLACE INTO funder_outgoing_transfers
                (funder_address, recipient_address, amount_sol, recipient_type, transaction_signature, block_time, is_cex, cex_exchange, cex_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                outgoing_rows,
            )
            outgoing_saved = len(outgoing_rows)

        conn.commit()

        # Write to transfer_index from already-assembled rows — zero extra RPC
        # incoming row: (sender, funder, amount_sol, type, sig, ts, ...)
        # outgoing row: (funder, recipient, amount_sol, type, sig, ts, ...)
        ti_rows = []
        _now = time.time()
        for r in incoming_rows:
            sig, ts, amount_sol = r[4], r[5], r[2]
            if sig and ts and ts > 0 and amount_sol > 0:
                lamports = int(amount_sol * 1_000_000_000)
                if lamports > 0:
                    ti_rows.append((sig, r[0], r[1], lamports, int(ts), _now))
        for r in outgoing_rows:
            sig, ts, amount_sol = r[4], r[5], r[2]
            if sig and ts and ts > 0 and amount_sol > 0:
                lamports = int(amount_sol * 1_000_000_000)
                if lamports > 0:
                    ti_rows.append((sig, r[0], r[1], lamports, int(ts), _now))
        if ti_rows:
            try:
                cur.executemany("""
                    INSERT OR IGNORE INTO transfer_index
                    (signature, source, destination, amount_lamports, slot, block_time, indexed_at, is_valid, transfer_type)
                    VALUES (?, ?, ?, ?, 0, ?, ?, 1, 'standard')
                """, ti_rows)
                conn.commit()
            except Exception as ti_err:
                log_print(f"[TRANSFER_INDEX] ⚠ Insert error: {ti_err}")

        conn.close()

    total_sol = float(sum(r[2] for r in incoming_rows) + sum(r[2] for r in outgoing_rows))
    log_print(f"[SUMMARY] {funder_address[:16]}... | {incoming_saved} IN, {outgoing_saved} OUT | {total_sol:.4f} SOL | source={source}")

    # Save fingerprint after scan completes (with transaction pattern analysis)
    if FINGERPRINT_CLUSTER is not None and txs:
        try:
            wallet_type, conf = _fingerprint_wallet_type_and_confidence(funder_address, txs)
            # If we have cached confidence and it's higher, keep it
            if cached_type and cached_conf is not None and cached_conf >= conf:
                wallet_type, conf = cached_type, float(cached_conf)
            FINGERPRINT_CLUSTER.save_fingerlog_print(
                funder_address,
                wallet_type=wallet_type,
                confidence=float(conf),
                pages_scanned=int(helius_pages),
                skip_reason=str(source),
            )
            logger.debug(f"[FINGERPRINT] Saved fingerprint for {funder_address[:16]}... type={wallet_type} conf={conf:.2f}")
        except Exception as e:
            logger.warning(f"[FINGERPRINT] Save failed: {e}")

    return {
        "incoming_count": incoming_saved,
        "outgoing_count": outgoing_saved,
        "total_sol": total_sol,
        "source": source,
        "funder": funder_address,
    }


# -------------------------
# Creator-level extraction (async safe)
# -------------------------

async def extract_for_creator_async(
    creator_address: str,
    *,
    concurrency: int = DEFAULT_CONCURRENCY,
    helius_limit: int = DEFAULT_HELIUS_LIMIT,
    rpc_sig_limit: int = DEFAULT_RPC_SIG_LIMIT,
) -> Dict:
    """
    Extract for all funders of a creator using bounded concurrency.
    Safe to call from within an existing event loop.
    """
    _ensure_tables_and_indexes()

    log_print(f"\n{'='*80}")
    log_print(f"[START] Extracting funder transfers (IN/OUT) for creator: {creator_address}")
    log_print(f"{'='*80}")

    funders = get_creator_funders(creator_address)
    log_print(f"[DB] Found {len(funders)} funder(s) for this creator")

    if not funders:
        return {"error": "no_funders", "creator": creator_address}

    # Cost control: separate fresh vs cached funders
    fresh_funders = []
    cached_funders = []

    for addr, amt in funders:
        inc_count, out_count, _ = _has_cached_funder_transfers(addr)
        if inc_count or out_count:
            cached_funders.append((addr, amt))
        else:
            fresh_funders.append((addr, amt))

    # Prioritize largest fresh funders first (most likely to have useful data)
    fresh_funders = sorted(fresh_funders, key=lambda x: x[1], reverse=True)[:MAX_FRESH_FUNDERS_PER_CREATOR]

    # Process cached funders directly (no threading needed - instant DB lookup)
    log_print(f"[BUDGET] Processing: {len(cached_funders)} cached + {len(fresh_funders)} fresh (limited to {MAX_FRESH_FUNDERS_PER_CREATOR} max fresh)")
    if len(funders) > len(cached_funders) + len(fresh_funders):
        skipped = len(funders) - len(cached_funders) - len(fresh_funders)
        log_print(f"[BUDGET] Skipped {skipped} fresh funders (cost control)")

    # Initialize source-level audit tracking
    source_counts = {
        "database_cache": 0,
        "fingerprint_skip_with_cache": 0,
        "fingerprint_skip_deferred": 0,
        "deferred_large_history": 0,
        "helius_address_feed": 0,
        "helius_batch_from_rpc_sigs": 0,
        "rpc_only": 0,
        "no_data": 0,
    }

    total_sol = 0.0
    total_incoming = 0
    total_outgoing = 0
    error_count = 0

    # Fast-path: cached funders (0 API cost, instant DB lookup)
    for cached_addr, _ in cached_funders:
        inc_count, out_count, cached_sol = _has_cached_funder_transfers(cached_addr)
        total_sol += cached_sol
        total_incoming += inc_count
        total_outgoing += out_count
        source_counts["database_cache"] += 1

    # Thread pool: fresh funders (API calls required)
    sem = asyncio.Semaphore(max(1, int(concurrency)))

    async def _process_one(funder_addr: str, _amount: float) -> Dict:
        async with sem:
            return await asyncio.to_thread(
                extract_transfers_for_funder,
                funder_addr,
                helius_limit=helius_limit,
                rpc_sig_limit=rpc_sig_limit,
            )

    tasks = [_process_one(addr, amt) for addr, amt in fresh_funders]
    fresh_results = await asyncio.gather(*tasks, return_exceptions=True)

    for r in fresh_results:
        if isinstance(r, Exception):
            error_count += 1
            continue
        if isinstance(r, dict):
            total_sol += float(r.get("total_sol", 0.0))
            total_incoming += int(r.get("incoming_count", 0))
            total_outgoing += int(r.get("outgoing_count", 0))

            # Track source for audit
            src = r.get("source")
            if src in source_counts:
                source_counts[src] += 1

    # Mark completion (best-effort)
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE creator_funders
            SET last_analyzed = CURRENT_TIMESTAMP
            WHERE creator_address = ?
            """,
            (creator_address,),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        log_print(f"[DB] Error marking completion: {e}")

    # Calculate explicit counters for auditing
    fresh_funders_selected = len(fresh_funders)
    fresh_funders_skipped_budget = max(0, len(funders) - len(cached_funders) - len(fresh_funders))
    fresh_funders_completed = sum(
        source_counts.get(k, 0)
        for k in (
            "helius_address_feed",
            "helius_batch_from_rpc_sigs",
            "rpc_only",
            "no_data",
            "deferred_large_history",
            "fingerprint_skip_deferred",
        )
    )

    log_print(f"\n{'='*80}")
    log_print(f"[COMPLETE] {creator_address}")
    log_print(f"  Total incoming transfers: {total_incoming}")
    log_print(f"  Total outgoing transfers: {total_outgoing}")
    log_print(f"  Total SOL traced: {total_sol:.4f}")
    log_print(f"  Cached funders: {len(cached_funders)}")
    log_print(f"  Fresh funders selected: {fresh_funders_selected}")
    log_print(f"  Fresh funders completed: {fresh_funders_completed}")
    log_print(f"  Fresh funders skipped (budget): {fresh_funders_skipped_budget}")
    if error_count:
        log_print(f"  ⚠ {error_count} errors during processing")
    log_print(f"\n  Source breakdown:")
    for source, count in sorted(source_counts.items()):
        if count > 0:
            log_print(f"    {source}: {count}")
    log_print(f"{'='*80}\n")

    return {
        "creator": creator_address,
        "incoming_found": total_incoming,
        "outgoing_found": total_outgoing,
        "total_sol": total_sol,
        "errors": error_count,
        "status": "complete",
        "cached_funders": len(cached_funders),
        "fresh_funders_selected": fresh_funders_selected,
        "fresh_funders_completed": fresh_funders_completed,
        "fresh_funders_skipped_budget": fresh_funders_skipped_budget,
        "source_breakdown": {k: v for k, v in source_counts.items() if v > 0},
    }


def extract_for_creator(creator_address: str) -> Dict:
    """
    Sync wrapper.
    - If no event loop is running: runs async extraction normally.
    - If called from an existing loop, instruct caller to use extract_for_creator_async().
    """
    try:
        loop = asyncio.get_running_loop()
        # If we’re here, we’re already inside an event loop
        raise RuntimeError(
            "extract_for_creator() called inside a running event loop. "
            "Use: await extract_for_creator_async(creator_address)"
        )
    except RuntimeError as e:
        # No running loop -> safe to run
        if "no running event loop" in str(e).lower():
            return asyncio.run(extract_for_creator_async(creator_address))
        # Running loop -> propagate the helpful error
        raise


# -------------------------
# CLI
# -------------------------

if __name__ == "__main__":
    if len(sys.argv) < 2:
        log_print("Usage: python3 funder_incoming_extractor.py <creator_address>")
        sys.exit(1)

    creator = sys.argv[1].strip()
    result = extract_for_creator(creator)
    log_print(result)