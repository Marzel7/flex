"""
CreatorOutboundWorker — Phase 3 RPC worker.

Picks approved/high-priority creators from creator_outbound_queue and uses
getSignaturesForAddress + getTransaction to discover outbound SOL transfers
not yet present in creator_outgoing_transfers.

Results are written to creator_outgoing_transfers (existing table).
CreatorOutboundBuilder then classifies them into creator_outbound_classifications.

Feature flag: CREATOR_OUTBOUND_ENABLED=true  (default false)

Config (env overridable):
    MAX_CREATORS_PER_RUN  = 20
    MAX_RPC_CALLS_PER_RUN = 2000
    LOOKBACK_TXS          = 100     signatures per creator
    MIN_OUTBOUND_SOL      = 0.1
"""

import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

# ── Feature flag ──────────────────────────────────────────────────────────────
CREATOR_OUTBOUND_ENABLED = (
    os.getenv("CREATOR_OUTBOUND_ENABLED", "false").lower() == "true"
)

# ── Config ─────────────────────────────────────────────────────────────────────
MAX_CREATORS_PER_RUN  = int(os.getenv("COW_MAX_CREATORS", "20"))
MAX_RPC_CALLS_PER_RUN = int(os.getenv("COW_MAX_RPC_CALLS", "2000"))
LOOKBACK_TXS          = int(os.getenv("COW_LOOKBACK_TXS", "100"))
MIN_OUTBOUND_SOL      = float(os.getenv("COW_MIN_OUTBOUND_SOL", "0.1"))
CACHE_TTL_SECONDS     = int(os.getenv("COW_CACHE_TTL_HOURS", "168")) * 3600

# ── Program accounts to exclude as recipients ─────────────────────────────────
SYSTEM_PROGRAM = "11111111111111111111111111111111"

EXCLUDED_RECIPIENTS: frozenset = frozenset({
    SYSTEM_PROGRAM,
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",   # pump.fun
    "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",   # PumpSwap
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",   # SPL Token
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",   # Token-2022
    "9DCxsMizn3H1hprZ7xWe6LDzeUeZBksYFpBWBtSf5PNT",  # pump.fun migration
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",  # Raydium AMM v4
    "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C",  # Raydium CPMM
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJe1brs",  # ATA program
})


def _resolve_rpc_url() -> Optional[str]:
    for key in ("RPC_HTTP", "RPC_URL", "HELIUS_RPC_URL"):
        val = os.environ.get(key)
        if val:
            return val
    env_file = Path(__file__).resolve().parent.parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip().lstrip("export").strip()
            for key in ("RPC_HTTP", "RPC_URL", "HELIUS_RPC_URL"):
                if line.startswith(key + "="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val:
                        return val
    return None


def apply_migration(db_path: str) -> None:
    migration = (
        Path(__file__).resolve().parent.parent.parent
        / "database" / "migrations" / "add_creator_outbound.sql"
    )
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    for stmt in migration.read_text().split(";"):
        stmt = stmt.strip()
        if stmt:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError as e:
                if "already exists" not in str(e).lower():
                    logger.warning(f"[COW] Migration warning: {e}")
    conn.commit()
    conn.close()


class CreatorOutboundWorker:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.rpc_url = _resolve_rpc_url()
        self._rpc_calls_used = 0

    # ── Public ─────────────────────────────────────────────────────────────────

    def run(self, force: bool = False) -> dict:
        t0 = time.time()
        apply_migration(self.db_path)

        if not force and not CREATOR_OUTBOUND_ENABLED:
            logger.info("[COW] Creator outbound scanning disabled — skipping")
            return {"status": "skipped", "reason": "disabled",
                    "creators_scanned": 0, "transfers_written": 0, "rpc_calls_used": 0}

        if not self.rpc_url:
            logger.error("[COW] No RPC URL configured")
            return {"status": "failed", "reason": "no_rpc_url",
                    "creators_scanned": 0, "transfers_written": 0, "rpc_calls_used": 0}

        self._rpc_calls_used = 0
        creators_scanned = 0
        transfers_written = 0
        errors = 0

        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row

        # Load bonding curve / pool addresses to exclude as recipients
        self._excluded_addrs: set = EXCLUDED_RECIPIENTS | {
            row[0] for row in conn.execute("""
                SELECT bonding_curve_pda FROM token_analysis WHERE bonding_curve_pda IS NOT NULL
                UNION
                SELECT pool_address FROM token_analysis WHERE pool_address IS NOT NULL
                UNION
                SELECT pumpswap_pool_address FROM token_analysis WHERE pumpswap_pool_address IS NOT NULL
            """).fetchall()
        }

        # Load known creator addresses for return-to-funder classification hint
        self._known_creators: set = {
            row[0] for row in conn.execute(
                "SELECT DISTINCT earliest_tx_creator FROM token_analysis WHERE earliest_tx_creator IS NOT NULL"
            ).fetchall()
        }

        try:
            creators = self._pick_creators(conn)
            logger.info(f"[COW] Picked {len(creators)} creators to scan")

            for creator in creators:
                if self._rpc_calls_used >= MAX_RPC_CALLS_PER_RUN:
                    logger.info("[COW] RPC cap reached — stopping")
                    break
                if creators_scanned >= MAX_CREATORS_PER_RUN:
                    break

                try:
                    written = self._scan_creator(conn, creator)
                    transfers_written += written
                    creators_scanned += 1
                    logger.info(f"[COW] {creator[:12]}… — transfers_written={written} rpc={self._rpc_calls_used}")
                except Exception as exc:
                    errors += 1
                    logger.warning(f"[COW] {creator[:12]}… — error: {exc}", exc_info=True)
                    self._mark_failed(conn, creator, str(exc))

            conn.commit()
        finally:
            conn.close()

        duration = round(time.time() - t0, 2)
        logger.info(
            f"[COW] Done — creators={creators_scanned} transfers={transfers_written} "
            f"rpc={self._rpc_calls_used} errors={errors} duration={duration}s"
        )
        return {
            "status": "success",
            "creators_scanned": creators_scanned,
            "transfers_written": transfers_written,
            "rpc_calls_used": self._rpc_calls_used,
            "errors": errors,
            "duration_seconds": duration,
        }

    # ── Queue management ───────────────────────────────────────────────────────

    def _pick_creators(self, conn: sqlite3.Connection) -> List[str]:
        now = int(time.time())
        rows = conn.execute("""
            SELECT creator_address FROM creator_outbound_queue
            WHERE status = 'pending'
              AND next_attempt_at <= ?
            ORDER BY priority DESC, created_at ASC
            LIMIT ?
        """, (now, MAX_CREATORS_PER_RUN)).fetchall()
        return [r[0] for r in rows]

    def _mark_scanning(self, conn: sqlite3.Connection, creator: str) -> None:
        conn.execute(
            "UPDATE creator_outbound_queue SET status='scanning', attempt_count=attempt_count+1 WHERE creator_address=?",
            (creator,)
        )

    def _mark_done(self, conn: sqlite3.Connection, creator: str) -> None:
        now = int(time.time())
        next_rescan = now + CACHE_TTL_SECONDS
        conn.execute("""
            UPDATE creator_outbound_queue
            SET status='done', scanned_at=?, next_attempt_at=?, last_error=NULL
            WHERE creator_address=?
        """, (now, next_rescan, creator))

    def _mark_failed(self, conn: sqlite3.Connection, creator: str, error: str) -> None:
        now = int(time.time())
        retry_delay = 3600  # 1 hour back-off
        conn.execute("""
            UPDATE creator_outbound_queue
            SET status='pending', next_attempt_at=?, last_error=?
            WHERE creator_address=?
        """, (now + retry_delay, error[:500], creator))

    # ── Core scan ─────────────────────────────────────────────────────────────

    def _scan_creator(self, conn: sqlite3.Connection, creator: str) -> int:
        """Scan one creator's recent outbound transfers. Returns rows written."""
        self._mark_scanning(conn, creator)

        if self._rpc_calls_used >= MAX_RPC_CALLS_PER_RUN:
            return 0

        # getSignaturesForAddress
        t0 = time.time()
        sig_resp = self._rpc_post({
            "jsonrpc": "2.0", "id": 1,
            "method": "getSignaturesForAddress",
            "params": [creator, {"limit": LOOKBACK_TXS}],
        })
        self._rpc_calls_used += 1

        if not sig_resp or not sig_resp.get("result"):
            self._mark_done(conn, creator)
            return 0

        signatures = [
            s["signature"] for s in sig_resp["result"]
            if s.get("err") is None and s.get("signature")
        ]

        # Skip sigs already stored for this creator
        existing_sigs = {
            row[0] for row in conn.execute(
                "SELECT transaction_signature FROM creator_outgoing_transfers WHERE creator_address=?",
                (creator,)
            ).fetchall()
        }
        new_sigs = [s for s in signatures if s not in existing_sigs]

        transfers_written = 0
        for sig in new_sigs:
            if self._rpc_calls_used >= MAX_RPC_CALLS_PER_RUN:
                break

            tx_resp = self._rpc_post({
                "jsonrpc": "2.0", "id": 1,
                "method": "getTransaction",
                "params": [sig, {"encoding": "json", "maxSupportedTransactionVersion": 0}],
            })
            self._rpc_calls_used += 1

            if not tx_resp or not tx_resp.get("result"):
                continue

            written = self._extract_and_store(conn, creator, sig, tx_resp["result"])
            transfers_written += written

        self._mark_done(conn, creator)
        return transfers_written

    def _extract_and_store(self, conn: sqlite3.Connection, creator: str,
                           sig: str, tx_data: dict) -> int:
        """Parse a transaction and write outbound SOL transfers from creator."""
        meta = tx_data.get("meta") or {}
        tx = tx_data.get("transaction") or {}
        block_time = tx_data.get("blockTime")

        if meta.get("err") is not None:
            return 0

        account_keys = tx.get("message", {}).get("accountKeys") or []
        pre_balances = meta.get("preBalances") or []
        post_balances = meta.get("postBalances") or []

        if not account_keys or len(pre_balances) != len(account_keys):
            return 0

        # Find creator's index in this transaction
        try:
            creator_idx = account_keys.index(creator)
        except ValueError:
            return 0

        creator_pre = pre_balances[creator_idx]
        creator_post = post_balances[creator_idx]

        # Creator must be a net sender in this tx
        if creator_post >= creator_pre:
            return 0

        written = 0
        for i, recipient in enumerate(account_keys):
            if i == creator_idx:
                continue
            if recipient in self._excluded_addrs:
                continue

            pre = pre_balances[i] if i < len(pre_balances) else 0
            post = post_balances[i] if i < len(post_balances) else 0
            delta_sol = (post - pre) / 1e9

            if delta_sol < MIN_OUTBOUND_SOL:
                continue

            try:
                conn.execute("""
                    INSERT OR IGNORE INTO creator_outgoing_transfers
                        (creator_address, recipient_address, amount_sol,
                         transaction_signature, block_time, recipient_type, is_cex)
                    VALUES (?, ?, ?, ?, ?, 'unknown', 0)
                """, (creator, recipient, round(delta_sol, 9), sig, block_time))
                if conn.execute("SELECT changes()").fetchone()[0]:
                    written += 1
            except sqlite3.Error:
                pass

        return written

    # ── RPC ───────────────────────────────────────────────────────────────────

    def _rpc_post(self, payload: dict, timeout: int = 15) -> Optional[dict]:
        try:
            resp = requests.post(
                self.rpc_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=timeout,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.debug(f"[COW] RPC error: {exc}")
            return None


def enqueue_creator_for_outbound_scan(
    conn: sqlite3.Connection,
    creator_address: str,
    priority: int = 0,
) -> bool:
    """
    Enqueue a creator for outbound RPC scanning only if they already have at least
    one classification in creator_outbound_classifications. This gates speculative
    RPC scans — we only deepen confirmed signals, not fish blind.
    Returns True if enqueued.
    """
    has_signal = conn.execute(
        "SELECT 1 FROM creator_outbound_classifications WHERE creator_address = ? LIMIT 1",
        (creator_address,)
    ).fetchone()
    if not has_signal:
        return False

    now = int(time.time())
    try:
        conn.execute("""
            INSERT INTO creator_outbound_queue (creator_address, priority, status, created_at)
            VALUES (?, ?, 'pending', ?)
            ON CONFLICT(creator_address) DO UPDATE SET
                priority = MAX(excluded.priority, creator_outbound_queue.priority)
            WHERE creator_outbound_queue.status IN ('done', 'failed')
        """, (creator_address, priority, now))
        return True
    except sqlite3.Error:
        return False
