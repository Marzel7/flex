#!/usr/bin/env python3
"""Bounded read-only upstream check for P3R's launch-linked direct funders.

One signature-history page per distinct direct funder; one transaction read only
for each immediate ledger entry older than a P3R creator-funding transaction.
No recursive history traversal and no database writes.
"""
from __future__ import annotations

import collections
import hashlib
import json
import re
import sqlite3
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "database/wt_ops_v2.db"
OPERATOR = "777211c3-211e-551b-9310-ff9301570627"
OUT = ROOT / "docs/agent_handoff/p3r/p3r_operator_direct_funder_rpc"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rpc_url() -> str:
    match = re.search(r'^(?:export\s+)?(?:HELIUS_RPC_URL|SOLANA_RPC_URL)=["\']?([^"\'\n]+)', (ROOT / ".env").read_text(), re.M)
    if not match:
        raise RuntimeError("No RPC URL in .env")
    return match.group(1)


def call(url: str, method: str, params: list, calls: list[dict]) -> object:
    request = urllib.request.Request(url, data=json.dumps({"jsonrpc": "2.0", "id": len(calls) + 1, "method": method, "params": params}).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read())
    calls.append({"method": method, "subject": str(params[0]) if params else None})
    if payload.get("error"):
        raise RuntimeError(f"{method}: {payload['error']}")
    return payload.get("result")


def key(value: object) -> str | None:
    return value if isinstance(value, str) else value.get("pubkey") if isinstance(value, dict) else None


def inbound_summary(tx: dict | None, funder: str) -> dict:
    if not tx:
        return {"observable": False, "reason": "NULL_TRANSACTION"}
    keys = [key(item) for item in tx["transaction"]["message"]["accountKeys"]]
    if funder not in keys:
        return {"observable": False, "reason": "FUNDER_NOT_IN_ACCOUNT_KEYS"}
    index = keys.index(funder)
    meta = tx.get("meta") or {}
    pre, post = meta.get("preBalances") or [], meta.get("postBalances") or []
    if index >= len(pre) or index >= len(post):
        return {"observable": False, "reason": "MISSING_BALANCES"}
    delta = post[index] - pre[index]
    debits = [(a - b, keys[i]) for i, (a, b) in enumerate(zip(pre, post)) if a - b > 0]
    source = max(debits, default=(0, None))[1]
    return {"observable": True, "native_delta_lamports": delta, "upstream_source": source,
            "block_time": tx.get("blockTime"), "slot": tx.get("slot"),
            "programs": sorted({item.get("programId") or item.get("program") or "UNKNOWN" for item in tx["transaction"]["message"].get("instructions") or [] if isinstance(item, dict)})}


def main() -> None:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True); conn.row_factory = sqlite3.Row
    try:
        launches = [dict(row) for row in conn.execute(
            "SELECT m.mint,q.creator,q.funder_wallet,q.funder_sig,q.funder_block_time,q.funding_mechanism "
            "FROM operator_launch_membership m JOIN wt_walkback_queue q ON q.mint=m.mint "
            "WHERE m.operator_id=? AND q.funder_wallet IS NOT NULL AND q.funder_sig IS NOT NULL ORDER BY m.mint", (OPERATOR,))]
    finally:
        conn.close()
    funders = sorted({row["funder_wallet"] for row in launches})
    calls: list[dict] = []; url = rpc_url(); histories = {}
    for funder in funders:
        histories[funder] = call(url, "getSignaturesForAddress", [funder, {"limit": 1000, "commitment": "confirmed"}], calls) or []
    indexes = {funder: {row["signature"]: i for i, row in enumerate(rows)} for funder, rows in histories.items()}
    candidates = {}; links = []
    for launch in launches:
        history = histories[launch["funder_wallet"]]; position = indexes[launch["funder_wallet"]].get(launch["funder_sig"])
        prior = history[position + 1] if position is not None and position + 1 < len(history) else None
        candidate_sig = prior.get("signature") if prior else None
        if candidate_sig:
            candidates[(launch["funder_wallet"], candidate_sig)] = prior
        links.append({**launch, "history_position": position, "immediate_prior_signature": candidate_sig,
                      "immediate_prior_block_time": prior.get("blockTime") if prior else None})
    transactions = {(funder, signature): inbound_summary(call(url, "getTransaction", [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}], calls), funder)
                    for (funder, signature) in sorted(candidates)}
    for link in links:
        info = transactions.get((link["funder_wallet"], link["immediate_prior_signature"]), {}) if link["immediate_prior_signature"] else {}
        link.update({"upstream_observable": info.get("observable", False), "upstream_source": info.get("upstream_source"),
                     "upstream_amount_lamports": info.get("native_delta_lamports"), "upstream_block_time": info.get("block_time"),
                     "qualifying_material_inbound": bool(info.get("native_delta_lamports", 0) > 0), "upstream_programs": info.get("programs", [])})
    by_funder = []
    for funder in funders:
        group = [row for row in links if row["funder_wallet"] == funder]
        material = [row for row in group if row["qualifying_material_inbound"]]
        by_funder.append({"direct_funder": funder, "launch_links": len(group), "history_entries": len(histories[funder]),
                          "material_inbound_links": len(material),
                          "upstream_sources": collections.Counter(row["upstream_source"] for row in material if row["upstream_source"]),
                          "inbound_amounts": collections.Counter(row["upstream_amount_lamports"] for row in material)})
    source_counts = collections.Counter(row["upstream_source"] for row in links if row["qualifying_material_inbound"] and row["upstream_source"])
    payload = {"schema_version": "P3R_OPERATOR_DIRECT_FUNDER_UPSTREAM_RPC.v1", "operator_id": OPERATOR,
               "scope": "78 launch links, deduplicated to 53 direct-funder address histories", "selection_rule": "immediate ledger entry older than the selected creator-funding signature; accept only positive native balance delta into direct funder", "rpc_manifest": {"calls": calls, "getSignaturesForAddress_calls": len(funders), "getTransaction_calls": len(transactions), "recursive_calls": 0},
               "launch_links": links, "direct_funders": by_funder, "summary": {"launch_links": len(links), "distinct_direct_funders": len(funders), "material_inbound_links": sum(row["qualifying_material_inbound"] for row in links), "unresolved_links": sum(not row["qualifying_material_inbound"] for row in links), "recurrent_upstream_sources": [{"address": address, "links": count} for address, count in source_counts.most_common()]},
               "safety": {"database_writes": False, "ui_changes": False, "queue_replay": False, "operation_mutation": False}}
    payload["result_digest"] = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    OUT.mkdir(parents=True, exist_ok=True)
    report = OUT / "p3r_operator_direct_funder_upstream_rpc.v1.json"; report.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    manifest = OUT / "p3r_operator_direct_funder_upstream_rpc_manifest.v1.json"; manifest.write_text(json.dumps({"report": str(report.relative_to(ROOT)), "report_sha256": sha(report), "result_digest": payload["result_digest"], "rpc_calls": len(calls), "methods": dict(collections.Counter(row["method"] for row in calls)), "selection_rule": payload["selection_rule"]}, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"report": str(report), "report_sha256": sha(report), "manifest": str(manifest), "manifest_sha256": sha(manifest), "summary": payload["summary"], "rpc_calls": len(calls)}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
