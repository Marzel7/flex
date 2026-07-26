import importlib.util
import sqlite3
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "x49_1_shadow_replay.py"
SPEC = importlib.util.spec_from_file_location("x49_1_shadow_replay", SCRIPT)
x49 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(x49)


def test_production_connection_is_read_only(tmp_path):
    path = tmp_path / "production.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE evidence(value TEXT)")
    conn.commit()
    conn.close()

    shadow = x49.ro(str(path))
    with pytest.raises(sqlite3.OperationalError):
        shadow.execute("INSERT INTO evidence VALUES ('mutated')")
    shadow.close()


def test_close_destination_must_match_creator(monkeypatch):
    monkeypatch.setattr(x49.worker, "_close_account_destination", lambda tx: "other")
    status, dimension, destination = x49.mechanism_evidence({}, "creator")
    assert status == "ACCOUNT_CLOSE_DESTINATION_MISMATCH"
    assert dimension == "ACCOUNT_CLOSE_PROVEN"
    assert destination == "other"


def test_legacy_close_label_is_not_structured_proof(monkeypatch):
    monkeypatch.setattr(x49.worker, "_close_account_destination", lambda tx: None)
    monkeypatch.setattr(x49.worker, "_detect_mechanism", lambda *args: "")
    status, dimension, destination = x49.mechanism_evidence(
        {}, "creator", "WSOL_WRAP_CLOSE")
    assert (status, dimension, destination) == (
        "ACCOUNT_CLOSE_LABEL_ONLY", "ACCOUNT_CLOSE_LABEL_ONLY", "")


def test_parsed_creator_destination_is_proven(monkeypatch):
    monkeypatch.setattr(x49.worker, "_close_account_destination", lambda tx: "creator")
    assert x49.mechanism_evidence({}, "creator") == (
        "ACCOUNT_CLOSE_PROVEN", "ACCOUNT_CLOSE_PROVEN", "creator")


class FakeRpc:
    def reset_thread_stats(self):
        pass

    def thread_stats(self):
        return {}


def test_confirmed_treasury_requires_reconstructed_path(monkeypatch):
    monkeypatch.setattr(x49.worker, "_find_funder_via_rpc",
                        lambda *args, **kwargs: (None, None, None, None, None, None))
    item = {
        "replay_state": "READY", "creator": "creator", "create_transaction": "create",
        "legacy_funding_mechanism": "", "token": "token", "ordinal": 1,
        "migration_delay": "",
    }
    result, hops, _ = x49.replay_one(item, FakeRpc(), {"registered-treasury"}, 4, 30)
    assert not hops
    assert result["final_classification"] != "CONFIRMED_WATCHTOWER"
    assert result["known_treasury"] == ""

