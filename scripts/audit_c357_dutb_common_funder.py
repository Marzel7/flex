#!/usr/bin/env python3
"""Bounded, read-only verification of DuTb funding into C357 direct funders."""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UPSTREAM_AUDIT = ROOT / "docs/audits/c357_remaining_upstream_funders.v1.json"
OUT = ROOT / "docs/audits/c357_dutb_common_funder_rpc.v1.json"
DUTB = "DuTbZR8VJGsyLvkhcAyiByPwnPRJj1GTmB88ShgAezCX"
PAGES_PER_FUNDER = 20
PAGES_FOR_DUTB = 20
PAGE_SIZE = 1_000
MAX_TRANSACTION_LOOKUPS = 1_000
MAX_ROOT_TRANSACTION_LOOKUPS = 100
ROOT_HISTORY_OFFSET = int(os.environ.get("C357_DUTB_ROOT_HISTORY_OFFSET", "0"))


def rpc_url() -> str:
    match = re.search(
        r"^(?:export\s+)?(?:HELIUS_RPC_URL|SOLANA_RPC_URL)=[\"']?([^\"'\n]+)",
        (ROOT / ".env").read_text(),
        re.M,
    )
    if not match:
        raise RuntimeError("HELIUS_RPC_URL unavailable")
    return match.group(1)


def rpc(url: str, method: str, params: list, calls: list[str]):
    request = urllib.request.Request(
        url,
        data=json.dumps({"jsonrpc": "2.0", "id": len(calls) + 1, "method": method, "params": params}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        value = json.loads(response.read())
    calls.append(method)
    if value.get("error"):
        raise RuntimeError(value["error"])
    return value.get("result")


def history(url: str, address: str, pages: int, calls: list[str]) -> list[dict]:
    rows: list[dict] = []
    before = None
    for _ in range(pages):
        options = {"limit": PAGE_SIZE}
        if before:
            options["before"] = before
        page = rpc(url, "getSignaturesForAddress", [address, options], calls) or []
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            break
        before = page[-1]["signature"]
    return rows


def native_funding_events(value):
    if isinstance(value, dict):
        parsed = value.get("parsed")
        if isinstance(parsed, dict) and parsed.get("type") in {"transfer", "createAccount", "createAccountWithSeed", "closeAccount"}:
            info = parsed.get("info")
            if isinstance(info, dict):
                if parsed["type"] == "transfer":
                    yield {"kind": "transfer", **info}
                elif parsed["type"] == "closeAccount":
                    yield {"kind": "closeAccount", "source": info.get("owner"), "destination": info.get("destination"), "temporary_account": info.get("account")}
                else:
                    yield {"kind": "createAccount", "source": info.get("source"), "destination": info.get("newAccount"), "lamports": info.get("lamports")}
        for child in value.values():
            yield from native_funding_events(child)
    elif isinstance(value, list):
        for child in value:
            yield from native_funding_events(child)


def main() -> None:
    providers = json.loads(UPSTREAM_AUDIT.read_text())["providers"]
    known_funders = {"ByZc7RNeYowEg2jKo2giytWb9WmNyZPrQ1hXhnGSzHTY", "F5ZCNpw2xRcZNnuwYaFvNBb13Rzk3Pn4CnmSkyRsK229", "HS5GjB4KTJbbBdYHkJV8qDpq8gmU9wck2qsxgz3ifgke"}
    funders = sorted({provider["direct_funder"] for provider in providers} | known_funders)
    upstreams = sorted({item["address"] for provider in providers for item in provider.get("upstream_provisioners", [])})
    targets = sorted(set(funders) | set(upstreams))
    calls: list[str] = []
    url = rpc_url()
    started = time.monotonic()

    def fetch(address: str):
        return address, history(url, address, PAGES_FOR_DUTB if address == DUTB else PAGES_PER_FUNDER, calls)

    with ThreadPoolExecutor(max_workers=5) as pool:
        histories = dict(pool.map(fetch, [DUTB, *targets]))
    dutb_rows = histories[DUTB]
    dutb_sigs = {row["signature"]: row for row in dutb_rows}
    funder_sigs: dict[str, set[str]] = {}
    for target in targets:
        for row in histories[target]:
            if row["signature"] in dutb_sigs:
                funder_sigs.setdefault(row["signature"], set()).add(target)
    shared = sorted(funder_sigs)
    if len(shared) > MAX_TRANSACTION_LOOKUPS:
        raise RuntimeError("transaction lookup cap exceeded")
    # Preserve RPC newest-to-oldest order for this deterministic first batch.
    root_signatures = [row["signature"] for row in dutb_rows[ROOT_HISTORY_OFFSET:ROOT_HISTORY_OFFSET + MAX_ROOT_TRANSACTION_LOOKUPS]]
    lookup_signatures = list(dict.fromkeys([*root_signatures, *shared]))

    def fetch_transaction(signature: str):
        return signature, rpc(url, "getTransaction", [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}], calls)

    with ThreadPoolExecutor(max_workers=3) as pool:
        root_transactions = dict(pool.map(fetch_transaction, lookup_signatures))

    outbound, inbound, wsol_deliveries = [], [], []
    for signature in shared:
        tx = root_transactions[signature] or {}
        meta = tx.get("meta") or {}
        if meta.get("err"):
            continue
        events = list(native_funding_events(tx))
        temporary_funding = defaultdict(int)
        for item in events:
            source, destination = item.get("source"), item.get("destination")
            lamports = item.get("lamports")
            if source == DUTB and destination and item["kind"] in {"transfer", "createAccount"}:
                temporary_funding[destination] += lamports or 0
            if source == DUTB and destination in targets:
                outbound.append({"signature": signature, "block_time": tx.get("blockTime"), "destination": destination, "destination_role": "direct_funder" if destination in funders else "immediate_upstream", "lamports": lamports, "kind": item["kind"]})
            if destination == DUTB and source in targets:
                inbound.append({"signature": signature, "block_time": tx.get("blockTime"), "source": source, "source_role": "direct_funder" if source in funders else "immediate_upstream", "lamports": lamports, "kind": item["kind"]})
            if item["kind"] == "closeAccount" and source == DUTB and destination in funders:
                wsol_deliveries.append({"signature": signature, "block_time": tx.get("blockTime"), "destination": destination, "temporary_account": item.get("temporary_account"), "funding_lamports": temporary_funding.get(item.get("temporary_account")), "kind": "DuTb-owned WSOL close"})

    def summary(items: list[dict], counterpart_key: str) -> list[dict]:
        grouped: dict[str, list[dict]] = defaultdict(list)
        for item in items:
            grouped[item[counterpart_key]].append(item)
        return [
            {
                "wallet": wallet,
                "transfer_count": len(group),
                "lamports": sum(item.get("lamports") or 0 for item in group),
                "first_block_time": min((item.get("block_time") for item in group if item.get("block_time") is not None), default=None),
                "last_block_time": max((item.get("block_time") for item in group if item.get("block_time") is not None), default=None),
            }
            for wallet, group in sorted(grouped.items())
        ]

    root_inbound = []
    for signature in root_signatures:
        tx = root_transactions[signature] or {}
        if (tx.get("meta") or {}).get("err"):
            continue
        for item in native_funding_events(tx):
            if item.get("destination") == DUTB and item.get("source") != DUTB:
                root_inbound.append({"signature": signature, "block_time": tx.get("blockTime"), "source": item.get("source"), "lamports": item.get("lamports"), "kind": item["kind"]})

    result = {
        "schema_version": "C357_DUTB_COMMON_FUNDER_RPC.v1",
        "candidate_id": "p3r-v2-c357da9d0d4d560311e4",
        "root_wallet": DUTB,
        "scope": {
            "direct_funders": len(funders),
            "immediate_upstreams": len(upstreams),
            "root_history_pages": PAGES_FOR_DUTB,
            "root_history_offset": ROOT_HISTORY_OFFSET,
            "root_history_batch_size": MAX_ROOT_TRANSACTION_LOOKUPS,
            "direct_funder_history_pages_each": PAGES_PER_FUNDER,
            "page_size": PAGE_SIZE,
            "recursive_walkback": False,
            "read_only": True,
        },
        "coverage": {
            "root_history_entries": len(dutb_rows),
            "audited_wallet_history_entries": {target: len(histories[target]) for target in targets},
            "shared_signatures": len(shared),
            "root_history_transactions_inspected": len(root_transactions),
        },
        "dutb_to_audited_wallets": {"transfers": outbound, "by_wallet": summary(outbound, "destination")},
        "dutb_to_direct_funders_via_wsol_close": {"deliveries": wsol_deliveries, "by_funder": summary(wsol_deliveries, "destination")},
        "audited_wallets_to_dutb": {"transfers": inbound, "by_wallet": summary(inbound, "source")},
        "funding_into_dutb": {"event_count": len(root_inbound), "by_source": summary(root_inbound, "source")},
        "rpc": {"request_count": len(calls), "method_counts": {method: calls.count(method) for method in sorted(set(calls))}, "elapsed_seconds": round(time.monotonic() - started, 3)},
        "safety": {"source_db_writes": 0, "workflow_writes": 0, "provider_mutations": 0, "operation_membership_changed": False},
    }
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"funders": len(funders), "shared": len(shared), "outbound": len(outbound), "inbound": len(inbound), "calls": len(calls)}))


if __name__ == "__main__":
    main()
