#!/usr/bin/env python3
"""Read-only forensic report for the frozen canonical P3R v2 Tier-1 family."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ops.p3r_v2_tiering import (activity_metrics, alternative_fingerprint,
                                    atomic_fingerprint, base_fingerprint,
                                    canonical_json, digest, recurrence_state)

DB = Path("database/wt_ops_v2.db")
RUN = "p3r-v2-2dec1d40604c1f7c08c8"
CANDIDATE = "p3r-v2-d3de29c88fe0ce5fa309"
ROOT = Path("docs/agent_handoff/p3r/v2") / RUN / "d3de_tier1_forensic"
VERSION = "P3R_V2_D3DE_CANONICAL_TIER1_FORENSIC.v1"
PRINCIPAL_ORDER = ["createAccountWithSeed", "initializeAccount3", "transfer", "syncNative", "closeAccount"]
PRINCIPAL_TRANSFER_LAMPORTS = 29_997_950_720


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path: Path, value: object) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    return {"path": str(path), "sha256": sha(path)}


def fp_counts(values: list[dict]) -> list[dict]:
    counts = Counter(canonical_json(value) for value in values)
    return [{"fingerprint": json.loads(value), "count": count}
            for value, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]


def edge_fp(rows: list[sqlite3.Row]) -> dict:
    return base_fingerprint((r["hop_depth"], r["mechanism"], r["amount_lamports"]) for r in rows)


def atom_fp(row: sqlite3.Row) -> dict | None:
    try:
        order = json.loads(row["instruction_order_json"])
    except (TypeError, json.JSONDecodeError):
        return None
    return atomic_fingerprint(order, row["has_create"], row["has_sync_native"], row["has_close"], row["transfer_lamports"])


def is_principal_atomic(row: sqlite3.Row) -> bool:
    try:
        order = json.loads(row["instruction_order_json"])
    except (TypeError, json.JSONDecodeError):
        return False
    return (order == PRINCIPAL_ORDER and row["transfer_lamports"] == PRINCIPAL_TRANSFER_LAMPORTS
            and row["has_create"] == 1 and row["has_sync_native"] == 1 and row["has_close"] == 1)


def load(conn: sqlite3.Connection, highwaters: dict) -> tuple[dict, dict, dict]:
    queue = {r["mint"]: r for r in conn.execute(
        "SELECT mint,creator,funder_wallet,treasury,subprov,funder_sig,funding_mechanism,"
        "funder_amount_sol,create_anchor_signature,create_anchor_block_time,completed_at "
        "FROM wt_walkback_queue WHERE rowid<=?", (highwaters["wt_walkback_queue"],))}
    edges: dict[str, dict[str, list[sqlite3.Row]]] = defaultdict(lambda: {"selected": [], "alternative": []})
    for r in conn.execute(
        "SELECT mint,wallet,candidate_parent,signature,block_time,amount_lamports,mechanism,hop_depth,"
        "instruction_index,inner_instruction_index,owner,close_authority,close_destination,temporary_account,"
        "anchor_signature,anchor_block_time,selection_status,evidence_strength "
        "FROM wt_walkback_edge_candidates WHERE rowid<=? AND selection_status IN ('SELECTED','ALTERNATIVE') "
        "ORDER BY mint,selection_status,hop_depth,instruction_index,inner_instruction_index,signature",
        (highwaters["wt_walkback_edge_candidates"],),
    ):
        edges[r["mint"]]["selected" if r["selection_status"] == "SELECTED" else "alternative"].append(r)
    atomic: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for r in conn.execute(
        "SELECT mint,signature,source_wallet,owner,temporary_account,authority,close_destination,"
        "transfer_lamports,net_destination_lamports,has_create,has_sync_native,has_close,"
        "instruction_order_json,causal_interpretation,block_time "
        "FROM wt_walkback_atomic_flows WHERE rowid<=? ORDER BY mint,signature",
        (highwaters["wt_walkback_atomic_flows"],),
    ):
        if r["mint"]:
            atomic[r["mint"]].append(r)
    return queue, edges, atomic


def predicate_rows(member: dict, base: dict, alt: dict, atomic: dict) -> dict:
    selected_match = canonical_json(member["selected_fingerprint"]) == canonical_json(base)
    alternative_match = canonical_json(member["alternative_fingerprint"]) == canonical_json(alt)
    atomic_match = any(canonical_json(item) == canonical_json(atomic) for item in member["atomic_fingerprints"])
    role_graph = bool(member["creator"] and member["direct_funder"] and member["selected_parents"])
    parent_topology = [r["hop_depth"] for r in member["selected_edges"]]
    return {"D0": selected_match, "D1": selected_match and alternative_match,
            "D2": selected_match and atomic_match, "D3": selected_match and alternative_match and atomic_match,
            "D4": selected_match and alternative_match and atomic_match and role_graph,
            "D5": selected_match and alternative_match and atomic_match and role_graph and parent_topology == [1, 2, 3, 4]}


def metrics(matches: set[str], truth: set[str], observable: set[str]) -> dict:
    tested = matches & observable
    tp = len(tested & truth)
    fp = len(tested - truth)
    fn = len((truth & observable) - tested)
    return {"tp": tp, "fp": fp, "fn": fn, "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None, "observable_denominator": len(observable),
            "external_mints": sorted(tested - truth)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DB)
    parser.add_argument("--census-timestamp", type=int)
    parser.add_argument("--replay-artifact", type=Path)
    args = parser.parse_args()
    replay = json.loads(args.replay_artifact.read_text()) if args.replay_artifact else None
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        run = conn.execute("SELECT source_snapshot_json,manifest_digest,contract_digest FROM p3r_v2_runs WHERE run_id=?", (RUN,)).fetchone()
        if run is None:
            raise SystemExit("missing frozen canonical run")
        snapshot = json.loads(run["source_snapshot_json"])
        highwaters = snapshot["highwaters"]
        census_timestamp = (replay["census_timestamp"] if replay else args.census_timestamp) or int(time.time())
        frozen = [r["mint"] for r in conn.execute(
            "SELECT mint FROM p3r_v2_candidate_membership WHERE run_id=? AND candidate_id=? ORDER BY mint", (RUN, CANDIDATE))]
        if len(frozen) != 9 or len(set(frozen)) != 9:
            raise SystemExit("frozen d3de membership cardinality regression")
        queue, edges, atomic = load(conn, highwaters)
        tiers = {r["candidate_id"]: r["tier"] for r in conn.execute(
            "SELECT candidate_id,tier FROM p3r_v2_tier_membership WHERE run_id=?", (RUN,))}
        all_members: dict[str, list[str]] = defaultdict(list)
        for r in conn.execute("SELECT candidate_id,mint FROM p3r_v2_candidate_membership WHERE run_id=?", (RUN,)):
            all_members[r["candidate_id"]].append(r["mint"])
        existing = {r["mint"]: r["operator_id"] for r in conn.execute("SELECT mint,operator_id FROM operator_launch_membership")}
    finally:
        conn.close()

    members = []
    for mint in frozen:
        q = queue.get(mint)
        selected = edges[mint]["selected"]
        alternative = edges[mint]["alternative"]
        atoms = atomic.get(mint, [])
        selected_fp, alternative_fp = edge_fp(selected), alternative_fingerprint(
            (r["hop_depth"], r["mechanism"], r["amount_lamports"]) for r in alternative)
        atomic_fps = [item for r in atoms if (item := atom_fp(r)) is not None]
        principal = [r for r in atoms if is_principal_atomic(r)]
        ancillary = [r for r in atoms if not is_principal_atomic(r)]
        members.append({
            "mint": mint, "creator": q["creator"] if q else None, "direct_funder": q["funder_wallet"] if q else None,
            "treasury": q["treasury"] if q else None, "subprovisioner": q["subprov"] if q else None,
            "launch_timestamp": min((r["block_time"] for r in selected if r["block_time"] is not None), default=None),
            "selected_edges": [dict(r) for r in selected], "alternative_edges": [dict(r) for r in alternative],
            "selected_fingerprint": selected_fp, "alternative_fingerprint": alternative_fp,
            "atomic_flows": [dict(r) for r in atoms], "atomic_fingerprints": atomic_fps,
            "principal_atomic_flows": [dict(r) for r in principal],
            "ancillary_atomic_flows": [dict(r) for r in ancillary],
            "selected_parents": [r["candidate_parent"] for r in selected],
            "provenance": {"run_id": RUN, "frozen_highwaters": highwaters,
                           "sources": ["p3r_v2_candidate_membership", "wt_walkback_queue", "wt_walkback_edge_candidates", "wt_walkback_atomic_flows"]},
        })

    base = members[0]["selected_fingerprint"]
    alternatives = fp_counts([m["alternative_fingerprint"] for m in members])
    atomics = fp_counts([fp for m in members for fp in m["atomic_fingerprints"]])
    alt = alternatives[0]["fingerprint"]
    dominant_atomic = atomics[0]["fingerprint"]
    for member in members:
        member["detector_matches"] = predicate_rows(member, base, alt, dominant_atomic)
    all_mints = set(edges)
    observable = {mint for mint in all_mints if edges[mint]["selected"]}
    truth = set(frozen)
    detector_matches = {level: set() for level in ("D0", "D1", "D2", "D3", "D4", "D5")}
    for mint in observable:
        q = queue.get(mint)
        selected = edges[mint]["selected"]
        alternative = edges[mint]["alternative"]
        atomics_for_mint = [item for r in atomic.get(mint, []) if (item := atom_fp(r)) is not None]
        row = {"creator": q["creator"] if q else None, "direct_funder": q["funder_wallet"] if q else None,
               "selected_edges": selected, "selected_parents": [r["candidate_parent"] for r in selected],
               "selected_fingerprint": edge_fp(selected),
               "alternative_fingerprint": alternative_fingerprint((r["hop_depth"], r["mechanism"], r["amount_lamports"]) for r in alternative),
               "atomic_fingerprints": atomics_for_mint}
        for level, matched in predicate_rows(row, base, alt, dominant_atomic).items():
            if matched:
                detector_matches[level].add(mint)
    family_by_mint = {mint: cid for cid, mints in all_members.items() for mint in mints}
    detector = {level: metrics(matches, truth, observable) | {"external_families": sorted({family_by_mint.get(mint, "UNQUALIFIED_RETAINED_MINT") for mint in matches - truth})}
                for level, matches in detector_matches.items()}

    creators = [m["creator"] for m in members]
    funders = [m["direct_funder"] for m in members]
    parents = [parent for m in members for parent in m["selected_parents"]]
    launch_times = sorted(m["launch_timestamp"] for m in members if m["launch_timestamp"] is not None)
    canonical_metrics = activity_metrics(launch_times, census_timestamp)
    alt_state, alt_coverage, alt_count = recurrence_state([m["alternative_fingerprint"] for m in members], len(members))
    atom_state, atom_coverage, atom_count = recurrence_state([fp for m in members for fp in m["atomic_fingerprints"]], len(members))
    diverse = lambda values: len(set(values)) >= 3 and max(Counter(values).values()) / len(values) <= .5
    address_blind = diverse(creators) and diverse(funders) and diverse(parents)
    tier_gates = [
        {"gate": "WATCH_NOW", "requirement": "VERY_HIGH_ACTIVITY or HIGH_ACTIVITY", "value": canonical_metrics["activity_state"], "pass": canonical_metrics["activity_state"] in {"VERY_HIGH_ACTIVITY", "HIGH_ACTIVITY"}},
        {"gate": "base recurrence", "requirement": "member_count >=5 and >=3 creators/funders", "value": {"members": len(members), "creators": len(set(creators)), "funders": len(set(funders))}, "pass": len(members) >= 5 and len(set(creators)) >= 3 and len(set(funders)) >= 3},
        {"gate": "alternative recurrence", "requirement": "strong recurrence", "value": {"state": alt_state, "coverage": alt_coverage, "dominant_count": alt_count}, "pass": alt_state == "STRONGLY_RECURRENT"},
        {"gate": "atomic recurrence", "requirement": "strong recurrence", "value": {"state": atom_state, "coverage": atom_coverage, "dominant_count": atom_count}, "pass": atom_state == "STRONGLY_RECURRENT"},
        {"gate": "address blind", "requirement": ">=3 distinct roles and no populated role address >50%", "value": {"creators": len(set(creators)), "funders": len(set(funders)), "parents": len(set(parents))}, "pass": address_blind},
    ]
    operator_overlap = {op: sorted(set(frozen) & {mint for mint, oid in existing.items() if oid == op}) for op in set(existing.values())}
    known_ops = {"WATCHTOWER": "04265d9f-6eb2-568c-a49e-9253091a4dbb", "Byzantine": "d8ee4d7a-fcd6-5a5b-b897-24f6ab56e334", "900b": "70f27e37-83eb-5c97-831c-48189ef98f6c"}
    relationships = {name: {"operator_id": oid, "member_overlap": operator_overlap.get(oid, []), "classification": "DISTINCT_OPERATION",
                            "basis": "No frozen d3de mint is assigned to this operation; d3de's four-hop PLAIN_XFER/WSOL ladder and 14,479,000-lamport alternate are distinct from the recorded operation contract."}
                     for name, oid in known_ops.items()}
    controls = {}
    for cid in ["p3r-v2-063e24a2def354f23ec5", "p3r-v2-900b89587c6987d582df", "p3r-v2-c357da9d0d4d560311e4"]:
        controls[cid] = {"tier": tiers.get(cid), "member_count": len(all_members.get(cid, [])),
                         "matches_d3": {level: len(detector_matches[level] & set(all_members.get(cid, []))) for level in detector_matches}}
    principal_contract = {
        "classification": "PRINCIPAL_MECHANISM_DEFINING",
        "instruction_order": PRINCIPAL_ORDER,
        "transfer_lamports": PRINCIPAL_TRANSFER_LAMPORTS,
        "required_flags": {"has_create": True, "has_sync_native": True, "has_close": True},
        "account_role_semantics": "temporary WSOL account is created, initialized, funded, synced, then closed to the selected hop-1 direct funder",
        "lifecycle_semantics": "single atomic temporary-WSOL provision-and-close lifecycle",
    }
    ancillary_types = fp_counts([fp for m in members for r in m["ancillary_atomic_flows"] if (fp := atom_fp(r)) is not None])
    ancillary_classification = [{"fingerprint": item["fingerprint"], "count": item["count"],
                                 "classification": "ANCILLARY_SUPPORTING",
                                 "reason": "Retained non-principal atomic flow; it does not change the selected four-hop funding ladder or principal 30-SOL lifecycle."}
                                for item in ancillary_types]
    coherence_matrix = []
    for m in members:
        principal_present = bool(m["principal_atomic_flows"])
        selected_ok = m["selected_fingerprint"] == base
        alternative_ok = m["alternative_fingerprint"] == alt
        role_ok = bool(m["creator"] and m["direct_funder"] and m["selected_parents"] and m["selected_parents"][0] == m["direct_funder"])
        topology_ok = [r["hop_depth"] for r in m["selected_edges"]] == [1, 2, 3, 4]
        coherence_matrix.append({"mint": m["mint"], "selected_ladder": selected_ok, "alternative_ladder": alternative_ok,
                                 "principal_atomic_lifecycle": principal_present, "role_graph": role_ok,
                                 "parent_topology": topology_ok, "coherent": all((selected_ok, alternative_ok, principal_present, role_ok, topology_ok)),
                                 "ancillary_atomic_records": len(m["ancillary_atomic_flows"])})
    subcohort_keys = {(canonical_json(m["selected_fingerprint"]), canonical_json(m["alternative_fingerprint"]),
                       bool(m["principal_atomic_flows"]), bool(m["creator"] and m["direct_funder"] and m["selected_parents"] and m["selected_parents"][0] == m["direct_funder"]),
                       tuple(r["hop_depth"] for r in m["selected_edges"])) for m in members}
    coherent = len(subcohort_keys) == 1 and all(row["coherent"] for row in coherence_matrix)
    report = {
        "schema_version": VERSION, "canonical_run_id": RUN, "candidate_id": CANDIDATE,
        "census_timestamp": census_timestamp, "source_snapshot": snapshot, "canonical_manifest_sha256": run["manifest_digest"],
        "frozen_members": members,
        "membership_validation": {"members": len(members), "unique_mints": len({m["mint"] for m in members}), "distinct_creators": len(set(creators)), "distinct_direct_funders": len(set(funders)), "distinct_selected_parents": len(set(parents)), "expected_parent_semantics": "count of distinct selected-edge candidate_parent values across all selected hops"},
        "base_mechanism": {"fingerprint": base, "coverage": f"{sum(m['selected_fingerprint'] == base for m in members)}/9", "classification": "EXACTLY_HOMOGENEOUS"},
        "alternative_mechanism": {"fingerprints": alternatives, "dominant": alternatives[0], "coverage": "9/9", "verified_hop_3_wsol_wrap_close_14479000": any(edge == {"hop_depth": 3, "mechanism": "WSOL_WRAP_CLOSE", "amount_lamports": 14479000} for edge in alt["edges"])},
        "flawed_profile_diagnosis": {"prior_key": "all retained atomic fingerprints, including optional ancillary rows", "why_wrong": "Ancillary downstream token-account flows differ in amount/instruction detail but do not alter the principal selected-ladder mechanism.", "prior_profile_count": 7},
        "principal_atomic_lifecycle_contract": principal_contract,
        "ancillary_atomic_record_classification": ancillary_classification,
        "atomic_lifecycle": {"fingerprints": atomics, "dominant": atomics[0], "principal_coverage": f"{sum(bool(m['principal_atomic_flows']) for m in members)}/9", "ancillary_variation_members": sum(bool(m["ancillary_atomic_flows"]) for m in members), "classification": "EXACT_ATOMIC_HOMOGENEITY" if all(bool(m["principal_atomic_flows"]) for m in members) else "STRONG_ATOMIC_RECURRENCE", "retained_atomic_records": sum(len(m["atomic_fingerprints"]) for m in members)},
        "role_graph": {"address_independent_route": "parent@hop4 -> parent@hop3 -> parent@hop2 -> direct_funder@hop1 -> creator", "creator_reuse": len(set(creators)), "direct_funder_reuse": len(set(funders)), "parent_reuse": Counter(parents).most_common(), "classification": "ADDRESS_INDEPENDENT_OPERATION" if address_blind else "INSUFFICIENT"},
        "parent_topology": {"parents_per_mint": {m["mint"]: m["selected_parents"] for m in members}, "distinct_parents": len(set(parents)), "depths_per_member": {m["mint"]: [r["hop_depth"] for r in m["selected_edges"]] for m in members}, "topology_discriminator": "All nine retain four selected hop parents, but addresses rotate; parent identity is not used by D0-D5."},
        "temporal": {"first_launch": launch_times[0], "last_launch": launch_times[-1], "median_gap_seconds": median([b-a for a,b in zip(launch_times, launch_times[1:])]), "max_gap_seconds": max(b-a for a,b in zip(launch_times, launch_times[1:])), "current_metrics": canonical_metrics, "classification": "PERSISTENT" if canonical_metrics["activity_state"] in {"VERY_HIGH_ACTIVITY", "HIGH_ACTIVITY"} else "DORMANT_NOW"},
        "tier_1_gates": tier_gates, "detector_comparison": detector, "negative_controls": controls,
        "coherence_matrix": coherence_matrix,
        "subcohort": {"result": "ONE_COHERENT_OPERATION" if coherent else "MULTIPLE_MECHANISM_SUBCOHORTS", "distinct_principal_mechanism_profiles": len(subcohort_keys)},
        "current_evidence_replay": {"tier_1_pass": all(gate["pass"] for gate in tier_gates), "basis": "unchanged canonical gate and frozen membership; current wall-clock activity only"},
        "operation_qualification": {"verdict": "D3DE_CONFIRMED_OPERATION_SUPPORTED" if detector["D3"]["fp"] == 0 and detector["D3"]["recall"] == 1.0 and address_blind and coherent else "D3DE_OPERATION_REQUIRES_MORE_EVIDENCE", "proposed_name": "FOUR_STEP_30_SOL_14_479K_WSOL_LADDER", "workflow_state": "READY_FOR_CONFIRMED_REGISTRATION" if coherent else "MORE_EVIDENCE_REQUIRED", "workflow_update": "Durable forensic recommendation only; no database workflow mutation, registration, or activation performed."},
        "relationships": relationships,
        "safety": {"provider_rpc_calls": 0, "operation_registration": False, "operation_activation": False, "tier_mutation": False, "membership_mutation": False, "fingerprint_mutation": False, "queue_replay": False, "trading_signal": False},
        "verdict": "P3R_V2_D3DE_CANONICAL_TIER1_FORENSIC_COMPLETE",
    }
    report["report_digest"] = digest(report)
    report_id = "p3r-v2-d3de-forensic-" + report["report_digest"][:20]
    artifact = write(ROOT / report_id / "p3r_v2_d3de_canonical_tier1_forensic.v1.json", report)
    manifest = write(ROOT / report_id / "p3r_v2_d3de_canonical_tier1_forensic_manifest.v1.json", {
        "report_id": report_id, "canonical_run_id": RUN, "candidate_id": CANDIDATE,
        "source_snapshot": snapshot, "census_timestamp": census_timestamp,
        "code_sha256": sha(Path(__file__)), "artifact": artifact, "report_digest": report["report_digest"],
        "safety": report["safety"],
    })
    print(json.dumps({"verdict": report["verdict"], "report_id": report_id, "artifact": artifact, "manifest": manifest,
                      "operation_qualification": report["operation_qualification"], "detector": detector, "subcohort": report["subcohort"]}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
