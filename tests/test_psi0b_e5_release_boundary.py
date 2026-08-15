"""PSI0B-E5: diagnostic failures must never retain the physical write lease."""

import os

import pytest

from src.core import database_write_service as dws


def _acquire(db_path, tx="tx-1"):
    return dws.acquire_write_lease("tracked:test", str(db_path), tx, "psi0b-e5")


def _assert_free_and_reacquirable(db_path):
    lock_path = f"{os.path.realpath(db_path)}.write.lock"
    assert dws.probe_kernel_flock(lock_path)["state"] == "FREE"
    lease = _acquire(db_path, "tx-next")
    dws.release_write_lease(lease)


def test_success_removes_owner_and_allows_reacquisition(tmp_path):
    db_path = tmp_path / "ops.db"
    lease = _acquire(db_path)
    owner_path = lease.owner_path

    dws.release_write_lease(lease)

    assert lease.file.closed
    assert not os.path.exists(owner_path)
    _assert_free_and_reacquirable(db_path)


def test_pending_sidecar_failure_still_unlocks_closes_and_cleans_tmp(tmp_path, monkeypatch):
    db_path = tmp_path / "ops.db"
    lease = _acquire(db_path)
    original = dws._write_owner_metadata

    def fail_pending(path, owner):
        if owner.get("state") == "RELEASE_PENDING":
            temporary = f"{path}.{os.getpid()}.{lease.owner_thread_ident}.tmp"
            with open(temporary, "w", encoding="utf-8") as handle:
                handle.write("partial")
            raise OSError("pending sidecar failed")
        return original(path, owner)

    monkeypatch.setattr(dws, "_write_owner_metadata", fail_pending)
    with pytest.raises(OSError, match="pending sidecar failed"):
        dws.release_write_lease(lease)

    assert lease.file.closed
    assert not os.path.exists(f"{lease.owner_path}.{os.getpid()}.{lease.owner_thread_ident}.tmp")
    monkeypatch.setattr(dws, "_write_owner_metadata", original)
    _assert_free_and_reacquirable(db_path)


def test_lock_bound_diagnostic_failure_still_unlocks_and_closes(tmp_path, monkeypatch):
    db_path = tmp_path / "ops.db"
    lease = _acquire(db_path)
    original = dws._write_lock_bound_owner

    def fail_pending(_file, owner):
        if owner.get("state") == "RELEASE_PENDING":
            raise OSError("lock-bound publication failed")
        return original(_file, owner)

    monkeypatch.setattr(dws, "_write_lock_bound_owner", fail_pending)
    with pytest.raises(OSError, match="lock-bound publication failed"):
        dws.release_write_lease(lease)

    assert lease.file.closed
    monkeypatch.setattr(dws, "_write_lock_bound_owner", original)
    _assert_free_and_reacquirable(db_path)


def test_owner_unlink_failure_is_reported_after_physical_release(tmp_path, monkeypatch):
    db_path = tmp_path / "ops.db"
    lease = _acquire(db_path)
    original = os.unlink

    def fail_owner(path):
        if path == lease.owner_path:
            raise OSError("owner unlink failed")
        return original(path)

    monkeypatch.setattr(os, "unlink", fail_owner)
    with pytest.raises(OSError, match="owner unlink failed"):
        dws.release_write_lease(lease)

    assert lease.file.closed
    monkeypatch.setattr(os, "unlink", original)
    _assert_free_and_reacquirable(db_path)


def test_close_failure_uses_raw_fd_fallback_and_reports_error(tmp_path):
    db_path = tmp_path / "ops.db"
    lease = _acquire(db_path)
    real_file = lease.file

    class CloseFailure:
        @property
        def closed(self):
            return real_file.closed

        def fileno(self):
            return real_file.fileno()

        def __getattr__(self, name):
            return getattr(real_file, name)

        def close(self):
            raise OSError("wrapper close failed")

    lease.file = CloseFailure()
    with pytest.raises(OSError, match="wrapper close failed"):
        dws.release_write_lease(lease)

    _assert_free_and_reacquirable(db_path)


def test_cross_thread_release_clears_registry_and_physical_lease(tmp_path):
    db_path = tmp_path / "ops.db"
    lease = _acquire(db_path)
    errors = []

    import threading
    thread = threading.Thread(target=lambda: _release_into(lease, errors), name="e5-releaser")
    thread.start()
    thread.join(5)

    assert not thread.is_alive()
    assert errors == []
    _assert_free_and_reacquirable(db_path)


def _release_into(lease, errors):
    try:
        dws.release_write_lease(lease)
    except Exception as exc:  # pragma: no cover - assertion reports the value
        errors.append(exc)
