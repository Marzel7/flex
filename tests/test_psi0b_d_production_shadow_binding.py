from dataclasses import replace
from pathlib import Path

import pytest

from src.evidence.contracts.production_shadow_production_binding import (
    AUTHORITY_CLASS,
    PRODUCTION_PATHS,
    ProductionShadowProductionBindingError,
    bind_production_invocation,
    build_production_execution_authorization,
    canonical_production_source_bindings,
    execute_production_shadow,
    production_binding_contract_digest,
    verify_bound_production_invocation,
    verify_production_execution_authorization,
)
from src.evidence.contracts.production_shadow_run_preflight import (
    build_immutable_cohort_artifact,
    build_production_shadow_run_preflight,
)


def _record(tmp_path):
    return build_production_execution_authorization(
        authorization_id="approval-1", run_id="psi0b-shadow-1",
        output_directory=tmp_path / "new-output",
    )


def test_authorization_and_invocation_exact_replay(tmp_path):
    record = _record(tmp_path)
    assert verify_production_execution_authorization(record)
    assert tuple(row.absolute_path for row in record.source_bindings) == tuple(
        PRODUCTION_PATHS[key] for key in sorted(PRODUCTION_PATHS)
    )
    invocation = bind_production_invocation(record)
    assert verify_bound_production_invocation(invocation, record)
    assert invocation.consumable_attempts == 1
    assert len(invocation.query_ids) == 5
    assert not record.grants_integration_authority
    assert not record.grants_activation_authority
    assert len(production_binding_contract_digest()) == 64


@pytest.mark.parametrize("authority", ("FIXTURE", "LOCAL_TEST", "UNKNOWN"))
def test_fixture_and_unknown_authorization_tokens_rejected(tmp_path, authority):
    with pytest.raises(ProductionShadowProductionBindingError, match="FIXTURE_OR_UNKNOWN"):
        build_production_execution_authorization(
            authorization_id="approval-1", run_id="run-1",
            output_directory=tmp_path / "output", authority_class=authority,
        )


def test_unbound_or_altered_source_path_rejected(tmp_path):
    paths = dict(PRODUCTION_PATHS)
    paths["main"] = str(tmp_path / "flex_complete_database.db")
    with pytest.raises(ProductionShadowProductionBindingError, match="PATH_BINDING_DRIFT"):
        build_production_execution_authorization(
            authorization_id="approval-1", run_id="run-1",
            output_directory=tmp_path / "output", source_paths=paths,
        )


def test_reused_output_rejected(tmp_path):
    output = tmp_path / "output"; output.mkdir()
    with pytest.raises(ProductionShadowProductionBindingError, match="OUTPUT_NOT_NEW"):
        build_production_execution_authorization(
            authorization_id="approval-1", run_id="run-1", output_directory=output,
        )


@pytest.mark.parametrize("field,value,reason", (
    ("maximum_attempts", 2, "REPLAY_MISMATCH"),
    ("retries_allowed", True, "REPLAY_MISMATCH"),
    ("grants_integration_authority", True, "REPLAY_MISMATCH"),
    ("bound_preflight_digest", "0" * 64, "REPLAY_MISMATCH"),
    ("query_identity_digest", "0" * 64, "REPLAY_MISMATCH"),
))
def test_authority_lineage_and_query_fault_injection_fail_closed(tmp_path, field, value, reason):
    record = replace(_record(tmp_path), **{field: value})
    with pytest.raises(ProductionShadowProductionBindingError, match=reason):
        verify_production_execution_authorization(record)


def test_source_binding_fingerprint_fault_fails_closed(tmp_path):
    record = _record(tmp_path)
    rows = list(record.source_bindings)
    rows[0] = replace(rows[0], logical_path_fingerprint="0" * 64)
    with pytest.raises(ProductionShadowProductionBindingError, match="REPLAY_MISMATCH"):
        verify_production_execution_authorization(replace(record, source_bindings=tuple(rows)))


def test_invocation_mutation_fails_replay(tmp_path):
    record = _record(tmp_path)
    invocation = replace(bind_production_invocation(record), consumable_attempts=2)
    with pytest.raises(ProductionShadowProductionBindingError, match="INVOCATION_REPLAY_MISMATCH"):
        verify_bound_production_invocation(invocation, record)


def test_canonical_paths_have_expected_filenames():
    for row in canonical_production_source_bindings():
        assert Path(row.absolute_path).name == row.expected_filename


def test_production_entry_rejects_unbound_preflight_before_source_open(tmp_path):
    record = _record(tmp_path)
    cohort = build_immutable_cohort_artifact(
        cohort_id="fixture", mints=("mint-a",), source_artifact_digest="a" * 64,
    )
    preflight = build_production_shadow_run_preflight(
        run_id=record.run_id, cohort=cohort, fact_family="LaunchFact",
        output_directory=Path(record.output_directory),
    )
    with pytest.raises(ProductionShadowProductionBindingError, match="BOUND_PREFLIGHT_MISMATCH"):
        execute_production_shadow(
            record, preflight, prestart_health=None,
            active_health_check=lambda _: None, clock=lambda: 0.0,
        )
