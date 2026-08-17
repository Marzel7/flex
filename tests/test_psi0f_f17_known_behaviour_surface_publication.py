from dataclasses import replace
import json
from pathlib import Path

import pytest

import src.evidence.contracts.known_behaviour_surface_publication as publication
from src.evidence.contracts.known_behaviour_surface_publication import (
    KnownBehaviourSurfacePublicationError,
    build_known_behaviour_surface_publication_contract,
    publish_fixture_known_behaviour_surface,
    verify_fixture_known_behaviour_surface_publication,
    verify_known_behaviour_surface_publication_contract,
)
from src.evidence.contracts.operational_family_retained_input_store import (
    publish_fixture_operational_family_retained_inputs,
)
from tests.test_psi0f_d_known_behaviour_operational_surface_adapters import psi0e_files
from tests.test_psi0f_f13_operational_family_retained_input_store import review_metadata
from tests.test_psi0f_f5_operational_family_source_materialization import material


def retained(path: Path, values=None):
    values = values or material()
    return publish_fixture_operational_family_retained_inputs(
        path, **values, review_metadata=review_metadata(values), logical_capture_sequence=7,
    )


def apply(monkeypatch, tmp_path, *, output_name="publication", values=None):
    source = retained(tmp_path / f"{output_name}-retained.db", values)
    psi_files, _ = psi0e_files(monkeypatch)
    result = publish_fixture_known_behaviour_surface(
        build_known_behaviour_surface_publication_contract(), retained_source=source.path,
        retention_id=source.retention_id, psi0e_files=psi_files,
        output_directory=(tmp_path / output_name).resolve(), run_id="fixture-f17",
    )
    return source, psi_files, result


def test_contract_is_atomic_fixture_only_and_authority_free():
    contract = build_known_behaviour_surface_publication_contract()
    assert verify_known_behaviour_surface_publication_contract(contract)
    assert contract.fixture_only and contract.output_requires_new_path and contract.atomic_root_publication
    assert not contract.cross_layer_join_allowed and not contract.real_capture_authorized
    assert not any(contract.authority.values())
    with pytest.raises(KnownBehaviourSurfacePublicationError, match="CONTRACT_REPLAY_MISMATCH"):
        verify_known_behaviour_surface_publication_contract(
            replace(contract, real_capture_authorized=True)
        )


def test_end_to_end_retained_store_to_eb0_4h_and_surface_replays(monkeypatch, tmp_path):
    source, psi_files, result = apply(monkeypatch, tmp_path)
    replay = verify_fixture_known_behaviour_surface_publication(
        build_known_behaviour_surface_publication_contract(), output_directory=result.output_directory,
        retained_source=source.path, retention_id=source.retention_id, psi0e_files=psi_files,
    )
    assert replay == result
    assert set(item.name for item in result.output_directory.iterdir()) == {"eb0_4h", "surface"}
    assert set(item.name for item in (result.output_directory / "eb0_4h").iterdir()) == {
        "run.json", "accounting.json", "manifests.json", "corpora.json", "hashes.json",
    }
    assert set(item.name for item in (result.output_directory / "surface").iterdir()) == {
        "run.json", "surface.json", "hashes.json",
    }
    surface = json.loads((result.output_directory / "surface" / "surface.json").read_bytes())
    assert surface["cross_layer_join_performed"] is False
    assert not any(surface["authority"].values()) and not any(surface["interpretation"].values())
    assert set(surface["operational_roles"]) == {"PROPOSED_ROLE", "SUPPORTED_ROLE"}


def test_f5_source_and_f9_retention_identities_are_preserved(monkeypatch, tmp_path):
    source, _, result = apply(monkeypatch, tmp_path)
    run = json.loads((result.output_directory / "surface" / "run.json").read_bytes())
    assert result.retention_id == source.retention_id == run["retention_id"]
    assert result.retention_manifest_digest == source.manifest_digest == run["retention_manifest_digest"]
    assert result.f9_bundle_digest == source.bundle_digest == run["f9_bundle_digest"]
    assert result.f5_source_digest == source.source_digest == run["f5_source_digest"]
    assert result.eb0_4h_bundle_digest == run["eb0_4h_bundle_digest"]
    assert result.surface_digest == run["surface_digest"]


def test_input_collection_order_is_end_to_end_identity_independent(monkeypatch, tmp_path):
    values = material()
    _, _, first = apply(monkeypatch, tmp_path, output_name="first", values=values)
    reversed_values = material()
    for name in ("cohort", "evaluations", "runtime", "candidates", "dispositions"):
        reversed_values[name] = list(reversed(reversed_values[name]))
    _, _, second = apply(monkeypatch, tmp_path, output_name="second", values=reversed_values)
    assert first.f9_bundle_digest == second.f9_bundle_digest
    assert first.f5_source_digest == second.f5_source_digest
    assert first.eb0_4h_bundle_digest == second.eb0_4h_bundle_digest
    assert first.surface_digest == second.surface_digest
    assert first.publication_digest == second.publication_digest


def test_output_reuse_is_rejected_without_altering_owner_content(monkeypatch, tmp_path):
    source = retained(tmp_path / "retained.db")
    psi_files, _ = psi0e_files(monkeypatch)
    output = (tmp_path / "publication").resolve()
    output.mkdir()
    marker = output / "owned"
    marker.write_text("owner")
    with pytest.raises(KnownBehaviourSurfacePublicationError, match="OUTPUT_NOT_NEW"):
        publish_fixture_known_behaviour_surface(
            build_known_behaviour_surface_publication_contract(), retained_source=source.path,
            retention_id=source.retention_id, psi0e_files=psi_files,
            output_directory=output, run_id="fixture-f17",
        )
    assert marker.read_text() == "owner"


def test_adapter_failure_leaves_no_partial_publication(monkeypatch, tmp_path):
    source = retained(tmp_path / "retained.db")
    psi_files, _ = psi0e_files(monkeypatch)
    output = (tmp_path / "publication").resolve()
    monkeypatch.setattr(publication, "project_immutable_summary_bytes", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("fault")))
    with pytest.raises(RuntimeError, match="fault"):
        publish_fixture_known_behaviour_surface(
            build_known_behaviour_surface_publication_contract(), retained_source=source.path,
            retention_id=source.retention_id, psi0e_files=psi_files,
            output_directory=output, run_id="fixture-f17",
        )
    assert not output.exists()
    assert not list(tmp_path.glob(".publication.staging-*"))


def test_stale_surface_hash_tamper_fails_closed(monkeypatch, tmp_path):
    source, psi_files, result = apply(monkeypatch, tmp_path)
    path = result.output_directory / "surface" / "surface.json"
    document = json.loads(path.read_bytes())
    document["cross_layer_join_performed"] = True
    path.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n")
    with pytest.raises(KnownBehaviourSurfacePublicationError, match="HASH_REPLAY_MISMATCH"):
        verify_fixture_known_behaviour_surface_publication(
            build_known_behaviour_surface_publication_contract(), output_directory=result.output_directory,
            retained_source=source.path, retention_id=source.retention_id, psi0e_files=psi_files,
        )


def test_rehashed_surface_tamper_fails_content_replay(monkeypatch, tmp_path):
    source, psi_files, result = apply(monkeypatch, tmp_path)
    directory = result.output_directory / "surface"
    surface_path = directory / "surface.json"
    document = json.loads(surface_path.read_bytes())
    document["cross_layer_join_performed"] = True
    surface_path.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n")
    run_payload = (directory / "run.json").read_bytes()
    surface_payload = surface_path.read_bytes()
    from hashlib import sha256
    file_digests = {"run.json": sha256(run_payload).hexdigest(), "surface.json": sha256(surface_payload).hexdigest()}
    publication_digest = publication._digest(file_digests)
    (directory / "hashes.json").write_bytes(publication._canonical({
        "schema_version": publication.PUBLICATION_SCHEMA_VERSION,
        "file_digests": file_digests, "publication_digest": publication_digest,
    }))
    with pytest.raises(KnownBehaviourSurfacePublicationError, match="PUBLICATION_CONTENT_REPLAY_MISMATCH"):
        verify_fixture_known_behaviour_surface_publication(
            build_known_behaviour_surface_publication_contract(), output_directory=result.output_directory,
            retained_source=source.path, retention_id=source.retention_id, psi0e_files=psi_files,
        )


def test_rehashed_surface_run_cannot_diverge_from_eb0_4h_run(monkeypatch, tmp_path):
    source, psi_files, result = apply(monkeypatch, tmp_path)
    directory = result.output_directory / "surface"
    run_path = directory / "run.json"
    run = json.loads(run_path.read_bytes())
    run["run_id"] = "diverged"
    run_path.write_bytes(publication._canonical(run))
    from hashlib import sha256
    file_digests = {
        "run.json": sha256(run_path.read_bytes()).hexdigest(),
        "surface.json": sha256((directory / "surface.json").read_bytes()).hexdigest(),
    }
    (directory / "hashes.json").write_bytes(publication._canonical({
        "schema_version": publication.PUBLICATION_SCHEMA_VERSION,
        "file_digests": file_digests,
        "publication_digest": publication._digest(file_digests),
    }))
    with pytest.raises(KnownBehaviourSurfacePublicationError, match="CROSS_PUBLICATION_RUN_IDENTITY_DRIFT"):
        verify_fixture_known_behaviour_surface_publication(
            build_known_behaviour_surface_publication_contract(), output_directory=result.output_directory,
            retained_source=source.path, retention_id=source.retention_id, psi0e_files=psi_files,
        )


def test_eb0_4h_tamper_and_wrong_psi0e_identity_fail_closed(monkeypatch, tmp_path):
    source, psi_files, result = apply(monkeypatch, tmp_path)
    accounting = result.output_directory / "eb0_4h" / "accounting.json"
    accounting.write_bytes(b"{}\n")
    with pytest.raises(Exception, match="EB0_4H"):
        verify_fixture_known_behaviour_surface_publication(
            build_known_behaviour_surface_publication_contract(), output_directory=result.output_directory,
            retained_source=source.path, retention_id=source.retention_id, psi0e_files=psi_files,
        )
    _, psi_files, result = apply(monkeypatch, tmp_path, output_name="second")
    changed = dict(psi_files)
    changed["envelope.json"] = b"{}\n"
    with pytest.raises(Exception, match="PSI0E"):
        verify_fixture_known_behaviour_surface_publication(
            build_known_behaviour_surface_publication_contract(), output_directory=result.output_directory,
            retained_source=tmp_path / "second-retained.db", retention_id=result.retention_id,
            psi0e_files=changed,
        )


def test_root_and_surface_file_set_drift_fail_closed(monkeypatch, tmp_path):
    source, psi_files, result = apply(monkeypatch, tmp_path)
    (result.output_directory / "extra").write_text("x")
    with pytest.raises(KnownBehaviourSurfacePublicationError, match="ROOT_FILE_SET_MISMATCH"):
        verify_fixture_known_behaviour_surface_publication(
            build_known_behaviour_surface_publication_contract(), output_directory=result.output_directory,
            retained_source=source.path, retention_id=source.retention_id, psi0e_files=psi_files,
        )
