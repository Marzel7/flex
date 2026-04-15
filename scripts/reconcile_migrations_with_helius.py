#!/usr/bin/env python3
"""
Replay recent Pump.fun program activity from Helius and compare detected migrations
against locally stored token_analysis.migration_tx rows.

This is the strongest way to check whether the websocket listener is missing
Pump.fun -> PumpSwap migrations:
1. Pull recent signatures mentioning the Pump.fun program from Helius RPC
2. Fetch each transaction
3. Apply the same migration detection logic as the listener
4. Diff the resulting migration signatures against local DB state
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple


PUMPFUN_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMPSWAP_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
DEFAULT_DB_PATH = "database/flex_complete_database.db"
DEFAULT_LOOKBACK_HOURS = 24


def load_helius_rpc_url() -> str:
    env_candidates = [
        os.environ.get("HELIUS_RPC_URL", "").strip(),
        os.environ.get("RPC_HTTP", "").strip(),
    ]
    for candidate in env_candidates:
        if candidate and "helius" in candidate:
            return candidate

    env_path = ".env"
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as handle:
            text = handle.read()
        match = re.search(r'HELIUS_RPC_URL="([^"]+)"', text)
        if match:
            return match.group(1).strip()

    raise RuntimeError("Could not find Helius RPC URL in environment or .env")


def rpc_post(url: str, payload: dict, timeout: int = 30) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    return json.loads(data.decode("utf-8"))


def is_migration_transaction(logs: Sequence[str]) -> bool:
    """
    Match real Pump.fun migration transactions conservatively.
    """
    logs_text = " ".join(logs or [])
    logs_text_lower = logs_text.lower()
    if "Instruction: Buy" in logs_text or "Instruction: Sell" in logs_text:
        return False
    if "MigrateBondingCurveCreator" in logs_text:
        return False
    if "Instruction: Migrate" not in logs_text:
        return False
    if not any(
        pattern in logs_text_lower
        for pattern in (
            "initialize",
            "create_pool",
            "createpool",
            "initializepool",
            PUMPSWAP_PROGRAM.lower(),
        )
    ):
        return False
    return True


def short(sig: Optional[str], width: int = 16) -> str:
    if not sig:
        return "none"
    return sig if len(sig) <= width else sig[:width]


def load_db_migrations(db_path: str, since_ts: int) -> Tuple[Set[str], Dict[str, sqlite3.Row]]:
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT mint, migration_tx, migrated_at
        FROM token_analysis
        WHERE migration_tx IS NOT NULL
          AND migration_tx != ''
          AND migrated_at IS NOT NULL
          AND migrated_at >= ?
        """,
        (since_ts,),
    ).fetchall()
    conn.close()

    sigs: Set[str] = set()
    by_sig: Dict[str, sqlite3.Row] = {}
    for row in rows:
        sig = row["migration_tx"]
        if not sig:
            continue
        sigs.add(sig)
        by_sig[sig] = row
    return sigs, by_sig


@dataclass
class ReplayResult:
    scanned_signatures: int
    fetched_transactions: int
    detected_migrations: List[Tuple[str, int]]


def replay_recent_migrations(
    helius_rpc_url: str,
    since_ts: int,
    source_program: str,
    max_pages: int = 20,
    page_limit: int = 1000,
    *,
    verbose: bool = True,
) -> ReplayResult:
    before: Optional[str] = None
    scanned_signatures = 0
    fetched_transactions = 0
    detected: List[Tuple[str, int]] = []

    for page_index in range(max_pages):
        if verbose:
            print(
                f"[RECON] Fetching signature page {page_index + 1}/{max_pages}"
                f"{' before ' + short(before) if before else ''}",
                flush=True,
            )
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [
                source_program,
                {
                    "limit": page_limit,
                    **({"before": before} if before else {}),
                },
            ],
        }
        data = rpc_post(helius_rpc_url, payload, timeout=30)
        sigs = (data or {}).get("result") or []
        if not sigs:
            break

        window_sigs = []
        reached_older_history = False
        for item in sigs:
            bt = item.get("blockTime")
            sig = item.get("signature")
            if not sig or item.get("err") is not None:
                continue
            scanned_signatures += 1
            if isinstance(bt, int) and bt < since_ts:
                reached_older_history = True
                continue
            window_sigs.append((sig, bt))

        if verbose:
            print(
                f"[RECON] Page {page_index + 1}: {len(sigs)} signatures,"
                f" {len(window_sigs)} in window, scanned {scanned_signatures} total",
                flush=True,
            )

        for sig, bt in window_sigs:
            tx_payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [sig, {"encoding": "json", "maxSupportedTransactionVersion": 0}],
            }
            tx_data = rpc_post(helius_rpc_url, tx_payload, timeout=30)
            tx = (tx_data or {}).get("result")
            fetched_transactions += 1
            if not tx:
                continue
            tx_bt = tx.get("blockTime")
            if isinstance(tx_bt, int) and tx_bt < since_ts:
                continue
            logs = ((tx.get("meta") or {}).get("logMessages") or [])
            if is_migration_transaction(logs):
                detected.append((sig, tx_bt if isinstance(tx_bt, int) else 0))

        if verbose:
            print(
                f"[RECON] Page {page_index + 1}: fetched {fetched_transactions} txs,"
                f" detected {len(detected)} migrations so far",
                flush=True,
            )

        before = sigs[-1].get("signature")
        if reached_older_history:
            break

    detected.sort(key=lambda item: item[1], reverse=True)
    return ReplayResult(
        scanned_signatures=scanned_signatures,
        fetched_transactions=fetched_transactions,
        detected_migrations=detected,
    )


def format_ts(ts: int) -> str:
    if not ts:
        return "unknown"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def run_reconciliation(
    *,
    db_path: str = DEFAULT_DB_PATH,
    hours: int = DEFAULT_LOOKBACK_HOURS,
    max_pages: int = 20,
    page_limit: int = 1000,
    show_missing: int = 25,
    source_program: str = PUMPFUN_PROGRAM,
    verbose: bool = False,
) -> Dict[str, object]:
    since_ts = int(time.time()) - (max(1, hours) * 3600)
    helius_rpc_url = load_helius_rpc_url()

    if verbose:
        print(f"[RECON] Using Helius RPC: {helius_rpc_url[:80]}")
        print(f"[RECON] Lookback window: last {hours}h since {format_ts(since_ts)}")

    db_sigs, db_rows = load_db_migrations(db_path, since_ts)
    replay = replay_recent_migrations(
        helius_rpc_url,
        since_ts=since_ts,
        source_program=source_program,
        max_pages=max(1, max_pages),
        page_limit=max(1, page_limit),
        verbose=verbose,
    )

    helius_sigs = {sig for sig, _ts in replay.detected_migrations}
    matched_sigs = helius_sigs & db_sigs
    missing_in_db = [item for item in replay.detected_migrations if item[0] not in db_sigs]
    extra_in_db = sorted(sig for sig in db_sigs if sig not in helius_sigs)
    capture_rate = (100.0 * len(matched_sigs) / len(helius_sigs)) if helius_sigs else 100.0

    missing_records = [
        {
            "signature": sig,
            "block_time": ts,
            "detected_at": format_ts(ts),
        }
        for sig, ts in missing_in_db[: max(1, show_missing)]
    ]
    extra_records = []
    for sig in extra_in_db[: max(1, show_missing)]:
        row = db_rows.get(sig)
        ts = int(row["migrated_at"]) if row and row["migrated_at"] is not None else 0
        mint = row["mint"] if row else None
        extra_records.append(
            {
                "signature": sig,
                "mint": mint,
                "migrated_at": ts,
                "migrated_at_text": format_ts(ts),
            }
        )

    return {
        "since_ts": since_ts,
        "since_text": format_ts(since_ts),
        "hours": max(1, hours),
        "source_program": source_program,
        "scanned_signatures": replay.scanned_signatures,
        "fetched_transactions": replay.fetched_transactions,
        "helius_detected": len(helius_sigs),
        "db_detected": len(db_sigs),
        "captured_by_db": len(matched_sigs),
        "missing_in_db": len(missing_in_db),
        "extra_in_db": len(extra_in_db),
        "capture_rate": round(capture_rate, 1),
        "missing_records": missing_records,
        "extra_records": extra_records,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Reconcile recent migrations against Helius replay")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    parser.add_argument("--hours", type=int, default=DEFAULT_LOOKBACK_HOURS)
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--page-limit", type=int, default=1000)
    parser.add_argument("--show-missing", type=int, default=25)
    parser.add_argument(
        "--source-program",
        default=PUMPFUN_PROGRAM,
        help="Program address to replay with getSignaturesForAddress (default: Pump.fun program)",
    )
    args = parser.parse_args(argv)

    result = run_reconciliation(
        db_path=args.db_path,
        hours=args.hours,
        max_pages=args.max_pages,
        page_limit=args.page_limit,
        show_missing=args.show_missing,
        source_program=args.source_program,
        verbose=True,
    )

    print()
    print("[RECON] Summary")
    print(f"  Helius scanned source-program signatures: {result['scanned_signatures']}")
    print(f"  Helius fetched transactions:        {result['fetched_transactions']}")
    print(f"  Helius detected migrations:         {result['helius_detected']}")
    print(f"  DB migrated rows in window:         {result['db_detected']}")
    print(f"  Captured by DB:                     {result['captured_by_db']}")
    print(f"  Missing in DB:                      {result['missing_in_db']}")
    print(f"  Extra in DB:                        {result['extra_in_db']}")
    print(f"  Capture rate:                       {result['capture_rate']:.1f}%")

    missing_records = result["missing_records"]
    extra_records = result["extra_records"]

    if missing_records:
        print()
        print(f"[RECON] Missing migrations (top {min(args.show_missing, len(missing_records))})")
        for record in missing_records:
            print(f"  {record['detected_at']} | {record['signature']}")

    if extra_records:
        print()
        print(f"[RECON] Extra DB migrations not seen in replay (top {min(args.show_missing, len(extra_records))})")
        for record in extra_records:
            print(f"  {record['migrated_at_text']} | {short(record['mint'], 20)} | {record['signature']}")

    return 0 if not missing_records else 2


if __name__ == "__main__":
    raise SystemExit(main())
