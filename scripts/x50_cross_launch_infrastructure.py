#!/usr/bin/env python3
"""X50 collective infrastructure analysis over X49.1 shadow evidence.

Reads production SQLite in query-only mode and writes audit CSV/JSON files only.
No attribution, registry, operator, operation, or UI state is modified.
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict, deque
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.utils.infra_mapping import is_known_account

SOURCE = "X50_CROSS_LAUNCH_INTELLIGENCE"


def ro(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def read_csv(path: Path) -> list[dict]:
    return list(csv.DictReader(open(path, newline="")))


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def number(value: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--x49-dir", default="/private/tmp/x49_1_shadow_replay")
    parser.add_argument("--ops-db", default="database/wt_ops_v2.db")
    parser.add_argument("--output-dir", default="/private/tmp/x50_cross_launch_intelligence")
    args = parser.parse_args()
    x49, out = Path(args.x49_dir), Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    results = {r["token"]: r for r in read_csv(x49 / "x49_1_population_results.csv")}
    pending = {token for token, row in results.items()
               if row["final_classification"] == "WT_ACCOUNT_CLOSE_LINEAGE_PENDING"}
    if len(pending) != 85:
        raise SystemExit(f"Expected 85 pending launches, found {len(pending)}")
    paths = [h for h in read_csv(x49 / "x49_1_walkback_paths.csv") if h["token"] in pending]

    ops = ro(args.ops_db)
    confirmed = {r[0] for r in ops.execute("SELECT treasury FROM wt_confirmed_treasuries")}
    entities = defaultdict(list)
    for row in ops.execute("SELECT entity_address,entity_type,operator_id,confidence FROM operator_entities"):
        entities[row["entity_address"]].append(dict(row))
    persisted = [dict(r) for r in ops.execute("SELECT * FROM wt_provisioning_edges")]
    ops.close()

    graph_rows, valid_paths = [], defaultdict(list)
    for hop in paths:
        valid = hop["ambiguity_status"] != "CYCLE_DETECTED" and hop["source_wallet"] != hop["destination_wallet"]
        if valid:
            valid_paths[hop["token"]].append(hop)
        graph_rows.append({
            "source": SOURCE, "token": hop["token"], "creator": results[hop["token"]]["creator"],
            "source_wallet": hop["source_wallet"], "destination_wallet": hop["destination_wallet"],
            "hop_depth": hop["hop_number"], "signature": hop["signature"], "amount_sol": hop["amount"],
            "block_time": hop["block_time"], "mechanism": results[hop["token"]]["mechanism_evidence"],
            "known_entity_status": hop["known_entity_status"], "ambiguity_status": hop["ambiguity_status"],
            "edge_usable_for_scoring": int(valid),
        })
    graph_fields = ["source","token","creator","source_wallet","destination_wallet","hop_depth",
                    "signature","amount_sol","block_time","mechanism","known_entity_status",
                    "ambiguity_status","edge_usable_for_scoring"]
    write_csv(out / "x50_infrastructure_graph.csv", graph_rows, graph_fields)

    wallet_tokens, wallet_creators = defaultdict(set), defaultdict(set)
    shadow_downstream, historical_downstream = defaultdict(set), defaultdict(set)
    wallet_amounts, wallet_times, wallet_depths = defaultdict(list), defaultdict(list), defaultdict(list)
    wallet_migrations = defaultdict(list)
    immediate_tokens, immediate_creators = defaultdict(set), defaultdict(set)
    inbound_seen = Counter()
    for token, hops in valid_paths.items():
        creator = results[token]["creator"]
        for hop in hops:
            wallet = hop["source_wallet"]
            wallet_tokens[wallet].add(token); wallet_creators[wallet].add(creator)
            shadow_downstream[wallet].add(hop["destination_wallet"])
            historical_downstream[wallet].add(hop["destination_wallet"])
            wallet_amounts[wallet].append(number(hop["amount"]))
            if hop["block_time"]: wallet_times[wallet].append(int(float(hop["block_time"])))
            wallet_depths[wallet].append(int(hop["hop_number"]))
            if results[token]["migration_delay"]:
                wallet_migrations[wallet].append(number(results[token]["migration_delay"]))
            inbound_seen[hop["destination_wallet"]] += 1
            if hop["hop_number"] == "1":
                immediate_tokens[wallet].add(token); immediate_creators[wallet].add(creator)
    for edge in persisted:
        wallet = edge["from_wallet"]
        historical_downstream[wallet].add(edge["to_wallet"])

    all_wallets = set(wallet_tokens) | {results[t]["creator"] for t in pending}
    cluster_rows = []
    for wallet in sorted(all_wallets):
        amounts, times = wallet_amounts[wallet], wallet_times[wallet]
        known = "CONFIRMED_TREASURY" if wallet in confirmed else (
            ",".join(sorted({e["entity_type"] for e in entities[wallet]})) or "UNKNOWN")
        launches, creators = len(wallet_tokens[wallet]), len(wallet_creators[wallet])
        downstream = len(shadow_downstream[wallet])
        is_immediate = bool(immediate_tokens[wallet])
        if wallet in confirmed:
            role, score = "CONFIRMED_TREASURY", "REFERENCE"
        elif known != "UNKNOWN" or is_known_account(wallet):
            role, score = "UNKNOWN_INFRASTRUCTURE", "LOW"
            known = known if known != "UNKNOWN" else "KNOWN_SERVICE_OR_EXCHANGE"
        elif launches >= 3 and downstream >= 3 and not is_immediate:
            role, score = "TREASURY_CANDIDATE", "HIGH"
        elif is_immediate and (len(immediate_tokens[wallet]) >= 2 or len(immediate_creators[wallet]) >= 2):
            role, score = "SUBPROVIDER_CANDIDATE", "HIGH" if len(immediate_creators[wallet]) >= 2 else "MEDIUM"
        elif not is_immediate and launches >= 2 and downstream >= 2:
            role, score = "PROVISIONING_HUB", "MEDIUM"
        elif launches >= 2:
            role, score = "RELAY_CANDIDATE", "LOW"
        elif launches:
            role, score = "UNKNOWN_INFRASTRUCTURE", "LOW"
        else:
            role, score = "UNKNOWN", "LOW"
        cluster_rows.append({
            "source": SOURCE, "wallet": wallet, "role_classification": role, "review_score": score,
            "launches_reached": launches, "creators_reached": creators,
            "immediate_funders_supplied": downstream if not is_immediate else 0,
            "immediate_funder_launches": len(immediate_tokens[wallet]),
            "total_observed_sol": round(sum(amounts), 9),
            "largest_observed_transfer_sol": round(max(amounts, default=0), 9),
            "average_transfer_sol": round(statistics.mean(amounts), 9) if amounts else 0,
            "transfer_variance": round(statistics.pvariance(amounts), 12) if len(amounts) > 1 else 0,
            "first_seen": min(times, default=""), "last_seen": max(times, default=""),
            "observed_lifetime_seconds": max(times)-min(times) if len(times) > 1 else 0,
            "observed_transaction_count": len(amounts),
            "historical_fanout_local": len(historical_downstream[wallet]),
            "mean_funding_cadence_seconds": round(statistics.mean(
                [b-a for a,b in zip(sorted(times), sorted(times)[1:])]), 3) if len(times) > 1 else "",
            "mean_migration_delay_seconds": round(statistics.mean(wallet_migrations[wallet]), 3)
                if wallet_migrations[wallet] else "",
            "known_entity_status": known, "operator_overlap": json.dumps(entities[wallet], sort_keys=True),
            "account_close_frequency": round(launches / len(amounts), 4) if amounts else 0,
            "watchtower_similarity": score,
        })
    cluster_fields = list(cluster_rows[0])
    write_csv(out / "x50_wallet_clusters.csv", cluster_rows, cluster_fields)
    by_wallet = {r["wallet"]: r for r in cluster_rows}

    shared = []
    for wallet, tokens in wallet_tokens.items():
        if len(tokens) < 2:
            continue
        row = by_wallet[wallet]
        shared.append({
            "source": SOURCE, "wallet": wallet, "launches_connected": len(tokens),
            "creators_connected": len(wallet_creators[wallet]),
            "immediate_funders_connected": row["immediate_funders_supplied"],
            "account_close_descendants": len(tokens), "tokens": json.dumps(sorted(tokens)),
            "role_classification": row["role_classification"], "review_score": row["review_score"],
            "known_entity_status": row["known_entity_status"],
        })
    shared.sort(key=lambda r: (-r["launches_connected"], -r["immediate_funders_connected"], r["wallet"]))
    shared_fields = list(shared[0]) if shared else ["source","wallet"]
    write_csv(out / "x50_shared_ancestors.csv", shared, shared_fields)

    # Families are connected components of launches joined by a clean repeated wallet.
    adjacency = defaultdict(set)
    for wallet, tokens in wallet_tokens.items():
        if len(tokens) > 1:
            for token in tokens:
                adjacency[token].update(tokens - {token})
    unseen, families = set(pending), []
    while unseen:
        root = min(unseen); queue = deque([root]); component = set()
        while queue:
            token = queue.popleft()
            if token in component: continue
            component.add(token); unseen.discard(token); queue.extend(adjacency[token] - component)
        family_wallets = sorted(w for w, ts in wallet_tokens.items() if len(ts & component) >= 2)
        families.append((component, family_wallets))
    families.sort(key=lambda x: (-len(x[0]), min(x[0])))
    family_rows = []
    for index, (tokens, wallets) in enumerate(families, 1):
        creators = {results[t]["creator"] for t in tokens}
        funders = {h["source_wallet"] for t in tokens for h in valid_paths[t] if h["hop_number"] == "1"}
        candidates = [w for w in wallets if by_wallet[w]["role_classification"] == "TREASURY_CANDIDATE"]
        family_rows.append({
            "source": SOURCE, "family_id": f"X50-F{index:03d}", "launch_count": len(tokens),
            "creator_count": len(creators), "immediate_funder_count": len(funders),
            "launches": json.dumps(sorted(tokens)), "creators": json.dumps(sorted(creators)),
            "immediate_funders": json.dumps(sorted(funders)), "shared_infrastructure": json.dumps(wallets),
            "candidate_treasuries": json.dumps(candidates),
            "confidence": "HIGH" if candidates else ("MEDIUM" if len(tokens) >= 2 else "LOW"),
            "evidence_summary": f"{len(tokens)} launches connected by {len(wallets)} repeated clean wallets",
        })
    family_fields = list(family_rows[0])
    write_csv(out / "x50_family_clusters.csv", family_rows, family_fields)

    role_files = {
        "TREASURY_CANDIDATE": "x50_treasury_candidates.csv",
        "SUBPROVIDER_CANDIDATE": "x50_subprovider_candidates.csv",
        "RELAY_CANDIDATE": "x50_relay_candidates.csv",
    }
    candidate_fields = cluster_fields + ["tokens", "evidence_for", "evidence_against", "recommended_action"]
    candidates_by_role = {}
    for role, filename in role_files.items():
        rows = []
        for base in cluster_rows:
            if base["role_classification"] != role: continue
            wallet = base["wallet"]
            row = dict(base, tokens=json.dumps(sorted(wallet_tokens[wallet])),
                       evidence_for=(f"Repeated clean ancestry across {base['launches_reached']} launches and "
                                     f"{base['immediate_funders_supplied']} downstream wallets"),
                       evidence_against="Root funding not recovered; observed role may be a provisioning hub rather than treasury",
                       recommended_action="REVIEW_NEW_TREASURY" if role == "TREASURY_CANDIDATE" else "REVIEW_INFRASTRUCTURE_ROLE")
            rows.append(row)
        candidates_by_role[role] = rows
        write_csv(out / filename, rows, candidate_fields)

    named_roles = set(role_files) | {"CONFIRMED_TREASURY"}
    unknown_rows = [dict(r, tokens=json.dumps(sorted(wallet_tokens[r["wallet"]])),
                         recommended_action="REVIEW_INFRASTRUCTURE_ROLE")
                    for r in cluster_rows if r["role_classification"] not in named_roles and r["launches_reached"]]
    write_csv(out / "x50_unknown_infrastructure.csv", unknown_rows,
              cluster_fields + ["tokens", "recommended_action"])

    review_rows = []
    for role in ("TREASURY_CANDIDATE", "SUBPROVIDER_CANDIDATE", "RELAY_CANDIDATE"):
        for row in candidates_by_role[role]:
            review_rows.append({
                "source": SOURCE, "wallet": row["wallet"], "role": role,
                "review_score": row["review_score"], "launches_linked": row["launches_reached"],
                "creators_linked": row["creators_reached"],
                "immediate_funders_linked": row["immediate_funders_supplied"],
                "total_sol": row["total_observed_sol"], "largest_transfer": row["largest_observed_transfer_sol"],
                "first_seen": row["first_seen"], "last_seen": row["last_seen"],
                "tokens": row["tokens"], "evidence_for": row["evidence_for"],
                "evidence_against": row["evidence_against"],
                "recommended_action": row["recommended_action"],
            })
    review_rows.sort(key=lambda r: ({"HIGH":0,"MEDIUM":1,"LOW":2}.get(r["review_score"],3), -int(r["launches_linked"]), r["wallet"]))
    review_fields = list(review_rows[0]) if review_rows else ["source","wallet"]
    write_csv(out / "x50_review_pack.csv", review_rows, review_fields)

    multi_funder_wallets = sum(len(immediate_tokens[w]) >= 2 for w in immediate_tokens)
    summary = {
        "source": SOURCE, "population": len(pending), "graph_edges": len(graph_rows),
        "clean_graph_edges": sum(int(r["edge_usable_for_scoring"]) for r in graph_rows),
        "unique_wallets": len(all_wallets), "reused_immediate_funders": multi_funder_wallets,
        "shared_ancestor_wallets": len(shared), "families": len(families),
        "multi_launch_families": sum(len(tokens) > 1 for tokens, _ in families),
        "largest_family_launches": max(len(tokens) for tokens, _ in families),
        "treasury_candidates": len(candidates_by_role["TREASURY_CANDIDATE"]),
        "subprovider_candidates": len(candidates_by_role["SUBPROVIDER_CANDIDATE"]),
        "relay_candidates": len(candidates_by_role["RELAY_CANDIDATE"]),
        "production_connections": "SQLITE_MODE_RO_QUERY_ONLY",
        "production_mutations": 0,
    }
    (out / "x50_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
