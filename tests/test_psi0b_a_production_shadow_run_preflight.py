from dataclasses import replace

import pytest

from src.evidence.contracts.production_shadow_run_preflight import (
    MAXIMUM_ROWS_PER_QUERY,
    PATH_BINDINGS,
    QUERY_BOUNDARIES,
    ProductionShadowRunPreflightError,
    build_immutable_cohort_artifact,
    build_production_shadow_run_preflight,
    canonical_query_parameters,
    canonical_source_bindings,
    production_shadow_run_preflight_contract_digest,
    verify_immutable_cohort_artifact,
    verify_production_shadow_run_preflight,
)


SOURCE = "a" * 64


def _cohort(mints=("mint-a", "mint-b")):
    return build_immutable_cohort_artifact(
        cohort_id="fixture-cohort", mints=mints, source_artifact_digest=SOURCE,
    )


def _preflight(tmp_path):
    return build_production_shadow_run_preflight(
        run_id="psi0b-a-fixture", cohort=_cohort(), fact_family="LaunchFact",
        output_directory=tmp_path / "new-output",
    )


def test_caller_cohort_and_run_preflight_replay_exactly(tmp_path):
    cohort = _cohort()
    assert verify_immutable_cohort_artifact(cohort)
    preflight = _preflight(tmp_path)
    assert verify_production_shadow_run_preflight(preflight)
    assert not preflight.grants_extraction_authority
    assert not preflight.grants_integration_authority
    assert not preflight.grants_activation_authority
    assert len(production_shadow_run_preflight_contract_digest()) == 64


def test_exact_c16_boundaries_and_five_query_parameters_are_frozen(tmp_path):
    preflight = _preflight(tmp_path)
    assert {item.query_id: item.rowid_upper_inclusive for item in preflight.query_parameters} == QUERY_BOUNDARIES
    assert all(item.row_limit == MAXIMUM_ROWS_PER_QUERY for item in preflight.query_parameters)
    evidence = next(item for item in preflight.query_parameters if item.query_id == "evidence_launch_facts")
    assert evidence.fact_family == "LaunchFact" and evidence.cohort_digest is None
    assert all(item.cohort_digest == preflight.cohort.cohort_digest for item in preflight.query_parameters if item is not evidence)


def test_exact_corrected_source_fingerprints_are_bound(tmp_path):
    preflight = _preflight(tmp_path)
    observed = {item.logical_source: (item.expected_filename, item.path_binding_digest) for item in preflight.source_bindings}
    assert observed == PATH_BINDINGS
    assert preflight.source_bindings == canonical_source_bindings()


def test_health_is_placeholder_only_and_never_fabricated(tmp_path):
    preflight = _preflight(tmp_path)
    assert len(preflight.health_checkpoint_placeholders) == 3
    assert all(item.endswith("REQUIRED_AT_EXECUTION") for item in preflight.health_checkpoint_placeholders)


@pytest.mark.parametrize(
    "mints",
    ((), ("x", "x"), tuple(f"m{i}" for i in range(5001)), ("",)),
)
def test_missing_duplicate_oversized_or_invalid_cohort_fails_closed(mints):
    with pytest.raises(ProductionShadowRunPreflightError, match="COHORT"):
        _cohort(mints)


def test_existing_output_and_invalid_run_id_fail_before_preflight(tmp_path):
    output = tmp_path / "existing"; output.mkdir()
    with pytest.raises(ProductionShadowRunPreflightError, match="OUTPUT_NOT_NEW"):
        build_production_shadow_run_preflight(
            run_id="run", cohort=_cohort(), fact_family="LaunchFact", output_directory=output,
        )
    with pytest.raises(ProductionShadowRunPreflightError, match="INVALID_RUN_ID"):
        build_production_shadow_run_preflight(
            run_id="../escape", cohort=_cohort(), fact_family="LaunchFact",
            output_directory=tmp_path / "other",
        )


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    (
        ("psi0a_h_bundle_digest", "b" * 64, "LINEAGE"),
        ("source_bindings", (), "PATH_BINDING"),
        ("query_parameters", (), "QUERY_OR_BOUNDARY"),
        ("health_checkpoint_placeholders", (), "HEALTH_PLACEHOLDER"),
        ("retry_allowed", True, "AUTHORITY_OR_WIDENING"),
        ("grants_extraction_authority", True, "AUTHORITY_OR_WIDENING"),
    ),
)
def test_lineage_path_parameter_health_and_authority_drift_fail(field, value, reason, tmp_path):
    preflight = _preflight(tmp_path)
    with pytest.raises(ProductionShadowRunPreflightError, match=reason):
        verify_production_shadow_run_preflight(replace(preflight, **{field: value}))


def test_cohort_and_preflight_digest_mutations_fail_replay(tmp_path):
    cohort = _cohort()
    with pytest.raises(ProductionShadowRunPreflightError, match="COHORT_REPLAY"):
        verify_immutable_cohort_artifact(replace(cohort, cohort_id="changed"))
    preflight = _preflight(tmp_path)
    with pytest.raises(ProductionShadowRunPreflightError, match="PREFLIGHT_REPLAY"):
        verify_production_shadow_run_preflight(replace(preflight, run_id="changed"))


def test_empty_fact_family_is_rejected(tmp_path):
    with pytest.raises(ProductionShadowRunPreflightError, match="FACT_FAMILY"):
        build_production_shadow_run_preflight(
            run_id="run", cohort=_cohort(), fact_family="", output_directory=tmp_path / "out",
        )
