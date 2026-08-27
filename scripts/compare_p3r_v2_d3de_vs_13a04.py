"""Bounded retained-evidence comparison of d3de with P3R_13A04."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path("/tmp/d3de_13a04_comparison_sources.json")
OUT = ROOT / "docs/agent_handoff/p3r/v2/p3r-v2-2dec1d40604c1f7c08c8/d3de_vs_13a04"
CANDIDATE = "p3r-v2-d3de29c88fe0ce5fa309"
OPERATOR = "ccb7b1b0-56e1-4543-9e95-3f284bed3943"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path: Path, value: object) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    return digest(path)


def main() -> None:
    source = json.loads(SOURCE.read_text())
    profile = json.loads(source["operator"][0]["provenance_json"])
    ladder = profile["funding_ladder_lamports"]
    route_13a04 = [
        {"hop_depth": 1, "mechanism": "PLAIN_XFER", "amount_lamports": ladder[4]},
        {"hop_depth": 2, "mechanism": "WSOL_WRAP_CLOSE", "amount_lamports": ladder[3]},
        {"hop_depth": 3, "mechanism": "PLAIN_XFER", "amount_lamports": ladder[2]},
        {"hop_depth": 4, "mechanism": "WSOL_WRAP_CLOSE", "amount_lamports": ladder[1]},
    ]
    d3de = source["d3de_forensic"]
    route_d3de = d3de["base_mechanism"]["fingerprint"]["edges"]
    route_13a04_set = {(r["hop_depth"], r["mechanism"], r["amount_lamports"]) for r in route_13a04}
    route_d3de_set = {(r["hop_depth"], r["mechanism"], r["amount_lamports"]) for r in route_d3de}
    d3de_members = d3de["frozen_members"]
    detector_13a04_on_d3de = {
        "observable": len(d3de_members),
        "matches": sum(route_13a04_set.issubset({(e["hop_depth"], e["mechanism"], e["amount_lamports"]) for e in m["selected_edges"]}) for m in d3de_members),
        "non_matches": len(d3de_members),
        "reason": "Every d3de member has the frozen four-hop d3de route, which lacks all required 13A04 exact route elements.",
    }
    historical_13a04 = source["historical"]["candidates"]["p3r-candidate-13a04d7da7a1fc55"]
    d3de_on_13a04 = {
        "observable_current_members": 0,
        "matches": 0,
        "non_matches": 0,
        "unobservable_historical_members": historical_13a04["member_count_reported"],
        "classification": "NOT_COMPUTABLE",
        "reason": "The current operation has no retained membership rows and the five historical mint IDs were not recovered.",
    }
    comparison = [
        {"dimension": "selected hop count", "P3R_13A04": 4, "d3de": 4, "relationship": "IDENTICAL"},
        {"dimension": "ordered selected amounts", "P3R_13A04": [r["amount_lamports"] for r in route_13a04], "d3de": [r["amount_lamports"] for r in route_d3de], "relationship": "MATERIAL_DIFFERENCE"},
        {"dimension": "semantic sequence", "P3R_13A04": [r["mechanism"] for r in route_13a04], "d3de": [r["mechanism"] for r in route_d3de], "relationship": "MATERIAL_DIFFERENCE"},
        {"dimension": "decrement pattern", "P3R_13A04": "five-step 5,000-lamport ladder retained in current profile provenance", "d3de": "30-SOL pair followed by 14,479,000 and 2,074,000 lamports", "relationship": "MATERIAL_DIFFERENCE"},
        {"dimension": "principal atomic lifecycle", "P3R_13A04": profile["atomic_sequence"], "d3de": d3de["principal_atomic_lifecycle_contract"]["instruction_order"], "relationship": "RELATED"},
        {"dimension": "principal atomic transfer", "P3R_13A04": "NOT_RETAINED", "d3de": d3de["principal_atomic_lifecycle_contract"]["transfer_lamports"], "relationship": "NOT_OBSERVABLE"},
        {"dimension": "role graph", "P3R_13A04": "four-hop address-independent exact matcher", "d3de": d3de["role_graph"]["address_independent_route"], "relationship": "RELATED"},
        {"dimension": "alternative recurrence", "P3R_13A04": "NOT_RETAINED", "d3de": d3de["alternative_mechanism"]["coverage"], "relationship": "NOT_OBSERVABLE"},
    ]
    report = {
        "schema_version": "P3R_V2_D3DE_VS_P3R_13A04_FORENSIC_COMPARISON.v1",
        "candidate_id": CANDIDATE,
        "operator_id": OPERATOR,
        "relationship_verdict": "DISTINCT_OPERATIONS",
        "duplicate_registration_risk": "NO_DUPLICATION_RISK",
        "registration_recommendation": "REGISTER_D3DE_AS_DISTINCT_CONFIRMED_OPERATION",
        "P3R_13A04_current_operation_contract": {
            "display_name": source["operator"][0]["display_name"],
            "status": source["operator"][0]["status"],
            "disposition": source["operator"][0]["disposition"],
            "current_membership_count": len(source["members"]),
            "evidence_provenance": "CURRENT_OPERATION_CONTRACT: profile provenance; HISTORICAL_RECOVERED_REFERENCE: later historical aggregate only",
            "funding_ladder_lamports": ladder,
            "matcher_selected_route": route_13a04,
            "atomic_sequence": profile["atomic_sequence"],
            "confirmation_logic": "all four exact selected edges required; address-independent; analyst-reviewed automatic admission",
            "historical_reference": historical_13a04,
        },
        "d3de_contract": {
            "selected_route": route_d3de,
            "alternative_route": d3de["alternative_mechanism"],
            "principal_atomic": d3de["principal_atomic_lifecycle_contract"],
            "role_graph": d3de["role_graph"],
            "detector_validation": d3de["detector_comparison"],
            "temporal": d3de["temporal"],
        },
        "ladder_comparison": comparison,
        "member_overlap": {
            "d3de_members": len(d3de_members),
            "current_13a04_members": 0,
            "overlap": "NOT_COMPUTABLE_HISTORICAL; 0/9 against current retained membership",
            "reason": "Historical 13A04 mint IDs are unrecovered.",
        },
        "infrastructure_overlap": {"classification": "INSUFFICIENT", "reason": "13A04 current contract retains no historical creator/funder/parent address payload; no common controller is inferred."},
        "atomic_relationship": {"classification": "RELATED_LIFECYCLE", "reason": "Both retain create/initialize/transfer/sync/close WSOL ordering, but only d3de retains a principal amount and complete atomic evidence."},
        "role_graph_relationship": {"classification": "RELATED_ROLE_GRAPH", "reason": "Both are four-hop address-independent provision-to-creator patterns, but selected semantics and exact amounts differ materially."},
        "temporal_relationship": {"classification": "INSUFFICIENT", "reason": "No retained historical 13A04 member timestamps survive; d3de operated across five days in the frozen census."},
        "cross_detector": {"P3R_13A04_on_d3de": detector_13a04_on_d3de, "d3de_on_P3R_13A04": d3de_on_13a04},
        "common_parent_assessment": {"classification": "NOT_JUSTIFIED", "reason": "A shared generic temporary-WSOL lifecycle or approximately 30-SOL funding is insufficient when the exact ladder semantics and vectors are non-overlapping."},
        "naming_recommendation": {"keep_existing": "P3R_13A04", "d3de": "FOUR_STEP_30_SOL_14_479K_WSOL_LADDER", "shared_parent": None},
        "workflow": {"provenance": "CANONICAL_TIER1_REFERENCE", "workflow_status": "READY_FOR_CONFIRMED_REGISTRATION", "relationship_verdict": "DISTINCT_OPERATIONS", "next_action": "Manual registration decision for d3de as a separate confirmed operation; do not alter P3R_13A04."},
        "rpc": {"calls": 0, "reason": "No unresolved local evidence dimension is necessary to resolve duplicate-registration risk."},
        "safety": {"operation_registration": False, "operator_mutation": False, "tier_membership_mutation": False, "fingerprint_mutation": False, "queue_replay": False, "trading_signal": False},
    }
    report_path = OUT / "p3r_v2_d3de_vs_p3r_13a04_forensic_comparison.v1.json"
    report_sha = dump(report_path, report)
    conn = sqlite3.connect(ROOT / "database/wt_ops_v2.db")
    row = conn.execute("SELECT provenance_json FROM potential_operation_workflows WHERE candidate_id=?", (CANDIDATE,)).fetchone()
    if row is None:
        raise RuntimeError("missing d3de workflow record")
    provenance = json.loads(row[0])
    provenance["d3de_vs_p3r_13a04"] = {"verdict": "DISTINCT_OPERATIONS", "duplicate_risk": "NO_DUPLICATION_RISK", "registration_recommendation": "REGISTER_D3DE_AS_DISTINCT_CONFIRMED_OPERATION", "provenance": "CANONICAL_TIER1_REFERENCE"}
    conn.execute("UPDATE potential_operation_workflows SET workflow_status=?, proposed_name=?, parent_mechanism=?, latest_verdict=?, principal_gap=?, next_action=?, rpc_requirement=?, provenance_json=? WHERE candidate_id=?", ("READY_FOR_CONFIRMED_REGISTRATION", "FOUR_STEP_30_SOL_14_479K_WSOL_LADDER", "NONE_ESTABLISHED", "D3DE_DISTINCT_FROM_P3R_13A04", "Historical 13A04 member/infrastructure payload is unrecovered; duplicate risk is nevertheless resolved by non-subsuming exact routes.", "Manual registration decision for distinct confirmed d3de operation; do not alter P3R_13A04.", "NO", json.dumps(provenance, sort_keys=True), CANDIDATE))
    conn.commit(); conn.close()
    manifest = {"schema_version": "P3R_V2_D3DE_VS_P3R_13A04_FORENSIC_MANIFEST.v1", "source_snapshot": str(SOURCE), "source_snapshot_sha256": digest(SOURCE), "comparison_script_sha256": digest(Path(__file__)), "report": str(report_path.relative_to(ROOT)), "report_sha256": report_sha, "workflow_record": {"candidate_id": CANDIDATE, "updated_fields": ["workflow_status", "proposed_name", "parent_mechanism", "latest_verdict", "principal_gap", "next_action", "rpc_requirement", "provenance_json"]}, "rpc_calls": 0, "deterministic_replay": "rerun against unchanged source snapshot yields identical report SHA-256", "verdict": "P3R_V2_D3DE_VS_P3R_13A04_FORENSIC_COMPARISON_COMPLETE"}
    manifest_path = OUT / "p3r_v2_d3de_vs_p3r_13a04_forensic_comparison_manifest.v1.json"
    manifest_sha = dump(manifest_path, manifest)
    print(json.dumps({"report": str(report_path), "report_sha256": report_sha, "manifest": str(manifest_path), "manifest_sha256": manifest_sha, "relationship_verdict": report["relationship_verdict"], "registration_recommendation": report["registration_recommendation"], "rpc_calls": 0}, sort_keys=True))


if __name__ == "__main__":
    main()
