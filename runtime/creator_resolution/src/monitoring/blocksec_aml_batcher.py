#!/usr/bin/env python3
"""
BlockSec AML API Batch Processor

The API is limited to 10 calls/day. This system:
1. Collects addresses from funders and recipients (2-3 per token)
2. Caches them in database to avoid duplicate lookups
3. Batches up to 100 addresses per API call every 2.4 hours
4. Stores labels back in address_labels table for future reference
5. Prevents redundant API calls for already-labeled addresses

Rate Limit: 10 calls/day = ~1 call every 2.4 hours (144 min)
Max Batch Size: 100 addresses per call
Expected Coverage: 24 tokens/day * 2-3 new addresses = 48-72 addresses/day
Batches Needed: ~1 per day (fits within 10 call limit)
"""

import sqlite3
import asyncio
import time
import hashlib
from typing import List, Dict, Optional, Set
from datetime import datetime, timedelta
import aiohttp

from src.utils.db_locking import managed_db_connect

DB_PATH = "flex_complete_database.db"
BLOCKSEC_API_KEY = "639144520c4d750bbce34dd8177314fff080de733c1773fb3667ce7754cb5738"
BLOCKSEC_API_URL = "https://aml.blocksec.com/address-label/api/v3/batch-labels"

# Batch timing: 24 hours / 10 calls = 144 minutes between batches
BATCH_INTERVAL_SECONDS = 144 * 60  # 8640 seconds = 2.4 hours
MAX_ADDRESSES_PER_BATCH = 100

# Solana chain ID
SOLANA_CHAIN_ID = -3


class BlockSecAMLBatcher:
    """Manages batching and caching of BlockSec AML label lookups."""

    def __init__(self):
        self.db_path = DB_PATH
        self.api_key = BLOCKSEC_API_KEY
        self.last_batch_time = None
        self._ensure_tables()

    def _ensure_tables(self):
        """Create necessary tables for batch tracking.

        X78.0 -- this runs on EVERY BlockSecAMLBatcher() instantiation
        (auto_batch_new_addresses() creates a new instance per call, and it
        is called on every single creator-funding extraction via
        _try_blocksec_batch's fire-and-forget background task). conn.close()
        was only reached on the success path; any exception between
        connect() and close() (e.g. this thread's TrackedConnection write
        lease already poisoned by an earlier leak elsewhere on the same
        reused asyncio.to_thread/event-loop worker thread) left the
        connection open, extending whatever lease it managed to acquire for
        the rest of that thread's life -- contributing to the
        self-perpetuating stall documented in
        docs/audits/x78_0_creator_funding_concurrency.md. conn is declared
        before the try so finally can safely close it regardless of which
        statement raised. Also checks sqlite_master first so a
        fully-migrated DB (true after the very first ever call) issues zero
        write statements here at all."""
        conn = None
        try:
            conn = sqlite3.connect(self.db_path, timeout=5)
            existing = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name IN ('blocksec_aml_cache', 'blocksec_batch_log')"
                ).fetchall()
            }
            cursor = conn.cursor()

            if "blocksec_aml_cache" not in existing:
                # Table to track which addresses have been queried
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS blocksec_aml_cache (
                        address TEXT PRIMARY KEY,
                        label_name TEXT,
                        category TEXT,
                        risk_level TEXT,
                        risk_score REAL,
                        aml_status TEXT,
                        raw_response TEXT,
                        queried_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        source TEXT DEFAULT 'blocksec'
                    )
                """)
                conn.commit()

            if "blocksec_batch_log" not in existing:
                # Table to track batch submissions for rate limiting
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS blocksec_batch_log (
                        batch_id TEXT PRIMARY KEY,
                        batch_size INTEGER,
                        addresses_submitted TEXT,
                        submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        api_response TEXT,
                        status TEXT
                    )
                """)
                conn.commit()
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def get_unlabeled_addresses(self, limit: int = 100) -> List[str]:
        """
        Get addresses that haven't been looked up yet via BlockSec AML.

        PRIORITIZES BY ACTIVITY:
        - Accounts used multiple times (funders/recipients to 2+ creators)
        - More likely to be infrastructure or significant accounts
        - Ignores one-off transactions (noise)

        Returns addresses ordered by:
        1. Total activity count (descending - most active first)
        2. Exclude addresses already in blocksec_aml_cache
        3. Exclude infrastructure accounts and known CEX wallets
        """
        # X76.3 -- conn declared before try so the finally below can close it
        # on every path (this function previously only closed on the one
        # explicit success path, leaking on any exception raised while
        # building/executing the two queries).
        conn = None
        try:
            conn = sqlite3.connect(self.db_path, timeout=5)
            cursor = conn.cursor()

            # Get addresses by activity level (used multiple times)
            # This prioritizes legitimate accounts over noise/one-time transfers
            cursor.execute("""
                SELECT address, activity_count FROM (
                    -- Funders: count how many creators they funded
                    SELECT
                        funder_address as address,
                        COUNT(DISTINCT creator_address) as activity_count
                    FROM creator_funders
                    WHERE funder_address NOT IN (
                        SELECT address FROM blocksec_aml_cache
                    )
                    AND is_cex = 0  -- Exclude known CEX wallets
                    GROUP BY funder_address
                    HAVING COUNT(DISTINCT creator_address) >= 2  -- Used 2+ times

                    UNION ALL

                    -- Recipients: count how many creators sent to them
                    SELECT
                        receiver_address as address,
                        COUNT(DISTINCT creator_address) as activity_count
                    FROM creator_receivers
                    WHERE receiver_address NOT IN (
                        SELECT address FROM blocksec_aml_cache
                    )
                    AND receiver_type NOT IN ('token_mint', 'bonding_curve')
                    GROUP BY receiver_address
                    HAVING COUNT(DISTINCT creator_address) >= 2  -- Used 2+ times
                )
                ORDER BY activity_count DESC  -- Prioritize most-used accounts
                LIMIT ?
            """, (limit,))

            addresses = [row[0] for row in cursor.fetchall()]

            if addresses:
                print(f"[BLOCKSEC] Found {len(addresses)} active addresses (used 2+ times)", flush=True)
            else:
                # Fallback: if no highly-active addresses, get any unlabeled
                cursor.execute("""
                    SELECT DISTINCT address FROM (
                        SELECT DISTINCT funder_address as address
                        FROM creator_funders
                        WHERE funder_address NOT IN (
                            SELECT address FROM blocksec_aml_cache
                        )
                        AND is_cex = 0

                        UNION

                        SELECT DISTINCT receiver_address as address
                        FROM creator_receivers
                        WHERE receiver_address NOT IN (
                            SELECT address FROM blocksec_aml_cache
                        )
                        AND receiver_type NOT IN ('token_mint', 'bonding_curve')
                    )
                    LIMIT ?
                """, (limit,))

                addresses = [row[0] for row in cursor.fetchall()]
                if addresses:
                    print(f"[BLOCKSEC] No highly-active addresses, falling back to {len(addresses)} less-active addresses", flush=True)

            return addresses

        except Exception as e:
            print(f"[BLOCKSEC] Error getting unlabeled addresses: {e}")
            return []
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def _load_batch_time(self):
        """Load last batch submission time from log."""
        try:
            # X76.3 -- managed_db_connect guarantees close() on exception.
            with managed_db_connect(self.db_path, timeout=5) as conn:
                cursor = conn.cursor()

                cursor.execute("""
                    SELECT submitted_at FROM blocksec_batch_log
                    ORDER BY submitted_at DESC
                    LIMIT 1
                """)
                row = cursor.fetchone()

            if row:
                # Parse timestamp and return as seconds since epoch
                dt = datetime.fromisoformat(row[0].replace('Z', '+00:00'))
                return dt.timestamp()
            return None

        except Exception:
            return None

    def _should_batch_now(self) -> bool:
        """Check if enough time has passed for next batch."""
        last_time = self._load_batch_time()

        if last_time is None:
            # Never batched before, do it now
            return True

        time_since_last = time.time() - last_time
        return time_since_last >= BATCH_INTERVAL_SECONDS

    async def submit_batch(self, addresses: List[str]) -> Dict:
        """
        Submit a batch of addresses to BlockSec AML API.

        Returns dict with:
        - success: bool
        - batch_id: str (hash of submission)
        - count: int (addresses submitted)
        - results: dict (address -> label mappings)
        - error: str (if failed)
        """
        if not addresses:
            return {
                "success": False,
                "error": "No addresses to submit",
                "count": 0
            }

        # Respect rate limit
        if not self._should_batch_now():
            time_since = time.time() - (self._load_batch_time() or 0)
            time_until = BATCH_INTERVAL_SECONDS - time_since
            return {
                "success": False,
                "error": f"Rate limited. Next batch in {int(time_until/60)} minutes",
                "count": len(addresses)
            }

        # Remove duplicates
        addresses = list(set(addresses))[:MAX_ADDRESSES_PER_BATCH]

        # Create batch ID
        batch_hash = hashlib.md5(
            ','.join(sorted(addresses)).encode()
        ).hexdigest()

        print(f"[BLOCKSEC] Submitting batch {batch_hash[:8]}... ({len(addresses)} addresses)")

        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "chain_id": SOLANA_CHAIN_ID,
                    "addresses": addresses
                }

                headers = {
                    "API-KEY": self.api_key,
                    "Content-Type": "application/json"
                }

                async with session.post(
                    BLOCKSEC_API_URL,
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        print(f"[BLOCKSEC] Batch {batch_hash[:8]} submitted successfully")

                        # Validate response format (should be dict, not list)
                        if not isinstance(data, dict):
                            print(f"[BLOCKSEC] Error: Expected dict response, got {type(data).__name__}")
                            self._log_batch(batch_hash, addresses, str(data), "failed")
                            return {
                                "success": False,
                                "error": f"Invalid response format: {type(data).__name__}",
                                "count": len(addresses)
                            }

                        # Parse and cache results
                        results = self._process_response(data, addresses)

                        # Log batch submission
                        self._log_batch(batch_hash, addresses, data, "success")

                        return {
                            "success": True,
                            "batch_id": batch_hash,
                            "count": len(addresses),
                            "results": results
                        }
                    else:
                        error_text = await resp.text()
                        print(f"[BLOCKSEC] API error {resp.status}: {error_text}")

                        self._log_batch(batch_hash, addresses, error_text, "failed")

                        return {
                            "success": False,
                            "error": f"API returned {resp.status}",
                            "count": len(addresses)
                        }

        except asyncio.TimeoutError:
            print("[BLOCKSEC] Request timeout")
            return {
                "success": False,
                "error": "Request timeout",
                "count": len(addresses)
            }
        except Exception as e:
            print(f"[BLOCKSEC] Error: {e}")
            return {
                "success": False,
                "error": str(e),
                "count": len(addresses)
            }

    def _process_response(self, api_response: Dict, addresses: List[str]) -> Dict:
        """
        Process BlockSec API response and cache results.

        Expected response format:
        {
            "code": 0,
            "message": "ok",
            "data": {
                "address": {
                    "address": "...",
                    "labels": [
                        {
                            "label": "Binance Hot Wallet",
                            "category": "exchange",
                            "score": 0.95
                        }
                    ]
                }
            }
        }
        """
        results = {}

        try:
            # X76.3 -- managed_db_connect guarantees close() on exception.
            with managed_db_connect(self.db_path, timeout=5) as conn:
                cursor = conn.cursor()

                # Extract data from response (already validated as dict by caller)
                data = api_response.get("data", {}) if isinstance(api_response, dict) else {}

                for addr in addresses:
                    addr_data = data.get(addr, {})
                    labels = addr_data.get("labels", [])

                    if labels:
                        # Take the top label (highest confidence)
                        top_label = labels[0]
                        label_name = top_label.get("label", "Unknown")
                        category = top_label.get("category", "other")
                        score = top_label.get("score", 0)

                        results[addr] = {
                            "label_name": label_name,
                            "category": category,
                            "risk_score": score
                        }

                        # Cache in database
                        cursor.execute("""
                            INSERT OR REPLACE INTO blocksec_aml_cache
                            (address, label_name, category, risk_score, raw_response, queried_at)
                            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                        """, (
                            addr,
                            label_name,
                            category,
                            score,
                            str(labels)
                        ))

                        print(f"[BLOCKSEC] {addr[:16]}... → {label_name} ({category}, score: {score})")

                        # Add to infrastructure or CEX mapping for future reference
                        self._add_to_mappings(addr, label_name, category)

                    else:
                        # No label found, still cache to avoid re-querying
                        results[addr] = {
                            "label_name": None,
                            "category": "unknown",
                            "risk_score": 0
                        }

                        cursor.execute("""
                            INSERT OR REPLACE INTO blocksec_aml_cache
                            (address, label_name, category, risk_score, queried_at)
                            VALUES (?, NULL, 'unknown', 0, CURRENT_TIMESTAMP)
                        """, (addr,))

                conn.commit()

        except Exception as e:
            print(f"[BLOCKSEC] Error processing response: {e}")

        return results

    def _log_batch(self, batch_id: str, addresses: List[str], response, status: str):
        """Log batch submission for rate limit tracking."""
        try:
            # X76.3 -- managed_db_connect guarantees close() on exception.
            with managed_db_connect(self.db_path, timeout=5) as conn:
                cursor = conn.cursor()

                cursor.execute("""
                    INSERT OR REPLACE INTO blocksec_batch_log
                    (batch_id, batch_size, addresses_submitted, api_response, status, submitted_at)
                    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (
                    batch_id,
                    len(addresses),
                    ','.join(addresses),
                    str(response)[:500],  # Truncate very long responses
                    status
                ))

                conn.commit()

        except Exception as e:
            print(f"[BLOCKSEC] Error logging batch: {e}")

    def _add_to_mappings(self, address: str, label_name: str, category: str):
        """
        Add BlockSec-labeled address to infrastructure or CEX mapping.

        This ensures we never need to query it again - it's cached locally
        and immediately recognized in future extractions.
        """
        try:
            # Determine if this is a CEX or infrastructure account
            is_cex = category.lower() in ('exchange', 'cex', 'trading')

            if is_cex:
                from src.utils.infra_mapping import add_cex_account

                # Extract exchange name from label (e.g., "Binance Hot Wallet" -> "Binance")
                exchange_name = label_name.split()[0] if label_name else "Unknown"

                add_cex_account(
                    address=address,
                    name=label_name,
                    exchange=exchange_name,
                    description=f"Auto-detected via BlockSec AML (confidence: high)",
                    tags=["blocksec", "aml", "auto-detected"],
                    risk_level="neutral"
                )
                print(f"[BLOCKSEC] ✓ Added to CEX mapping: {label_name}", flush=True)

            else:
                from src.utils.infra_mapping import add_infrastructure_account

                add_infrastructure_account(
                    address=address,
                    name=label_name,
                    category=category,
                    description=f"Auto-detected via BlockSec AML",
                    tags=["blocksec", "aml", "auto-detected", category],
                    risk_level="neutral"
                )
                print(f"[BLOCKSEC] ✓ Added to Infrastructure mapping: {label_name}", flush=True)

        except ImportError:
            print(f"[BLOCKSEC] Warning: Could not import src.utils.infra_mapping", flush=True)
        except Exception as e:
            print(f"[BLOCKSEC] Error adding to mappings: {e}", flush=True)

    def get_cached_label(self, address: str) -> Optional[Dict]:
        """Get cached label for an address if available."""
        try:
            conn = sqlite3.connect(self.db_path, timeout=5)
            cursor = conn.cursor()

            cursor.execute("""
                SELECT label_name, category, risk_score
                FROM blocksec_aml_cache
                WHERE address = ?
            """, (address,))

            row = cursor.fetchone()
            conn.close()

            if row:
                return {
                    "label_name": row[0],
                    "category": row[1],
                    "risk_score": row[2],
                    "source": "blocksec"
                }

            return None

        except Exception:
            return None

    def get_batch_stats(self) -> Dict:
        """Get stats on batch usage and next batch time."""
        try:
            conn = sqlite3.connect(self.db_path, timeout=5)
            cursor = conn.cursor()

            # Total cached
            cursor.execute("SELECT COUNT(*) FROM blocksec_aml_cache")
            cached_count = cursor.fetchone()[0]

            # Total batches submitted
            cursor.execute("SELECT COUNT(*) FROM blocksec_batch_log WHERE status = 'success'")
            successful_batches = cursor.fetchone()[0]

            # Last batch time
            cursor.execute("""
                SELECT submitted_at FROM blocksec_batch_log
                WHERE status = 'success'
                ORDER BY submitted_at DESC
                LIMIT 1
            """)
            last_batch = cursor.fetchone()

            conn.close()

            if last_batch:
                last_time = datetime.fromisoformat(last_batch[0].replace('Z', '+00:00'))
                next_batch_time = last_time + timedelta(seconds=BATCH_INTERVAL_SECONDS)
                time_until = next_batch_time - datetime.now(last_time.tzinfo)
                time_until_minutes = max(0, int(time_until.total_seconds() / 60))
            else:
                time_until_minutes = 0

            return {
                "cached_addresses": cached_count,
                "successful_batches": successful_batches,
                "rate_limit": "10 calls/day",
                "batch_interval_minutes": BATCH_INTERVAL_SECONDS // 60,
                "max_per_batch": MAX_ADDRESSES_PER_BATCH,
                "next_batch_in_minutes": time_until_minutes,
                "can_batch_now": self._should_batch_now()
            }

        except Exception as e:
            return {"error": str(e)}


async def auto_batch_new_addresses():
    """
    Called periodically to batch new addresses.
    Run every 30 minutes or so, will only submit batch if 2.4 hours passed.
    """
    batcher = BlockSecAMLBatcher()

    # Get unlabeled addresses
    addresses = batcher.get_unlabeled_addresses(limit=100)

    if not addresses:
        print("[BLOCKSEC] No new addresses to batch")
        return

    print(f"[BLOCKSEC] Found {len(addresses)} unlabeled addresses, preparing batch...")

    # Submit batch
    result = await batcher.submit_batch(addresses)

    if result["success"]:
        print(f"[BLOCKSEC] Batch complete: {result['count']} addresses, {len(result['results'])} labeled")
    else:
        print(f"[BLOCKSEC] Batch failed: {result['error']}")

    return result


if __name__ == "__main__":
    # Test the system
    batcher = BlockSecAMLBatcher()

    print("\n=== BlockSec AML Batcher Test ===\n")

    # Show stats
    stats = batcher.get_batch_stats()
    print("Current Stats:")
    for key, value in stats.items():
        print(f"  {key}: {value}")

    # Get unlabeled addresses
    addresses = batcher.get_unlabeled_addresses(limit=10)
    print(f"\nFound {len(addresses)} unlabeled addresses")
    for addr in addresses[:3]:
        print(f"  {addr}")

    # Try to submit batch (will respect rate limit)
    print("\nAttempting batch submission...")
    result = asyncio.run(batcher.submit_batch(addresses))
    print(f"Result: {result}")
