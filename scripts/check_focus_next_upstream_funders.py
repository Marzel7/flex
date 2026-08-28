#!/usr/bin/env python3
"""Bounded, read-only upstream-funder check for the frozen Focus Next cohort."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DB = ROOT / "database/wt_ops_v2.db"
OUT = ROOT / "docs/audits/focus_next_upstream_funder_rpc.v1.json"
CANDIDATE_ID = "p3r-v2-dc4953db7adb853337c4"
MAX_FUNDERS = 27
MAX_PAGES_PER_FUNDER = 3
PAGE_SIZE = 1000
MAX_REQUESTS = MAX_FUNDERS * (MAX_PAGES_PER_FUNDER + 1)


def endpoint() -> str:
    if value := os.environ.get("HELIUS_RPC_URL"):
        return value
    env = ROOT / ".env"
    match = re.search(r"^(?:export\s+)?HELIUS_RPC_URL=[\"']?([^\"'\n]+)", env.read_text(), re.M)
    if not match:
        raise RuntimeError("HELIUS_RPC_URL unavailable")
    return match.group(1)


def rpc(url: str, method: str, params: list, calls: list[dict]) -> dict | list | None:
    if len(calls) >= MAX_REQUESTS:
        raise RuntimeError("RPC request ceiling reached")
    request = urllib.request.Request(
        url,
        data=json.dumps({"jsonrpc": "2.0", "id": len(calls) + 1, "method": method, "params": params}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read())
    calls.append({"ordinal": len(calls) + 1, "method": method, "target": params[0]})
    if payload.get("error"):
        raise RuntimeError(f"RPC {method} failed: {payload['error'].get('code')}")
    return payload.get("result")


def members() -> list[dict]:
    from src.ops.potential_operations import detail

    candidate = detail(str(DB), CANDIDATE_ID)
    if not candidate or len(candidate["members"]) != MAX_FUNDERS:
        raise RuntimeError("frozen Focus Next member contract is unavailable")
    return [{"mint": item["mint"], "creator": item["creator"], "direct_funder": item["parent"]} for item in candidate["members"]]


def incoming_source(transaction: dict | None, address: str) -> dict:
    if not transaction:
        return {"status": "TRANSACTION_UNAVAILABLE"}
    message = transaction.get("transaction", {}).get("message", {})
    keys = [item.get("pubkey") if isinstance(item, dict) else item for item in message.get("accountKeys", [])]
    meta = transaction.get("meta") or {}
    if address not in keys:
        return {"status": "ADDRESS_NOT_IN_TRANSACTION"}
    position = keys.index(address)
    pre, post = (meta.get("preBalances") or [])[position], (meta.get("postBalances") or [])[position]
    sources = []
    for index, (before, after) in enumerate(zip(meta.get("preBalances") or [], meta.get("postBalances") or [])):
        if before > after:
            sources.append({"address": keys[index], "lamports": before - after})
    source = max(sources, key=lambda item: item["lamports"], default={"address": None, "lamports": 0})
    return {"status": "INITIAL_INBOUND_IDENTIFIED" if post > pre and source["address"] else "NO_MATERIAL_NATIVE_INBOUND_IDENTIFIED", "upstream_account": source["address"], "received_lamports": post - pre, "source_lamports": source["lamports"], "slot": transaction.get("slot"), "block_time": transaction.get("blockTime")}


def account_birth(url: str, address: str, calls: list[dict]) -> dict:
    before = None
    history = []
    exhausted = False
    for _ in range(MAX_PAGES_PER_FUNDER):
        options = {"limit": PAGE_SIZE}
        if before:
            options["before"] = before
        page = rpc(url, "getSignaturesForAddress", [address, options], calls) or []
        history.extend(page)
        if len(page) < PAGE_SIZE:
            exhausted = True
            break
        before = page[-1]["signature"]
    if not history:
        return {"direct_funder": address, "status": "NO_HISTORY", "history_entries": 0, "history_exhausted": exhausted}
    earliest = history[-1]
    result = incoming_source(rpc(url, "getTransaction", [earliest["signature"], {"encoding": "json", "maxSupportedTransactionVersion": 0}], calls), address)
    result.update({"direct_funder": address, "history_entries": len(history), "history_pages": (len(history) + PAGE_SIZE - 1) // PAGE_SIZE, "history_exhausted": exhausted, "earliest_signature": earliest["signature"], "earliest_block_time": earliest.get("blockTime")})
    if not exhausted:
        result["status"] = "HISTORY_CAP_REACHED"
        result["upstream_account"] = None
    return result


def main() -> None:
    cohort = members()
    if len({item["direct_funder"] for item in cohort}) != MAX_FUNDERS:
        raise RuntimeError("expected 27 distinct direct funders")
    calls: list[dict] = []
    url = endpoint()
    started = time.monotonic()
    results = [account_birth(url, item["direct_funder"], calls) | {"mint": item["mint"], "creator": item["creator"]} for item in cohort]
    report = {"schema_version": "focus_next_upstream_funder_rpc.v1", "candidate_id": CANDIDATE_ID, "scope": {"members": MAX_FUNDERS, "distinct_direct_funders": MAX_FUNDERS, "recursive_walkback": False, "read_only": True}, "caps": {"pages_per_funder": MAX_PAGES_PER_FUNDER, "page_size": PAGE_SIZE, "max_requests": MAX_REQUESTS, "retries": 0}, "results": results, "rpc": {"request_count": len(calls), "method_counts": {method: sum(item["method"] == method for item in calls) for method in {item["method"] for item in calls}}, "elapsed_seconds": round(time.monotonic() - started, 3)}, "report_digest": ""}
    semantic = {key: value for key, value in report.items() if key != "report_digest"}
    report["report_digest"] = hashlib.sha256(json.dumps(semantic, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    OUT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"candidate_id": CANDIDATE_ID, "report": str(OUT.relative_to(ROOT)), "digest": report["report_digest"], "requests": len(calls)}, sort_keys=True))


if __name__ == "__main__":
    main()
