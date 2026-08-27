#!/usr/bin/env python3
"""Verify and idempotently register canonical d3de as a confirmed operation."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.ops.d3de_operation import (DETECTOR_VERSION, DISPLAY_NAME, OPERATOR_ID, SELECTED_ROUTE,
                                    SOURCE_CANDIDATE_ID, ensure_schema, is_d0_match,
                                    project_completed_walkback, selected_evidence)
from src.ops.manual_registry import refresh_operator_activity_snapshot
from src.ops.provisional_operations import ensure_schema as ensure_qualification_schema

OPS_DB = ROOT / "database/wt_ops_v2.db"
CORE_DB = ROOT / "database/flex_complete_database.db"
RUN = "p3r-v2-2dec1d40604c1f7c08c8"
FORENSIC = ROOT / "docs/agent_handoff/p3r/v2" / RUN / "d3de_tier1_forensic/p3r-v2-d3de-forensic-48663be8d4182e5613be/p3r_v2_d3de_canonical_tier1_forensic.v1.json"
ADVERSARIAL = ROOT / "docs/agent_handoff/p3r/v2" / RUN / "d3de_adversarial_coherence/p3r-v2-d3de-adversarial-bfc5e660de16735f8069/p3r_v2_d3de_adversarial_coherence.v1.json"
COMPARISON = ROOT / "docs/agent_handoff/p3r/v2" / RUN / "d3de_vs_13a04/p3r_v2_d3de_vs_p3r_13a04_forensic_comparison.v1.json"
OUT = ROOT / "docs/agent_handoff/p3r/v2" / RUN / "d3de_confirmed_registration/p3r_v2_d3de_confirmed_registration.v1.json"
MANIFEST = ROOT / "docs/agent_handoff/p3r/v2" / RUN / "d3de_confirmed_registration/p3r_v2_d3de_confirmed_registration_manifest.v1.json"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify() -> tuple[dict, dict, dict, list[dict]]:
    forensic, adversarial, comparison = (json.loads(path.read_text()) for path in (FORENSIC, ADVERSARIAL, COMPARISON))
    expected = [dict(hop_depth=d, mechanism=m, amount_lamports=a) for d, m, a in SELECTED_ROUTE]
    if ([{"hop_depth": edge["hop_depth"], "mechanism": edge["mechanism"], "amount_lamports": edge["amount_lamports"]} for edge in forensic["base_mechanism"]["fingerprint"]["edges"]] != expected
        or len(forensic["frozen_members"]) != 9
        or forensic["principal_atomic_lifecycle_contract"]["transfer_lamports"] != 29_997_950_720
        or forensic["alternative_mechanism"]["coverage"] != "9/9"
        or any(forensic["detector_comparison"][level][key] != value for level in ("D0", "D1", "D2", "D3", "D4", "D5") for key, value in (("tp", 9), ("fp", 0), ("fn", 0), ("observable_denominator", 12041)))
        or adversarial["adversarial_verdict"] != "D3DE_ONE_OPERATION_ADVERSARIALLY_CONFIRMED"
        or comparison["relationship_verdict"] != "DISTINCT_OPERATIONS"
        or comparison["registration_recommendation"] != "REGISTER_D3DE_AS_DISTINCT_CONFIRMED_OPERATION"):
        raise RuntimeError("qualification artifact verification failed")
    return forensic, adversarial, comparison, forensic["frozen_members"]


def main() -> None:
    forensic, adversarial, comparison, members = verify()
    now = int(time.time())
    conn = sqlite3.connect(OPS_DB)
    conn.row_factory = sqlite3.Row
    try:
        ensure_schema(conn); ensure_qualification_schema(conn)
        actions = {}
        for member in members:
            evidence = selected_evidence(conn, member["mint"])
            if not is_d0_match(evidence):
                raise RuntimeError(f"historical D0 replay failed: {member['mint']}")
        controls = {}
        for name, operator_id in (("P3R_13A04", "ccb7b1b0-56e1-4543-9e95-3f284bed3943"), ("WATCHTOWER", "04265d9f-6eb2-568c-a49e-9253091a4dbb"), ("Byzantine", "d8ee4d7a-fcd6-5a5b-b897-24f6ab56e334"), ("900b", "70f27e37-83eb-5c97-831c-48189ef98f6c")):
            mints = [r[0] for r in conn.execute("SELECT mint FROM operator_launch_membership WHERE operator_id=?", (operator_id,))]
            controls[name] = {"observable": len(mints), "matches": [mint for mint in mints if is_d0_match(selected_evidence(conn, mint))]}
            if controls[name]["matches"]:
                raise RuntimeError(f"D0 control contamination: {name}")
        conn.execute("INSERT INTO operators(operator_id,status,confidence,summary,review_state,display_name,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(operator_id) DO UPDATE SET status='CONFIRMED',confidence='CERTAIN',summary=excluded.summary,review_state='REVIEWED',display_name=excluded.display_name,updated_at=excluded.updated_at", (OPERATOR_ID, "CONFIRMED", "CERTAIN", "Confirmed address-independent exact selected four-step 30-SOL/14,479K WSOL ladder.", "REVIEWED", DISPLAY_NAME, now, now))
        conn.execute("INSERT INTO operation_registry_dispositions(operator_id,disposition,manual_reviewer,reason,source_candidate_id,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(operator_id) DO UPDATE SET disposition=excluded.disposition,manual_reviewer=excluded.manual_reviewer,reason=excluded.reason,source_candidate_id=excluded.source_candidate_id,updated_at=excluded.updated_at", (OPERATOR_ID, "ACTIVE_MANUAL", "approved_d3de_registration", "Approved canonical Tier-1 address-independent operation", SOURCE_CANDIDATE_ID, now))
        member_evidence = [{"mint": m["mint"], "creator": m["creator"], "direct_funder": m["direct_funder"], "selected_edges": m["selected_edges"], "principal_atomic": m["principal_atomic_flows"][0], "observed_at": min(edge["block_time"] for edge in m["selected_edges"] if edge.get("block_time") is not None)} for m in members]
        provenance = {"provenance": "CANONICAL_TIER1_REFERENCE", "detector_version": DETECTOR_VERSION, "detector_level": "D0", "selected_ladder": [dict(hop_depth=d, mechanism=m, amount_lamports=a) for d, m, a in SELECTED_ROUTE], "alternative_route": forensic["alternative_mechanism"]["fingerprints"], "atomic_sequence": forensic["principal_atomic_lifecycle_contract"]["instruction_order"], "principal_atomic_amount_lamports": 29_997_950_720, "address_independent": True, "qualification": {"frozen_tp": 9, "frozen_fp": 0, "frozen_fn": 0, "precision": 1.0, "recall": 1.0, "observable_retained_population": 12041, "wording": "Frozen validation across 12,041 retained mints where the behaviour was observable; not a guarantee of future accuracy."}, "historical_member_evidence": member_evidence, "relationship_to_p3r_13a04": "DISTINCT_OPERATION"}
        profile_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"profile:{OPERATOR_ID}:1"))
        mints = [member["mint"] for member in members]
        conn.execute("INSERT INTO operation_behavioural_profiles(profile_id,operator_id,source_candidate_id,profile_version,status,provenance_json,member_mints_json,created_at,reviewed_at,reviewer) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(operator_id,profile_version) DO UPDATE SET status=excluded.status,provenance_json=excluded.provenance_json,member_mints_json=excluded.member_mints_json,reviewed_at=excluded.reviewed_at,reviewer=excluded.reviewer", (profile_id, OPERATOR_ID, SOURCE_CANDIDATE_ID, 1, "CONFIRMED", json.dumps(provenance, sort_keys=True), json.dumps(mints), now, now, "approved_d3de_registration"))
        contract_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"contract:{OPERATOR_ID}:{DETECTOR_VERSION}"))
        conn.execute("INSERT INTO operation_qualification_contracts(contract_id,operator_id,qualification_category,automation_eligibility,detector_version,parent_mechanism,source_candidate_id,benchmark_json,contract_json,evidence_lineage_json,frozen_edge_highwater,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(operator_id,detector_version) DO UPDATE SET benchmark_json=excluded.benchmark_json,contract_json=excluded.contract_json,evidence_lineage_json=excluded.evidence_lineage_json", (contract_id, OPERATOR_ID, "CONFIRMED", "ELIGIBLE", DETECTOR_VERSION, "NONE_ESTABLISHED", SOURCE_CANDIDATE_ID, json.dumps(provenance["qualification"], sort_keys=True), json.dumps({"D0_exact_selected_route": provenance["selected_ladder"]}, sort_keys=True), json.dumps({"forensic": str(FORENSIC.relative_to(ROOT)), "adversarial": str(ADVERSARIAL.relative_to(ROOT)), "comparison": str(COMPARISON.relative_to(ROOT))}, sort_keys=True), 60299, now))
        conn.commit()
        for mint in mints:
            actions[mint] = project_completed_walkback(conn, mint, core_db_path=str(CORE_DB), now=now)
        if set(actions.values()) - {"admitted", "already_present"}:
            raise RuntimeError(f"historical membership projection failed: {actions}")
        workflow = conn.execute("SELECT provenance_json FROM potential_operation_workflows WHERE candidate_id=?", (SOURCE_CANDIDATE_ID,)).fetchone()
        provenance_workflow = json.loads(workflow[0]); provenance_workflow["confirmed_registration"] = {"operator_id": OPERATOR_ID, "detector_version": DETECTOR_VERSION, "provenance": "CANONICAL_TIER1_REFERENCE"}
        conn.execute("UPDATE potential_operation_workflows SET workflow_status='ACTIVE_CONFIRMED',related_operator_id=?,latest_verdict='D3DE_CONFIRMED_ACTIVE_OPERATION',principal_gap='None retained.',next_action='Monitor completed walkbacks through the frozen D0 behavioural detector.',rpc_requirement='NO',provenance_json=?,updated_at=? WHERE candidate_id=?", (OPERATOR_ID, json.dumps(provenance_workflow, sort_keys=True), now, SOURCE_CANDIDATE_ID))
        conn.commit()
        activity = refresh_operator_activity_snapshot(conn, OPERATOR_ID, core_db_path=str(CORE_DB), now=now)
        output = {"schema_version": "P3R_V2_D3DE_CONFIRMED_REGISTRATION.v1", "operator_id": OPERATOR_ID, "display_name": DISPLAY_NAME, "detector_version": DETECTOR_VERSION, "minimum_detector": "D0 exact selected route only; no atomic, alternative, or address requirement", "qualification_sources": {str(p.relative_to(ROOT)): digest(p) for p in (FORENSIC, ADVERSARIAL, COMPARISON)}, "historical_member_count": len(mints), "historical_replay": actions, "negative_controls": controls, "activity": activity, "workflow_status": "ACTIVE_CONFIRMED", "safety": {"rpc_calls": 0, "queue_replay": False, "tier_mutation": False, "address_requirements": False, "trading_signal": False}}
        OUT.parent.mkdir(parents=True, exist_ok=True); OUT.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")
        artifact_sha = digest(OUT)
        manifest = {"schema_version": "P3R_V2_D3DE_CONFIRMED_REGISTRATION_MANIFEST.v1", "canonical_run_id": RUN, "operator_id": OPERATOR_ID, "detector_version": DETECTOR_VERSION, "registration_artifact": str(OUT.relative_to(ROOT)), "registration_artifact_sha256": artifact_sha, "qualification_sources": output["qualification_sources"], "registration_script_sha256": digest(Path(__file__)), "rpc_calls": 0, "queue_replay": False, "worker_activation": "walkback_worker restart is performed only after the focused test gate outside this script."}
        MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        print(json.dumps({"operator_id": OPERATOR_ID, "members": len(mints), "artifact": str(OUT), "sha256": artifact_sha, "manifest": str(MANIFEST), "manifest_sha256": digest(MANIFEST), "controls": controls}, sort_keys=True))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
