#!/usr/bin/env python3
"""
Creator SOL Watch Manager

Continuously monitors creators for incoming/outgoing SOL transfers after token launch.
Maintains an append-only ledger of all SOL balance changes.

Key Design:
- Track SOL in/out transactions for each creator
- Poll getSignaturesForAddress for new signatures (NEW: uses `until` for forward polling)
- Compute SOL deltas from preBalances/postBalances
- Store in creator_tx_ledger (append-only, idempotent by signature)
- Track polling state (last_signature, last_slot)

NEW (Backfill PRE-migration):
- backfill_creator_pre_migration(): pages backwards (newest->older) and only processes
  signatures with blockTime <= migration_blocktime, bounded by a lookback window.

Efficiency upgrades (NO Enhanced Transactions):
✅ Bulk "already processed?" check per page (single SQLite query in chunks)
✅ getTransaction uses encoding="json" by default (smaller/faster than jsonParsed)
✅ Optional bounded concurrency for backfill page processing
✅ Polling uses `until` (newer-than anchor) instead of `before` (older-than pagination)
"""

import asyncio
import aiohttp
import sqlite3
from typing import Dict, List, Optional, Tuple
import time

DB_PATH = "flex_complete_database.db"

# Import unified recipient tracker if available
try:
    from unified_recipient_tracker import UnifiedRecipientTracker
    HAS_UNIFIED_TRACKER = True
except ImportError:
    HAS_UNIFIED_TRACKER = False


class CreatorWatchManager:
    """Manages continuous monitoring of creator SOL activity"""

    # Tune these
    MIN_MEANINGFUL_LAMPORTS_DEFAULT = 50_000  # 0.00005 SOL (ignore fees/noise)
    DEFAULT_BACKFILL_CONCURRENCY = 10         # backfill getTransaction parallelism

    def __init__(self, rpc_url: str, helius_rpc: str = None, session: aiohttp.ClientSession = None):
        self.rpc_url = rpc_url
        self.helius_rpc = helius_rpc
        self.session = session
        self._own_session = False

        self.poll_interval = 5
        self.confirm_level = "confirmed"  # or "finalized"

        self.watching_creators = {}
        self.polling_enabled = True

        self._ensure_db()

    async def ensure_session(self):
        if self.session is None:
            self.session = aiohttp.ClientSession()
            self._own_session = True

    async def close(self):
        if self._own_session and self.session:
            await self.session.close()

    # -----------------------------
    # DB
    # -----------------------------
    def _ensure_db(self):
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA journal_mode=WAL")
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS creator_watch (
                creator_pubkey TEXT PRIMARY KEY,
                first_seen_slot INTEGER,
                first_seen_ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                create_sig TEXT UNIQUE,
                confidence TEXT DEFAULT 'confirmed',
                labels TEXT,
                monitored INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

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

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_creator_tx_ledger ON creator_tx_ledger(creator_pubkey)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_signature ON creator_tx_ledger(signature)")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS creator_state (
                creator_pubkey TEXT PRIMARY KEY,
                last_signature TEXT,
                last_slot INTEGER,
                last_processed_at TIMESTAMP,
                total_signatures_processed INTEGER DEFAULT 0,
                total_sol_in_lamports INTEGER DEFAULT 0,
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
        try:
            conn = sqlite3.connect(DB_PATH, timeout=5)
            cursor = conn.cursor()

            cursor.execute("""
                INSERT OR IGNORE INTO creator_watch
                (creator_pubkey, first_seen_slot, create_sig, confidence)
                VALUES (?, ?, ?, ?)
            """, (creator_pubkey, slot, create_sig, confidence))

            cursor.execute("""
                INSERT OR IGNORE INTO creator_state
                (creator_pubkey, last_signature, last_slot)
                VALUES (?, NULL, ?)
            """, (creator_pubkey, slot))

            conn.commit()
            conn.close()

            self.watching_creators[creator_pubkey] = {'watching': True, 'last_poll': 0}
            print(f"[CREATOR_WATCH] 👁️ Now watching creator {creator_pubkey[:16]}...", flush=True)

        except Exception as e:
            print(f"[CREATOR_WATCH] ⚠️ Error adding creator: {e}", flush=True)

    # -----------------------------
    # RPC
    # -----------------------------
    async def _post_rpc(self, payload: dict, endpoint: str = None) -> Optional[dict]:
        if endpoint is None:
            endpoint = self.rpc_url
        try:
            async with self.session.post(
                endpoint, json=payload, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                if resp.status == 429:
                    return None
        except Exception:
            return None
        return None

    async def _post_rpc_with_fallback(self, payload: dict) -> Optional[dict]:
        endpoints = [self.rpc_url, self.helius_rpc]
        endpoints = [e for e in endpoints if e]

        for endpoint in endpoints:
            res = await self._post_rpc(payload, endpoint)
            if res and "result" in res:
                return res
            await asyncio.sleep(0.05)
        return None

    async def get_signatures(
        self,
        address: str,
        before: str = None,
        until: str = None,
        limit: int = 1000
    ) -> List[dict]:
        """
        getSignaturesForAddress returns newest -> oldest.

        - `before`: paginate OLDER than this signature (walk backwards)
        - `until`: return signatures NEWER than this signature (great for forward polling)

        Solana supports up to 1000; some providers may clamp lower.
        """
        cfg = {"limit": max(1, min(int(limit), 1000))}
        if before:
            cfg["before"] = before
        if until:
            cfg["until"] = until

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [address, cfg]
        }

        result = await self._post_rpc_with_fallback(payload)
        if result and "result" in result:
            return result["result"] or []
        return []

    async def get_transaction(self, signature: str, encoding: str = "json") -> Optional[dict]:
        """
        Use encoding='json' by default (smaller/faster).
        We only need meta.preBalances/postBalances + accountKeys for SOL delta.
        """
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTransaction",
            "params": [
                signature,
                {
                    "encoding": encoding,
                    "commitment": self.confirm_level,
                    "maxSupportedTransactionVersion": 0,
                }
            ]
        }
        result = await self._post_rpc_with_fallback(payload)
        if result and "result" in result:
            return result["result"]
        return None

    async def get_blocktime_for_signature(self, signature: str) -> Optional[int]:
        tx = await self.get_transaction(signature, encoding="json")
        if not tx:
            return None
        bt = tx.get("blockTime")
        return bt if isinstance(bt, int) else None

    # -----------------------------
    # Ledger existence checks (FAST)
    # -----------------------------
    def _existing_signatures_in_ledger(self, sigs: List[str]) -> set:
        """
        Bulk check: returns set(signatures) that already exist in creator_tx_ledger.
        This avoids 1 SQLite open/close per signature.
        """
        if not sigs:
            return set()
        try:
            conn = sqlite3.connect(DB_PATH, timeout=5)
            cursor = conn.cursor()

            existing = set()
            # SQLite variable limit often 999, so chunk safely
            CHUNK = 900
            for i in range(0, len(sigs), CHUNK):
                chunk = sigs[i:i + CHUNK]
                placeholders = ",".join("?" for _ in chunk)
                cursor.execute(
                    f"SELECT signature FROM creator_tx_ledger WHERE signature IN ({placeholders})",
                    chunk
                )
                existing.update(r[0] for r in cursor.fetchall())

            conn.close()
            return existing
        except Exception:
            return set()

    # -----------------------------
    # SOL delta computation/classification
    # -----------------------------
    def _classify_sol_delta_balance_only(
        self,
        tx: dict,
        creator_pubkey: str,
        min_meaningful_lamports: int
    ) -> Tuple[int, Optional[str], str]:
        """
        Fast, RPC-encoding-agnostic classification based on pre/post SOL only.

        - delta from preBalances/postBalances for creator index.
        - classification:
            - system_transfer_in  (delta > +threshold)
            - system_transfer_out (delta < -threshold)
            - noise               (abs(delta) <= threshold)

        Counterparty is unknown with balance-only.
        """
        if not tx or "transaction" not in tx:
            return 0, None, "unknown"

        meta = tx.get("meta") or {}
        pre = meta.get("preBalances") or []
        post = meta.get("postBalances") or []
        if not pre or not post:
            return 0, None, "unknown"

        message = (tx.get("transaction") or {}).get("message") or {}
        account_keys = message.get("accountKeys") or []

        # accountKeys can be list[str] or list[dict] depending on encoding/provider
        creator_idx = None
        for idx, key in enumerate(account_keys):
            k = key.get("pubkey") if isinstance(key, dict) else str(key)
            if k == creator_pubkey:
                creator_idx = idx
                break

        if creator_idx is None or creator_idx >= len(pre) or creator_idx >= len(post):
            return 0, None, "unknown"

        delta = int(post[creator_idx]) - int(pre[creator_idx])

        if abs(delta) <= int(min_meaningful_lamports):
            return delta, None, "noise"

        if delta > 0:
            return delta, None, "system_transfer_in"
        return delta, None, "system_transfer_out"

    def _try_extract_system_transfer_counterparty(
        self,
        tx: dict,
        creator_pubkey: str,
        delta_lamports: int
    ) -> Optional[str]:
        """
        Best-effort counterparty extraction.
        Only works if the RPC provided parsed SystemProgram transfer instructions.
        If encoding='json' and provider doesn't include parsed, this will return None.

        We keep this as optional: SOL delta tracking works without it.
        """
        try:
            message = (tx.get("transaction") or {}).get("message") or {}
            instructions = message.get("instructions") or []
            if not instructions:
                return None

            system_program = "11111111111111111111111111111111"

            for instr in instructions:
                if instr.get("programId") != system_program:
                    continue
                parsed = instr.get("parsed")
                if not isinstance(parsed, dict):
                    continue
                if (parsed.get("type") or "").lower() != "transfer":
                    continue
                info = parsed.get("info") or {}
                src = info.get("source")
                dst = info.get("destination")
                if not src or not dst:
                    continue

                # If creator gained SOL, counterparty is the sender; else receiver.
                if delta_lamports > 0 and dst == creator_pubkey:
                    return src
                if delta_lamports < 0 and src == creator_pubkey:
                    return dst

            return None
        except Exception:
            return None

    # -----------------------------
    # Ingest one signature
    # -----------------------------
    async def process_signature(
        self,
        creator_pubkey: str,
        sig_info: dict,
        source: str = "poll",
        min_meaningful_lamports: int = None,
        fetch_counterparty_if_possible: bool = False
    ) -> bool:
        """
        Fetch tx, compute delta, insert into ledger if meaningful funding transfer.
        Returns True if inserted, else False.
        """
        signature = sig_info.get("signature")
        if not signature:
            return False

        if min_meaningful_lamports is None:
            min_meaningful_lamports = self.MIN_MEANINGFUL_LAMPORTS_DEFAULT

        block_time = int(sig_info.get("blockTime") or 0)
        slot = int(sig_info.get("slot") or 0)

        tx = await self.get_transaction(signature, encoding="json")
        if not tx:
            return False

        delta_lamports, _, cls = self._classify_sol_delta_balance_only(
            tx, creator_pubkey, min_meaningful_lamports=min_meaningful_lamports
        )

        # Only track meaningful in/out (skip noise)
        if cls == "noise" or cls == "unknown":
            return False

        counterparty = None
        if fetch_counterparty_if_possible:
            counterparty = self._try_extract_system_transfer_counterparty(tx, creator_pubkey, delta_lamports)

        meta = tx.get("meta") or {}
        fee_lamports = int(meta.get("fee") or 0)
        compute_units = int(meta.get("computeUnitsConsumed") or 0)

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
                int(delta_lamports), int(fee_lamports), int(compute_units),
                counterparty, cls, source, 1
            ))

            conn.commit()
            conn.close()
            return cursor.rowcount > 0

        except Exception as e:
            print(f"[CREATOR_WATCH] ⚠️ Error saving tx: {e}", flush=True)
            return False

    # -------------------------------------------------------------------------
    # NEW: Backfill PRE-migration (backwards, bounded by migration_blocktime)
    # -------------------------------------------------------------------------
    async def backfill_creator_pre_migration(
        self,
        creator_pubkey: str,
        migration_blocktime: Optional[int] = None,
        migration_signature: Optional[str] = None,
        lookback_days: int = 30,
        page_limit: int = 1000,
        max_pages: int = 200,
        skip_missing_blocktime: bool = True,
        per_page_concurrency: int = None,
        min_meaningful_lamports: int = None,
        fetch_counterparty_if_possible: bool = False,
        per_tx_delay_s: float = 0.0,
    ) -> Dict[str, int]:
        """
        Walk backwards through creator history and ingest ONLY pre-migration funding transfers.

        Rules:
          - Only process signatures where blockTime <= migration_blocktime
          - Stop once blockTime < migration_blocktime - lookback_days*86400
          - Uses 'before' pagination (newest -> older)

        Efficiency:
          - bulk "already in ledger" check per page
          - optional bounded concurrency per page
        """
        await self.ensure_session()

        if per_page_concurrency is None:
            per_page_concurrency = self.DEFAULT_BACKFILL_CONCURRENCY
        if min_meaningful_lamports is None:
            min_meaningful_lamports = self.MIN_MEANINGFUL_LAMPORTS_DEFAULT

        if migration_blocktime is None and migration_signature:
            migration_blocktime = await self.get_blocktime_for_signature(migration_signature)
            if migration_blocktime:
                print(f"[BACKFILL] 🕐 migration_blocktime={migration_blocktime} from sig={migration_signature[:16]}...", flush=True)

        if migration_blocktime is None:
            raise ValueError("backfill_creator_pre_migration requires migration_blocktime or migration_signature")

        cutoff_bt = int(migration_blocktime) - int(lookback_days) * 86400

        stats = {
            "pages": 0,
            "sigs_seen": 0,
            "sigs_skipped_post_migration": 0,
            "sigs_skipped_missing_bt": 0,
            "sigs_skipped_errored": 0,
            "sigs_skipped_already": 0,
            "tx_attempted": 0,
            "funding_rows_inserted": 0,
            "stopped_at_cutoff": 0,
        }

        before = None
        reached_cutoff = False
        started_pre_migration_region = False

        print(
            f"[BACKFILL] 🔎 Creator {creator_pubkey[:16]}... pre-migration backfill "
            f"(migration_bt={migration_blocktime}, lookback_days={lookback_days}, cutoff_bt={cutoff_bt}, "
            f"page_limit={page_limit}, concurrency={per_page_concurrency})",
            flush=True,
        )

        sem = asyncio.Semaphore(max(1, int(per_page_concurrency)))

        async def _proc_one(sig_info: dict) -> bool:
            async with sem:
                ok = await self.process_signature(
                    creator_pubkey,
                    sig_info,
                    source="backfill_pre_migration",
                    min_meaningful_lamports=min_meaningful_lamports,
                    fetch_counterparty_if_possible=fetch_counterparty_if_possible,
                )
                if per_tx_delay_s:
                    await asyncio.sleep(per_tx_delay_s)
                return ok

        for page in range(1, max_pages + 1):
            stats["pages"] += 1

            sigs = await self.get_signatures(creator_pubkey, before=before, limit=page_limit)
            if not sigs:
                break

            stats["sigs_seen"] += len(sigs)

            # Bulk existence check for this page
            page_sig_list = [s.get("signature") for s in sigs if s.get("signature")]
            existing = self._existing_signatures_in_ledger(page_sig_list)

            # We iterate oldest->newest in this page so inserts are chronological
            candidates: List[dict] = []

            for sig_info in reversed(sigs):
                sig = sig_info.get("signature")
                if not sig:
                    continue

                if sig_info.get("err") is not None:
                    stats["sigs_skipped_errored"] += 1
                    continue

                bt = sig_info.get("blockTime")

                if bt is None:
                    if skip_missing_blocktime:
                        stats["sigs_skipped_missing_bt"] += 1
                        continue
                    # else allow through (costs getTransaction)
                else:
                    bt = int(bt)

                    if bt > int(migration_blocktime):
                        stats["sigs_skipped_post_migration"] += 1
                        continue

                    started_pre_migration_region = True

                    if bt < cutoff_bt:
                        reached_cutoff = True
                        stats["stopped_at_cutoff"] = 1
                        break

                if sig in existing:
                    stats["sigs_skipped_already"] += 1
                    continue

                candidates.append(sig_info)

            if candidates:
                stats["tx_attempted"] += len(candidates)

                # Run bounded concurrency for this page
                tasks = [asyncio.create_task(_proc_one(si)) for si in candidates]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                inserted = sum(1 for r in results if r is True)
                stats["funding_rows_inserted"] += inserted

            if reached_cutoff:
                break

            before = sigs[-1].get("signature")

            if not started_pre_migration_region:
                print(
                    f"[BACKFILL] page={page}: still post-migration region, paging older... (before={before[:16]}...)",
                    flush=True,
                )
            else:
                print(
                    f"[BACKFILL] page={page}: candidates={len(candidates)} inserted_total={stats['funding_rows_inserted']} "
                    f"skipped_post_mig={stats['sigs_skipped_post_migration']} skipped_missing_bt={stats['sigs_skipped_missing_bt']} "
                    f"skipped_already={stats['sigs_skipped_already']}",
                    flush=True,
                )

            await asyncio.sleep(0.05)

        print(f"[BACKFILL] ✅ Done for {creator_pubkey[:16]}... stats={stats}", flush=True)
        return stats

    # -------------------------------------------------------------------------
    # Polling (forward)
    # -------------------------------------------------------------------------
    async def poll_creator(self, creator_pubkey: str, limit: int = 1000) -> int:
        """
        Poll creator for NEW signatures since last poll.

        IMPORTANT:
        - Use `until=last_sig` to get signatures NEWER than last_sig.
        - This avoids walking backwards and reprocessing history.
        """
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()
        cursor.execute("SELECT last_signature, last_slot FROM creator_state WHERE creator_pubkey = ?", (creator_pubkey,))
        row = cursor.fetchone()
        last_sig = row[0] if row else None
        conn.close()

        sigs = await self.get_signatures(creator_pubkey, until=last_sig, limit=limit)

        if not sigs:
            return 0

        # sigs are newest->oldest; we want chronological processing
        page_sig_list = [s.get("signature") for s in sigs if s.get("signature")]
        existing = self._existing_signatures_in_ledger(page_sig_list)

        candidates = []
        for sig_info in reversed(sigs):
            sig = sig_info.get("signature")
            if not sig or sig in existing:
                continue
            if sig_info.get("err") is not None:
                continue
            candidates.append(sig_info)

        processed = 0
        for sig_info in candidates:
            ok = await self.process_signature(
                creator_pubkey,
                sig_info,
                source="poll",
                min_meaningful_lamports=self.MIN_MEANINGFUL_LAMPORTS_DEFAULT,
                fetch_counterparty_if_possible=False,  # keep poll lightweight; flip on if you really need it
            )
            if ok:
                processed += 1

        # Update state to newest signature in the response (sigs[0])
        latest_sig = sigs[0].get("signature")
        latest_slot = int(sigs[0].get("slot") or 0)

        if latest_sig:
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

    def _check_migration_setting(self, key: str, default: bool = True) -> bool:
        """
        Read migration settings from JSON file.
        Returns the setting value or default if not found.
        """
        import json
        import os
        
        settings_file = "migration_settings.json"
        try:
            if os.path.exists(settings_file):
                with open(settings_file, 'r') as f:
                    settings = json.load(f)
                    return settings.get(key, default)
        except Exception as e:
            print(f"[CREATOR_WATCH] ⚠️ Error reading migration settings: {e}", flush=True)
        
        return default

    async def poll_all_creators(self):
        try:
            conn = sqlite3.connect(DB_PATH, timeout=5)
            cursor = conn.cursor()
            cursor.execute("SELECT creator_pubkey FROM creator_watch WHERE monitored = 1")
            creators = [row[0] for row in cursor.fetchall()]
            conn.close()
        except Exception as e:
            print(f"[CREATOR_WATCH] ⚠️ Error loading creators: {e}", flush=True)
            return

        if not creators:
            return

        print(f"[CREATOR_WATCH] 🔍 Polling {len(creators)} creators...", flush=True)

        for creator_pubkey in creators:
            processed = await self.poll_creator(creator_pubkey)
            if processed:
                print(f"[CREATOR_WATCH]    {creator_pubkey[:16]}... → {processed} new funding txs", flush=True)
            await asyncio.sleep(0.2)

    async def run_polling_loop(self, poll_interval: int = 30):
        await self.ensure_session()
        print(f"[CREATOR_WATCH] 🚀 Starting polling loop (interval: {poll_interval}s)", flush=True)

        while True:
            try:
                # Check migration settings each poll cycle
                should_poll = self._check_migration_setting('creator_history_check', default=True)
                
                if should_poll and self.polling_enabled:
                    await self.poll_all_creators()
                elif not should_poll:
                    # Creator Analysis is disabled - skip polling this cycle
                    pass
                
                await asyncio.sleep(poll_interval)
            except Exception as e:
                print(f"[CREATOR_WATCH] ⚠️ Polling error: {e}", flush=True)
                await asyncio.sleep(30)

    def pause_polling(self):
        self.polling_enabled = False
        print(f"[CREATOR_WATCH] ⏸️  Polling PAUSED", flush=True)
        return {"status": "paused"}

    def resume_polling(self):
        self.polling_enabled = True
        print(f"[CREATOR_WATCH] ▶️  Polling RESUMED", flush=True)
        return {"status": "resumed"}

    # -------------------------------------------------------------------------
    # Query helpers
    # -------------------------------------------------------------------------
    def get_recent_ledger(self, creator_pubkey: str, limit: int = 50) -> List[dict]:
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
            return [dict(r) for r in rows]
        except Exception as e:
            print(f"[CREATOR_WATCH] ⚠️ Error getting ledger: {e}", flush=True)
            return []

    def check_address_cross_references(self, address: str, creator_pubkey: str) -> Dict:
        if not HAS_UNIFIED_TRACKER:
            return {'status': 'unified_tracker_unavailable'}

        try:
            tracker = UnifiedRecipientTracker()
            other_creators = tracker.find_shared_recipients(creator_pubkey)

            if address in other_creators and other_creators[address]:
                print(f"[CROSS_REF] ⚠️ Address {address[:16]}... linked to {len(other_creators[address])} other creators!", flush=True)

                for other_creator in other_creators[address]:
                    tracker.log_cross_reference(address, creator_pubkey, other_creator, context="cross_creator_recipient")

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


# Convenience function for use in listener
async def start_creator_watch(rpc_url: str, helius_rpc: str = None, session: aiohttp.ClientSession = None):
    manager = CreatorWatchManager(rpc_url, helius_rpc, session)
    return manager
