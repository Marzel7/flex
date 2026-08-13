import json
from hashlib import sha256
from pathlib import Path

import pytest

from src.evidence.contracts.gmgn_market_kline_normalizer import RequestMetadata
from src.evidence.contracts.historical_market_observation_bundle import (
    HistoricalMarketObservationBundleError,
    verify_historical_market_observation_bundle,
    write_historical_market_observation_bundle,
)


FIXTURES = Path(__file__).parent / "fixtures"
REVISION = "f39f934e2d0fe8ee608695e950dbf02c65bf0d22"


def _envelope():
    return json.loads((FIXTURES / "eb0_3e_gmgn_market_kline_envelope.json").read_text())


def _hashes():
    return json.loads((FIXTURES / "eb0_3f_market_observation_file_hashes.json").read_text())


def _metadata():
    return RequestMetadata(
        platform_mint="8TQAPEgP8jcWPeLTiQhFCFtPYwKhpjRvTngoPAmjpump",
        provider_version="gmgn-cli-1.5.6", endpoint_version="v1", interval="1m",
        request_from_ms=1785699480000, request_to_ms=1785703080000,
        observed_at_ms=1786645752000, request_run_id="frozen-eb0-3g",
        physical_request_sequence=1, request_cost_units=2,
        physical_requests_observed=1, retry=False, failover=False, pagination=False,
    )


def _write(path):
    return write_historical_market_observation_bundle(
        path, envelope=_envelope(), metadata=_metadata(),
        source_file_hashes=_hashes(), engineering_revision=REVISION,
    )


def _rehash(path):
    canonical = lambda value: json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    hashes = json.loads((path / "hashes.json").read_text())
    for name in hashes["files"]:
        hashes["files"][name] = sha256((path / name).read_bytes()).hexdigest()
    hashes["bundle_digest"] = sha256(canonical(hashes["files"])).hexdigest()
    (path / "hashes.json").write_bytes(canonical(hashes))


def test_bundle_is_canonical_complete_and_exactly_replayable(tmp_path):
    bundle = _write(tmp_path / "bundle")
    assert verify_historical_market_observation_bundle(
        bundle.output_directory, envelope=_envelope(), source_file_hashes=_hashes()
    ) == bundle
    assert {p.name for p in bundle.output_directory.iterdir()} == {
        "run.json", "projection.json", "manifest.json", "observations.json", "hashes.json"
    }


def test_same_inputs_produce_identical_bundle_bytes(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    _write(first); _write(second)
    assert {p.name: p.read_bytes() for p in first.iterdir()} == {
        p.name: p.read_bytes() for p in second.iterdir()
    }


def test_nonempty_output_and_overwrite_are_rejected(tmp_path):
    output = tmp_path / "bundle"; output.mkdir(); (output / "foreign").write_text("x")
    with pytest.raises(HistoricalMarketObservationBundleError, match="OUTPUT_NOT_EMPTY"):
        _write(output)


def test_missing_extra_and_altered_files_fail_verification(tmp_path):
    output = tmp_path / "bundle"; _write(output)
    (output / "projection.json").write_text("{}\n")
    with pytest.raises(HistoricalMarketObservationBundleError, match="FILE_DIGEST_MISMATCH"):
        verify_historical_market_observation_bundle(output, envelope=_envelope(), source_file_hashes=_hashes())
    (output / "extra").write_text("x")
    with pytest.raises(HistoricalMarketObservationBundleError, match="FILE_SET_MISMATCH"):
        verify_historical_market_observation_bundle(output, envelope=_envelope(), source_file_hashes=_hashes())


def test_rehashed_content_tampering_still_fails_replay(tmp_path):
    output = tmp_path / "bundle"; _write(output)
    run = json.loads((output / "run.json").read_text())
    run["request_metadata"]["observed_at_ms"] += 1
    (output / "run.json").write_text(json.dumps(run, sort_keys=True, separators=(",", ":")) + "\n")
    _rehash(output)
    with pytest.raises(HistoricalMarketObservationBundleError, match="CONTENT_REPLAY_FAILED|REPLAY_MISMATCH|RUN_DIGEST"):
        verify_historical_market_observation_bundle(output, envelope=_envelope(), source_file_hashes=_hashes())


def test_noncanonical_json_fails_even_with_recomputed_hashes(tmp_path):
    output = tmp_path / "bundle"; _write(output)
    run = json.loads((output / "run.json").read_text())
    (output / "run.json").write_text(json.dumps(run, indent=2) + "\n"); _rehash(output)
    with pytest.raises(HistoricalMarketObservationBundleError, match="NONCANONICAL_JSON"):
        verify_historical_market_observation_bundle(output, envelope=_envelope(), source_file_hashes=_hashes())


def test_invalid_revision_and_run_id_fail_before_output(tmp_path):
    with pytest.raises(HistoricalMarketObservationBundleError, match="INVALID_ENGINEERING_REVISION"):
        write_historical_market_observation_bundle(
            tmp_path / "bad", envelope=_envelope(), metadata=_metadata(),
            source_file_hashes=_hashes(), engineering_revision="bad",
        )
    metadata = _metadata().__class__(**{**_metadata().__dict__, "request_run_id": "../bad"})
    with pytest.raises(HistoricalMarketObservationBundleError, match="INVALID_RUN_ID"):
        write_historical_market_observation_bundle(
            tmp_path / "bad-run", envelope=_envelope(), metadata=metadata,
            source_file_hashes=_hashes(), engineering_revision=REVISION,
        )


def test_forbidden_content_is_rejected_upstream_and_never_written(tmp_path):
    envelope = _envelope(); envelope["api_key"] = "secret"
    with pytest.raises(Exception, match="ENVELOPE_SCHEMA_DRIFT"):
        write_historical_market_observation_bundle(
            tmp_path / "forbidden", envelope=envelope, metadata=_metadata(),
            source_file_hashes=_hashes(), engineering_revision=REVISION,
        )
    assert not (tmp_path / "forbidden").exists()
