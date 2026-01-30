#!/usr/bin/env python3
"""
Creator Funding History via Helius Enhanced API

Fetches all SOL transfers for a creator address, excluding noise like:
- Token mints (the tokens they launched)
- Bonding curves
- System programs
- Specified exclusion addresses

Saves all funders and receivers to database.

Usage:
  python3 creator_funding_helius.py <CREATOR_ADDRESS> [--max-txs 5000] [--exclude ADDRESS1 ADDRESS2 ...]
"""

import argparse
import os
import sys
import time
import asyncio
import aiohttp
import sqlite3
from collections import defaultdict
from typing import Dict, List, Optional
from datetime import datetime

DB_PATH = "pumpswap_tokens.db"
LAMPORTS_PER_SOL = 1_000_000_000
WSOL_MINT = "So11111111111111111111111111111111111111112"  # Wrapped SOL mint address

def lamports_to_sol(x: int) -> float:
    return x / LAMPORTS_PER_SOL

def is_token_account(address: str, tx: dict) -> bool:
    """Check if an address is a token account by examining account info in the transaction"""
    # Token accounts have specific signatures in their owners/type field
    # For now, use heuristic: if it appears in postTokenBalances, it's a token account
    if not isinstance(tx.get("tokenTransfers"), list):
        return False

    for tt in tx.get("tokenTransfers", []):
        if tt.get("source") == address or tt.get("destination") == address:
            return True

    # Also check postTokenBalances
    post_token_balances = tx.get("postTokenBalances", [])
    for ptb in post_token_balances:
        if ptb.get("owner") == address:
            return True

    return False

def collapse_wsol_transfers(tx: dict, watch_addr: str) -> List[dict]:
    """
    Collapse WSOL wrap/unwrap transfers into single wallet-to-wallet transfers.

    Pattern:
      wallet A sends X SOL to WSOL token account
      WSOL token account closes, returns SOL to wallet B

    Should be logged as: wallet A -> wallet B (1 transfer, not 2)
    """
    native = tx.get("nativeTransfers", []) or []
    token_transfers = tx.get("tokenTransfers", []) or []

    collapsed = []
    processed_indices = set()

    # Find WSOL token transfers in this transaction
    wsol_transfers = [tt for tt in token_transfers if tt.get("mint") == WSOL_MINT]

    # For each WSOL transfer, try to find corresponding native transfers
    for idx, nt in enumerate(native):
        if idx in processed_indices:
            continue

        frm = nt.get("fromUserAccount")
        to = nt.get("toUserAccount")
        amt = nt.get("amount")

        if not isinstance(frm, str) or not isinstance(to, str) or not isinstance(amt, int):
            continue

        # Check if this is a transfer to a token account that later closes
        if is_token_account(to, tx):
            # Look for a closeAccount instruction or a return transfer from this token account
            for other_idx, other_nt in enumerate(native):
                if other_idx <= idx:  # Only look forward in sequence
                    continue

                other_from = other_nt.get("fromUserAccount")
                other_to = other_nt.get("toUserAccount")
                other_amt = other_nt.get("amount")

                # If same token account sends back SOL, collapse the transfers
                if other_from == to and isinstance(other_to, str) and isinstance(other_amt, int):
                    # Use minimum amount (SOL sent in - fees)
                    effective_amount = min(amt, other_amt)

                    collapsed.append({
                        "signature": tx.get("signature"),
                        "timestamp": tx.get("timestamp"),
                        "slot": tx.get("slot"),
                        "direction": "in" if other_to == watch_addr else "out",
                        "from": frm,
                        "to": other_to,
                        "counterparty": frm if other_to == watch_addr else other_to,
                        "lamports": effective_amount,
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
) -> List[dict]:
    """Fetch one page of transactions from Helius"""
    url = f"{helius_base_url()}/addresses/{address}/transactions"
    params = {
        "api-key": api_key,
        "limit": str(limit),
        "sort-order": "desc",
        "commitment": "finalized",
    }
    if before_sig:
        params["before-signature"] = before_sig

    async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=timeout_s)) as resp:
        if resp.status != 200:
            txt = await resp.text()
            raise RuntimeError(f"Helius HTTP {resp.status}: {txt[:200]}")
        data = await resp.json()
        if not isinstance(data, list):
            raise RuntimeError(f"Unexpected response type: {type(data)}")
        return data

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
            "lamports": amt,
            "source_type": source_type,
            "immediate_sender": frm if direction == "in" else None,
            "is_immediate_sender_intermediary": immediate_sender_is_intermediary,
        })

    return out

def save_transfers_to_db(creator: str, transfers: List[dict]) -> None:
    """Save transfers to database"""
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
            amount = lamports_to_sol(t["lamports"])
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
            receivers[t["counterparty"]]["amount"] += lamports_to_sol(t["lamports"])
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

    print(f"✅ Saved {saved_funders} funders, {saved_inbound_txs} inbound txs, and {saved_receivers} receivers to database")

async def run(
    address: str,
    max_txs: int,
    page_size: int,
    exclude_counterparties: List[str],
    api_key: str,
) -> None:
    """Fetch funding history and save to database"""

    if not api_key:
        print("❌ Missing Helius API key", file=sys.stderr)
        sys.exit(1)

    t0 = time.time()
    transfers: List[dict] = []

    totals_in = defaultdict(int)
    totals_out = defaultdict(int)
    total_in = 0
    total_out = 0

    exclude_set = set(exclude_counterparties)

    before = None
    fetched = 0
    pages = 0

    print(f"🔍 Fetching funding history for {address}")
    print(f"   Excluding: {len(exclude_counterparties)} addresses")
    print()

    async with aiohttp.ClientSession() as session:
        while fetched < max_txs:
            pages += 1
            limit = min(page_size, max_txs - fetched)

            try:
                page = await fetch_page(session, address, api_key, before, limit)
            except Exception as e:
                print(f"❌ Error fetching page {pages}: {e}")
                break

            if not page:
                break

            fetched += len(page)

            # next pagination cursor
            before = page[-1].get("signature")

            # Extract transfers
            page_xfers = 0
            for tx in page:
                for tr in extract_native_transfers(tx, address):
                    # Skip excluded counterparties
                    if tr["counterparty"] in exclude_set:
                        continue

                    transfers.append(tr)
                    page_xfers += 1

                    if tr["direction"] == "in":
                        total_in += tr["lamports"]
                        totals_in[tr["counterparty"]] += tr["lamports"]
                    else:
                        total_out += tr["lamports"]
                        totals_out[tr["counterparty"]] += tr["lamports"]

            print(f"[PAGE {pages:2d}] txs={len(page):3d} fetched={fetched:4d} transfers_found={page_xfers:3d} (total={len(transfers):4d})", flush=True)

            if not before:
                break

    # Sort chronologically
    transfers.sort(key=lambda x: (x.get("timestamp") or 0, x.get("slot") or 0))

    def top_n(d: Dict[str, int], n: int = 20):
        return sorted(d.items(), key=lambda kv: kv[1], reverse=True)[:n]

    # Print summary
    print("\n" + "=" * 80)
    print("FUNDING SUMMARY (Helius nativeTransfers)")
    print("=" * 80)
    print(f"Address:    {address}")
    print(f"Txs scanned: {fetched}")
    print(f"Transfers:  {len(transfers)}")
    print(f"Total IN:   {lamports_to_sol(total_in):.6f} SOL")
    print(f"Total OUT:  {lamports_to_sol(total_out):.6f} SOL")
    print(f"Net:        {lamports_to_sol(total_in - total_out):.6f} SOL")

    print("\n📥 Top inbound (funders):")
    for cp, lam in top_n(totals_in, 20):
        sol = lamports_to_sol(lam)
        print(f"  {sol:>10.6f} SOL  {cp}")

    print("\n📤 Top outbound (receivers):")
    for cp, lam in top_n(totals_out, 20):
        sol = lamports_to_sol(lam)
        print(f"  {sol:>10.6f} SOL  {cp}")

    print(f"\n⏱️  Completed in {time.time() - t0:.1f}s")

    # Save to database
    print()
    save_transfers_to_db(address, transfers)

def main():
    ap = argparse.ArgumentParser(
        description="Fetch creator funding history from Helius and save to database"
    )
    ap.add_argument("address", help="Creator address to analyze")
    ap.add_argument("--max-txs", type=int, default=5000, help="Max transactions to scan")
    ap.add_argument("--page-size", type=int, default=100, help="Helius page size")
    ap.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Exclude counterparty (repeatable)",
    )
    ap.add_argument("--api-key", help="Helius API key (or set HELIUS_API_KEY env var)")

    args = ap.parse_args()

    api_key = args.api_key or os.getenv("HELIUS_API_KEY") or "84ec9a31-f8c2-4116-8e98-695a9377c5ed"

    asyncio.run(run(args.address, args.max_txs, args.page_size, args.exclude, api_key))

if __name__ == "__main__":
    main()
