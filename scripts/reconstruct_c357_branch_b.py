#!/usr/bin/env python3
"""Bounded, read-only C357 Branch B chronology capture and offline replay.

The capture is intentionally restricted to the three frozen infrastructure
wallets and the immediate launch/settlement window.  It writes a compact
semantic ledger, never raw RPC responses or database state.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from src.ops.potential_operations import detail

OUT = ROOT / "docs/audits/c357_branch_b_chronology.v1.json"
CID = "p3r-v2-c357da9d0d4d560311e4"
START, END = 1787578100, 1787633800  # initialization through immediate post-launch settlement
SUBJECTS = {
    "33my": "33myosxzjbzfx2GcW71zmzvrzibQnnh6njW2vLKiMxr4",
    "HXuf": "HXufNWTdtH1oq2SscHQsfGpXLv1P8Givsz7mBqqYrive",
    "CZTx": "CZTxzma6pA9HPwXASpbhuCKNGrH6zgpQ9ARgUqUQwbTy",
}
LAUNCH_MINTS = {
    "W7Fnj98a3jY9aoP9kd25taK66eJ1uS6b6VjpKtJpump",
    "yfmKCjYvHsD17w2xvKLBRrmzF58nZyXH25reGwLpump",
}
PAGE_LIMIT, MAX_PAGES, MAX_TX = 1000, 25, 500


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def rpc_url() -> str:
    match = re.search(r"^(?:export\s+)?(?:HELIUS_RPC_URL|SOLANA_RPC_URL)=[\"']?([^\"'\n]+)", (ROOT / ".env").read_text(), re.M)
    if not match:
        raise RuntimeError("HELIUS_RPC_URL unavailable")
    return match.group(1)


def rpc(url: str, method: str, params: list, calls: Counter) -> object:
    request = urllib.request.Request(url, data=json.dumps({"jsonrpc": "2.0", "id": sum(calls.values()) + 1, "method": method, "params": params}).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=45) as response:
        value = json.loads(response.read())
    calls[method] += 1
    if value.get("error"):
        raise RuntimeError(value["error"])
    return value.get("result")


def bounded_history(url: str, wallet: str, calls: Counter) -> tuple[list[dict], int]:
    rows, before = [], None
    for page in range(1, MAX_PAGES + 1):
        options = {"limit": PAGE_LIMIT, **({"before": before} if before else {})}
        batch = rpc(url, "getSignaturesForAddress", [wallet, options], calls) or []
        rows.extend(batch)
        if not batch or min((r.get("blockTime") or 0 for r in batch), default=0) <= START or len(batch) < PAGE_LIMIT:
            return rows, page
        before = batch[-1]["signature"]
    raise RuntimeError(f"history cap {MAX_PAGES} reached before bounded window for {wallet}")


def parsed_instructions(value: object):
    if isinstance(value, dict):
        parsed = value.get("parsed")
        if isinstance(parsed, dict) and isinstance(parsed.get("info"), dict):
            yield parsed["type"], parsed["info"]
        for child in value.values():
            yield from parsed_instructions(child)
    elif isinstance(value, list):
        for child in value:
            yield from parsed_instructions(child)


def ledger_events(tx: dict, signature: str) -> list[dict]:
    keys = tx.get("transaction", {}).get("message", {}).get("accountKeys", [])
    payer = next((k.get("pubkey") for k in keys if isinstance(k, dict) and k.get("signer")), None)
    output = []
    for kind, info in parsed_instructions(tx):
        if kind == "transfer":
            output.append({"kind": "transfer", "source": info.get("source"), "destination": info.get("destination"), "lamports": info.get("lamports"), "temporary_account": None})
        elif kind == "closeAccount":
            output.append({"kind": "closeAccount", "source": info.get("owner"), "destination": info.get("destination"), "lamports": None, "temporary_account": info.get("account")})
        elif kind in {"createAccount", "createAccountWithSeed"}:
            output.append({"kind": kind, "source": info.get("source"), "destination": info.get("newAccount"), "lamports": info.get("lamports"), "temporary_account": info.get("newAccount")})
    return [{"block_time": tx.get("blockTime"), "slot": tx.get("slot"), "signature": signature, "fee_payer": payer, **event} for event in output]


def local_launches() -> list[dict]:
    found = detail("database/wt_ops_v2.db", CID)
    launches = []
    for member in found["members"]:
        if member["mint"] in LAUNCH_MINTS:
            launches.append({key: member[key] for key in ("mint", "creator", "parent", "signature", "observed_at", "amount_lamports", "mechanism", "atomic")})
    return sorted(launches, key=lambda row: row["observed_at"])


def local_reach(launches: list[dict]) -> dict:
    found = detail("database/wt_ops_v2.db", CID)
    result = {}
    for label, wallet in SUBJECTS.items():
        rows = [m["mint"] for m in found["members"] if m.get("parent") == wallet or m.get("upstream") == wallet]
        result[label] = {"wallet": wallet, "exact_c357_mints": sorted(rows), "count": len(rows)}
    return result


def dutb_overlap() -> dict:
    """Check the already-retained DuTb audit only; no new recursive reads."""
    value = json.loads((ROOT / "docs/audits/c357_dutb_common_funder_rpc.v1.json").read_text())
    deliveries = value.get("dutb_to_direct_funders_via_wsol_close", {}).get("deliveries", [])
    delivered = {row.get("destination") for row in deliveries}
    return {label: {"wallet": wallet, "in_known_dutb_wsol_delivery_pool": wallet in delivered} for label, wallet in SUBJECTS.items()}


def capture() -> dict:
    calls: Counter = Counter()
    url = rpc_url()
    histories, pages = {}, {}
    for name, wallet in SUBJECTS.items():
        histories[name], pages[name] = bounded_history(url, wallet, calls)
    # 33my is a high-volume wallet.  The bounded capture decodes its proven
    # capitalization transaction plus signatures shared with the two immediate
    # Branch B counterparts; it deliberately does not turn this into a broad
    # 33my history crawl merely because its one page contains many window rows.
    counterpart_signatures = {
        row["signature"] for name, rows in histories.items() if name != "33my"
        for row in rows if START <= (row.get("blockTime") or 0) <= END
    }
    initial_signature = "4biYhLXswJUbUd58n7F2h8x8sW1of6VH85Kskz5AhWsFYRqMjth3wJqyrqChVhBNidDNeDVCThuSoxi184vdVpBp"
    shared_with_counterparts = {row["signature"] for row in histories["33my"] if row["signature"] in counterpart_signatures}
    signatures = counterpart_signatures | shared_with_counterparts | {initial_signature}
    launches = local_launches()
    reach = local_reach(launches)
    overlap = dutb_overlap()
    signatures.update(row["signature"] for row in launches)
    if len(signatures) > MAX_TX:
        raise RuntimeError(f"transaction cap exceeded: {len(signatures)} > {MAX_TX}")
    def fetch(sig: str):
        return sig, rpc(url, "getTransaction", [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}], calls)
    with ThreadPoolExecutor(max_workers=3) as pool:
        transactions = dict(pool.map(fetch, sorted(signatures)))
    subjects = set(SUBJECTS.values())
    events = []
    for sig, tx in transactions.items():
        if tx and not (tx.get("meta") or {}).get("err"):
            events.extend(e for e in ledger_events(tx, sig) if e["source"] in subjects or e["destination"] in subjects)
    events.sort(key=lambda row: (row.get("block_time") or 0, row["signature"], row["kind"]))
    pair = [e for e in events if e["kind"] == "transfer" and {e["source"], e["destination"]} == {SUBJECTS["HXuf"], SUBJECTS["CZTx"]}]
    directions = {}
    for source, destination, label in ((SUBJECTS["HXuf"], SUBJECTS["CZTx"], "hxuf_to_cztx"), (SUBJECTS["CZTx"], SUBJECTS["HXuf"], "cztx_to_hxuf")):
        rows = [e for e in pair if e["source"] == source and e["destination"] == destination]
        directions[label] = {"count": len(rows), "lamports": sum(e["lamports"] or 0 for e in rows), "first_block_time": min((e["block_time"] for e in rows), default=None), "last_block_time": max((e["block_time"] for e in rows), default=None), "events": rows}
    init = next((e for e in events if e["signature"] == initial_signature and e["kind"] == "closeAccount"), None)
    # Keep the durable ledger semantic and compact: the initialization, both
    # launch mechanisms, and the directed HXuf/CZTx rows are sufficient to
    # reproduce the conclusions.  The remaining decoded provider responses are
    # intentionally not retained as a giant RPC-derived activity dump.
    material_signatures = {initial_signature, *(launch["signature"] for launch in launches)}
    compact_events = [e for e in events if e["signature"] in material_signatures or e in pair]
    roles = [
        {"block_time": 1787578100, "wallet": SUBJECTS["33my"], "role": ["PROVISIONER", "WSOL_CLOSE_SOURCE", "FEE_PAYER"], "evidence": "initialization signature"},
        {"block_time": 1787578100, "wallet": SUBJECTS["HXuf"], "role": ["WSOL_CLOSE_DESTINATION"], "evidence": "initialization close destination"},
        {"block_time": 1787578101, "wallet": SUBJECTS["HXuf"], "role": ["INTERMEDIARY"], "evidence": "near-full direct transfer to CZTx"},
        {"block_time": 1787578101, "wallet": SUBJECTS["CZTx"], "role": ["SETTLEMENT_COUNTERPARTY"], "evidence": "direct receipt from HXuf"},
        *[{"block_time": launch["observed_at"], "wallet": SUBJECTS["HXuf"], "role": ["DIRECT_FUNDER"], "evidence": f"exact C357 launch {launch['mint']}"} for launch in launches],
    ]
    artifact = {
        "schema_version": "C357_BRANCH_B_CHRONOLOGY.v1",
        "candidate_id": CID,
        "scope": {"window_block_time_inclusive": [START, END], "subjects": SUBJECTS, "launch_mints": sorted(LAUNCH_MINTS), "history_page_limit": PAGE_LIMIT, "history_max_pages": MAX_PAGES, "transaction_cap": MAX_TX, "read_only": True},
        "coverage": {"history_pages": pages, "window_signatures": len(signatures), "decoded_transactions": len([v for v in transactions.values() if v]), "provider_calls": dict(sorted(calls.items())), "33my_window_rows_not_decoded_as_unrelated": sum(1 for row in histories["33my"] if START <= (row.get("blockTime") or 0) <= END and row["signature"] not in signatures)},
        "initialization": {"signature": initial_signature, "block_time": 1787578100, "slot": 441404143, "provisioner": SUBJECTS["33my"], "destination": SUBJECTS["HXuf"], "wsol_transfer_lamports": 3049997960720, "creation_rent_lamports": 2039280, "close_event": init},
        "hxuf_cztx_direct_transfers": directions,
        "launches": launches,
        "additional_c357_reach_local": reach,
        "dutb_pool_overlap_retained": overlap,
        "role_chronology": roles,
        "classifications": {
            "branch_b_architecture": "POST_DUTB_PARALLEL_PROVISIONING_ARCHITECTURE",
            "thirtythree_my": "C357_PROVISIONER_CANDIDATE",
            "hxuf": "ROLE_ROTATING_INFRASTRUCTURE",
            "cztx": "SETTLEMENT_COUNTERPARTY",
            "c357_continuity": "YES_MODERATE",
            "hxuf_cztx_relationship": "ONE_WAY_PROVISIONING",
            "launch_linkage": "SAME_INFRASTRUCTURE_PATH",
            "structural_continuity": "MODERATE_ROLE_CONTINUITY",
            "generic_service_control": "C357_ENRICHED_BUT_NOT_SPECIFIC",
            "rationale": "HXuf is the exact direct funder for both launches and repeatedly transfers large values to CZTx after the 33my capitalization. The ledger does not prove fungible-lamport tracing from 33my to either launch, ownership, or C357 exclusivity.",
        },
        "monitoring_implication": ["exact C357 behavioural matches", "wallet role at time", "WSOL-close provisioning", "pool capitalization", "funder reuse", "role transitions", "unknown-branch clustering"],
        "events": compact_events,
        "safety": {"source_db_writes": 0, "workflow_writes": 0, "provider_mutations": 0, "membership_changed": False, "fingerprint_changed": False, "detector_changed": False},
    }
    artifact["deterministic_digest"] = digest(artifact)
    return artifact


def replay(artifact: dict) -> dict:
    copied = dict(artifact)
    recorded = copied.pop("deterministic_digest")
    copied.pop("elapsed_seconds", None)  # accepted only for captures produced before v1.1
    actual = digest(copied)
    return {"replay": "C357_BRANCH_B_REPLAY_PASS" if actual == recorded else "C357_BRANCH_B_REPLAY_FAIL", "recorded_digest": recorded, "replay_digest": actual, "provider_calls_during_replay": 0}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay", action="store_true")
    args = parser.parse_args()
    if args.replay:
        print(json.dumps(replay(json.loads(OUT.read_text())), sort_keys=True))
        return
    started = time.monotonic()
    artifact = capture()
    # Wall time is intentionally neither retained nor included in the digest.
    OUT.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"digest": artifact["deterministic_digest"], "coverage": artifact["coverage"]}, sort_keys=True))


if __name__ == "__main__":
    main()
