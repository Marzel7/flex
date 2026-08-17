from dataclasses import replace
import json
from pathlib import Path

import pytest

import src.evidence.contracts.operational_family_rematerialization as subject
from src.evidence.contracts.operational_family_bundle import verify_operational_family_bundle
from src.evidence.contracts.operational_family_rematerialization import (
    OperationalFamilyRematerializationError,
    build_immutable_operational_family_source,
    build_operational_family_rematerialization_contract,
    rematerialize_operational_family_bundle,
    verify_immutable_operational_family_source,
    verify_operational_family_rematerialization_contract,
)


REVISION = "4215f899b510f7d366c21702ce6cef63c6882878"


def material():
    operations = ["operation-alpha", "operation-beta", "operation-gamma", "operation-delta"]
    cohort = [{"position": position, "operation_id": operation} for position, operation in enumerate(operations)]
    runtime = []
    for position, operation in enumerate(operations):
        proposed = position >= 2
        runtime.append({
            "schema_version": "eb0.4c.normalized-runtime.v1",
            "identity_basis": "PLATFORM_OPERATION_ID",
            "operation_id": operation,
            "primary_role": "ROLE_PROPOSED" if proposed else "ROLE_SUPPORTED",
            "contract_id": f"contract-{position}",
            "contract_version": "1",
            "module_id": f"module-{position}",
            "module_version": "1",
            "topology_revision_id": f"topology-{position}",
            "behaviour_observation_id": f"behaviour-{position}",
            "input_digest": f"input-{position}",
            "edge_features": ["WRITES_TO_QUEUE"],
            "mechanism_features": ["BOUNDED_BATCH"],
            "temporal_features": ["PERIODIC_HEARTBEAT"],
            "quality_state": "CONFLICTING" if proposed else "OBSERVED",
            "completeness_state": "PARTIAL" if proposed else "COMPLETE",
            "conflict_group_id": "conflict-proposed" if proposed else None,
        })
    memberships = [
        {"group_id": "group-proposed", "position": 0, "operation_id": operations[2], "nomination_state": "PROPOSED"},
        {"group_id": "group-proposed", "position": 1, "operation_id": operations[3], "nomination_state": "PROPOSED"},
        {"group_id": "group-supported", "position": 0, "operation_id": operations[0], "nomination_state": "SUPPORTED"},
        {"group_id": "group-supported", "position": 1, "operation_id": operations[1], "nomination_state": "SUPPORTED"},
    ]
    vocabulary = {
        "roles": ["ROLE_SUPPORTED", "ROLE_PROPOSED"],
        "edge": ["WRITES_TO_QUEUE"],
        "mechanism": ["BOUNDED_BATCH"],
        "temporal": ["PERIODIC_HEARTBEAT"],
    }
    return cohort, runtime, memberships, vocabulary


def source_payload(**changes):
    cohort, runtime, memberships, vocabulary = material()
    values = {"cohort": cohort, "runtime": runtime, "memberships": memberships, "vocabulary": vocabulary}
    values.update(changes)
    return build_immutable_operational_family_source(build_operational_family_rematerialization_contract(), **values)


def canonical(document):
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"


def test_contract_is_fixture_only_replayable_and_has_no_authority():
    contract = build_operational_family_rematerialization_contract()
    assert verify_operational_family_rematerialization_contract(contract)
    assert contract.fixture_only and not contract.retries_allowed
    assert not contract.topology_only_support_allowed and not contract.implicit_membership_allowed
    assert not contract.retains_source_values_outside_boundary and not any(contract.authority.values())
    with pytest.raises(OperationalFamilyRematerializationError, match="CONTRACT_REPLAY_MISMATCH"):
        verify_operational_family_rematerialization_contract(replace(contract, retries_allowed=True))


def test_source_manifest_is_canonical_accounted_hashed_and_replayable():
    contract = build_operational_family_rematerialization_contract()
    payload = source_payload()
    source_digest = verify_immutable_operational_family_source(contract, payload)
    document = json.loads(payload)
    assert document["accounting"] == {"candidate_group_count": 2, "cohort_count": 4, "membership_count": 4, "runtime_count": 4}
    assert document["source_digest"] == source_digest
    assert document["provenance_class"] == contract.source_provenance_class
    assert not any(document["authority"].values())


def test_input_order_is_canonicalized_without_changing_explicit_positions():
    cohort, runtime, memberships, vocabulary = material()
    first = source_payload()
    second = source_payload(cohort=list(reversed(cohort)), runtime=list(reversed(runtime)), memberships=list(reversed(memberships)), vocabulary=dict(reversed(list(vocabulary.items()))))
    assert first == second


def test_rematerializes_exact_replayable_bundle_with_proposed_supported_and_conflicts(tmp_path):
    output = tmp_path / "bundle"
    result = rematerialize_operational_family_bundle(
        build_operational_family_rematerialization_contract(), source_payload(), output,
        run_id="fixture-run", engineering_revision=REVISION,
    )
    assert {item.name for item in output.iterdir()} == {"run.json", "accounting.json", "manifests.json", "corpora.json", "hashes.json"}
    verified = verify_operational_family_bundle(output)
    assert verified.bundle_digest == result.bundle_digest
    accounting = json.loads((output / "accounting.json").read_bytes())
    manifests = json.loads((output / "manifests.json").read_bytes())["manifests"]
    assert accounting["candidate_group_count"] == 2 and accounting["conflict_count"] == 2
    assert manifests[0]["nomination_state_counts"] == {"PROPOSED": 1, "SUPPORTED": 1}
    assert result.contract_digest == build_operational_family_rematerialization_contract().contract_digest


def test_same_logical_source_and_run_produce_identical_bundle_bytes(tmp_path):
    contract = build_operational_family_rematerialization_contract()
    outputs = [tmp_path / "one", tmp_path / "two"]
    results = [rematerialize_operational_family_bundle(contract, source_payload(), output, run_id="stable", engineering_revision=REVISION) for output in outputs]
    assert results[0].bundle_digest == results[1].bundle_digest
    assert {item.name: item.read_bytes() for item in outputs[0].iterdir()} == {item.name: item.read_bytes() for item in outputs[1].iterdir()}


@pytest.mark.parametrize("mutation,code", [
    (lambda d: d.pop("accounting"), "SOURCE_SCHEMA_DRIFT"),
    (lambda d: d.update(extra=True), "SOURCE_SCHEMA_DRIFT"),
    (lambda d: d.update(engineering_revision="0" * 40), "SOURCE_LINEAGE_DRIFT"),
    (lambda d: d["authority"].update(ranking=True), "AUTHORITY_DRIFT"),
    (lambda d: d["component_digests"].update(operation_cohort="0" * 64), "COMPONENT_DIGEST_DRIFT"),
    (lambda d: d["accounting"].update(cohort_count=999), "ACCOUNTING_DRIFT"),
    (lambda d: d.update(source_digest="0" * 64), "SOURCE_DIGEST_DRIFT"),
])
def test_manifest_drift_fails_closed(mutation, code):
    document = json.loads(source_payload())
    mutation(document)
    with pytest.raises(OperationalFamilyRematerializationError, match=code):
        verify_immutable_operational_family_source(build_operational_family_rematerialization_contract(), canonical(document))


def test_noncanonical_malformed_and_nonbyte_sources_fail_closed():
    contract = build_operational_family_rematerialization_contract()
    with pytest.raises(OperationalFamilyRematerializationError, match="SOURCE_NONCANONICAL"):
        verify_immutable_operational_family_source(contract, source_payload().rstrip(b"\n"))
    with pytest.raises(OperationalFamilyRematerializationError, match="SOURCE_INVALID_JSON"):
        verify_immutable_operational_family_source(contract, b"not-json")
    with pytest.raises(OperationalFamilyRematerializationError, match="SOURCE_BYTES_REQUIRED"):
        verify_immutable_operational_family_source(contract, "not-bytes")


def test_invalid_cohort_duplicate_runtime_and_orphans_fail_closed():
    cohort, runtime, memberships, vocabulary = material()
    with pytest.raises(OperationalFamilyRematerializationError, match="INVALID_COHORT_ORDER"):
        source_payload(cohort=[{**cohort[0], "position": 1}, *cohort[1:]])
    with pytest.raises(OperationalFamilyRematerializationError, match="DUPLICATE_RUNTIME"):
        source_payload(runtime=[*runtime, dict(runtime[0])])
    with pytest.raises(OperationalFamilyRematerializationError, match="ORPHAN_RUNTIME"):
        source_payload(runtime=[{**runtime[0], "operation_id": "orphan"}, *runtime[1:]])
    with pytest.raises(OperationalFamilyRematerializationError, match="ORPHAN_MEMBERSHIP"):
        source_payload(memberships=[{**memberships[0], "operation_id": "orphan"}, *memberships[1:]])


def test_ambiguous_membership_and_nomination_authority_fail_closed():
    _, _, memberships, _ = material()
    with pytest.raises(OperationalFamilyRematerializationError, match="AMBIGUOUS_CANDIDATE_GROUP"):
        source_payload(memberships=[memberships[0], *memberships[2:]])
    with pytest.raises(OperationalFamilyRematerializationError, match="AMBIGUOUS_CANDIDATE_GROUP"):
        source_payload(memberships=[memberships[0], {**memberships[1], "nomination_state": "SUPPORTED"}, *memberships[2:]])
    with pytest.raises(OperationalFamilyRematerializationError, match="INVALID_NOMINATION_STATE"):
        source_payload(memberships=[{**memberships[0], "nomination_state": "CONFIRMED"}, *memberships[1:]])


def test_unknown_role_descriptor_topology_only_and_forbidden_vocabulary_fail_closed():
    _, runtime, _, vocabulary = material()
    with pytest.raises(OperationalFamilyRematerializationError, match="UNKNOWN_ROLE"):
        source_payload(runtime=[{**runtime[0], "primary_role": "UNKNOWN"}, *runtime[1:]])
    with pytest.raises(OperationalFamilyRematerializationError, match="UNKNOWN_DESCRIPTOR"):
        source_payload(runtime=[{**runtime[0], "mechanism_features": ["UNKNOWN"]}, *runtime[1:]])
    with pytest.raises(OperationalFamilyRematerializationError, match="RUNTIME_CONTRACT_REJECTED"):
        source_payload(runtime=[{**runtime[0], "mechanism_features": [], "temporal_features": []}, *runtime[1:]])
    with pytest.raises(OperationalFamilyRematerializationError, match="INVALID_VOCABULARY"):
        source_payload(vocabulary={**vocabulary, "roles": ["operator-owner"]})


def test_output_must_be_new_and_no_retry_or_alternate_output_occurs(tmp_path):
    output = tmp_path / "bundle"; output.mkdir()
    with pytest.raises(OperationalFamilyRematerializationError, match="OUTPUT_NOT_NEW"):
        rematerialize_operational_family_bundle(build_operational_family_rematerialization_contract(), source_payload(), output, run_id="run", engineering_revision=REVISION)
    assert list(output.iterdir()) == []


@pytest.mark.parametrize("stage", ["fixture", "extractor", "publisher", "fsync", "rename", "partial_rename"])
def test_faults_remove_staging_and_publish_nothing(tmp_path, monkeypatch, stage):
    output = tmp_path / "bundle"
    kwargs = {}
    if stage == "fixture":
        monkeypatch.setattr(subject, "_write_fixture", lambda *args: (_ for _ in ()).throw(RuntimeError("fixture")))
    elif stage == "extractor":
        kwargs["extractor"] = lambda *args, **values: (_ for _ in ()).throw(RuntimeError("extractor"))
    elif stage == "publisher":
        kwargs["publisher"] = lambda *args, **values: (_ for _ in ()).throw(RuntimeError("publisher"))
    elif stage == "fsync":
        kwargs["fsync"] = lambda descriptor: (_ for _ in ()).throw(OSError("fsync"))
    elif stage == "rename":
        kwargs["rename"] = lambda source, target: (_ for _ in ()).throw(OSError("rename"))
    elif stage == "partial_rename":
        def partial_rename(source, target):
            source.rename(target)
            raise OSError("partial rename")
        kwargs["rename"] = partial_rename
    with pytest.raises((RuntimeError, OSError)):
        rematerialize_operational_family_bundle(build_operational_family_rematerialization_contract(), source_payload(), output, run_id="run", engineering_revision=REVISION, **kwargs)
    assert not output.exists()
    assert not list(tmp_path.glob(".bundle.staging-*"))


def test_post_publication_replay_failure_removes_renamed_output(tmp_path):
    output = tmp_path / "bundle"
    calls = 0
    def verifier(path: Path):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("replay")
        return verify_operational_family_bundle(path)
    with pytest.raises(RuntimeError, match="replay"):
        rematerialize_operational_family_bundle(build_operational_family_rematerialization_contract(), source_payload(), output, run_id="run", engineering_revision=REVISION, verifier=verifier)
    assert not output.exists()
    assert not list(tmp_path.glob(".bundle.staging-*"))


def test_extractor_deadline_failure_is_not_retried_and_cleans_up(tmp_path):
    output = tmp_path / "bundle"
    calls = 0
    def extractor(path):
        nonlocal calls
        calls += 1
        raise RuntimeError("EB0_4G_QUERY_TIMEOUT")
    with pytest.raises(RuntimeError, match="QUERY_TIMEOUT"):
        rematerialize_operational_family_bundle(build_operational_family_rematerialization_contract(), source_payload(), output, run_id="run", engineering_revision=REVISION, extractor=extractor)
    assert calls == 1 and not output.exists()


def test_result_retains_only_path_and_immutable_identities(tmp_path):
    result = rematerialize_operational_family_bundle(build_operational_family_rematerialization_contract(), source_payload(), tmp_path / "bundle", run_id="run", engineering_revision=REVISION)
    assert set(result.__dataclass_fields__) == {"output_directory", "source_digest", "extraction_result_digest", "bundle_digest", "file_digests", "contract_digest"}
