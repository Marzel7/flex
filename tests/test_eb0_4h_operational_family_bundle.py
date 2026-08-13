import json
import sqlite3

import pytest

from src.evidence.contracts.operational_family_bundle import OperationalFamilyBundleError, verify_operational_family_bundle, write_operational_family_bundle
from src.evidence.contracts.operational_family_extractor import extract_operational_families


REVISION = "57ead756160456ea083aed1bebba3aea53d6a8cb"


def _result(path):
    db = sqlite3.connect(path)
    db.executescript("CREATE TABLE operation_cohort(position INTEGER,operation_id TEXT); CREATE TABLE normalized_operation_runtime(schema_version TEXT,identity_basis TEXT,operation_id TEXT,primary_role TEXT,contract_id TEXT,contract_version TEXT,module_id TEXT,module_version TEXT,topology_revision_id TEXT,behaviour_observation_id TEXT,input_digest TEXT,edge_features_json TEXT,mechanism_features_json TEXT,temporal_features_json TEXT,quality_state TEXT,completeness_state TEXT,conflict_group_id TEXT); CREATE TABLE nomination_candidates(group_id TEXT,position INTEGER,operation_id TEXT,nomination_state TEXT);")
    for position, operation in enumerate(("operation-alpha", "operation-beta")):
        db.execute("INSERT INTO operation_cohort VALUES (?,?)", (position, operation))
        db.execute("INSERT INTO normalized_operation_runtime VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("eb0.4c.normalized-runtime.v1","PLATFORM_OPERATION_ID",operation,"ROLE","contract","1",f"module-{position}","1",f"t-{position}",f"b-{position}",f"i-{position}",json.dumps(["A->B"]),json.dumps(["MECHANISM"]),json.dumps(["TEMPORAL"]),"OBSERVED","COMPLETE",None))
        db.execute("INSERT INTO nomination_candidates VALUES (?,?,?,?)", ("g",position,operation,"SUPPORTED"))
    db.commit(); db.close()
    return extract_operational_families(path)


def test_bundle_is_canonical_write_once_and_exactly_replayable(tmp_path):
    result = _result(tmp_path / "source.db"); output = tmp_path / "bundle"
    bundle = write_operational_family_bundle(result, output, run_id="fixture-run", engineering_revision=REVISION)
    assert verify_operational_family_bundle(output) == bundle
    assert {item.name for item in output.iterdir()} == {"run.json","accounting.json","manifests.json","corpora.json","hashes.json"}
    with pytest.raises(OperationalFamilyBundleError, match="OUTPUT_NOT_EMPTY"):
        write_operational_family_bundle(result, output, run_id="fixture-run", engineering_revision=REVISION)


def test_same_inputs_produce_identical_bytes(tmp_path):
    result = _result(tmp_path / "source.db")
    first, second = tmp_path / "one", tmp_path / "two"
    write_operational_family_bundle(result, first, run_id="stable", engineering_revision=REVISION)
    write_operational_family_bundle(result, second, run_id="stable", engineering_revision=REVISION)
    assert {p.name:p.read_bytes() for p in first.iterdir()} == {p.name:p.read_bytes() for p in second.iterdir()}


def test_missing_extra_altered_and_invalid_metadata_fail_closed(tmp_path):
    result = _result(tmp_path / "source.db"); output = tmp_path / "bundle"
    write_operational_family_bundle(result, output, run_id="run", engineering_revision=REVISION)
    (output / "accounting.json").write_text("{}\n")
    with pytest.raises(OperationalFamilyBundleError, match="FILE_DIGEST_MISMATCH"):
        verify_operational_family_bundle(output)
    (output / "extra").write_text("x")
    with pytest.raises(OperationalFamilyBundleError, match="FILE_SET_MISMATCH"):
        verify_operational_family_bundle(output)
    with pytest.raises(OperationalFamilyBundleError, match="INVALID_RUN_ID"):
        write_operational_family_bundle(result, tmp_path / "bad", run_id="../bad", engineering_revision=REVISION)
