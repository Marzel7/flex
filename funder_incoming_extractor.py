#!/usr/bin/env python3
"""
Extract and save funder transfers (both incoming and outgoing) to database.

Uses Helius API for fast, efficient transaction history. Falls back to Solana RPC if needed.

For each funder for a creator:
1. Get recent transactions via Helius or Solana RPC
2. Parse SOL transfers where funder is involved (both IN and OUT)
3. For INCOMING: Identify sender addresses (funder received SOL)
4. For OUTGOING: Identify recipient addresses (funder sent SOL)
5. Classify senders/recipients (CEX, INFRA, unknown)
6. Save to funder_incoming_transfers table (incoming)
7. Save to funder_outgoing_transfers table (outgoing)
"""

import sqlite3
import asyncio
import aiohttp
from typing import Dict, List, Tuple, Optional
import sys
import time
import os
from functools import lru_cache
sys.path.insert(0, '/Users/kevinkeaveney/Dev/claude/flex')

from infra_mapping import get_account_info, get_cex_info
import requests

# Try to load environment variables from .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv not required, env vars can be set directly

DB_PATH = "pumpswap_tokens.db"
SOLANA_RPC = "https://api.mainnet-beta.solana.com"
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "").strip()
RPC_RATE_LIMIT_DELAY = 2  # 2 second delay between RPC calls to avoid rate limiting
MAX_RETRIES = 3  # Retry failed requests up to 3 times
LAMPORTS_PER_SOL = 1_000_000_000
USE_HELIUS = bool(HELIUS_API_KEY)  # Use Helius if API key is available


def get_creator_funders(creator_address: str) -> list:
    """Get all funders for a creator from database"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT funder_address, amount_sol
            FROM creator_funders
            WHERE creator_address = ?
            ORDER BY amount_sol DESC
        """,
            (creator_address,),
        )

        funders = [(row["funder_address"], row["amount_sol"]) for row in cursor.fetchall()]
        conn.close()
        return funders
    except Exception as e:
        print(f"[DB] Error getting funders: {e}")
        return []


@lru_cache(maxsize=50000)
def classify_sender(sender_address: str) -> Tuple[str, Optional[str], Optional[str]]:
    """Classify a sender address and return (sender_type, exchange_name, exchange_type)

    Results are cached to avoid repeated lookups for the same addresses.
    """

    # Check CEX first
    cex_info = get_cex_info(sender_address)
    if cex_info:
        return ("cex", cex_info.get('name'), cex_info.get('cex_type'))

    # Check infrastructure
    infra_info = get_account_info(sender_address)
    if infra_info:
        return ("infra", infra_info.get('name'), None)

    return ("unknown", None, None)


def _open_db_optimized():
    """Open SQLite connection with optimizations for bulk operations"""
    conn = sqlite3.connect(DB_PATH, timeout=60)
    # WAL mode: write-ahead logging for better concurrency
    conn.execute("PRAGMA journal_mode=WAL;")
    # NORMAL: sync after each transaction (faster than FULL, safer than OFF)
    conn.execute("PRAGMA synchronous=NORMAL;")
    # Store temp tables in memory for speed
    conn.execute("PRAGMA temp_store=MEMORY;")
    # Increase cache to 200MB for better performance
    conn.execute("PRAGMA cache_size=-200000;")
    return conn


def save_funder_incoming_transfer(sender_address: str, funder_address: str, amount_sol: float,
                                  tx_signature: str, block_time: Optional[int] = None):
    """Save a funder incoming transfer to database (store CEX/INFRA but mark as terminal)"""
    try:
        # Classify sender
        sender_type, exchange_name, exchange_type = classify_sender(sender_address)

        # Check if sender is CEX or INFRA (mark for display but don't trace through)
        is_cex = 1 if sender_type == "cex" else 0

        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()

        # Insert or update
        cursor.execute(
            """
            INSERT OR REPLACE INTO funder_incoming_transfers
            (sender_address, funder_address, amount_sol, sender_type, transaction_signature, block_time, is_cex, cex_exchange, cex_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (sender_address, funder_address, amount_sol, sender_type, tx_signature, block_time, is_cex, exchange_name, exchange_type),
        )

        conn.commit()
        conn.close()

        # Show marker for terminal accounts (don't trace further)
        marker = "🚫" if sender_type in ("cex", "infra") else "✅"
        print(f"[DB] {marker} Saved incoming: {sender_address[:16]}... → {funder_address[:16]}... | {amount_sol:.4f} SOL ({sender_type})")
        return True

    except Exception as e:
        print(f"[DB] Error saving incoming transfer: {e}")
        return False


def save_funder_outgoing_transfer(funder_address: str, recipient_address: str, amount_sol: float,
                                  tx_signature: str, block_time: Optional[int] = None):
    """Save a funder outgoing transfer to database (store CEX/INFRA but mark as terminal)"""
    try:
        # Classify recipient
        recipient_type, exchange_name, exchange_type = classify_sender(recipient_address)

        # Check if recipient is CEX or INFRA (mark for display but don't trace through)
        is_cex = 1 if recipient_type == "cex" else 0

        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()

        # Insert or update
        cursor.execute(
            """
            INSERT OR REPLACE INTO funder_outgoing_transfers
            (funder_address, recipient_address, amount_sol, recipient_type, transaction_signature, block_time, is_cex, cex_exchange, cex_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (funder_address, recipient_address, amount_sol, recipient_type, tx_signature, block_time, is_cex, exchange_name, exchange_type),
        )

        conn.commit()
        conn.close()

        # Show marker for terminal accounts (don't trace further)
        marker = "🚫" if recipient_type in ("cex", "infra") else "✅"
        print(f"[DB] {marker} Saved outgoing: {funder_address[:16]}... → {recipient_address[:16]}... | {amount_sol:.4f} SOL ({recipient_type})")
        return True

    except Exception as e:
        print(f"[DB] Error saving outgoing transfer: {e}")
        return False


def get_transactions_helius(address: str, limit: int = 1000) -> Optional[List[Dict]]:
    """Get transactions for an address via Helius API"""
    if not HELIUS_API_KEY:
        return None

    try:
        url = f"https://api.helius.xyz/v0/addresses/{address}/transactions?api-key={HELIUS_API_KEY}"
        print(f"[HELIUS] Fetching transactions for {address[:16]}...")
        response = requests.get(url, timeout=15)
        data = response.json()

        if isinstance(data, list):
            print(f"[HELIUS] Retrieved {len(data)} transactions")
            return data
        else:
            print(f"[HELIUS] Error: {data}")
            return None

    except Exception as e:
        print(f"[HELIUS] Error: {e}")
        return None


def get_transactions_for_address(address: str, limit: int = 100) -> List[Dict]:
    """Get recent transactions for an address via RPC with retry logic"""
    for attempt in range(MAX_RETRIES):
        try:
            time.sleep(RPC_RATE_LIMIT_DELAY)  # Rate limiting

            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignaturesForAddress",
                "params": [address, {"limit": limit}]
            }

            response = requests.post(SOLANA_RPC, json=payload, timeout=10)
            data = response.json()

            if 'error' in data:
                error_msg = data['error'].get('message', str(data['error']))
                if '429' in str(data['error']) or 'rate' in error_msg.lower():
                    print(f"[RPC] Rate limited (attempt {attempt + 1}/{MAX_RETRIES}): {error_msg}")
                    if attempt < MAX_RETRIES - 1:
                        time.sleep(5)  # Wait longer before retry
                        continue
                    return []
                else:
                    print(f"[RPC] Error: {data['error']}")
                    return []

            if 'result' not in data or not data['result']:
                return []

            return data['result']

        except Exception as e:
            print(f"[RPC] Error getting signatures (attempt {attempt + 1}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(5)  # Wait longer before retry
            else:
                return []

    return []


def parse_transaction(tx_sig: str) -> Optional[Dict]:
    """Parse a transaction to find SOL transfers with retry logic"""
    for attempt in range(MAX_RETRIES):
        try:
            time.sleep(RPC_RATE_LIMIT_DELAY)  # Rate limiting

            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [tx_sig, {"encoding": "json", "maxSupportedTransactionVersion": 0}]
            }

            response = requests.post(SOLANA_RPC, json=payload, timeout=10)
            data = response.json()

            if 'error' in data:
                error_msg = str(data['error']).lower()
                if '429' in str(data['error']) or 'rate' in error_msg:
                    if attempt < MAX_RETRIES - 1:
                        time.sleep(5)  # Wait longer before retry
                        continue
                return None

            if 'result' not in data or data['result'] is None:
                return None

            tx = data['result']

            return {
                'signature': tx_sig,
                'accounts': tx['transaction']['message']['accountKeys'],
                'pre_balances': tx['meta']['preBalances'],
                'post_balances': tx['meta']['postBalances'],
                'block_time': tx['blockTime'],
                'err': tx['meta']['err']
            }

        except Exception as e:
            print(f"[RPC] Error parsing transaction {tx_sig[:16]}... (attempt {attempt + 1}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(5)  # Wait longer before retry
            else:
                return None

    return None


def extract_transfers_for_funder(funder_address: str) -> Dict:
    """Extract incoming and outgoing SOL transfers for a funder address

    Checks database first - if data already exists, returns cached results.
    Only extracts from Helius/RPC if no data found.
    """
    print(f"\n[EXTRACT] Analyzing funder: {funder_address}")

    # OPTIMIZATION: Check if we already have extraction data for this funder
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()

        # Count existing transfers
        cursor.execute("SELECT COUNT(*) FROM funder_incoming_transfers WHERE funder_address = ?", (funder_address,))
        incoming_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM funder_outgoing_transfers WHERE funder_address = ?", (funder_address,))
        outgoing_count = cursor.fetchone()[0]

        # If we have data, return it instead of re-extracting
        if incoming_count > 0 or outgoing_count > 0:
            # Get total SOL
            cursor.execute("SELECT SUM(amount_sol) FROM funder_incoming_transfers WHERE funder_address = ?", (funder_address,))
            incoming_total = (cursor.fetchone()[0] or 0)

            cursor.execute("SELECT SUM(amount_sol) FROM funder_outgoing_transfers WHERE funder_address = ?", (funder_address,))
            outgoing_total = (cursor.fetchone()[0] or 0)

            conn.close()

            result = {
                'incoming_count': incoming_count,
                'outgoing_count': outgoing_count,
                'total_sol': incoming_total + outgoing_total,
                'source': 'database_cache'
            }
            print(f"[EXTRACT] ✅ Using cached data from DB: {incoming_count} IN, {outgoing_count} OUT")
            return result

        conn.close()
    except Exception as e:
        print(f"[EXTRACT] Database check error (will extract): {e}")
        # Continue with extraction if check fails

    # No cached data found - need to extract from blockchain
    print(f"[EXTRACT] No cache found - extracting from blockchain")

    incoming_transfers = []
    outgoing_transfers = []

    # Try Helius first (much faster), fall back to Solana RPC
    txs = None
    if USE_HELIUS:
        helius_txs = get_transactions_helius(funder_address, limit=1000)
        if helius_txs:
            # Convert Helius format to our format for processing
            txs = helius_txs
            is_helius = True
        else:
            print(f"[RPC] Helius failed, falling back to Solana RPC")
            is_helius = False
    else:
        is_helius = False

    if not txs:
        # Fall back to standard RPC
        sigs = get_transactions_for_address(funder_address, limit=200)
        print(f"[RPC] Found {len(sigs)} transactions for funder")
        if not sigs:
            return {'incoming_count': 0, 'outgoing_count': 0, 'total_sol': 0}
        txs = sigs
        is_helius = False

    # Parse each transaction
    for i, tx_data in enumerate(txs):
        try:
            if is_helius:
                # Parse Helius format
                tx_sig = tx_data.get('signature', '')
                timestamp = tx_data.get('timestamp')

                # Skip failed transactions
                if tx_data.get('type') == 'FAILED':
                    continue

                # Get native transfers from Helius enriched data
                native_transfers = tx_data.get('nativeTransfers', [])

                if not native_transfers:
                    continue

                # Check each native transfer
                for transfer in native_transfers:
                    from_addr = transfer.get('fromUserAccount', '')
                    to_addr = transfer.get('toUserAccount', '')
                    amount_lamports = transfer.get('amount', 0)
                    amount_sol = amount_lamports / LAMPORTS_PER_SOL

                    # Filter dust
                    if amount_sol < 0.001:
                        continue

                    # INCOMING: Funder is receiving
                    if to_addr == funder_address and from_addr:
                        if amount_sol > 0:
                            incoming_transfers.append({
                                'sender': from_addr,
                                'funder': funder_address,
                                'amount_sol': amount_sol,
                                'tx_sig': tx_sig,
                                'block_time': timestamp
                            })

                    # OUTGOING: Funder is sending
                    elif from_addr == funder_address and to_addr:
                        if amount_sol > 0:
                            outgoing_transfers.append({
                                'funder': funder_address,
                                'recipient': to_addr,
                                'amount_sol': amount_sol,
                                'tx_sig': tx_sig,
                                'block_time': timestamp
                            })
            else:
                # Parse RPC format
                sig_info = tx_data
                sig = sig_info['signature']

                # Parse transaction
                tx = parse_transaction(sig)
                if not tx:
                    continue

                # Skip if transaction failed
                if tx['err'] is not None:
                    continue

                accounts = tx['accounts']
                pre_balances = tx['pre_balances']
                post_balances = tx['post_balances']
                block_time = tx['block_time']

                # Find funder in accounts
                funder_idx = None
                try:
                    funder_idx = accounts.index(funder_address)
                except ValueError:
                    continue

                pre_balance = pre_balances[funder_idx]
                post_balance = post_balances[funder_idx]
                balance_change = post_balance - pre_balance

                # INCOMING: Funder's balance increased
                if balance_change > 0:
                    amount_lamports = balance_change
                    amount_sol = amount_lamports / 1e9

                    # Only save transfers > 0.001 SOL (filter dust)
                    if amount_sol > 0.001:
                        # Find sender (account that decreased by similar amount)
                        sender = None
                        best_match = None
                        best_diff = float('inf')

                        for j, (pre, post) in enumerate(zip(pre_balances, post_balances)):
                            if j == funder_idx:
                                continue

                            if pre > post:
                                # This account lost SOL
                                lost_amount = (pre - post) / 1e9

                                # Find the account that lost approximately the right amount
                                diff = abs(lost_amount - amount_sol)
                                if diff < best_diff:
                                    best_diff = diff
                                    best_match = (j, accounts[j], lost_amount)

                        # Use best match if it's close enough (within 5%)
                        if best_match and best_diff < amount_sol * 0.05:
                            sender = best_match[1]

                        if sender:
                            incoming_transfers.append({
                                'sender': sender,
                                'funder': funder_address,
                                'amount_sol': amount_sol,
                                'tx_sig': sig,
                                'block_time': block_time
                            })

                # OUTGOING: Funder's balance decreased
                elif balance_change < 0:
                    amount_lamports = abs(balance_change)
                    amount_sol = amount_lamports / 1e9

                    # Only save transfers > 0.001 SOL (filter dust)
                    if amount_sol > 0.001:
                        # Find recipient (account that increased by similar amount)
                        recipient = None
                        best_match = None
                        best_diff = float('inf')

                        for j, (pre, post) in enumerate(zip(pre_balances, post_balances)):
                            if j == funder_idx:
                                continue

                            if post > pre:
                                # This account gained SOL
                                gained_amount = (post - pre) / 1e9

                                # Find the account that gained approximately the right amount
                                diff = abs(gained_amount - amount_sol)
                                if diff < best_diff:
                                    best_diff = diff
                                    best_match = (j, accounts[j], gained_amount)

                        # Use best match if it's close enough (within 5%)
                        if best_match and best_diff < amount_sol * 0.05:
                            recipient = best_match[1]

                        if recipient:
                            outgoing_transfers.append({
                                'funder': funder_address,
                                'recipient': recipient,
                                'amount_sol': amount_sol,
                                'tx_sig': sig,
                                'block_time': block_time
                            })

        except Exception as e:
            print(f"[PARSE] Error processing transaction: {e}")
            continue

        if (i + 1) % 100 == 0:
            print(f"[PROGRESS] Processed {i + 1}/{len(txs)} transactions")

    # Save all transfers using batch inserts for speed
    incoming_saved = 0
    outgoing_saved = 0

    if incoming_transfers or outgoing_transfers:
        try:
            conn = _open_db_optimized()
            cur = conn.cursor()

            # Batch insert incoming transfers
            if incoming_transfers:
                incoming_rows = []
                for transfer in incoming_transfers:
                    sender_type, exchange_name, exchange_type = classify_sender(transfer['sender'])
                    is_cex = 1 if sender_type == "cex" else 0
                    incoming_rows.append((
                        transfer['sender'],
                        transfer['funder'],
                        transfer['amount_sol'],
                        sender_type,
                        transfer['tx_sig'],
                        transfer['block_time'],
                        is_cex,
                        exchange_name,
                        exchange_type
                    ))

                cur.executemany("""
                    INSERT OR REPLACE INTO funder_incoming_transfers
                    (sender_address, funder_address, amount_sol, sender_type, transaction_signature, block_time, is_cex, cex_exchange, cex_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, incoming_rows)
                incoming_saved = len(incoming_rows)

            # Batch insert outgoing transfers
            if outgoing_transfers:
                outgoing_rows = []
                for transfer in outgoing_transfers:
                    recipient_type, exchange_name, exchange_type = classify_sender(transfer['recipient'])
                    is_cex = 1 if recipient_type == "cex" else 0
                    outgoing_rows.append((
                        transfer['funder'],
                        transfer['recipient'],
                        transfer['amount_sol'],
                        recipient_type,
                        transfer['tx_sig'],
                        transfer['block_time'],
                        is_cex,
                        exchange_name,
                        exchange_type
                    ))

                cur.executemany("""
                    INSERT OR REPLACE INTO funder_outgoing_transfers
                    (funder_address, recipient_address, amount_sol, recipient_type, transaction_signature, block_time, is_cex, cex_exchange, cex_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, outgoing_rows)
                outgoing_saved = len(outgoing_rows)

            conn.commit()
            conn.close()

            print(f"[DB] Batch saved: {incoming_saved} incoming, {outgoing_saved} outgoing transfers")
        except Exception as e:
            print(f"[DB] Error batch saving transfers: {e}")

    total_sol = sum(t['amount_sol'] for t in incoming_transfers) + sum(t['amount_sol'] for t in outgoing_transfers)

    print(f"[SUMMARY] Funder {funder_address[:16]}...: {incoming_saved} incoming, {outgoing_saved} outgoing, {total_sol:.4f} SOL total")

    return {
        'incoming_count': incoming_saved,
        'outgoing_count': outgoing_saved,
        'total_sol': total_sol,
        'funder': funder_address
    }


def extract_for_creator(creator_address: str) -> Dict:
    """Extract incoming and outgoing transfers for all funders of a creator (async with bounded concurrency)"""
    print(f"\n{'='*80}")
    print(f"[START] Extracting funder transfers (IN/OUT) for creator: {creator_address}")
    print(f"{'='*80}")

    # Get all funders for this creator
    funders = get_creator_funders(creator_address)
    print(f"[DB] Found {len(funders)} funder(s) for this creator")

    if not funders:
        print("[RESULT] No funders found for creator")
        return {'error': 'no_funders'}

    # Run async extraction with bounded concurrency
    result = asyncio.run(_extract_all_funders_async(creator_address, funders))
    return result



async def _extract_all_funders_async(creator_address: str, funders: List[Tuple[str, float]]) -> Dict:
    """Process all funders concurrently with bounded concurrency (max 8 at a time)"""
    # Semaphore limits concurrent operations to 8
    sem = asyncio.Semaphore(8)

    async def process_funder(funder_addr: str, funder_amount: float) -> Dict:
        """Process single funder with semaphore constraint"""
        async with sem:
            # Run blocking extract_transfers_for_funder in thread pool
            return await asyncio.to_thread(extract_transfers_for_funder, funder_addr)

    # Process all funders concurrently
    tasks = [process_funder(addr, amount) for addr, amount in funders]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Aggregate results
    total_sol = 0
    total_incoming = 0
    total_outgoing = 0
    error_count = 0

    for result in results:
        if isinstance(result, Exception):
            error_count += 1
            print(f"[ERROR] Exception processing funder: {result}")
            continue
        
        if isinstance(result, dict):
            total_sol += result.get('total_sol', 0)
            total_incoming += result.get('incoming_count', 0)
            total_outgoing += result.get('outgoing_count', 0)

    # Mark extraction as complete by updating last_analyzed timestamp for all funders
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE creator_funders
            SET last_analyzed = CURRENT_TIMESTAMP
            WHERE creator_address = ?
        """, (creator_address,))
        conn.commit()
        conn.close()
        print(f"[DB] Marked extraction complete for all funders of {creator_address[:16]}...")
    except Exception as e:
        print(f"[DB] Error marking completion: {e}")

    print(f"\n{'='*80}")
    print(f"[COMPLETE] Extraction complete for {creator_address}")
    print(f"  Total incoming transfers: {total_incoming}")
    print(f"  Total outgoing transfers: {total_outgoing}")
    print(f"  Total SOL traced: {total_sol:.4f}")
    if error_count > 0:
        print(f"  ⚠ {error_count} errors during processing")
    print(f"  ✅ Funding Complete")
    print(f"{'='*80}\n")

    return {
        'creator': creator_address,
        'incoming_found': total_incoming,
        'outgoing_found': total_outgoing,
        'total_sol': total_sol,
        'status': 'complete'
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 funder_incoming_extractor.py <creator_address>")
        sys.exit(1)

    creator = sys.argv[1]
    result = extract_for_creator(creator)
    print(result)
