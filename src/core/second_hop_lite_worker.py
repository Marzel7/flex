"""
SecondHopLiteWorker — Phase 2 Lite RPC worker.

Picks priority funders from second_hop_lite_queue and uses Helius RPC to
discover inbound SOL transfers not present in transfer_index.  Results are
written to funder_upstream_links with source='rpc_backfill', allowing the
existing SecondHopExpansionBuilder to incorporate them on its next run.

Feature flag: SECOND_HOP_RPC_LITE_ENABLED=false  (default off)

Config (env overridable):
    MAX_FUNDER_SCANS_PER_RUN  = 10
    MAX_RPC_CALLS_PER_RUN     = 300
    MAX_TX_PER_FUNDER         = 50
    LOOKBACK_DAYS             = 30
    CACHE_TTL_HOURS           = 168   (7 days)
"""

import json
import logging
import os
import sqlite3
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

# ── Feature flag ──────────────────────────────────────────────────────────────
SECOND_HOP_RPC_LITE_ENABLED = (
    os.getenv("SECOND_HOP_RPC_LITE_ENABLED", "false").lower() == "true"
)

# ── Config ────────────────────────────────────────────────────────────────────
MAX_FUNDER_SCANS_PER_RUN = int(os.getenv("SHL_MAX_FUNDER_SCANS", "2000"))
MAX_RPC_CALLS_PER_RUN    = int(os.getenv("SHL_MAX_RPC_CALLS", "20000"))
MAX_TX_PER_FUNDER        = int(os.getenv("SHL_MAX_TX_PER_FUNDER", "50"))
LOOKBACK_DAYS            = int(os.getenv("SHL_LOOKBACK_DAYS", "30"))
CACHE_TTL_HOURS          = int(os.getenv("SHL_CACHE_TTL_HOURS", "168"))

# ── RPC URL resolution (same priority as main.py) ─────────────────────────────
def _resolve_rpc_url() -> Optional[str]:
    for key in ("RPC_HTTP", "RPC_URL", "HELIUS_RPC_URL"):
        val = os.environ.get(key)
        if val:
            return val
    # Fall back to .env file if not in environment
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

# ── Migration SQL path ────────────────────────────────────────────────────────
_MIGRATION_SQL = (
    Path(__file__).resolve().parent.parent.parent
    / "database" / "migrations" / "add_second_hop_lite.sql"
)

MIN_UPSTREAM_SOL = 0.01
MAX_UPSTREAM_SOL = 1000.0
TX_BATCH_SIZE    = 10      # getTransaction calls per batch

# ── Account classification ────────────────────────────────────────────────────
MAX_ACCOUNT_INFO_CHECKS_PER_RUN = int(os.getenv("SHL_MAX_ACCOUNT_CHECKS", "20"))

SYSTEM_PROGRAM    = "11111111111111111111111111111111"
SPL_TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022        = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"

# Any account owned by these programs is NOT a real wallet
EXCLUDED_OWNER_PROGRAMS: frozenset = frozenset({
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",  # pump.fun
    "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",  # PumpSwap
    SPL_TOKEN_PROGRAM,
    TOKEN_2022,
    "9DCxsMizn3H1hprZ7xWe6LDzeUeZBksYFpBWBtSf5PNT",  # pump.fun migration program
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",  # Raydium AMM v4
    "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C",  # Raydium CPMM
    "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",   # Orca Whirlpool
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJe1brs",  # Associated Token Account program
    "BPFLoaderUpgradeab1e11111111111111111111111",     # BPF Loader
    "Vote111111111111111111111111111111111111111h",    # Vote program
})


class SecondHopLiteWorker:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.rpc_url = _resolve_rpc_url()
        self._rpc_calls_used = 0

    # ── Public ────────────────────────────────────────────────────────────────

    def _is_enabled(self, conn) -> bool:
        """Check DB setting first, fall back to env var."""
        try:
            row = conn.execute(
                "SELECT setting_value FROM listener_settings WHERE setting_key='second_hop_lite_enabled'"
            ).fetchone()
            if row is not None:
                return row[0] == 'true'
        except Exception:
            pass
        return SECOND_HOP_RPC_LITE_ENABLED

    def run(self, force: bool = False) -> dict:
        t0 = time.time()
        self._apply_migrations()

        if not force:
            conn_check = sqlite3.connect(self.db_path, timeout=10)
            enabled = self._is_enabled(conn_check)
            conn_check.close()
        else:
            enabled = True

        if not enabled:
            logger.info("[SHL-Worker] Phase 2 Lite disabled — skipping RPC")
            return {
                "status": "skipped",
                "reason": "disabled",
                "funders_scanned": 0,
                "links_written": 0,
                "rpc_calls_used": 0,
                "duration_seconds": 0,
            }

        if not self.rpc_url:
            logger.error("[SHL-Worker] No RPC URL configured — set RPC_HTTP, RPC_URL, or HELIUS_RPC_URL")
            return {
                "status": "failed",
                "reason": "no_rpc_url",
                "funders_scanned": 0,
                "links_written": 0,
                "rpc_calls_used": 0,
                "duration_seconds": round(time.time() - t0, 2),
            }

        self._rpc_calls_used = 0
        self._account_checks_used = 0
        funders_scanned = 0
        links_written = 0
        errors = 0

        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row

        # Load all known program/pool addresses to exclude as upstream
        self._bonding_curve_pdas: set = {
            row[0] for row in conn.execute("""
                SELECT bonding_curve_pda FROM token_analysis WHERE bonding_curve_pda IS NOT NULL
                UNION
                SELECT pool_address FROM token_analysis WHERE pool_address IS NOT NULL
                UNION
                SELECT pumpswap_pool_address FROM token_analysis WHERE pumpswap_pool_address IS NOT NULL
                UNION
                SELECT address FROM shl_excluded_upstreams
            """).fetchall()
        }

        # Reset done funders older than 7 days back to pending for rescan
        ttl_cutoff = int(time.time()) - (7 * 86400)
        reset = conn.execute("""
            UPDATE second_hop_lite_queue
            SET status='pending', next_attempt_at=0
            WHERE status='done' AND scanned_at < ?
        """, (ttl_cutoff,)).rowcount
        if reset:
            logger.info(f"[SHL-Worker] Reset {reset} funders for rescan (TTL expired)")
        conn.commit()

        # Snapshot before scanning so new links are caught by diff
        try:
            from src.core.relationship_events import take_snapshot as _take_snapshot
            _before_snapshot = _take_snapshot(self.db_path)
        except Exception:
            _before_snapshot = {}

        try:
            funders = self._pick_funders(conn)
            logger.info(f"[SHL-Worker] Picked {len(funders)} funders to scan")

            for funder_address in funders:
                if self._rpc_calls_used >= MAX_RPC_CALLS_PER_RUN:
                    logger.info("[SHL-Worker] RPC cap reached — stopping")
                    break
                if funders_scanned >= MAX_FUNDER_SCANS_PER_RUN:
                    break

                try:
                    written = self._scan_funder(conn, funder_address)
                    links_written += written
                    funders_scanned += 1
                    conn.commit()  # commit after each funder so kills don't lose progress
                except Exception as exc:
                    logger.error(f"[SHL-Worker] Error scanning {funder_address}: {exc}", exc_info=True)
                    self._mark_failed(conn, funder_address, str(exc))
                    errors += 1

            conn.commit()
        finally:
            conn.close()

        duration = round(time.time() - t0, 2)
        logger.info(
            f"[SHL-Worker] Done — funders={funders_scanned} "
            f"links={links_written} rpc={self._rpc_calls_used} "
            f"errors={errors} duration={duration}s"
        )

        # Trigger lightweight rebuild + relationship event logging if any work was done
        rebuild_result = {}
        if funders_scanned > 0:
            try:
                from src.core.relationship_events import rebuild_after_scan
                scan_id = f"shl_{int(time.time())}"
                rebuild_result = rebuild_after_scan(self.db_path, scan_id=scan_id, before=_before_snapshot)
            except Exception as _rb_exc:
                logger.warning(f"[SHL-Worker] rebuild_after_scan failed (non-fatal): {_rb_exc}")

        return {
            "status": "success",
            "funders_scanned": funders_scanned,
            "links_written": links_written,
            "rpc_calls_used": self._rpc_calls_used,
            "errors": errors,
            "duration_seconds": duration,
            "rebuild": rebuild_result,
        }

    # ── Migrations ────────────────────────────────────────────────────────────

    def _apply_migrations(self) -> None:
        try:
            sql = _MIGRATION_SQL.read_text(encoding="utf-8")
            conn = sqlite3.connect(self.db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            # Execute each statement separately (sqlite3 doesn't support executescript in WAL mode well)
            for stmt in sql.split(";"):
                stmt = stmt.strip()
                if stmt:
                    try:
                        conn.execute(stmt)
                    except sqlite3.OperationalError as e:
                        # IF NOT EXISTS guards mean duplicates are safe
                        if "already exists" not in str(e).lower():
                            logger.warning(f"[SHL-Worker] Migration statement warning: {e}")
            conn.commit()
            conn.close()
            logger.debug("[SHL-Worker] Migrations applied")
        except Exception as exc:
            logger.error(f"[SHL-Worker] Migration failed: {exc}", exc_info=True)

    # ── Queue management ──────────────────────────────────────────────────────

    def _pick_funders(self, conn: sqlite3.Connection) -> List[str]:
        """Return up to MAX_FUNDER_SCANS_PER_RUN funders ready for scanning."""
        now = int(time.time())
        try:
            rows = conn.execute("""
                SELECT funder_address
                FROM second_hop_lite_queue
                WHERE status IN ('pending', 'failed')
                  AND next_attempt_at <= ?
                ORDER BY priority DESC, created_at ASC
                LIMIT ?
            """, (now, MAX_FUNDER_SCANS_PER_RUN)).fetchall()
            return [r["funder_address"] for r in rows]
        except sqlite3.OperationalError:
            # Table not yet created
            return []

    def _mark_scanning(self, conn: sqlite3.Connection, funder: str) -> None:
        try:
            conn.execute("""
                UPDATE second_hop_lite_queue
                SET status='scanning', attempts=attempts+1
                WHERE funder_address=?
            """, (funder,))
        except sqlite3.OperationalError:
            pass

    def _mark_done(self, conn: sqlite3.Connection, funder: str, rpc_calls: int) -> None:
        now = int(time.time())
        try:
            conn.execute("""
                UPDATE second_hop_lite_queue
                SET status='done', scanned_at=?, rpc_calls_used=?
                WHERE funder_address=?
            """, (now, rpc_calls, funder))
        except sqlite3.OperationalError:
            pass

    def _mark_failed(self, conn: sqlite3.Connection, funder: str, error: str) -> None:
        now = int(time.time())
        # Back-off: retry after 1 hour
        next_attempt = now + 3600
        try:
            conn.execute("""
                UPDATE second_hop_lite_queue
                SET status='failed', last_error=?, next_attempt_at=?
                WHERE funder_address=?
            """, (error[:500], next_attempt, funder))
        except sqlite3.OperationalError:
            pass

    # ── Per-funder scan ───────────────────────────────────────────────────────

    def _scan_funder(self, conn: sqlite3.Connection, funder_address: str) -> int:
        """
        Scan one funder.  Returns number of funder_upstream_links rows written.
        """
        # ── 1. Check cache ────────────────────────────────────────────────────
        now = int(time.time())
        cached = self._check_cache(conn, funder_address, now)
        if cached is not None:
            self._log_rpc(conn, funder_address, "cache_check", True, 0, None, True)
            logger.debug(f"[SHL-Worker] Cache hit for {funder_address[:12]}…")
            self._mark_done(conn, funder_address, 0)
            return 0

        self._mark_scanning(conn, funder_address)

        rpc_calls_this_funder = 0
        cutoff_ts = now - (LOOKBACK_DAYS * 86400)

        # ── 2. getSignaturesForAddress ────────────────────────────────────────
        if self._rpc_calls_used >= MAX_RPC_CALLS_PER_RUN:
            return 0

        t_sig = time.time()
        sig_resp = self._rpc_post({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [funder_address, {"limit": MAX_TX_PER_FUNDER}],
        })
        sig_latency = int((time.time() - t_sig) * 1000)
        rpc_calls_this_funder += 1
        self._rpc_calls_used += 1

        if sig_resp is None:
            self._log_rpc(conn, funder_address, "getSignaturesForAddress", False, sig_latency, "request_failed", False)
            self._write_cache(conn, funder_address, {}, 0, rpc_calls_this_funder, "error", "sig_request_failed")
            self._mark_done(conn, funder_address, rpc_calls_this_funder)
            return 0

        self._log_rpc(conn, funder_address, "getSignaturesForAddress", True, sig_latency, None, False)

        signatures = sig_resp.get("result") or []
        # Filter: only successful TXs within lookback window
        valid_sigs = []
        for sig_info in signatures:
            if sig_info.get("err") is not None:
                continue
            block_time = sig_info.get("blockTime") or 0
            if block_time < cutoff_ts:
                continue
            sig = sig_info.get("signature")
            if sig:
                valid_sigs.append(sig)

        if not valid_sigs:
            self._write_cache(conn, funder_address, {}, 0, rpc_calls_this_funder, "ok", None)
            self._mark_done(conn, funder_address, rpc_calls_this_funder)
            return 0

        # ── 3. getTransaction for each signature (batched) ────────────────────
        # aggregate: upstream_sender -> {sol, count, first_ts, last_ts}
        upstream_agg: Dict[str, dict] = defaultdict(lambda: {
            "total_sol": 0.0, "count": 0, "first_ts": None, "last_ts": None
        })

        for batch_start in range(0, len(valid_sigs), TX_BATCH_SIZE):
            if self._rpc_calls_used >= MAX_RPC_CALLS_PER_RUN:
                break

            batch = valid_sigs[batch_start:batch_start + TX_BATCH_SIZE]
            for sig in batch:
                if self._rpc_calls_used >= MAX_RPC_CALLS_PER_RUN:
                    break

                t_tx = time.time()
                tx_resp = self._rpc_post({
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTransaction",
                    "params": [sig, {"encoding": "json", "maxSupportedTransactionVersion": 0}],
                })
                tx_latency = int((time.time() - t_tx) * 1000)
                rpc_calls_this_funder += 1
                self._rpc_calls_used += 1

                if tx_resp is None:
                    self._log_rpc(conn, funder_address, "getTransaction", False, tx_latency, "request_failed", False)
                    continue

                self._log_rpc(conn, funder_address, "getTransaction", True, tx_latency, None, False)
                tx_data = tx_resp.get("result")
                if not tx_data:
                    continue

                # Parse inbound SOL transfers to funder_address
                self._parse_inbound_transfers(tx_data, funder_address, upstream_agg)

        # ── 4. Filter and write upstream links ────────────────────────────────
        links_written = 0
        bonding_curves = getattr(self, '_bonding_curve_pdas', set())
        candidates: Dict[str, dict] = {
            sender: data for sender, data in upstream_agg.items()
            if MIN_UPSTREAM_SOL <= data["total_sol"] <= MAX_UPSTREAM_SOL
            and sender != funder_address
            and sender not in bonding_curves
        }

        now_ts = int(time.time())
        filtered_agg: Dict[str, dict] = {}
        for sender, data in candidates.items():
            classification = self._classify_account(conn, sender)
            is_excluded = 0 if classification == "wallet" else 1
            avg_sol = data["total_sol"] / max(data["count"], 1)
            try:
                conn.execute("""
                    INSERT OR REPLACE INTO funder_upstream_links (
                        funder_address, upstream_address,
                        transfer_count, total_sol, avg_transfer_sol,
                        first_transfer_ts, last_transfer_ts,
                        source, is_excluded, funders_touched, built_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'rpc_backfill', ?, 1, ?)
                """, (
                    funder_address, sender,
                    data["count"], data["total_sol"], avg_sol,
                    data["first_ts"], data["last_ts"],
                    is_excluded, now_ts,
                ))
                if not is_excluded:
                    links_written += 1
                    filtered_agg[sender] = data
                else:
                    logger.debug(f"[SHL-Worker] Excluded {sender[:12]}… ({classification})")
            except sqlite3.OperationalError as exc:
                logger.warning(f"[SHL-Worker] Link insert failed for {sender}: {exc}")

        # ── 5. Update cache ───────────────────────────────────────────────────
        self._write_cache(
            conn, funder_address,
            {k: v for k, v in filtered_agg.items()},
            len(filtered_agg),
            rpc_calls_this_funder,
            "ok", None,
        )
        self._mark_done(conn, funder_address, rpc_calls_this_funder)

        logger.info(
            f"[SHL-Worker] {funder_address[:12]}… — sigs={len(valid_sigs)} "
            f"upstreams={len(filtered_agg)} links={links_written} rpc={rpc_calls_this_funder}"
        )
        return links_written

    # ── TX parsing ────────────────────────────────────────────────────────────

    def _parse_inbound_transfers(
        self,
        tx_data: dict,
        funder_address: str,
        agg: Dict[str, dict],
    ) -> None:
        """
        Parse a getTransaction result and find senders that sent SOL to
        funder_address.  Updates agg in-place.
        """
        try:
            meta = tx_data.get("meta") or {}
            tx = tx_data.get("transaction") or {}
            block_time = tx_data.get("blockTime")

            # Account keys
            msg = tx.get("message") or {}
            account_keys = msg.get("accountKeys") or []
            if not account_keys:
                return

            # Find index of funder_address in accountKeys
            funder_idx = None
            for i, key in enumerate(account_keys):
                addr = key if isinstance(key, str) else (key.get("pubkey") if isinstance(key, dict) else None)
                if addr == funder_address:
                    funder_idx = i
                    break

            if funder_idx is None:
                return

            pre_balances  = meta.get("preBalances")  or []
            post_balances = meta.get("postBalances") or []

            if funder_idx >= len(pre_balances) or funder_idx >= len(post_balances):
                return

            # funder_address received SOL?
            funder_delta = post_balances[funder_idx] - pre_balances[funder_idx]
            if funder_delta <= 0:
                return

            funder_received_sol = funder_delta / 1e9

            # Find senders: accounts whose balance decreased
            for idx, (pre, post) in enumerate(zip(pre_balances, post_balances)):
                if idx == funder_idx:
                    continue
                delta = post - pre
                if delta >= 0:
                    continue  # this account paid out or unchanged

                # This account lost SOL — potential sender
                lost_sol = abs(delta) / 1e9
                # Attribution: cap at what the funder received
                attributed = min(lost_sol, funder_received_sol)
                if attributed < MIN_UPSTREAM_SOL:
                    continue

                if idx >= len(account_keys):
                    continue
                key = account_keys[idx]
                sender_addr = key if isinstance(key, str) else (
                    key.get("pubkey") if isinstance(key, dict) else None
                )
                if not sender_addr:
                    continue

                entry = agg[sender_addr]
                entry["total_sol"] += attributed
                entry["count"] += 1
                if block_time:
                    if entry["first_ts"] is None or block_time < entry["first_ts"]:
                        entry["first_ts"] = block_time
                    if entry["last_ts"] is None or block_time > entry["last_ts"]:
                        entry["last_ts"] = block_time

        except Exception as exc:
            logger.debug(f"[SHL-Worker] TX parse error: {exc}")

    # ── Account classification ────────────────────────────────────────────────

    def _classify_account(self, conn: sqlite3.Connection, address: str) -> str:
        """
        Return 'wallet', 'program_account', 'token_account', or 'unknown'.
        Checks DB cache first; falls back to getAccountInfo RPC if budget allows.
        Always returns a string — never raises.
        """
        # 1. Check classification cache
        try:
            row = conn.execute(
                "SELECT classification FROM upstream_account_classification WHERE address = ?",
                (address,)
            ).fetchone()
            if row:
                return row[0]
        except sqlite3.OperationalError:
            pass

        # 2. RPC budget exhausted — treat as unknown (allow, don't block pipeline)
        if self._account_checks_used >= MAX_ACCOUNT_INFO_CHECKS_PER_RUN:
            return "unknown"

        # 3. Call getAccountInfo
        owner, classification = self._get_account_info(address)
        self._account_checks_used += 1
        self._rpc_calls_used += 1

        # 4. Persist result
        try:
            conn.execute("""
                INSERT OR REPLACE INTO upstream_account_classification
                    (address, owner_program, classification, checked_at)
                VALUES (?, ?, ?, strftime('%s','now'))
            """, (address, owner, classification))

            # Auto-add program accounts to manual blocklist for fast future exclusion
            if classification == "program_account":
                conn.execute("""
                    INSERT OR IGNORE INTO shl_excluded_upstreams (address, reason)
                    VALUES (?, ?)
                """, (address, f"auto:{owner[:20] if owner else 'unknown'}"))
        except sqlite3.OperationalError:
            pass

        return classification

    def _get_account_info(self, address: str) -> Tuple[Optional[str], str]:
        """
        Call getAccountInfo and return (owner_program, classification).
        Returns (None, 'unknown') on any failure.
        """
        try:
            resp = self._rpc_post({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getAccountInfo",
                "params": [address, {"encoding": "base64"}],
            })
            if resp is None:
                return None, "unknown"

            value = (resp.get("result") or {}).get("value")
            if value is None:
                # Account doesn't exist on-chain — treat as wallet (may be new/empty)
                return SYSTEM_PROGRAM, "wallet"

            owner = value.get("owner")
            if not owner:
                return None, "unknown"

            if owner == SYSTEM_PROGRAM:
                return owner, "wallet"
            elif owner in (SPL_TOKEN_PROGRAM, TOKEN_2022):
                return owner, "token_account"
            elif owner in EXCLUDED_OWNER_PROGRAMS:
                return owner, "program_account"
            else:
                # Unknown program owner — classify as program_account to be safe
                # (real wallets are always owned by System Program)
                return owner, "program_account"

        except Exception as exc:
            logger.debug(f"[SHL-Worker] getAccountInfo failed for {address[:12]}…: {exc}")
            return None, "unknown"

    # ── Cache helpers ─────────────────────────────────────────────────────────

    def _check_cache(
        self, conn: sqlite3.Connection, funder: str, now: int
    ) -> Optional[dict]:
        """Returns cached data dict if valid, else None."""
        try:
            row = conn.execute("""
                SELECT upstream_json, expires_at
                FROM funder_rpc_scan_cache
                WHERE funder_address = ? AND expires_at > ?
            """, (funder, now)).fetchone()
            if row:
                return json.loads(row["upstream_json"] or "{}")
        except (sqlite3.OperationalError, json.JSONDecodeError):
            pass
        return None

    def _write_cache(
        self,
        conn: sqlite3.Connection,
        funder: str,
        upstream_dict: dict,
        upstream_count: int,
        rpc_calls: int,
        status: str,
        error: Optional[str],
    ) -> None:
        now = int(time.time())
        expires_at = now + (CACHE_TTL_HOURS * 3600)
        try:
            conn.execute("""
                INSERT OR REPLACE INTO funder_rpc_scan_cache (
                    funder_address, scanned_at, expires_at,
                    upstream_json, inbound_upstream_count,
                    rpc_calls_used, status, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                funder, now, expires_at,
                json.dumps(upstream_dict),
                upstream_count, rpc_calls, status, error,
            ))
        except sqlite3.OperationalError as exc:
            logger.warning(f"[SHL-Worker] Cache write failed: {exc}")

    # ── RPC log helper ────────────────────────────────────────────────────────

    def _log_rpc(
        self,
        conn: sqlite3.Connection,
        funder: str,
        call_type: str,
        success: bool,
        latency_ms: int,
        error: Optional[str],
        cache_hit: bool,
    ) -> None:
        try:
            conn.execute("""
                INSERT INTO second_hop_lite_rpc_log
                (called_at, funder_address, call_type, success, latency_ms, error, cache_hit)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                int(time.time()), funder, call_type,
                1 if success else 0, latency_ms, error,
                1 if cache_hit else 0,
            ))
        except sqlite3.OperationalError:
            pass

    # ── RPC POST ──────────────────────────────────────────────────────────────

    def _rpc_post(self, payload: dict, timeout: int = 15) -> Optional[dict]:
        """POST a JSON-RPC payload to Helius.  Returns parsed JSON or None."""
        try:
            resp = requests.post(
                self.rpc_url,
                json=payload,
                timeout=timeout,
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code != 200:
                logger.debug(f"[SHL-Worker] RPC HTTP {resp.status_code} for {payload.get('method')}")
                return None
            return resp.json()
        except requests.RequestException as exc:
            logger.debug(f"[SHL-Worker] RPC request error: {exc}")
            return None
