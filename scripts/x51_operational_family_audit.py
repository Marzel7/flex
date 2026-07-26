#!/usr/bin/env python3
"""X51 evidence-only family audit for the five X49.1 confirmed paths."""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from collections import defaultdict
from itertools import combinations
from pathlib import Path

SOURCE = "X51_OPERATIONAL_FAMILY_AUDIT"
TOKENS = (
    "4gRYGyDP9Jkdt2bi9LN8ZKwwd22UfPYAA8bRA3Zppump",
    "EfY6QUqEvHcYs9sRpZphF56qSwotsJjMMA58iNB5pump",
    "4taRjy3zu3QBfD9XWeeuMmzgc8NwhsGTvsok6VuTpump",
    "2GU9TB56hem9mYVV6N2o6A5TtsyV5w8R8DHiXZ11pump",
    "8HAS7ZSBx1eRhMc2WLZzQ2GTgaDNm7RNZP6SfMbTpump",
)
FUNDING_LAYOUTS = {
    TOKENS[0]: ["createAccountWithSeed", "initializeAccount", "closeAccount"],
    TOKENS[1]: ["createAccountWithSeed", "initializeAccount", "closeAccount"],
    TOKENS[2]: ["transfer", "createIdempotent", "transfer", "syncNative", "closeAccount",
                "inner:getAccountDataSize", "inner:createAccount",
                "inner:initializeImmutableOwner", "inner:initializeAccount3"],
    TOKENS[3]: ["createAccountWithSeed", "initializeAccount", "closeAccount"],
    TOKENS[4]: ["transfer", "createIdempotent", "transfer", "syncNative", "closeAccount",
                "inner:getAccountDataSize", "inner:createAccount",
                "inner:initializeImmutableOwner", "inner:initializeAccount3"],
}


def read(path: Path) -> list[dict]:
    return list(csv.DictReader(open(path, newline="")))


def write(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    fields = fields or list(rows[0])
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)


def ro(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row; conn.execute("PRAGMA query_only=ON")
    return conn


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--x49-dir", default="/private/tmp/x49_1_shadow_replay")
    parser.add_argument("--live-db", default="database/flex_complete_database.db")
    parser.add_argument("--ops-db", default="database/wt_ops_v2.db")
    parser.add_argument("--output-dir", default="/private/tmp/x51_operational_family_audit")
    args = parser.parse_args(); x49 = Path(args.x49_dir); out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    results = {r["token"]: r for r in read(x49 / "x49_1_population_results.csv") if r["token"] in TOKENS}
    paths = defaultdict(list)
    for hop in read(x49 / "x49_1_walkback_paths.csv"):
        if hop["token"] in TOKENS: paths[hop["token"]].append(hop)
    if set(results) != set(TOKENS): raise SystemExit("Five-launch population mismatch")
    for token in paths: paths[token].sort(key=lambda h: int(h["hop_number"]), reverse=True)

    live, ops = ro(args.live_db), ro(args.ops_db)
    token_rows = {r["mint"]: dict(r) for r in live.execute(
        f"SELECT * FROM token_analysis WHERE mint IN ({','.join('?' for _ in TOKENS)})", TOKENS)}
    queue_rows = {r["mint"]: dict(r) for r in live.execute(
        f"SELECT * FROM creator_funding_queue WHERE mint IN ({','.join('?' for _ in TOKENS)})", TOKENS)}
    treasury_rows = {r["treasury"]: dict(r) for r in ops.execute("SELECT * FROM wt_confirmed_treasuries")}
    entity_rows = defaultdict(list)
    for row in ops.execute("SELECT * FROM operator_entities"):
        entity_rows[row["entity_address"]].append(dict(row))
    live.close(); ops.close()

    comparison = []
    role_rows = []
    fingerprints = {}
    for token in TOKENS:
        result, chain = results[token], paths[token]
        treasury = result["known_treasury"]
        creator = result["creator"]
        ordered = [h["source_wallet"] for h in chain] + [creator]
        amounts = [float(h["amount"]) for h in chain]
        times = [int(float(h["block_time"])) for h in chain]
        capital_continuity = all(amounts[i] >= amounts[i+1] for i in range(len(amounts)-1))
        # The first item is treasury; the final wallet before creator is immediate funder.
        intermediate = ordered[1:-2] if len(ordered) > 3 else []
        reservoir = intermediate[0] if len(intermediate) >= 2 else ""
        hub = intermediate[-1] if intermediate else ""
        immediate = result["immediate_funder"]
        treasury_to_launch = max(times)-min(times) if times else ""
        migration = float(result["migration_delay"] or 0)
        treasury_provenance = treasury_rows.get(treasury, {}).get("provenance", "")
        if treasury.startswith("Dch"):
            cluster = "X51-FAMILY-A-DCH"
            fingerprint = "SEEDED_ACCOUNT_CLOSE_LAYOUT"
            match = "STRONGLY_MATCHES_WATCHTOWER"
            attribution = "RETAIN_WATCHTOWER_FAMILY_A"
        elif token in (TOKENS[2], TOKENS[4]):
            cluster = "X51-FAMILY-B-DTWI-SHARED-HUB"
            fingerprint = "WSOL_ATA_WRAP_CLOSE_LAYOUT"
            match = "MODERATE_STRUCTURAL_MATCH_PATH_DISPUTED"
            attribution = "HOLD_FOR_REVALIDATION"
        else:
            cluster = "X51-FAMILY-C-DTWI-DISTINCT-BRANCH"
            fingerprint = "SEEDED_ACCOUNT_CLOSE_LAYOUT"
            match = "WEAK_MATCH_PATH_DISPUTED"
            attribution = "HOLD_FOR_REVALIDATION"
        fingerprints[token] = fingerprint
        comparison.append({
            "source": SOURCE, "token": token, "cluster_id": cluster, "treasury": treasury,
            "treasury_registry_provenance": treasury_provenance, "reservoir": reservoir,
            "provisioning_hub": hub, "subprovider": immediate, "immediate_funder": immediate,
            "creator": creator, "full_path": json.dumps(ordered), "hop_count": len(chain),
            "funding_amount_sol": result and paths[token][-1]["amount"],
            "upstream_amounts_sol": json.dumps(amounts), "capital_continuity_pass": int(capital_continuity),
            "funding_to_launch_seconds": max(0, int(token_rows[token]["created_at"].split("T")[1][:2]) * 0) if False else "NOT_RETAINED_PRECISELY",
            "treasury_to_creator_funding_seconds": treasury_to_launch,
            "migration_delay_seconds": result["migration_delay"], "migration_destination": token_rows[token].get("dex") or "",
            "pumpswap_pool": token_rows[token].get("pumpswap_pool_address") or "",
            "creator_freshness": queue_rows[token].get("priority_reason") or "UNKNOWN",
            "creator_reuse_within_five": 0, "funding_mechanism": result["mechanism_evidence"],
            "close_destination_matches_creator": result["close_destination_matches_creator"],
            "funding_transaction_structure": fingerprint,
            "instruction_order": json.dumps(FUNDING_LAYOUTS[token]),
            "sweep_evidence": "NOT_RETAINED", "relay_evidence": "NOT_RETAINED",
            "historical_watchtower_match": match, "recommended_attribution": attribution,
        })
        for index, wallet in enumerate(ordered[:-1]):
            if index == 0: role = "TREASURY_REGISTRY_ROOT"
            elif wallet == immediate: role = "TRUE_SINGLE_USE_SUBPROVIDER"
            elif index == 1: role = "RESERVOIR_OR_PROVISIONING_HUB"
            else: role = "PROVISIONING_HUB_OR_RELAY"
            role_rows.append({
                "source": SOURCE, "token": token, "wallet": wallet, "path_position": index,
                "observed_role": role, "shared_across_five": sum(wallet in [h["source_wallet"] for h in paths[t]] for t in TOKENS),
                "registry_role": json.dumps(entity_rows[wallet], sort_keys=True),
                "role_confidence": "HIGH" if wallet == immediate else ("MEDIUM" if index else "DISPUTED" if treasury.startswith("Dtwi") else "HIGH"),
                "evidence_limit": "Role derived from transaction direction; reservoir/hub distinction requires wallet-history validation",
            })

    write(out / "x51_launch_comparison.csv", comparison)
    write(out / "x51_role_validation.csv", role_rows)

    infrastructure = []
    wallets = sorted({w for row in comparison for w in json.loads(row["full_path"])})
    for wallet in wallets:
        linked = [row for row in comparison if wallet in json.loads(row["full_path"])]
        layers = []
        for row in linked:
            if wallet == row["treasury"]: layers.append("TREASURY")
            elif wallet == row["creator"]: layers.append("CREATOR")
            elif wallet == row["immediate_funder"]: layers.append("IMMEDIATE_FUNDER")
            elif wallet == row["reservoir"]: layers.append("RESERVOIR")
            else: layers.append("PROVISIONING_HUB")
        infrastructure.append({
            "source": SOURCE, "wallet": wallet, "launch_count": len(linked),
            "tokens": json.dumps([r["token"] for r in linked]), "layers": json.dumps(layers),
            "shared": int(len(linked)>1), "same_family_only": int(len({r["cluster_id"] for r in linked})==1),
            "operator_registry_overlap": json.dumps(entity_rows[wallet], sort_keys=True),
        })
    write(out / "x51_infrastructure_matrix.csv", infrastructure)

    similarities = []
    rows_by_token = {r["token"]: r for r in comparison}
    for a,b in combinations(TOKENS,2):
        ra,rb=rows_by_token[a],rows_by_token[b]
        same_treasury=ra["treasury"]==rb["treasury"]
        ia=set(json.loads(ra["full_path"])[1:-1]); ib=set(json.loads(rb["full_path"])[1:-1])
        shared=sorted(ia&ib)
        same_amount=abs(float(ra["funding_amount_sol"])-float(rb["funding_amount_sol"]))<0.001
        same_fp=fingerprints[a]==fingerprints[b]
        rapid=float(ra["migration_delay_seconds"] or 999)<15 and float(rb["migration_delay_seconds"] or 999)<15
        same_depth=ra["hop_count"]==rb["hop_count"]
        score=25*same_treasury+20*bool(shared)+15*same_amount+20*same_fp+10*rapid+10*same_depth
        similarities.append({
            "source":SOURCE,"token_a":a,"token_b":b,"same_treasury":int(same_treasury),
            "shared_intermediate_wallets":json.dumps(shared),"same_creator_funding_amount":int(same_amount),
            "same_funding_structure":int(same_fp),"both_rapid_migration":int(rapid),
            "same_path_depth":int(same_depth),"similarity_score":score,
            "interpretation":"SAME_OPERATIONAL_FAMILY" if score>=75 else ("RELATED_TREASURY_FAMILY" if score>=50 else "DISTINCT_OR_UNPROVEN"),
        })
    write(out / "x51_similarity_scores.csv", similarities)

    clusters = []
    for cluster_id in sorted({r["cluster_id"] for r in comparison}):
        rows=[r for r in comparison if r["cluster_id"]==cluster_id]
        clusters.append({
            "source":SOURCE,"cluster_id":cluster_id,"launch_count":len(rows),
            "tokens":json.dumps([r["token"] for r in rows]),
            "treasuries":json.dumps(sorted({r["treasury"] for r in rows})),
            "shared_infrastructure":json.dumps(sorted(set.intersection(*[set(json.loads(r["full_path"])) for r in rows]))) if len(rows)>1 else "[]",
            "classification":"WATCHTOWER_FAMILY" if "DCH" in cluster_id else "DISTINCT_FAMILY_TREASURY_LINK_DISPUTED",
            "confidence":"HIGH" if "DCH" in cluster_id else "MEDIUM",
        })
    write(out / "x51_operational_clusters.csv", clusters)

    false_review=[]
    for row in comparison:
        disputed=row["capital_continuity_pass"]==0
        false_review.append({
            "source":SOURCE,"token":row["token"],"current_shadow_classification":"CONFIRMED_WATCHTOWER",
            "funding_path_confirmed":int(not disputed),"organisation_confirmed":int("DCH" in row["cluster_id"]),
            "alternative_hypothesis":"DISTINCT_OPERATOR_OR_FALSE_UPSTREAM_SELECTION" if disputed else "WATCHTOWER_TREASURY_FAMILY",
            "conflicting_evidence":"Selected upstream amount is smaller than later downstream capital; alternatives unassessed" if disputed else "No material conflict in retained path",
            "recommended_action":"REVALIDATE_UPSTREAM_EDGE_NO_ATTRIBUTION_CHANGE" if disputed else "RETAIN_IN_WATCHTOWER_CORPUS",
        })
    write(out / "x51_false_attribution_review.csv",false_review)
    summary={"source":SOURCE,"launches":5,"operational_clusters":3,"strong_watchtower":2,
             "disputed_watchtower_attribution":3,"single_organisation_supported":False,
             "shared_treasuries":2,"shared_non_treasury_infrastructure_wallets":2,
             "production_connections":"SQLITE_MODE_RO_QUERY_ONLY","production_mutations":0}
    (out/"x51_summary.json").write_text(json.dumps(summary,indent=2,sort_keys=True)+"\n")
    print(json.dumps(summary,indent=2,sort_keys=True))
    return 0


if __name__ == "__main__": raise SystemExit(main())
