from dataclasses import replace
from hashlib import sha256
import json

import pytest

from src.evidence.contracts.operational_family_retention_bundle import (
    BUNDLE_FILES,
    OperationalFamilyRetentionBundleError,
    build_fixture_operational_family_retention_bundle,
    build_operational_family_retention_bundle_contract,
    replay_fixture_operational_family_retention_bundle,
    verify_operational_family_retention_bundle_contract,
)
from tests.test_psi0f_f5_operational_family_source_materialization import material


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"


def build(values=None):
    return build_fixture_operational_family_retention_bundle(
        build_operational_family_retention_bundle_contract(), **(values or material()),
    )


def rehash(files):
    data_files = [name for name in BUNDLE_FILES if name != "hashes.json"]
    hashes = {name: sha256(files[name]).hexdigest() for name in data_files}
    digest = sha256(json.dumps(hashes, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
    files["hashes.json"] = canonical({
        "schema_version": "psi0f-f9.v1.hashes", "files": hashes, "bundle_digest": digest,
    })


def test_contract_is_complete_pure_fixture_only_and_authority_free():
    contract = build_operational_family_retention_bundle_contract()
    assert verify_operational_family_retention_bundle_contract(contract)
    assert contract.complete_f5_replay_inputs and contract.fixture_only and not contract.performs_io
    assert not contract.lifecycle_grants_nomination_authority and not any(contract.authority.values())
    with pytest.raises(OperationalFamilyRetentionBundleError, match="CONTRACT_REPLAY_MISMATCH"):
        verify_operational_family_retention_bundle_contract(replace(contract, performs_io=True))


def test_bundle_has_exact_canonical_complete_f5_input_file_set_and_hashes():
    bundle = build()
    assert set(bundle.files) == set(BUNDLE_FILES)
    assert {"cohort.json", "runtime.json", "vocabulary.json"}.issubset(bundle.files)
    assert all(payload == canonical(json.loads(payload)) for payload in bundle.files.values())
    assert json.loads(bundle.files["hashes.json"])["bundle_digest"] == bundle.bundle_digest


def test_replay_reconstructs_exact_f5_source_identity_and_accounting():
    bundle = build()
    source = replay_fixture_operational_family_retention_bundle(
        build_operational_family_retention_bundle_contract(), bundle.files,
    )
    assert source.source_digest == bundle.source_digest
    assert (source.operation_count, source.runtime_count, source.candidate_group_count, source.membership_count) == (4, 4, 2, 4)
    assert json.loads(bundle.files["accounting.json"])["nomination_states"] == {"PROPOSED": 1, "SUPPORTED": 1}


def test_input_collection_order_is_independent():
    values = material()
    expected = build(values)
    for name in ("cohort", "evaluations", "runtime", "candidates", "dispositions"):
        values[name] = list(reversed(values[name]))
    actual = build(values)
    assert actual.files == expected.files
    assert actual.bundle_digest == expected.bundle_digest


@pytest.mark.parametrize("missing", ["cohort.json", "runtime.json", "vocabulary.json", "hashes.json"])
def test_missing_replay_input_or_hash_manifest_fails_closed(missing):
    files = dict(build().files)
    files.pop(missing)
    with pytest.raises(OperationalFamilyRetentionBundleError, match="FILE_SET_MISMATCH"):
        replay_fixture_operational_family_retention_bundle(
            build_operational_family_retention_bundle_contract(), files,
        )


def test_noncanonical_bytes_fail_closed_before_semantic_replay():
    files = dict(build().files)
    files["cohort.json"] = b" " + files["cohort.json"]
    with pytest.raises(OperationalFamilyRetentionBundleError, match="CANONICAL_BYTES_MISMATCH"):
        replay_fixture_operational_family_retention_bundle(
            build_operational_family_retention_bundle_contract(), files,
        )


def test_altered_file_with_stale_hashes_fails_closed():
    files = dict(build().files)
    document = json.loads(files["evaluations.json"])
    document["items"][0]["snapshot_digest"] = "altered"
    files["evaluations.json"] = canonical(document)
    with pytest.raises(OperationalFamilyRetentionBundleError, match="HASH_REPLAY_MISMATCH"):
        replay_fixture_operational_family_retention_bundle(
            build_operational_family_retention_bundle_contract(), files,
        )


def test_rehashed_lineage_tamper_is_rejected_by_f5_replay():
    files = dict(build().files)
    document = json.loads(files["evaluations.json"])
    document["items"][0]["snapshot_digest"] = "altered"
    files["evaluations.json"] = canonical(document)
    rehash(files)
    with pytest.raises(OperationalFamilyRetentionBundleError, match="F5_REPLAY_REJECTED.*RUNTIME_EVALUATION_LINEAGE_DRIFT"):
        replay_fixture_operational_family_retention_bundle(
            build_operational_family_retention_bundle_contract(), files,
        )


def test_rehashed_accounting_tamper_fails_closed():
    files = dict(build().files)
    document = json.loads(files["accounting.json"])
    document["membership_count"] += 1
    files["accounting.json"] = canonical(document)
    rehash(files)
    with pytest.raises(OperationalFamilyRetentionBundleError, match="ACCOUNTING_REPLAY_MISMATCH"):
        replay_fixture_operational_family_retention_bundle(
            build_operational_family_retention_bundle_contract(), files,
        )


def test_invalid_fixture_semantics_are_rejected_during_construction():
    values = material()
    values["dispositions"][0]["nomination_state"] = "RECURRING_PATTERN"
    with pytest.raises(OperationalFamilyRetentionBundleError, match="F5_SOURCE_REJECTED.*INVALID_NOMINATION_STATE"):
        build(values)
