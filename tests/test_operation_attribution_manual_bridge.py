import sqlite3

import pytest

from src.ops.operation_attribution_manual_bridge import (
    _validate_resolved_input,
    promote_validated_operation,
    validate_potential_operation,
)
from src.ops.potential_operation_validation import resolve_validation_input


def _family(name, state, group):
    return {"family": name, "state": state, "independence_from_detector": "PASS", "independence_from_other_families": "PASS", "dependency_group": group, "evidence_refs": [f"fixture:{name}"]}


def _schema(conn):
    conn.execute("CREATE TABLE operators(operator_id TEXT PRIMARY KEY,status TEXT,confidence TEXT,summary TEXT,review_state TEXT,display_name TEXT,created_at INT,updated_at INT)")
    conn.execute("CREATE TABLE operator_launch_membership(mint TEXT PRIMARY KEY,operator_id TEXT,source_population_id TEXT,assigned_at INT,event_id TEXT)")


def _qualified_resolution(candidate_id="fixture-positive"):
    return {"candidate_source_id": candidate_id, "candidate_source_type": "POTENTIAL_OPERATION", "candidate_snapshot_ref": "sha256:fixture-snapshot", "detector_contract": "FIXTURE", "detector_evidence_refs": ["fixture:detector"], "candidate_member_refs": ["mint-a", "mint-b"], "proposed_operation_id": "fixture-operation", "proposed_operation_label": "Fixture operation", "evidence_families": [_family("CONTROLLER_CONTINUITY", "PROVEN_STRONG", "controller"), _family("EARLY_EXECUTION_FINGERPRINT", "PROVEN_MODERATE", "execution")], "common_infrastructure_exclusions": [], "completeness_state": "COMPLETE"}


def test_server_side_resolver_rejects_unknown_and_requires_more_evidence_for_retained_candidate():
    conn = sqlite3.connect(":memory:")
    with pytest.raises(ValueError, match="uniquely resolvable"):
        resolve_validation_input("not-a-candidate")
    # A real retained candidate is resolved only from local provenance and has
    # detector evidence, not independent proof injected from a caller.
    pid, decision = validate_potential_operation(conn, "p3r-v2-dc4953db7adb853337c4")
    assert pid and decision["validation_state"] == "ADDITIONAL_EVIDENCE_REQUIRED"
    # The retained Potential Operations candidate linked to Nexus receives the
    # same server-side-only outcome and never becomes eligible from a client
    # payload.
    nexus_pid, nexus = validate_potential_operation(conn, "p3r-v2-6437acd385e566e301a7")
    assert nexus_pid and nexus["validation_state"] == "ADDITIONAL_EVIDENCE_REQUIRED"


def test_positive_control_commits_canonical_membership_once_and_is_idempotent():
    workflow = sqlite3.connect(":memory:")
    canonical = sqlite3.connect(":memory:")
    _schema(canonical)
    resolved = _qualified_resolution()
    proposal_id, decision = _validate_resolved_input(workflow, resolved)
    assert decision["attribution_state"] == "ATTRIBUTION_PROVEN"
    assert canonical.execute("SELECT count(*) FROM operator_launch_membership").fetchone()[0] == 0
    first = promote_validated_operation(workflow, canonical, proposal_id, resolver=lambda _: resolved)
    assert first["rows_expected"] == 2 and first["rows_written"] == 2
    assert canonical.execute("SELECT count(*) FROM operator_launch_membership").fetchone()[0] == 2
    second = promote_validated_operation(workflow, canonical, proposal_id, resolver=lambda _: resolved)
    assert second["rows_written"] == 0 and second["idempotent"] is True
