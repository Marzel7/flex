#!/usr/bin/env python3
"""
Real-time creator funding extractor.
Hooks into token migration events to extract creator funding immediately.

When a new token is detected as migrated:
  1. Get creator address from transaction
  2. Query all signatures BEFORE migration timestamp
  3. Extract SOL transfers TO creator (two types):
     - OUTGOING: Creator signed tx that moved SOL in (creator is fee payer)
     - INCOMING: Transfers where creator is recipient account (not signer)
  4. Save funder relationships to database
  5. Flag suspicious funding patterns

KEY DISTINCTION:
- FUNDING ACCOUNT: Fee payer who signed a transaction sending SOL
- RECIPIENT ACCOUNT: Account receiving SOL without necessarily signing
  (detected via balance change analysis or transaction parsing)
"""

import sqlite3
import asyncio
import aiohttp
import os
import time
from typing import Optional, Dict, List, Set, Iterable, Tuple
from datetime import datetime
from infra_mapping import INFRASTRUCTURE_ACCOUNTS, CEX_ACCOUNTS
from dust_addresses import DUST_ADDRESSES
from domain_extraction import extract_from_helius_transaction_async
from domain_mapping import register_domain, link_domain_to_address
from automatic_cex_detection import classify_addresses_from_funding

DB_PATH = "pumpswap_tokens.db"
# FIX #6: Remove hardcoded API key fallback — fail safe instead
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "").strip()
USE_HELIUS = bool(HELIUS_API_KEY)

# SNS Domain Resolver Configuration
SNS_API_BASE = "https://sns-api.bonfida.com"
SNS_PRIMARY_ENDPOINT = "/v2/user/fav-domains/"
DOMAIN_CACHE_TTL_SECS = 7 * 24 * 60 * 60  # 7 days local TTL

# Same RPC configuration as post_migration_analyzer for consistency
# RPC Configuration: Use Helius + Public Solana only (QuickNode removed)
RPC_URLS = []
if USE_HELIUS:
    RPC_URLS.append(f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}")
RPC_URLS.append("https://api.mainnet-beta.solana.com")  # Public fallback

MAX_CONCURRENT_RPC = 8  # FIX #8: Bound RPC concurrency (was unused BATCH_SIZE = 10)
# FIX #2: Pagination limit (was hardcoded inline as 100)
MAX_PAGES = 8
MAX_RETRIES = 5
RPC_TIMEOUT = 30

# Pump.Fun program ID - used to filter out Pump.Fun token operations
PUMPFUN_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"


class DomainResolver:
    """Resolve Solana domain names (SNS) for addresses with caching"""

    def __init__(self, db_path: str, session: aiohttp.ClientSession):
        self.db_path = db_path
        self.session = session
        self.mem: Dict[str, Tuple[Optional[str], int]] = {}  # address -> (domain_or_none, updated_at)
        self._ensure_table()

    def _ensure_table(self):
        """Create address_domains table if it doesn't exist"""
        conn = sqlite3.connect(self.db_path, timeout=60)
        cur = conn.cursor()
        
        # Domains cache table (for resolution state tracking)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS address_domains (
                address TEXT PRIMARY KEY,
                primary_domain TEXT,
                updated_at INTEGER
            )
        """)
        
        # Address tags table (persistent tags like INFRA and CEX)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS address_tags (
                address TEXT,
                tag_type TEXT,
                tag_value TEXT,
                source TEXT,
                first_seen_at INTEGER,
                PRIMARY KEY (address, tag_type, tag_value)
            )
        """)
        
        cur.execute("CREATE INDEX IF NOT EXISTS idx_address_tags_address ON address_tags(address)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_address_tags_type ON address_tags(tag_type)")
        
        conn.commit()
        conn.close()

    def _db_get(self, address: str) -> Optional[Tuple[Optional[str], int]]:
        """Get domain from database cache"""
        conn = sqlite3.connect(self.db_path, timeout=60)
        cur = conn.cursor()
        cur.execute("SELECT primary_domain, updated_at FROM address_domains WHERE address = ? LIMIT 1", (address,))
        row = cur.fetchone()
        conn.close()
        if not row:
            return None
        return (row[0], row[1])

    def _db_set_many(self, rows: List[Tuple[str, Optional[str], int]]):
        """Save multiple domain lookups to database cache"""
        conn = sqlite3.connect(self.db_path, timeout=60)
        cur = conn.cursor()
        cur.executemany("""
            INSERT OR REPLACE INTO address_domains (address, primary_domain, updated_at)
            VALUES (?, ?, ?)
        """, rows)
        conn.commit()
        conn.close()

    def _is_fresh(self, updated_at: int) -> bool:
        """Check if cached entry is still fresh"""
        return (int(time.time()) - updated_at) < DOMAIN_CACHE_TTL_SECS

    def _save_address_tag(self, address: str, domain: str):
        """Save a discovered domain as a persistent address tag and register it"""
        if not domain:
            return

        try:
            conn = sqlite3.connect(self.db_path, timeout=60)
            cur = conn.cursor()

            # Save domain tag (tag_type='domain', tag_value=actual domain name)
            cur.execute("""
                INSERT OR REPLACE INTO address_tags
                (address, tag_type, tag_value, source, first_seen_at)
                VALUES (?, 'domain', ?, 'sns_resolver', ?)
            """, (address, domain, int(time.time())))

            conn.commit()
            conn.close()

            # Register domain in persistent mapping
            register_domain(domain, domain_type='owned',
                          metadata={'owner': address, 'source': 'sns_resolution'},
                          source='sns_resolver')

            # Link address to domain in mapping
            link_domain_to_address(domain, address)

        except Exception as e:
            pass  # Non-critical

    async def resolve_primary_domains(self, addresses: Iterable[str]) -> Dict[str, Optional[str]]:
        """
        Resolve primary SNS domains for addresses using Bonfida's improved endpoint.
        Returns {address: 'name.sol' or None}.
        Uses SNS primary domains endpoint with batching and caching.
        Saves discovered domains as persistent address tags.
        
        Endpoint: GET /v2/user/fav-domains/{pubkeys}
        - Returns primary/favorite domains (what most explorers display)
        - Includes subdomains
        - More reliable than old endpoint
        - Supports up to 20 addresses per request
        """
        now = int(time.time())
        addrs = [a for a in set(addresses) if isinstance(a, str) and len(a) > 20]

        if not addrs:
            return {}

        out: Dict[str, Optional[str]] = {}
        missing: List[str] = []

        # 1) Check memory cache
        for a in addrs:
            if a in self.mem and self._is_fresh(self.mem[a][1]):
                out[a] = self.mem[a][0]
            else:
                missing.append(a)

        # 2) Check database cache
        still_missing: List[str] = []
        for a in missing:
            row = self._db_get(a)
            if row and self._is_fresh(row[1]):
                domain, ts = row
                self.mem[a] = (domain, ts)
                out[a] = domain
                # Save cached domain as persistent tag if found
                if domain:
                    self._save_address_tag(a, domain)
            else:
                still_missing.append(a)

        # 3) Query SNS API in batches of 20 using new v2/user/fav-domains endpoint
        to_persist: List[Tuple[str, Optional[str], int]] = []
        for i in range(0, len(still_missing), 20):
            batch = still_missing[i:i+20]
            pubkeys = ",".join(batch)
            url = f"https://sns-api.bonfida.com/v2/user/fav-domains/{pubkeys}"

            try:
                async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        # Mark as unknown but cache locally
                        for a in batch:
                            self.mem[a] = (None, now)
                            out[a] = None
                            to_persist.append((a, None, now))
                        continue

                    data = await resp.json()
                    # Response format: {pubkey: "domain"} (note: no .sol suffix in response)
                    # We need to add .sol back
                    for a in batch:
                        domain_name = data.get(a)
                        if isinstance(domain_name, str) and domain_name:
                            domain = f"{domain_name}.sol"  # Add .sol suffix
                        else:
                            domain = None
                        
                        self.mem[a] = (domain, now)
                        out[a] = domain
                        to_persist.append((a, domain, now))
                        
                        # Save domain as persistent tag if found
                        if domain:
                            self._save_address_tag(a, domain)

            except Exception as e:
                # On error, mark as unknown but cache short-term
                print(f"[DOMAIN_RESOLVER] ⚠ Error resolving batch: {e}", flush=True)
                for a in batch:
                    self.mem[a] = (None, now)
                    out[a] = None
                    to_persist.append((a, None, now))

            # Gentle throttle between batches
            await asyncio.sleep(0.05)

        if to_persist:
            self._db_set_many(to_persist)

        return out


class RealTimeCreatorFundingExtractor:
    """Extract creator funding in real-time when new tokens launch"""

    def __init__(self):
        self.processed_creators: Set[str] = set()
        self.session = None
        self.domain_resolver: Optional[DomainResolver] = None
        self.seen_bonding_curves: Set[str] = set()  # Cache bonding curves to skip trading noise
        self._rpc_sem = asyncio.Semaphore(MAX_CONCURRENT_RPC)  # FIX #8: Bound RPC concurrency

    async def init_session(self):
        """Initialize aiohttp session and domain resolver"""
        if not self.session:
            self.session = aiohttp.ClientSession()
        if not self.domain_resolver:
            self.domain_resolver = DomainResolver(DB_PATH, self.session)

        # Initialize domain registry
        from domain_mapping import init_domain_registry
        init_domain_registry()

        # Setup SQLite optimizations for performance
        self._setup_db_optimizations()

    def _setup_db_optimizations(self):
        """Configure SQLite for better performance (PRAGMA settings)"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA temp_store=MEMORY;")
            conn.execute("PRAGMA cache_size=-50000;")  # ~50MB cache (reduced from 200MB)
            conn.execute("PRAGMA busy_timeout=60000;")  # 60s timeout for locked DB
            conn.commit()
            conn.close()
            print("[PERF] SQLite optimizations applied (WAL mode, 50MB cache, 60s busy timeout)", flush=True)
        except Exception as e:
            print(f"[PERF] Warning: Could not apply SQLite optimizations: {e}", flush=True)

    async def close_session(self):
        """Close aiohttp session"""
        if self.session:
            await self.session.close()

    async def _post_rpc(self, payload: dict) -> Optional[dict]:
        """Post to RPC with failover chain + semaphore concurrency control - mirrors post_migration_analyzer approach"""
        async with self._rpc_sem:  # FIX #8: Bound concurrent RPC calls
            for attempt in range(MAX_RETRIES):
                # Try each RPC endpoint in the failover chain
                for rpc_url in RPC_URLS:
                    try:
                        async with self.session.post(
                            rpc_url,
                            json=payload,
                            timeout=aiohttp.ClientTimeout(total=RPC_TIMEOUT)
                        ) as resp:
                            # HTTP-level errors
                            if resp.status != 200:
                                if resp.status == 429:
                                    # Rate limited - check for Retry-After header
                                    retry_after = resp.headers.get("Retry-After")
                                    retry_delay = None
                                    if retry_after:
                                        try:
                                            retry_delay = float(retry_after)
                                        except (ValueError, TypeError):
                                            retry_delay = None

                                    wait_time = retry_delay or (0.5 * (2 ** attempt))
                                    await asyncio.sleep(min(30.0, wait_time))  # Cap at 30s
                                    continue
                                elif resp.status >= 500:
                                    # Server error, try next RPC
                                    continue
                                else:
                                    # Client error, don't retry
                                    return None

                            data = await resp.json()

                            # RPC-level errors
                            if "error" in data:
                                error_code = data["error"].get("code", -1)
                                # Retryable RPC errors
                                if error_code in {-32008, -32000, -32003, -32009}:
                                    continue
                                else:
                                    return None

                            # Success
                            if "result" in data:
                                return data

                    except asyncio.TimeoutError:
                        # Timeout on this RPC, try next
                        continue
                    except Exception as e:
                        # Other errors, try next RPC
                        continue

                # After trying all RPCs once, wait before next attempt
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(0.5 * (2 ** attempt))

        return None

    async def get_signatures_until_time(
        self, creator: str, until_timestamp: int, limit: int = 1000
    ) -> List[tuple]:
        """
        Get signatures UNTIL a specific timestamp (Unix seconds).
        Returns list of (signature, blockTime) tuples.
        """
        signatures = []
        before = None

        while True:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignaturesForAddress",
                "params": [
                    creator,
                    {
                        "limit": limit,
                        **({"before": before} if before else {})
                    }
                ]
            }

            result = await self._post_rpc(payload)
            if not result or "result" not in result:
                break

            sigs = result.get("result", [])
            if not sigs:
                break

            for sig_info in sigs:
                sig = sig_info["signature"]
                block_time = sig_info.get("blockTime", 0)

                # API returns signatures newest-to-oldest
                # We want all signatures BEFORE the target time (for pre-migration funding)
                # Skip anything at or after the target time
                if block_time and block_time >= until_timestamp:
                    # Still in the post-migration period, skip
                    continue

                # This signature is before target time, include it
                signatures.append((sig, block_time))

            # If we got fewer than requested, we've reached the end
            if len(sigs) < limit:
                break

            before = sigs[-1]["signature"]
            await asyncio.sleep(0.05)

        return signatures

    async def get_transaction(self, signature: str) -> Optional[Dict]:
        """Get transaction with RPC failover"""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTransaction",
            "params": [
                signature,
                {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}
            ]
        }
        result = await self._post_rpc(payload)
        if result and "result" in result:
            tx = result.get("result")
            # RPC may return null for old/pruned transactions
            if tx is not None:
                return tx
        return None

    def extract_sol_transfers(self, tx: Dict, creator: str) -> List[Dict]:
        """Extract SOL transfers to/from creator"""
        transfers = []

        try:
            if not tx or "meta" not in tx:
                return transfers

            meta = tx.get("meta", {})
            pre_balances = meta.get("preBalances", [])
            post_balances = meta.get("postBalances", [])
            accounts = tx.get("transaction", {}).get("message", {}).get("accountKeys", [])

            # Find creator account index
            creator_idx = None
            for idx, acc in enumerate(accounts):
                acc_str = acc.get("pubkey") if isinstance(acc, dict) else str(acc)
                if acc_str == creator:
                    creator_idx = idx
                    break

            if creator_idx is None:
                return transfers

            # Calculate balance change for creator
            if creator_idx < len(pre_balances) and creator_idx < len(post_balances):
                balance_change = post_balances[creator_idx] - pre_balances[creator_idx]

                # Only track meaningful amounts (> 1000 lamports = 0.000001 SOL)
                if abs(balance_change) > 1000:
                    amount_sol = abs(balance_change) / 1e9

                    # Determine direction
                    direction = "in" if balance_change > 0 else "out"

                    # FIX #3: Find best counterparty (account with opposite balance change)
                    # For multi-party transactions, identify the LARGEST opposite account (not smallest)
                    best_counterparty = None
                    best_match = 0  # FIX: was float('inf') — pick MAXIMUM magnitude

                    for idx2, acc2 in enumerate(accounts):
                        if idx2 == creator_idx or idx2 >= len(pre_balances) or idx2 >= len(post_balances):
                            continue

                        balance_change2 = post_balances[idx2] - pre_balances[idx2]

                        # Look for accounts with opposite direction
                        if direction == "in" and balance_change2 < 0:
                            # Best match is MOST negative (largest outflow = biggest sender) — FIX: > instead of <
                            if abs(balance_change2) > best_match:
                                best_match = abs(balance_change2)
                                best_counterparty = acc2.get("pubkey") if isinstance(acc2, dict) else str(acc2)
                        elif direction == "out" and balance_change2 > 0:
                            # Best match is MOST positive (largest inflow = primary recipient) — FIX: > instead of <
                            if balance_change2 > best_match:
                                best_match = balance_change2
                                best_counterparty = acc2.get("pubkey") if isinstance(acc2, dict) else str(acc2)

                    # Report the transfer with best counterparty found
                    # (if no counterparty found, use system/fee account as placeholder)
                    counterparty = best_counterparty or "SYSTEM"

                    transfers.append({
                        "direction": direction,
                        "counterparty": counterparty,
                        "amount_sol": amount_sol,
                    })

        except Exception as e:
            pass

        return transfers

    async def _save_funder(self, creator: str, funder: str, amount_sol: float):
        """Save funder relationship to database, accumulating amounts from multiple transfers"""
        try:
            from infra_mapping import is_infrastructure_account, is_cex_account, get_account_info
            from address_tags import get_domain_tag

            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            # First, check if this funder already exists for this creator
            cursor.execute("""
                SELECT amount_sol, is_classified, fully_analyzed
                FROM creator_funders
                WHERE creator_address = ? AND funder_address = ?
                LIMIT 1
            """, (creator, funder))
            existing = cursor.fetchone()

            # Get existing amount, or 0 if new
            existing_amount = existing[0] if existing else 0
            new_total_amount = existing_amount + amount_sol

            # Check if funder is a known CEX wallet
            cex_exchange = None
            cex_type = None
            is_classified = 0

            try:
                cursor.execute("""
                    SELECT exchange_name, wallet_type
                    FROM cex_wallets
                    WHERE cex_address = ? AND is_active = 1
                    LIMIT 1
                """, (funder,))
                cex_row = cursor.fetchone()
                if cex_row:
                    exchange, wallet_type = cex_row
                    cex_exchange = exchange
                    cex_type = wallet_type
                    is_classified = 1  # Mark as classified (already tagged)
                    print(f"[FUNDING] 🏛️ CEX FUNDER DETECTED: {exchange} {wallet_type} → {creator[:16]}... ({new_total_amount:.2f} SOL total)", flush=True)
            except:
                pass

            # Check if funder is infrastructure/automation account
            if not cex_exchange and is_infrastructure_account(funder):
                is_classified = 1  # Mark as classified (infrastructure)

                # Special handling for deBridge
                info = get_account_info(funder)
                if info and "debridge" in str(info.get("tags", [])).lower():
                    print(f"[FUNDING] 🌉 DEBRIDGE FUNDER DETECTED: {creator[:16]}... received {amount_sol:.6f} SOL from deBridge", flush=True)
                    # Tag creator for deBridge usage
                    try:
                        cursor.execute("""
                            INSERT OR REPLACE INTO creator_tags
                            (creator_address, tag, description, amount_sol)
                            VALUES (?, ?, ?, ?)
                        """, (creator, "uses_debridge", f"Creator receives transfers from deBridge", new_total_amount))
                        conn.commit()
                        print(f"[FUNDING] ✅ Tagged creator as 'uses_debridge' - Total: {new_total_amount:.6f} SOL", flush=True)
                    except Exception as tag_err:
                        print(f"[FUNDING] ⚠ Could not tag deBridge usage: {tag_err}", flush=True)

            # Check if funder is CEX via infra_mapping
            fully_analyzed_now = 0
            if not cex_exchange and is_cex_account(funder):
                is_classified = 1  # Mark as classified (CEX in mapping)

            # Skip history extraction for CEX/INFRA - mark as fully_analyzed immediately
            if cex_exchange or is_classified:
                fully_analyzed_now = 1
                print(f"[FUNDING] 🚫 Skipping history extraction for CEX/INFRA: {funder[:16]}... ({cex_exchange or 'INFRA'})", flush=True)

            # NOTE: For regular wallets: Do NOT set fully_analyzed at discovery time.
            # fully_analyzed should only be set AFTER actual extraction of incoming transfers.
            # Discovery only creates the record; extraction sets fully_analyzed=1 and last_analyzed timestamp.
            # BUT: For CEX/INFRA: Set fully_analyzed=1 immediately so we don't trace their history.

            cursor.execute("""
                INSERT OR REPLACE INTO creator_funders
                (creator_address, funder_address, amount_sol, first_detected_at, is_cex, cex_exchange, cex_type, is_classified, fully_analyzed)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?, ?, ?, ?, ?)
            """, (creator, funder, new_total_amount, 1 if cex_exchange else 0, cex_exchange, cex_type, is_classified, fully_analyzed_now))

            conn.commit()
            conn.close()

            # Resolve and cache domain names (non-blocking)
            if self.domain_resolver:
                try:
                    domains = await self.domain_resolver.resolve_primary_domains([funder, creator])
                    funder_domain = domains.get(funder)
                    creator_domain = domains.get(creator)

                    if funder_domain:
                        print(f"[DOMAIN] 🌐 Funder domain: {funder} → {funder_domain}", flush=True)
                    if creator_domain:
                        print(f"[DOMAIN] 🌐 Creator domain: {creator} → {creator_domain}", flush=True)
                except Exception as domain_err:
                    pass  # Domain resolution is non-critical

            # Look up and tag funder with local database label if available (non-blocking)
            try:
                from solscan_address_tagger import tag_funder_if_labeled, format_address_with_label
                label_info = tag_funder_if_labeled(funder)
                if label_info and label_info.get("label_name"):
                    formatted = format_address_with_label(funder, label_info)
                    print(f"[LABEL] 🏷️ Funder labeled: {formatted}", flush=True)
            except Exception as label_err:
                pass  # Label lookup is non-critical

        except:
            pass

    async def _save_recipient(self, creator: str, recipient: str, amount_sol: float):
        """Save recipient relationship to database (creator sent SOL to recipient)"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            # Check if recipient is INFRA or CEX account
            is_infra = recipient in INFRASTRUCTURE_ACCOUNTS
            is_cex = recipient in CEX_ACCOUNTS

            # Get classification info if available
            recipient_type = None
            recipient_name = None
            if is_infra:
                recipient_type = "INFRA"
                recipient_name = INFRASTRUCTURE_ACCOUNTS[recipient].get("name", "")
            elif is_cex:
                recipient_type = "CEX"
                recipient_name = CEX_ACCOUNTS[recipient].get("name", "")

            # Create table if needed
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS creator_receivers (
                    creator_address TEXT NOT NULL,
                    receiver_address TEXT NOT NULL,
                    amount_sol REAL,
                    receiver_type TEXT,
                    receiver_name TEXT,
                    first_detected_at TEXT,
                    PRIMARY KEY (creator_address, receiver_address)
                )
            """)

            # Add new columns if they don't exist (for existing tables)
            try:
                cursor.execute("ALTER TABLE creator_receivers ADD COLUMN receiver_type TEXT")
            except:
                pass  # Column already exists

            try:
                cursor.execute("ALTER TABLE creator_receivers ADD COLUMN receiver_name TEXT")
            except:
                pass  # Column already exists

            cursor.execute("""
                INSERT OR REPLACE INTO creator_receivers
                (creator_address, receiver_address, amount_sol, receiver_type, receiver_name, first_detected_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (creator, recipient, amount_sol, recipient_type, recipient_name))

            conn.commit()
            conn.close()

            # Look up and tag recipient with local database label if available (non-blocking)
            try:
                from solscan_address_tagger import tag_recipient_if_labeled, format_address_with_label
                label_info = tag_recipient_if_labeled(recipient)
                if label_info and label_info.get("label_name"):
                    formatted = format_address_with_label(recipient, label_info)
                    print(f"[LABEL] 🏷️ Recipient labeled: {formatted}", flush=True)
            except Exception:
                pass  # Label lookup is non-critical

        except:
            pass

    def _save_outgoing_transfer(self, creator: str, recipient: str, amount_sol: float, sig: str = None, block_time: int = None):
        """Save outgoing transfer from creator to recipient

        Checks against:
        1. CEX_ACCOUNTS mapping (immediate)
        2. cex_wallets table (manual + auto-detected)
        3. address_classification table (auto-detected with confidence)
        """
        try:
            from infra_mapping import is_cex_account, CEX_ACCOUNTS

            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            # Check if recipient is a known CEX wallet
            recipient_type = None
            exchange_name = None
            wallet_type = None
            is_cex = 0
            classification_confidence = None

            # Layer 1: Check CEX_ACCOUNTS mapping (immediate)
            if is_cex_account(recipient):
                cex_info = CEX_ACCOUNTS.get(recipient, {})
                exchange_name = cex_info.get("exchange", "Unknown")
                wallet_type = cex_info.get("name", "Exchange Wallet")
                is_cex = 1
                recipient_type = f"cex_{exchange_name.lower()}"
                print(f"[FUNDING] 💸 OUTGOING TO CEX: {creator[:16]}... → {exchange_name} {wallet_type} ({amount_sol:.2f} SOL)", flush=True)

            # Layer 2: Check cex_wallets table (manual + auto-detected)
            else:
                try:
                    cursor.execute("""
                        SELECT exchange_name, wallet_type
                        FROM cex_wallets
                        WHERE cex_address = ? AND is_active = 1
                        LIMIT 1
                    """, (recipient,))
                    cex_row = cursor.fetchone()
                    if cex_row:
                        exchange_name, wallet_type = cex_row
                        is_cex = 1
                        recipient_type = f"cex_{exchange_name.lower()}"
                        print(f"[FUNDING] 💸 OUTGOING TO CEX: {creator[:16]}... → {exchange_name} {wallet_type} ({amount_sol:.2f} SOL)", flush=True)
                except Exception as e:
                    pass

            # Layer 3: Check address_classification (auto-detected with confidence)
            if not is_cex:
                try:
                    cursor.execute("""
                        SELECT classification, confidence_score, solscan_exchange_name
                        FROM address_classification
                        WHERE address = ?
                        LIMIT 1
                    """, (recipient,))
                    class_row = cursor.fetchone()
                    if class_row:
                        classification, confidence, solscan_exch = class_row
                        if classification == 'cex_confirmed':  # Only high confidence
                            exchange_name = solscan_exch or "Detected CEX"
                            wallet_type = "Auto-detected"
                            is_cex = 1
                            classification_confidence = confidence
                            recipient_type = f"cex_autodetected_{exchange_name.lower()}"
                            print(f"[FUNDING] 💸 OUTGOING TO CEX (AUTO-DETECTED): {creator[:16]}... → {exchange_name} (confidence: {confidence}) ({amount_sol:.2f} SOL)", flush=True)
                except Exception as e:
                    pass

            cursor.execute("""
                INSERT OR REPLACE INTO creator_outgoing_transfers
                (creator_address, recipient_address, amount_sol, transaction_signature, block_time,
                 recipient_type, is_cex, cex_exchange, cex_type, classification_confidence, first_detected_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (creator, recipient, amount_sol, sig, block_time, recipient_type, is_cex,
                  exchange_name, wallet_type, classification_confidence))

            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[FUNDING] ⚠ Error saving outgoing transfer: {e}", flush=True)

    def get_creator_cex_outflows(self, creator: str) -> Dict:
        """Get all SOL transfers from creator to CEX addresses"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=5)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("""
                SELECT
                    recipient_address,
                    amount_sol,
                    cex_exchange,
                    cex_type,
                    classification_confidence,
                    transaction_signature,
                    first_detected_at
                FROM creator_outgoing_transfers
                WHERE creator_address = ? AND is_cex = 1
                ORDER BY amount_sol DESC
            """, (creator,))

            outflows = [dict(row) for row in cursor.fetchall()]
            conn.close()
            return outflows
        except Exception as e:
            print(f"[FUNDING] Error getting CEX outflows: {e}")
            return []

    async def extract_incoming_transfers(self, creator: str) -> Dict:
        """
        Search for incoming SOL transfers to creator by scanning recent transactions.
        This finds transfers where creator is a RECIPIENT (not signer).

        Alternative approach: We look at all recent transactions on-chain that mention
        the creator address and extract transfers TO the creator.
        """
        print(f"[REALTIME_FUNDING]    🔍 Searching for INCOMING transfers to creator...", flush=True)

        funders = {}
        max_attempts = 5
        attempt = 0

        # We'll need to search recent block transactions
        # This is a simplified version - in production, use indexed services
        try:
            # For now, return empty - we'd need to implement transaction scanning
            # This would require either:
            # 1. Scanning recent blocks manually
            # 2. Using a service like Helius that indexes transactions
            # 3. Using getSignaturesForAddress on all known funders (not scalable)
            return funders
        except Exception as e:
            print(f"[REALTIME_FUNDING]    ⚠ Error searching incoming: {e}", flush=True)
            return funders

    async def extract_outgoing_transfers(self, creator: str, after_timestamp: int, limit: int = 100) -> Dict:
        """
        Search for outgoing transfers FROM creator AFTER a specific timestamp (post-migration).
        Returns dict of recipient -> {amount: total_sol, count: tx_count}
        """
        print(f"[REALTIME_FUNDING]    🔍 Searching for OUTGOING transfers after migration...", flush=True)

        recipients = {}
        before = None
        max_sigs = 0

        try:
            # Get all signatures for the creator
            while max_sigs < limit:
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getSignaturesForAddress",
                    "params": [
                        creator,
                        {
                            "limit": 50,
                            **({"before": before} if before else {})
                        }
                    ]
                }

                result = await self._post_rpc(payload)
                if not result or "result" not in result:
                    break

                sigs = result.get("result", [])
                if not sigs:
                    break

                for sig_info in sigs:
                    sig = sig_info["signature"]
                    block_time = sig_info.get("blockTime", 0)

                    # We want signatures AFTER the migration time (post-migration)
                    if block_time and block_time <= after_timestamp:
                        # Before or at migration time, skip
                        continue

                    # This is post-migration, analyze it
                    tx = await self.get_transaction(sig)
                    if not tx:
                        continue

                    transfers = self.extract_sol_transfers(tx, creator)
                    for transfer in transfers:
                        if transfer["direction"] != "out":
                            continue

                        counterparty = transfer["counterparty"]
                        amount = transfer["amount_sol"]

                        if counterparty not in recipients:
                            recipients[counterparty] = {"amount": 0, "count": 0}

                        recipients[counterparty]["amount"] += amount
                        recipients[counterparty]["count"] += 1

                        # Save to database immediately
                        self._save_outgoing_transfer(creator, counterparty, amount, sig, block_time)

                    max_sigs += 1

                if len(sigs) < 50:
                    break

                before = sigs[-1]["signature"]
                await asyncio.sleep(0.1)  # Increased delay to reduce rate limiting

            return recipients

        except Exception as e:
            print(f"[REALTIME_FUNDING]    ⚠ Error searching outgoing: {e}", flush=True)
            return recipients

    async def extract_for_creator(self, creator: str, migration_timestamp_str: str) -> Dict:
        """
        Extract funding activity for a creator using Helius Enhanced API.
        Uses same reliable approach as standalone tmp.py script:
        - Single page fetch (100 txs) instead of rapid pagination
        - Proper delays between requests
        - Filters pre-migration transfers only
        - Excludes token mints and bonding curves from both directions
        - Skips transactions with ANY Pump.Fun token transfers (bonding curves, migrations)
        
        This is slower than pagination but avoids 429 rate limit errors.
        """
        # Check if already processed in this session
        if creator in self.processed_creators:
            return {"status": "already_processed"}

        # Mark as processed to prevent duplicate API calls in same session
        self.processed_creators.add(creator)

        # FIX #6: Fail safe if no Helius API key
        if not USE_HELIUS:
            print("[REALTIME_FUNDING] ⚠ No HELIUS_API_KEY set — skipping enriched extraction", flush=True)
            return {"creator": creator, "error": "no_helius_key", "status": "skipped"}

        try:
            # Parse migration timestamp
            if "T" in migration_timestamp_str:
                migration_dt = datetime.fromisoformat(migration_timestamp_str.replace("Z", "+00:00"))
            else:
                migration_dt = datetime.fromisoformat(migration_timestamp_str)

            migration_timestamp = int(migration_dt.timestamp())

            # Calculate 1 month cutoff (30 days back from migration)
            one_month_cutoff = migration_timestamp - (30 * 24 * 60 * 60)

            print(f"[REALTIME_FUNDING] 🔍 Extracting creator funding for {creator[:16]}...", flush=True)
            print(f"[REALTIME_FUNDING]    Migration timestamp: {migration_timestamp_str}", flush=True)
            print(f"[REALTIME_FUNDING]    Will fetch up to 1 month of history", flush=True)

            # Build exclusion set: token mints + bonding curves created by this creator
            exclude_set = set()

            # Get all tokens launched by this creator to exclude them
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT mint, bonding_curve_pda, create_tx_signature
                FROM token_analysis
                WHERE earliest_tx_creator = ?
            """, (creator,))
            creator_tokens = cursor.fetchall()

            # Get CREATE tx signature(s) - if multiple tokens, just use the first one
            # (we mainly want to avoid double-counting Jito tips on the CREATE tx)
            create_tx_signature = None
            for row in creator_tokens:
                mint, bonding_curve, create_sig = row
                if create_sig and not create_tx_signature:
                    create_tx_signature = create_sig
                if mint:
                    exclude_set.add(mint)
                if bonding_curve:
                    exclude_set.add(bonding_curve)

            # Exclude only funders already identified for THIS SPECIFIC CREATOR
            # Don't exclude globally analyzed funders, as they may fund multiple creators
            cursor.execute("""
                SELECT DISTINCT funder_address
                FROM creator_funders
                WHERE creator_address = ? AND fully_analyzed = 1
            """, (creator,))
            fully_analyzed = cursor.fetchall()
            for (funder,) in fully_analyzed:
                exclude_set.add(funder)

            # Check if creator is already tagged with deBridge usage
            # If so, skip deBridge transaction detection in the loop
            cursor.execute("""
                SELECT 1 FROM creator_tags
                WHERE creator_address = ? AND tag = ?
            """, (creator, "uses_debridge"))
            creator_uses_debridge = cursor.fetchone() is not None

            if creator_uses_debridge:
                print(f"[REALTIME_FUNDING]    ℹ Creator already tagged as 'uses_debridge', skipping detection", flush=True)

            if exclude_set:
                print(f"[REALTIME_FUNDING]    Excluding {len(exclude_set)} addresses (creator's tokens & bonding curves)", flush=True)

            # Use Helius Enhanced API - paginate through all transactions
            funders = {}
            recipients = {}
            filtered_dust = 0
            filtered_excluded = 0
            filtered_token_transfers = 0

            MIN_SOL = 0.001  # Filter dust

            print(f"[REALTIME_FUNDING]    Fetching all pre-migration transactions from Helius API...", flush=True)

            try:
                url = f"https://api-mainnet.helius-rpc.com/v0/addresses/{creator}/transactions"

                page_num = 0
                before_signature = None
                total_fetched = 0
                found_pre_migration = False
                empty_inbound_pages = 0

                while True:
                    page_num += 1

                    # Build URL with query parameters directly
                    # Note: Helius Enhanced API max limit is 100, not 1000
                    query_url = f"{url}?api-key={HELIUS_API_KEY}&limit=100&sort-order=desc&commitment=finalized"
                    if before_signature:
                        query_url += f"&before={before_signature}"

                    try:
                        # Log the RPC call
                        print(f"[REALTIME_FUNDING]    [PAGE {page_num}] RPC CALL #{page_num}...", flush=True)

                        async with self.session.get(
                                query_url,
                                timeout=aiohttp.ClientTimeout(total=30)
                            ) as resp:
                                if resp.status == 429:
                                    print(f"[REALTIME_FUNDING]    ⚠ Rate limited (429) on page {page_num}", flush=True)
                                    break

                                if resp.status != 200:
                                    txt = await resp.text()
                                    print(f"[REALTIME_FUNDING]    ⚠ Helius HTTP {resp.status} on page {page_num}", flush=True)
                                    break

                                page = await resp.json()
                                if not isinstance(page, list) or len(page) == 0:
                                    print(f"[REALTIME_FUNDING]    [PAGE {page_num}] No more transactions", flush=True)
                                    break

                                print(f"[REALTIME_FUNDING]    [PAGE {page_num}] fetched={len(page)} txs", flush=True)
                                total_fetched += len(page)

                                # Process transactions
                                page_has_pre_migration = False
                                earliest_tx_timestamp = None
                                page_funders_found = 0
                                page_dust_filtered = 0
                                page_excluded_filtered = 0
                                page_token_transfers_filtered = 0

                                for tx in page:
                                    tx_ts = tx.get("timestamp", 0)

                                    # Track earliest timestamp on this page
                                    if earliest_tx_timestamp is None or tx_ts < earliest_tx_timestamp:
                                        earliest_tx_timestamp = tx_ts

                                    # Extract domain names from transaction description
                                    # (both explicit .sol mentions and SNS domains for addresses in descriptions)
                                    try:
                                        domain_count, domains_found = await extract_from_helius_transaction_async(tx, creator, self.domain_resolver)
                                        if domain_count > 0:
                                            print(f"[DOMAIN] 📝 Found {domain_count} domain(s) in tx {tx.get('signature', '')[:16]}... {domains_found}", flush=True)
                                    except Exception as e:
                                        print(f"[DOMAIN] ⚠ Error during domain extraction: {e}", flush=True)

                                    # Extract service names from transaction description and tag creator
                                    try:
                                        from solscan_address_tagger import extract_service_names_from_description, tag_creator_with_services
                                        tx_description = tx.get("description", "")
                                        services = extract_service_names_from_description(tx_description)
                                        if services:
                                            tags_added = tag_creator_with_services(creator, services)
                                            if tags_added > 0:
                                                print(f"[SERVICES] 🏷️ Tagged creator with {tags_added} service(s): {', '.join(sorted(services))}", flush=True)
                                    except Exception:
                                        pass  # Service tagging is non-critical

                                    # Check for Jito tips in this transaction (using existing Helius data)
                                    # Only save as "uses_jitotip_other" if NOT the CREATE tx for current token
                                    try:
                                        tx_sig = tx.get("signature", "")
                                        if tx_sig and tx_sig != create_tx_signature:  # Skip CREATE tx
                                            tx_accounts = tx.get("accountKeys", []) or []
                                            native_transfers = tx.get("nativeTransfers", []) or []
                                            fee = tx.get("fee", 0)
                                            network_fee_sol = fee / 1e9
                                            tx_description = tx.get("description", "Unknown")  # Get Helius transaction type

                                            # Check for Jito tips via native transfers to Jito accounts
                                            for jito_addr in [
                                                '96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5',  # Jitotip 1
                                                'HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe',  # Jitotip 2
                                                'Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY',  # Jitotip 3
                                                'ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt6iGPaS49',  # Jitotip 4
                                                'DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh',  # Jitotip 5
                                                'ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwDcEt',  # Jitotip 6
                                                'DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL',  # Jitotip 7
                                                '3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnizKZ6jT',  # Jitotip 8
                                            ]:
                                                for transfer in native_transfers:
                                                    if transfer.get("toUserAccount") == jito_addr:
                                                        jitotip_amount = transfer.get("amount", 0) / 1e9
                                                        if jitotip_amount > 0:
                                                            total_cost_sol = network_fee_sol + jitotip_amount
                                                            tip_percentage = (jitotip_amount / total_cost_sol * 100) if total_cost_sol > 0 else 0

                                                            try:
                                                                cursor.execute("""
                                                                    INSERT OR IGNORE INTO creator_service_history
                                                                    (creator_address, tag, amount_sol, tx_signature, mint, network_fee_sol, tip_percentage, tx_type, created_at)
                                                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                                                                """, (creator, "uses_jitotip_other", jitotip_amount, tx_sig, None, network_fee_sol, tip_percentage, tx_description))
                                                                # FIX #8: Don't commit here - batch commit after page processing
                                                                print(f"[REALTIME_FUNDING]      ✅ Jito tip ({jitotip_amount:.6f} SOL, {tip_percentage:.1f}%) detected in {tx_description} tx {tx_sig[:20]}...", flush=True)
                                                            except Exception:
                                                                pass
                                                        break
                                    except Exception:
                                        pass  # Jito scanning is non-critical

                                    # Capture ALL transfers regardless of pre/post migration
                                    # (we want all funding sources, not just pre-migration)
                                    page_has_pre_migration = True
                                    found_pre_migration = True

                                    # FIX #7: Check if tx has native SOL transfers first
                                    # If nativeTransfers exist, process them even if there are token ops
                                    native = tx.get("nativeTransfers") or []
                                    if not native:
                                        # No SOL transfers - safe to skip if token ops present
                                        tx_programs = tx.get("programs") or []
                                        if PUMPFUN_PROGRAM in tx_programs:
                                            token_transfers = tx.get("tokenTransfers") or []
                                            if token_transfers:
                                                filtered_token_transfers += 1
                                                page_token_transfers_filtered += 1
                                                continue

                                        # Also check cached token ops (even non-Pump.Fun)
                                        token_transfers = tx.get("tokenTransfers") or []
                                        if token_transfers:
                                            skip_tx_for_token_ops = False
                                            for tt in token_transfers:
                                                mint = tt.get("mint")
                                                if mint and mint in self.seen_bonding_curves:
                                                    skip_tx_for_token_ops = True
                                                    break
                                                elif mint:
                                                    self.seen_bonding_curves.add(mint)

                                            if skip_tx_for_token_ops:
                                                filtered_token_transfers += 1
                                                page_token_transfers_filtered += 1
                                                continue

                                    # Check if deBridge is a signer in this transaction (ONLY if not already detected)
                                    # For cross-chain transfers, deBridge initiates but creator may not be direct signer
                                    # Skip this check if creator is already known to use deBridge
                                    if not creator_uses_debridge:
                                        tx_accounts = tx.get("accountKeys", []) or []
                                        debridge_account = "2snHHreXbpJ7UwZxPe37gnUNf7Wx7wv6UKDSR2JckKuS"

                                        if debridge_account in tx_accounts:
                                            # This transaction involves deBridge
                                            # Count it as a transfer from deBridge to creator
                                            # Note: We'll estimate a reasonable amount based on context
                                            # or mark for manual review
                                            print(f"[REALTIME_FUNDING] 🌉 DEBRIDGE TRANSACTION: {tx.get('signature', '')[:16]}...", flush=True)

                                            # Mark creator for deBridge usage
                                            try:
                                                conn = sqlite3.connect(DB_PATH, timeout=60)
                                                cursor = conn.cursor()
                                                cursor.execute("""
                                                    INSERT OR REPLACE INTO creator_tags
                                                    (creator_address, tag, description)
                                                    VALUES (?, ?, ?)
                                                """, (creator, "uses_debridge", f"Creator uses deBridge for cross-chain transfers"))
                                                conn.commit()
                                                conn.close()
                                                print(f"[REALTIME_FUNDING] ✅ Tagged creator as 'uses_debridge'", flush=True)
                                                # Update flag so we don't check again in this extraction run
                                                creator_uses_debridge = True
                                            except Exception as tag_err:
                                                print(f"[REALTIME_FUNDING] ⚠ Could not tag deBridge: {tag_err}", flush=True)

                                    # Process nativeTransfers (already extracted by FIX #7)
                                    for nt in native:
                                        frm = nt.get("fromUserAccount")
                                        to = nt.get("toUserAccount")
                                        amt = nt.get("amount", 0)

                                        if not isinstance(frm, str) or not isinstance(to, str):
                                            continue

                                        amount_sol = amt / 1_000_000_000

                                        # Filter dust
                                        if amount_sol < MIN_SOL:
                                            filtered_dust += 1
                                            page_dust_filtered += 1
                                            continue

                                        # Inbound: someone sent creator SOL
                                        if to == creator and amount_sol > 0:
                                            # Skip dust addresses (known plumbing accounts)
                                            if frm in DUST_ADDRESSES:
                                                filtered_dust += 1
                                                page_dust_filtered += 1
                                                continue

                                            if frm in exclude_set:
                                                filtered_excluded += 1
                                                page_excluded_filtered += 1
                                                continue

                                            if frm not in funders:
                                                funders[frm] = 0
                                                page_funders_found += 1
                                            funders[frm] += amount_sol
                                            await self._save_funder(creator, frm, amount_sol)

                                        # Outbound: creator sent SOL
                                        elif frm == creator and amount_sol > 0:
                                            # Filter dust on outbound too
                                            if amount_sol < MIN_SOL:
                                                filtered_dust += 1
                                                page_dust_filtered += 1
                                                continue

                                            if to in exclude_set:
                                                filtered_excluded += 1
                                                page_excluded_filtered += 1
                                                continue

                                            if to not in recipients:
                                                recipients[to] = 0
                                            recipients[to] += amount_sol
                                            await self._save_recipient(creator, to, amount_sol)

                                # FIX #8: Batch commit after page processing (includes Jito tips)
                                conn.commit()

                                # Log page summary
                                if page_funders_found > 0 or page_dust_filtered > 0 or page_excluded_filtered > 0 or page_token_transfers_filtered > 0:
                                    details = []
                                    if page_funders_found > 0:
                                        details.append(f"✓ {page_funders_found} new funders")
                                    if page_dust_filtered > 0:
                                        details.append(f"🚫 {page_dust_filtered} dust")
                                    if page_excluded_filtered > 0:
                                        details.append(f"🔄 {page_excluded_filtered} excluded")
                                    if page_token_transfers_filtered > 0:
                                        details.append(f"🪙 {page_token_transfers_filtered} token ops")
                                    print(f"[REALTIME_FUNDING]    [PAGE {page_num}] " + " | ".join(details), flush=True)

                                    # OPTIMIZATION: Early stopping if no inbound funding found
                                    if page_funders_found == 0:
                                        empty_inbound_pages += 1
                                    else:
                                        empty_inbound_pages = 0

                                    # Stop if we've found enough funding or hit empty pages
                                    if empty_inbound_pages >= 5 and len(funders) >= 5:
                                        print(f"[REALTIME_FUNDING] ✅ EARLY STOP: {len(funders)} funders found + {empty_inbound_pages} empty pages", flush=True)
                                        break
                                    elif len(funders) >= 50:
                                        print(f"[REALTIME_FUNDING] ✅ EARLY STOP: {len(funders)} funders found (sufficient coverage)", flush=True)
                                        break

                                # Set up next page - continue if within 1-month cutoff AND under 100 pages
                                should_continue = False
                                if page:
                                    # Check if we've reached the 1-month cutoff
                                    if earliest_tx_timestamp and earliest_tx_timestamp < one_month_cutoff:
                                        print(f"[REALTIME_FUNDING]    [PAGE {page_num}] Reached 1-month cutoff", flush=True)
                                        break

                                    # FIX #2: Check if we've reached MAX_PAGES limit
                                    if page_num >= MAX_PAGES:
                                        print(f"[REALTIME_FUNDING]    [PAGE {page_num}] Reached {MAX_PAGES} page limit", flush=True)
                                        break

                                    # Continue if we found pre-migration txs
                                    if page_has_pre_migration:
                                        should_continue = True
                                    # OR if the earliest tx on this page is still after migration (means older txs exist)
                                    elif earliest_tx_timestamp and earliest_tx_timestamp > migration_timestamp:
                                        should_continue = True
                                        print(f"[REALTIME_FUNDING]    [PAGE {page_num}] All post-migration, but continuing to find older txs...", flush=True)

                                    if should_continue:
                                        before_signature = page[-1].get("signature")
                                        if before_signature:
                                            await asyncio.sleep(0.5)  # Rate limit delay
                                        else:
                                            print(f"[REALTIME_FUNDING]    No more signatures available", flush=True)
                                            break
                                    else:
                                        print(f"[REALTIME_FUNDING]    Pagination complete (reached end)", flush=True)
                                        break
                                else:
                                    break

                    except asyncio.TimeoutError:
                        print(f"[REALTIME_FUNDING]    ⚠ Timeout on page {page_num}", flush=True)
                        break
                    except Exception as e:
                        print(f"[REALTIME_FUNDING]    ⚠ Error on page {page_num}: {e}", flush=True)
                        break

                    print(f"[REALTIME_FUNDING]    Total transactions fetched: {total_fetched}", flush=True)

            except Exception as e:
                print(f"[REALTIME_FUNDING]    ⚠ Error: {e}", flush=True)
                return {"creator": creator, "error": str(e)}
            
            # Summary
            total_inbound = sum(funders.values())
            total_outbound = sum(recipients.values())
            
            print(f"[REALTIME_FUNDING]    ✓ Inbound: {len(funders)} funders ({total_inbound:.2f} SOL)", flush=True)
            print(f"[REALTIME_FUNDING]    ✓ Outbound: {len(recipients)} recipients ({total_outbound:.2f} SOL)", flush=True)
            
            if filtered_dust > 0:
                print(f"[REALTIME_FUNDING]    ℹ Filtered {filtered_dust} dust transfers", flush=True)
            if filtered_excluded > 0:
                print(f"[REALTIME_FUNDING]    ℹ Filtered {filtered_excluded} internal transfers (token/curve)", flush=True)
            if filtered_token_transfers > 0:
                print(f"[REALTIME_FUNDING]    ℹ Filtered {filtered_token_transfers} token operations (swaps, migrations)", flush=True)
            
            # Show top funders
            if funders:
                sorted_funders = sorted(funders.items(), key=lambda x: x[1], reverse=True)[:3]
                for i, (funder, amount) in enumerate(sorted_funders, 1):
                    print(f"[REALTIME_FUNDING]    Funder #{i}: {funder[:16]}... → {amount:.2f} SOL", flush=True)

            # Trigger automatic CEX detection asynchronously (non-blocking)
            # This will classify new funding addresses and potentially discover new CEX wallets
            if funders:
                asyncio.create_task(self._run_automatic_cex_detection())

            # Trigger BlockSec AML batching (caches new addresses for batch submission)
            # Rate limited to 1 batch per 2.4 hours (10 calls/day = 24/10 hours between batches)
            asyncio.create_task(self._try_blocksec_batch())

            # Close database connection after all processing
            conn.close()

            return {
                "creator": creator,
                "status": "success",
                "funding_sources": len(funders),
                "total_inbound": total_inbound,
                "outgoing_transfers": len(recipients),
                "total_outbound": total_outbound,
                "funders": {k: v for k, v in sorted(funders.items(), key=lambda x: x[1], reverse=True)[:10]} if funders else {}
            }

        except Exception as e:
            print(f"[REALTIME_FUNDING] ⚠ Error: {e}", flush=True)
            return {"creator": creator, "error": str(e)}

    async def _run_automatic_cex_detection(self):
        """
        Run automatic CEX detection on classified funding addresses.
        
        This is called after funding extraction completes to classify any new
        addresses found in funding relationships. If high-confidence CEX wallets
        are detected, they are automatically added to the cex_wallets table.
        
        Runs non-blocking to avoid delaying token processing.
        """
        try:
            result = await classify_addresses_from_funding(max_addresses=200)
            
            if result.get("error"):
                print(f"[AUTO-CEX] Error during classification: {result.get('error')}", flush=True)
                return
            
            classified = result.get("classified", 0)
            confirmed = result.get("confirmed", 0)
            likely = result.get("likely", 0)
            total = result.get("total_analyzed", 0)
            
            if classified > 0:
                print(f"[AUTO-CEX] Classification complete: {classified} classified, {confirmed} confirmed, {likely} likely (from {total} addresses)", flush=True)
        
        except Exception as e:
            print(f"[AUTO-CEX] Error: {e}", flush=True)

    async def _try_blocksec_batch(self):
        """
        Try to submit a batch to BlockSec AML API for address labeling.
        
        Addresses are cached for batching since we're limited to 10 calls/day.
        This method:
        1. Collects new funders/recipients that haven't been labeled yet
        2. Checks if enough time has passed since last batch (2.4 hours)
        3. Submits batch if ready, or queues for next scheduled batch
        
        Runs non-blocking to avoid delaying token processing.
        """
        try:
            from blocksec_aml_batcher import BlockSecAMLBatcher, auto_batch_new_addresses
            
            # Just trigger the auto-batch function
            # It will check rate limits internally and only submit if ready
            result = await auto_batch_new_addresses()
            
            if result and result.get("success"):
                print(f"[BLOCKSEC] Batch submitted: {result['count']} addresses", flush=True)
            elif result and not result.get("success"):
                # Check if it's rate limited or an actual error
                if "Rate limited" in result.get("error", ""):
                    # This is normal - just log at debug level
                    batcher = BlockSecAMLBatcher()
                    stats = batcher.get_batch_stats()
                    if stats.get("next_batch_in_minutes"):
                        print(f"[BLOCKSEC] Rate limited. Next batch in {stats['next_batch_in_minutes']} minutes", flush=True)
                else:
                    print(f"[BLOCKSEC] Batch warning: {result.get('error')}", flush=True)
        
        except ImportError:
            # BlockSec module not available, skip silently
            pass
        except Exception as e:
            print(f"[BLOCKSEC] Error during batch attempt: {e}", flush=True)

    async def check_create_tx_for_jitotip(self, creator: str, create_tx_sig: str, mint: str = None):
        """Check if CREATE transaction uses Jitotip and tag creator if so"""
        if not create_tx_sig:
            return

        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            # Get list of Jitotip accounts from INFRASTRUCTURE_ACCOUNTS
            jitotip_accounts = [addr for addr in INFRASTRUCTURE_ACCOUNTS.keys() if "jito" in INFRASTRUCTURE_ACCOUNTS[addr].get("name", "").lower()]

            found_jitotip = False
            jitotip_amount = 0

            # Try Helius RPC first (more reliable), then fallback to public RPC
            rpc_urls = [
                f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}",  # Helius first
                "https://api.mainnet-beta.solana.com"  # Public fallback
            ]

            for rpc_url in rpc_urls:
                payload = {
                    "jsonrpc": "2.0",
                    "id": "1",
                    "method": "getTransaction",
                    "params": [create_tx_sig, {
                        "encoding": "json",
                        "maxSupportedTransactionVersion": 0
                    }]
                }

                try:
                    async with self.session.post(rpc_url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                            if resp.status == 200:
                                result = await resp.json()

                                if "result" in result and result["result"]:
                                    tx = result["result"]

                                    # Get account keys
                                    message = tx.get("transaction", {}).get("message", {})
                                    accounts = message.get('accountKeys', [])

                                    # Check if any Jitotip account is in the transaction
                                    for jito in jitotip_accounts:
                                        if jito in accounts:
                                            # Found Jitotip, check balance changes
                                            jito_idx = accounts.index(jito)
                                            meta = tx.get("meta", {})
                                            post_balances = meta.get('postBalances', [])
                                            pre_balances = meta.get('preBalances', [])

                                            if jito_idx < len(post_balances) and jito_idx < len(pre_balances):
                                                diff = post_balances[jito_idx] - pre_balances[jito_idx]
                                                if diff > 0:  # Jitotip received SOL
                                                    found_jitotip = True
                                                    jitotip_amount = diff / 1e9

                                                    # Calculate total tx cost (network fee + jito tip)
                                                    network_fee_lamports = tx.get("meta", {}).get("fee", 0)
                                                    network_fee_sol = network_fee_lamports / 1e9
                                                    total_cost_sol = network_fee_sol + jitotip_amount

                                                    # Calculate tip as % of total cost
                                                    tip_percentage = (jitotip_amount / total_cost_sol * 100) if total_cost_sol > 0 else 0

                                                    rpc_name = "Helius" if "helius" in rpc_url else "Public RPC"
                                                    print(f"[REALTIME_FUNDING] 🎯 JITOTIP DETECTED (via {rpc_name}) in CREATE tx: {creator[:16]}... sent {jitotip_amount:.9f} SOL ({tip_percentage:.1f}% of {total_cost_sol:.6f} SOL total cost) to {INFRASTRUCTURE_ACCOUNTS[jito].get('name', 'Jitotip')}", flush=True)
                                                    break

                                    # If found, break out of RPC loop
                                    if found_jitotip:
                                        break
                except Exception as rpc_err:
                    # Try next RPC on error
                    continue

            # If Jitotip found, tag the creator
            if found_jitotip:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS creator_tags (
                        creator_address TEXT NOT NULL,
                        tag TEXT NOT NULL,
                        description TEXT,
                        amount_sol REAL,
                        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY(creator_address, tag)
                    )
                """)

                # Create history table to track all tip amounts per transaction
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS creator_service_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        creator_address TEXT NOT NULL,
                        tag TEXT NOT NULL,
                        amount_sol REAL,
                        tx_signature TEXT,
                        mint TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        network_fee_sol REAL,
                        tip_percentage REAL,
                        UNIQUE(creator_address, tag, tx_signature)
                    )
                """)

                # 1. Save summary in creator_tags (for UI display - shows latest/highest)
                cursor.execute("""
                    INSERT OR REPLACE INTO creator_tags
                    (creator_address, tag, description, amount_sol)
                    VALUES (?, ?, ?, ?)
                """, (creator, "uses_jitotip", f"Creator uses Jitotip for MEV/fee tipping in CREATE transaction", jitotip_amount))

                # 2. Save to history table (full audit trail of all tips)
                try:
                    cursor.execute("""
                        INSERT OR IGNORE INTO creator_service_history
                        (creator_address, tag, amount_sol, tx_signature, mint, network_fee_sol, tip_percentage, tx_type)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (creator, "uses_jitotip", jitotip_amount, create_tx_sig, mint, network_fee_sol, tip_percentage, "Create"))
                except Exception as hist_err:
                    pass  # Ignore duplicates

                conn.commit()
                print(f"[REALTIME_FUNDING] ✅ Tagged creator as 'uses_jitotip' - Tip amount: {jitotip_amount:.6f} SOL ({tip_percentage:.1f}% of tx cost)", flush=True)

            conn.close()

        except Exception as e:
            print(f"[REALTIME_FUNDING] ⚠ Error checking CREATE tx for Jitotip: {e}", flush=True)

    async def check_transfers_for_meteora(self, creator: str):
        """Check if creator has inbound/outbound transfers to/from Meteora and tag if so"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            # Meteora Pool Authority
            meteora_account = "HLnpSz9h2S4hiLQ43rnSD9XkcUThA7B8hQMKmDaiTLcC"

            found_meteora = False
            meteora_amount = 0
            meteora_direction = None
            meteora_source = None

            # Check inbound (Meteora sending to creator)
            cursor.execute("""
                SELECT SUM(amount_sol) FROM creator_funders
                WHERE creator_address = ? AND funder_address = ?
            """, (creator, meteora_account))
            
            inbound_result = cursor.fetchone()
            if inbound_result and inbound_result[0]:
                found_meteora = True
                meteora_amount = inbound_result[0]
                meteora_direction = "inbound"
                meteora_source = "direct_transfer"
                print(f"[REALTIME_FUNDING] 🎯 METEORA DETECTED (inbound): {creator[:16]}... received {meteora_amount:.6f} SOL from Meteora", flush=True)

            # Check outbound (creator sending to Meteora)
            if not found_meteora:
                cursor.execute("""
                    SELECT SUM(amount_sol) FROM creator_receivers
                    WHERE creator_address = ? AND receiver_address = ?
                """, (creator, meteora_account))
                
                outbound_result = cursor.fetchone()
                if outbound_result and outbound_result[0]:
                    found_meteora = True
                    meteora_amount = outbound_result[0]
                    meteora_direction = "outbound"
                    meteora_source = "direct_transfer"
                    print(f"[REALTIME_FUNDING] 🎯 METEORA DETECTED (outbound): {creator[:16]}... sent {meteora_amount:.6f} SOL to Meteora", flush=True)

            # If Meteora found, tag the creator
            if found_meteora:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS creator_tags (
                        creator_address TEXT NOT NULL,
                        tag TEXT NOT NULL,
                        description TEXT,
                        amount_sol REAL,
                        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY(creator_address, tag)
                    )
                """)

                # Create history table if not exists
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS creator_service_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        creator_address TEXT NOT NULL,
                        tag TEXT NOT NULL,
                        amount_sol REAL,
                        tx_signature TEXT,
                        mint TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(creator_address, tag, tx_signature)
                    )
                """)

                # Save summary in creator_tags
                cursor.execute("""
                    INSERT OR REPLACE INTO creator_tags
                    (creator_address, tag, description, amount_sol)
                    VALUES (?, ?, ?, ?)
                """, (creator, "uses_meteora", f"Creator uses Meteora for {meteora_direction} transfers via {meteora_source}", meteora_amount))

                # Save to history table (each Meteora interaction)
                try:
                    cursor.execute("""
                        INSERT OR IGNORE INTO creator_service_history
                        (creator_address, tag, amount_sol, tx_signature)
                        VALUES (?, ?, ?, ?)
                    """, (creator, "uses_meteora", meteora_amount, None))
                except Exception as hist_err:
                    pass

                conn.commit()
                print(f"[REALTIME_FUNDING] ✅ Tagged creator as 'uses_meteora' - Amount: {meteora_amount:.6f} SOL", flush=True)

            conn.close()

        except Exception as e:
            print(f"[REALTIME_FUNDING] ⚠ Error checking transfers for Meteora: {e}", flush=True)

    async def check_for_meteora_program_interaction(self, creator: str):
        """Check if creator has interacted with Meteora program through transaction analysis
        
        This catches Meteora swaps/interactions that don't show as direct transfers.
        Since we don't have transaction signatures stored, we'd need to parse from extraction logs.
        For now, this method is a placeholder for future enhancement.
        """
        try:
            # NOTE: Full implementation would require storing transaction signatures
            # for all creator transfers and parsing them for Meteora program interactions.
            # This is noted for future enhancement when we store tx signatures in creator_receivers.
            pass
        except Exception as e:
            print(f"[REALTIME_FUNDING] ⚠ Error checking for Meteora program interaction: {e}", flush=True)

    async def check_transfers_for_debridge(self, creator: str):
        """Check if creator has inbound or outbound transfers to/from deBridge and tag if so"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            # deBridge vault
            debridge_account = "2snHHreXbpJ7UwZxPe37gnUNf7Wx7wv6UKDSR2JckKuS"

            found_debridge = False
            debridge_amount = 0
            debridge_direction = None

            # Check inbound (deBridge sending to creator)
            cursor.execute("""
                SELECT SUM(amount_sol) FROM creator_funders
                WHERE creator_address = ? AND funder_address = ?
            """, (creator, debridge_account))
            
            inbound_result = cursor.fetchone()
            if inbound_result and inbound_result[0]:
                found_debridge = True
                debridge_amount = inbound_result[0]
                debridge_direction = "inbound"
                print(f"[REALTIME_FUNDING] 🎯 DEBRIDGE DETECTED (inbound): {creator[:16]}... received {debridge_amount:.6f} SOL from deBridge", flush=True)

            # Check outbound (creator sending to deBridge)
            if not found_debridge:
                cursor.execute("""
                    SELECT SUM(amount_sol) FROM creator_receivers
                    WHERE creator_address = ? AND receiver_address = ?
                """, (creator, debridge_account))
                
                outbound_result = cursor.fetchone()
                if outbound_result and outbound_result[0]:
                    found_debridge = True
                    debridge_amount = outbound_result[0]
                    debridge_direction = "outbound"
                    print(f"[REALTIME_FUNDING] 🎯 DEBRIDGE DETECTED (outbound): {creator[:16]}... sent {debridge_amount:.6f} SOL to deBridge", flush=True)

            # If deBridge found, tag the creator
            if found_debridge:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS creator_tags (
                        creator_address TEXT PRIMARY KEY,
                        tag TEXT,
                        description TEXT,
                        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                cursor.execute("""
                    INSERT OR REPLACE INTO creator_tags
                    (creator_address, tag, description, amount_sol)
                    VALUES (?, ?, ?, ?)
                """, (creator, "uses_debridge", f"Creator uses deBridge for {debridge_direction} transfers", debridge_amount))

                conn.commit()
                print(f"[REALTIME_FUNDING] ✅ Tagged creator as 'uses_debridge'", flush=True)

            conn.close()

        except Exception as e:
            print(f"[REALTIME_FUNDING] ⚠ Error checking transfers for deBridge: {e}", flush=True)

    async def check_transfers_for_axiom(self, creator: str):
        """Check if creator has interactions with Axiom and tag if so"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            # Axiom automation account
            axiom_account = "AxiomRXZAq1Jgjj9pHmNqVP7Lhu67wLXZJZbaK87TTSk"

            found_axiom = False
            axiom_amount = 0
            axiom_direction = None

            # Check inbound (Axiom sending to creator)
            cursor.execute("""
                SELECT SUM(amount_sol) FROM creator_funders
                WHERE creator_address = ? AND funder_address = ?
            """, (creator, axiom_account))
            
            inbound_result = cursor.fetchone()
            if inbound_result and inbound_result[0]:
                found_axiom = True
                axiom_amount = inbound_result[0]
                axiom_direction = "inbound"
                print(f"[REALTIME_FUNDING] 📊 AXIOM DETECTED (inbound): {creator[:16]}... received {axiom_amount:.6f} SOL from Axiom", flush=True)

            # Check outbound (creator sending to Axiom)
            if not found_axiom:
                cursor.execute("""
                    SELECT SUM(amount_sol) FROM creator_receivers
                    WHERE creator_address = ? AND receiver_address = ?
                """, (creator, axiom_account))
                
                outbound_result = cursor.fetchone()
                if outbound_result and outbound_result[0]:
                    found_axiom = True
                    axiom_amount = outbound_result[0]
                    axiom_direction = "outbound"
                    print(f"[REALTIME_FUNDING] 📊 AXIOM DETECTED (outbound): {creator[:16]}... sent {axiom_amount:.6f} SOL to Axiom", flush=True)

            # If Axiom found, tag the creator
            if found_axiom:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS creator_tags (
                        creator_address TEXT PRIMARY KEY,
                        tag TEXT,
                        description TEXT,
                        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                cursor.execute("""
                    INSERT OR REPLACE INTO creator_tags
                    (creator_address, tag, description, amount_sol)
                    VALUES (?, ?, ?, ?)
                """, (creator, "uses_axiom", f"Creator uses Axiom automation/oracle services ({axiom_direction} transfers)", axiom_amount))

                conn.commit()
                print(f"[REALTIME_FUNDING] ✅ Tagged creator as 'uses_axiom'", flush=True)

            conn.close()

        except Exception as e:
            print(f"[REALTIME_FUNDING] ⚠ Error checking transfers for Axiom: {e}", flush=True)

    async def check_transactions_for_meteora_programs(self, creator: str):
        """
        Check if creator's transactions call Meteora DLMM program directly.
        This detects program-level interactions in inner instructions.
        
        Meteora programs to detect:
        - dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN (DLMM)
        """
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            # Check if creator is already tagged with Meteora usage
            cursor.execute("""
                SELECT 1 FROM creator_tags
                WHERE creator_address = ? AND tag = ?
            """, (creator, "uses_meteora"))
            
            if cursor.fetchone() is not None:
                print(f"[REALTIME_FUNDING]    ℹ Creator already tagged as 'uses_meteora', skipping detection", flush=True)
                conn.close()
                return

            conn.close()

            # Use Helius to get transactions and check for Meteora program calls
            meteora_dlmm = "dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN"
            
            found_meteora = False
            meteora_tx_count = 0
            
            print(f"[REALTIME_FUNDING]    🔍 Checking for Meteora DLMM program calls...", flush=True)

            try:
                url = f"https://api-mainnet.helius-rpc.com/v0/addresses/{creator}/transactions"
                query_url = f"{url}?api-key={HELIUS_API_KEY}&limit=50&sort-order=desc&commitment=finalized"

                # First get address transactions to find signatures
                async with self.session.get(query_url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                        if resp.status == 200:
                            address_txs = await resp.json()
                            
                            if isinstance(address_txs, list):
                                # Now fetch full details for each transaction to check inner instructions
                                signatures_to_check = [tx.get('signature') for tx in address_txs[:20] if tx.get('signature')]
                                
                                if signatures_to_check:
                                    # Fetch full transaction details
                                    tx_url = f"https://api.helius.xyz/v0/transactions?api-key={HELIUS_API_KEY}"
                                    tx_payload = {
                                        "transactions": signatures_to_check
                                    }
                                    
                                    async with self.session.post(tx_url, json=tx_payload, timeout=aiohttp.ClientTimeout(total=30)) as tx_resp:
                                        if tx_resp.status == 200:
                                            full_txs = await tx_resp.json()
                                            
                                            if isinstance(full_txs, list):
                                                for tx in full_txs:
                                                    instructions = tx.get("instructions", []) or []
                                                    
                                                    for instr in instructions:
                                                        # Check top-level program
                                                        program_id = instr.get("programId")
                                                        if program_id == meteora_dlmm:
                                                            found_meteora = True
                                                            meteora_tx_count += 1
                                                            print(f"[REALTIME_FUNDING] 🔄 METEORA DLMM CALL DETECTED (top-level): {tx.get('signature', '')[:16]}...", flush=True)
                                                            break
                                                        
                                                        # Check inner instructions
                                                        inner_instrs = instr.get("innerInstructions", []) or []
                                                        for inner_instr in inner_instrs:
                                                            inner_prog = inner_instr.get("programId")
                                                            if inner_prog == meteora_dlmm:
                                                                found_meteora = True
                                                                meteora_tx_count += 1
                                                                print(f"[REALTIME_FUNDING] 🔄 METEORA DLMM CALL DETECTED (inner): {tx.get('signature', '')[:16]}...", flush=True)
                                                                break
                                                        
                                                        if found_meteora:
                                                            break
                                                    
                                                    if found_meteora and meteora_tx_count >= 1:
                                                        # Found at least one Meteora interaction
                                                        break

            except Exception as e:
                print(f"[REALTIME_FUNDING]    ⚠ Error checking Helius for Meteora programs: {e}", flush=True)

            # If Meteora DLMM usage found, tag the creator
            if found_meteora:
                try:
                    conn = sqlite3.connect(DB_PATH, timeout=60)
                    cursor = conn.cursor()
                    
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS creator_tags (
                            creator_address TEXT,
                            tag TEXT,
                            description TEXT,
                            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            PRIMARY KEY (creator_address, tag)
                        )
                    """)

                    cursor.execute("""
                        INSERT OR IGNORE INTO creator_tags
                        (creator_address, tag, description)
                        VALUES (?, ?, ?)
                    """, (creator, "uses_meteora", f"Creator uses Meteora DLMM program ({meteora_tx_count} transaction(s))"))

                    conn.commit()
                    conn.close()
                    print(f"[REALTIME_FUNDING] ✅ Tagged creator as 'uses_meteora' (program-level detection)", flush=True)
                except Exception as e:
                    print(f"[REALTIME_FUNDING] ⚠ Error tagging Meteora: {e}", flush=True)

        except Exception as e:
            print(f"[REALTIME_FUNDING] ⚠ Error checking transactions for Meteora programs: {e}", flush=True)

    async def process_new_token(self, creator: str, migration_timestamp_str: str):
        """
        Process a newly detected token.
        Call from main listener when migration is detected.
        """
        # Ensure session is initialized
        await self.init_session()

        # Extract funding in background (don't block main listener)
        try:
            result = await self.extract_for_creator(creator, migration_timestamp_str)
            return result
        except Exception as e:
            print(f"[REALTIME_FUNDING] ⚠ Unexpected error: {e}", flush=True)
            return {"error": str(e)}


# Global instance
_extractor = None


async def get_extractor() -> RealTimeCreatorFundingExtractor:
    """Get or create global extractor instance"""
    global _extractor
    if not _extractor:
        _extractor = RealTimeCreatorFundingExtractor()
        await _extractor.init_session()
    return _extractor


async def extract_funding_for_new_token(creator: str, migration_timestamp_str: str, create_tx_signature: str = None, mint: str = None):
    """
    Public function to extract funding when new token detected.

    Call from pumpfun_curve_listener.py in handle_migration():
        await extract_funding_for_new_token(creator, migration_time, create_tx_sig, mint)
    """
    extractor = await get_extractor()
    result = await extractor.process_new_token(creator, migration_timestamp_str)

    # Check CREATE tx for Jitotip usage (if signature provided)
    if create_tx_signature:
        await extractor.check_create_tx_for_jitotip(creator, create_tx_signature, mint)

    # Check inbound/outbound transfers for infrastructure usage
    await extractor.check_transfers_for_meteora(creator)
    await extractor.check_transfers_for_debridge(creator)
    await extractor.check_transfers_for_axiom(creator)

    # Check for program-level calls to Meteora DLMM
    await extractor.check_transactions_for_meteora_programs(creator)

    # Extract post-migration outgoing transfers (token sales to recipients/exchanges)
    try:
        from datetime import datetime
        migration_dt = datetime.fromisoformat(migration_timestamp_str.replace('Z', '+00:00'))
        migration_timestamp = int(migration_dt.timestamp())
        await extractor.extract_outgoing_transfers(creator, migration_timestamp)
        print(f"[REALTIME_FUNDING] ✅ Extracted outgoing transfers for {creator[:16]}...", flush=True)
    except Exception as e:
        print(f"[REALTIME_FUNDING] ⚠ Error extracting outgoing transfers: {e}", flush=True)

    return result


if __name__ == "__main__":
    # Test with a known creator
    async def test():
        extractor = RealTimeCreatorFundingExtractor()
        await extractor.init_session()

        # Example: Extract for a specific creator
        creator = "cwPG1BF4GqAPDF8p"  # Replace with real creator
        timestamp = "2026-01-16T17:28:51"

        result = await extractor.extract_for_creator(creator, timestamp)
        print(f"\nResult: {result}")

        await extractor.close_session()

    asyncio.run(test())
