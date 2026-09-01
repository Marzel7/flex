from dataclasses import replace

import pytest

from src.evidence.contracts.psi0h_e4_live_census_preflight import (
    Psi0hLiveCensusPreflightError, build_live_census_preflight, verify_live_census_preflight,
)


def preflight(tmp_path):
    census = tmp_path / "oip_migration_census.jsonl"
    census.write_text('{"schema_version":1}\\n')
    return build_live_census_preflight(
        run_id="psi0h-e4-live-census", source_id="pumpportal-migration-census",
        source_kind="migration-census-live-file", census_path=census.resolve(),
        maximum_census_bytes=64 * 1024, interval_start=101, interval_end=110, cutoff=100,
        staging_directory=tmp_path / "staging", output_directory=tmp_path / "output",
        consumption_directory=tmp_path / "consumption",
    )


def test_preflight_captures_high_water_and_forbids_all_live_authority(tmp_path):
    record = preflight(tmp_path)
    assert verify_live_census_preflight(record)
    assert record.census_start_offset == record.census_size_bytes
    assert record.census_start_offset >= 0
    assert record.census_size_bytes == len('{"schema_version":1}\\n')
    assert not record.source_read_authorized and not record.provider_access_authorized


def test_preflight_replay_drift_fails_closed(tmp_path):
    record = preflight(tmp_path)
    with pytest.raises(Psi0hLiveCensusPreflightError, match="REPLAY_FAILED"):
        verify_live_census_preflight(replace(record, activation_authorized=True))


def test_preflight_rejects_missing_or_duplicate_outputs(tmp_path):
    record = preflight(tmp_path)
    with pytest.raises(Psi0hLiveCensusPreflightError, match="PREFLIGHT_INVALID"):
        build_live_census_preflight(
            run_id="x", source_id="pumpportal-migration-census", source_kind="migration-census-live-file",
            census_path=tmp_path / "oip_migration_census.jsonl", maximum_census_bytes=64 * 1024,
            interval_start=101, interval_end=110, cutoff=100,
            staging_directory=tmp_path / "output", output_directory=tmp_path / "output",
            consumption_directory=tmp_path / "consumption",
        )
    (tmp_path / "consumption").mkdir()
    with pytest.raises(Psi0hLiveCensusPreflightError, match="PREFLIGHT_INVALID"):
        build_live_census_preflight(
            run_id="psi0h-e4-live-census", source_id="pumpportal-migration-census",
            source_kind="migration-census-live-file", census_path=tmp_path / "oip_migration_census.jsonl",
            maximum_census_bytes=64 * 1024, interval_start=101, interval_end=110,
            cutoff=100, staging_directory=tmp_path / "staging",
            output_directory=tmp_path / "output2", consumption_directory=tmp_path / "consumption",
        )


def test_reject_non_file_or_mutating_boundaries(tmp_path):
    directory = tmp_path / "dir"; directory.mkdir()
    with pytest.raises(Psi0hLiveCensusPreflightError, match="PREFLIGHT_INVALID"):
        build_live_census_preflight(
            run_id="psi0h-e4-live-census", source_id="pumpportal-migration-census",
            source_kind="migration-census-live-file", census_path=directory,
            maximum_census_bytes=64 * 1024, interval_start=101, interval_end=110,
            cutoff=100, staging_directory=tmp_path / "staging",
            output_directory=tmp_path / "output", consumption_directory=tmp_path / "consumption",
        )
