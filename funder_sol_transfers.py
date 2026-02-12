#!/usr/bin/env python3
"""
Funder SOL IN/OUT tracking using complete RPC pagination.

For a creator's funders:
1. Get all signatures (paginated, not limited to recent)
2. For each signature, compute SOL delta
3. Show complete IN/OUT history
4. Save to database
"""

import time
import json
import math
import random
import sqlite3
import sys
from typing import Any, Dict, List, Optional, Tuple
import requests

sys.path.insert(0, '/Users/kevinkeaveney/Dev/claude/flex')
from infra_mapping import get_cex_info, get_account_info

RPC_URL = "https://api.mainnet-beta.solana.com"
LAMPORTS_PER_SOL = 1_000_000_000
DB_PATH = "pumpswap_tokens.db"


def rpc_call(method: str, params: list, session: requests.Session, timeout: int = 30, max_retries: int = 8):
    """JSON-RPC client with exponential backoff for rate limiting"""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params,
    }

    backoff = 0.5
    for attempt in range(max_retries):
        try:
            r = session.post(RPC_URL, json=payload, timeout=timeout)
            if r.status_code == 429:
                sleep_s = backoff * (2 ** attempt) + random.uniform(0, 0.25)
                time.sleep(min(sleep_s, 20))
                continue

            r.raise_for_status()
            data = r.json()

            if "error" in data and data["error"] is not None:
                return None, data["error"]

            return data.get("result"), None

        except (requests.RequestException, json.JSONDecodeError) as e:
            sleep_s = backoff * (2 ** attempt) + random.uniform(0, 0.25)
            time.sleep(min(sleep_s, 20))

    return None, {"message": "max_retries_exceeded"}


def get_all_signatures(address: str, session: requests.Session, limit: int = 100, max_pages: Optional[int] = None):
    """Paginate getSignaturesForAddress"""
    before = None
    page = 0

    while True:
        cfg = {"limit": limit}
        if before:
            cfg["before"] = before

        result, err = rpc_call("getSignaturesForAddress", [address, cfg], session)
        if err:
            print(f"[RPC] Error: {err}")
            break

        if not result:
            break

        yield result

        before = result[-1]["signature"]
        page += 1
        if max_pages is not None and page >= max_pages:
            break


def get_tx(signature: str, session: requests.Session):
    """Fetch transaction details"""
    result, err = rpc_call(
        "getTransaction",
        [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
        session,
    )
    return result, err


def compute_sol_delta_for_address(tx: Dict[str, Any], address: str) -> Optional[Tuple[float, float, Optional[str]]]:
    """Compute (delta_sol, fee_sol, counterparty) for address in transaction"""
    meta = tx.get("meta")
    trx = tx.get("transaction")
    if not meta or not trx:
        return None

    msg = trx.get("message", {})
    account_keys = msg.get("accountKeys", [])

    keys = []
    for k in account_keys:
        if isinstance(k, str):
            keys.append(k)
        elif isinstance(k, dict) and "pubkey" in k:
            keys.append(k["pubkey"])
        else:
            keys.append(str(k))

    try:
        i = keys.index(address)
    except ValueError:
        return None

    pre = meta.get("preBalances", [])
    post = meta.get("postBalances", [])
    if i >= len(pre) or i >= len(post):
        return None

    delta_lamports = post[i] - pre[i]
    delta_sol = delta_lamports / LAMPORTS_PER_SOL

    fee_lamports = meta.get("fee", 0)
    fee_sol = fee_lamports / LAMPORTS_PER_SOL

    # Find counterparty (other account with balance change)
    counterparty = None
    max_other_delta = 0
    for idx, key in enumerate(keys):
        if idx != i and idx < len(pre) and idx < len(post):
            other_delta = abs(post[idx] - pre[idx])
            if other_delta > max_other_delta and other_delta > 0:
                max_other_delta = other_delta
                counterparty = key

    return delta_sol, fee_sol, counterparty


def fetch_all_sol_in_out(address: str, rps_delay: float = 0.15, max_txs: Optional[int] = None):
    """Fetch complete SOL IN/OUT history with pagination"""
    out = []
    seen = 0
    page = 0

    with requests.Session() as session:
        for sig_page in get_all_signatures(address, session):
            page += 1
            print(f"[RPC] Page {page}: Processing {len(sig_page)} signatures...", flush=True)

            for i, s in enumerate(sig_page, 1):
                sig = s["signature"]
                block_time = s.get("blockTime")
                errflag = s.get("err")

                tx, txerr = get_tx(sig, session)
                time.sleep(rps_delay)

                if tx is None:
                    continue

                delta = compute_sol_delta_for_address(tx, address)
                if delta is None:
                    continue

                delta_sol, fee_sol, counterparty = delta
                if math.isclose(delta_sol, 0.0, abs_tol=1e-12):
                    continue

                direction = "IN" if delta_sol > 0 else "OUT"

                out.append({
                    "signature": sig,
                    "blockTime": block_time,
                    "err": errflag,
                    "deltaSOL": delta_sol,
                    "feeSOL": fee_sol,
                    "direction": direction,
                    "counterparty": counterparty,
                })

                seen += 1

                # Show progress every 10 transactions
                if seen % 10 == 0:
                    print(f"      [{seen}] Found {len([t for t in out if t['direction'] == 'IN'])} IN, {len([t for t in out if t['direction'] == 'OUT'])} OUT", flush=True)

                if max_txs is not None and seen >= max_txs:
                    print(f"[RPC] ✅ Reached max_txs limit ({max_txs})", flush=True)
                    return out

    print(f"[RPC] ✅ Complete! Fetched {len(out)} total transactions across {page} pages", flush=True)
    return out


def get_creator_funders(creator_address: str) -> list:
    """Get all funders for a creator"""
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
        print(f"[DB] Error: {e}")
        return []


def classify_address(address: str) -> str:
    """Classify address type"""
    cex_info = get_cex_info(address)
    if cex_info:
        return f"✅ CEX: {cex_info.get('name')}"

    infra_info = get_account_info(address)
    if infra_info:
        return f"✅ INFRA: {infra_info.get('name')}"

    return "❓ UNKNOWN"


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Get complete SOL IN/OUT history for a funder using full RPC pagination"
    )
    parser.add_argument("funder", type=str, help="Funder address to analyze")
    parser.add_argument("--delay", type=float, default=0.15, help="RPC delay between requests (default 0.15s)")
    parser.add_argument("--max-txs", type=int, default=None, help="Max transactions to fetch (default: all)")
    args = parser.parse_args()

    funder = args.funder

    print(f"[ANALYSIS] Funder SOL IN/OUT History")
    print(f"[FUNDER] {funder}\n")

    # Classify funder
    funder_type = classify_address(funder)
    print(f"Type: {funder_type}\n")

    # Get all SOL movements
    print(f"[RPC] Fetching complete transaction history (paginated)...")
    rows = fetch_all_sol_in_out(funder, rps_delay=args.delay, max_txs=args.max_txs)

    if not rows:
        print(f"[RPC] ℹ️  No SOL movements detected for this address")
        return

    # Separate IN and OUT
    inflows = [r for r in rows if r["direction"] == "IN"]
    outflows = [r for r in rows if r["direction"] == "OUT"]

    total_in = sum(r["deltaSOL"] for r in inflows)
    total_out = sum(-r["deltaSOL"] for r in outflows)
    total_fees = sum(r["feeSOL"] for r in rows)

    print(f"\n{'='*100}")
    print(f"SUMMARY")
    print(f"{'='*100}")
    print(f"Total transactions: {len(rows)}")
    print(f"Total IN:  {total_in:>12.4f} SOL ({len(inflows)} txs)")
    print(f"Total OUT: {total_out:>12.4f} SOL ({len(outflows)} txs)")
    print(f"Total FEES: {total_fees:>11.4f} SOL")
    print(f"Net: {total_in - total_out:>17.4f} SOL")

    # Show inflows
    if inflows:
        print(f"\n📥 TOP INFLOWS (From):\n")
        sorted_in = sorted(inflows, key=lambda r: r["deltaSOL"], reverse=True)
        for i, r in enumerate(sorted_in[:10], 1):
            counterparty = r.get("counterparty", "Unknown")
            time_str = f"  | {time.strftime('%Y-%m-%d', time.localtime(r['blockTime']))}" if r["blockTime"] else ""
            print(f"[{i:2}] {r['deltaSOL']:>8.4f} SOL ← {counterparty}{time_str}")

        if len(sorted_in) > 10:
            remaining = sum(r["deltaSOL"] for r in sorted_in[10:])
            print(f"     ... and {len(sorted_in) - 10} more ({remaining:.4f} SOL)")

    # Show outflows
    if outflows:
        print(f"\n📤 TOP OUTFLOWS (To):\n")
        sorted_out = sorted(outflows, key=lambda r: r["deltaSOL"], reverse=True)
        for i, r in enumerate(sorted_out[:10], 1):
            counterparty = r.get("counterparty", "Unknown")
            time_str = f"  | {time.strftime('%Y-%m-%d', time.localtime(r['blockTime']))}" if r["blockTime"] else ""
            print(f"[{i:2}] {-r['deltaSOL']:>8.4f} SOL → {counterparty}{time_str}")

        if len(sorted_out) > 10:
            remaining = sum(-r["deltaSOL"] for r in sorted_out[10:])
            print(f"     ... and {len(sorted_out) - 10} more ({remaining:.4f} SOL)")


if __name__ == "__main__":
    main()
