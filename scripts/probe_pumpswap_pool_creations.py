#!/usr/bin/env python3
"""
Read-only PumpSwap migration-shape probe.

This scans recent PumpSwap program transactions from Helius and classifies broad
pool-creation / migration candidates without writing to the database. It is meant
to answer one question during outages:

    Are plausible Pump.fun -> PumpSwap migrations happening on-chain while our
    old detector and/or PumpPortal stream see zero?

The existing reconciler is intentionally strict and requires "Instruction:
Migrate". This probe reports both the old strict marker and newer pool-init-like
signals so we can spot detector drift.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import time
import urllib.request
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple


PUMPSWAP_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
PUMPFUN_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112"
DEFAULT_DB_PATH = "database/flex_complete_database.db"

PUMPSWAP_POOL_TERMS = (
    "instruction: createpool",
    "instruction: create_pool",
    "instruction: initializepool",
    "instruction: initialize_pool",
    "instruction: migrate",
    "instruction: migratebondingcurve",
    "create_pool",
    "createpool",
    "initialize_pool",
    "initializepool",
)

NON_CANDIDATE_TERMS = (
    "instruction: buy",
    "instruction: sell",
    "instruction: collectcreatorfee",
    "instruction: collectcreatorfeev2",
    "instruction: collectcoincreatorfee",
)


def load_helius_rpc_url() -> str:
    for name in ("HELIUS_RPC_URL", "RPC_HTTP"):
        candidate = os.environ.get(name, "").strip()
        if candidate and "helius" in candidate:
            return candidate

    if os.path.exists(".env"):
        with open(".env", "r", encoding="utf-8") as handle:
            text = handle.read()
        for name in ("HELIUS_RPC_URL", "RPC_HTTP"):
            match = re.search(rf'{name}="?([^"\n]+)"?', text)
            if match and "helius" in match.group(1):
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
        return json.loads(resp.read().decode("utf-8"))


def format_ts(ts: Optional[int]) -> str:
    if not ts:
        return "unknown"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def short(value: Optional[str], width: int = 12) -> str:
    if not value:
        return "none"
    return value if len(value) <= width else value[:width]


def log_text(logs: Sequence[str]) -> str:
    return " ".join(logs or [])


def extract_instruction_counts(logs: Sequence[str]) -> Counter:
    counts: Counter = Counter()
    for log in logs or []:
        lower = log.lower()
        marker = "instruction:"
        if marker not in lower:
            continue
        instruction = lower.split(marker, 1)[1].strip().split(" ", 1)[0]
        if instruction:
            counts[instruction] += 1
    return counts


def extract_mints_from_balances(tx: dict) -> List[str]:
    meta = tx.get("meta") or {}
    mints: List[str] = []
    seen: Set[str] = set()
    for balance in (meta.get("preTokenBalances") or []) + (meta.get("postTokenBalances") or []):
        mint = balance.get("mint")
        if not mint or mint == WRAPPED_SOL_MINT or mint in seen:
            continue
        seen.add(mint)
        mints.append(mint)
    return mints


def extract_account_keys(tx: dict) -> List[str]:
    message = ((tx.get("transaction") or {}).get("message") or {})
    keys = message.get("accountKeys") or []
    out: List[str] = []
    for key in keys:
        if isinstance(key, str):
            out.append(key)
        elif isinstance(key, dict) and key.get("pubkey"):
            out.append(key["pubkey"])
    return out


def load_db_migration_sigs(db_path: str, since_ts: int) -> Set[str]:
    if not db_path or not os.path.exists(db_path):
        return set()
    conn = sqlite3.connect(db_path, timeout=10)
    rows = conn.execute(
        """
        SELECT migration_tx
        FROM token_analysis
        WHERE migration_tx IS NOT NULL
          AND migration_tx != ''
          AND migrated_at IS NOT NULL
          AND migrated_at >= ?
        """,
        (since_ts,),
    ).fetchall()
    conn.close()
    return {row[0] for row in rows if row and row[0]}


@dataclass
class Candidate:
    signature: str
    block_time: int
    strict_old_migrate: bool
    pumpswap_pool_like: bool
    mentions_pumpfun: bool
    mints: List[str]
    instruction_counts: Counter
    log_sample: List[str]
    in_db: bool


def classify_candidate(tx: dict, signature: str, db_sigs: Set[str]) -> Optional[Candidate]:
    meta = tx.get("meta") or {}
    logs = meta.get("logMessages") or []
    text = log_text(logs)
    lower = text.lower()

    if any(term in lower for term in NON_CANDIDATE_TERMS):
        return None

    strict_old_migrate = "Instruction: Migrate" in text
    pumpswap_pool_like = any(term in lower for term in PUMPSWAP_POOL_TERMS)
    mentions_pumpfun = PUMPFUN_PROGRAM in text or PUMPFUN_PROGRAM in extract_account_keys(tx)
    mints = extract_mints_from_balances(tx)

    # Keep this broader than the production detector, but avoid generic ATA /
    # token-account initialization and Pump.fun fee-collection noise. A strong
    # candidate needs an explicit pool/migration instruction.
    if not (strict_old_migrate or pumpswap_pool_like):
        return None

    return Candidate(
        signature=signature,
        block_time=tx.get("blockTime") or 0,
        strict_old_migrate=strict_old_migrate,
        pumpswap_pool_like=pumpswap_pool_like,
        mentions_pumpfun=mentions_pumpfun,
        mints=mints,
        instruction_counts=extract_instruction_counts(logs),
        log_sample=list(logs[:8]),
        in_db=signature in db_sigs,
    )


def fetch_signatures(
    rpc_url: str,
    since_ts: int,
    max_pages: int,
    page_limit: int,
    verbose: bool,
) -> Tuple[List[Tuple[str, int]], bool]:
    before: Optional[str] = None
    signatures: List[Tuple[str, int]] = []
    reached_older = False

    for page_idx in range(max_pages):
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [
                PUMPSWAP_PROGRAM,
                {
                    "limit": page_limit,
                    **({"before": before} if before else {}),
                },
            ],
        }
        data = rpc_post(rpc_url, payload)
        page = (data or {}).get("result") or []
        if not page:
            break

        in_window = 0
        for item in page:
            sig = item.get("signature")
            bt = item.get("blockTime")
            if not sig or item.get("err") is not None:
                continue
            if isinstance(bt, int) and bt < since_ts:
                reached_older = True
                continue
            signatures.append((sig, bt if isinstance(bt, int) else 0))
            in_window += 1

        if verbose:
            oldest = page[-1].get("blockTime")
            print(
                f"[PROBE] page {page_idx + 1}/{max_pages}: {in_window}/{len(page)} in window,"
                f" oldest={format_ts(oldest)}",
                flush=True,
            )

        before = page[-1].get("signature")
        if reached_older:
            break

    return signatures, reached_older


def run_probe(args: argparse.Namespace) -> int:
    since_ts = int(time.time()) - max(1, args.hours) * 3600
    rpc_url = load_helius_rpc_url()
    display_rpc = re.sub(r"(api-key=)[^&]+", r"\1***", rpc_url)
    print(f"[PROBE] Using Helius RPC: {display_rpc}")
    print(f"[PROBE] Window: last {args.hours}h since {format_ts(since_ts)}")
    print(f"[PROBE] Source: PumpSwap program {PUMPSWAP_PROGRAM}")

    db_sigs = load_db_migration_sigs(args.db_path, since_ts)
    if args.signature:
        return run_signature_probe(rpc_url, db_sigs, args.signature)

    signatures, reached_older = fetch_signatures(
        rpc_url,
        since_ts,
        max_pages=max(1, args.max_pages),
        page_limit=max(1, args.page_limit),
        verbose=True,
    )

    if args.max_txs:
        signatures = signatures[: args.max_txs]

    print(
        f"[PROBE] Fetched signature refs: {len(signatures)}"
        f" ({'covered window' if reached_older else 'did not reach window start'})"
    )

    candidates: List[Candidate] = []
    fetched = 0
    for idx, (sig, _bt) in enumerate(signatures, start=1):
        tx_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTransaction",
            "params": [sig, {"encoding": "json", "maxSupportedTransactionVersion": 0}],
        }
        tx_data = rpc_post(rpc_url, tx_payload)
        tx = (tx_data or {}).get("result")
        fetched += 1
        if not tx:
            continue
        candidate = classify_candidate(tx, sig, db_sigs)
        if candidate:
            candidates.append(candidate)
            if len(candidates) <= args.show:
                print()
                print(
                    f"[CANDIDATE] {len(candidates)} {format_ts(candidate.block_time)} "
                    f"sig={candidate.signature}"
                )
                print(
                    "  evidence: "
                    f"strict_old_migrate={candidate.strict_old_migrate} "
                    f"pumpswap_pool_like={candidate.pumpswap_pool_like} "
                    f"mentions_pumpfun={candidate.mentions_pumpfun} "
                    f"in_db={candidate.in_db}"
                )
                print(
                    f"  mints: {', '.join(candidate.mints[:6]) if candidate.mints else 'none'}"
                )
                print(f"  instructions: {dict(candidate.instruction_counts)}")
                for line in candidate.log_sample:
                    print(f"    log: {line[:180]}")

        if args.progress_every and idx % args.progress_every == 0:
            print(f"[PROBE] scanned {idx}/{len(signatures)} txs, candidates={len(candidates)}", flush=True)

    strict_count = sum(1 for item in candidates if item.strict_old_migrate)
    pool_like_count = sum(1 for item in candidates if item.pumpswap_pool_like)
    pumpfun_count = sum(1 for item in candidates if item.mentions_pumpfun)
    in_db_count = sum(1 for item in candidates if item.in_db)
    mint_count = sum(1 for item in candidates if item.mints)

    print()
    print("[PROBE] Summary")
    print(f"  signature refs scanned:      {len(signatures)}")
    print(f"  transactions fetched:        {fetched}")
    print(f"  broad candidates:            {len(candidates)}")
    print(f"  strict old migrate marker:   {strict_count}")
    print(f"  PumpSwap pool-like cands:    {pool_like_count}")
    print(f"  mentions Pump.fun program:   {pumpfun_count}")
    print(f"  candidates with mint:        {mint_count}")
    print(f"  candidates already in DB:    {in_db_count}")
    print(f"  reached full time window:    {reached_older}")

    if candidates and strict_count == 0 and pool_like_count > 0:
        print()
        print("[PROBE] Read: found broad Pump.fun/PumpSwap candidates but zero old 'Instruction: Migrate' markers.")
        print("        That is the detector-drift shape we are looking for.")
    elif not candidates:
        print()
        print("[PROBE] Read: no broad candidates found in scanned PumpSwap traffic.")
        print("        Increase --max-pages/--max-txs or validate expected migration volume externally.")

    return 0


def run_signature_probe(rpc_url: str, db_sigs: Set[str], signatures: Sequence[str]) -> int:
    print(f"[PROBE] Direct signature mode: {len(signatures)} signature(s)")
    for sig in signatures:
        tx_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTransaction",
            "params": [sig, {"encoding": "json", "maxSupportedTransactionVersion": 0}],
        }
        tx_data = rpc_post(rpc_url, tx_payload)
        tx = (tx_data or {}).get("result")
        print()
        print(f"[SIGNATURE] {sig}")
        if not tx:
            print("  result: transaction not found")
            continue
        candidate = classify_candidate(tx, sig, db_sigs)
        logs = ((tx.get("meta") or {}).get("logMessages") or [])
        print(f"  block_time: {format_ts(tx.get('blockTime'))}")
        print(f"  log_count: {len(logs)}")
        print(f"  mints: {', '.join(extract_mints_from_balances(tx)[:8]) or 'none'}")
        print(f"  instructions: {dict(extract_instruction_counts(logs))}")
        if candidate:
            print(
                "  candidate: yes "
                f"strict_old_migrate={candidate.strict_old_migrate} "
                f"pumpswap_pool_like={candidate.pumpswap_pool_like} "
                f"mentions_pumpfun={candidate.mentions_pumpfun} "
                f"in_db={candidate.in_db}"
            )
        else:
            print("  candidate: no")
        for line in logs[:20]:
            print(f"    log: {line[:180]}")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Probe broad PumpSwap pool-creation candidates")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    parser.add_argument("--hours", type=int, default=2)
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--page-limit", type=int, default=200)
    parser.add_argument("--max-txs", type=int, default=600)
    parser.add_argument("--show", type=int, default=12)
    parser.add_argument("--progress-every", type=int, default=100)
    parser.add_argument(
        "--signature",
        action="append",
        help="Probe one transaction signature directly instead of scanning pages",
    )
    args = parser.parse_args(argv)
    return run_probe(args)


if __name__ == "__main__":
    raise SystemExit(main())
