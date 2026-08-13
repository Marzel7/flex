import json
from pathlib import Path

import pytest

from src.evidence.contracts.birth_valuation import project_birth_valuation
from src.evidence.contracts.birth_valuation_adapters import (
    ADAPTER_VERSION,
    BirthValuationSourceAdapterError,
    adapt_launch_fact,
    adapt_market_observation,
    adapt_observed_migration,
    adapt_platform_receive,
)


FIXTURE = Path(__file__).parent / "fixtures" / "eb0_1c_source_adapters.json"


def _fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_four_adapters_project_distinct_canonical_events():
    fixture = _fixture()
    inputs = [
        adapt_launch_fact(fixture["launch_fact"]),
        adapt_platform_receive(fixture["platform_receive"]),
        adapt_observed_migration(fixture["migration_receive"]),
        adapt_market_observation(fixture["market_conflicts"][0]),
    ]
    projected = project_birth_valuation(inputs)

    assert {item.event_kind for item in projected} == {
        "CHAIN_BIRTH", "PLATFORM_FIRST_SEEN", "MIGRATION", "MARKET_FIRST_OBSERVED"
    }
    assert all(item.source_version.startswith(ADAPTER_VERSION) for item in projected)


def test_launch_fact_uses_chain_time_but_never_invents_birth_valuation():
    adapted = adapt_launch_fact(_fixture()["launch_fact"])
    projected = project_birth_valuation([adapted])[0]

    assert projected.event_time_utc_ns == 1710000000000000000
    assert projected.observed_at_utc_ns == 1710000010000000000
    assert projected.price_or_market_cap_value is None
    assert projected.valuation_semantics == "UNKNOWN"
    assert projected.completeness_state == "NOT_OBSERVED"


def test_source_record_digest_is_preserved_in_projected_fact():
    source = _fixture()["platform_receive"]
    projected = project_birth_valuation([adapt_platform_receive(source)])[0]

    assert projected.source_record_digest == source["source_record_digest"]


def test_legacy_unversioned_source_gets_documented_adapter_version():
    source = _fixture()["platform_receive"]
    source.pop("source_schema_version")
    adapted = adapt_platform_receive(source)

    assert adapted["source_version"] == f"{ADAPTER_VERSION}:platform-receive:legacy-unversioned"


def test_mixed_platform_timestamps_fail_closed():
    source = _fixture()["platform_receive"]
    source["created_at"] = 1710000000
    with pytest.raises(BirthValuationSourceAdapterError, match="AMBIGUOUS_PLATFORM_TIMESTAMP"):
        adapt_platform_receive(source)


def test_migrated_at_proxy_without_receive_boundary_fails_closed():
    source = {
        "mint": "MintAdapter11111111111111111111111111111",
        "signature": "FrozenMigrationSignature",
        "migrated_at": 1710001000,
        "source": "token_analysis",
        "source_record_digest": "sha256:ambiguous-migration"
    }
    with pytest.raises(BirthValuationSourceAdapterError, match="AMBIGUOUS_MIGRATION_TIMESTAMP"):
        adapt_observed_migration(source)


def test_unverified_launch_fact_cannot_be_promoted_to_chain_birth():
    source = _fixture()["launch_fact"]
    source["verification_state"] = "OBSERVED"
    with pytest.raises(BirthValuationSourceAdapterError, match="LAUNCH_NOT_VERIFIED"):
        adapt_launch_fact(source)


def test_launch_fact_requires_complete_chain_lineage():
    source = _fixture()["launch_fact"]
    source["payload"].pop("creation_signature")
    with pytest.raises(BirthValuationSourceAdapterError, match="INVALID_CREATION_SIGNATURE"):
        adapt_launch_fact(source)


def test_conflicting_market_observations_remain_separate_facts():
    sources = _fixture()["market_conflicts"]
    projected = project_birth_valuation(adapt_market_observation(item) for item in sources)

    assert len(projected) == 2
    assert {item.quality_state for item in projected} == {"CONFLICTING"}
    assert {item.price_or_market_cap_value for item in projected} == {"25000", "25500"}
    assert {item.source_record_digest for item in projected} == {
        "sha256:frozen-market-a", "sha256:frozen-market-b"
    }


def test_market_observation_is_not_birth_market_cap():
    projected = project_birth_valuation(
        [adapt_market_observation(_fixture()["market_conflicts"][0])]
    )[0]

    assert projected.event_kind == "MARKET_FIRST_OBSERVED"
    assert projected.valuation_semantics == "MARKET_CAP_AT_EVENT"
    assert projected.valuation_semantics != "BIRTH_MARKET_CAP"


def test_market_observation_with_incoherent_timestamps_fails_closed():
    source = _fixture()["market_conflicts"][0]
    source["observed_at"] = source["captured_at"] - 1
    with pytest.raises(BirthValuationSourceAdapterError, match="OBSERVED_BEFORE_CAPTURE"):
        adapt_market_observation(source)
