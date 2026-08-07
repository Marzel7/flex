#!/usr/bin/env python3
"""Build the X78.13 additive transaction-first lineage substrate."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.ops.transaction_first_lineage import (  # noqa: E402
    LaunchFact,
    build_populations,
    classify_path,
    connect_substrate,
    extract_context,
    extract_directional_edges,
    finish_run,
    graph_digest,
    initialise_run,
    longest_chronological_path,
    priority_key,
    stable_id,
    verify_launch,
)


def ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def as_timestamp(value) -> int | None:
    if isinstance(value, (int, float)) and value > 0:
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value))
        except ValueError:
            return None
    return None


def freeze_launches(main: sqlite3.Connection, ops: sqlite3.Connection,
                    out: sqlite3.Connection, batch_size: int) -> dict:
    walkback_mints = {r[0] for r in ops.execute(
        "SELECT DISTINCT mint FROM wt_walkback_edge_candidates WHERE mint IS NOT NULL")}
    census = dict(total=0, creator=0, creation_signature=0, creation_timestamp=0,
                  source_platform=0, existing_walkback=0, without_walkback=0,
                  rpc_pending=0)
    cursor = main.execute("""
        SELECT mint,coalesce(earliest_tx_creator,pf_ws_creator) creator,
               create_tx_signature,coalesce(created_at,first_observed_at) creation_time,
               source_platform
        FROM token_analysis ORDER BY mint
    """)
    while True:
        rows = cursor.fetchmany(batch_size)
        if not rows:
            break
        values = []
        queue = []
        for row in rows:
            mint, creator, signature = row["mint"], row["creator"], row["create_tx_signature"]
            creation_time = as_timestamp(row["creation_time"])
            has_walkback = int(mint in walkback_mints)
            if creator and signature and creation_time:
                status, reason = "PARTIAL_LAUNCH", "creation transaction pending verification"
            else:
                status, reason = "PARTIAL_LAUNCH", "one or more launch facts missing"
            acquisition = ("PERSISTED_EVIDENCE_READY" if has_walkback else
                           ("RPC_PENDING_BUDGET" if creator and signature
                            else "EVIDENCE_UNAVAILABLE"))
            values.append((mint, creator, signature, creation_time, row["source_platform"],
                           status, reason, has_walkback, acquisition))
            if creator and signature and not has_walkback:
                queue.append((mint, creator, signature, priority_key(mint), "RPC_PENDING_BUDGET",
                              "CREATION_AND_CREATOR_FUNDING", 2))
            census["total"] += 1
            census["creator"] += bool(creator)
            census["creation_signature"] += bool(signature)
            census["creation_timestamp"] += bool(creation_time)
            census["source_platform"] += bool(row["source_platform"])
            census["existing_walkback"] += has_walkback
            census["without_walkback"] += not has_walkback
            census["rpc_pending"] += bool(creator and signature and not has_walkback)
        out.executemany("""
            INSERT INTO tf_launches
              (mint,creator,creation_signature,creation_time,source_platform,launch_status,
               verification_reason,has_persisted_walkback,acquisition_state)
            VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(mint) DO UPDATE SET creator=excluded.creator,
              creation_signature=excluded.creation_signature,creation_time=excluded.creation_time,
              source_platform=excluded.source_platform,
              has_persisted_walkback=excluded.has_persisted_walkback,
              acquisition_state=CASE WHEN tf_launches.launch_status='VERIFIED_LAUNCH'
                THEN tf_launches.acquisition_state ELSE excluded.acquisition_state END
        """, values)
        out.executemany("""
            INSERT INTO tf_acquisition_queue
              (mint,creator,creation_signature,priority_key,state,required_evidence,estimated_rpc_calls)
            VALUES (?,?,?,?,?,?,?) ON CONFLICT(mint) DO UPDATE SET
              creator=excluded.creator,creation_signature=excluded.creation_signature,
              priority_key=excluded.priority_key,required_evidence=excluded.required_evidence,
              estimated_rpc_calls=excluded.estimated_rpc_calls
        """, queue)
        out.commit()
    return census


def existing_census(out: sqlite3.Connection) -> dict:
    out.execute("""
        UPDATE tf_launches SET acquisition_state='RPC_PENDING_BUDGET'
        WHERE has_persisted_walkback=0 AND creator IS NOT NULL
          AND creation_signature IS NOT NULL
    """)
    out.execute("""
        UPDATE tf_launches SET acquisition_state='EVIDENCE_UNAVAILABLE'
        WHERE has_persisted_walkback=0
          AND (creator IS NULL OR creation_signature IS NULL)
    """)
    out.commit()
    row = out.execute("""
        SELECT count(*) total,sum(creator IS NOT NULL) creator,
          sum(creation_signature IS NOT NULL) creation_signature,
          sum(creation_time IS NOT NULL) creation_timestamp,
          sum(source_platform IS NOT NULL) source_platform,
          sum(has_persisted_walkback=1) existing_walkback,
          sum(has_persisted_walkback=0) without_walkback,
          sum(acquisition_state='RPC_PENDING_BUDGET') rpc_pending
        FROM tf_launches
    """).fetchone()
    return dict(row)


def load_cache(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open() as handle:
        return json.load(handle)


def evidence_rows(ops: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in ops.execute("""
        SELECT mint,wallet,candidate_parent,signature,block_time,mechanism,hop_depth,
               selection_status,anchor_signature
        FROM wt_walkback_edge_candidates
        WHERE mint IS NOT NULL AND signature IS NOT NULL
        ORDER BY mint,hop_depth,signature,candidate_parent,wallet
    """)]


def persist_transactions(out: sqlite3.Connection, signatures: set[str], cache: dict,
                         batch_size: int) -> tuple[int, int]:
    now = int(time.time())
    available = missing = 0
    batch = []
    for signature in sorted(signatures):
        tx = cache.get(signature)
        if tx:
            available += 1
            batch.append((signature, tx.get("blockTime"), json.dumps(tx, separators=(",", ":")),
                          now, "RPC_CACHE_IMPORT", 1, "PARSED"))
        else:
            missing += 1
            batch.append((signature, None, None, now, "CACHE_MISS", 0, "UNAVAILABLE"))
        if len(batch) >= batch_size:
            out.executemany("""
                INSERT INTO tf_transaction_cache
                  (signature,block_time,transaction_json,fetched_at,source,rpc_verified,parse_status)
                VALUES (?,?,?,?,?,?,?) ON CONFLICT(signature) DO UPDATE SET
                  block_time=excluded.block_time,transaction_json=coalesce(excluded.transaction_json,tf_transaction_cache.transaction_json),
                  fetched_at=excluded.fetched_at,source=excluded.source,
                  rpc_verified=max(tf_transaction_cache.rpc_verified,excluded.rpc_verified),
                  parse_status=excluded.parse_status
            """, batch)
            out.commit()
            batch.clear()
    if batch:
        out.executemany("""
            INSERT INTO tf_transaction_cache
              (signature,block_time,transaction_json,fetched_at,source,rpc_verified,parse_status)
            VALUES (?,?,?,?,?,?,?) ON CONFLICT(signature) DO UPDATE SET
              block_time=excluded.block_time,transaction_json=coalesce(excluded.transaction_json,tf_transaction_cache.transaction_json),
              fetched_at=excluded.fetched_at,source=excluded.source,
              rpc_verified=max(tf_transaction_cache.rpc_verified,excluded.rpc_verified),
              parse_status=excluded.parse_status
        """, batch)
        out.commit()
    return available, missing


def rebuild_graph(out: sqlite3.Connection, rows: list[dict], cache: dict,
                  batch_size: int) -> dict:
    started = time.monotonic()
    out.execute("DELETE FROM tf_context_observations")
    out.execute("DELETE FROM tf_edges")
    out.execute("DELETE FROM tf_paths")
    out.execute("DELETE FROM tf_population_members")
    out.execute("DELETE FROM tf_populations")
    out.commit()

    by_mint = defaultdict(list)
    for row in rows:
        by_mint[row["mint"]].append(row)
    edge_total = context_total = verified_launches = 0
    for index, mint in enumerate(sorted(by_mint), 1):
        launch_row = out.execute("""
            SELECT mint,creator,creation_signature,creation_time,source_platform
            FROM tf_launches WHERE mint=?
        """, (mint,)).fetchone()
        if not launch_row:
            continue
        fact = LaunchFact(*launch_row)
        create_tx = cache.get(fact.creation_signature)
        launch_status, reason = verify_launch(fact, create_tx)
        verified_creation_time = (
            create_tx.get("blockTime") if create_tx and isinstance(create_tx.get("blockTime"), int)
            else fact.creation_time
        )
        out.execute("""
            UPDATE tf_launches SET launch_status=?,verification_reason=?,creation_time=?,verified_at=?,
              acquisition_state=? WHERE mint=?
        """, (launch_status, reason, verified_creation_time, int(time.time()),
              "PERSISTED_EVIDENCE_RECONSTRUCTED" if launch_status == "VERIFIED_LAUNCH"
              else "EVIDENCE_UNAVAILABLE", mint))
        verified_launches += launch_status == "VERIFIED_LAUNCH"

        mint_edges = []
        seen_transactions = set()
        for candidate in by_mint[mint]:
            signature = candidate["signature"]
            tx = cache.get(signature)
            if not tx:
                continue
            extracted = extract_directional_edges(tx)
            exact = [edge for edge in extracted
                     if edge["sender"] == candidate["candidate_parent"]
                     and edge["recipient"] == candidate["wallet"]]
            for edge in exact:
                edge_id = stable_id(signature, edge["sender"], edge["recipient"],
                                    edge["relationship_type"], edge["asset"], mint)
                persisted = dict(edge, edge_id=edge_id, signature=signature,
                                 block_time=tx["blockTime"], hop_depth=candidate["hop_depth"],
                                 launch_context=mint, creator_context=fact.creator,
                                 evidence_source="WALKBACK_SIGNATURE_RPC_REPLAY",
                                 rpc_verified=1)
                mint_edges.append(persisted)
                out.execute("""
                    INSERT OR IGNORE INTO tf_edges
                      (edge_id,sender,recipient,signature,block_time,amount,asset,
                       relationship_type,mechanism,source_program,hop_depth,launch_context,
                       creator_context,evidence_source,observed_or_inherited,rpc_verified)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (edge_id, edge["sender"], edge["recipient"], signature, tx["blockTime"],
                      edge["amount"], edge["asset"], edge["relationship_type"], edge["mechanism"],
                      edge["source_program"], candidate["hop_depth"], mint, fact.creator,
                      "WALKBACK_SIGNATURE_RPC_REPLAY", "OBSERVED", 1))
                edge_total += 1
            if signature not in seen_transactions:
                for observation in extract_context(tx):
                    context_id = stable_id(signature, observation["context_type"], mint)
                    out.execute("""
                        INSERT OR IGNORE INTO tf_context_observations
                          (context_id,signature,block_time,context_type,wallets_json,
                           launch_context,evidence_source,rpc_verified)
                        VALUES (?,?,?,?,?,?,?,1)
                    """, (context_id, signature, tx.get("blockTime"),
                          observation["context_type"], json.dumps(observation["wallets"]),
                          mint, "TRANSACTION_RPC_REPLAY"))
                    context_total += 1
                seen_transactions.add(signature)

        path = longest_chronological_path(
            mint_edges, fact.creator, verified_creation_time,
        ) if fact.creator and verified_creation_time else []
        path_status, termination = classify_path(path, launch_status == "VERIFIED_LAUNCH")
        root = path[0]["sender"] if path else None
        subprovider = path[-1]["sender"] if path else None
        for position, edge in enumerate(path[:-1]):
            following = path[position + 1]
            incoming = edge.get("amount")
            outgoing = following.get("amount")
            difference = None
            if edge.get("asset") == following.get("asset"):
                try:
                    difference = str(int(incoming) - int(outgoing))
                except (TypeError, ValueError):
                    pass
            out.execute("""
                UPDATE tf_edges SET incoming_amount=?,outgoing_amount=?,time_gap_seconds=?,
                  amount_difference=? WHERE edge_id=?
            """, (incoming, outgoing, following["block_time"] - edge["block_time"],
                  difference, edge["edge_id"]))
        out.execute("""
            INSERT INTO tf_paths
              (mint,creator,root,subprovider,edge_count,max_depth,path_status,edge_ids_json,
               termination_reason,chronology_valid,reconstructed_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(mint) DO UPDATE SET creator=excluded.creator,root=excluded.root,
              subprovider=excluded.subprovider,edge_count=excluded.edge_count,
              max_depth=excluded.max_depth,path_status=excluded.path_status,
              edge_ids_json=excluded.edge_ids_json,termination_reason=excluded.termination_reason,
              chronology_valid=excluded.chronology_valid,reconstructed_at=excluded.reconstructed_at
        """, (mint, fact.creator, root, subprovider, len(path), len(path), path_status,
              json.dumps([edge["edge_id"] for edge in path]), termination, 1, int(time.time())))
        if index % batch_size == 0:
            out.commit()
    out.commit()
    return {"verified_launches": verified_launches, "edges": edge_total,
            "contexts": context_total, "reconstructed_mints": len(by_mint),
            "graph_build_seconds": round(time.monotonic() - started, 3)}


def canonical_overlay(ops: sqlite3.Connection, out: sqlite3.Connection) -> dict:
    frozen_at = int(time.time())
    out.execute("DELETE FROM tf_canonical_overlay WHERE object_type='WATCHTOWER_LAUNCH'")
    canonical = [dict(r) for r in ops.execute("SELECT * FROM wt_watchtower_launches")]
    counts = defaultdict(int)
    for row in canonical:
        path = out.execute("SELECT root,path_status FROM tf_paths WHERE mint=?", (row["mint"],)).fetchone()
        if not path:
            comparison = "EVIDENCE_UNAVAILABLE"
            reconstructed_root = None
        elif path["path_status"] == "COMPLETE":
            reconstructed_root = path["root"]
            comparison = ("INDEPENDENTLY_REDISCOVERED_SAME_ROOT"
                          if reconstructed_root == row["treasury_wallet"]
                          else "INDEPENDENTLY_REDISCOVERED_DIFFERENT_ROOT")
        elif path["path_status"] == "PARTIALLY_REDISCOVERED":
            comparison, reconstructed_root = "PARTIALLY_REDISCOVERED", path["root"]
        else:
            comparison, reconstructed_root = "EVIDENCE_UNAVAILABLE", path["root"]
        counts[comparison] += 1
        out.execute("""
            INSERT INTO tf_canonical_overlay
              (object_type,object_id,mint,canonical_root,reconstructed_root,comparison_class,compared_at)
            VALUES ('WATCHTOWER_LAUNCH','WATCHTOWER',?,?,?,?,?)
        """, (row["mint"], row["treasury_wallet"], reconstructed_root, comparison, frozen_at))
    out.commit()
    return dict(counts)


def registry_overlay(out: sqlite3.Connection, ops_path: Path, main_path: Path) -> list[dict]:
    """Compare governed objects only after the transaction graph is frozen.

    Importantly, this adapter is not reachable from launch verification, edge
    extraction, path reconstruction, population construction, or graph digesting.
    Registry names and lifecycle labels can therefore annotate, but never shape,
    the clean-room graph.
    """
    from src.ops.emerging_operator_service import EmergingOperatorService

    service = EmergingOperatorService(str(ops_path), str(main_path))
    snapshot = service.list(limit=500, debug=False)
    sections = {
        "confirmed_operations_reconciled": "REGISTRY_CONFIRMED",
        "active_investigations_reconciled": "REGISTRY_INVESTIGATION",
        "review_cases_reconciled": "REGISTRY_REVIEW",
        "infrastructure_alerts_reconciled": "REGISTRY_INFRASTRUCTURE",
    }
    out.execute("DELETE FROM tf_canonical_overlay WHERE object_type LIKE 'REGISTRY_%'")
    compared_at = int(time.time())
    summaries = []
    for section, object_type in sections.items():
        for summary in snapshot.get(section) or []:
            family = service.get(summary.get("family_id")) or summary
            object_id = family.get("family_id")
            counts = defaultdict(int)
            roots = defaultdict(int)
            for mint in sorted(set(family.get("launch_list") or [])):
                path = out.execute(
                    "SELECT root,path_status FROM tf_paths WHERE mint=?", (mint,)
                ).fetchone()
                if not path:
                    comparison, root = "EVIDENCE_UNAVAILABLE", None
                elif path["path_status"] == "COMPLETE":
                    comparison, root = "TRANSACTION_PATH_RECONSTRUCTED", path["root"]
                elif path["path_status"] == "PARTIALLY_REDISCOVERED":
                    comparison, root = "PARTIALLY_RECONSTRUCTED", path["root"]
                else:
                    comparison, root = "EVIDENCE_UNAVAILABLE", path["root"]
                counts[comparison] += 1
                if root:
                    roots[root] += 1
                out.execute("""
                    INSERT INTO tf_canonical_overlay
                      (object_type,object_id,mint,canonical_root,reconstructed_root,
                       comparison_class,compared_at)
                    VALUES (?,?,?,?,?,?,?)
                """, (object_type, object_id, mint, family.get("terminal_entity"), root,
                      comparison, compared_at))
            summaries.append({
                "object_type": object_type,
                "object_id": object_id,
                "name": family.get("family_name"),
                "governed_launches": len(set(family.get("launch_list") or [])),
                "comparison": dict(sorted(counts.items())),
                "reconstructed_roots": [
                    {"root": root, "launches": count}
                    for root, count in sorted(roots.items(), key=lambda x: (-x[1], x[0]))[:5]
                ],
            })
    out.commit()
    return summaries


def session_overlay(ops: sqlite3.Connection, out: sqlite3.Connection, cache: dict,
                    batch_size: int) -> dict:
    out.execute("DELETE FROM tf_session_comparison")
    out.commit()
    counts = defaultdict(int)
    batch = []
    for row in ops.execute("""
        SELECT id,treasury_wallet,subprov_wallet,funding_signature
        FROM wt_active_subprov_sessions ORDER BY id
    """):
        tx = cache.get(row["funding_signature"])
        edges = extract_directional_edges(tx)
        exact = [e for e in edges if e["sender"] == row["treasury_wallet"]
                 and e["recipient"] == row["subprov_wallet"]]
        incoming = [e for e in edges if e["recipient"] == row["subprov_wallet"]]
        if exact:
            classification, direct = "CORRECT_DIRECT_RELATIONSHIP", row["treasury_wallet"]
        elif incoming:
            classification, direct = "INCORRECT_INHERITED_ANCESTRY", incoming[0]["sender"]
        elif tx:
            classification, direct = "INCORRECT_INHERITED_ANCESTRY", None
        else:
            classification, direct = "UNVERIFIABLE_HISTORICAL_ANCESTRY", None
        counts[classification] += 1
        batch.append((row["id"], row["treasury_wallet"], row["subprov_wallet"], direct,
                      row["funding_signature"], classification, int(time.time())))
        if len(batch) >= batch_size:
            out.executemany("""
                INSERT INTO tf_session_comparison
                  (session_id,stored_root,stored_child,direct_sender,signature,comparison_class,compared_at)
                VALUES (?,?,?,?,?,?,?)
            """, batch)
            out.commit()
            batch.clear()
    if batch:
        out.executemany("""
            INSERT INTO tf_session_comparison
              (session_id,stored_root,stored_child,direct_sender,signature,comparison_class,compared_at)
            VALUES (?,?,?,?,?,?,?)
        """, batch)
        out.commit()
    return dict(counts)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--main-db", default=str(ROOT / "database/flex_complete_database.db"))
    parser.add_argument("--ops-db", default=str(ROOT / "database/wt_ops_v2.db"))
    parser.add_argument("--substrate", default=str(ROOT / "database/transaction_first_lineage.db"))
    parser.add_argument("--cache", default="/tmp/x7812_tx_cache.json")
    parser.add_argument("--report", default="/tmp/x7813_run_report.json")
    parser.add_argument("--batch-size", type=int, default=2000)
    parser.add_argument("--run-id", default="x78_13")
    parser.add_argument("--skip-census", action="store_true")
    args = parser.parse_args()

    started = time.monotonic()
    main_db, ops_db = ro(Path(args.main_db)), ro(Path(args.ops_db))
    out = connect_substrate(args.substrate)
    out.row_factory = sqlite3.Row
    initialise_run(out, args.run_id)

    write_started = time.monotonic()
    census = existing_census(out) if args.skip_census else freeze_launches(
        main_db, ops_db, out, args.batch_size)
    rows = evidence_rows(ops_db)
    cache = load_cache(Path(args.cache))
    signatures = {r["signature"] for r in rows} | {
        r["anchor_signature"] for r in rows if r.get("anchor_signature")
    }
    signatures |= {r[0] for r in out.execute("""
        SELECT creation_signature FROM tf_launches
        WHERE has_persisted_walkback=1 AND creation_signature IS NOT NULL
    """)}
    available, missing = persist_transactions(out, signatures, cache, args.batch_size)
    graph = rebuild_graph(out, rows, cache, args.batch_size)
    populations = build_populations(out, args.run_id)
    digest = graph_digest(out)
    overlay = canonical_overlay(ops_db, out)
    governed_objects = registry_overlay(out, Path(args.ops_db), Path(args.main_db))
    sessions = session_overlay(ops_db, out, cache, args.batch_size)
    db_write_ms = round((time.monotonic() - write_started) * 1000)

    metrics = {
        "launch_count": census["total"], "transaction_count": available,
        "edge_count": out.execute("SELECT count(*) FROM tf_edges").fetchone()[0],
        "path_count": out.execute("SELECT count(*) FROM tf_paths").fetchone()[0],
        "population_count": populations, "rpc_calls": 0, "cache_hits": available,
        "db_write_ms": db_write_ms, "peak_batch_size": args.batch_size,
    }
    finish_run(out, args.run_id, metrics)
    coverage = dict(out.execute("""
        SELECT
          count(*) historical_launches,
          sum(launch_status='VERIFIED_LAUNCH') verified_launches,
          sum(coalesce((SELECT edge_count FROM tf_paths p WHERE p.mint=tf_launches.mint),0)>=1) one_hop,
          sum(coalesce((SELECT edge_count FROM tf_paths p WHERE p.mint=tf_launches.mint),0)>=2) two_hop,
          sum(coalesce((SELECT edge_count FROM tf_paths p WHERE p.mint=tf_launches.mint),0)>=3) multi_hop,
          sum(acquisition_state IN ('RPC_PENDING_BUDGET','EVIDENCE_UNAVAILABLE')) evidence_unavailable
        FROM tf_launches
    """).fetchone())
    report = {
        "run_id": args.run_id, "census": census,
        "rpc_budget": {
            "launches_requiring_additional_rpc": census["rpc_pending"],
            "minimum_unique_signatures": census["rpc_pending"],
            "minimum_expected_rpc_calls": census["rpc_pending"] * 2,
            "estimated_cache_growth_gb_at_8kb_per_tx": round(census["rpc_pending"] * 2 * 8192 / 1e9, 1),
            "estimated_runtime_hours_at_25_calls_per_second": round(census["rpc_pending"] * 2 / 25 / 3600, 1),
            "executed_rpc_calls": 0,
            "reason": "uncontrolled multi-million-call retrieval prohibited; persisted cache exhausted first",
        },
        "persisted_evidence": {"candidate_rows": len(rows), "signatures_requested": len(signatures),
                               "cache_hits": available, "cache_misses": missing},
        "graph": graph, "coverage": coverage, "population_count": populations,
        "graph_digest": digest, "watchtower_overlay": overlay,
        "governed_object_overlay": governed_objects,
        "historical_session_overlay": sessions, "metrics": metrics,
        "runtime_seconds": round(time.monotonic() - started, 3),
    }
    Path(args.report).write_text(json.dumps(report, indent=2, sort_keys=True))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
