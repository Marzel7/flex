#!/usr/bin/env python3
"""
Extract and save funder incoming transfers to database.

For each funder for a creator:
1. Get recent transaction signatures via Solana RPC
2. Parse SOL transfers (IN flows) where funder is recipient
3. Identify sender addresses
4. Classify senders (CEX, INFRA, unknown)
5. Save to funder_incoming_transfers table
"""

import sqlite3
import asyncio
import aiohttp
from typing import Dict, List, Tuple, Optional
import sys
sys.path.insert(0, '/Users/kevinkeaveney/Dev/claude/flex')

from infra_mapping import get_account_info, get_cex_info
import requests

DB_PATH = "pumpswap_tokens.db"
SOLANA_RPC = "https://api.mainnet-beta.solana.com"


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


def classify_sender(sender_address: str) -> Tuple[str, Optional[str], Optional[str]]:
    """Classify a sender address and return (sender_type, exchange_name, exchange_type)"""

    # Check CEX first
    cex_info = get_cex_info(sender_address)
    if cex_info:
        return ("cex", cex_info.get('name'), cex_info.get('cex_type'))

    # Check infrastructure
    infra_info = get_account_info(sender_address)
    if infra_info:
        return ("infra", infra_info.get('name'), None)

    return ("unknown", None, None)


def save_funder_incoming_transfer(sender_address: str, funder_address: str, amount_sol: float,
                                  tx_signature: str, block_time: Optional[int] = None):
    """Save a funder incoming transfer to database"""
    try:
        # Classify sender
        sender_type, exchange_name, exchange_type = classify_sender(sender_address)

        # Check if sender is CEX
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
        print(f"[DB] Error saving transfer: {e}")
        return False


def get_transactions_for_address(address: str, limit: int = 100) -> List[Dict]:
    """Get recent transactions for an address via RPC"""
    try:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [address, {"limit": limit}]
        }

        response = requests.post(SOLANA_RPC, json=payload, timeout=10)
        data = response.json()

        if 'error' in data:
            print(f"[RPC] Error: {data['error']}")
            return []

        if 'result' not in data or not data['result']:
            return []

        return data['result']

    except Exception as e:
        print(f"[RPC] Error getting signatures: {e}")
        return []


def parse_transaction(tx_sig: str) -> Optional[Dict]:
    """Parse a transaction to find SOL transfers"""
    try:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTransaction",
            "params": [tx_sig, {"encoding": "json", "maxSupportedTransactionVersion": 0}]
        }

        response = requests.post(SOLANA_RPC, json=payload, timeout=10)
        data = response.json()

        if 'error' in data or 'result' not in data or data['result'] is None:
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
        print(f"[RPC] Error parsing transaction {tx_sig[:16]}...: {e}")
        return None


def extract_transfers_for_funder(funder_address: str) -> Dict:
    """Extract incoming SOL transfers to a funder address"""
    print(f"\n[EXTRACT] Analyzing funder: {funder_address}")

    transfers = []

    # Get recent transactions for this funder (increased limit)
    sigs = get_transactions_for_address(funder_address, limit=1000)
    print(f"[RPC] Found {len(sigs)} transactions for funder")

    if not sigs:
        return {'count': 0, 'total_sol': 0}

    # Parse each transaction
    for i, sig_info in enumerate(sigs):
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

        # Check if funder's balance increased (incoming transfer)
        pre_balance = pre_balances[funder_idx]
        post_balance = post_balances[funder_idx]

        if post_balance > pre_balance:
            # Funder received SOL
            amount_lamports = post_balance - pre_balance
            amount_sol = amount_lamports / 1e9

            # Only save transfers > 0.001 SOL (filter dust)
            if amount_sol <= 0.001:
                continue

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
                transfers.append({
                    'sender': sender,
                    'funder': funder_address,
                    'amount_sol': amount_sol,
                    'tx_sig': sig,
                    'block_time': block_time
                })
                print(f"[TRANSFER] {sender[:16]}... → {funder_address[:16]}... | {amount_sol:.4f} SOL")

        if (i + 1) % 100 == 0:
            print(f"[PROGRESS] Processed {i + 1}/{len(sigs)} transactions")

    # Save all transfers to database
    saved_count = 0
    for transfer in transfers:
        if save_funder_incoming_transfer(
            transfer['sender'],
            transfer['funder'],
            transfer['amount_sol'],
            transfer['tx_sig'],
            transfer['block_time']
        ):
            saved_count += 1

    total_sol = sum(t['amount_sol'] for t in transfers)

    print(f"[SUMMARY] Funder {funder_address[:16]}...: {saved_count} transfers saved, {total_sol:.4f} SOL total")

    return {
        'count': saved_count,
        'total_sol': total_sol,
        'funder': funder_address
    }


def extract_for_creator(creator_address: str) -> Dict:
    """Extract incoming transfers for all funders of a creator"""
    print(f"\n{'='*80}")
    print(f"[START] Extracting funder incoming transfers for creator: {creator_address}")
    print(f"{'='*80}")

    # Get all funders for this creator
    funders = get_creator_funders(creator_address)
    print(f"[DB] Found {len(funders)} funder(s) for this creator")

    if not funders:
        print("[RESULT] No funders found for creator")
        return {'error': 'no_funders'}

    # Extract for each funder
    total_transferred = 0
    total_saved = 0

    for funder_addr, funder_amount in funders:
        result = extract_transfers_for_funder(funder_addr)
        total_transferred += result['total_sol']
        total_saved += result['count']

    print(f"\n{'='*80}")
    print(f"[COMPLETE] Extraction complete for {creator_address}")
    print(f"  Total senders found: {total_saved}")
    print(f"  Total SOL traced: {total_transferred:.4f}")
    print(f"{'='*80}\n")

    return {
        'creator': creator_address,
        'senders_found': total_saved,
        'total_sol': total_transferred
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 funder_incoming_extractor.py <creator_address>")
        sys.exit(1)

    creator = sys.argv[1]
    result = extract_for_creator(creator)
    print(result)
