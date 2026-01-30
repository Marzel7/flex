#!/usr/bin/env python3
"""
Batch Creator Funding Extractor

Periodically extracts creator funding from all creators in database.
Uses Helius Enhanced API with delays to avoid rate limiting.

This replaces real-time extraction with periodic batch processing:
- More reliable (avoids 429 rate limit errors)
- Slower but guaranteed to work
- Processes all creators over time
- Can be run as a background task or periodic job

Usage:
  python3 creator_funding_batch_processor.py [--batch-size 5] [--delay 3]

  --batch-size N: Process N creators per run (default 5)
  --delay S: Wait S seconds between creators (default 3)
"""

import argparse
import os
import sys
import time
import asyncio
import aiohttp
import sqlite3
from collections import defaultdict
from typing import Dict, List, Optional, Set
from datetime import datetime

DB_PATH = "pumpswap_tokens.db"
LAMPORTS_PER_SOL = 1_000_000_000
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY") or "84ec9a31-f8c2-4116-8e98-695a9377c5ed"
WSOL_MINT = "So11111111111111111111111111111111111111112"  # Wrapped SOL mint address

def lamports_to_sol(x: int) -> float:
    return x / LAMPORTS_PER_SOL

def is_token_account(address: str, tx: dict) -> bool:
    """Check if an address is a token account by examining account info in the transaction"""
    if not isinstance(tx.get("tokenTransfers"), list):
        return False

    for tt in tx.get("tokenTransfers", []):
        if tt.get("source") == address or tt.get("destination") == address:
            return True

    post_token_balances = tx.get("postTokenBalances", [])
    for ptb in post_token_balances:
        if ptb.get("owner") == address:
            return True

    return False

def collapse_wsol_transfers(tx: dict, watch_addr: str) -> List[dict]:
    """Collapse WSOL wrap/unwrap transfers into single wallet-to-wallet transfers"""
    native = tx.get("nativeTransfers", []) or []

    collapsed = []
    processed_indices = set()

    for idx, nt in enumerate(native):
        if idx in processed_indices:
            continue

        frm = nt.get("fromUserAccount")
        to = nt.get("toUserAccount")
        amt = nt.get("amount")

        if not isinstance(frm, str) or not isinstance(to, str) or not isinstance(amt, int):
            continue

        # Check if transfer to a token account
        if is_token_account(to, tx):
            # Look for return transfer from token account
            for other_idx, other_nt in enumerate(native):
                if other_idx <= idx:
                    continue

                other_from = other_nt.get("fromUserAccount")
                other_to = other_nt.get("toUserAccount")
                other_amt = other_nt.get("amount")

                # If same token account sends back SOL, collapse the transfers
                if other_from == to and isinstance(other_to, str) and isinstance(other_amt, int):
                    effective_amount = min(amt, other_amt)

                    collapsed.append({
                        "signature": tx.get("signature"),
                        "timestamp": tx.get("timestamp"),
                        "slot": tx.get("slot"),
                        "direction": "in" if other_to == watch_addr else "out",
                        "from": frm,
                        "to": other_to,
                        "counterparty": frm if other_to == watch_addr else other_to,
                        "amount_sol": lamports_to_sol(effective_amount),
                        "source_type": "original_sender",
                        "immediate_sender": None,
                        "is_immediate_sender_intermediary": False,
                        "is_wsol_wrap": True,
                    })

                    processed_indices.add(idx)
                    processed_indices.add(other_idx)
                    break

    return collapsed

def helius_base_url() -> str:
    return "https://api-mainnet.helius-rpc.com/v0"

async def fetch_page(
    session: aiohttp.ClientSession,
    address: str,
    api_key: str,
    before_sig: Optional[str],
    limit: int,
    timeout_s: int = 30,
    max_retries: int = 5,
) -> List[dict]:
    """Fetch one page of transactions from Helius with retry on 429"""
    url = f"{helius_base_url()}/addresses/{address}/transactions"
    params = {
        "api-key": api_key,
        "limit": str(limit),
        "sort-order": "desc",
        "commitment": "finalized",
    }
    if before_sig:
        params["before-signature"] = before_sig

    retry_delay = 2.0  # Start with 2 second delay

    for attempt in range(max_retries):
        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=timeout_s)) as resp:
                if resp.status == 429:
                    # Rate limited - retry with exponential backoff
                    if attempt < max_retries - 1:
                        print(f"    ⚠️  Rate limited (429), retrying in {retry_delay}s... (attempt {attempt+1}/{max_retries})")
                        await asyncio.sleep(retry_delay)
                        retry_delay *= 2  # Double the delay for next retry
                        continue
                    else:
                        raise RuntimeError(f"Helius HTTP 429: max usage reached (after {max_retries} retries)")

                if resp.status != 200:
                    txt = await resp.text()
                    raise RuntimeError(f"Helius HTTP {resp.status}: {txt[:200]}")

                data = await resp.json()
                if not isinstance(data, list):
                    raise RuntimeError(f"Unexpected response type: {type(data)}")
                return data

        except asyncio.TimeoutError:
            if attempt < max_retries - 1:
                print(f"    ⚠️  Timeout, retrying in {retry_delay}s... (attempt {attempt+1}/{max_retries})")
                await asyncio.sleep(retry_delay)
                retry_delay *= 2
                continue
            else:
                raise RuntimeError(f"Timeout after {max_retries} retries")

    raise RuntimeError("Max retries exceeded")

def extract_native_transfers(tx: dict, watch_addr: str) -> List[dict]:
    """Extract SOL transfers involving the watched address, tracing back through intermediaries
    
    This function implements recursive backtracking to find the true originator in multi-hop
    transfer chains. It traces backwards through the nativeTransfers array to identify the
    ultimate source of funds.
    
    Returns transfers with source_type classification:
    - "original_sender": Account that only sends (true originator)
    - "intermediary": Account that both receives and sends
    
    Algorithm:
    1. For each transfer to the creator, identify the immediate sender
    2. Check if that sender received SOL in the same transaction
    3. If yes, recursively trace back to find the source of that incoming transfer
    4. Continue until we find an account that only sends (doesn't receive)
    
    This handles complex relay chains like:
    OriginalSender → Consolidator → Relay1 → Relay2 → Creator
    """
    out = []
    sig = tx.get("signature")
    ts = tx.get("timestamp")
    slot = tx.get("slot")
    native = tx.get("nativeTransfers") or []

    if not native:
        return out

    # Build a map of all transfers in this transaction to trace chains
    transfers_to = {}
    transfers_from = {}
    
    for nt in native:
        frm = nt.get("fromUserAccount")
        to = nt.get("toUserAccount")
        amt = nt.get("amount")
        
        if isinstance(frm, str) and isinstance(to, str) and isinstance(amt, int):
            if to not in transfers_to:
                transfers_to[to] = []
            transfers_to[to].append({
                "from": frm,
                "to": to,
                "amount": amt,
            })
            
            if frm not in transfers_from:
                transfers_from[frm] = []
            transfers_from[frm].append({
                "from": frm,
                "to": to,
                "amount": amt,
            })
    
    def find_true_source(account: str, max_depth: int = 10) -> tuple:
        """Recursively trace back to find the true originator of funds

        Args:
            account: The account to trace back from
            max_depth: Maximum recursion depth to prevent infinite loops

        Returns:
            Tuple of (true_originating_account, source_type)
            source_type is "original_sender" if account only sends, "intermediary" if it receives too
        """
        if max_depth == 0:
            # Depth limit reached, return current account
            source_type = "intermediary" if account in transfers_to else "original_sender"
            return account, source_type

        # If this account received SOL in the transaction
        if account in transfers_to and len(transfers_to[account]) > 0:
            # Find the largest incoming transfer
            largest_incoming = max(transfers_to[account], key=lambda x: x["amount"])
            sender = largest_incoming["from"]

            # If the sender also appears to be just an intermediary (receives and sends),
            # trace back one more level
            if sender in transfers_to and len(transfers_to[sender]) > 0:
                # Recursively trace back
                return find_true_source(sender, max_depth - 1)
            else:
                # This sender is the true source (only sends, doesn't receive)
                return sender, "original_sender"
        else:
            # This account doesn't appear in any receiving transfers
            # It's either the true source or a final destination
            return account, "original_sender"

    # First, collapse WSOL wrap/unwrap patterns
    wsol_collapsed = collapse_wsol_transfers(tx, watch_addr)
    if wsol_collapsed:
        out.extend(wsol_collapsed)

    # Now process transfers involving watch_addr (excluding token accounts)
    for nt in native:
        frm = nt.get("fromUserAccount")
        to = nt.get("toUserAccount")
        amt = nt.get("amount")

        if not isinstance(frm, str) or not isinstance(to, str) or not isinstance(amt, int):
            continue

        # Skip if either party is a token account (already handled by WSOL collapse)
        if is_token_account(frm, tx) or is_token_account(to, tx):
            continue

        # Only care about transfers where watch_addr is either sender or receiver
        if watch_addr != frm and watch_addr != to:
            continue

        direction = "in" if watch_addr == to else "out"
        amount_sol = lamports_to_sol(amt)

        # Filter dust (< 0.001 SOL)
        if amount_sol < 0.001:
            continue

        # For inbound transfers, trace back to find the true originator
        if direction == "in":
            counterparty, source_type = find_true_source(frm)
            # If the immediate sender is not the true source, mark it as intermediary
            if counterparty != frm:
                # We traced back and found a different account
                immediate_sender_is_intermediary = True
            else:
                immediate_sender_is_intermediary = False
        else:
            counterparty = to
            source_type = "recipient"
            immediate_sender_is_intermediary = False

        out.append({
            "signature": sig,
            "timestamp": ts,
            "slot": slot,
            "direction": direction,
            "from": frm,
            "to": to,
            "counterparty": counterparty,
            "amount_sol": amount_sol,
            "source_type": source_type,
            "immediate_sender": frm if direction == "in" else None,
            "is_immediate_sender_intermediary": immediate_sender_is_intermediary,
        })

    return out

def save_transfers_to_db(creator: str, transfers: List[dict]) -> tuple:
    """Save transfers to database, return (funders_count, recipients_count)"""
    conn = sqlite3.connect(DB_PATH, timeout=60)
    cursor = conn.cursor()

    # Create tables if needed
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS creator_funders (
                creator_address TEXT NOT NULL,
                funder_address TEXT NOT NULL,
                amount_sol REAL,
                source_type TEXT,
                first_detected_at TEXT,
                is_cex INTEGER DEFAULT 0,
                cex_exchange TEXT,
                cex_type TEXT,
                PRIMARY KEY (creator_address, funder_address)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS creator_receivers (
                creator_address TEXT NOT NULL,
                receiver_address TEXT NOT NULL,
                amount_sol REAL,
                transaction_signature TEXT,
                timestamp INTEGER,
                first_detected_at TEXT,
                PRIMARY KEY (creator_address, receiver_address)
            )
        """)
    except sqlite3.OperationalError:
        pass  # Tables already exist

    # Build set of addresses that received SOL (intermediaries)
    receivers_in_tx = set()
    for t in transfers:
        if t["direction"] == "in":
            immediate = t.get("immediate_sender")
            if immediate and t.get("is_immediate_sender_intermediary"):
                receivers_in_tx.add(immediate)

    # Dust filtering - exclude spam/test transfers below threshold
    # and addresses known to be dust/spam or WSOL token accounts
    DUST_THRESHOLD = 0.0001  # 0.0001 SOL = 100,000 lamports
    DUST_ADDRESSES = {
        "3XxhMgcsvzCcDi6UKvWoSqUxt8JuGN5CR73tRkkDNDs5",  # Known spam dust account
        "3jYf1yHVQEkHNvacdz4wFRXcvFirF6nFjwLq9m8ML1ME",  # WSOL token account (wrap/unwrap plumbing)
        "GeuiPGMCpwDFQBCUqZ7h6NGyT6cpR5fULz9mnXeN3yRJ",  # Creator-specific WSOL ATA (zero balance change)
    }

    # Group inbound transfers by counterparty (funders)
    # Track both the true originator and any intermediaries
    funders = {}  # key: funder_address, value: {amount, source_type}
    inbound_txs = []  # Transaction-level data for tracing

    for t in transfers:
        if t["direction"] == "in":
            counterparty = t["counterparty"]
            amount = t["amount_sol"]
            source_type = t.get("source_type", "original_sender")

            # Skip dust transfers
            if amount < DUST_THRESHOLD or counterparty in DUST_ADDRESSES:
                continue

            # Record transaction-level data for tracing
            inbound_txs.append({
                "creator": creator,
                "funder": counterparty,
                "signature": t.get("signature"),
                "amount": amount,
                "timestamp": t.get("timestamp"),
                "slot": t.get("slot"),
                "source_type": source_type,
            })

            # Save the traced counterparty with its source_type
            if counterparty not in funders:
                funders[counterparty] = {
                    "amount": 0.0,
                    "source_type": source_type,
                }
            funders[counterparty]["amount"] += amount

            # Also save the immediate sender if it's different and it's an intermediary
            # BUT skip if it's a known plumbing account
            immediate = t.get("immediate_sender")
            if immediate and immediate != counterparty and t.get("is_immediate_sender_intermediary"):
                if immediate not in DUST_ADDRESSES:  # Skip plumbing accounts
                    if immediate not in funders:
                        funders[immediate] = {
                            "amount": 0.0,
                            "source_type": "intermediary",
                        }
                    funders[immediate]["amount"] += amount

    # Group outbound transfers by counterparty (receivers)
    receivers = defaultdict(lambda: {"amount": 0.0, "sig": None, "ts": None})
    for t in transfers:
        if t["direction"] == "out":
            receivers[t["counterparty"]]["amount"] += t["amount_sol"]
            if not receivers[t["counterparty"]]["sig"]:
                receivers[t["counterparty"]]["sig"] = t["signature"]
                receivers[t["counterparty"]]["ts"] = t["timestamp"]

    # Save funders with source_type
    saved_funders = 0
    for funder, data in funders.items():
        try:
            # Check if column exists (for backwards compatibility)
            try:
                cursor.execute("""
                    INSERT OR REPLACE INTO creator_funders
                    (creator_address, funder_address, amount_sol, source_type, first_detected_at)
                    VALUES (?, ?, ?, ?, datetime('now'))
                """, (creator, funder, data["amount"], data["source_type"]))
            except sqlite3.OperationalError:
                # source_type column doesn't exist yet, just save without it
                cursor.execute("""
                    INSERT OR REPLACE INTO creator_funders
                    (creator_address, funder_address, amount_sol, first_detected_at)
                    VALUES (?, ?, ?, datetime('now'))
                """, (creator, funder, data["amount"]))
            
            saved_funders += 1
        except Exception as e:
            print(f"⚠️  Error saving funder {funder}: {e}")

    # Save transaction-level inbound transfers
    saved_inbound_txs = 0
    for tx in inbound_txs:
        try:
            cursor.execute("""
                INSERT OR IGNORE INTO creator_inbound_transfers
                (creator_address, funder_address, transaction_signature, amount_sol, timestamp, slot, direction, source_type)
                VALUES (?, ?, ?, ?, ?, ?, 'in', ?)
            """, (tx["creator"], tx["funder"], tx["signature"], tx["amount"], tx["timestamp"], tx["slot"], tx["source_type"]))
            saved_inbound_txs += 1
        except Exception as e:
            print(f"⚠️  Error saving inbound transfer {tx['signature']}: {e}")

    # Save receivers
    saved_receivers = 0
    for receiver, data in receivers.items():
        try:
            cursor.execute("""
                INSERT OR REPLACE INTO creator_receivers
                (creator_address, receiver_address, amount_sol, transaction_signature, timestamp, first_detected_at)
                VALUES (?, ?, ?, ?, ?, datetime('now'))
            """, (creator, receiver, data["amount"], data["sig"], data["ts"]))
            saved_receivers += 1
        except Exception as e:
            print(f"⚠️  Error saving receiver {receiver}: {e}")

    conn.commit()
    conn.close()

    return saved_funders, saved_receivers

async def extract_for_creator(creator: str, api_key: str) -> Dict:
    """Extract funding for a single creator"""
    print(f"\n🔍 Extracting funding for {creator[:16]}...")

    transfers: List[dict] = []
    before = None
    fetched = 0
    pages = 0
    max_pages = 10  # ~1000 transactions

    try:
        async with aiohttp.ClientSession() as session:
            while fetched < 1000 and pages < max_pages:
                pages += 1
                limit = min(100, 1000 - fetched)

                page = await fetch_page(session, creator, api_key, before, limit)
                if not page:
                    break

                fetched += len(page)
                before = page[-1].get("signature")

                # Extract transfers
                for tx in page:
                    for tr in extract_native_transfers(tx, creator):
                        transfers.append(tr)

                print(f"  [PAGE {pages}] fetched={fetched} transfers_found={len(transfers)}", flush=True)

                if not before:
                    break

                # Small delay between pages to respect rate limits
                await asyncio.sleep(0.5)

        # Sort chronologically
        transfers.sort(key=lambda x: (x.get("timestamp") or 0, x.get("slot") or 0))

        # Save to database
        funders_saved, receivers_saved = save_transfers_to_db(creator, transfers)

        # Summary
        total_in = sum(t["amount_sol"] for t in transfers if t["direction"] == "in")
        total_out = sum(t["amount_sol"] for t in transfers if t["direction"] == "out")

        print(f"  ✅ Extracted: {len(transfers)} transfers, {funders_saved} funders ({total_in:.2f} SOL), {receivers_saved} receivers ({total_out:.2f} SOL)")

        return {
            "creator": creator,
            "transfers": len(transfers),
            "funders": funders_saved,
            "total_in": total_in,
            "receivers": receivers_saved,
            "total_out": total_out
        }

    except Exception as e:
        print(f"  ❌ Error: {e}")
        return {"creator": creator, "error": str(e)}

async def get_pending_creators(limit: int) -> Set[str]:
    """Get creators that don't have funding data yet"""
    conn = sqlite3.connect(DB_PATH, timeout=60)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT DISTINCT t.earliest_tx_creator
        FROM token_analysis t
        WHERE t.earliest_tx_creator IS NOT NULL
        AND NOT EXISTS (
            SELECT 1 FROM creator_funders cf
            WHERE cf.creator_address = t.earliest_tx_creator
        )
        LIMIT ?
    """, (limit,))

    creators = {row[0] for row in cursor.fetchall()}
    conn.close()
    return creators

async def run_batch(batch_size: int = 5, delay_between_creators: float = 3.0):
    """Run batch extraction for pending creators"""
    print(f"\n{'='*80}")
    print(f"CREATOR FUNDING BATCH PROCESSOR")
    print(f"{'='*80}")
    print(f"API Key: {HELIUS_API_KEY[:20]}...")
    print(f"Batch Size: {batch_size}")
    print(f"Delay Between Creators: {delay_between_creators}s\n")

    # Get pending creators
    pending = await get_pending_creators(batch_size)

    if not pending:
        print("✅ No pending creators - all have funding data!")
        return

    print(f"Processing {len(pending)} creators...\n")

    results = []
    for i, creator in enumerate(pending, 1):
        result = await extract_for_creator(creator, HELIUS_API_KEY)
        results.append(result)

        # Delay between creators to avoid rate limiting
        if i < len(pending):
            print(f"  ⏳ Waiting {delay_between_creators}s before next creator...")
            await asyncio.sleep(delay_between_creators)

    # Summary
    print(f"\n{'='*80}")
    print(f"BATCH SUMMARY ({len(results)} creators)")
    print(f"{'='*80}")

    total_transfers = 0
    total_funders = 0
    total_in = 0
    total_receivers = 0
    total_out = 0
    errors = 0

    for result in results:
        if "error" in result:
            print(f"❌ {result['creator'][:16]}... - Error: {result['error']}")
            errors += 1
        else:
            print(f"✅ {result['creator'][:16]}... - {result['transfers']} transfers, {result['funders']} funders ({result['total_in']:.2f} SOL)")
            total_transfers += result['transfers']
            total_funders += result['funders']
            total_in += result['total_in']
            total_receivers += result['receivers']
            total_out += result['total_out']

    print(f"\nTotal: {total_transfers} transfers, {total_funders} funders ({total_in:.2f} SOL), {total_receivers} receivers ({total_out:.2f} SOL)")
    if errors > 0:
        print(f"Errors: {errors}")

def main():
    ap = argparse.ArgumentParser(
        description="Batch extract creator funding from all pending creators"
    )
    ap.add_argument("--batch-size", type=int, default=5, help="Number of creators to process per run (default 5)")
    ap.add_argument("--delay", type=float, default=3.0, help="Seconds to wait between creators (default 3)")

    args = ap.parse_args()

    asyncio.run(run_batch(args.batch_size, args.delay))

if __name__ == "__main__":
    main()
