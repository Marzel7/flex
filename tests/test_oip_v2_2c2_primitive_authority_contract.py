import pytest

from src.evidence.primitives.authority import (
    FAMILY_CONTRACTS, AuthorityRule, authority_group, authority_rank,
    contract_for, corrected_freshness,
)
from src.evidence.primitives.contracts import PrimitiveType


def test_every_registered_primitive_has_complete_authority_contract():
    assert set(FAMILY_CONTRACTS) == {item.value for item in PrimitiveType}
    for family, contract in FAMILY_CONTRACTS.items():
        assert contract.semantic_type
        assert contract.cohort_sensitivity
        assert contract.authority_rule
        assert contract.version_policy == "SEMANTIC_CHANGE_MUST_INCREMENT"
        assert contract.current_versions == ("1",)
        assert contract.replay_policy == "CURRENT_STATE_REPLAY"
        assert contract.consumer_policy == "CURRENT_AUTHORITATIVE"


def test_unregistered_family_fails_closed():
    with pytest.raises(ValueError, match="unregistered Primitive family"):
        contract_for("FUTURE_UNKNOWN_FAMILY")


def test_corrected_freshness_semantics_are_explicit():
    assert corrected_freshness({"history_order": "NEWEST_FIRST",
                                "reference_boundary": "STRICTLY_PRECEDING"})
    assert not corrected_freshness({"require_complete_history": True})


def test_aggregate_group_and_rank_are_deterministic():
    group = authority_group(PrimitiveType.REPEATED_COUNTERPARTY.value, ("a", "b"), {},
                            {"source": "a", "destination": "b"}, "1")
    assert group == ("REPEATED_COUNTERPARTY", "1", "a", "b")
    earlier = authority_rank("REPEATED_COUNTERPARTY", 2, 1, 2,
                             {"transaction_count": 2}, 10, "a")
    later = authority_rank("REPEATED_COUNTERPARTY", 3, 1, 3,
                           {"transaction_count": 3}, 11, "b")
    assert later > earlier
    assert contract_for("REPEATED_COUNTERPARTY").authority_rule is AuthorityRule.LATEST_PER_GROUP
