"""Bounded DEV-only Byzantine actual-entry and short-MCAP-lifecycle acquisition.

The runner deliberately retains summaries only.  It has no database writes and
never turns a lifecycle observation into a terminal/ended conclusion.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import time
import argparse
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "database/wt_ops_v2.db"
CORE = ROOT / "database/flex_complete_database.db"
OUT = ROOT / "docs/audits/byzantine_actual_entry_24h_ath_backfill.v1.json"
URL = "https://public-api.birdeye.so/defi/v3/ohlcv"
PUMP_FUN = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMP_SWAP = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
HORIZONS = (("2H", 7200), ("6H", 21600), ("12H", 43200), ("24H", 86400))


def secret(name: str) -> str | None:
    """Read an existing local binding without emitting it or retaining it."""
    if os.environ.get(name):
        return os.environ[name]
    path = ROOT / ".env"
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(rf"^\s*(?:export\s+)?{re.escape(name)}\s*=\s*(.*?)\s*$", line)
        if match:
            return match.group(1).split("#", 1)[0].strip().strip("\"'") or None
    return None


def supervisor_secret(name: str) -> str | None:
    path = ROOT / "config/supervisor/supervisord.conf"
    if not path.is_file():
        return None
    match = re.search(rf"(?:^|[\s,=]){re.escape(name)}\s*=\s*(?:\"([^\"]+)\"|'([^']+)'|([^,\s#]+))", path.read_text(encoding="utf-8"), re.M)
    return next((value.strip() for value in match.groups() if value is not None), None) if match else None


def epoch(value: str) -> int:
    return int(dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def frozen_population():
    import sqlite3
    with sqlite3.connect(OPS) as db:
        db.row_factory = sqlite3.Row
        facts = [dict(row) for row in db.execute(
            "SELECT mint,monitor_state,assignment_timestamp,entry_method FROM operation_monitor_facts "
            "WHERE operation_id='byzantine' ORDER BY assignment_timestamp")]
    with sqlite3.connect(CORE) as db:
        db.row_factory = sqlite3.Row
        metadata = {row["mint"]: dict(row) for row in db.execute(
            "SELECT a.mint,t.symbol,a.created_at,a.create_tx_signature FROM token_analysis a "
            "LEFT JOIN tracked_tokens t ON t.mint=a.mint WHERE a.mint IN (%s)" % ",".join("?" * len(facts)), [x["mint"] for x in facts])}
    rows = []
    for fact in facts:
        info = metadata.get(fact["mint"], {})
        rows.append({**fact, "ticker": info.get("symbol"),
                     "canonical_create_signature": info.get("create_tx_signature"),
                     "canonical_create_timestamp": epoch(info["created_at"]) if info.get("created_at") else None})
    return rows


def rpc_client():
    key = supervisor_secret("HELIUS_API_KEY")
    if not key:
        raise RuntimeError("HELIUS_BINDING_UNAVAILABLE")
    return "https://mainnet.helius-rpc.com/?api-key=" + key


def rpc(url, method, params):
    response = requests.post(url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=45)
    if response.status_code != 200:
        return None
    return response.json().get("result")


def account_keys(tx):
    return [x if isinstance(x, str) else x.get("pubkey") for x in (((tx.get("transaction") or {}).get("message") or {}).get("accountKeys") or [])]


def classify_trade(tx, mint):
    logs = " ".join((tx.get("meta") or {}).get("logMessages") or [])
    if "Instruction: Buy" not in logs and "Instruction: Sell" not in logs:
        return None
    keys = set(account_keys(tx))
    if PUMP_SWAP in keys or PUMP_SWAP in logs:
        venue = "PUMPSWAP"
    elif PUMP_FUN in keys or PUMP_FUN in logs:
        venue = "PUMP_FUN"
    else:
        return None
    return {"venue": venue, "event_type": "BUY" if "Instruction: Buy" in logs else "SELL", "timestamp": tx.get("blockTime")}


def ohlcv(key, mint, start, end):
    params = {"address": mint, "type": "15s", "chart_type": "mcap", "currency": "usd", "mode": "range", "padding": "false", "time_from": start, "time_to": end}
    response = requests.get(URL, headers={"X-API-KEY": key, "x-chain": "solana", "accept": "application/json"}, params=params, timeout=45)
    raw = ((response.json().get("data") or {}).get("items") or []) if response.status_code == 200 else []
    points = []
    for item in raw:
        try:
            points.append({"timestamp": int(item.get("unix_time", item.get("unixTime"))), "h": float(item["h"]), "c": float(item["c"])})
        except (KeyError, TypeError, ValueError):
            continue
    points.sort(key=lambda x: x["timestamp"])
    return {"parameters": params, "http_status": response.status_code, "points": points,
            "returned_first_timestamp": points[0]["timestamp"] if points else None,
            "returned_last_timestamp": points[-1]["timestamp"] if points else None,
            "returned_count": len(points)}


def compact_call(kind, mint, result):
    return {"kind": kind, "mint": mint, "parameters": result["parameters"], "http_status": result["http_status"],
            "returned_first_timestamp": result["returned_first_timestamp"], "returned_last_timestamp": result["returned_last_timestamp"], "returned_count": result["returned_count"]}


def recover_create_slots():
    """Execute the separately authorized one-known-signature lookup per target.

    The previous run retained page coverage/count metadata, not the page's raw
    signature list, so this function deliberately does not resume candidate
    discovery or issue any address-history request.
    """
    population = frozen_population()
    waiting = [x for x in population if x["monitor_state"] == "WAITING_FOR_ENTRY_REFERENCE"]
    if len(population) != 12 or len(waiting) != 11:
        raise RuntimeError("BYZANTINE_UI_POPULATION_NOT_EXPECTED_12")
    url = rpc_client()
    recovered = []
    for target in waiting:
        tx = rpc(url, "getTransaction", [target["canonical_create_signature"], {"encoding": "json", "maxSupportedTransactionVersion": 0}])
        keys = set(account_keys(tx or {}))
        slot = (tx or {}).get("slot")
        timestamp = (tx or {}).get("blockTime")
        qualified = target["mint"] in keys and isinstance(slot, int) and slot > 0 and isinstance(timestamp, int) and timestamp > 0
        recovered.append({"mint": target["mint"], "ticker": target["ticker"], "canonical_create_signature": target["canonical_create_signature"],
                          "canonical_create_slot": slot if qualified else None, "canonical_create_timestamp": timestamp if qualified else None,
                          "retained_create_timestamp": target["canonical_create_timestamp"], "signature_identity_matches_mint": target["mint"] in keys,
                          "status": "QUALIFIED_CANONICAL_CREATE" if qualified else "CANONICAL_CREATE_LOOKUP_FAILED_CLOSED"})
        time.sleep(0.25)
    if not OUT.exists():
        raise RuntimeError("PRIOR_COMPACT_BACKFILL_AUDIT_REQUIRED")
    artifact = json.loads(OUT.read_text())
    artifact["create_slot_recovery"] = {"method": "ONE_KNOWN_CANONICAL_CREATE_SIGNATURE_GETTRANSACTION_PER_WAITING_MINT", "previous_signature_discovery_calls": 11,
        "additional_signature_discovery_calls": 0, "new_create_transaction_lookups": len(recovered), "results": recovered,
        "canonical_create_slot_qualified_count": sum(x["status"] == "QUALIFIED_CANONICAL_CREATE" for x in recovered),
        "canonical_create_slot_failed_count": sum(x["status"] != "QUALIFIED_CANONICAL_CREATE" for x in recovered),
        "canonical_create_signature_changed": False, "canonical_create_slot_durably_retained": all(x["status"] == "QUALIFIED_CANONICAL_CREATE" for x in recovered),
        "candidate_discovery_resumed": False, "reason": "PREVIOUS_COMPACT_SIGNATURE_PAGE_ITEMS_NOT_RETAINED; NO_ADDITIONAL_SIGNATURE_DISCOVERY_AUTHORIZED"}
    artifact["status"] = "HOLD_COMPACT_SIGNATURE_PAGE_ITEMS_NOT_RETAINED" if all(x["status"] == "QUALIFIED_CANONICAL_CREATE" for x in recovered) else "HOLD_CANONICAL_CREATE_LOOKUP_FAILURE"
    artifact["safety"].update({"additional_signature_discovery_calls": 0, "address_pagination_added": False, "canonical_create_signature_changed": False,
                                "raw_provider_payloads_retained": False, "raw_blocks_retained": False})
    encoded = json.dumps(artifact, indent=2, sort_keys=True) + "\n"
    if len(encoded.encode()) >= artifact["safety"]["artifact_max_bytes"]:
        raise RuntimeError("COMPACT_ARTIFACT_SIZE_BOUND_EXCEEDED")
    OUT.write_text(encoded)
    print(json.dumps({"status": artifact["status"], "qualified": artifact["create_slot_recovery"]["canonical_create_slot_qualified_count"], "failed": artifact["create_slot_recovery"]["canonical_create_slot_failed_count"], "bytes": len(encoded.encode())}, sort_keys=True))


def stability(points, peak):
    """Conservative adaptive continuation rule from the compact pilot diagnostics.

    A window can stop only after an hour without a new ATH and a latest close at
    or below half its ATH.  Otherwise the next bounded horizon is necessary.
    """
    if not points:
        return False
    latest = points[-1]
    return latest["timestamp"] - peak["timestamp"] >= 3600 and latest["c"] / peak["h"] <= 0.5


def execute():
    population = frozen_population()
    if len(population) != 12 or sum(x["monitor_state"] == "WAITING_FOR_ENTRY_REFERENCE" for x in population) != 11:
        raise RuntimeError("BYZANTINE_UI_POPULATION_NOT_EXPECTED_12")
    if any(not x["canonical_create_signature"] or x["canonical_create_timestamp"] is None for x in population):
        raise RuntimeError("CANONICAL_CREATE_COORDINATES_UNAVAILABLE")
    if not OUT.exists():
        raise RuntimeError("CREATE_SLOT_RECOVERY_AUDIT_REQUIRED")
    prior = json.loads(OUT.read_text())
    recovery = {x["mint"]: x for x in (prior.get("create_slot_recovery") or {}).get("results", []) if x.get("status") == "QUALIFIED_CANONICAL_CREATE"}
    if len(recovery) != 11:
        raise RuntimeError("ELEVEN_QUALIFIED_CREATE_SLOTS_REQUIRED")
    rpc_url, birdeye_key = rpc_client(), secret("BIRDEYE")
    if not birdeye_key:
        raise RuntimeError("BIRDEYE_BINDING_UNAVAILABLE")
    calls = {"signature_discovery": 0, "transaction_lookup": 0, "entry_pricing": 0, "lifecycle_2h": 0, "lifecycle_6h": 0, "lifecycle_12h": 0, "lifecycle_24h": 0}
    rows = []
    for target in population:
        base = {k: target[k] for k in ("mint", "ticker", "monitor_state", "canonical_create_signature", "canonical_create_timestamp")}
        if target["monitor_state"] != "WAITING_FOR_ENTRY_REFERENCE":
            rows.append({**base, "status": "PRESERVED_EXISTING_SCENARIO_D_ACTIVE", "actual_entry": None, "lifecycle": None})
            continue
        calls["signature_discovery"] += 1
        page = rpc(rpc_url, "getSignaturesForAddress", [target["mint"], {"limit": 100}]) or []
        create_slot = int(recovery[target["mint"]]["canonical_create_slot"])
        candidates = sorted((x for x in page if not x.get("err") and int(x.get("slot") or 0) > create_slot), key=lambda x: (int(x.get("slot") or 0), x.get("signature") or ""))[:8]
        compact_items = [{k: x.get(k) for k in ("signature", "slot", "blockTime", "err", "confirmationStatus", "status")} for x in page]
        actual = None
        for candidate in candidates:
            calls["transaction_lookup"] += 1
            tx = rpc(rpc_url, "getTransaction", [candidate["signature"], {"encoding": "json", "maxSupportedTransactionVersion": 0}])
            if not tx:
                continue
            trade = classify_trade(tx, target["mint"])
            if trade and trade.get("timestamp"):
                actual = {"signature": candidate["signature"], "slot": int(candidate["slot"]), "timestamp": int(trade["timestamp"]),
                          "venue": trade["venue"], "event_type": trade["event_type"], "seconds_after_create": int(trade["timestamp"]) - target["canonical_create_timestamp"]}
                break
        base.update({"canonical_create_slot": create_slot, "canonical_create_timestamp": int(recovery[target["mint"]]["canonical_create_timestamp"]), "signature_page_returned": len(page), "compact_signature_items": compact_items, "candidate_count_within_page": len(candidates)})
        if not actual:
            rows.append({**base, "transaction_lookups": min(len(candidates), 8), "status": "ACTUAL_ENTRY_NOT_FOUND_WITHIN_BOUND", "actual_entry": None, "lifecycle": None})
            continue
        t = actual["timestamp"]
        entry_call = ohlcv(birdeye_key, target["mint"], t - 15, t + 15); calls["entry_pricing"] += 1
        containing = [x for x in entry_call["points"] if x["timestamp"] <= t < x["timestamp"] + 15]
        entry_point = containing[-1] if containing else (min(entry_call["points"], key=lambda x: abs(x["timestamp"] - t)) if entry_call["points"] else None)
        if not entry_point:
            rows.append({**base, "actual_entry": actual, "entry_window": compact_call("ENTRY", target["mint"], entry_call), "status": "ENTRY_MCAP_NOT_AVAILABLE", "lifecycle": None})
            continue
        # Start strictly after the containing 15s candle.  Thus no pre-entry
        # price action can contribute to a candle high used as ATH.
        start = ((t // 15) + 1) * 15
        all_points, horizon_summaries, stopped = [], {}, None
        previous = 0
        for label, end_offset in HORIZONS:
            if previous and stopped:
                break
            result = ohlcv(birdeye_key, target["mint"], start + previous, start + end_offset)
            calls["lifecycle_" + label.lower()] += 1
            all_points.extend(result["points"])
            dedup = {x["timestamp"]: x for x in all_points}
            qualified = [x for x in sorted(dedup.values(), key=lambda x: x["timestamp"]) if x["timestamp"] >= start]
            if not qualified:
                horizon_summaries[label] = {"available": False, "call": compact_call(label, target["mint"], result)}
                previous = end_offset
                continue
            peak = max(qualified, key=lambda x: (x["h"], -x["timestamp"]))
            horizon_summaries[label] = {"available": True, "ath_mc_usd": peak["h"], "ath_timestamp": peak["timestamp"], "latest_close_mc_usd": qualified[-1]["c"], "call": compact_call(label, target["mint"], result)}
            if stability(qualified, peak):
                stopped = label
            previous = end_offset
        available = [(label, item) for label, item in horizon_summaries.items() if item.get("available")]
        if not available:
            rows.append({**base, "actual_entry": actual, "entry_mc_usd": entry_point["c"], "entry_candle_timestamp": entry_point["timestamp"], "entry_window": compact_call("ENTRY", target["mint"], entry_call), "horizons": horizon_summaries, "status": "POST_ENTRY_MCAP_NOT_AVAILABLE", "lifecycle": None})
            continue
        final_label, final = max(available, key=lambda x: (x[1]["ath_mc_usd"], -x[1]["ath_timestamp"]))
        lifecycle = {"ath_mc_usd": final["ath_mc_usd"], "ath_timestamp": final["ath_timestamp"], "time_to_ath_seconds": final["ath_timestamp"] - t, "max_multiple": final["ath_mc_usd"] / entry_point["c"], "horizon_capturing_ath": final_label, "stopped_at": stopped or "24H"}
        rows.append({**base, "actual_entry": actual, "entry_mc_usd": entry_point["c"], "entry_candle_timestamp": entry_point["timestamp"], "entry_window": compact_call("ENTRY", target["mint"], entry_call), "horizons": horizon_summaries, "lifecycle": lifecycle, "status": "QUALIFIED_ACTUAL_ENTRY_AND_ATH"})
        time.sleep(0.55)
    qualified = [x for x in rows if x["status"] == "QUALIFIED_ACTUAL_ENTRY_AND_ATH"]
    artifact = {"schema_version": "BYZANTINE_ACTUAL_ENTRY_24H_ATH_BACKFILL_V1", "status": "COMPLETE_BOUNDED_ACQUISITION",
        "frozen_population": population, "rows": rows,
        "create_slot_recovery": prior["create_slot_recovery"],
        "method": {"entry": "EARLIEST_DECODER_QUALIFIED_POST_CREATE_PUMPSWAP_TRADE_WITHIN_ONE_PAGE_BOUND", "ath_source": "BIRDEYE_V3_OHLCV_CHART_TYPE_MCAP_USD_15S", "ath_field": "h", "entry_field": "c", "max_horizon_hours": 24, "adaptive_horizon": True, "stability_rule": ">=3600 seconds since ATH and latest close / ATH <= 0.5", "pre_entry_price_action_included_in_ath": False, "double_counted_candles": 0},
        "counts": {**calls, "total_helius_calls": calls["signature_discovery"] + calls["transaction_lookup"], "total_birdeye_calls": sum(v for k, v in calls.items() if k.startswith("entry_") or k.startswith("lifecycle_")), "qualified_actual_entry": len(qualified), "actual_entry_not_found": sum(x["status"] == "ACTUAL_ENTRY_NOT_FOUND_WITHIN_BOUND" for x in rows), "qualified_entry_mc": sum(x.get("entry_mc_usd") is not None for x in qualified), "qualified_ath": len(qualified)},
        "safety": {"pilot_entry_reacquired": 0, "pilot_entry_mc_reacquired": 0, "pilot_ath_reacquired": 0, "canonical_create_reacquired": False, "create_transaction_reacquired": False, "previous_signature_discovery_calls": 11, "previous_create_transaction_lookups": 11, "replacement_signature_pages_made": calls["signature_discovery"], "compact_signature_items_retained": True, "address_pagination_added": False, "unbounded_address_pagination": False, "non_trade_event_used_as_entry": False, "invalid_low_field_used": False, "max_drawdown_qualified": False, "drawdown_85_coordinates_qualified": False, "scenario_d_provenance_overwritten": False, "raw_provider_payloads_retained": False, "raw_signature_page_payload_retained": False, "raw_blocks_retained": False, "project_500mb_rule_preserved": True, "artifact_max_bytes": 500000, "unbounded_growth_paths": 0}}
    encoded = json.dumps(artifact, indent=2, sort_keys=True) + "\n"
    if len(encoded.encode()) >= artifact["safety"]["artifact_max_bytes"]:
        raise RuntimeError("COMPACT_ARTIFACT_SIZE_BOUND_EXCEEDED")
    OUT.write_text(encoded)
    print(json.dumps({"status": artifact["status"], "counts": artifact["counts"], "sha256": hashlib.sha256(encoded.encode()).hexdigest(), "bytes": len(encoded.encode())}, sort_keys=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--recover-create-slots", action="store_true")
    args = parser.parse_args()
    recover_create_slots() if args.recover_create_slots else execute()
