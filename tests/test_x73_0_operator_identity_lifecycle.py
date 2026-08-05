import inspect
import sqlite3
import time

import pytest

from src.ops.operator_identity_governance import (
    GovernanceError,
    OperatorIdentityGovernanceService,
    read_identity_lifecycle,
    resolve_canonical_operator,
)
from src.ops.operator_reader import OperatorReader
from src.ops.operator_writer import OperatorWriter


def seed_operator(path, operator_id, name, status="CONFIRMED"):
    now = int(time.time())
    OperatorWriter(str(path)).transaction(
        f"seed-{operator_id}",
        lambda conn: conn.execute(
            "INSERT INTO operators(operator_id,status,confidence,first_seen,last_seen,summary,"
            "review_state,display_name,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (operator_id, status, "CERTAIN", now, now, name, "REVIEWED", name, now, now),
        ),
    )


@pytest.fixture
def lifecycle(tmp_path):
    path = tmp_path / "operators.db"
    OperatorWriter(str(path)).initialize_schema()
    seed_operator(path, "watchtower", "WATCHTOWER")
    seed_operator(path, "three-sw2", "3SW2")
    service = OperatorIdentityGovernanceService(str(path))
    service.bootstrap_confirmed()
    return path, service


def metadata(reason="Validated evidence"):
    return {"analyst": "analyst-1", "evidence_revision": "evidence-r7", "reason": reason}


def test_bootstrap_creates_immutable_confirmation_history(lifecycle):
    path, _ = lifecycle
    identity = read_identity_lifecycle(str(path), "three-sw2")
    assert identity["identity_status"] == "CONFIRMED"
    assert identity["activity_status"] == "ACTIVE"
    assert [event["event_type"] for event in identity["timeline"]] == ["CONFIRMED"]
    with sqlite3.connect(path) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM operator_identity_events WHERE operator_id='three-sw2'")


def test_expansion_preserves_identity_and_resolves_new_infrastructure(lifecycle):
    path, service = lifecycle
    result = service.expand("watchtower", {
        **metadata("Treasury rotation proven by independent lineage."),
        "asset_type": "TREASURY", "asset_value": "new-treasury-wallet",
    })
    identity = read_identity_lifecycle(str(path), "watchtower")
    assert result["event_type"] == "TREASURY_ADDED"
    assert identity["identity_status"] == "CONFIRMED"
    assert identity["assets"][0]["asset_value"] == "new-treasury-wallet"
    assert resolve_canonical_operator(str(path), "new-treasury-wallet")["operator_id"] == "watchtower"


def test_dormancy_and_review_do_not_delete_attribution(lifecycle):
    path, service = lifecycle
    service.set_activity("three-sw2", "DORMANT", metadata("No activity in the dormancy window."))
    identity = read_identity_lifecycle(str(path), "three-sw2")
    assert identity["identity_status"] == "CONFIRMED"
    assert identity["activity_status"] == "DORMANT"
    service.move_to_review("three-sw2", {
        **metadata("Contradictory controller evidence appeared."),
        "blocking_evidence": ["controller-conflict-1"],
        "required_investigation": "Resolve controller ownership.",
    })
    assert read_identity_lifecycle(str(path), "three-sw2")["identity_status"] == "REVIEW"
    assert OperatorReader(str(path)).fetch_operator("three-sw2") is not None
    service.resolve_review("three-sw2", metadata("Contradiction independently resolved."))
    assert read_identity_lifecycle(str(path), "three-sw2")["identity_status"] == "CONFIRMED"


def test_merge_redirects_history_and_launches_without_deleting_source(lifecycle):
    path, service = lifecycle
    seed_operator(path, "source-op", "Source Operation")
    service.bootstrap_confirmed()
    OperatorWriter(str(path)).transaction(
        "seed-membership",
        lambda conn: conn.execute(
            "INSERT INTO operator_launch_membership(mint,operator_id,assigned_at) VALUES(?,?,?)",
            ("mint-merge", "source-op", int(time.time())),
        ),
    )
    service.merge("watchtower", ["source-op"], metadata("Same controller independently proven."))
    source = read_identity_lifecycle(str(path), "source-op")
    assert source["identity_status"] == "MERGED"
    assert source["merged_into_operator_id"] == "watchtower"
    assert any(event["event_type"] == "MERGED" for event in source["timeline"])
    assert OperatorReader(str(path)).fetch_operator("source-op") is not None
    assert resolve_canonical_operator(str(path), "mint-merge")["operator_id"] == "watchtower"


def test_split_preserves_parent_and_creates_audited_children(lifecycle):
    path, service = lifecycle
    result = service.split("three-sw2", [
        {"display_name": "3SW2 Alpha", "launches": ["mint-a"], "entities": []},
        {"display_name": "3SW2 Beta", "launches": ["mint-b"], "entities": []},
    ], metadata("Independent control domains proven."))
    parent = read_identity_lifecycle(str(path), "three-sw2")
    assert parent["identity_status"] == "SPLIT"
    assert len(parent["split_children"]) == 2
    for child_id in result["child_operator_ids"]:
        child = read_identity_lifecycle(str(path), child_id)
        assert child["identity_status"] == "CONFIRMED"
        assert any(event["event_type"] == "CONFIRMED" for event in child["timeline"])
    assert resolve_canonical_operator(str(path), "mint-a")["display_name"] == "3SW2 Alpha"


def test_closed_identity_cannot_be_reactivated(lifecycle):
    _, service = lifecycle
    service.retire("watchtower", metadata("Identity maintenance formally ended."))
    with pytest.raises(GovernanceError) as exc:
        service.set_activity("watchtower", "REACTIVATED", metadata())
    assert exc.value.code == "INVALID_IDENTITY_STATE"


def test_canonical_family_launches_resolve_to_operator_identity_profile(lifecycle, monkeypatch):
    from src.ops.operation_attribution import OperationAttributionService, clear_operation_attribution_cache
    from src.ops.emerging_operator_service import EmergingOperatorService

    path, _ = lifecycle
    monkeypatch.setattr( EmergingOperatorService, "_compose", lambda _self: [{
        "family_id": "canonical:three-sw2", "canonical_operator_id": "three-sw2",
        "family_name": "3SW2", "stage": "CONFIRMED", "launch_list": ["3sw2-mint"],
        "member_wallets": [],
    }])
    clear_operation_attribution_cache(str(path))
    resolved = OperationAttributionService(str(path), str(path)).resolve_operation_for_token("3sw2-mint")
    assert resolved["operation_id"] == "three-sw2"
    assert resolved["profile_href"] == "/intelligence/operator/three-sw2"


def test_governance_surface_has_state_gates_and_no_deletion_workflow():
    template = open("templates/operator_intelligence.html", encoding="utf-8").read()
    routes = inspect.getsource(__import__("src.ops.operator_routes", fromlist=["operator_routes"]))
    assert "Expand Identity" in template
    assert "Move to Review" in template
    assert "Resolve Review" in template
    assert "Permanent Identity Timeline" in template
    assert "identity/expand" in routes and "identity/split" in routes and "identity/merge" in routes
    assert "identity/delete" not in routes.lower()
