#!/usr/bin/env python3
"""Bounded, shadow-only creator role-window discrimination census.

The network boundary is deliberately fixed: one history page per frozen
creator followed by transactions immediately adjacent to the frozen launch.
It never follows accounts, mutates registry state, or derives detector input.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
import json
import re
import sqlite3
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs/audits/c357_creator_window_shadow_manifest.v1.json"
CAPTURE = ROOT / "docs/audits/c357_creator_window_shadow_capture.v1.json"
DECODE = ROOT / "docs/audits/c357_creator_window_shadow_decode.v1.json"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def digest(value):
    return sha256(canonical(value).encode()).hexdigest()


def rpc_url():
    text = (ROOT / ".env").read_text()
    match = re.search(r"^(?:export\s+)?(?:HELIUS_RPC_URL|SOLANA_RPC_URL)=[\"']?([^\"'\n]+)", text, re.M)
    if not match:
        raise RuntimeError("RPC_URL_NOT_CONFIGURED")
    return match.group(1)


def rpc(method, params, url):
    request = urllib.request.Request(
        url,
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        payload = json.loads(response.read())
    if payload.get("error"):
        raise RuntimeError(payload["error"].get("message", "RPC_ERROR"))
    return payload.get("result")


def history(args):
    creator, url = args
    try:
        return creator, rpc("getSignaturesForAddress", [creator, {"limit": 1000}], url), None
    except Exception as exc:
        return creator, [], type(exc).__name__


def transaction(args):
    signature, url = args
    try:
        return signature, rpc("getTransaction", [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}], url), None
    except Exception as exc:
        return signature, None, type(exc).__name__


def materialize_manifest():
    db = sqlite3.connect(ROOT / "database/wt_ops_v2.db")
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "select e.mint,e.wallet as creator,e.block_time,e.signature "
        "from wt_walkback_edge_candidates e join wt_walkback_atomic_flows a on a.signature=e.signature "
        "where e.selection_status='SELECTED' and e.amount_lamports=99999985000 "
        "and a.transfer_lamports=99997955720 and a.has_create=1 and a.has_sync_native=1 and a.has_close=1 "
        "order by e.mint"
    ).fetchall()
    db.close()
    value = {"schema_version": "C357_CREATOR_WINDOW_SHADOW_MANIFEST.v1", "population": len(rows), "window_contract": "one getSignaturesForAddress page per frozen creator; no recursive follow-up", "rows": [dict(row) for row in rows], "safety": {"shadow_only": True, "provider_calls": 0, "membership_mutation": False}}
    MANIFEST.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    return value


def load_json(path):
    return json.loads(path.read_text())


def adjacent_signatures(manifest, capture):
    selected, positions = set(), {}
    histories = capture["history_metadata"]
    for row in manifest["rows"]:
        page = histories.get(row["creator"], [])
        index = next((i for i, item in enumerate(page) if item["signature"] == row["signature"]), None)
        positions[row["signature"]] = index
        if index is not None:
            selected.update(item["signature"] for item in page[max(0, index - 1): index + 2])
    return sorted(selected), positions


def account_keys(tx):
    return tx.get("transaction", {}).get("message", {}).get("accountKeys", []) if tx else []


def fee_payer(tx):
    keys = account_keys(tx)
    for key in keys:
        if isinstance(key, dict) and key.get("signer"):
            return key.get("pubkey")
    return keys[0].get("pubkey") if keys and isinstance(keys[0], dict) else None


def instructions(tx):
    message = tx.get("transaction", {}).get("message", {}) if tx else {}
    outer = message.get("instructions", [])
    inner = tx.get("meta", {}).get("innerInstructions", []) if tx else []
    return outer + [item for group in inner for item in group.get("instructions", [])]


def activity(tx, creator):
    result = {"inbound_lamports": 0, "outbound_lamports": 0, "inbound_transfers": 0, "outbound_transfers": 0, "wsol_initialize_owner": False, "wsol_close_owner": False, "wsol_close_destination": False}
    for instruction in instructions(tx):
        parsed = instruction.get("parsed") if isinstance(instruction, dict) else None
        info = parsed.get("info", {}) if isinstance(parsed, dict) else {}
        typ = parsed.get("type") if isinstance(parsed, dict) else ""
        if typ in {"transfer", "transferChecked"}:
            amount = info.get("lamports")
            if amount is None:
                amount = info.get("tokenAmount", {}).get("amount", 0)
            try:
                amount = int(amount)
            except (TypeError, ValueError):
                amount = 0
            if info.get("destination") == creator:
                result["inbound_lamports"] += amount
                result["inbound_transfers"] += 1
            if info.get("source") == creator:
                result["outbound_lamports"] += amount
                result["outbound_transfers"] += 1
        if typ in {"initializeAccount", "initializeAccount2", "initializeAccount3"} and info.get("owner") == creator:
            result["wsol_initialize_owner"] = True
        if typ == "closeAccount":
            if info.get("owner") == creator:
                result["wsol_close_owner"] = True
            if info.get("destination") == creator:
                result["wsol_close_destination"] = True
    return result


def labels_by_mint():
    clusters = load_json(ROOT / "docs/audits/c357_exact_compatible_attribution_clusters.v1.json")
    return {
        mint for cluster in clusters["clusters"]
        if cluster["attribution"] == "C357_BASELINE_CLUSTER"
        for mint in cluster["mints"]
    }


def summarize(rows, key):
    subset = [row for row in rows if row["comparison_metadata"] == key]
    count = len(subset) or 1
    return {"population": len(subset), "launch_creator_fee_payer": sum(row["launch_creator_fee_payer"] is True for row in subset), "pre_direct_inbound_lamports": sum(row["pre_window"]["inbound_lamports"] for row in subset), "post_direct_outbound_lamports": sum(row["post_window"]["outbound_lamports"] for row in subset), "pre_any_direct_inbound_rate": round(sum(row["pre_window"]["inbound_transfers"] > 0 for row in subset) / count, 6), "post_any_direct_outbound_rate": round(sum(row["post_window"]["outbound_transfers"] > 0 for row in subset) / count, 6), "launch_wsol_owner_rate": round(sum(row["launch_window"]["wsol_initialize_owner"] or row["launch_window"]["wsol_close_owner"] for row in subset) / count, 6)}


def decode():
    manifest, capture = load_json(MANIFEST), load_json(CAPTURE)
    signatures, positions = adjacent_signatures(manifest, capture)
    with ThreadPoolExecutor(max_workers=4) as pool:
        fetched = list(pool.map(transaction, [(signature, rpc_url()) for signature in signatures]))
    txs = {signature: tx for signature, tx, error in fetched if tx}
    errors = {signature: error for signature, tx, error in fetched if error}
    known, decoded, histories = labels_by_mint(), [], capture["history_metadata"]
    for row in manifest["rows"]:
        page, index = histories.get(row["creator"], []), positions[row["signature"]]
        before = page[index + 1] if index is not None and index + 1 < len(page) else None
        after = page[index - 1] if index is not None and index > 0 else None
        launch = txs.get(row["signature"])
        decoded.append({"mint": row["mint"], "creator": row["creator"], "launch_signature": row["signature"], "launch_block_time": row["block_time"], "comparison_metadata": "C357_BASELINE" if row["mint"] in known else "EXACT_COMPATIBLE_COLLISION", "launch_position_found": index is not None, "launch_fee_payer": fee_payer(launch), "launch_creator_fee_payer": fee_payer(launch) == row["creator"] if launch else None, "pre_signature": before["signature"] if before else None, "post_signature": after["signature"] if after else None, "launch_window": activity(launch, row["creator"]), "pre_window": activity(txs.get(before["signature"]), row["creator"]) if before else activity(None, row["creator"]), "post_window": activity(txs.get(after["signature"]), row["creator"]) if after else activity(None, row["creator"])})
    result = {"schema_version": "C357_CREATOR_WINDOW_SHADOW_DECODE.v1", "mode": "SHADOW_ONLY", "source_manifest_sha256": sha256(MANIFEST.read_bytes()).hexdigest(), "source_capture_sha256": sha256(CAPTURE.read_bytes()).hexdigest(), "population": len(decoded), "fixed_adjacent_transaction_budget": len(signatures), "provider_calls": len(signatures), "transaction_errors": errors, "launch_position_coverage": sum(row["launch_position_found"] for row in decoded), "rows": decoded, "comparison_only": {key: summarize(decoded, key) for key in ("C357_BASELINE", "EXACT_COMPATIBLE_COLLISION")}, "interpretation": "Comparison metadata is audit-only and is not a detector feature. This bounded direct-transfer/role window cannot establish common control or causal attribution.", "safety": {"membership_mutation": False, "fingerprint_change": False, "queue_change": False, "production_change": False, "recursive_follow_up": False}}
    result["artifact_sha256"] = digest(result)
    DECODE.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"population": len(decoded), "transactions": len(signatures), "errors": len(errors), "coverage": result["launch_position_coverage"]}))


def replay():
    value = load_json(DECODE)
    observed = value.pop("artifact_sha256")
    assert observed == digest(value), "DECODE_DIGEST_MISMATCH"
    assert value["provider_calls"] == value["fixed_adjacent_transaction_budget"]
    assert value["population"] == 161
    assert value["safety"]["membership_mutation"] is False
    print("C357_CREATOR_WINDOW_SHADOW_REPLAY_PASS provider_calls_during_replay=0")


if __name__ == "__main__":
    if "--capture" in sys.argv:
        manifest = materialize_manifest()
        creators = sorted({row["creator"] for row in manifest["rows"]})
        with ThreadPoolExecutor(max_workers=4) as pool:
            got = list(pool.map(history, [(creator, rpc_url()) for creator in creators]))
        result = {"schema_version": "C357_CREATOR_WINDOW_SHADOW_CAPTURE.v1", "manifest_population": len(manifest["rows"]), "creator_count": len(creators), "history_pages": {c: len(h) for c, h, e in got}, "history_metadata": {c: [{"signature": r.get("signature"), "block_time": r.get("blockTime")} for r in h] for c, h, e in got}, "errors": {c: e for c, h, e in got if e}, "provider_calls": len(creators), "safety": manifest["safety"]}
        CAPTURE.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        print(json.dumps({"captured": len(creators), "errors": len(result["errors"])}))
    elif "--decode" in sys.argv:
        decode()
    elif "--replay" in sys.argv:
        replay()
    else:
        print(materialize_manifest()["population"])
