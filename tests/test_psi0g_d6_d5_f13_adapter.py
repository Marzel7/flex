import json
from pathlib import Path
import shutil
import sqlite3

import pytest

from src.evidence.contracts.operational_family_retained_input_store import export_operational_family_retained_inputs
from src.evidence.contracts.psi0g_d5_f13_adapter import (
    Psi0gD5F13AdapterError,
    adapt_d5_to_f13_compatible_store,
)


ROOT = Path(__file__).resolve().parents[1]
REAL_D5 = ROOT / "docs/audits/psi0g_runs/psi0g-d5-real-provenance-retention-20260817-02"


def test_real_d5_adapts_losslessly_and_exports_through_unchanged_f13(tmp_path):
    adapted = adapt_d5_to_f13_compatible_store(REAL_D5, tmp_path / "retained.db")
    exported = export_operational_family_retained_inputs(adapted.path, adapted.retention_id)
    assert exported.manifest_digest == adapted.manifest_digest
    assert exported.bundle.bundle_digest == adapted.f9_bundle_digest
    assert exported.bundle.source_digest == adapted.f5_source_digest
    candidates = json.loads(exported.bundle.files["candidates.json"])["items"]
    dispositions = json.loads(exported.bundle.files["dispositions.json"])["items"]
    assert candidates[0]["candidate_id"] == "1647cb5804e13ba1f34030eca7dba7abd8454f24f6186cf33d026e9d843672c0"
    assert len(candidates[0]["missing_evidence"]) == 14
    assert dispositions[0]["nomination_state"] == "PROPOSED"
    assert dispositions[0]["operation_ids"] == ["watchtower", "three_sw2"]
    assert not any(dispositions[0]["authority"].values())


def test_destination_must_be_new(tmp_path):
    destination = tmp_path / "retained.db"
    destination.write_text("owner")
    with pytest.raises(Psi0gD5F13AdapterError, match="DESTINATION_NOT_NEW"):
        adapt_d5_to_f13_compatible_store(REAL_D5, destination)
    assert destination.read_text() == "owner"


def test_d5_disposition_tamper_is_rejected_before_write(tmp_path):
    source = tmp_path / "d5"
    shutil.copytree(REAL_D5, source)
    path = source / "disposition.json"
    value = json.loads(path.read_text())
    value["nomination_state"] = "SUPPORTED"
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
    with pytest.raises(Exception, match="PSI0G_D5_PAYLOAD_IDENTITY_DRIFT"):
        adapt_d5_to_f13_compatible_store(source, tmp_path / "retained.db")


def test_export_tamper_remains_fail_closed(tmp_path):
    adapted = adapt_d5_to_f13_compatible_store(REAL_D5, tmp_path / "retained.db")
    connection = sqlite3.connect(adapted.path)
    connection.execute("DROP TRIGGER candidate_payloads_no_update")
    connection.execute("UPDATE candidate_payloads SET payload_digest=?", ("0" * 64,))
    connection.execute(
        "CREATE TRIGGER candidate_payloads_no_update BEFORE UPDATE ON candidate_payloads "
        "BEGIN SELECT RAISE(ABORT, 'immutable candidate_payloads'); END"
    )
    connection.commit()
    connection.close()
    with pytest.raises(Exception, match="PAYLOAD_REPLAY_MISMATCH"):
        export_operational_family_retained_inputs(adapted.path, adapted.retention_id)
