import importlib.util
import json
from pathlib import Path

import pytest

from src.ops.watchtower_lifecycle_probe import request_identity


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("lifecycle_wrapper", ROOT / "scripts" / "run_dev014_birdeye_preflight.py")
wrapper = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(wrapper)


def _authorization():
    value = {"rank": 593, "mint": "new-mint", "creation_timestamp": 100, "time_from": 100, "time_to": 1000, "interval": "15m"}
    value["content_hash"] = request_identity(value)
    return value


def test_reusable_binding_accepts_only_hash_named_frozen_authorization(tmp_path, monkeypatch):
    auth_root, journal_root = tmp_path / "authorizations", tmp_path / "journals"
    auth_root.mkdir(); value = _authorization(); identity = value["content_hash"]
    path = auth_root / f"{identity}.json"; path.write_text(json.dumps(value))
    monkeypatch.setattr(wrapper.Path, "glob", lambda _self, _pattern: [])
    authorization, journal = wrapper._lifecycle_probe_paths(path, authorization_root=auth_root, journal_root=journal_root)
    assert authorization == path.resolve() and journal == journal_root / identity / "journal.json"


def test_reusable_binding_rejects_path_hash_and_prior_attempt_conflicts(tmp_path, monkeypatch):
    auth_root, journal_root = tmp_path / "authorizations", tmp_path / "journals"
    auth_root.mkdir(); value = _authorization(); identity = value["content_hash"]
    wrong = auth_root / "wrong.json"; wrong.write_text(json.dumps(value))
    with pytest.raises(wrapper.CredentialBindingDenied, match="FILENAME"):
        wrapper._lifecycle_probe_paths(wrong, authorization_root=auth_root, journal_root=journal_root)
    path = auth_root / f"{identity}.json"; path.write_text(json.dumps(value))
    prior = tmp_path / "dev014-lifecycle-probe-old" / "journal.json"; prior.parent.mkdir()
    prior.write_text(json.dumps({"state": "ATTEMPTED", "request_identity": "other", "mint": "new-mint"}))
    monkeypatch.setattr(wrapper.Path, "glob", lambda _self, _pattern: [prior])
    with pytest.raises(wrapper.CredentialBindingDenied, match="PRIOR_ATTEMPT"):
        wrapper._lifecycle_probe_paths(path, authorization_root=auth_root, journal_root=journal_root)
