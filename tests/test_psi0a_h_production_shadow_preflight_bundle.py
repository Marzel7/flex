import hashlib
import json

import pytest

from src.evidence.contracts.production_shadow_preflight_bundle import (
    ABORT_ISOLATION_CONTRACT_DIGEST,
    CANONICAL_MANIFEST_DIGEST,
    CLOSURE_VERDICT,
    HEALTH_GATE_CONTRACT_DIGEST,
    PLAN_QUALIFICATION_DIGEST,
    READ_BOUNDARY_DIGEST,
    RESOURCE_CEILING_CONTRACT_DIGEST,
    ProductionShadowPreflightBundleError,
    build_preflight_component_summary,
    verify_production_shadow_preflight_bundle,
    write_production_shadow_preflight_bundle,
)


def _components(**changes):
    values = {
        "capture_manifest": (CANONICAL_MANIFEST_DIGEST, 5),
        "read_boundary": (READ_BOUNDARY_DIGEST, 5),
        "query_plan_qualification": (PLAN_QUALIFICATION_DIGEST, 5),
        "resource_ceiling": (RESOURCE_CEILING_CONTRACT_DIGEST, 5),
        "health_gate": (HEALTH_GATE_CONTRACT_DIGEST, 3),
        "abort_isolation": (ABORT_ISOLATION_CONTRACT_DIGEST, 11),
    }
    values.update(changes)
    return tuple(build_preflight_component_summary(
        component=name, status="PASS", identity_digest=digest, item_count=count,
    ) for name, (digest, count) in values.items())


def _canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _rehash(output):
    names = {"run.json", "lineage.json", "preflight.json", "closure.json"}
    files = {name: hashlib.sha256((output / name).read_bytes()).hexdigest() for name in names}
    hashes = {
        "bundle_version": "psi0a-h.v1",
        "files": files,
        "bundle_digest": hashlib.sha256(_canonical(files)).hexdigest(),
    }
    (output / "hashes.json").write_bytes(_canonical(hashes))


def test_write_once_bundle_exact_files_hashes_lineage_and_replay(tmp_path):
    output = tmp_path / "bundle"
    written = write_production_shadow_preflight_bundle(
        _components(), output, run_id="psi0a-h-fixture",
    )
    assert {item.name for item in output.iterdir()} == {
        "run.json", "lineage.json", "preflight.json", "closure.json", "hashes.json",
    }
    assert written.closure_verdict == CLOSURE_VERDICT
    assert verify_production_shadow_preflight_bundle(output) == written


def test_closure_grants_no_psi0b_extraction_integration_or_activation(tmp_path):
    output = tmp_path / "bundle"
    write_production_shadow_preflight_bundle(_components(), output, run_id="closure")
    closure = json.loads((output / "closure.json").read_text())
    assert closure == {
        "verdict": CLOSURE_VERDICT,
        "psi0b_execution_authorized": False,
        "production_integration_authorized": False,
        "grants_extraction_authority": False,
        "grants_activation_authority": False,
        "separate_psi0b_authorization_required": True,
    }


def test_existing_output_and_staging_fail_closed_without_overwrite(tmp_path):
    output = tmp_path / "bundle"
    output.mkdir()
    with pytest.raises(ProductionShadowPreflightBundleError, match="OUTPUT_ALREADY_EXISTS"):
        write_production_shadow_preflight_bundle(_components(), output, run_id="run")
    output.rmdir()
    staging = tmp_path / ".bundle.run.tmp"
    staging.mkdir()
    with pytest.raises(ProductionShadowPreflightBundleError, match="STAGING_ALREADY_EXISTS"):
        write_production_shadow_preflight_bundle(_components(), output, run_id="run")
    assert not output.exists()


@pytest.mark.parametrize("mutation", ("missing", "extra", "altered", "noncanonical"))
def test_missing_extra_altered_and_noncanonical_content_fails_closed(tmp_path, mutation):
    output = tmp_path / mutation
    write_production_shadow_preflight_bundle(_components(), output, run_id=mutation)
    if mutation == "missing":
        (output / "closure.json").unlink()
        reason = "FILE_SET"
    elif mutation == "extra":
        (output / "extra.json").write_text("{}")
        reason = "FILE_SET"
    elif mutation == "altered":
        (output / "closure.json").write_text("{}\n")
        reason = "DIGEST"
    else:
        value = json.loads((output / "run.json").read_text())
        (output / "run.json").write_text(json.dumps(value, indent=2))
        reason = "NONCANONICAL"
    with pytest.raises(ProductionShadowPreflightBundleError, match=reason):
        verify_production_shadow_preflight_bundle(output)


def test_rehashed_authority_or_lineage_change_still_fails(tmp_path):
    output = tmp_path / "authority"
    write_production_shadow_preflight_bundle(_components(), output, run_id="authority")
    run = json.loads((output / "run.json").read_text())
    run["grants_extraction_authority"] = True
    (output / "run.json").write_bytes(_canonical(run))
    _rehash(output)
    with pytest.raises(ProductionShadowPreflightBundleError, match="AUTHORITY"):
        verify_production_shadow_preflight_bundle(output)

    output = tmp_path / "lineage"
    write_production_shadow_preflight_bundle(_components(), output, run_id="lineage")
    lineage = json.loads((output / "lineage.json").read_text())
    lineage["read_boundary_digest"] = "a" * 64
    (output / "lineage.json").write_bytes(_canonical(lineage))
    _rehash(output)
    with pytest.raises(ProductionShadowPreflightBundleError, match="LINEAGE"):
        verify_production_shadow_preflight_bundle(output)


@pytest.mark.parametrize(
    "components",
    (
        lambda: _components(read_boundary=("a" * 64, 5)),
        lambda: _components(health_gate=(HEALTH_GATE_CONTRACT_DIGEST, 2)),
        lambda: _components()[:-1],
    ),
)
def test_drift_missing_and_count_mismatch_rejected_before_write(tmp_path, components):
    output = tmp_path / "bundle"
    with pytest.raises(ProductionShadowPreflightBundleError, match="COMPONENT"):
        write_production_shadow_preflight_bundle(components(), output, run_id="run")
    assert not output.exists()


def test_invalid_run_id_rejected_before_write(tmp_path):
    with pytest.raises(ProductionShadowPreflightBundleError, match="INVALID_RUN_ID"):
        write_production_shadow_preflight_bundle(
            _components(), tmp_path / "bundle", run_id="../escape",
        )
