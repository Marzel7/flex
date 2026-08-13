import json
from dataclasses import replace
from pathlib import Path

import pytest

from src.evidence.contracts.birth_valuation_adapters import (
    adapt_launch_fact,
    adapt_market_observation,
    adapt_observed_migration,
    adapt_platform_receive,
)
from src.evidence.contracts.birth_valuation_manifest import (
    BirthValuationManifestError,
    build_birth_valuation_manifest,
    verify_birth_valuation_manifest,
)


FIXTURE = Path(__file__).parent / "fixtures" / "eb0_1c_source_adapters.json"


def _records():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return [
        adapt_launch_fact(fixture["launch_fact"]),
        adapt_platform_receive(fixture["platform_receive"]),
        adapt_observed_migration(fixture["migration_receive"]),
        *(adapt_market_observation(item) for item in fixture["market_conflicts"]),
    ]


def test_manifest_is_input_order_independent_and_replay_verifies():
    records = _records()
    forward = build_birth_valuation_manifest(records)
    reverse = build_birth_valuation_manifest(reversed(records))

    assert forward == reverse
    assert verify_birth_valuation_manifest(forward, list(reversed(records))) is True


def test_manifest_binds_expected_frozen_digest():
    manifest = build_birth_valuation_manifest(_records())

    assert manifest.manifest_digest == "1728039343e52cf2a32ea5dac89643013d44ebf23bc1c93059821a8382e40afc"
    assert manifest.input_digest == "4a4ef56bcc17849692a38371b219628b210d7d48c6b09db52b0550b81797959f"
    assert manifest.projection_digest == "e2d30e12fb697a791331e560182d67900f72f4775feae36efa5b35dcb1f97e4b"


def test_manifest_counts_events_quality_completeness_conflicts_and_missingness():
    manifest = build_birth_valuation_manifest(_records())

    assert manifest.observation_count == 5
    assert manifest.event_counts == {
        "CHAIN_BIRTH": 1,
        "MARKET_FIRST_OBSERVED": 2,
        "MIGRATION": 1,
        "PLATFORM_FIRST_SEEN": 1,
    }
    assert manifest.quality_counts == {"CONFLICTING": 2, "OBSERVED": 2, "VERIFIED": 1}
    assert manifest.completeness_counts == {"COMPLETE": 2, "NOT_OBSERVED": 3}
    assert manifest.conflicting_observation_count == 2
    assert manifest.missing_valuation_count == 3


def test_duplicate_input_fails_closed_instead_of_silent_deduplication():
    records = _records()
    with pytest.raises(BirthValuationManifestError, match="DUPLICATE_INPUT"):
        build_birth_valuation_manifest(records + records[:1])


def test_empty_and_extra_field_inputs_fail_closed():
    with pytest.raises(BirthValuationManifestError, match="EMPTY_INPUT"):
        build_birth_valuation_manifest([])

    record = _records()[0]
    record["created_at"] = 1710000000
    with pytest.raises(BirthValuationManifestError, match="NONCANONICAL_FIELDS"):
        build_birth_valuation_manifest([record])


def test_non_normalised_decimal_fails_closed():
    record = _records()[-1]
    record["price_or_market_cap_value"] = "25500.00"
    with pytest.raises(BirthValuationManifestError, match="INPUT_NOT_CANONICALLY_NORMALISED"):
        build_birth_valuation_manifest([record])


def test_replay_detects_manifest_or_input_tampering():
    records = _records()
    manifest = build_birth_valuation_manifest(records)
    tampered_manifest = replace(manifest, missing_valuation_count=999)
    with pytest.raises(BirthValuationManifestError, match="REPLAY_MISMATCH"):
        verify_birth_valuation_manifest(tampered_manifest, records)

    changed_records = _records()
    changed_records[-1]["price_or_market_cap_value"] = "25501"
    with pytest.raises(BirthValuationManifestError, match="REPLAY_MISMATCH"):
        verify_birth_valuation_manifest(manifest, changed_records)


def test_schema_binding_mismatch_fails_before_replay():
    manifest = build_birth_valuation_manifest(_records())
    with pytest.raises(BirthValuationManifestError, match="SCHEMA_VERSION_MISMATCH"):
        verify_birth_valuation_manifest(replace(manifest, schema_version="future"), _records())
