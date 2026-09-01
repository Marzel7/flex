#!/usr/bin/env python3
"""Trace direct funders from a strict 135-wallet cohort using full-history paging."""

import argparse
import csv
import json
import os
import re
import requests
from pathlib import Path
from typing import Optional


LIMIT_LAMPORTS = 100000  # ignore dust noise
DEFAULT_API_URL = "https://api.helius.xyz/v0/addresses/{wallet}/transactions"


def _load_creator_addresses(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []

    if path.suffix.lower() == ".jsonl":
        addrs: list[str] = []
        for raw_line in text.splitlines():
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            item = json.loads(raw_line)
            if isinstance(item, str):
                candidate = item
            elif isinstance(item, dict):
                candidate = (
                    item.get("creator")
                    or item.get("creator_address")
                    or item.get("address")
                    or item.get("wallet")
                    or item.get("creator_wallet")
                )
            else:
                continue
            if candidate:
                addrs.append(str(candidate).strip())
        return addrs

    if path.suffix.lower() == ".json":
        payload = json.loads(text)
        if isinstance(payload, dict):
            creators = payload.get("creators", payload.get("addresses", []))
        else:
            creators = payload
        if not isinstance(creators, list):
            raise ValueError("JSON cohort file must be an array, or {'creators': [...]} object")
        return [str(item).strip() for item in creators if str(item).strip()]

    if path.suffix.lower() == ".csv":
        addrs: list[str] = []
        reader = csv.reader(text.splitlines())
        header = next(reader, None)
        if not header:
            return addrs

        norm_header = [c.strip().lower() for c in header]
        for i, col_name in enumerate(("creator", "creator_address", "address", "wallet", "creator_wallet")):
            if col_name in norm_header:
                wallet_idx = norm_header.index(col_name)
                break
        else:
            wallet_idx = 0

        for row in reader:
            if not row:
                continue
            if wallet_idx >= len(row):
                continue
            value = row[wallet_idx].strip()
            if value:
                addrs.append(value)
        return addrs

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return [line for line in lines]


def _coerce_api_key(value: Optional[str]) -> str:
    if value:
        return value.strip()
    return os.getenv("HELIUS_API_KEY", "").strip()


def _to_sol(lamports: int) -> float:
    return lamports / 1e9


def _extract_direct_funder(wallet: str, api_key: str, max_pages: int, timeout_s: int = 15, per_page: int = 100) -> tuple[Optional[str], float, int, int]:
    """Return (funder_address, amount_sol, pages_scanned, tx_scanned)."""
    best_src: Optional[str] = None
    best_amt = 0.0
    tx_before = None
    pages_scanned = 0
    tx_scanned = 0

    while True:
        if max_pages and pages_scanned >= max_pages:
            break

        params = {
            "api-key": api_key,
            "limit": per_page,
        }
        if tx_before:
            params["before"] = tx_before

        r = requests.get(
            DEFAULT_API_URL.format(wallet=wallet),
            params=params,
            timeout=timeout_s,
        )
        pages_scanned += 1

        if r.status_code != 200:
            break

        txs = r.json() if r.headers.get("content-type", "").startswith("application/json") else []
        if not isinstance(txs, list) or not txs:
            break

        for tx in txs:
            tx_scanned += 1
            for tr in tx.get("nativeTransfers") or []:
                if (
                    tr.get("toUserAccount") == wallet
                    and int(tr.get("amount") or 0) > LIMIT_LAMPORTS
                ):
                    lamports = int(tr.get("amount") or 0)
                    amount = _to_sol(lamports)
                    if amount > best_amt:
                        best_amt = amount
                        best_src = tr.get("fromUserAccount")

        if len(txs) < per_page:
            break

        tx_before = txs[-1].get("signature")
        if not tx_before:
            break

    return best_src, best_amt, pages_scanned, tx_scanned


def _validate_address(address: str) -> str:
    if not re.match(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$", address):
        raise ValueError(f"Invalid wallet address: {address}")
    return address


def _save_report_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cohort-file", required=True, type=Path, help="Text or JSON file with 135 creator addresses")
    parser.add_argument("--api-key", default=None, help="HELIUS API key; falls back to HELIUS_API_KEY env")
    parser.add_argument("--expected-count", type=int, default=135)
    parser.add_argument("--max-pages-per-wallet", type=int, default=0, help="0 = unlimited")
    parser.add_argument("--per-page", type=int, default=100)
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--output", type=Path, default=Path("artifacts/full_history_direct_funder_trace_135.jsonl"))
    parser.add_argument("--only-with-funder", action="store_true")
    args = parser.parse_args()

    api_key = _coerce_api_key(args.api_key)
    if not api_key:
        print("[ERROR] Missing HELIUS_API_KEY")
        return 2

    raw_creators = [c.strip() for c in _load_creator_addresses(args.cohort_file) if c.strip()]
    if not raw_creators:
        print(f"[ERROR] No creators in {args.cohort_file}")
        return 2

    try:
        creators = [_validate_address(c) for c in raw_creators]
    except ValueError as exc:
        print(f"[ERROR] {exc}")
        return 2

    unique_creators = sorted(set(creators))
    if len(unique_creators) != args.expected_count:
        print(
            f"[ERROR] Cohort count mismatch: expected {args.expected_count}, got {len(unique_creators)}"
        )
        return 2

    max_pages = args.max_pages_per_wallet
    if max_pages <= 0:
        max_pages = 10**9

    rows = []
    mapped = 0
    mapped_amount = 0.0

    for idx, wallet in enumerate(unique_creators, 1):
        funder, amount, pages, txs = _extract_direct_funder(
            wallet,
            api_key,
            max_pages=max_pages,
            timeout_s=args.timeout,
            per_page=args.per_page,
        )

        if funder:
            mapped += 1
            mapped_amount += amount
            status = "mapped"
        else:
            status = "unmapped"

        print(
            f"[{idx}/{len(unique_creators)}] {wallet[:10]}... => "
            f"{status} {funder[:10]+'...' if funder else 'None'} ({pages} pages, {txs} txs)"
        )

        row = {
            "creator": wallet,
            "direct_funder": funder,
            "amount_sol": round(amount, 9) if funder else None,
            "pages_scanned": pages,
            "transactions_scanned": txs,
            "status": status,
        }
        if args.only_with_funder and not funder:
            continue
        rows.append(row)

    _save_report_rows(args.output, rows)
    print(
        f"[DONE] traced={mapped}/{len(unique_creators)} "
        f"mapped_sol={round(mapped_amount, 9)} output={args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
