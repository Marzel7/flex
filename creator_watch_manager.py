#!/usr/bin/env python3
"""
Creator SOL Watch Manager

Continuously monitors creators for incoming/outgoing SOL transfers after token launch.
Maintains an append-only ledger of all SOL balance changes.

Key Design:
- Track every SOL in/out transaction for each creator
- Poll getSignaturesForAddress every N seconds for new signatures
- Compute SOL deltas from preBalances/postBalances
- Store in creator_tx_ledger (append-only, idempotent by signature)
- Track state (last_signature, last_slot) for resumable polling
"""

import asyncio
import aiohttp
import sqlite3
import json
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
import time

DB_PATH = "pumpswap_tokens.db"

class CreatorWatchManager:
    """Manages continuous monitoring of creator SOL activity"""

    def __init__(self, rpc_url: str, rpc_url_2: str = None, helius_rpc: str = None, session: aiohttp.ClientSession = None):
        """
        Initialize the watch manager.

        Args:
            rpc_url: Primary RPC endpoint (QuickNode)
            rpc_url_2: Secondary RPC endpoint
            helius_rpc: Helius RPC endpoint
            session: Optional aiohttp session (creates own if not provided)
        """
        self.rpc_url = rpc_url
        self.rpc_url_2 = rpc_url_2
        self.helius_rpc = helius_rpc
        self.session = session
        self._own_session = False
        self.poll_interval = 5  # seconds between polls per creator
        self.confirm_level = "confirmed"  # or "finalized"

        # Track which creators we're watching
        self.watching_creators = {}  # creator_pubkey -> {'watching': bool, 'last_poll': time}

        self._ensure_db()

    async def ensure_session(self):
        """Ensure we have an aiohttp session"""
        if self.session is None:
            self.session = aiohttp.ClientSession()
            self._own_session = True

    async def close(self):
        """Clean up resources"""
        if self._own_session and self.session:
            await self.session.close()

    def _ensure_db(self):
        """Create database schema for creator tracking"""
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA journal_mode=WAL")
        cursor = conn.cursor()

        # Master creator table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS creator_watch (
                creator_pubkey TEXT PRIMARY KEY,
                first_seen_slot INTEGER,
                first_seen_ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                create_sig TEXT UNIQUE,
                confidence TEXT DEFAULT 'confirmed',
                labels TEXT,  -- JSON array of tags (e.g. ["bot", "team"])
                monitored INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Append-only transaction ledger (one row = one SOL balance delta)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS creator_tx_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                creator_pubkey TEXT NOT NULL,
                signature TEXT NOT NULL UNIQUE,
                slot INTEGER,
                blockTime INTEGER,
                delta_sol_lamports INTEGER,  -- Net SOL change (can be negative)
                fee_lamports INTEGER,  -- Fee paid by this tx
                compute_units INTEGER,
                compute_units_consumed INTEGER,
                counterparty TEXT,  -- Optional: detected counterparty
                tx_type TEXT,  -- 'transfer', 'swap', 'rent', 'unknown'
                source TEXT,  -- 'websocket' or 'poll'
                is_confirmed INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY(creator_pubkey) REFERENCES creator_watch(creator_pubkey),
                INDEX idx_creator_ledger ON creator_pubkey,
                INDEX idx_signature ON signature
            )
        """)

        # Creator polling state (tracks where we left off)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS creator_state (
                creator_pubkey TEXT PRIMARY KEY,
                last_signature TEXT,
                last_slot INTEGER,
                last_processed_at TIMESTAMP,
                total_signatures_processed INTEGER DEFAULT 0,
                total_sol_in_lamports INTEGER DEFAULT 0,  -- Cumulative
                total_sol_out_lamports INTEGER DEFAULT 0,
                last_24h_sol_in REAL DEFAULT 0,
                last_24h_sol_out REAL DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY(creator_pubkey) REFERENCES creator_watch(creator_pubkey)
            )
        """)

        conn.commit()
        conn.close()

    def add_creator(self, creator_pubkey: str, create_sig: str, slot: int, confidence: str = "confirmed"):
        """Register a new creator to watch"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=5)
            cursor = conn.cursor()

            cursor.execute("""
                INSERT OR IGNORE INTO creator_watch
                (creator_pubkey, first_seen_slot, create_sig, confidence)
                VALUES (?, ?, ?, ?)
            """, (creator_pubkey, slot, create_sig, confidence))

            # Initialize polling state
            cursor.execute("""
                INSERT OR IGNORE INTO creator_state
                (creator_pubkey, last_signature, last_slot)
                VALUES (?, ?, ?)
            """, (creator_pubkey, create_sig, slot))

            conn.commit()
            conn.close()

            self.watching_creators[creator_pubkey] = {
                'watching': True,
                'last_poll': 0  # Poll immediately
            }

            print(f"[CREATOR_WATCH] 👁️ Now watching creator {creator_pubkey[:16]}...", flush=True)

        except Exception as e:
            print(f"[CREATOR_WATCH] ⚠️ Error adding creator: {e}", flush=True)

    async def _post_rpc(self, payload: dict, endpoint: str = None) -> Optional[dict]:
        """Post RPC request with proper error handling"""
        if endpoint is None:
            endpoint = self.rpc_url

        try:
            async with self.session.post(endpoint, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    return await resp.json()
                elif resp.status == 429:
                    # Rate limited
                    return None
        except Exception as e:
            pass
        return None

    async def _post_rpc_with_fallback(self, payload: dict) -> Optional[dict]:
        """Post RPC request with fallback chain"""
        endpoints = [self.rpc_url, self.rpc_url_2, self.helius_rpc]
        endpoints = [e for e in endpoints if e]  # Remove None values

        for endpoint in endpoints:
            result = await self._post_rpc(payload, endpoint)
            if result and "result" in result:
                return result
            await asyncio.sleep(0.1)

        return None

    async def get_signatures(self, creator: str, before: str = None, limit: int = 100) -> List[dict]:
        """
        Get signatures for creator using getSignaturesForAddress

        Returns list of dicts: [{"signature": "...", "blockTime": ..., "slot": ...}, ...]
        """
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [
                creator,
                {
                    "limit": min(limit, 50),  # API max is 50
                    **({"before": before} if before else {})
                }
            ]
        }

        result = await self._post_rpc_with_fallback(payload)
        if result and "result" in result:
            return result["result"]
        return []

    async def get_transaction(self, signature: str, encoding: str = "jsonParsed") -> Optional[dict]:
        """Get full transaction with account data"""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTransaction",
            "params": [signature, {"encoding": encoding, "commitment": self.confirm_level}]
        }

        result = await self._post_rpc_with_fallback(payload)
        if result and "result" in result:
            return result["result"]
        return None

    def _compute_sol_delta(self, tx: dict, creator_pubkey: str) -> Tuple[int, Optional[str]]:
        """
        Compute SOL balance change for creator in a transaction.

        Returns: (delta_lamports, counterparty_pubkey)

        Algorithm:
        1. Find creator's account index in message.accountKeys
        2. delta = postBalances[i] - preBalances[i]
        3. If fee payer (index 0), fee is already deducted from preBalance
        4. For transfers: counterparty is usually the receiving account (but need heuristic)
        """
        if not tx or "transaction" not in tx:
            return 0, None

        try:
            tx_data = tx["transaction"]
            meta = tx.get("meta", {})

            if not meta or "preBalances" not in meta or "postBalances" not in meta:
                return 0, None

            message = tx_data.get("message", {})
            account_keys = message.get("accountKeys", [])

            # Find creator's account index
            creator_idx = None
            for idx, key in enumerate(account_keys):
                key_pubkey = key.get("pubkey") if isinstance(key, dict) else str(key)
                if key_pubkey == creator_pubkey:
                    creator_idx = idx
                    break

            if creator_idx is None:
                return 0, None

            pre_bal = meta["preBalances"][creator_idx]
            post_bal = meta["postBalances"][creator_idx]
            delta = post_bal - pre_bal

            # Heuristic for counterparty: if creator received SOL (delta > 0),
            # counterparty is likely the account that sent it (account 0 often, or look at instructions)
            counterparty = None
            if delta > 0 and len(account_keys) > 1:
                # Creator received SOL - likely from account 0 or another early account
                counterparty = account_keys[0].get("pubkey") if isinstance(account_keys[0], dict) else str(account_keys[0])
            elif delta < 0 and len(account_keys) > 1:
                # Creator sent SOL - look for likely recipient in account keys
                # (simple heuristic: first writable account after creator)
                for idx in range(creator_idx + 1, len(account_keys)):
                    key = account_keys[idx]
                    key_pubkey = key.get("pubkey") if isinstance(key, dict) else str(key)
                    if key_pubkey != "11111111111111111111111111111111":  # Skip system program
                        counterparty = key_pubkey
                        break

            return delta, counterparty

        except Exception as e:
            print(f"[CREATOR_WATCH] ⚠️ Error computing SOL delta: {e}", flush=True)
            return 0, None

    async def process_signature(self, creator_pubkey: str, sig_info: dict) -> bool:
        """
        Process a single signature: fetch tx, compute delta, save to ledger.
        Returns True if processed, False if already exists or error.
        """
        signature = sig_info["signature"]
        block_time = sig_info.get("blockTime", 0)
        slot = sig_info.get("slot", 0)

        # Check if already in ledger (idempotent)
        try:
            conn = sqlite3.connect(DB_PATH, timeout=5)
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM creator_tx_ledger WHERE signature = ? LIMIT 1", (signature,))
            if cursor.fetchone():
                conn.close()
                return False  # Already processed
            conn.close()
        except:
            pass

        # Fetch full transaction
        tx = await self.get_transaction(signature)
        if not tx:
            return False

        # Compute SOL delta
        delta_lamports, counterparty = self._compute_sol_delta(tx, creator_pubkey)

        # Determine tx type
        tx_type = "unknown"
        if delta_lamports != 0:
            tx_type = "transfer" if abs(delta_lamports) > 5000 else "rent"

        # Extract fee
        meta = tx.get("meta", {})
        fee_lamports = meta.get("fee", 0)
        compute_units = meta.get("computeUnitsConsumed", 0)

        # Save to ledger
        try:
            conn = sqlite3.connect(DB_PATH, timeout=5)
            cursor = conn.cursor()

            cursor.execute("""
                INSERT OR IGNORE INTO creator_tx_ledger
                (creator_pubkey, signature, slot, blockTime, delta_sol_lamports, fee_lamports,
                 compute_units_consumed, counterparty, tx_type, source, is_confirmed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                creator_pubkey, signature, slot, block_time,
                delta_lamports, fee_lamports, compute_units,
                counterparty, tx_type, "poll", 1
            ))

            conn.commit()
            conn.close()

            return True

        except Exception as e:
            print(f"[CREATOR_WATCH] ⚠️ Error saving tx: {e}", flush=True)
            return False

    async def poll_creator(self, creator_pubkey: str, limit: int = 100) -> int:
        """
        Poll creator for new signatures since last poll.

        Returns number of new signatures processed.
        """
        # Get state (last_signature for pagination)
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()
        cursor.execute("SELECT last_signature, last_slot FROM creator_state WHERE creator_pubkey = ?", (creator_pubkey,))
        row = cursor.fetchone()
        last_sig = row[0] if row else None
        conn.close()

        # Get new signatures
        sigs = await self.get_signatures(creator_pubkey, before=last_sig, limit=limit)

        if not sigs:
            return 0

        # Process each signature
        processed = 0
        for sig_info in sigs:
            if await self.process_signature(creator_pubkey, sig_info):
                processed += 1

        # Update state
        if sigs:
            latest_sig = sigs[0]["signature"]
            latest_slot = sigs[0].get("slot", 0)

            conn = sqlite3.connect(DB_PATH, timeout=5)
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE creator_state
                SET last_signature = ?, last_slot = ?, last_processed_at = CURRENT_TIMESTAMP,
                    total_signatures_processed = total_signatures_processed + ?
                WHERE creator_pubkey = ?
            """, (latest_sig, latest_slot, processed, creator_pubkey))
            conn.commit()
            conn.close()

        return processed

    async def poll_all_creators(self):
        """Poll all watched creators for new signatures"""
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()
        cursor.execute("SELECT creator_pubkey FROM creator_watch WHERE monitored = 1")
        creators = [row[0] for row in cursor.fetchall()]
        conn.close()

        if not creators:
            return

        print(f"[CREATOR_WATCH] 🔍 Polling {len(creators)} creators for new SOL transfers...", flush=True)

        for creator_pubkey in creators:
            processed = await self.poll_creator(creator_pubkey)
            if processed > 0:
                print(f"[CREATOR_WATCH]    {creator_pubkey[:16]}... → {processed} new transactions", flush=True)

            # Respect rate limits
            await asyncio.sleep(0.2)

    async def run_polling_loop(self, poll_interval: int = 30):
        """
        Main polling loop - runs continuously.

        Polls all creators every poll_interval seconds.
        """
        await self.ensure_session()
        print(f"[CREATOR_WATCH] 🚀 Starting polling loop (interval: {poll_interval}s)", flush=True)

        while True:
            try:
                await self.poll_all_creators()
                await asyncio.sleep(poll_interval)
            except Exception as e:
                print(f"[CREATOR_WATCH] ⚠️ Polling error: {e}", flush=True)
                await asyncio.sleep(30)

    # --- Query Methods (for UI/API) ---

    def get_creator_stats(self, creator_pubkey: str) -> dict:
        """Get summary stats for a creator"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=5)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Get state
            cursor.execute("SELECT * FROM creator_state WHERE creator_pubkey = ?", (creator_pubkey,))
            state_row = cursor.fetchone()

            if not state_row:
                conn.close()
                return None

            # Get recent ledger entries (last 24h)
            cursor.execute("""
                SELECT
                    COUNT(*) as tx_count,
                    SUM(delta_sol_lamports) as net_delta,
                    SUM(CASE WHEN delta_sol_lamports > 0 THEN delta_sol_lamports ELSE 0 END) as total_in,
                    SUM(CASE WHEN delta_sol_lamports < 0 THEN ABS(delta_sol_lamports) ELSE 0 END) as total_out,
                    SUM(fee_lamports) as total_fees
                FROM creator_tx_ledger
                WHERE creator_pubkey = ? AND blockTime > (unixepoch() - 86400)
            """, (creator_pubkey,))
            ledger_row = cursor.fetchone()

            conn.close()

            return {
                "creator_pubkey": creator_pubkey,
                "total_sigs_processed": state_row["total_signatures_processed"] or 0,
                "last_processed_at": state_row["last_processed_at"],
                "cumulative_sol_in": (state_row["total_sol_in_lamports"] or 0) / 1e9,
                "cumulative_sol_out": (state_row["total_sol_out_lamports"] or 0) / 1e9,
                "last_24h": {
                    "tx_count": ledger_row["tx_count"] or 0,
                    "net_delta_sol": (ledger_row["net_delta"] or 0) / 1e9,
                    "total_in_sol": (ledger_row["total_in"] or 0) / 1e9,
                    "total_out_sol": (ledger_row["total_out"] or 0) / 1e9,
                    "total_fees_sol": (ledger_row["total_fees"] or 0) / 1e9,
                }
            }

        except Exception as e:
            print(f"[CREATOR_WATCH] ⚠️ Error getting stats: {e}", flush=True)
            return None

    def get_recent_ledger(self, creator_pubkey: str, limit: int = 50) -> List[dict]:
        """Get recent SOL transactions for creator"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=5)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("""
                SELECT
                    signature,
                    blockTime,
                    delta_sol_lamports,
                    fee_lamports,
                    tx_type,
                    counterparty,
                    created_at
                FROM creator_tx_ledger
                WHERE creator_pubkey = ?
                ORDER BY blockTime DESC
                LIMIT ?
            """, (creator_pubkey, limit))

            rows = cursor.fetchall()
            conn.close()

            return [dict(row) for row in rows]

        except Exception as e:
            print(f"[CREATOR_WATCH] ⚠️ Error getting ledger: {e}", flush=True)
            return []


# Convenience function for use in listener
async def start_creator_watch(rpc_url: str, rpc_url_2: str = None, helius_rpc: str = None, session: aiohttp.ClientSession = None):
    """Factory for starting creator watch manager"""
    manager = CreatorWatchManager(rpc_url, rpc_url_2, helius_rpc, session)
    return manager
