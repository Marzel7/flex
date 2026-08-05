import sqlite3
from pathlib import Path

import pytest

from src.ops.investigation_lifecycle import (
    InvestigationLifecycleError, InvestigationLifecycleService,
    apply_lifecycle_overlay, read_lifecycle,
)
from src.ops.emerging_operator_service import _analyst_queue, _attention_queue


ROOT = Path(__file__).resolve().parents[1]


class Registry:
    def __init__(self, family): self.family = family
    def get(self, family_id): return dict(self.family) if family_id == self.family["family_id"] else None


def family(disposition="REVIEW"):
    return {
        "family_id": "family:gf7y", "family_name": "GF7Y Family",
        "launch_list": ["M1"], "treasuries": [], "client_wallets": ["GF7Y"],
        "funding_mechanisms": ["PLAIN_XFER"], "observed_topology_variants": [],
        "walkback_descendant_count": 1,
        "reconciliation": {"disposition": disposition, "supporting_evidence": []},
        "presentation": {"disposition": disposition, "kind": "investigation_population"},
        "investigation_trigger": {"family_id": "family:gf7y", "signals": ["Funding Mechanism"]},
    }


def test_dismiss_and_reopen_are_durable_without_deleting_evidence(tmp_path):
    path = str(tmp_path / "ops.db"); sqlite3.connect(path).close()
    source = family(); service = InvestigationLifecycleService(path, Registry(source))
    dismissed = service.dismiss("family:gf7y", {
        "analyst": "analyst@example", "reason_code": "SPAM_DUSTING", "notes": "Known APAM dusting",
    })
    assert dismissed["presentation"]["disposition"] == "DISMISSED"
    assert dismissed["reconciliation"]["disposition"] == "REVIEW"
    assert dismissed["launch_list"] == ["M1"]
    assert dismissed["investigation_trigger"] == source["investigation_trigger"]
    lifecycle = read_lifecycle(path, "family:gf7y")
    assert lifecycle["history"][0]["event_type"] == "INVESTIGATION_DISMISSED"
    reopened = service.reopen("family:gf7y", {"analyst": "analyst@example", "reason": "Manual review"})
    assert reopened["analyst_lifecycle"] == "REOPENED"
    assert reopened["presentation"]["disposition"] == "REVIEW"
    assert reopened["investigation_trigger"] == source["investigation_trigger"]
    assert [x["event_type"] for x in read_lifecycle(path, "family:gf7y")["history"]] == [
        "INVESTIGATION_REOPENED", "INVESTIGATION_DISMISSED",
    ]


def test_material_change_recommends_reopen_but_does_not_change_state(tmp_path):
    path = str(tmp_path / "ops.db"); sqlite3.connect(path).close()
    source = family(); service = InvestigationLifecycleService(path, Registry(source))
    service.dismiss("family:gf7y", {"analyst": "a", "reason_code": "SPAM_DUSTING"})
    changed = family(); changed["treasuries"] = ["NEW_TREASURY"]
    overlaid = apply_lifecycle_overlay(changed, path)
    assert overlaid["analyst_lifecycle"] == "DISMISSED"
    assert overlaid["investigation_lifecycle"]["reopen_recommended"] is True
    assert "New Treasury" in overlaid["investigation_lifecycle"]["material_changes"]


def test_confirmed_operations_cannot_be_dismissed(tmp_path):
    path = str(tmp_path / "ops.db"); sqlite3.connect(path).close()
    service = InvestigationLifecycleService(path, Registry(family("CONFIRMED_OPERATION")))
    with pytest.raises(InvestigationLifecycleError) as exc:
        service.dismiss("family:gf7y", {"analyst": "a", "reason_code": "OTHER"})
    assert exc.value.code == "DISMISSAL_NOT_PERMITTED"


def test_dismissal_backfills_the_five_slot_attention_queue():
    ranked = [{"family_id": f"family:{index}"} for index in range(6)]
    ranked[1]["analyst_lifecycle"] = "DISMISSED"

    visible, dismissed = _attention_queue(ranked)

    assert [row["family_id"] for row in visible] == [
        "family:0", "family:2", "family:3", "family:4", "family:5",
    ]
    assert [row["family_id"] for row in dismissed] == ["family:1"]


def test_analyst_queue_fills_review_slots_without_changing_dispositions():
    reviews = [{"family_id": f"review:{index}", "presentation": {"disposition": "REVIEW"}} for index in range(4)]
    unresolved = [{"family_id": "unresolved:1", "presentation": {"disposition": "UNRESOLVED"}}]

    queue = _analyst_queue(reviews, unresolved)

    assert len(queue) == 5
    assert queue[-1]["presentation"]["disposition"] == "UNRESOLVED"
    assert all(row["presentation"]["disposition"] == "REVIEW" for row in queue[:4])


def test_ui_contracts_expose_dismissed_without_registry_actions():
    profile = (ROOT / "templates/operation_profile.html").read_text()
    registry = (ROOT / "templates/operators_index.html").read_text()
    discovery = (ROOT / "templates/discovery.html").read_text()
    routes = (ROOT / "src/ops/operator_routes.py").read_text()
    assert "Dismiss Investigation" in profile and "Reopen Investigation" in profile
    assert "INVESTIGATION_DISMISSED" in (ROOT / "src/ops/investigation_lifecycle.py").read_text()
    assert 'data-filter="DISMISSED"' in registry
    assert "(r.lifecycle&&r.lifecycle.history)||[]" in registry
    assert "Dismissed Investigations" in discovery
    assert "/dismiss" in routes and "/reopen" in routes
    assert "Confirm dismissal" not in registry
