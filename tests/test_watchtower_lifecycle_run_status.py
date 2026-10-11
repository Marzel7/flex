import importlib.util
import json
from pathlib import Path

import pytest

from src.ops.dev014_batch4_runtime_gate import RuntimeGateDenied
from src.ops.watchtower_lifecycle_cohort import build_cohort_authorization
from src.ops.watchtower_lifecycle_probe import request_identity


SCRIPT = Path(__file__).parents[1] / "scripts" / "run_watchtower_lifecycle_cohort.py"
SPEC = importlib.util.spec_from_file_location("lifecycle_cohort_runner", SCRIPT)
runner = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(runner)


def _record(rank):
    value = {"rank":rank,"mint":f"mint-{rank}","creation_timestamp":rank,"time_from":rank,"time_to":rank+864000,"interval":"15m"}
    value["content_hash"] = request_identity(value)
    return value


def test_status_is_atomic_bounded_and_does_not_touch_journals(tmp_path):
    journal = tmp_path / "journals" / "identity" / "journal.json"
    journal.parent.mkdir(parents=True)
    journal.write_text('{"state":"COMPLETED"}')
    payload = {"state":"STARTED","run_id":"run","authorization_hash":"hash","engineering_source_sha":"a" * 40}
    runner._write_status(tmp_path, payload)
    assert json.loads((tmp_path / runner.STATUS_NAME).read_text()) == payload
    assert (tmp_path / runner.STATUS_NAME).stat().st_size < runner.STATUS_MAX_BYTES
    assert journal.read_text() == '{"state":"COMPLETED"}'
    (tmp_path / runner.STATUS_NAME).unlink()
    (tmp_path / runner.STATUS_NAME).symlink_to(journal)
    with pytest.raises(RuntimeError, match="SYMLINK"):
        runner._write_status(tmp_path, payload)
    assert journal.read_text() == '{"state":"COMPLETED"}'


def test_status_classifies_runtime_hold_and_restart_excludes_terminal(tmp_path):
    authorization = build_cohort_authorization([_record(1), _record(2)], chronology_hash="c", authority_id="a")
    first = authorization["records"][0]
    journal = tmp_path / "journals" / first["request_identity"] / "journal.json"
    journal.parent.mkdir(parents=True)
    journal.write_text(json.dumps({"state":"COMPLETED","request_identity":first["request_identity"],"mint":first["mint"]}))
    assert runner._durable_completion_count(authorization, tmp_path / "journals") == 1
    assert runner._is_controlled_hold(RuntimeGateDenied("CHECKPOINT_PROGRESS_UNPROVEN"))
    assert not runner._is_controlled_hold(RuntimeError("unexpected"))
    status = runner._status(authorization, "a" * 40, "run", 1, "HOLD", completed=1, completed_sessions=0, current_session=1, reason="CHECKPOINT_PROGRESS_UNPROVEN")
    runner._write_status(tmp_path, status)
    assert json.loads((tmp_path / runner.STATUS_NAME).read_text())["last_durable_completion_count"] == 1
    assert json.loads((tmp_path / runner.STATUS_NAME).read_text())["exit_code"] is None
