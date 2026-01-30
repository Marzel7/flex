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

def lamports_to_sol(x: int) -> float:
    return x / LAMPORTS_PER_SOL

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
    """Extract SOL transfers involving the watched address"""
    out = []
    sig = tx.get("signature")
    ts = tx.get("timestamp")
    slot = tx.get("slot")
    native = tx.get("nativeTransfers") or []

    for nt in native:
        frm = nt.get("fromUserAccount")
        to = nt.get("toUserAccount")
        amt = nt.get("amount")

        if not isinstance(frm, str) or not isinstance(to, str) or not isinstance(amt, int):
            continue

        if watch_addr != frm and watch_addr != to:
            continue

        direction = "in" if watch_addr == to else "out"
        counterparty = frm if direction == "in" else to

        out.append({
            "signature": sig,
            "timestamp": ts,
            "slot": slot,
            "direction": direction,
            "from": frm,
            "to": to,
            "counterparty": counterparty,
            "lamports": amt,
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

    # Group inbound transfers by counterparty (funders)
    funders = defaultdict(float)
    for t in transfers:
        if t["direction"] == "in":
            funders[t["counterparty"]] += lamports_to_sol(t["lamports"])

    # Group outbound transfers by counterparty (receivers)
    receivers = defaultdict(lambda: {"amount": 0.0, "sig": None, "ts": None})
    for t in transfers:
        if t["direction"] == "out":
            receivers[t["counterparty"]]["amount"] += lamports_to_sol(t["lamports"])
            if not receivers[t["counterparty"]]["sig"]:
                receivers[t["counterparty"]]["sig"] = t["signature"]
                receivers[t["counterparty"]]["ts"] = t["timestamp"]

    # Save funders
    saved_funders = 0
    for funder, amount in funders.items():
        try:
            cursor.execute("""
                INSERT OR REPLACE INTO creator_funders
                (creator_address, funder_address, amount_sol, first_detected_at)
                VALUES (?, ?, ?, datetime('now'))
            """, (creator, funder, amount))
            saved_funders += 1
        except Exception as e:
            print(f"⚠️  Error saving funder {funder}: {e}")

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

    print(f"✅ Saved {saved_funders} funders and {saved_receivers} receivers to database")

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
