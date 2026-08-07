from __future__ import annotations

import errno
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from flask import Flask

from src.evidence.artifacts import ArtifactStore
from src.evidence.config import EvidenceConfig
from src.evidence.database import EvidenceDatabase
from src.evidence.errors import (
    ArtifactCorruption, ComponentDisabled, IsolationError, QueueCorruption,
    QueueFull, WriterOwnershipError,
)
from src.evidence.health import create_evidence_health_blueprint
from src.evidence.queue import EvidenceIntakeQueue
from src.evidence.service import EvidencePlatform


def config(tmp_path: Path, **changes) -> EvidenceConfig:
    values = dict(
        platform_enabled=True, writer_enabled=True, queue_enabled=True,
        artifact_store_enabled=True, health_enabled=True,
        database_path=tmp_path / "evidence" / "evidence.db",
        queue_path=tmp_path / "queue", artifact_path=tmp_path / "artifacts",
        queue_max_messages=10, queue_max_bytes=1024 * 1024,
        writer_batch_size=10, writer_poll_seconds=0.01, max_attempts=2,
    )
    values.update(changes)
    return EvidenceConfig(**values)


def counts(path: Path) -> dict[str, int]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    result = {name: conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] for name in (
        "evidence_envelopes", "evidence_provenance", "artifact_references", "writer_receipts"
    )}
    conn.close()
    return result


def test_every_feature_flag_defaults_off():
    value = EvidenceConfig.from_env({})
    assert value.completely_off
    assert not value.platform_enabled
    assert not value.writer_enabled
    assert not value.queue_enabled
    assert not value.artifact_store_enabled
    assert not value.health_enabled


def test_disabled_writer_entrypoint_is_clean_noop(monkeypatch):
    from src.evidence import writer
    for key in ("EVIDENCE_PLATFORM_ENABLED", "EVIDENCE_WRITER_ENABLED"):
        monkeypatch.delenv(key, raising=False)
    assert writer.main() == 0


def test_evidence_off_creates_no_files(tmp_path):
    value = config(tmp_path, platform_enabled=False, writer_enabled=False,
                   queue_enabled=False, artifact_store_enabled=False, health_enabled=False)
    platform = EvidencePlatform(value)
    assert platform.health()["status"] == "DISABLED"
    with pytest.raises(ComponentDisabled):
        platform.synthetic_message(b"{}", observed_at=1)
    assert list(tmp_path.iterdir()) == []


def test_paths_cannot_alias_production_database(tmp_path, monkeypatch):
    production = tmp_path / "production.db"
    monkeypatch.setenv("DB_PATH", str(production))
    with pytest.raises(IsolationError):
        config(tmp_path, database_path=production).validate_isolation()


def test_artifact_store_is_content_addressed_compressed_and_idempotent(tmp_path):
    store = ArtifactStore(tmp_path, enabled=True, clock=lambda: 10)
    first = store.put(b'{"fact":1}', metadata={"source": "test"})
    second = store.put(b'{"fact":1}', metadata={"source": "ignored-on-reuse"})
    assert first.digest == hashlib.sha256(b'{"fact":1}').hexdigest()
    assert first.digest == second.digest
    assert store.get(first.digest) == b'{"fact":1}'
    assert len(list(tmp_path.glob("*/*/*.json.gz"))) == 1


def test_corrupt_artifact_is_detected(tmp_path):
    store = ArtifactStore(tmp_path, enabled=True)
    reference = store.put(b"immutable")
    artifact, _ = store._paths(reference.digest)
    artifact.write_bytes(b"not gzip")
    with pytest.raises(ArtifactCorruption):
        store.verify(reference.digest)


def test_disk_full_propagates_without_partial_artifact(tmp_path, monkeypatch):
    store = ArtifactStore(tmp_path, enabled=True)
    def full(*_args, **_kwargs):
        raise OSError(errno.ENOSPC, "disk full")
    monkeypatch.setattr(store, "_atomic_write", full)
    with pytest.raises(OSError, match="disk full"):
        store.put(b"fact")
    assert not list(tmp_path.glob("**/*.json.gz"))


def test_retry_repairs_artifact_metadata_after_interrupted_put(tmp_path):
    store = ArtifactStore(tmp_path, enabled=True, clock=lambda: 10)
    data = b"recover metadata"
    digest = hashlib.sha256(data).hexdigest()
    artifact, metadata = store._paths(digest)
    original = store._atomic_write
    calls = 0
    def interrupt(path, writer):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("metadata write interrupted")
        return original(path, writer)
    store._atomic_write = interrupt
    with pytest.raises(OSError, match="interrupted"):
        store.put(data)
    assert artifact.exists() and not metadata.exists()
    store._atomic_write = original
    reference = store.put(data)
    assert reference.digest == digest
    assert metadata.exists()


def test_retention_hook_never_deletes(tmp_path):
    store = ArtifactStore(tmp_path, enabled=True, clock=lambda: 10)
    reference = store.put(b"retained")
    assert store.retention_candidates(11) == [reference.digest]
    assert store.get(reference.digest) == b"retained"


def test_queue_is_durable_idempotent_and_bounded(tmp_path):
    queue = EvidenceIntakeQueue(tmp_path, enabled=True, max_messages=1)
    assert queue.enqueue({"value": 1}, message_id="same") == "same"
    assert queue.enqueue({"value": 2}, message_id="same") == "same"
    with pytest.raises(QueueFull):
        queue.enqueue({"value": 3}, message_id="other")
    replacement = EvidenceIntakeQueue(tmp_path, enabled=True, max_messages=1)
    assert replacement.depth()["pending"] == 1


def test_queue_recovers_processing_and_retry_messages(tmp_path):
    queue = EvidenceIntakeQueue(tmp_path, enabled=True, max_attempts=3)
    queue.enqueue({"value": 1}, message_id="a")
    claimed = queue.claim(1)[0]
    assert queue.depth()["processing"] == 1
    assert EvidenceIntakeQueue(tmp_path, enabled=True).recover() == 1
    claimed = queue.claim(1)[0]
    queue.nack(claimed, "temporary")
    assert queue.depth()["retry"] == 1
    assert queue.recover() == 1
    assert queue.depth()["pending"] == 1


def test_duplicate_and_corrupt_queue_messages(tmp_path):
    queue = EvidenceIntakeQueue(tmp_path, enabled=True)
    queue.initialize()
    (tmp_path / "pending" / "bad.json").write_text("not-json")
    with pytest.raises(QueueCorruption):
        queue.claim(1)
    assert queue.depth()["dead_letter"] == 1


def test_writer_cold_start_batch_append_and_shutdown(tmp_path):
    platform = EvidencePlatform(config(tmp_path))
    platform.synthetic_message(b'{"one":1}', observed_at=1, acquired_at=2, message_id="m1")
    platform.synthetic_message(b'{"two":2}', observed_at=3, acquired_at=4, message_id="m2")
    platform.writer.start()
    result = platform.writer.run_once()
    assert result == {"claimed": 2, "inserted": 2, "duplicates": 0, "failed": 0}
    assert counts(platform.config.database_path) == {
        "evidence_envelopes": 2, "evidence_provenance": 2,
        "artifact_references": 2, "writer_receipts": 2,
    }
    platform.writer.stop()
    assert platform.writer.health()["status"] == "STOPPED"


def test_single_writer_ownership_is_enforced(tmp_path):
    first = EvidencePlatform(config(tmp_path))
    second = EvidencePlatform(config(tmp_path))
    first.writer.start()
    try:
        with pytest.raises(WriterOwnershipError):
            second.writer.start()
    finally:
        first.writer.stop()


def test_idempotent_append_for_duplicate_evidence_and_message(tmp_path):
    platform = EvidencePlatform(config(tmp_path))
    platform.synthetic_message(b"same evidence", observed_at=1, acquired_at=2, message_id="first")
    platform.synthetic_message(b"same evidence", observed_at=1, acquired_at=2, message_id="second")
    platform.writer.start()
    result = platform.writer.run_once()
    platform.writer.stop()
    assert result["inserted"] == 1
    assert result["duplicates"] == 1
    assert counts(platform.config.database_path)["evidence_envelopes"] == 1
    assert counts(platform.config.database_path)["writer_receipts"] == 2


def test_crash_after_commit_is_recovered_without_duplicate(tmp_path):
    crashed = False
    def crash_once():
        nonlocal crashed
        if not crashed:
            crashed = True
            raise RuntimeError("simulated crash after commit")
    first = EvidencePlatform(config(tmp_path))
    first.writer.after_commit = crash_once
    first.synthetic_message(b"crash-safe", observed_at=1, acquired_at=2, message_id="crash")
    first.writer.start()
    with pytest.raises(RuntimeError, match="simulated crash"):
        first.writer.run_once()
    first.writer.stop()

    restarted = EvidencePlatform(config(tmp_path))
    restarted.writer.start()
    result = restarted.writer.run_once()
    restarted.writer.stop()
    assert result["inserted"] == 0
    assert result["duplicates"] == 1
    assert counts(restarted.config.database_path)["evidence_envelopes"] == 1


def test_database_unavailable_releases_writer_ownership(tmp_path):
    blocker = tmp_path / "blocked"
    blocker.write_text("file where directory is required")
    platform = EvidencePlatform(config(tmp_path, database_path=blocker / "evidence.db"))
    with pytest.raises(OSError):
        platform.writer.start()
    assert platform.writer._lease is None


def test_database_failure_retries_claimed_batch(tmp_path, monkeypatch):
    platform = EvidencePlatform(config(tmp_path))
    platform.synthetic_message(b"retry-db", observed_at=1, acquired_at=2, message_id="retrydb")
    platform.writer.start()
    def unavailable(_messages):
        raise sqlite3.OperationalError("database unavailable")
    monkeypatch.setattr(platform.writer.database, "append_batch", unavailable)
    with pytest.raises(sqlite3.OperationalError, match="unavailable"):
        platform.writer.run_once()
    platform.writer.stop()
    assert platform.queue.depth()["retry"] == 1
    assert counts(platform.config.database_path)["evidence_envelopes"] == 0


def test_immutable_tables_reject_update_and_delete(tmp_path):
    platform = EvidencePlatform(config(tmp_path))
    platform.synthetic_message(b"immutable", observed_at=1, acquired_at=2, message_id="immutable")
    platform.writer.start(); platform.writer.run_once(); platform.writer.stop()
    conn = sqlite3.connect(platform.config.database_path)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("UPDATE evidence_envelopes SET source='changed'")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("DELETE FROM evidence_envelopes")
    conn.close()


def test_corrupt_artifact_is_retried_then_dead_lettered(tmp_path):
    platform = EvidencePlatform(config(tmp_path, max_attempts=1))
    platform.synthetic_message(b"corrupt-me", observed_at=1, acquired_at=2, message_id="corrupt")
    digest = hashlib.sha256(b"corrupt-me").hexdigest()
    platform.artifacts._paths(digest)[0].write_bytes(b"corrupt")
    platform.writer.start()
    result = platform.writer.run_once()
    platform.writer.stop()
    assert result["failed"] == 1
    assert platform.queue.depth()["dead_letter"] == 1
    assert counts(platform.config.database_path)["evidence_envelopes"] == 0


def test_health_and_metrics_are_independent_and_opt_in(tmp_path):
    enabled = EvidencePlatform(config(tmp_path))
    app = Flask(__name__)
    app.register_blueprint(create_evidence_health_blueprint(enabled))
    client = app.test_client()
    assert client.get("/api/evidence/health").status_code == 503  # database not initialized
    assert client.get("/api/evidence/metrics").status_code == 200

    disabled = EvidencePlatform(config(tmp_path / "off", health_enabled=False))
    other = Flask("disabled")
    other.register_blueprint(create_evidence_health_blueprint(disabled))
    assert other.test_client().get("/api/evidence/health").status_code == 404


def test_package_has_no_production_database_or_worker_imports():
    root = Path(__file__).resolve().parents[1] / "src" / "evidence"
    text = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.py"))
    forbidden = (
        "src.core.db", "src.utils.db_locking", "creator_funding_worker",
        "walkback_worker", "operator_identity_governance", "WATCHTOWER",
    )
    assert all(value not in text for value in forbidden)


def test_database_health_uses_read_only_connection(tmp_path):
    platform = EvidencePlatform(config(tmp_path))
    platform.writer.start(); platform.writer.stop()
    health = EvidenceDatabase.read_health(platform.config.database_path)
    assert health["status"] == "HEALTHY"
    assert health["counts"]["evidence_envelopes"] == 0
