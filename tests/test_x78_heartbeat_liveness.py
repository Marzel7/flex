from __future__ import annotations

import os

from src.core import worker_liveness


def test_liveness_sidecar_is_bounded_atomic_and_tracks_progress(tmp_path):
    path = tmp_path / "resolution.json"
    assert worker_liveness.publish_liveness(
        str(path),
        worker_name="creator-resolution",
        pid=123,
        progress_at=90.0,
        progress_phase="cycle_processing",
        progress_cycle=7,
        observed_at=100.0,
    )

    record = worker_liveness.read_liveness(
        str(path), worker_name="creator-resolution", now=105.0
    )
    assert record == {
        "pid": 123,
        "observed_at": 100.0,
        "progress_at": 90.0,
        "progress_phase": "cycle_processing",
        "progress_cycle": 7,
        "liveness_age_s": 5,
        "progress_age_s": 15,
    }
    assert path.stat().st_size <= worker_liveness.MAX_LIVENESS_BYTES
    assert not list(tmp_path.glob("*.tmp.*"))


def test_missing_or_malformed_liveness_fails_closed(tmp_path):
    path = tmp_path / "resolution.json"
    path.write_text("not-json")
    assert worker_liveness.read_liveness(
        str(path), worker_name="creator-resolution", now=100.0
    ) is None
    path.write_bytes(b"x" * (worker_liveness.MAX_LIVENESS_BYTES + 1))
    assert worker_liveness.read_liveness(
        str(path), worker_name="creator-resolution", now=100.0
    ) is None


def test_liveness_never_weakens_stale_database_progress_health():
    row = {"last_seen": 1, "age_s": 500, "status": "ok", "stale": True}
    result = worker_liveness.annotate_progress_health(
        row,
        {
            "pid": os.getpid(),
            "liveness_age_s": 1,
            "progress_age_s": 500,
            "progress_phase": "cycle_processing",
            "progress_cycle": 3,
        },
    )
    assert result["stale"] is True
    assert result["progress_age_s"] == 500
    assert result["liveness"]["liveness_age_s"] == 1


def test_worker_liveness_publish_does_not_open_a_database_connection(monkeypatch):
    from src.core import creator_resolution_worker as worker

    calls = []
    monkeypatch.setattr(worker, "publish_liveness", lambda *a, **k: calls.append(k) or True)
    monkeypatch.setattr(worker, "_db_connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("DB open")))
    monkeypatch.setattr(worker, "_liveness_progress_at", 10.0)
    monkeypatch.setattr(worker, "_liveness_progress_phase", "cycle_processing")
    monkeypatch.setattr(worker, "_liveness_progress_cycle", 2)

    worker._publish_liveness()

    assert len(calls) == 1
    assert calls[0]["worker_name"] == "creator-resolution"
    assert calls[0]["progress_cycle"] == 2
