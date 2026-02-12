#!/usr/bin/env python3
"""
Funder SOL IN/OUT tracking using complete RPC pagination (LAST 30 DAYS) + spam filtering.

For a creator's funders:
1. Get signatures (paginated) but STOP once older than cutoff
2. For each signature, compute SOL delta (and delta excluding fee when fee payer)
3. Filter spam:
   - skip failed txs
   - skip fee-only outs
   - skip dust (< MIN_ABS_SOL) unless it’s an explicit system transfer involving the address
4. Show IN/OUT summary + top flows
"""

import time
import json
import math
import random
import sqlite3
import sys
from typing import Any, Dict, List, Optional, Tuple, Union
import requests

sys.path.insert(0, "/Users/kevinkeaveney/Dev/claude/flex")
from infra_mapping import (
    get_cex_info,
    get_account_info,
    get_pumpfun_creator_info,
    get_suspicious_wallet_info,
)

RPC_URL = "https://mainnet.helius-rpc.com/?api-key=3b2917b8-9bed-4e2e-8c05-a74adbc34bb8"
LAMPORTS_PER_SOL = 1_000_000_000
DB_PATH = "pumpswap_tokens.db"

# Defaults for speed + spam filtering
DEFAULT_SINCE_DAYS = 30
DEFAULT_SIG_LIMIT = 100  # RPC-friendly batch size (1000 causes rate limit issues)
DEFAULT_MIN_ABS_SOL = 0.001  # dust threshold (tune: 0.001–0.01)
DEFAULT_INCLUDE_PROGRAM_SOL = True  # keep non-system-program SOL movements if above dust


def rpc_call(
    method: str,
    params: list,
    session: requests.Session,
    timeout: int = 30,
    max_retries: int = 8,
):
    """JSON-RPC client with exponential backoff for rate limiting."""
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}

    backoff = 0.5
    for attempt in range(max_retries):
        try:
            r = session.post(RPC_URL, json=payload, timeout=timeout)

            if r.status_code == 429:
                sleep_s = backoff * (2**attempt) + random.uniform(0, 0.25)
                time.sleep(min(sleep_s, 20))
                continue

            r.raise_for_status()
            data = r.json()

            if data.get("error") is not None:
                return None, data["error"]

            return data.get("result"), None

        except (requests.RequestException, json.JSONDecodeError):
            sleep_s = backoff * (2**attempt) + random.uniform(0, 0.25)
            time.sleep(min(sleep_s, 20))

    return None, {"message": "max_retries_exceeded"}


def get_all_signatures(
    address: str,
    session: requests.Session,
    limit: int = DEFAULT_SIG_LIMIT,
    max_pages: Optional[int] = None,
):
    """Paginate getSignaturesForAddress (newest -> oldest)."""
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
    """Fetch transaction details."""
    result, err = rpc_call(
        "getTransaction",
        [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
        session,
    )
    return result, err


def _extract_account_keys(message: Dict[str, Any]) -> List[str]:
    account_keys = message.get("accountKeys", [])
    keys: List[str] = []
    for k in account_keys:
        if isinstance(k, str):
            keys.append(k)
        elif isinstance(k, dict) and "pubkey" in k:
            keys.append(k["pubkey"])
        else:
            keys.append(str(k))
    return keys


def _has_system_transfer_for_address(message: Dict[str, Any], address: str) -> bool:
    """True if there's an explicit System Program transfer involving the address."""
    try:
        for ins in message.get("instructions", []) or []:
            if not isinstance(ins, dict):
                continue
            if ins.get("program") != "system":
                continue
            parsed = ins.get("parsed")
            if not isinstance(parsed, dict):
                continue
            if parsed.get("type") != "transfer":
                continue
            info = parsed.get("info")
            if not isinstance(info, dict):
                continue
            if info.get("source") == address or info.get("destination") == address:
                return True
    except Exception:
        pass
    return False


def compute_sol_delta_for_address(
    tx: Dict[str, Any], address: str
) -> Optional[Tuple[float, float, float, Optional[str], bool, bool]]:
    """
    Returns:
      (delta_sol_incl_fee, fee_sol, delta_sol_excl_fee, counterparty, is_fee_payer, has_system_transfer)
    Notes:
      - delta_sol_incl_fee is net balance change (includes fee, rent effects)
      - delta_sol_excl_fee only adjusts for fee if address is fee payer
    """
    meta = tx.get("meta")
    trx = tx.get("transaction")
    if not meta or not trx:
        return None

    msg = trx.get("message", {}) or {}
    keys = _extract_account_keys(msg)

    try:
        i = keys.index(address)
    except ValueError:
        return None

    pre = meta.get("preBalances", []) or []
    post = meta.get("postBalances", []) or []
    if i >= len(pre) or i >= len(post):
        return None

    delta_lamports = post[i] - pre[i]
    delta_sol = delta_lamports / LAMPORTS_PER_SOL

    fee_lamports = meta.get("fee", 0) or 0
    fee_sol = fee_lamports / LAMPORTS_PER_SOL

    # Fee payer is typically first account key in the message
    is_fee_payer = (len(keys) > 0 and keys[0] == address)

    # Exclude fee only when this address is fee payer
    delta_excl_fee_lamports = delta_lamports + fee_lamports if is_fee_payer else delta_lamports
    delta_excl_fee_sol = delta_excl_fee_lamports / LAMPORTS_PER_SOL

    # Counterparty heuristic: largest other absolute balance delta
    counterparty = None
    max_other_delta = 0
    for idx, key in enumerate(keys):
        if idx == i:
            continue
        if idx < len(pre) and idx < len(post):
            other_delta = abs(post[idx] - pre[idx])
            if other_delta > max_other_delta and other_delta > 0:
                max_other_delta = other_delta
                counterparty = key

    has_system_transfer = _has_system_transfer_for_address(msg, address)

    return delta_sol, fee_sol, delta_excl_fee_sol, counterparty, is_fee_payer, has_system_transfer


def fetch_all_sol_in_out(
    address: str,
    rps_delay: float = 0.15,
    max_txs: Optional[int] = None,
    since_days: int = DEFAULT_SINCE_DAYS,
    min_abs_sol: float = DEFAULT_MIN_ABS_SOL,
    include_program_sol: bool = DEFAULT_INCLUDE_PROGRAM_SOL,
    sig_limit: int = DEFAULT_SIG_LIMIT,
):
    """
    Fetch SOL IN/OUT history:
      - only last `since_days`
      - skip failed signatures
      - spam filters:
          * fee-only outs (only fee loss)
          * dust moves (abs(delta_excl_fee) < min_abs_sol), unless system transfer
          * optionally keep program-induced SOL moves if above dust
    """
    out: List[Dict[str, Any]] = []
    seen = 0
    page = 0

    cutoff_ts = int(time.time()) - since_days * 86400
    print(
        f"[RPC] ⏱️  Limiting scan to last {since_days} days "
        f"(since {time.strftime('%Y-%m-%d', time.localtime(cutoff_ts))})"
    )
    print(f"[RPC] 🧹 Spam filters: min_abs_sol={min_abs_sol}, include_program_sol={include_program_sol}")
    print(f"[RPC] 📄 Signature page size: {sig_limit}")

    with requests.Session() as session:
        time.sleep(0.5)  # Initial delay before first RPC call
        for sig_page in get_all_signatures(address, session, limit=sig_limit):
            page += 1
            print(f"[RPC] Page {page}: Processing {len(sig_page)} signatures...", flush=True)
            time.sleep(0.2)  # Delay between pages

            # If oldest in the page is older than cutoff, we can stop after processing newer items
            oldest_bt = sig_page[-1].get("blockTime")
            stop_after_page = bool(oldest_bt and oldest_bt < cutoff_ts)

            for s in sig_page:
                # Skip failed signatures (saves getTransaction calls)
                if s.get("err") is not None:
                    continue

                sig = s["signature"]
                block_time = s.get("blockTime")

                # Stop scanning older txs (newest -> oldest)
                if block_time and block_time < cutoff_ts:
                    break

                tx, _txerr = get_tx(sig, session)
                time.sleep(rps_delay)

                if tx is None:
                    continue

                delta = compute_sol_delta_for_address(tx, address)
                if delta is None:
                    continue

                delta_sol, fee_sol, delta_excl_fee_sol, counterparty, is_fee_payer, has_system_transfer = delta

                # Ignore true zero net delta
                if math.isclose(delta_sol, 0.0, abs_tol=1e-12):
                    continue

                # 1) Fee-only OUT spam: net negative but excluding fee it’s basically nothing
                #    Example: you paid fee, no meaningful SOL moved.
                if is_fee_payer and delta_sol < 0 and abs(delta_excl_fee_sol) < min_abs_sol:
                    continue

                # 2) Dust filter:
                #    - keep explicit system transfers even if tiny (optional; helpful for real dust deposits)
                #    - otherwise drop tiny movements
                if abs(delta_excl_fee_sol) < min_abs_sol:
                    if not has_system_transfer:
                        continue

                # 3) Optionally drop non-system-program SOL movements (program-induced net changes)
                #    If you only care about direct transfers, set include_program_sol=False.
                if not include_program_sol and not has_system_transfer:
                    continue

                direction = "IN" if delta_excl_fee_sol > 0 else "OUT"

                out.append(
                    {
                        "signature": sig,
                        "blockTime": block_time,
                        "err": s.get("err"),
                        "deltaSOL": delta_sol,  # net incl fee
                        "deltaExclFeeSOL": delta_excl_fee_sol,  # net excluding fee (if fee payer)
                        "feeSOL": fee_sol,
                        "direction": direction,
                        "counterparty": counterparty,
                        "hasSystemTransfer": has_system_transfer,
                        "isFeePayer": is_fee_payer,
                    }
                )

                seen += 1

                if seen % 10 == 0:
                    in_cnt = sum(1 for t in out if t["direction"] == "IN")
                    out_cnt = len(out) - in_cnt
                    print(f"      [{seen}] Kept {in_cnt} IN, {out_cnt} OUT (after spam filters)", flush=True)

                if max_txs is not None and seen >= max_txs:
                    print(f"[RPC] ✅ Reached max_txs limit ({max_txs})", flush=True)
                    return out

            if stop_after_page:
                print("[RPC] ⛔ Reached cutoff date, stopping pagination.", flush=True)
                break

    print(f"[RPC] ✅ Complete! Kept {len(out)} total transactions across {page} pages", flush=True)
    return out


def get_creator_funders(creator_address: str) -> list:
    """Get all funders for a creator."""
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


def classify_address(address: str) -> Tuple[str, str]:
    """Classify address type and return (label, classification)."""
    cex_info = get_cex_info(address)
    if cex_info:
        return (f"✅ CEX: {cex_info.get('name')}", "cex")

    infra_info = get_account_info(address)
    if infra_info:
        return (f"✅ INFRA: {infra_info.get('name')}", "infra")

    pumpfun_info = get_pumpfun_creator_info(address)
    if pumpfun_info:
        return (f"🎯 PUMPFUN: {pumpfun_info.get('name')}", "pumpfun")

    suspicious_info = get_suspicious_wallet_info(address)
    if suspicious_info:
        return (f"⚠️ SUSPICIOUS: {suspicious_info.get('name')}", "suspicious")

    return ("❓ UNKNOWN", "unknown")


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Get SOL IN/OUT history for an address using free RPC (fast + last 30d + spam filtering)"
    )
    parser.add_argument("funder", type=str, help="Funder address to analyze")
    parser.add_argument("--delay", type=float, default=0.15, help="Delay between getTransaction calls (default 0.15s)")
    parser.add_argument("--max-txs", type=int, default=None, help="Max kept transactions (default: all kept)")
    parser.add_argument("--since-days", type=int, default=30, help="Only scan last N days (default 30)")
    parser.add_argument("--min-sol", type=float, default=DEFAULT_MIN_ABS_SOL, help="Dust filter threshold in SOL (default 0.001)")
    parser.add_argument(
        "--only-system-transfers",
        action="store_true",
        help="Only keep explicit System Program transfers involving the address",
    )
    args = parser.parse_args()

    funder = args.funder

    print(f"[ANALYSIS] Funder SOL IN/OUT History")
    print(f"[FUNDER] {funder}\n")

    funder_type, _ = classify_address(funder)
    print(f"Type: {funder_type}\n")

    include_program_sol = not args.only_system_transfers

    print("[RPC] Fetching transaction history (paginated, cutoff + spam filters)...")
    rows = fetch_all_sol_in_out(
        funder,
        rps_delay=args.delay,
        max_txs=args.max_txs,
        since_days=args.since_days,
        min_abs_sol=args.min_sol,
        include_program_sol=include_program_sol,
        sig_limit=DEFAULT_SIG_LIMIT,
    )

    if not rows:
        print("[RPC] ℹ️  No (non-spam) SOL movements detected for this address in the selected window.")
        return

    inflows = [r for r in rows if r["direction"] == "IN"]
    outflows = [r for r in rows if r["direction"] == "OUT"]

    # Use deltaExclFeeSOL for “actual movement” direction/magnitude
    total_in = sum(r["deltaExclFeeSOL"] for r in inflows)
    total_out = sum(-r["deltaExclFeeSOL"] for r in outflows)
    total_fees = sum(r["feeSOL"] for r in rows)

    print(f"\n{'='*100}")
    print("SUMMARY")
    print(f"{'='*100}")
    print(f"Window: last {args.since_days} days")
    print(f"Kept transactions: {len(rows)} (after spam filters)")
    print(f"Total IN:   {total_in:>12.4f} SOL ({len(inflows)} txs)")
    print(f"Total OUT:  {total_out:>12.4f} SOL ({len(outflows)} txs)")
    print(f"Total FEES: {total_fees:>12.4f} SOL (fees in kept txs)")
    print(f"Net:        {total_in - total_out:>12.4f} SOL")

    # Top inflows
    if inflows:
        print("\n📥 TOP INFLOWS (From):\n")
        sorted_in = sorted(inflows, key=lambda r: r["deltaExclFeeSOL"], reverse=True)
        for i, r in enumerate(sorted_in[:10], 1):
            counterparty = r.get("counterparty") or "Unknown"
            classification, _ = (
                classify_address(counterparty) if counterparty != "Unknown" else ("❓ UNKNOWN", "unknown")
            )
            time_str = (
                f"  | {time.strftime('%Y-%m-%d', time.localtime(r['blockTime']))}"
                if r.get("blockTime")
                else ""
            )
            sys_flag = " (system)" if r.get("hasSystemTransfer") else ""
            print(f"[{i:2}] {r['deltaExclFeeSOL']:>8.4f} SOL ← {counterparty}{sys_flag}")
            print(f"      {classification}{time_str}")

        if len(sorted_in) > 10:
            remaining = sum(r["deltaExclFeeSOL"] for r in sorted_in[10:])
            print(f"     ... and {len(sorted_in) - 10} more ({remaining:.4f} SOL)")

    # Top outflows
    if outflows:
        print("\n📤 TOP OUTFLOWS (To):\n")
        sorted_out = sorted(outflows, key=lambda r: r["deltaExclFeeSOL"])  # most negative first
        for i, r in enumerate(sorted_out[:10], 1):
            counterparty = r.get("counterparty") or "Unknown"
            classification, _ = (
                classify_address(counterparty) if counterparty != "Unknown" else ("❓ UNKNOWN", "unknown")
            )
            time_str = (
                f"  | {time.strftime('%Y-%m-%d', time.localtime(r['blockTime']))}"
                if r.get("blockTime")
                else ""
            )
            sys_flag = " (system)" if r.get("hasSystemTransfer") else ""
            print(f"[{i:2}] {-r['deltaExclFeeSOL']:>8.4f} SOL → {counterparty}{sys_flag}")
            print(f"      {classification}{time_str}")

        if len(sorted_out) > 10:
            remaining = sum(-r["deltaExclFeeSOL"] for r in sorted_out[10:])
            print(f"     ... and {len(sorted_out) - 10} more ({remaining:.4f} SOL)")


if __name__ == "__main__":
    main()
