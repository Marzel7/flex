#!/usr/bin/env python3
"""One-token, bounded validation harness for the frozen recovered Byzantine method.

This harness imports the recovered request builder and decoder unchanged.  It
never persists provider responses: only the compact normalized values the
frozen selection and containing-candle reduction consume are written.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = Path("/Users/kevinkeaveney/Dev/claude/flex")
SOURCE = SOURCE_ROOT / "scripts/run_byzantine_actual_entry_24h_backfill.py"
MINT = "Ah7xh8F2auwkZWh1KHDEjwuabdhKKJt2sCxqBH8mpump"
OUT = ROOT / "docs/fixtures/byzantine_actual_entry_v1/pepeinu.json"
AUDIT = ROOT / "docs/audits/byzantine_pepeinu_real_opening_validation.v1.json"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def frozen_module():
    spec = importlib.util.spec_from_file_location("frozen_byzantine_actual_entry", SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def retained_assignment() -> dict:
    ops = SOURCE_ROOT / "database/wt_ops_v2.db"
    core = SOURCE_ROOT / "database/flex_complete_database.db"
    with sqlite3.connect(ops) as db:
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT event_id,operator_id,mint,assigned_at FROM operator_launch_membership WHERE mint=? AND operator_id='d8ee4d7a-fcd6-5a5b-b897-24f6ab56e334' ORDER BY assigned_at DESC LIMIT 1", (MINT,)).fetchone()
    if not row:
        raise RuntimeError("PEPEINU_CANONICAL_ASSIGNMENT_NOT_FOUND")
    with sqlite3.connect(core) as db:
        db.row_factory = sqlite3.Row
        token = db.execute("SELECT mint,earliest_tx_creator,create_tx_signature,created_at FROM token_analysis WHERE mint=?", (MINT,)).fetchone()
    if not token or not token["create_tx_signature"] or not token["created_at"]:
        raise RuntimeError("PEPEINU_RETAINED_CREATE_COORDINATE_NOT_FOUND")
    return {"event_id": row["event_id"], "operator_id": row["operator_id"], "mint": row["mint"], "assignment_timestamp": row["assigned_at"], "creator": token["earliest_tx_creator"], "create_signature": token["create_tx_signature"], "retained_create_timestamp": token["created_at"]}


def choose(candidates: list[dict], create_slot: int) -> dict | None:
    return next((item for item in candidates if item["decoder_qualified"] and item["slot"] > create_slot), None)


def containing(points: list[dict], timestamp: int) -> dict | None:
    candidates = [point for point in points if point["timestamp"] <= timestamp < point["timestamp"] + 15]
    return candidates[-1] if candidates else None


def main() -> None:
    module = frozen_module()
    assignment = retained_assignment()
    calls = {"helius": 0, "birdeye": 0}
    rpc_url = module.rpc_client()
    create = module.rpc(rpc_url, "getTransaction", [assignment["create_signature"], {"encoding": "json", "maxSupportedTransactionVersion": 0}])
    calls["helius"] += 1
    keys = set(module.account_keys(create or {}))
    create_slot, create_time = (create or {}).get("slot"), (create or {}).get("blockTime")
    if assignment["mint"] not in keys or not isinstance(create_slot, int) or not isinstance(create_time, int):
        outcome = {"status": "FAIL_CLOSED_CANONICAL_CREATE_UNQUALIFIED"}
    else:
        page = module.rpc(rpc_url, "getSignaturesForAddress", [assignment["mint"], {"limit": 100}]) or []
        calls["helius"] += 1
        ordered = sorted((item for item in page if not item.get("err") and int(item.get("slot") or 0) > create_slot), key=lambda item: (int(item.get("slot") or 0), item.get("signature") or ""))[:8]
        normalized = []
        for rank, candidate in enumerate(ordered, 1):
            transaction = module.rpc(rpc_url, "getTransaction", [candidate["signature"], {"encoding": "json", "maxSupportedTransactionVersion": 0}])
            calls["helius"] += 1
            trade = module.classify_trade(transaction or {}, assignment["mint"])
            normalized.append({"signature": candidate["signature"], "slot": int(candidate["slot"]), "block_time": candidate.get("blockTime"), "selection_rank": rank, "post_create": int(candidate["slot"]) > create_slot, "decoder_qualified": bool(trade and trade.get("timestamp")), "venue": trade.get("venue") if trade else None, "event_type": trade.get("event_type") if trade else None, "timestamp": int(trade["timestamp"]) if trade and trade.get("timestamp") else None})
            if trade and trade.get("timestamp"):
                break
        selected = choose(normalized, create_slot)
        outcome = {"status": "FAIL_CLOSED_NO_QUALIFYING_TRADE", "candidates": normalized} if not selected else {"status": "ACTUAL_ENTRY_COORDINATE_QUALIFIED", "candidates": normalized, "selected": selected}
        if selected:
            key = module.secret("BIRDEYE")
            if not key:
                outcome = {**outcome, "status": "FAIL_CLOSED_BIRDEYE_BINDING_UNAVAILABLE"}
            else:
                result = module.ohlcv(key, assignment["mint"], selected["timestamp"] - 15, selected["timestamp"] + 15)
                calls["birdeye"] += 1
                points = [{"timestamp": point["timestamp"], "h": point["h"], "c": point["c"]} for point in result["points"]]
                candle = containing(points, selected["timestamp"])
                outcome.update({"birdeye_request": {"parameters": result["parameters"], "http_status": result["http_status"], "returned_count": result["returned_count"], "returned_first_timestamp": result["returned_first_timestamp"], "returned_last_timestamp": result["returned_last_timestamp"]}, "candles": points})
                if candle:
                    outcome.update({"status": "QUALIFIED", "selected_candle": candle, "entry_mc_usd": candle["c"]})
                else:
                    outcome["status"] = "FAIL_CLOSED_NO_CONTAINING_CANDLE"
    if calls["helius"] > 10 or calls["birdeye"] > 1:
        raise RuntimeError("FROZEN_CALL_BOUND_EXCEEDED")
    fixture = {"schema_version": "BYZANTINE_ACTUAL_ENTRY_V1_PEPEINU_FIXTURE_V1", "implementation_sha256": digest(SOURCE), "assignment": assignment, "canonical_create": {"slot": create_slot if isinstance(create_slot, int) else None, "timestamp": create_time if isinstance(create_time, int) else None}, "outcome": outcome, "calls": calls, "raw_provider_payloads_retained": False, "raw_blocks_retained": False}
    fixture["replay"] = {"selected": choose(list(outcome.get("candidates") or []), fixture["canonical_create"]["slot"]) if fixture["canonical_create"]["slot"] is not None else None, "candle": containing(list(outcome.get("candles") or []), int((outcome.get("selected") or {}).get("timestamp") or 0)) if outcome.get("selected") else None}
    fixture["replay_pass"] = outcome.get("status") == "QUALIFIED" and fixture["replay"]["selected"] == outcome.get("selected") and fixture["replay"]["candle"] == outcome.get("selected_candle")
    payload = json.dumps(fixture, indent=2, sort_keys=True) + "\n"
    if len(payload.encode()) >= 500000:
        raise RuntimeError("COMPACT_FIXTURE_BOUND_EXCEEDED")
    OUT.write_text(payload)
    AUDIT.write_text(json.dumps({"schema_version": "BYZANTINE_PEPEINU_REAL_OPENING_VALIDATION_V1", "implementation_sha256": fixture["implementation_sha256"], "assignment": assignment, "outcome": outcome, "calls": calls, "provider_free_replay_pass": fixture["replay_pass"], "raw_provider_payloads_retained": False, "raw_blocks_retained": False}, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": outcome["status"], "calls": calls, "replay_pass": fixture["replay_pass"]}, sort_keys=True))


if __name__ == "__main__":
    main()
