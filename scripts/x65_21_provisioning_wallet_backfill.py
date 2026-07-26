"""X65.21 Phase 4 — Historical backfill for wt_provisioning_wallets.

Idempotent, additive-only. Re-running this script is always safe: every write
goes through provisioning_wallet.record_provisioning_wallet()'s own
ON CONFLICT(mint) DO UPDATE, so a launch already recorded is simply
re-confirmed, never duplicated.

Recovery priority, exactly matching X65.21 Phase 4's required order:
  1. Persisted funding signature (wt_watchtower_launches.wrap_close_signature)
     -> single getTransaction decode. No search.
  2. Bounded signature lookup (<=5 pages / <=100 signatures on the CREATOR
     wallet) when no usable signature is persisted. This is the exact bound
     X65.19 already used and validated -- never widened here.
  3. Anything that would require exceeding that bound is SKIPPED and reported
     as unresolved, per this task's explicit "do not perform unbounded
     wallet-history scans" constraint.

Requires an RPC endpoint (Solana JSON-RPC, e.g. Helius) to be provided via the
RPC_URL environment variable. Makes no writes to wt_provisioning_edges,
wt_watchtower_launches, or any other pre-existing table -- reads
wt_watchtower_launches, writes only wt_provisioning_wallets and
wt_provisioning_wallet_edges (both new, additive tables from X65.21).

Usage:
    RPC_URL="https://mainnet.helius-rpc.com/?api-key=..." python3 scripts/x65_21_provisioning_wallet_backfill.py [--dry-run]
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.ops.provisioning_wallet import (
    RECOVERY_BOUNDED_SIGNATURE_LOOKUP,
    RECOVERY_PERSISTED_SIGNATURE_DECODE,
    ensure_schema,
    record_provisioning_wallet,
)

OPS_DB_PATH = os.environ.get("WT_OPS_DB_PATH", "database/wt_ops_v2.db")
MAX_LOOKUP_PAGES = 5   # bounded: <=5 pages of 20 = <=100 signatures per wallet
PAGE_SIZE = 20


def _rpc_call(rpc_url: str, method: str, params: list) -> dict:
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(rpc_url, data=payload, headers={"Content-Type": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read())
        except Exception:
            if attempt == 2:
                raise
            time.sleep(1)
    return {}


def _get_transaction(rpc_url: str, sig: str) -> dict | None:
    r = _rpc_call(rpc_url, "getTransaction", [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
    return r.get("result")


def _get_signatures(rpc_url: str, addr: str, before: str | None = None, limit: int = PAGE_SIZE) -> list:
    params = [addr, {"limit": limit}]
    if before:
        params[1]["before"] = before
    r = _rpc_call(rpc_url, "getSignaturesForAddress", params)
    return r.get("result") or []


def _extract_wallet_p(result: dict, subprov: str) -> tuple[str | None, str | None]:
    """Returns (wallet_p, mechanism) from a decoded transaction's instructions,
    using exactly the two mechanisms X65.19 proved: WSOL wrap-close
    (system.transfer from subprov, closeAccount later in the same tx) and
    seeded-account-close (createAccountWithSeed from subprov)."""
    instrs = result["transaction"]["message"]["instructions"]
    wallet_p = None
    mechanism = None
    close_owner = None
    close_destination = None
    for ix in instrs:
        info = ix.get("parsed", {}).get("info", {})
        ptype = ix.get("parsed", {}).get("type")
        if ptype == "transfer" and info.get("source") == subprov:
            wallet_p = info.get("destination")
            mechanism = "WSOL_WRAP_CLOSE"
        if ptype == "createAccountWithSeed" and info.get("source") == subprov:
            mechanism = "SEEDED_ACCOUNT_CLOSE"
            # wallet_p resolved below from close_owner (the seeded account's
            # `base`), matching X65.19's own corrected extraction.
        if ptype == "closeAccount":
            close_owner = info.get("owner")
            close_destination = info.get("destination")
    if mechanism == "SEEDED_ACCOUNT_CLOSE" and close_owner:
        wallet_p = close_owner
    return wallet_p, mechanism, close_destination


def backfill(rpc_url: str, dry_run: bool = False) -> dict:
    conn = sqlite3.connect(OPS_DB_PATH)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)

    rows = conn.execute(
        "SELECT mint, creator_wallet, subprov_wallet, wrap_close_signature "
        "FROM wt_watchtower_launches"
    ).fetchall()

    stats = {"total": len(rows), "persisted_signature": 0, "bounded_lookup": 0,
              "unresolved": 0, "already_recorded_skipped": 0}
    unresolved_mints = []

    for row in rows:
        mint, creator, subprov = row["mint"], row["creator_wallet"], row["subprov_wallet"]
        if not creator or not subprov:
            stats["unresolved"] += 1
            unresolved_mints.append((mint, "missing_creator_or_subprov"))
            continue

        sig = row["wrap_close_signature"]
        recovery_method = None
        used_sig = None

        if sig and len(sig) > 60:
            result = _get_transaction(rpc_url, sig)
            if result:
                wallet_p, mechanism, close_dest = _extract_wallet_p(result, subprov)
                if wallet_p and mechanism and close_dest == creator:
                    recovery_method = RECOVERY_PERSISTED_SIGNATURE_DECODE
                    used_sig = sig
                    stats["persisted_signature"] += 1

        if not recovery_method:
            # Bounded signature lookup fallback -- capped at MAX_LOOKUP_PAGES,
            # exactly the bound X65.19 validated. Never widened.
            before = None
            earliest_sig = None
            pages = 0
            while pages < MAX_LOOKUP_PAGES:
                page = _get_signatures(rpc_url, creator, before=before)
                if not page:
                    break
                earliest_sig = page[-1]["signature"]
                before = earliest_sig
                pages += 1
                time.sleep(0.05)
            reached_end = pages < MAX_LOOKUP_PAGES
            if earliest_sig and reached_end:
                result = _get_transaction(rpc_url, earliest_sig)
                if result:
                    wallet_p, mechanism, close_dest = _extract_wallet_p(result, subprov)
                    if wallet_p and mechanism and close_dest == creator:
                        recovery_method = RECOVERY_BOUNDED_SIGNATURE_LOOKUP
                        used_sig = earliest_sig
                        stats["bounded_lookup"] += 1

        if not recovery_method:
            stats["unresolved"] += 1
            unresolved_mints.append((mint, "exceeded_bounded_search_limit_or_no_match"))
            continue

        if not dry_run:
            record_provisioning_wallet(
                conn, mint=mint, subprov_wallet=subprov, creator_wallet=creator,
                provisioning_wallet=wallet_p, mechanism=mechanism,
                recovery_method=recovery_method, funding_signature=used_sig,
                reconstructed=True,
            )
        time.sleep(0.05)

    if not dry_run:
        conn.commit()
    conn.close()

    stats["unresolved_mints"] = unresolved_mints
    return stats


if __name__ == "__main__":
    rpc_url = os.environ.get("RPC_URL")
    if not rpc_url:
        print("RPC_URL environment variable is required.")
        sys.exit(1)
    dry_run = "--dry-run" in sys.argv
    result = backfill(rpc_url, dry_run=dry_run)
    print(json.dumps(result, indent=2))
