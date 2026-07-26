#!/usr/bin/env python3
"""X61 read-only, exhaustive creator attribution validation."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.x55_exhaustive_history_audit import Rpc, scan_wallet
from src.core import walkback_worker as worker
from src.ops.operational_intelligence import build_operational_intelligence

CREATOR = "71ftvekAkhanTdJJXdZRLtz7ShkXxdAxhmVmyv2YVSFS"
SOURCE = "X61_READ_ONLY_ARCHIVAL_REPLAY"


def ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def one(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> dict:
    row = conn.execute(sql, args).fetchone()
    return dict(row) if row else {}


def iso(value) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, str) and "T" in value:
        return value
    return datetime.fromtimestamp(int(value), timezone.utc).isoformat().replace("+00:00", "Z")


def account_keys(tx: dict | None) -> list[str]:
    if not tx:
        return []
    keys = tx.get("transaction", {}).get("message", {}).get("accountKeys", [])
    return [k.get("pubkey", "") if isinstance(k, dict) else k for k in keys]


def tx_fact(rpc: Rpc, signature: str) -> tuple[dict | None, str]:
    if not signature:
        return None, "MISSING_SIGNATURE"
    return rpc.call("getTransaction", [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0, "commitment": "confirmed"}])


def role_evidence(ops: sqlite3.Connection, wallet: str) -> dict:
    entity = one(ops, "SELECT * FROM operator_entities WHERE entity_address=?", (wallet,))
    treasury = one(ops, "SELECT * FROM wt_confirmed_treasuries WHERE treasury=?", (wallet,))
    launches = ops.execute(
        "SELECT mint,creator_wallet,treasury_wallet,subprov_wallet,create_time,funding_mechanism "
        "FROM wt_watchtower_launches WHERE treasury_wallet=? OR subprov_wallet=? ORDER BY create_time",
        (wallet, wallet),
    ).fetchall()
    edges = ops.execute(
        "SELECT * FROM wt_provisioning_edges WHERE from_wallet=? OR to_wallet=? ORDER BY funding_block_time",
        (wallet, wallet),
    ).fetchall()
    return {
        "operator_entity": entity or None,
        "confirmed_treasury": treasury or None,
        "canonical_launch_occurrences": [dict(r) for r in launches],
        "provisioning_edges": [dict(r) for r in edges],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output-dir", default="docs/audits")
    ap.add_argument("--ops-db", default="database/wt_ops_v2.db")
    ap.add_argument("--live-db", default="database/flex_complete_database.db")
    ap.add_argument("--max-pages", type=int, default=100)
    ap.add_argument("--page-size", type=int, default=1000)
    ap.add_argument("--tx-ceiling", type=int, default=10000)
    ap.add_argument("--rpc-budget", type=int, default=50000)
    ap.add_argument("--wallet-ceiling", type=int, default=20)
    args = ap.parse_args()
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    ops, live = ro(Path(args.ops_db)), ro(Path(args.live_db))
    rpc = Rpc(os.environ.get("HELIUS_RPC_URL", worker.RPC_URL), args.rpc_budget)

    launches = [dict(r) for r in live.execute(
        "SELECT * FROM token_analysis WHERE pf_ws_creator=? OR earliest_tx_creator=? ORDER BY created_at", (CREATOR, CREATOR)
    )]
    core_launches = [dict(r) for r in live.execute("SELECT * FROM wt_creator_launches WHERE creator_wallet=?", (CREATOR,))]
    queue_rows = [dict(r) for r in live.execute("SELECT * FROM creator_funding_queue WHERE creator_address=?", (CREATOR,))]
    if not launches:
        raise RuntimeError("creator has no persisted launch")
    launch = launches[0]
    mint = launch["mint"]
    queue = one(ops, "SELECT * FROM wt_walkback_queue WHERE mint=?", (mint,))
    outcome = one(ops, "SELECT * FROM wt_attribution_outcomes WHERE mint=?", (mint,))
    audit = one(ops, "SELECT * FROM wt_launch_audit WHERE mint=?", (mint,))
    create_sig = launch.get("create_tx_signature") or (queue_rows[0].get("create_tx_signature") if queue_rows else "")
    migration_sig = launch.get("migration_tx") or (core_launches[0].get("migration_tx") if core_launches else "")
    create_tx, create_state = tx_fact(rpc, create_sig)
    migration_tx, migration_state = tx_fact(rpc, migration_sig)
    create_time = (create_tx or {}).get("blockTime") or launch.get("created_at")
    migration_time = (migration_tx or {}).get("blockTime") or launch.get("migrated_at")
    create_slot = (create_tx or {}).get("slot") or audit.get("create_slot")

    confirmed = {r[0] for r in ops.execute("SELECT treasury FROM wt_confirmed_treasuries")}
    current, anchor, visited = CREATOR, create_sig, {CREATOR}
    path, scans, terminal = [], [], None
    for depth in range(args.wallet_ceiling):
        scan = scan_wallet(rpc, current, anchor, args.max_pages, args.page_size, args.tx_ceiling)
        scans.append({k: v for k, v in scan.items() if k != "inbounds"})
        viable = [e for e in scan["inbounds"] if e["parent"] not in visited
                  and int(e.get("amount_lamports") or 0) > 0
                  and (e.get("mechanism") != "ATOMIC_WSOL_WRAP_CLOSE" or e.get("close_destination") == current)
                  and (not create_time or not e.get("block_time") or int(e["block_time"]) <= int(datetime.fromisoformat(str(create_time).replace("Z", "+00:00")).timestamp()) if isinstance(create_time, str) else int(create_time))]
        if not viable:
            terminal = {"wallet": current, "reason": scan["history_state"], "birth_reached": scan["birth_reached"]}
            break
        selected = max(viable, key=lambda e: (
            int(e["parent"] in confirmed),
            int(depth == 0 and e["parent"] == queue.get("funder_wallet")),
            int(e.get("mechanism") == "WSOL_WRAP_CLOSE"),
            int(e.get("mechanism") == "ATOMIC_WSOL_WRAP_CLOSE"),
            int(e.get("amount_lamports") or 0),
            int(e.get("block_time") or 0),
        ))
        edge = {
            "depth": depth + 1,
            "source_wallet": selected["parent"],
            "destination_wallet": current,
            "signature": selected["signature"],
            "block_time": selected.get("block_time"),
            "amount_lamports": selected.get("amount_lamports"),
            "amount_sol": round((selected.get("amount_lamports") or 0) / 1_000_000_000, 9),
            "mechanism": selected.get("mechanism"),
            "owner": selected.get("owner"),
            "close_destination": selected.get("close_destination"),
            "source_role_evidence": role_evidence(ops, selected["parent"]),
        }
        path.append(edge)
        current, anchor = selected["parent"], selected["signature"]
        visited.add(current)
        if current in confirmed:
            terminal = {"wallet": current, "reason": "CONFIRMED_WATCHTOWER_TREASURY", "birth_reached": False}
            break
    else:
        terminal = {"wallet": current, "reason": "EXPLICIT_WALLET_CEILING", "birth_reached": False}

    creator_scan = scans[0] if scans else {}
    birth_time = creator_scan.get("oldest_block_time") if creator_scan.get("birth_reached") else None
    first_tx = creator_scan.get("oldest_signature") if creator_scan.get("birth_reached") else None
    create_ts = int((create_tx or {}).get("blockTime") or datetime.fromisoformat(str(create_time).replace("Z", "+00:00")).timestamp())
    migration_ts = int((migration_tx or {}).get("blockTime") or migration_time)
    birth_to_create = create_ts - int(birth_time) if birth_time else None
    create_to_migration = migration_ts - create_ts
    timeline = {
        "source": SOURCE,
        "creator": CREATOR,
        "mint": mint,
        "creator_first_transaction": {"signature": first_tx, "timestamp": birth_time, "iso": iso(birth_time), "source": "RPC_EXHAUSTIVE_SIGNATURE_HISTORY" if birth_time else creator_scan.get("history_state")},
        "creator_funding": {"signature": path[0]["signature"] if path else queue.get("funder_sig"), "timestamp": path[0]["block_time"] if path else queue.get("funder_block_time"), "iso": iso(path[0]["block_time"] if path else queue.get("funder_block_time")), "source": "RPC_PARSED_TRANSACTION" if path else "wt_walkback_queue"},
        "create": {"signature": create_sig, "slot": create_slot, "timestamp": create_ts, "iso": iso(create_ts), "source": "RPC_PARSED_TRANSACTION", "rpc_state": create_state},
        "migration": {"signature": migration_sig, "slot": (migration_tx or {}).get("slot") or launch.get("migration_slot"), "timestamp": migration_ts, "iso": iso(migration_ts), "source": "RPC_PARSED_TRANSACTION", "rpc_state": migration_state},
        "intervals_seconds": {"birth_to_create": birth_to_create, "create_to_migration": create_to_migration, "birth_to_migration": (migration_ts - int(birth_time)) if birth_time else None},
        "classifications": {"quick_birth_migration": bool(birth_to_create is not None and 0 <= birth_to_create <= 86400 and 0 <= create_to_migration <= 900), "rapid_birth_create": bool(birth_to_create is not None and 0 <= birth_to_create <= 86400), "migration_under_5m": 0 <= create_to_migration < 300},
    }

    intel = build_operational_intelligence(args.ops_db, args.live_db, window_seconds=86400, now=max(create_ts, migration_ts) + 60)
    record = intel.get("records", {}).get(mint, {})
    launch_fact = {
        "mint": mint, "creator": CREATOR, "create_signature": create_sig, "create_slot": create_slot,
        "create_timestamp": create_ts, "migration_signature": migration_sig, "migration_timestamp": migration_ts,
        "topology": record.get("topology", "UNKNOWN"), "operation_assignment": record.get("operation_id"),
        "operation_confidence": record.get("operation_confidence"), "persisted_attribution_outcome": outcome,
    }
    funding_tx, funding_tx_state = tx_fact(rpc, path[0]["signature"] if path else queue.get("funder_sig", ""))
    funding_flows = []
    if funding_tx:
        for flow in __import__("src.core.deep_walkback", fromlist=["materialize_atomic_wsol"]).materialize_atomic_wsol(funding_tx, path[0]["signature"] if path else queue.get("funder_sig", "")):
            funding_flows.append({k: getattr(flow, k) for k in flow.__dataclass_fields__})
    lineage = {"source": SOURCE, "creator": CREATOR, "mint": mint, "path": path, "terminal": terminal, "wallet_scans": scans, "funding_transaction": {"signature": path[0]["signature"] if path else queue.get("funder_sig"), "rpc_state": funding_tx_state, "fee_payer": (account_keys(funding_tx) or [None])[0], "atomic_wsol_flows": funding_flows}, "rpc": {"calls": rpc.calls, "errors": dict(rpc.errors)}, "production_mutations": 0}

    infrastructure_wallets = [CREATOR] + [e["source_wallet"] for e in path]
    shared = {wallet: role_evidence(ops, wallet) for wallet in infrastructure_wallets}
    campaign = [dict(r) for r in ops.execute(
        "SELECT mint,creator_wallet,treasury_wallet,subprov_wallet,create_time,funding_mechanism,create_to_migration_secs "
        "FROM wt_watchtower_launches WHERE create_time BETWEEN ? AND ? ORDER BY create_time", (create_ts - 300, create_ts + 300)
    )]
    treasury_match = terminal["reason"] == "CONFIRMED_WATCHTOWER_TREASURY"
    shared_wallets = [w for w, evidence in shared.items() if evidence["canonical_launch_occurrences"] or evidence["operator_entity"] or evidence["confirmed_treasury"]]
    funding_match = bool(path and path[0]["mechanism"] in ("WSOL_WRAP_CLOSE", "ATOMIC_WSOL_WRAP_CLOSE") and path[0]["close_destination"] == CREATOR)
    causal_capital = next((edge for edge in path[1:] if edge["amount_sol"] >= 100), None)
    contradictions = []
    if terminal["reason"] not in ("CONFIRMED_WATCHTOWER_TREASURY",) and terminal.get("birth_reached"):
        contradictions.append("Exhaustive lineage terminated at a non-canonical wallet birth.")
    confidence = "CONFIRMED" if treasury_match and funding_match and len(shared_wallets) >= 2 and not contradictions else ("STRONG" if funding_match and causal_capital and not contradictions else "PROBABLE" if funding_match and shared_wallets else "WEAK")
    recommendation = "Promote to canonical WATCHTOWER" if confidence == "CONFIRMED" else "Leave as candidate pending human confirmation of the newly discovered treasury" if confidence in ("STRONG", "PROBABLE") else "Insufficient evidence"
    scorecard = {
        "operation_candidate": "WATCHTOWER", "confidence": confidence, "recommendation": recommendation,
        "evidence": {"treasury_match": treasury_match, "new_treasury_candidate": causal_capital["source_wallet"] if causal_capital else None, "provisioning_chain": bool(causal_capital and funding_match), "quick_birth_migration": timeline["classifications"]["quick_birth_migration"], "funding_lineage": bool(path), "campaign_timing": bool(campaign), "shared_infrastructure": bool(shared_wallets), "shared_operational_template": funding_match},
        "contradictions_found": bool(contradictions), "contradictions": contradictions,
    }
    comparison = {"shared_wallets": shared_wallets, "wallet_evidence": shared, "campaign_launches_within_5m": campaign, "create_fee_payer": (account_keys(create_tx) or [None])[0], "migration_fee_payer": (account_keys(migration_tx) or [None])[0], "funding_mechanism_match": funding_match}

    (out / "x61_71ftvek_lineage.json").write_text(json.dumps(lineage, indent=2, sort_keys=True) + "\n")
    (out / "x61_71ftvek_timeline.json").write_text(json.dumps(timeline, indent=2, sort_keys=True) + "\n")
    (out / "x61_71ftvek_scorecard.json").write_text(json.dumps(scorecard, indent=2, sort_keys=True) + "\n")
    (out / "x61_71ftvek_comparison.md").write_text("# X61 Canonical Comparison\n\n```json\n" + json.dumps(comparison, indent=2, sort_keys=True) + "\n```\n")
    summary = {"launch": launch_fact, "timeline": timeline, "lineage_terminal": terminal, "path": path, "scorecard": scorecard, "comparison": comparison, "persisted_walkback": queue}
    (out / "x61_71ftvek_validation.md").write_text("# X61 WATCHTOWER Validation\n\n```json\n" + json.dumps(summary, indent=2, sort_keys=True) + "\n```\n")
    print(json.dumps({"mint": mint, "terminal": terminal, "path_wallets": [e["source_wallet"] for e in path], "scorecard": scorecard, "rpc_calls": rpc.calls}, indent=2))
    ops.close(); live.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
