import pytest
from src.discovery.operational_fingerprint import OperationalFingerprint, higher_order_eligibility
from src.discovery.p3r_s2a_contract import canonical_digest, detector_safe_projection, deterministic_order, validate_design


def test_contract_design_is_bounded_and_deterministic_before_selection():
    assert validate_design(watchtower=8, three_sw2=6, contrast=10, migration_cap=24, topology_cap=12, behaviour_cap=8) == {"cohort_size": 24, "residual_request_maximum": 44}
    rows = deterministic_order(({"mint": "z", "source_rank": 2}, {"mint": "a", "source_rank": 1}))
    assert [row["mint"] for row in rows] == ["a", "z"]
    assert canonical_digest(rows) == canonical_digest(rows)


def test_detector_projection_rejects_canonical_labels_and_shared_funding_remains_insufficient():
    with pytest.raises(ValueError):
        detector_safe_projection({"mint": "m", "watchtower": True})
    assert detector_safe_projection({"mint": "m", "direct_funder": "f"}) == {"mint": "m", "direct_funder": "f"}
    assert higher_order_eligibility((OperationalFingerprint(mint="a", direct_funder="f"),)).get("eligible") is False


def test_budget_and_cex_like_context_cannot_bypass_contract():
    with pytest.raises(ValueError):
        validate_design(watchtower=8, three_sw2=6, contrast=10, migration_cap=24, topology_cap=18, behaviour_cap=12)
    assert higher_order_eligibility((OperationalFingerprint(mint="a", upstream_funders=("cex",)),)).get("eligible") is False
