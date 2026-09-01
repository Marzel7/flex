from pathlib import Path

from src.ops import potential_operations as po


def test_c357_is_a_registered_leviathan_subtype_not_an_unresolved_candidate():
    row = po._decorate({"candidate_id": po.C357_CANDIDATE, "workflow_status": "SUBTYPE_REGISTERED"})
    assert row["registered_subtype"] is True
    assert row["relationship_label"] == "Resolved Leviathan behaviour"
    assert row["subtype_url"].endswith(po.C357_SUBTYPE_ID)


def test_c357_is_visible_on_the_active_and_potential_operations_surfaces():
    page = Path("templates/potential_operations.html").read_text()
    registry = Path("templates/operators_index.html").read_text()
    assert "Leviathan Behaviours" in page
    assert "not r.registered_subtype" in page
    assert "C357 operational subtype" in registry
    assert po.C357_SUBTYPE_ID in registry
