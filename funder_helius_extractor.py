#!/usr/bin/env python3
"""
Extract funder transfers (incoming and outgoing) using Helius Enhanced API.

Helius provides much better performance and rate limiting than public Solana RPC:
- Fast transaction history lookup
- Better rate limiting
- Batch transaction parsing

For each funder for a creator:
1. Get all transactions via Helius
2. Parse SOL transfers
3. Identify incoming (balance increases) and outgoing (balance decreases)
4. Classify senders/recipients (CEX, INFRA, unknown)
5. Save to database
"""

import sqlite3
import sys
import time
import os
from typing import Dict, List, Tuple, Optional
from datetime import datetime
import requests

sys.path.insert(0, '/Users/kevinkeaveney/Dev/claude/flex')

from infra_mapping import get_account_info, get_cex_info

# Import RPC metrics recorder for monitoring
try:
    from rpc_metrics_recorder import record_request, initialize_recorder
    initialize_recorder(plan_monthly_credits=50_000_000)
except ImportError:
    def record_request(*args, **kwargs):
        pass  # No-op if metrics recorder not available

# Try to load environment variables from .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv not required, env vars can be set directly

DB_PATH = "flex_complete_database.db"
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "").strip()
LAMPORTS_PER_SOL = 1_000_000_000


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
            (creator_address,),,

        source_file="funder_helius_extractor")

        funders = [(row["funder_address"], row["amount_sol"]) for row in cursor.fetchall()]
        conn.close()
        return funders
    except Exception as e:
        print(f"[DB] Error getting funders: {e}")
        return []


def classify_account(address: str) -> Tuple[str, Optional[str], Optional[str]]:
    """Classify an account address and return (type, name, cex_type)"""
    # Check CEX first
    cex_info = get_cex_info(address)
    if cex_info:
        return ("cex", cex_info.get('name'), cex_info.get('cex_type'))

    # Check infrastructure
    infra_info = get_account_info(address)
    if infra_info:
        return ("infra", infra_info.get('name'), None)

    return ("unknown", None, None)


def save_funder_incoming_transfer(sender_address: str, funder_address: str, amount_sol: float,
                                  tx_signature: str, block_time: Optional[int] = None):
    """Save a funder incoming transfer to database (store CEX/INFRA but mark as terminal)"""
    try:
        # Classify sender
        sender_type, exchange_name, exchange_type = classify_account(sender_address)

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
        recipient_type, exchange_name, exchange_type = classify_account(recipient_address)

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


def _link_funder_to_creator_if_applicable(funder_address: str, incoming_transfers: List[Dict]):
    """
    If the funder address is itself a creator, record its pre-migration funding.
    This links funder extraction results to creator_funders table.
    """
    if not incoming_transfers:
        return  # No incoming transfers, can't be a creator funder

    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Check if this funder address is a known creator
        cursor.execute("""
            SELECT earliest_tx_creator, created_at
            FROM token_analysis
            WHERE earliest_tx_creator = ?
            LIMIT 1
        """, (funder_address,))

        creator_info = cursor.fetchone()
        if not creator_info:
            conn.close()
            return  # Not a creator

        creator_addr = creator_info['earliest_tx_creator']
        created_at_str = creator_info['created_at']

        from datetime import datetime
        created_at = datetime.fromisoformat(created_at_str.replace('Z', '+00:00'))

        print(f"\n[CREATOR_LINK] {funder_address[:16]}... is a creator! Linking funding sources...")

        # For each incoming transfer, record as pre-migration funding
        # (we assume all recorded transfers are pre-migration since Helius provides historical data)
        for transfer in incoming_transfers:
            sender = transfer['sender']
            amount = transfer['amount_sol']

            cursor.execute("""
                INSERT OR REPLACE INTO creator_funders
                (creator_address, funder_address, amount_sol, first_detected_at, source_type)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP, 'extraction_incoming')
            """, (creator_addr, sender, amount))

        conn.commit()
        conn.close()

        print(f"[CREATOR_LINK] ✅ Recorded {len(incoming_transfers)} funding source(s) for creator")

    except Exception as e:
        print(f"[CREATOR_LINK] ⚠️ Error linking funder to creator: {e}")


def mark_funder_analyzed(funder_address: str, creator_address: str):
    """Mark a funder as analyzed by updating last_analyzed timestamp"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()

        cursor.execute(
            """
            UPDATE creator_funders
            SET last_analyzed = CURRENT_TIMESTAMP, fully_analyzed = 1
            WHERE creator_address = ? AND funder_address = ?
        """,
            (creator_address, funder_address),
        )

        conn.commit()
        conn.close()
        print(f"[DB] ✅ Marked extraction complete for {funder_address[:16]}...")
        return True
    except Exception as e:
        print(f"[DB] Error marking funder analyzed: {e}")
        return False


def get_transactions_helius(
    address: str,
    *,
    limit: int = 100,
    max_pages: int = 1,
    before: Optional[str] = None,
    timeout: int = 15,
    retries: int = 3,
) -> List[Dict]:
    """
    Get transactions for an address via Helius API with cost controls.

    PRODUCTION-READY COST REDUCTION:
    - Respects `limit` parameter (passed in query string)
    - Supports pagination via `before` cursor and `max_pages` cap
    - Implements exponential backoff + retry logic
    - Handles HTTP 429 (rate limit) with Retry-After support
    - Returns combined list across pages
    - Defaults optimized for realtime (limit=100, max_pages=1)

    Args:
        address: Funder address to query
        limit: Transactions per page (default 100 for realtime cost control)
        max_pages: Maximum pages to fetch (default 1 for realtime, 5+ for background)
        before: Pagination cursor (signature to start before)
        timeout: Request timeout in seconds
        retries: Number of retries on transient failures

    Returns:
        List of transaction objects across all pages

    Cost Notes:
        - Realtime defaults (limit=100, max_pages=1): ~1 API call
        - Background defaults (limit=100, max_pages=5): ~5 API calls
        - Never fetch all transactions; cap at max_pages
    """
    all_transactions = []
    current_before = before
    pages_fetched = 0

    for page_num in range(max_pages):
        attempt = 0
        while attempt < retries:
            try:
                # Build URL with pagination support
                url = f"https://api.helius.xyz/v0/addresses/{address}/transactions?api-key={HELIUS_API_KEY}&limit={limit}"
                if current_before:
                    url += f"&before={current_before}"

                print(f"[HELIUS] Page {page_num + 1}/{max_pages}: Fetching {limit} txs for {address[:16]}... (attempt {attempt + 1}/{retries})", flush=True)

                # Record RPC request for metrics
                start_time = time.time()
                response = requests.get(url, timeout=timeout)
                latency_ms = (time.time() - start_time) * 1000

                record_request(
                    section="funder_incoming",
                    provider="helius_enhanced",
                    method="helius_enhanced_addresses_transactions",
                    status_code=response.status_code,
                    latency_ms=latency_ms,
                    mode="realtime" if max_pages == 1 else "background",
                    retries=attempt,
                )

                # Handle rate limiting
                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    if retry_after:
                        try:
                            sleep_time = float(retry_after)
                        except (ValueError, TypeError):
                            sleep_time = 0.5 * (2 ** attempt)
                    else:
                        sleep_time = 0.5 * (2 ** attempt)

                    sleep_time = min(sleep_time, 30.0)  # Cap at 30s
                    print(f"[HELIUS] Rate limited (429). Sleeping {sleep_time:.1f}s...", flush=True)
                    time.sleep(sleep_time)
                    attempt += 1
                    continue

                # Handle server errors
                if response.status_code >= 500:
                    sleep_time = 0.5 * (2 ** attempt)
                    print(f"[HELIUS] Server error ({response.status_code}). Backing off {sleep_time:.1f}s...", flush=True)
                    time.sleep(sleep_time)
                    attempt += 1
                    continue

                # Handle other HTTP errors
                if response.status_code != 200:
                    print(f"[HELIUS] HTTP {response.status_code}: {response.text[:200]}")
                    return all_transactions if all_transactions else []

                data = response.json()

                # Validate response
                if not isinstance(data, list):
                    print(f"[HELIUS] Invalid response (expected list): {str(data)[:200]}")
                    return all_transactions if all_transactions else []

                if not data:
                    print(f"[HELIUS] Page {page_num + 1}: No more transactions")
                    return all_transactions

                print(f"[HELIUS] Page {page_num + 1}: Got {len(data)} transactions", flush=True)
                all_transactions.extend(data)
                pages_fetched += 1

                # Prepare for next page
                if len(data) < limit:
                    # Got fewer than limit, so we've reached the end
                    print(f"[HELIUS] Reached end (got {len(data)} < {limit})")
                    return all_transactions

                # Set cursor for next page (last tx signature)
                current_before = data[-1].get("signature")
                if not current_before:
                    print(f"[HELIUS] No signature in last transaction, stopping")
                    return all_transactions

                break  # Success, exit retry loop

            except requests.Timeout:
                sleep_time = 0.5 * (2 ** attempt)
                print(f"[HELIUS] Timeout. Backing off {sleep_time:.1f}s... (attempt {attempt + 1}/{retries})", flush=True)
                time.sleep(sleep_time)
                attempt += 1

            except requests.ConnectionError as e:
                sleep_time = 0.5 * (2 ** attempt)
                print(f"[HELIUS] Connection error: {e}. Backing off {sleep_time:.1f}s... (attempt {attempt + 1}/{retries})", flush=True)
                time.sleep(sleep_time)
                attempt += 1

            except Exception as e:
                print(f"[HELIUS] Unexpected error: {e}")
                return all_transactions if all_transactions else []

        # If we exhausted retries for this page
        if attempt >= retries:
            print(f"[HELIUS] Exhausted retries for page {page_num + 1}")
            break

    print(f"[HELIUS] Total: Fetched {len(all_transactions)} transactions across {pages_fetched} pages", flush=True)
    return all_transactions


def extract_transfers_for_funder(funder_address: str, *, mode: str = "realtime") -> Dict:
    """
    Extract incoming and outgoing SOL transfers for a funder address using Helius.

    COST-CONTROLLED DEFAULTS:
    - Realtime mode: limit=100, max_pages=1 (typically 1 API call)
    - Background mode: limit=100, max_pages=5 (typically 5 API calls)

    Args:
        funder_address: Address to analyze
        mode: "realtime" (token detection) or "background" (12h scan)

    Returns:
        Dict with incoming_count, outgoing_count, total_sol
    """
    print(f"\n[EXTRACT] Analyzing funder: {funder_address} (mode={mode})", flush=True)

    incoming_transfers = []
    outgoing_transfers = []

    # Get transactions with cost-controlled defaults based on mode
    if mode == "realtime":
        # Realtime: tight controls for token detection
        txs = get_transactions_helius(funder_address, limit=100, max_pages=1, timeout=15)
    else:
        # Background: deeper scan for 12h enrichment (still bounded)
        txs = get_transactions_helius(funder_address, limit=100, max_pages=5, timeout=20)

    if not txs:
        return {'incoming_count': 0, 'outgoing_count': 0, 'total_sol': 0}

    print(f"[RPC] Found {len(txs)} transactions for funder")

    # Parse each transaction
    for i, tx in enumerate(txs):
        try:
            # Get transaction info
            tx_sig = tx.get('signature', '')
            timestamp = tx.get('timestamp')

            # Skip failed transactions
            if tx.get('type') == 'FAILED':
                continue

            # Get native transfers from Helius enriched data
            native_transfers = tx.get('nativeTransfers', [])

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
                        print(f"[INCOMING] {from_addr[:16]}... → {funder_address[:16]}... | {amount_sol:.4f} SOL")

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
                        print(f"[OUTGOING] {funder_address[:16]}... → {to_addr[:16]}... | {amount_sol:.4f} SOL")

        except Exception as e:
            print(f"[PARSE] Error parsing transaction: {e}")
            continue

        if (i + 1) % 100 == 0:
            print(f"[PROGRESS] Processed {i + 1}/{len(txs)} transactions")

    # Save all incoming transfers to database
    incoming_saved = 0
    for transfer in incoming_transfers:
        if save_funder_incoming_transfer(
            transfer['sender'],
            transfer['funder'],
            transfer['amount_sol'],
            transfer['tx_sig'],
            transfer['block_time']
        ):
            incoming_saved += 1

    # Save all outgoing transfers to database
    outgoing_saved = 0
    for transfer in outgoing_transfers:
        if save_funder_outgoing_transfer(
            transfer['funder'],
            transfer['recipient'],
            transfer['amount_sol'],
            transfer['tx_sig'],
            transfer['block_time']
        ):
            outgoing_saved += 1

    total_sol = sum(t['amount_sol'] for t in incoming_transfers) + sum(t['amount_sol'] for t in outgoing_transfers)

    print(f"[SUMMARY] Funder {funder_address[:16]}...: {incoming_saved} incoming, {outgoing_saved} outgoing, {total_sol:.4f} SOL total")

    # NEW: Check if this funder address is itself a creator, and link pre-migration funding
    _link_funder_to_creator_if_applicable(funder_address, incoming_transfers)

    return {
        'incoming_count': incoming_saved,
        'outgoing_count': outgoing_saved,
        'total_sol': total_sol,
        'funder': funder_address
    }


def extract_for_creator(creator_address: str) -> Dict:
    """Extract incoming and outgoing transfers for all funders of a creator"""
    print(f"\n{'='*80}")
    print(f"[START] Extracting funder transfers (IN/OUT) for creator: {creator_address}")
    print(f"{'='*80}")

    # Get all funders for this creator
    funders = get_creator_funders(creator_address)
    print(f"[DB] Found {len(funders)} funder(s) for this creator")

    if not funders:
        print("[RESULT] No funders found for creator")
        return {'error': 'no_funders'}

    # Extract for each funder
    total_sol = 0
    total_incoming = 0
    total_outgoing = 0

    for funder_addr, funder_amount in funders:
        result = extract_transfers_for_funder(funder_addr)
        total_sol += result['total_sol']
        total_incoming += result['incoming_count']
        total_outgoing += result['outgoing_count']

    print(f"\n{'='*80}")
    print(f"[COMPLETE] Extraction complete for {creator_address}")
    print(f"  Total incoming transfers: {total_incoming}")
    print(f"  Total outgoing transfers: {total_outgoing}")
    print(f"  Total SOL traced: {total_sol:.4f}")
    print(f"{'='*80}\n")

    return {
        'creator': creator_address,
        'incoming_found': total_incoming,
        'outgoing_found': total_outgoing,
        'total_sol': total_sol
    }


if __name__ == "__main__":
    if not HELIUS_API_KEY:
        print("[ERROR] HELIUS_API_KEY not set in environment")
        sys.exit(1)

    if len(sys.argv) < 2:
        print("Usage: python3 funder_helius_extractor.py <creator_address>")
        sys.exit(1)

    creator = sys.argv[1]
    result = extract_for_creator(creator)
    print(result)
