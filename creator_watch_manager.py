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

# Import unified recipient tracker if available
try:
    from unified_recipient_tracker import UnifiedRecipientTracker
    HAS_UNIFIED_TRACKER = True
except ImportError:
    HAS_UNIFIED_TRACKER = False

class CreatorWatchManager:
    """Manages continuous monitoring of creator SOL activity"""

    def __init__(self, rpc_url: str, helius_rpc: str = None, session: aiohttp.ClientSession = None):
        """
        Initialize the watch manager.

        Args:
            rpc_url: Primary RPC endpoint (Helius or Public Solana)
            helius_rpc: Helius RPC endpoint
            session: Optional aiohttp session (creates own if not provided)
        """
        self.rpc_url = rpc_url
        self.helius_rpc = helius_rpc
        self.session = session
        self._own_session = False
        self.poll_interval = 5  # seconds between polls per creator
        self.confirm_level = "confirmed"  # or "finalized"

        # Track which creators we're watching
        self.watching_creators = {}  # creator_pubkey -> {'watching': bool, 'last_poll': time}
        
        # Polling control flag
        self.polling_enabled = True  # Toggle for UI control

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
                delta_sol_lamports INTEGER,
                fee_lamports INTEGER,
                compute_units INTEGER,
                compute_units_consumed INTEGER,
                counterparty TEXT,
                tx_type TEXT,
                source TEXT,
                is_confirmed INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY(creator_pubkey) REFERENCES creator_watch(creator_pubkey)
            )
        """)

        # Create indexes separately (SQLite syntax)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_creator_tx_ledger ON creator_tx_ledger(creator_pubkey)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_signature ON creator_tx_ledger(signature)")

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

            # Initialize polling state - do NOT set last_signature yet
            # First poll will fetch most recent signatures without a "before" anchor
            cursor.execute("""
                INSERT OR IGNORE INTO creator_state
                (creator_pubkey, last_signature, last_slot)
                VALUES (?, NULL, ?)
            """, (creator_pubkey, slot))

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
        endpoints = [self.rpc_url, self.helius_rpc]
        endpoints = [e for e in endpoints if e]  # Remove None values

        for i, endpoint in enumerate(endpoints):
            result = await self._post_rpc(payload, endpoint)
            if result and "result" in result:
                return result
            # print(f"[RPC] Endpoint {i+1}/{len(endpoints)} failed ({endpoint[:30]}...)", flush=True)
            await asyncio.sleep(0.1)

        # print(f"[RPC] All {len(endpoints)} endpoints failed", flush=True)
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

    def _classify_sol_delta(self, tx: dict, creator_pubkey: str) -> Tuple[int, Optional[str], str]:
        """
        Classify SOL balance change by cause (funding vs trading vs noise).

        Returns: (delta_lamports, counterparty_pubkey, classification)

        Classifications:
        - system_transfer: Direct System::Transfer instruction (REAL FUNDING - p2p SOL move)
        - pump_fun_buy: Creator bought token (TRADING - skip for funding analysis)
        - pump_fun_sell: Creator sold token (TRADING - skip for funding analysis)
        - ata_rent: ATA account creation or closure (NOISE)
        - fee_only: Transaction fee (NOISE)
        - other: Unclassified SOL change (NOISE)
        """
        if not tx or "transaction" not in tx:
            return 0, None, "unknown"

        try:
            tx_data = tx["transaction"]
            meta = tx.get("meta", {})

            if not meta or "preBalances" not in meta or "postBalances" not in meta:
                return 0, None, "unknown"

            message = tx_data.get("message", {})
            account_keys = message.get("accountKeys", [])
            instructions = message.get("instructions", [])

            # Find creator's account index
            creator_idx = None
            for idx, key in enumerate(account_keys):
                key_pubkey = key.get("pubkey") if isinstance(key, dict) else str(key)
                if key_pubkey == creator_pubkey:
                    creator_idx = idx
                    break

            if creator_idx is None:
                return 0, None, "unknown"

            pre_bal = meta["preBalances"][creator_idx]
            post_bal = meta["postBalances"][creator_idx]
            delta = post_bal - pre_bal

            # Analyze instructions to classify the delta
            classification = "other"
            counterparty = None

            # Check if we have parsed instructions
            has_parsed_data = any("parsed" in instr for instr in instructions)

            if has_parsed_data:
                # Path 1: PARSED instruction analysis (most accurate)
                pump_fun_program = "6EF8rQNwhYf2qk5FVwW3mWBwyAEifzs74MHLsAXe8Qwp"
                has_pump_buy = False
                has_pump_sell = False

                for instr in instructions:
                    program_id = instr.get("programId", "")

                    # Pump.fun buy/sell detection
                    if program_id == pump_fun_program:
                        parsed = instr.get("parsed", {})
                        instr_type = parsed.get("type", "")
                        if instr_type == "buy":
                            has_pump_buy = True
                        elif instr_type == "sell":
                            has_pump_sell = True

                if has_pump_buy and delta < 0:
                    classification = "pump_fun_buy"
                elif has_pump_sell and delta > 0:
                    classification = "pump_fun_sell"

                # Check for System Program Transfer instructions (real p2p)
                system_program = "11111111111111111111111111111111"
                has_system_transfer = False
                system_transfer_target = None

                for instr in instructions:
                    program_id = instr.get("programId", "")
                    if program_id == system_program:
                        parsed = instr.get("parsed", {})
                        instr_type = parsed.get("type", "")
                        if instr_type == "transfer":
                            source = parsed.get("info", {}).get("source", "")
                            destination = parsed.get("info", {}).get("destination", "")
                            if source == creator_pubkey and delta < 0:
                                has_system_transfer = True
                                system_transfer_target = destination
                            elif destination == creator_pubkey and delta > 0:
                                has_system_transfer = True
                                system_transfer_target = source

                # PRIORITY: Check for System Program Transfer instructions FIRST
                # Real funding is p2p SOL transfers, everything else is noise for our purposes
                if has_system_transfer:
                    classification = "system_transfer"
                    counterparty = system_transfer_target
                elif has_pump_buy and delta < 0:
                    classification = "pump_fun_buy"
                elif has_pump_sell and delta > 0:
                    classification = "pump_fun_sell"
                else:
                    # Check for ATA (Associated Token Account) operations
                    # Token program: TokenkegQfeZyiNwAJsyFbPVwwQQfg5bgDCSm2c1fNV
                    token_program = "TokenkegQfeZyiNwAJsyFbPVwwQQfg5bgDCSm2c1fNV"

                    for instr in instructions:
                        program_id = instr.get("programId", "")
                        if program_id == token_program:
                            parsed = instr.get("parsed", {})
                            instr_type = parsed.get("type", "")
                            if instr_type in ["initializeMint", "createIdempotent", "closeAccount"]:
                                classification = "ata_rent"
                                break

            else:
                # Path 2: FALLBACK - Balance-based heuristic (when no parsed data available)
                # This is less accurate but works when RPC doesn't return parsed instructions
                
                # Check for Pump.fun program presence (by looking at programId field in raw format)
                pump_fun_program = "6EF8rQNwhYf2qk5FVwW3mWBwyAEifzs74MHLsAXe8Qwp"
                has_pump_program = any(instr.get("programId") == pump_fun_program for instr in instructions)
                
                # If transaction involves Pump.fun and creator lost SOL, it's likely a buy
                # If transaction involves Pump.fun and creator gained SOL, it's likely a sell
                if has_pump_program:
                    if delta < 0:
                        classification = "pump_fun_buy"
                    elif delta > 0:
                        classification = "pump_fun_sell"
                    else:
                        classification = "pump_fun_buy"  # Default to buy if no delta
                else:
                    # No Pump.fun program, check if it's a meaningful SOL change (not just fee)
                    fee = meta.get("fee", 0)
                    
                    # Small positive delta = probably just SOL received (funding)
                    # Large negative delta = probably SOL sent (not funding)
                    # Very small delta = probably just fees
                    
                    if abs(delta) < 5000:  # Less than 5000 lamports = negligible
                        classification = "fee_only"
                    elif delta > 5000:  # Received meaningful SOL
                        classification = "system_transfer"
                        # Without parsed data, counterparty is unknown
                        counterparty = None
                    else:
                        classification = "other"

            # Only system_transfer has a meaningful counterparty for funding analysis
            if classification != "system_transfer":
                counterparty = None

            return delta, counterparty, classification

        except Exception as e:
            print(f"[CREATOR_WATCH] ⚠️ Error classifying SOL delta: {e}", flush=True)
            return 0, None, "error"

    def _compute_sol_delta(self, tx: dict, creator_pubkey: str) -> Tuple[int, Optional[str]]:
        """Backward compatible wrapper - returns only delta and counterparty"""
        delta, counterparty, _ = self._classify_sol_delta(tx, creator_pubkey)
        return delta, counterparty

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

        # Classify SOL delta by cause (not just amount)
        delta_lamports, counterparty, classification = self._classify_sol_delta(tx, creator_pubkey)

        # Only track REAL FUNDING (system transfers)
        # Skip everything else: pump_fun trades, ata_rent, fees, other
        if classification != "system_transfer":
            return False

        # Extract fee and compute units
        meta = tx.get("meta", {})
        fee_lamports = meta.get("fee", 0)
        compute_units = meta.get("computeUnitsConsumed", 0)

        # Use classification as tx_type
        tx_type = classification

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
            print(f"[CREATOR_WATCH]    {creator_pubkey[:16]}... → No new signatures found (last_sig={last_sig[:16] if last_sig else 'None'})", flush=True)
            return 0

        print(f"[CREATOR_WATCH]    {creator_pubkey[:16]}... → Found {len(sigs)} signatures", flush=True)

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

        print(f"[CREATOR_WATCH]    {creator_pubkey[:16]}... → Processed {processed}/{len(sigs)} signatures", flush=True)

        return processed

    async def poll_all_creators(self):
        """Poll all watched creators for new signatures"""
        # Check if polling is enabled via database flag (for UI control)
        try:
            conn = sqlite3.connect(DB_PATH, timeout=5)
            cursor = conn.cursor()
            
            # Check database flag
            cursor.execute("SELECT setting_value FROM polling_settings WHERE setting_name = 'polling_enabled'")
            row = cursor.fetchone()
            db_polling_enabled = row[0] == '1' if row else True
            
            # Also check in-memory flag
            if not self.polling_enabled or not db_polling_enabled:
                conn.close()
                return
            
            # Get creators
            cursor.execute("SELECT creator_pubkey FROM creator_watch WHERE monitored = 1")
            creators = [row[0] for row in cursor.fetchall()]
            conn.close()
        except Exception as e:
            print(f"[CREATOR_WATCH] ⚠️ Error checking polling status: {e}", flush=True)
            return

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
        Respects the polling_enabled flag for UI control.
        """
        await self.ensure_session()
        print(f"[CREATOR_WATCH] 🚀 Starting polling loop (interval: {poll_interval}s)", flush=True)

        while True:
            try:
                if self.polling_enabled:
                    await self.poll_all_creators()
                else:
                    print(f"[CREATOR_WATCH] ⏸️  Polling paused", flush=True)
                await asyncio.sleep(poll_interval)
            except Exception as e:
                print(f"[CREATOR_WATCH] ⚠️ Polling error: {e}", flush=True)
                await asyncio.sleep(30)

    def pause_polling(self):
        """Pause creator TX polling"""
        self.polling_enabled = False
        print(f"[CREATOR_WATCH] ⏸️  Polling PAUSED", flush=True)
        return {"status": "paused"}

    def resume_polling(self):
        """Resume creator TX polling"""
        self.polling_enabled = True
        print(f"[CREATOR_WATCH] ▶️  Polling RESUMED", flush=True)
        return {"status": "resumed"}

    def toggle_polling(self):
        """Toggle polling on/off"""
        if self.polling_enabled:
            return self.pause_polling()
        else:
            return self.resume_polling()

    def get_polling_status(self):
        """Get current polling status"""
        return {
            "status": "enabled" if self.polling_enabled else "paused",
            "polling_enabled": self.polling_enabled
        }

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

    def check_address_cross_references(self, address: str, creator_pubkey: str) -> Dict:
        """
        Cross-reference check: when an address appears as counterparty for a creator,
        check if it's also linked to other creators (network detection).

        Returns dict with cross-reference findings.
        """
        if not HAS_UNIFIED_TRACKER:
            return {'status': 'unified_tracker_unavailable'}

        try:
            tracker = UnifiedRecipientTracker()

            # Find other creators linked to this address
            other_creators = tracker.find_shared_recipients(creator_pubkey)

            if address in other_creators and other_creators[address]:
                print(f"[CROSS_REF] ⚠️ Address {address[:16]}... linked to {len(other_creators[address])} other creators!", flush=True)

                # Log the cross-reference
                for other_creator in other_creators[address]:
                    tracker.log_cross_reference(
                        address, creator_pubkey, other_creator,
                        context="cross_creator_recipient"
                    )

                return {
                    'status': 'cross_reference_detected',
                    'address': address,
                    'creator': creator_pubkey,
                    'other_creators': other_creators[address],
                    'count': len(other_creators[address])
                }

            return {'status': 'no_cross_reference', 'address': address}

        except Exception as e:
            print(f"[CROSS_REF] Error checking cross-references: {e}", flush=True)
            return {'status': 'error', 'error': str(e)}

    def update_unified_recipient_tracking(self, creator_pubkey: str) -> Dict:
        """
        Update unified recipient tracking for a creator's recent transactions.
        Merges new tx_ledger entries into creator_recipients_unified.

        Returns stats on what was merged.
        """
        if not HAS_UNIFIED_TRACKER:
            return {'status': 'unified_tracker_unavailable'}

        try:
            tracker = UnifiedRecipientTracker()

            # Get recent outgoing transfers from this creator in tx_ledger
            conn = sqlite3.connect(DB_PATH, timeout=5)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("""
                SELECT counterparty, ABS(SUM(delta_sol_lamports)) as total_sol,
                       COUNT(*) as transfer_count, MAX(blockTime) as last_time
                FROM creator_tx_ledger
                WHERE creator_pubkey = ? AND delta_sol_lamports < 0
                  AND counterparty IS NOT NULL
                GROUP BY counterparty
            """, (creator_pubkey,))

            rows = cursor.fetchall()
            conn.close()

            stats = {
                'status': 'updated',
                'creator': creator_pubkey,
                'recipients_updated': 0,
                'cross_references_detected': 0
            }

            for row in rows:
                recipient = row['counterparty']
                amount = row['total_sol'] / 1e9

                # Check for cross-references
                cross_ref = self.check_address_cross_references(recipient, creator_pubkey)
                if cross_ref.get('status') == 'cross_reference_detected':
                    stats['cross_references_detected'] += 1

                stats['recipients_updated'] += 1

            return stats

        except Exception as e:
            print(f"[UNIFIED] Error updating recipient tracking: {e}", flush=True)
            return {'status': 'error', 'error': str(e)}


# Convenience function for use in listener
async def start_creator_watch(rpc_url: str, helius_rpc: str = None, session: aiohttp.ClientSession = None):
    """Factory for starting creator watch manager"""
    manager = CreatorWatchManager(rpc_url, helius_rpc, session)
    return manager
