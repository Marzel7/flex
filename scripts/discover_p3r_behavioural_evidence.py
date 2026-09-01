#!/usr/bin/env python3
"""Read-only source mapping for behavioural evidence joinable to frozen P3R."""

import argparse
import hashlib
import json
import os
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


EXPECTED = {
    "corpus": "a1779e0f78f7aff8813e7ec4402073c7a6c99232fc80f0f8dcdd562a945524ce",
    "queue": "d111116fd7a1e149e8fea30498cef6c35e3de534cdefef9da78dd4223daff5c3",
    "manifest": "c5aa554ab03f64bad048815e984be737e165f88982f4da5222d65fdb87836260",
    "qualification": "b7969bce6af3c2f15a88da9ab612ef165dd3d181e7cac85b01d08c61d78bbe39",
    "evaluation": "8c5d84c26d8356f23ef28aa8b35702f96faa3414b401714a684c3e440d84e28a",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def pct(n: int, d: int) -> float | None:
    return n * 100 / d if d else None


def source_identity(path: Path) -> dict:
    stat = path.stat()
    return {"path": str(path), "size_bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns, "inode": stat.st_ino,
            "access": "sqlite_uri_mode_ro"}


def ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)


def chunks(values: list[str], size=900):
    for start in range(0, len(values), size):
        yield values[start:start + size]


def mint_rows(conn: sqlite3.Connection, table: str, mint_col: str, fields: str, mints: list[str]) -> list[tuple]:
    result = []
    for group in chunks(mints):
        marks = ",".join("?" for _ in group)
        result.extend(conn.execute(f"select {fields} from {table} where {mint_col} in ({marks})", group).fetchall())
    return result


def coverage(mints: set[str], all_mints: set[str]) -> dict:
    return {"eligible_denominator": len(all_mints), "populated_numerator": len(mints), "missing": len(all_mints - mints),
            "coverage_pct": pct(len(mints), len(all_mints))}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--namespace", type=Path, required=True)
    parser.add_argument("--qualification", type=Path, required=True)
    parser.add_argument("--evaluation", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    args = parser.parse_args()
    ns = args.namespace
    corpus, queue = ns / "p3r_historical_features.jsonl", ns / "frozen_queue.txt"
    checkpoint, manifest = ns / "p3r_historical_features.checkpoint.json", ns / "p3r_historical_features.clean_rebuild_manifest.json"
    digests = {"corpus": sha256(corpus), "queue": sha256(queue), "qualification": sha256(args.qualification), "evaluation": sha256(args.evaluation)}
    cp, mf, qualification, evaluation = (json.loads(checkpoint.read_text()), json.loads(manifest.read_text()),
                                         json.loads(args.qualification.read_text()), json.loads(args.evaluation.read_text()))
    records = [json.loads(line) for line in corpus.read_text().splitlines()]
    mints_ordered = queue.read_text().splitlines(); mints = set(mints_ordered)
    if digests != {key: EXPECTED[key] for key in digests} or cp.get("run_manifest_digest") != EXPECTED["manifest"] or mf.get("run_identity") != ns.name:
        raise SystemExit("P3R_BEHAVIOURAL_DISCOVERY_INPUT_BINDING_FAILURE")
    if len(records) != 28883 or [row["mint"] for row in records] != mints_ordered or evaluation["principal_verdict"] != "P3R_STRUCTURAL_LINEAGE_EVALUATION_COMPLETE":
        raise SystemExit("P3R_BEHAVIOURAL_DISCOVERY_POPULATION_FAILURE")
    by_mint = {row["mint"]: row for row in records}
    creators = {row["creator"] for row in records if row.get("creator")}
    direct_pairs = {(row["creator"], row["direct_funder"]) for row in records if row.get("creator") and row.get("direct_funder")}
    parent_mints = {row["mint"] for row in records if row.get("parents")}
    valid_lineage_mints = {row["mint"] for row in records if row.get("parents") and row.get("max_hop_depth") <= row["edge_count"]}
    dbs = {
        "watchtower": Path("database/wt_ops_v2.db"), "normalized": Path("database/flex_complete_database.db"),
        "transaction_first": Path("database/transaction_first_lineage.db"), "local_discovery": Path("database/local_operation_discovery_corpus.db"),
    }
    identities = {name: source_identity(path) for name, path in dbs.items()}
    wt, normal, tf, local = (ro(dbs["watchtower"]), ro(dbs["normalized"]), ro(dbs["transaction_first"]), ro(dbs["local_discovery"]))

    # Direct mint-keyed raw Watchtower candidate evidence. Rows are intentionally not collapsed.
    print("stage=watchtower_edges", flush=True)
    candidate_rows = [row for row in wt.execute("select mint,amount_lamports,block_time,signature,mechanism,hop_depth,selection_status,evidence_key from wt_walkback_edge_candidates") if row[0] in mints]
    selected = [row for row in candidate_rows if row[6] == "SELECTED"]
    selected_mints = {row[0] for row in selected}
    selected_amount_mints = {row[0] for row in selected if row[1] is not None}
    selected_time_mints = {row[0] for row in selected if row[2] is not None}
    selected_sig_mints = {row[0] for row in selected if row[3]}
    selected_mechanism_mints = {row[0] for row in selected if row[4]}
    selected_mult = Counter(row[0] for row in selected)
    atomic_rows = [row for row in wt.execute("select mint,signature,block_time,transfer_lamports,instruction_order_json,has_create,has_sync_native,has_close from wt_walkback_atomic_flows") if row[0] in mints]
    atomic_mints = {row[0] for row in atomic_rows if row[4]}
    boundary_rows = [row for row in wt.execute("select launch_mint,boundary_signature,boundary_block_time,boundary_transfer_lamports,boundary_hop_depth,boundary_status,provenance from wt_funding_boundary") if row[0] in mints]
    boundary_mints = {row[0] for row in boundary_rows if row[1] and row[2] is not None and row[3] is not None}
    birth_rows = [row for row in wt.execute("select token_mint,funded_at,launched_at,birth_to_launch_s,funding_sig,launch_sig,base_amount_sol from wt_creator_birth_launch") if row[0] in mints]
    birth_complete = {row[0] for row in birth_rows if row[1] is not None and row[2] is not None}

    # Transaction-first evidence is direct by mint for launch facts; edges use explicit launch_context=mint.
    print("stage=transaction_first", flush=True)
    # The retained transaction-first database is mapped but its indexed launch
    # lookup also exceeds the bounded source-read window on this host.
    tf_launches = []
    tf_launch_mints = set()
    # tf_edges has no launch_context index on this large retained database. Its
    # full scan exceeded the bounded local discovery window, so it is mapped as
    # non-executable rather than silently treated as zero coverage.
    tf_edges = []
    tf_edge_mints = set()

    # Locally retained operation-discovery edges are direct mint keyed but may have multi-run multiplicity.
    print("stage=local_discovery", flush=True)
    discovery_edges = [row for row in local.execute("select mint,run_id,funding_signature,amount_lamports,funding_block_time,migrated_at,gap_seconds,confidence,has_extraction_failure from direct_funding_edges") if row[0] in mints]
    discovery_mints = {row[0] for row in discovery_edges if row[2] and row[3] is not None and row[4] is not None}
    discovery_mult = Counter(row[0] for row in discovery_edges)

    # Wallet-keyed normalized evidence requires exact (creator, direct_funder) equality and is therefore separately accounted.
    print("stage=normalized_wallet_pairs", flush=True)
    inbound_rows = []
    transfer_rows = []
    # Pair batches avoid materializing every historical transfer received by an
    # observed creator. The CTE is connection-local and performs no source write.
    for group in chunks(sorted(direct_pairs), 400):
        values_sql = ",".join("(?,?)" for _ in group)
        bindings = [item for pair in group for item in pair]
        pair_cte = f"with p(creator,funder) as (values {values_sql})"
        inbound_rows.extend(normal.execute(pair_cte + " select i.creator_address,i.funder_address,i.transaction_signature,i.amount_sol,i.timestamp,i.slot,i.direction,i.source_type from creator_inbound_transfers i join p on p.creator=i.creator_address and p.funder=i.funder_address", bindings).fetchall())
    # A separate in-memory connection makes the pair relation explicit and
    # forces the read-only partial (source,destination) index as an exact lookup.
    transfer_lookup = sqlite3.connect(":memory:", uri=True)
    transfer_lookup.execute("attach database ? as src", (dbs["normalized"].resolve().as_uri() + "?mode=ro",))
    transfer_lookup.execute("create table p3r_pairs (creator text not null, funder text not null, primary key(creator,funder))")
    transfer_lookup.executemany("insert into p3r_pairs values (?,?)", sorted(direct_pairs))
    transfer_rows = transfer_lookup.execute("select t.source,t.destination,t.signature,t.amount_lamports,t.block_time,t.slot,t.transfer_type from p3r_pairs p cross join src.transfer_index t indexed by idx_transfer_source_dest where t.is_valid=1 and t.source=p.funder and t.destination=p.creator").fetchall()
    transfer_lookup.close()
    inbound_pairs = {(row[0], row[1]) for row in inbound_rows if (row[0], row[1]) in direct_pairs and row[2] and row[3] is not None and row[4] is not None}
    transfer_pairs = {(row[1], row[0]) for row in transfer_rows if (row[1], row[0]) in direct_pairs and row[2] and row[3] is not None and row[4] is not None}
    pair_to_mints = defaultdict(set)
    for row in records:
        if row.get("creator") and row.get("direct_funder"):
            pair_to_mints[(row["creator"], row["direct_funder"])].add(row["mint"])
    inbound_mints = set().union(*(pair_to_mints[pair] for pair in inbound_pairs)) if inbound_pairs else set()
    transfer_mints = set().union(*(pair_to_mints[pair] for pair in transfer_pairs)) if transfer_pairs else set()

    # Parent/creator/funder recurrence is already directly measurable from frozen P3R without address-identity inference.
    parent_counts = Counter(parent for row in records for parent in (row.get("parents") or []))
    creator_counts = Counter(row["creator"] for row in records if row.get("creator"))
    funder_counts = Counter(row["direct_funder"] for row in records if row.get("direct_funder"))
    def recurrent(counter): return {"distinct_observed": len(counter), "repeated_keys": sum(v > 1 for v in counter.values()), "rows_or_members_under_repeated_keys": sum(v for v in counter.values() if v > 1)}

    launch_times = tf_launch_mints | birth_complete
    amount_mints = selected_amount_mints | discovery_mints | inbound_mints | transfer_mints
    timing_mints = selected_time_mints | discovery_mints | inbound_mints | transfer_mints
    topology_amount = valid_lineage_mints & amount_mints
    topology_timing = valid_lineage_mints & timing_mints
    topology_amount_timing = valid_lineage_mints & amount_mints & timing_mints
    funding_launch_interval = timing_mints & launch_times
    sources = {
        "watchtower_selected_edge_candidates": {"join": "P3R.mint = wt_walkback_edge_candidates.mint; selection_status=SELECTED", "kind": "raw locally retained transaction candidate evidence", "fields": ["amount_lamports", "block_time", "signature", "mechanism", "hop_depth", "evidence_key"], "units": {"amount_lamports": "lamports", "block_time": "Unix seconds"}, "multiplicity": {"selected_rows": len(selected), "mints_with_multiple_selected_edges": sum(v > 1 for v in selected_mult.values())}, "coverage": {"edge_evidence": coverage(selected_mints,mints), "amount": coverage(selected_amount_mints,mints), "timestamp": coverage(selected_time_mints,mints), "signature": coverage(selected_sig_mints,mints), "mechanism": coverage(selected_mechanism_mints,mints)}},
        "watchtower_atomic_flows": {"join": "P3R.mint = wt_walkback_atomic_flows.mint", "kind": "raw retained WSOL instruction-sequence evidence", "fields": ["signature", "block_time", "transfer_lamports", "instruction_order_json", "has_create", "has_sync_native", "has_close"], "coverage": coverage(atomic_mints,mints), "multiplicity_rows": len(atomic_rows)},
        "watchtower_funding_boundary": {"join": "P3R.mint = wt_funding_boundary.launch_mint", "kind": "derived bounded historical walkback boundary", "fields": ["boundary_transfer_lamports", "boundary_block_time", "boundary_signature", "boundary_hop_depth", "provenance"], "coverage": coverage(boundary_mints,mints), "multiplicity_rows": len(boundary_rows)},
        "watchtower_creator_birth_launch": {"join": "P3R.mint = wt_creator_birth_launch.token_mint", "kind": "locally derived creator funding/launch fact", "fields": ["funded_at", "launched_at", "birth_to_launch_s", "funding_sig", "launch_sig", "base_amount_sol"], "coverage": coverage(birth_complete,mints), "multiplicity_rows": len(birth_rows)},
        "transaction_first_lineage": {"join": "P3R.mint = tf_launches.mint; P3R.mint = tf_edges.launch_context", "kind": "retained transaction-first normalized/reconstructed evidence", "launch_coverage": {"status":"NOT_MEASURED_BOUNDED_IO", "reason":"the retained database's indexed tf_launches lookup exceeded the 30-second bounded local-discovery window; it is not treated as zero."}, "edge_coverage": {"status":"NOT_MEASURED_BOUNDED_IO", "reason":"tf_edges has no launch_context index and its complete retained-table scan exceeded the 30-second bounded local-discovery window; it is not treated as zero."}, "edge_fields": ["signature", "block_time", "amount", "asset", "mechanism", "hop_depth", "rpc_verified"], "multiplicity_rows": {"launches":0,"edges":0}},
        "local_operation_discovery_direct_funding": {"join": "P3R.mint = direct_funding_edges.mint; rows may repeat by run_id", "kind": "normalized retained direct-funding evidence", "fields": ["funding_signature", "amount_lamports", "funding_block_time", "migrated_at", "gap_seconds", "confidence", "has_extraction_failure"], "coverage": coverage(discovery_mints,mints), "multiplicity": {"rows":len(discovery_edges),"mints_multi_run_or_multirow":sum(v>1 for v in discovery_mult.values())}},
        "normalized_creator_inbound_transfers": {"join": "P3R.(creator,direct_funder) = creator_inbound_transfers.(creator_address,funder_address)", "kind": "normalized wallet transfer; exact wallet-pair join but a pair can map to multiple P3R mints", "fields": ["transaction_signature", "amount_sol", "timestamp", "slot", "direction", "source_type"], "pair_coverage": {"eligible_pairs":len(direct_pairs),"populated_pairs":len(inbound_pairs),"coverage_pct":pct(len(inbound_pairs),len(direct_pairs))}, "mint_coverage":coverage(inbound_mints,mints), "matched_rows":len(inbound_rows)},
        "normalized_transfer_index": {"join": "P3R.(creator,direct_funder) = transfer_index.(destination,source)", "kind": "normalized transfer index; exact wallet-pair join but a pair can map to multiple P3R mints", "fields": ["signature", "amount_lamports", "block_time", "slot", "transfer_type"], "pair_coverage": {"eligible_pairs":len(direct_pairs),"populated_pairs":len(transfer_pairs),"coverage_pct":pct(len(transfer_pairs),len(direct_pairs))}, "mint_coverage":coverage(transfer_mints,mints), "matched_rows":len(transfer_rows)},
    }
    contract = {
        "executable_features": [
            {"name":"selected_edge_amount_lamports", "family":"amount-based", "source":"watchtower_selected_edge_candidates", "type":"ordered list[int]", "rule":"sort SELECTED rows by hop_depth, block_time, signature, evidence_key; retain each raw amount_lamports", "missing":"null when no selected amount", "provenance":"evidence_key and signature required", "coverage":coverage(selected_amount_mints,mints)},
            {"name":"selected_edge_block_time", "family":"temporal", "source":"watchtower_selected_edge_candidates", "type":"ordered list[int Unix seconds]", "rule":"same deterministic edge ordering; no interval without independently qualified endpoint", "missing":"null when no selected block_time", "provenance":"evidence_key and signature required", "coverage":coverage(selected_time_mints,mints)},
            {"name":"selected_edge_signature_and_mechanism", "family":"transaction-behavioural", "source":"watchtower_selected_edge_candidates", "type":"ordered list[str]", "rule":"same deterministic edge ordering", "missing":"null when absent", "provenance":"evidence_key required", "coverage":coverage(selected_sig_mints & selected_mechanism_mints,mints)},
            {"name":"atomic_wsol_instruction_sequence", "family":"transaction-behavioural", "source":"watchtower_atomic_flows", "type":"ordered JSON instruction sequence", "rule":"sort by block_time, signature; retain instruction_order_json without normalization", "missing":"null when no atomic-flow evidence", "provenance":"signature required", "coverage":coverage(atomic_mints,mints)},
            {"name":"creator_recurrence_count", "family":"recurrence-based", "source":"frozen P3R corpus", "type":"int", "rule":"count identical non-null creator across frozen rows", "missing":"null when creator null", "provenance":"corpus manifest", "coverage":coverage({r['mint'] for r in records if r.get('creator')},mints)},
            {"name":"direct_funder_recurrence_count", "family":"recurrence-based", "source":"frozen P3R corpus", "type":"int", "rule":"count identical non-null direct_funder across frozen rows", "missing":"null when direct_funder null", "provenance":"corpus manifest", "coverage":coverage({r['mint'] for r in records if r.get('direct_funder')},mints)},
            {"name":"parent_recurrence_count", "family":"hybrid", "source":"frozen P3R parents", "type":"ordered list[int]", "rule":"for each sorted parent, count recurrence across observed parent lists", "missing":"null when parents null", "provenance":"corpus manifest", "coverage":coverage(parent_mints,mints)},
        ],
        "future_candidates_not_executable": [
            {"name":"funding_to_launch_interval_seconds", "reason":"requires a qualified funding timestamp and independent launch/birth timestamp; measured overlap below is not yet source-conflict qualified."},
            {"name":"upstream_amount_ratio_and_split_signature", "reason":"multiple candidate edges are retained but completeness and causal hop ordering are not established for every path."},
            {"name":"wallet_rotation_and_cross_launch_cadence", "reason":"requires qualified wallet/launch chronology and explicit role continuity beyond address equality."},
        ],
    }
    family_status = {
        "READY_LOCAL": {"structural_and_recurrence": {"coverage":"edge_count 28883/28883; creator 26098/28883; direct_funder 14394/28883; parents 11355/28883", "basis":"frozen qualified corpus"}},
        "PARTIAL_LOCAL": {"selected_edge_amount_time_signature_mechanism": coverage(selected_mints,mints), "atomic_instruction_sequence":coverage(atomic_mints,mints), "direct_mint_funding_boundary":coverage(boundary_mints,mints), "transaction_first_launch_and_edges":{"launch":sources["transaction_first_lineage"]["launch_coverage"],"edges":sources["transaction_first_lineage"]["edge_coverage"]}, "normalized_wallet_pair_transfers":{"inbound":coverage(inbound_mints,mints),"transfer_index":coverage(transfer_mints,mints)}, "funding_to_launch_endpoint_overlap":coverage(funding_launch_interval,mints)},
        "NOT_AVAILABLE_LOCAL": {"complete_total_funding_received":"No source establishes a complete all-hop receipt set for each P3R mint.", "qualified_hop_to_hop_delay":"No path-complete, conflict-qualified timestamp sequence for every edge.", "complete_transaction_sequence":"Atomic-flow sequence evidence is partial and WSOL-specific.", "wallet_rotation_inference":"Address recurrence exists, but local sources do not establish rotation semantics without identity inference."},
    }
    hypotheses = [
        {"name":"topology_amount_association", "required_features":["edge_count","selected_edge_amount_lamports"], "available_population":coverage(topology_amount,mints), "comparison":"valid lineage rows with observed selected amounts", "supports":"amount distributions differ across topology shapes", "weakens":"distributions overlap after preserving nulls"},
        {"name":"mechanism_depth_association", "required_features":["mechanisms","max_hop_depth"], "available_population":{"eligible_denominator":len(valid_lineage_mints),"populated_numerator":len(valid_lineage_mints)}, "comparison":"11,343 valid observed-lineage rows", "supports":"mechanism combinations have differing depth distributions", "weakens":"no measured distributional separation"},
        {"name":"repeated_funder_amount_signatures", "required_features":["direct_funder_recurrence_count","selected_edge_amount_lamports"], "available_population":coverage({m for m in selected_amount_mints if by_mint[m].get('direct_funder')},mints), "comparison":"rows with direct funder and selected amount", "supports":"recurrent funders show reproducible raw-amount patterns", "weakens":"amount signatures are not stable within recurrent-funder groups"},
        {"name":"topology_amount_timing_strata", "required_features":["edge_count","selected_edge_amount_lamports","selected_edge_block_time","qualified_launch_time"], "available_population":coverage(topology_amount_timing & launch_times,mints), "comparison":"only rows with independently qualified endpoints", "supports":"descriptive strata recur", "weakens":"no stable strata after denominator discipline"},
    ]
    artifact = {"artifact_type":"P3R_BEHAVIOURAL_EVIDENCE_DISCOVERY", "discovery_version":"p3r-behavioural-evidence-discovery-v1", "run_id":"p3r-behavioural-discovery-"+ns.name, "discovered_at_utc":datetime.now(timezone.utc).isoformat(), "discovery_code":{"path":str(Path(__file__).resolve()),"sha256":sha256(Path(__file__).resolve())}, "principal_verdict":"P3R_BEHAVIOURAL_EVIDENCE_PARTIAL_LOCAL", "authoritative_bindings":{"corpus_sha256":digests['corpus'],"frozen_queue_sha256":digests['queue'],"manifest_digest":EXPECTED['manifest'],"qualification_sha256":digests['qualification'],"structural_evaluation_sha256":digests['evaluation']}, "source_identities":identities, "sources":sources, "recurrence":{"creator":recurrent(creator_counts),"direct_funder":recurrent(funder_counts),"parent":recurrent(parent_counts),"coverage":{"creator":coverage({r['mint'] for r in records if r.get('creator')},mints),"direct_funder":coverage({r['mint'] for r in records if r.get('direct_funder')},mints),"parents":coverage(parent_mints,mints)}}, "combined_eligibility":{"topology_amount":coverage(topology_amount,mints),"topology_timing":coverage(topology_timing,mints),"topology_amount_timing":coverage(topology_amount_timing,mints),"funding_launch_interval_endpoints":coverage(funding_launch_interval,mints)}, "address_vs_behaviour":{"address_dependent":["creator","direct_funder","parents","exact signatures"],"behaviour_dependent":["edge_count","max_hop_depth","amount lists","mechanism combinations","atomic instruction order","intervals only with qualified endpoints"],"hybrid":["creator recurrence","direct-funder recurrence","parent recurrence","wallet-pair transfer joins"]}, "behavioural_feature_contract":contract, "local_family_status":family_status, "candidate_hypotheses":hypotheses, "provider_gap_assessment":[{"gap":"complete per-mint all-hop amount and time lineage","why":"needed for ratios, split/consolidation, and hop-delay analysis","affected_population":28883,"future_source":"archival transaction/RPC evidence","necessary_next_stage":False},{"gap":"independent launch/birth timestamps paired to funding evidence at broad coverage","why":"needed for funding-to-launch and cadence features","affected_population":28883,"future_source":"retained/archival create transactions","necessary_next_stage":False},{"gap":"complete non-WSOL transaction sequences","why":"needed for general transaction-template comparison","affected_population":28883,"future_source":"transaction evidence mirror or archival acquisition","necessary_next_stage":False}], "new_local_behavioural_corpus_materializable_immediately":True, "scope_limits":["No identity attribution, operation assertion, scoring, prediction, trading signal, or production decision.","No provider or network calls.","No source-table writes.","Wallet-keyed joins retain many-to-many multiplicity and are not assumed causal."], "recommended_next_stage":"Materialize a separately versioned, immutable partial local behavioural feature corpus using only the executable contract, then perform descriptive hypothesis tests with feature-specific denominators."}
    args.artifact.parent.mkdir(parents=True, exist_ok=True)
    with args.artifact.open('x',encoding='utf-8') as handle:
        json.dump(artifact,handle,sort_keys=True,indent=2); handle.write('\n'); handle.flush(); os.fsync(handle.fileno())
    print(json.dumps({"artifact":str(args.artifact),"sha256":sha256(args.artifact),"verdict":artifact['principal_verdict']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
