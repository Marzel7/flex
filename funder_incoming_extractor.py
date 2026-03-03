#!/usr/bin/env python3
"""
Extract and save funder transfers (both incoming and outgoing) to database.

SAFEST/RELIABLE (but still fast) version:
- Prefer Helius enriched tx feed when available (nativeTransfers already parsed)
- Fall back to Solana RPC only if Helius unavailable/fails
- Shared HTTP session w/ retries + exponential backoff
- Bounded concurrency for multi-funder processing (default 4)
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
from typing import Dict, List, Tuple, Optional, Iterable
from functools import lru_cache
import requests

sys.path.insert(0, "/Users/kevinkeaveney/Dev/claude/flex")

from db_locking import DB_WRITE_LOCK
from infra_mapping import get_account_info, get_cex_info  # type: ignore

# Import RPC metrics recorder for monitoring
try:
    from rpc_metrics_recorder import record_request, initialize_recorder
    initialize_recorder(plan_monthly_credits=50_000_000)
except ImportError:
    def record_request(*args, **kwargs):
        pass  # No-op if metrics recorder not available

# Env
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except Exception:
    pass

DB_PATH = "flex_complete_database.db"
SOLANA_RPC = "https://api.mainnet-beta.solana.com"
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "").strip()

LAMPORTS_PER_SOL = 1_000_000_000
USE_HELIUS = bool(HELIUS_API_KEY)

# Reliability defaults
MIN_SOL = 0.001
DEFAULT_HELIUS_LIMIT = 100        # Helius endpoint commonly maxes at 100
DEFAULT_RPC_SIG_LIMIT = 200       # keep bounded
MAX_HTTP_RETRIES = 5
MAX_RPC_RETRIES = 4
BASE_BACKOFF_SECS = 0.5

# Concurrency: reliable but still fast
DEFAULT_CONCURRENCY = 4

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
        print(f"[DB] Error getting funders: {e}")
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


def _request_json(method: str, url: str, *, json_body: Optional[dict] = None, timeout: float = 20.0, batch_size: int = 1) -> Optional[object]:
    """
    Reliable HTTP call with retry/backoff on 429/5xx and network errors.

    HARDENING FIX #2: Selective transient 4xx retry
    • Retryable 4xx: 408, 409, 423, 425 (timeout, conflict, locked, too early)
    • 429 rate-limit: handled separately with Retry-After header support
    • Non-retryable 4xx: 400, 401, 403, 404, etc. (client responsibility)

    Args:
        batch_size: For batch requests (e.g., Helius batch transactions), number of items in batch.
                   This multiplies the recorded credits since Helius charges per-item.
    """
    for attempt in range(MAX_HTTP_RETRIES):
        try:
            # Determine RPC method from URL or payload
            rpc_method = "unknown"
            if "helius" in url:
                rpc_method = "helius_enhanced_transactions_batch" if batch_size > 1 else "helius_addresses_transactions"
            elif method.upper() == "POST" and json_body:
                rpc_method = json_body.get("method", "unknown")

            start_time = time.time()
            if method.upper() == "GET":
                resp = SESSION.get(url, timeout=timeout)
            else:
                resp = SESSION.post(url, json=json_body, timeout=timeout)

            latency_ms = (time.time() - start_time) * 1000

            # Record metrics for all responses
            provider = "helius_rpc" if "helius" in url else "solana_rpc"
            # For batch requests, multiply credits by batch size (Helius charges per-item)
            credits = record_request(
                section="funder_incoming",
                provider=provider,
                method=rpc_method,
                status_code=resp.status_code,
                latency_ms=latency_ms,
                mode="realtime",
                retries=attempt,
            )
            # Apply batch multiplier (e.g., 100 txs in batch = 100x credit cost)
            if batch_size > 1 and credits > 0:
                # Already recorded once, need to add (batch_size - 1) more times
                for _ in range(batch_size - 1):
                    record_request(
                        section="funder_incoming",
                        provider=provider,
                        method=rpc_method,
                        status_code=resp.status_code,
                        latency_ms=latency_ms,
                        mode="realtime",
                        retries=attempt,
                    )

            if resp.status_code == 429:
                ra = resp.headers.get("Retry-After")
                retry_after = None
                if ra:
                    try:
                        retry_after = float(ra)
                    except (ValueError, TypeError):
                        retry_after = None
                print(f"[HTTP] 429 rate-limited. Backing off (attempt {attempt+1}/{MAX_HTTP_RETRIES})")
                _sleep_backoff(attempt, retry_after=retry_after)
                continue

            if resp.status_code >= 500:
                print(f"[HTTP] {resp.status_code} server error. Backing off (attempt {attempt+1}/{MAX_HTTP_RETRIES})")
                _sleep_backoff(attempt)
                continue

            # Selective transient 4xx retry
            if resp.status_code in TRANSIENT_HTTP_CODES:
                print(f"[HTTP] {resp.status_code} transient error. Backing off (attempt {attempt+1}/{MAX_HTTP_RETRIES})")
                _sleep_backoff(attempt)
                continue

            if resp.status_code != 200:
                # Permanent client errors: don't retry
                try:
                    txt = resp.text[:300]
                except Exception:
                    txt = ""
                print(f"[HTTP] Non-200 ({resp.status_code}). Body: {txt}")
                return None

            return resp.json()

        except (requests.Timeout, requests.ConnectionError) as e:
            rpc_method = "unknown"
            if "helius" in url:
                rpc_method = "helius_addresses_transactions"
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
            print(f"[HTTP] Network error: {e}. Backing off (attempt {attempt+1}/{MAX_HTTP_RETRIES})")
            _sleep_backoff(attempt)
            continue
        except Exception as e:
            print(f"[HTTP] Unexpected error: {e}")
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

    for _ in range(max_pages):
        url = (
            f"https://api.helius.xyz/v0/addresses/{address}/transactions"
            f"?api-key={HELIUS_API_KEY}&limit={lim}&before={all_txs[-1].get('signature', '') if all_txs else ''}"
        )
        data = _request_json("GET", url, timeout=25.0)
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
            # Record metric  for all RPC responses
            record_request(
                section="funder_incoming",
                provider="solana_rpc",
                method=rpc_method,
                status_code=resp.status_code,
                latency_ms=latency_ms,
                mode="realtime",
                retries=attempt,
            )

            if resp.status_code == 429:
                print(f"[RPC] 429 rate-limited. Backing off (attempt {attempt+1}/{MAX_RPC_RETRIES})")
                _sleep_backoff(attempt)
                continue
            if resp.status_code >= 500:
                print(f"[RPC] {resp.status_code} server error. Backing off (attempt {attempt+1}/{MAX_RPC_RETRIES})")
                _sleep_backoff(attempt)
                continue
            if resp.status_code != 200:
                return None

            data = resp.json()
            if isinstance(data, dict) and data.get("error"):
                error_obj = data["error"]
                if _is_rpc_error_retryable(error_obj):
                    print(f"[RPC] Transient error (code={error_obj.get('code')}). Backing off (attempt {attempt+1}/{MAX_RPC_RETRIES})")
                    _sleep_backoff(attempt)
                    continue
                else:
                    # Permanent error: fail fast
                    print(f"[RPC] Permanent error (code={error_obj.get('code')}): {error_obj.get('message')}")
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
            print(f"[RPC] Network error: {e}. Backing off (attempt {attempt+1}/{MAX_RPC_RETRIES})")
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
        data = _request_json("POST", url, json_body={"transactions": batch}, timeout=35.0, batch_size=len(batch))
        if not isinstance(data, list):
            # mark batch unknown
            for s in batch:
                out[s] = None
            continue

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

    print(f"\n[EXTRACT] Analyzing funder: {funder_address}")

    # Cache check
    inc_count, out_count, total_sol_cached = _has_cached_funder_transfers(funder_address)
    if inc_count or out_count:
        print(f"[EXTRACT] ✅ Using cached DB data: {inc_count} IN, {out_count} OUT")
        return {
            "incoming_count": inc_count,
            "outgoing_count": out_count,
            "total_sol": total_sol_cached,
            "source": "database_cache",
            "funder": funder_address,
        }

    incoming_rows: List[Tuple] = []
    outgoing_rows: List[Tuple] = []

    # 1) Prefer Helius address tx feed
    txs = get_transactions_helius(funder_address, limit=helius_limit) if USE_HELIUS else None
    source = "helius_address_feed"

    # 2) Fallback: RPC signatures → batch tx details (Helius) → last resort pure RPC getTransaction
    if not txs:
        sigs = get_signatures_for_address_rpc(funder_address, limit=rpc_sig_limit)
        if not sigs:
            return {"incoming_count": 0, "outgoing_count": 0, "total_sol": 0.0, "source": "no_data", "funder": funder_address}

        if USE_HELIUS:
            batch = helius_batch_get_transactions(sigs)
            txs = [t for t in batch.values() if isinstance(t, dict)]
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
        conn.close()

    total_sol = float(sum(r[2] for r in incoming_rows) + sum(r[2] for r in outgoing_rows))
    print(f"[SUMMARY] {funder_address[:16]}... | {incoming_saved} IN, {outgoing_saved} OUT | {total_sol:.4f} SOL | source={source}")

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

    print(f"\n{'='*80}")
    print(f"[START] Extracting funder transfers (IN/OUT) for creator: {creator_address}")
    print(f"{'='*80}")

    funders = get_creator_funders(creator_address)
    print(f"[DB] Found {len(funders)} funder(s) for this creator")

    if not funders:
        return {"error": "no_funders", "creator": creator_address}

    sem = asyncio.Semaphore(max(1, int(concurrency)))

    async def _process_one(funder_addr: str, _amount: float) -> Dict:
        async with sem:
            return await asyncio.to_thread(
                extract_transfers_for_funder,
                funder_addr,
                helius_limit=helius_limit,
                rpc_sig_limit=rpc_sig_limit,
            )

    tasks = [_process_one(addr, amt) for addr, amt in funders]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    total_sol = 0.0
    total_incoming = 0
    total_outgoing = 0
    error_count = 0

    for r in results:
        if isinstance(r, Exception):
            error_count += 1
            continue
        if isinstance(r, dict):
            total_sol += float(r.get("total_sol", 0.0))
            total_incoming += int(r.get("incoming_count", 0))
            total_outgoing += int(r.get("outgoing_count", 0))

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
        print(f"[DB] Error marking completion: {e}")

    print(f"\n{'='*80}")
    print(f"[COMPLETE] {creator_address}")
    print(f"  Total incoming transfers: {total_incoming}")
    print(f"  Total outgoing transfers: {total_outgoing}")
    print(f"  Total SOL traced: {total_sol:.4f}")
    if error_count:
        print(f"  ⚠ {error_count} errors during processing")
    print(f"{'='*80}\n")

    return {
        "creator": creator_address,
        "incoming_found": total_incoming,
        "outgoing_found": total_outgoing,
        "total_sol": total_sol,
        "errors": error_count,
        "status": "complete",
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
        print("Usage: python3 funder_incoming_extractor.py <creator_address>")
        sys.exit(1)

    creator = sys.argv[1].strip()
    result = extract_for_creator(creator)
    print(result)