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
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

DB_PATH = "pumpswap_tokens.db"
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
            (creator_address,),
        )

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
    """Save a funder incoming transfer to database"""
    try:
        # Classify sender
        sender_type, exchange_name, exchange_type = classify_account(sender_address)
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
        print(f"[DB] ✅ Saved incoming: {sender_address[:16]}... → {funder_address[:16]}... | {amount_sol:.4f} SOL")
        return True

    except Exception as e:
        print(f"[DB] Error saving incoming transfer: {e}")
        return False


def save_funder_outgoing_transfer(funder_address: str, recipient_address: str, amount_sol: float,
                                  tx_signature: str, block_time: Optional[int] = None):
    """Save a funder outgoing transfer to database"""
    try:
        # Classify recipient
        recipient_type, exchange_name, exchange_type = classify_account(recipient_address)
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
        print(f"[DB] ✅ Saved outgoing: {funder_address[:16]}... → {recipient_address[:16]}... | {amount_sol:.4f} SOL")
        return True

    except Exception as e:
        print(f"[DB] Error saving outgoing transfer: {e}")
        return False


def get_transactions_helius(address: str, limit: int = 1000) -> List[Dict]:
    """Get transactions for an address via Helius API"""
    try:
        # Use correct Helius endpoint format
        url = f"https://api.helius.xyz/v0/addresses/{address}/transactions?api-key={HELIUS_API_KEY}"

        print(f"[HELIUS] Fetching transactions for {address[:16]}...")
        response = requests.get(url, timeout=15)
        data = response.json()

        if isinstance(data, list):
            print(f"[HELIUS] Retrieved {len(data)} transactions")
            return data
        else:
            print(f"[HELIUS] Error: {data}")
            return []

    except Exception as e:
        print(f"[HELIUS] Error: {e}")
        return []


def extract_transfers_for_funder(funder_address: str) -> Dict:
    """Extract incoming and outgoing SOL transfers for a funder address using Helius"""
    print(f"\n[EXTRACT] Analyzing funder: {funder_address}")

    incoming_transfers = []
    outgoing_transfers = []

    # Get recent transactions for this funder via Helius
    txs = get_transactions_helius(funder_address, limit=1000)

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
