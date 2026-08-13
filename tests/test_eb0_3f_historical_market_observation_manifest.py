import json
from dataclasses import replace
from pathlib import Path

import pytest

from src.evidence.contracts.gmgn_market_kline_normalizer import RequestMetadata
from src.evidence.contracts.historical_market_observation_manifest import (
    HistoricalMarketObservationManifestError,
    build_historical_market_observation_manifest,
    verify_historical_market_observation_manifest,
)


FIXTURES = Path(__file__).parent / "fixtures"


def _envelope():
    return json.loads((FIXTURES / "eb0_3e_gmgn_market_kline_envelope.json").read_text())


def _hashes():
    return json.loads((FIXTURES / "eb0_3f_market_observation_file_hashes.json").read_text())


def _metadata(**changes):
    values = dict(
        platform_mint="8TQAPEgP8jcWPeLTiQhFCFtPYwKhpjRvTngoPAmjpump",
        provider_version="gmgn-cli-1.5.6",
        endpoint_version="v1",
        interval="1m",
        request_from_ms=1785699480000,
        request_to_ms=1785703080000,
        observed_at_ms=1786645752000,
        request_run_id="frozen-eb0-3f",
        physical_request_sequence=1,
        request_cost_units=2,
        physical_requests_observed=1,
        retry=False,
        failover=False,
        pagination=False,
    )
    values.update(changes)
    return RequestMetadata(**values)


def test_manifest_is_deterministic_complete_and_exactly_replayable():
    first = build_historical_market_observation_manifest(
        envelope=_envelope(), metadata=_metadata(), source_file_hashes=_hashes()
    )
    second = build_historical_market_observation_manifest(
        envelope=_envelope(), metadata=_metadata(), source_file_hashes=dict(reversed(list(_hashes().items())))
    )
    assert first == second
    assert verify_historical_market_observation_manifest(
        first, envelope=_envelope(), metadata=_metadata(), source_file_hashes=_hashes()
    )


def test_manifest_binds_versions_accounting_digests_counts_and_limitations():
    manifest = build_historical_market_observation_manifest(
        envelope=_envelope(), metadata=_metadata(), source_file_hashes=_hashes()
    )
    assert manifest.row_count == 4
    assert manifest.request_cost_units == 2
    assert manifest.quality_counts == {"OBSERVED": 4}
    assert manifest.completeness_counts == {"PARTIAL_INTERVAL": 4}
    assert manifest.conflict_count == 0
    assert manifest.market_cap_observed_count == 0
    assert manifest.earliest_semantics_counts == {"PAGE_EARLIEST_NOT_HISTORY": 4}
    assert len(manifest.raw_envelope_digest) == 64
    assert len(manifest.response_projection_digest) == 64
    assert len(manifest.observation_projection_digest) == 64
    assert len(manifest.manifest_digest) == 64


@pytest.mark.parametrize("hashes,match", [
    ({"raw_envelope.json": "a" * 64}, "SOURCE_FILE_SET_MISMATCH"),
    ({"raw_envelope.json": "a" * 64, "response_projection.json": "b" * 64, "extra.json": "c" * 64}, "SOURCE_FILE_SET_MISMATCH"),
    ({"raw_envelope.json": "bad", "response_projection.json": "b" * 64}, "INVALID_SOURCE_FILE_HASH"),
])
def test_missing_extra_and_invalid_file_hashes_fail_closed(hashes, match):
    with pytest.raises(HistoricalMarketObservationManifestError, match=match):
        build_historical_market_observation_manifest(
            envelope=_envelope(), metadata=_metadata(), source_file_hashes=hashes
        )


def test_altered_envelope_metadata_or_file_hash_fails_exact_replay():
    manifest = build_historical_market_observation_manifest(
        envelope=_envelope(), metadata=_metadata(), source_file_hashes=_hashes()
    )
    altered = _envelope()
    altered["list"][0]["volume"] = "1.0"
    with pytest.raises(HistoricalMarketObservationManifestError, match="REPLAY_MISMATCH"):
        verify_historical_market_observation_manifest(
            manifest, envelope=altered, metadata=_metadata(), source_file_hashes=_hashes()
        )
    with pytest.raises(HistoricalMarketObservationManifestError, match="REPLAY_MISMATCH"):
        verify_historical_market_observation_manifest(
            manifest, envelope=_envelope(), metadata=_metadata(request_run_id="changed"), source_file_hashes=_hashes()
        )
    changed_hashes = dict(_hashes(), **{"raw_envelope.json": "0" * 64})
    with pytest.raises(HistoricalMarketObservationManifestError, match="REPLAY_MISMATCH"):
        verify_historical_market_observation_manifest(
            manifest, envelope=_envelope(), metadata=_metadata(), source_file_hashes=changed_hashes
        )


@pytest.mark.parametrize("field", [
    "schema_version", "contract_version", "adapter_version", "normalizer_version", "manifest_digest"
])
def test_version_and_manifest_tampering_fail_closed(field):
    manifest = build_historical_market_observation_manifest(
        envelope=_envelope(), metadata=_metadata(), source_file_hashes=_hashes()
    )
    tampered = replace(manifest, **{field: "bad"})
    expected = "REPLAY_MISMATCH" if field == "manifest_digest" else field.upper() + "_MISMATCH"
    with pytest.raises(HistoricalMarketObservationManifestError, match=expected):
        verify_historical_market_observation_manifest(
            tampered, envelope=_envelope(), metadata=_metadata(), source_file_hashes=_hashes()
        )


def test_credentials_pagination_authority_and_derived_fields_cannot_enter_manifest():
    for field in ("api_key", "cursor", "market_cap", "ranking", "creator", "policy"):
        envelope = _envelope()
        envelope[field] = "forbidden"
        with pytest.raises(Exception, match="ENVELOPE_SCHEMA_DRIFT"):
            build_historical_market_observation_manifest(
                envelope=envelope, metadata=_metadata(), source_file_hashes=_hashes()
            )
    with pytest.raises(Exception, match="REQUEST_SCOPE_EXPANSION"):
        build_historical_market_observation_manifest(
            envelope=_envelope(), metadata=_metadata(pagination=True), source_file_hashes=_hashes()
        )
