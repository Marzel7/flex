"""Focused offline tests for scripts/git_tmp_pack_guard.py.

All tests operate against a synthetic temporary .git/objects/pack directory —
never the real repository — so nothing here can touch actual Git state.
"""
import importlib.util
import os
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "git_tmp_pack_guard",
    Path(__file__).resolve().parents[1] / "scripts" / "git_tmp_pack_guard.py",
)
guard = importlib.util.module_from_spec(_SPEC)
sys.modules["git_tmp_pack_guard"] = guard
_SPEC.loader.exec_module(guard)


@pytest.fixture
def fake_git_dir(tmp_path):
    git_dir = tmp_path / ".git"
    (git_dir / "objects" / "pack").mkdir(parents=True)
    return git_dir


def _make_pack(git_dir, name, size=1024, mtime_offset_seconds=0):
    path = git_dir / "objects" / "pack" / name
    path.write_bytes(b"x" * size)
    if mtime_offset_seconds:
        t = time.time() + mtime_offset_seconds
        os.utime(path, (t, t))
    return path


def _no_lsof_open(*a, **kw):
    return False, False


def _no_relevant_process(*a, **kw):
    return False, False


def test_old_stable_unopened_pack_is_verified_abandoned(fake_git_dir, monkeypatch):
    path = _make_pack(fake_git_dir, "tmp_pack_abc123", mtime_offset_seconds=-1000)
    monkeypatch.setattr(guard, "lsof_open_handle", _no_lsof_open)
    monkeypatch.setattr(guard, "any_relevant_git_process", _no_relevant_process)
    c = guard.classify(path, min_age_seconds=300, stability_interval_seconds=0.01, stability_samples=2)
    assert c.classification == "VERIFIED_ABANDONED"


def test_recently_created_pack_is_retained(fake_git_dir, monkeypatch):
    path = _make_pack(fake_git_dir, "tmp_pack_new1", mtime_offset_seconds=0)
    monkeypatch.setattr(guard, "lsof_open_handle", _no_lsof_open)
    monkeypatch.setattr(guard, "any_relevant_git_process", _no_relevant_process)
    c = guard.classify(path, min_age_seconds=300, stability_interval_seconds=0.01, stability_samples=2)
    assert c.classification == "ACTIVE_OR_RECENT"


def test_growing_pack_is_retained(fake_git_dir, monkeypatch):
    path = _make_pack(fake_git_dir, "tmp_pack_grow1", size=100, mtime_offset_seconds=-1000)
    monkeypatch.setattr(guard, "lsof_open_handle", _no_lsof_open)
    monkeypatch.setattr(guard, "any_relevant_git_process", _no_relevant_process)

    real_sleep = time.sleep

    def grow_during_sleep(seconds):
        with open(path, "ab") as f:
            f.write(b"y" * 50)
        real_sleep(0.001)

    with mock.patch.object(guard.time, "sleep", side_effect=grow_during_sleep):
        c = guard.classify(path, min_age_seconds=300, stability_interval_seconds=0.01, stability_samples=2)
    assert c.classification == "STABLE_GIT_ACTIVITY_PRESENT"


def test_open_pack_is_retained(fake_git_dir, monkeypatch):
    path = _make_pack(fake_git_dir, "tmp_pack_open1", mtime_offset_seconds=-1000)
    monkeypatch.setattr(guard, "lsof_open_handle", lambda p: (True, False))
    monkeypatch.setattr(guard, "any_relevant_git_process", _no_relevant_process)
    c = guard.classify(path, min_age_seconds=300, stability_interval_seconds=0.01, stability_samples=2)
    assert c.classification == "STABLE_BUT_OPEN"


def test_relevant_active_git_process_retains_pack(fake_git_dir, monkeypatch):
    path = _make_pack(fake_git_dir, "tmp_pack_gitproc1", mtime_offset_seconds=-1000)
    monkeypatch.setattr(guard, "lsof_open_handle", _no_lsof_open)
    monkeypatch.setattr(guard, "any_relevant_git_process", lambda: (True, False))
    c = guard.classify(path, min_age_seconds=300, stability_interval_seconds=0.01, stability_samples=2)
    assert c.classification == "STABLE_GIT_ACTIVITY_PRESENT"


def test_ambiguous_missing_file_is_retained_as_unknown(fake_git_dir):
    path = fake_git_dir / "objects" / "pack" / "tmp_pack_ghost1"
    c = guard.classify(path, min_age_seconds=300, stability_interval_seconds=0.01, stability_samples=2)
    assert c.classification == "UNKNOWN_DO_NOT_TOUCH"


def test_only_verified_abandoned_is_deleted(fake_git_dir, monkeypatch):
    abandoned = _make_pack(fake_git_dir, "tmp_pack_dead1", mtime_offset_seconds=-1000)
    recent = _make_pack(fake_git_dir, "tmp_pack_fresh1", mtime_offset_seconds=0)
    monkeypatch.setattr(guard, "lsof_open_handle", _no_lsof_open)
    monkeypatch.setattr(guard, "any_relevant_git_process", _no_relevant_process)

    guard.run_clean(fake_git_dir, min_age_seconds=300, stability_interval_seconds=0.01, stability_samples=2)

    assert not abandoned.exists()
    assert recent.exists()


def test_dry_run_check_never_deletes(fake_git_dir, monkeypatch):
    abandoned = _make_pack(fake_git_dir, "tmp_pack_stay1", mtime_offset_seconds=-1000)
    monkeypatch.setattr(guard, "lsof_open_handle", _no_lsof_open)
    monkeypatch.setattr(guard, "any_relevant_git_process", _no_relevant_process)

    guard.run_check(fake_git_dir, min_age_seconds=300, stability_interval_seconds=0.01, stability_samples=2)

    assert abandoned.exists()


def test_multiple_candidates_handled_independently(fake_git_dir, monkeypatch):
    dead1 = _make_pack(fake_git_dir, "tmp_pack_d1", mtime_offset_seconds=-1000)
    dead2 = _make_pack(fake_git_dir, "tmp_pack_d2", mtime_offset_seconds=-2000)
    fresh = _make_pack(fake_git_dir, "tmp_pack_f1", mtime_offset_seconds=0)
    monkeypatch.setattr(guard, "lsof_open_handle", _no_lsof_open)
    monkeypatch.setattr(guard, "any_relevant_git_process", _no_relevant_process)

    _, reclaimed = guard.run_clean(fake_git_dir, min_age_seconds=300, stability_interval_seconds=0.01, stability_samples=2)

    assert not dead1.exists()
    assert not dead2.exists()
    assert fresh.exists()
    assert reclaimed == 2048


def test_lsof_failure_fails_safe_and_retains(fake_git_dir, monkeypatch):
    path = _make_pack(fake_git_dir, "tmp_pack_lsoffail1", mtime_offset_seconds=-1000)
    monkeypatch.setattr(guard, "lsof_open_handle", lambda p: (False, True))
    monkeypatch.setattr(guard, "any_relevant_git_process", _no_relevant_process)
    c = guard.classify(path, min_age_seconds=300, stability_interval_seconds=0.01, stability_samples=2)
    assert c.classification == "UNKNOWN_DO_NOT_TOUCH"


def test_process_inspection_failure_fails_safe_and_retains(fake_git_dir, monkeypatch):
    path = _make_pack(fake_git_dir, "tmp_pack_procfail1", mtime_offset_seconds=-1000)
    monkeypatch.setattr(guard, "lsof_open_handle", _no_lsof_open)
    monkeypatch.setattr(guard, "any_relevant_git_process", lambda: (False, True))
    c = guard.classify(path, min_age_seconds=300, stability_interval_seconds=0.01, stability_samples=2)
    assert c.classification == "UNKNOWN_DO_NOT_TOUCH"


def test_filenames_outside_exact_pattern_never_matched(fake_git_dir):
    (fake_git_dir / "objects" / "pack" / "pack-abc123.pack").write_bytes(b"real pack")
    (fake_git_dir / "objects" / "pack" / "tmp_pack_ok.idx").write_bytes(b"not a candidate itself")
    (fake_git_dir / "objects" / "pack" / "sometmp_pack_x").write_bytes(b"wrong prefix")
    found = guard.find_tmp_packs(fake_git_dir)
    names = {p.name for p in found}
    assert "pack-abc123.pack" not in names
    assert "sometmp_pack_x" not in names


def test_finalized_idx_sibling_prevents_deletion(fake_git_dir, monkeypatch):
    path = _make_pack(fake_git_dir, "tmp_pack_hasidx", mtime_offset_seconds=-1000)
    (fake_git_dir / "objects" / "pack" / "tmp_pack_hasidx.idx").write_bytes(b"idx")
    monkeypatch.setattr(guard, "lsof_open_handle", _no_lsof_open)
    monkeypatch.setattr(guard, "any_relevant_git_process", _no_relevant_process)
    c = guard.classify(path, min_age_seconds=300, stability_interval_seconds=0.01, stability_samples=2)
    assert c.classification == "UNKNOWN_DO_NOT_TOUCH"


def test_disk_low_condition_does_not_bypass_proof_requirements(fake_git_dir, monkeypatch):
    # A freshly created (not-yet-aged) pack must stay retained even when
    # disk_state() reports CRITICAL — low disk must never weaken the proof.
    path = _make_pack(fake_git_dir, "tmp_pack_freshcritical", mtime_offset_seconds=0)
    monkeypatch.setattr(guard, "lsof_open_handle", _no_lsof_open)
    monkeypatch.setattr(guard, "any_relevant_git_process", _no_relevant_process)
    monkeypatch.setattr(guard, "disk_state", lambda git_dir: ("CRITICAL", 50))

    candidates = guard.run_check(fake_git_dir, min_age_seconds=300, stability_interval_seconds=0.01, stability_samples=2)

    assert candidates[0].classification == "ACTIVE_OR_RECENT"
    assert path.exists()
