import json
from dataclasses import asdict
from pathlib import Path

import pytest

from src.evidence.contracts.birth_valuation import (
    BirthValuationContractError,
    project_birth_valuation,
    projection_digest,
)


FIXTURE = Path(__file__).parent / "fixtures" / "eb0_1a_birth_valuation.json"


def _records():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return [{key: value for key, value in row.items() if key != "case"} for row in payload["records"]]


def test_projection_is_input_order_independent_and_replay_idempotent():
    records = _records()
    forward = project_birth_valuation(records)
    reverse_with_replay = project_birth_valuation(list(reversed(records)) + records[:1])

    assert forward == reverse_with_replay
    assert projection_digest(forward) == projection_digest(reverse_with_replay)
    assert len({row.observation_id for row in forward}) == len(records)


def test_event_kinds_remain_distinct_and_delayed_market_fact_is_not_birth():
    projected = project_birth_valuation(_records())
    kinds = {row.event_kind for row in projected}

    assert kinds == {
        "CHAIN_BIRTH",
        "PLATFORM_FIRST_SEEN",
        "MIGRATION",
        "MARKET_FIRST_OBSERVED",
    }
    delayed = next(
        row
        for row in projected
        if row.mint == "MintDelayedObserved11111111111111111111111"
        and row.event_kind == "MARKET_FIRST_OBSERVED"
    )
    assert delayed.valuation_semantics == "MARKET_CAP_AT_EVENT"
    assert delayed.observed_at_utc_ns > delayed.event_time_utc_ns


def test_delayed_market_observation_cannot_be_promoted_to_birth_market_cap():
    record = _records()[2]
    record.update(
        valuation_semantics="BIRTH_MARKET_CAP",
        quality_state="VERIFIED",
        birth_equivalence_proven=True,
    )

    with pytest.raises(BirthValuationContractError, match="BIRTH_VALUATION_NOT_PROVEN"):
        project_birth_valuation([record])


def test_missing_valuation_is_explicit_unknown_not_observed_and_never_zero():
    projected = project_birth_valuation(_records())
    missing = [row for row in projected if row.price_or_market_cap_value is None]

    assert missing
    assert all(row.valuation_semantics == "UNKNOWN" for row in missing)
    assert all(row.completeness_state == "NOT_OBSERVED" for row in missing)

    invalid = _records()[3]
    invalid["price_or_market_cap_value"] = "0"
    with pytest.raises(BirthValuationContractError, match="VALUATION_MUST_BE_POSITIVE"):
        project_birth_valuation([invalid])


def test_conflicting_versioned_facts_are_preserved_without_merge():
    conflicts = project_birth_valuation(_records()[4:])

    assert len(conflicts) == 2
    assert {row.source for row in conflicts} == {"frozen_provider_a", "frozen_provider_b"}
    assert len({row.observation_id for row in conflicts}) == 2
    assert len({row.provenance_digest for row in conflicts}) == 2


def test_provenance_and_identity_are_content_derived():
    first = project_birth_valuation([_records()[0]])[0]
    changed = _records()[0]
    changed["source_version"] = "v2"
    second = project_birth_valuation([changed])[0]

    assert first.provenance_digest != second.provenance_digest
    assert first.observation_id != second.observation_id
    assert "birth_equivalence_proven" not in asdict(first)


def test_missing_value_cannot_claim_observed_semantics_or_completeness():
    record = _records()[3]
    record["valuation_semantics"] = "MARKET_CAP_AT_EVENT"
    with pytest.raises(BirthValuationContractError, match="MISSING_VALUATION_NOT_EXPLICIT"):
        project_birth_valuation([record])


def test_exact_birth_requires_explicit_equivalence_proof():
    record = _records()[0]
    record.pop("birth_equivalence_proven")
    with pytest.raises(BirthValuationContractError, match="BIRTH_VALUATION_NOT_PROVEN"):
        project_birth_valuation([record])
