import json

import pytest

from src.evidence.contracts.psi0g_reviewed_candidate import (
    assess_fixture_structural_compatibility,
    bind_proposed_disposition,
)
from tests.test_psi0g_d1_operation_projection import material
from src.evidence.contracts.psi0g_operation_projection import project_psi0g_operation_candidate


def projection():
    return json.loads(project_psi0g_operation_candidate(**material(incomplete=True, absent=True)).payload)


def test_proposed_disposition_is_explicit_and_fails_closed_on_quality_vocabulary():
    value = projection()
    # Bind the production candidate identity expected by the reviewed decision.
    value["candidate"]["candidate_id"] = "95fba7d16194a1b2c03970910b5c737c70da669988fb2c317321318c41814505"
    value["candidate"]["quality_state"] = "INCOMPLETE"
    for row in value["runtime"]:
        row["quality_state"] = "INCOMPLETE"
    # The reviewed real candidate has exactly fourteen preserved gaps.
    value["candidate"]["missing_evidence"] = [f"gap-{i}" for i in range(14)]
    result = bind_proposed_disposition(value)
    disposition = result["values"]["dispositions"][0]
    assert disposition["nomination_state"] == "PROPOSED"
    assert disposition["operation_ids"] == ["watchtower", "three_sw2"]
    assert not any(disposition["authority"].values())
    assert not result["compatibility"]["structurally_accepted"]
    assert "RUNTIME_CONTRACT_REJECTED" in result["compatibility"]["blocker"]
    assert not result["compatibility"]["real_provenance_retained"]
    assert not result["compatibility"]["f13_publication_authorized"]


def test_supported_or_projection_authority_cannot_be_inferred():
    value = projection()
    value["authority"]["supported"] = True
    with pytest.raises(ValueError, match="AUTHORITY_DRIFT"):
        bind_proposed_disposition(value)


def test_degraded_partial_representation_is_structurally_accepted_without_authority():
    result = assess_fixture_structural_compatibility(projection())
    assert result["structurally_accepted"]
    assert result["fixture_only"]
    assert not result["real_provenance_retained"]
    assert not result["f13_publication_authorized"]
