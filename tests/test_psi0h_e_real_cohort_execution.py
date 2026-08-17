from dataclasses import replace

import pytest

from src.evidence.contracts.psi0h_real_cohort_execution import (
    Psi0hRealCohortExecutionError, build_real_cohort_authorization,
    execute_real_cohort_once, verify_real_cohort_authorization,
)


DIGEST = "a" * 64


def authorization(tmp_path, **overrides):
    values = dict(
        authorization_id="psi0h-e-test", run_id="run-1", source_id="injected-fixture",
        source_kind="isolated-adapter", interval_start=101, interval_end=110, cutoff=100,
        maximum_envelopes=2, maximum_primitives=2, maximum_provider_requests=0,
        provider_access_allowed=False, service_changes_allowed=False,
        isolated_output_directory=tmp_path / "output", collector_contract_digest=DIGEST,
    )
    values.update(overrides)
    return build_real_cohort_authorization(**values)


def capture(_authorization):
    return {
        "envelopes": [{"envelope_id": "env", "event_time": 102,
                       "acquired_at": 103, "artifact_digest": DIGEST}],
        "evidence_rows": [{"evidence_id": "e", "envelope_id": "env",
                           "fact_family": "LaunchFact", "event_time": 102,
                           "payload_digest": DIGEST}],
        "primitive_rows": [{"primitive_id": "p", "primitive_type": "LAUNCH_SIGNER",
                            "window_start": 102, "window_end": 102, "generated_at": 104,
                            "evidence_ids": ["e"], "missing_inputs": []}],
        "provider_request_count": 0,
    }


def test_single_run_publishes_isolated_replayable_cohort(tmp_path):
    auth = authorization(tmp_path)
    consumption = tmp_path / "consumption"; consumption.mkdir()
    result = execute_real_cohort_once(auth, consumption_directory=consumption, collector=capture)
    assert result["status"] == "PASS" and (tmp_path / "output/cohort.json").is_file()
    assert not result["comparison_performed"] and not result["monitoring_activated"]
    assert len(list(consumption.iterdir())) == 1


def test_authorization_replay_and_authority_drift_fail_closed(tmp_path):
    auth = authorization(tmp_path)
    assert verify_real_cohort_authorization(auth)
    with pytest.raises(Psi0hRealCohortExecutionError, match="REPLAY_FAILED"):
        verify_real_cohort_authorization(replace(auth, monitoring_allowed=True))


def test_provider_budget_must_match_explicit_access(tmp_path):
    with pytest.raises(Psi0hRealCohortExecutionError, match="AUTHORIZATION_INVALID"):
        authorization(tmp_path, maximum_provider_requests=1, provider_access_allowed=False)


def test_authorization_requires_new_output(tmp_path):
    output = tmp_path / "output"; output.mkdir()
    with pytest.raises(Psi0hRealCohortExecutionError, match="AUTHORIZATION_INVALID"):
        authorization(tmp_path)


def test_consumption_is_single_use(tmp_path):
    auth = authorization(tmp_path)
    consumption = tmp_path / "consumption"; consumption.mkdir()
    execute_real_cohort_once(auth, consumption_directory=consumption, collector=capture)
    with pytest.raises(Psi0hRealCohortExecutionError, match="DESTINATION_NOT_NEW_EMPTY"):
        execute_real_cohort_once(auth, consumption_directory=consumption, collector=capture)


def test_collector_shape_and_provider_ceiling_fail_after_consumption(tmp_path):
    auth = authorization(tmp_path)
    consumption = tmp_path / "consumption"; consumption.mkdir()
    with pytest.raises(Psi0hRealCohortExecutionError, match="COLLECTOR_SHAPE_INVALID"):
        execute_real_cohort_once(auth, consumption_directory=consumption,
                                 collector=lambda _: {"unexpected": True})
    assert len(list(consumption.iterdir())) == 1 and not (tmp_path / "output").exists()


def test_historical_event_cannot_publish(tmp_path):
    auth = authorization(tmp_path)
    consumption = tmp_path / "consumption"; consumption.mkdir()
    old = capture(auth); old["envelopes"][0]["event_time"] = 99
    old["evidence_rows"][0]["event_time"] = 99
    with pytest.raises(Exception, match="ENVELOPE_INVALID"):
        execute_real_cohort_once(auth, consumption_directory=consumption, collector=lambda _: old)
    assert not (tmp_path / "output").exists()
