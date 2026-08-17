from dataclasses import replace
from pathlib import Path

import pytest

from scripts.run_psi0h_e2_census_transaction_adapter_qualification import event, response
from src.evidence.contracts.psi0h_real_cohort_execution import build_real_cohort_authorization
from src.evidence.contracts.psi0h_real_run_preflight import (
    E2_ADAPTER_SHA256, Psi0hRealRunPreflightError, build_real_run_preflight,
    execute_preflight_bound_fixture, verify_real_run_preflight,
)


def preflight(tmp_path):
    return build_real_run_preflight(
        run_id="psi0h-e3-fixture", source_id="pumpportal-migration-census",
        census_path=Path("/future/oip_migration_census.jsonl"), census_device=1,
        census_inode=2, census_start_offset=100, maximum_census_bytes=65536,
        interval_start=101, interval_end=110, cutoff=100,
        endpoint_class="solana-json-rpc-gettransaction",
        staging_directory=tmp_path / "staging", output_directory=tmp_path / "output",
        consumption_directory=tmp_path / "consumption")


def authorization(tmp_path, item):
    return build_real_cohort_authorization(
        authorization_id="psi0h-e3-fixture-auth", run_id=item.run_id,
        source_id=item.source_id, source_kind="migration-census-byte-range",
        interval_start=item.interval_start, interval_end=item.interval_end, cutoff=item.cutoff,
        maximum_envelopes=20, maximum_primitives=20, maximum_provider_requests=20,
        provider_access_allowed=True, service_changes_allowed=False,
        isolated_output_directory=tmp_path / "output",
        collector_contract_digest=E2_ADAPTER_SHA256)


def test_preflight_replays_without_live_authority(tmp_path):
    item = preflight(tmp_path)
    assert verify_real_run_preflight(item)
    assert not item.source_read_authorized and not item.provider_access_authorized


def test_fixture_wrapper_executes_e2_through_single_use_e_boundary(tmp_path):
    item = preflight(tmp_path)
    result = execute_preflight_bound_fixture(preflight=item,
        authorization=authorization(tmp_path, item), events=[event()], transport=response)
    assert result["status"] == "PASS" and result["provider_request_count"] == 1
    assert (tmp_path / "output/cohort.json").is_file()
    assert (tmp_path / "staging/physical_attempts.jsonl").is_file()
    assert len(list((tmp_path / "consumption").iterdir())) == 1


def test_preflight_tamper_and_path_reuse_fail_closed(tmp_path):
    item = preflight(tmp_path)
    with pytest.raises(Psi0hRealRunPreflightError, match="REPLAY_FAILED"):
        verify_real_run_preflight(replace(item, provider_access_authorized=True))
    (tmp_path / "staging").mkdir()
    with pytest.raises(Psi0hRealRunPreflightError, match="REPLAY_FAILED"):
        verify_real_run_preflight(item)


def test_authorization_binding_drift_fails_before_consumption(tmp_path):
    item = preflight(tmp_path)
    auth = build_real_cohort_authorization(
        authorization_id="psi0h-e3-fixture-auth", run_id=item.run_id,
        source_id="different", source_kind="migration-census-byte-range",
        interval_start=item.interval_start, interval_end=item.interval_end, cutoff=item.cutoff,
        maximum_envelopes=20, maximum_primitives=20, maximum_provider_requests=20,
        provider_access_allowed=True, service_changes_allowed=False,
        isolated_output_directory=tmp_path / "output",
        collector_contract_digest=E2_ADAPTER_SHA256)
    with pytest.raises(Psi0hRealRunPreflightError, match="BINDING_DRIFT"):
        execute_preflight_bound_fixture(preflight=item,
            authorization=auth, events=[event()], transport=response)
    assert not (tmp_path / "consumption").exists()


def test_invalid_highwater_budget_or_overlapping_paths_rejected(tmp_path):
    with pytest.raises(Psi0hRealRunPreflightError, match="PREFLIGHT_INVALID"):
        build_real_run_preflight(run_id="run", source_id="source",
            census_path=Path("/future/census"), census_device=1, census_inode=2,
            census_start_offset=-1, maximum_census_bytes=1, interval_start=101,
            interval_end=110, cutoff=100, endpoint_class="rpc",
            staging_directory=tmp_path / "x", output_directory=tmp_path / "x",
            consumption_directory=tmp_path / "y")
