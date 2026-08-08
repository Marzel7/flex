from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pytest

from src.acquisition.factory import (
    MirroringTransactionAcquisition,
    build_transaction_acquisition,
    reset_mirror_for_tests,
)
from src.acquisition.transaction import (
    AcquisitionMetadata,
    AcquisitionResponse,
    SharedTransactionAcquisition,
)
from src.evidence.artifacts import ArtifactStore
from src.evidence.config import EvidenceConfig
from src.evidence.errors import IsolationError
from src.evidence.mirror import EvidenceMirrorPublisher
from src.evidence.queue import EvidenceIntakeQueue
from src.evidence.service import EvidencePlatform


class _Response:
    def __init__(self, status, data, headers=None):
        self.status = status
        self._data = data
        self._raw = json.dumps(data).encode("utf-8")
        self.headers = headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
        return self._data

    async def read(self):
        return self._raw

    async def text(self):
        return json.dumps(self._data)


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self.responses.pop(0)

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.responses.pop(0)


def _config(tmp_path: Path, **changes) -> EvidenceConfig:
    values = dict(
        platform_enabled=True,
        writer_enabled=False,
        queue_enabled=True,
        artifact_store_enabled=True,
        health_enabled=True,
        mirror_enabled=True,
        database_path=tmp_path / "evidence" / "evidence.db",
        queue_path=tmp_path / "intake",
        artifact_path=tmp_path / "artifacts",
        mirror_spool_path=tmp_path / "mirror_spool",
        queue_max_messages=100,
        queue_max_bytes=1024 * 1024,
        writer_batch_size=10,
        writer_poll_seconds=0.01,
        max_attempts=3,
        mirror_buffer_size=4,
        mirror_retry_seconds=0.01,
    )
    values.update(changes)
    return EvidenceConfig(**values)


def _response(acquisition_id="acq-1", *, timestamp=100.0,
              raw_body: bytes | None = None) -> AcquisitionResponse:
    metadata = AcquisitionMetadata(
        acquisition_id=acquisition_id,
        correlation_id="corr-1",
        purpose="creator_funding",
        creator="creator-1",
        launch="mint-1",
        request_type="json_rpc",
        provider="helius_rpc",
        method="getTransaction",
        page_number=1,
        cursor=None,
        timestamp=timestamp,
        cache_state="miss",
        retry_count=0,
    )
    return AcquisitionResponse(
        status=200,
        data={"result": {"signature": "sig-response", "slot": 1}},
        text=None,
        headers={"Content-Type": "application/json"},
        metadata=metadata,
        latency_ms=2.5,
        raw_body=raw_body,
        artifact_representation=(
            "EXACT_PROVIDER_ARTIFACT" if raw_body is not None
            else "RAW_BYTES_UNAVAILABLE"
        ),
    )


def _pending_payload(config: EvidenceConfig) -> dict:
    path = next((config.queue_path / "pending").glob("*.json"))
    return json.loads(path.read_text(encoding="utf-8"))


def test_mirror_flag_defaults_off_and_factory_returns_ep1_1_transport(monkeypatch):
    monkeypatch.setattr("src.acquisition.factory._MIRROR", None)
    for key in (
        "EVIDENCE_PLATFORM_ENABLED", "EVIDENCE_MIRROR_ENABLED",
        "EVIDENCE_QUEUE_ENABLED", "EVIDENCE_ARTIFACT_STORE_ENABLED",
    ):
        monkeypatch.delenv(key, raising=False)
    client = build_transaction_acquisition(_Session([]))
    assert type(client) is SharedTransactionAcquisition
    assert not EvidenceConfig.from_env({}).mirror_enabled


def test_mirror_spool_cannot_alias_production_database(tmp_path, monkeypatch):
    production = tmp_path / "production.db"
    monkeypatch.setenv("DB_PATH", str(production))
    with pytest.raises(IsolationError, match="Mirror Spool"):
        _config(tmp_path, mirror_spool_path=production).validate_isolation()


def test_enabled_mirror_writes_replayable_artifact_and_intake_envelope(tmp_path):
    config = _config(tmp_path)
    publisher = EvidenceMirrorPublisher(config, clock=lambda: 101.0)
    try:
        assert publisher.publish_nowait(
            _response(),
            http_method="POST",
            url="https://mainnet.helius-rpc.com/?api-key=secret",
            request_payload={"method": "getTransaction", "params": ["sig-request"]},
        )
        assert publisher.drain()
        payload = _pending_payload(config)
        envelope = payload["envelope"]
        acquisition = envelope["acquisition"]
        assert acquisition["acquisition_id"] == "acq-1"
        assert acquisition["correlation_id"] == "corr-1"
        assert acquisition["purpose"] == "creator_funding"
        assert acquisition["creator"] == "creator-1"
        assert acquisition["launch"] == "mint-1"
        assert acquisition["transaction_signatures"] == ["sig-request", "sig-response"]
        assert acquisition["parser_version"] == "raw-acquisition-v1"
        assert len(acquisition["request_digest"]) == 64
        assert acquisition["response_digest"] == envelope["artifact"]["digest"]
        raw = publisher.artifacts.get(envelope["artifact"]["digest"])
        assert hashlib.sha256(raw).hexdigest() == acquisition["response_digest"]
        assert b"sig-response" in raw
        assert not config.database_path.exists()
        assert "secret" not in json.dumps(envelope)
    finally:
        publisher.stop()


def test_existing_single_writer_accepts_mirrored_envelope(tmp_path):
    config = _config(tmp_path, writer_enabled=True)
    platform = EvidencePlatform(config)
    try:
        platform.mirror.publish_nowait(
            _response(),
            http_method="POST",
            url="https://mainnet.helius-rpc.com/",
            request_payload={"method": "getTransaction", "params": ["sig-request"]},
        )
        assert platform.mirror.drain()
        platform.writer.start()
        result = platform.writer.run_once()
        assert result["inserted"] == 1
        assert result["failed"] == 0
    finally:
        platform.writer.stop()
        platform.mirror.stop()


def test_distinct_observations_can_share_one_immutable_artifact(tmp_path):
    config = _config(tmp_path, writer_enabled=True, writer_batch_size=10)
    platform = EvidencePlatform(config)
    exact = b'{"jsonrpc":"2.0","result":null}'
    try:
        for acquisition_id, signature, timestamp in (
            ("acq-empty-a", "sig-a", 100.0),
            ("acq-empty-b", "sig-b", 101.0),
        ):
            assert platform.mirror.publish_nowait(
                _response(acquisition_id, timestamp=timestamp, raw_body=exact),
                http_method="POST", url="https://mainnet.helius-rpc.com/",
                request_payload={"method": "getTransaction", "params": [signature]},
            )
        assert platform.mirror.drain()
        queued = [json.loads(path.read_text()) for path in
                  (config.queue_path / "pending").glob("*.json")]
        assert len({item["envelope"]["artifact"]["digest"] for item in queued}) == 1
        assert len({item["envelope"]["evidence_digest"] for item in queued}) == 2
        platform.writer.start()
        result = platform.writer.run_once()
        assert result["inserted"] == 2
        connection = platform.writer.database.connection
        assert connection.execute("SELECT COUNT(*) FROM immutable_artifacts").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM evidence_envelopes").fetchone()[0] == 2
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        platform.writer.stop()
        platform.mirror.stop()


def test_publish_handoff_does_not_wait_for_artifact_persistence(tmp_path, monkeypatch):
    publisher = EvidenceMirrorPublisher(_config(tmp_path))
    original = publisher.artifacts.put

    def slow_put(*args, **kwargs):
        time.sleep(0.2)
        return original(*args, **kwargs)

    monkeypatch.setattr(publisher.artifacts, "put", slow_put)
    try:
        started = time.perf_counter()
        assert publisher.publish_nowait(
            _response(), http_method="POST", url="https://rpc.invalid",
            request_payload={"method": "getTransaction", "params": ["sig"]},
        )
        elapsed = time.perf_counter() - started
        assert elapsed < 0.05
        assert publisher.drain(timeout=2.0)
    finally:
        publisher.stop()


def test_backpressure_spools_without_silent_loss_and_replays_without_rpc(tmp_path, monkeypatch):
    config = _config(tmp_path, mirror_buffer_size=1)
    publisher = EvidenceMirrorPublisher(config)
    monkeypatch.setattr(publisher, "_ensure_started", lambda: None)
    assert publisher.publish_nowait(
        _response("first"), http_method="POST", url="https://rpc.invalid",
        request_payload={"method": "getTransaction", "params": ["sig-1"]},
    )
    assert publisher.publish_nowait(
        _response("second"), http_method="POST", url="https://rpc.invalid",
        request_payload={"method": "getTransaction", "params": ["sig-2"]},
    )
    assert len(list(config.mirror_spool_path.glob("*.json"))) == 1
    assert publisher.metrics.snapshot()["counters"]["mirror_backpressure"] == 1
    assert publisher.replay_spool() == 1
    assert len(list((config.queue_path / "pending").glob("*.json"))) == 1
    assert not list(config.mirror_spool_path.glob("*.json"))


def test_intake_failure_degrades_health_then_recovers_from_spool(tmp_path, monkeypatch):
    config = _config(tmp_path)
    publisher = EvidenceMirrorPublisher(config)
    original_enqueue = publisher.intake.enqueue
    monkeypatch.setattr(publisher.intake, "enqueue", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    try:
        publisher.publish_nowait(
            _response(), http_method="POST", url="https://rpc.invalid",
            request_payload={"method": "getTransaction", "params": ["sig"]},
        )
        assert publisher.drain()
        assert publisher.health()["status"] == "DEGRADED"
        assert publisher.health()["spool_depth"] == 1
        publisher.stop()
        monkeypatch.setattr(publisher.intake, "enqueue", original_enqueue)
        assert publisher.replay_spool() == 1
        assert publisher.health()["spool_depth"] == 0
        counters = publisher.metrics.snapshot()["counters"]
        assert counters["mirror_failures"] == 1
        assert counters["mirror_retries"] == 1
        assert counters["mirror_recovered"] == 1
    finally:
        publisher.stop()


@pytest.mark.asyncio
async def test_enabled_factory_adds_zero_rpc_and_preserves_response(tmp_path, monkeypatch):
    reset_mirror_for_tests()
    config = _config(tmp_path)
    env = {
        "EVIDENCE_PLATFORM_ENABLED": "1",
        "EVIDENCE_MIRROR_ENABLED": "1",
        "EVIDENCE_QUEUE_ENABLED": "1",
        "EVIDENCE_ARTIFACT_STORE_ENABLED": "1",
        "EVIDENCE_DATABASE_PATH": str(config.database_path),
        "EVIDENCE_QUEUE_PATH": str(config.queue_path),
        "EVIDENCE_ARTIFACT_PATH": str(config.artifact_path),
        "EVIDENCE_MIRROR_SPOOL_PATH": str(config.mirror_spool_path),
        "EVIDENCE_MIRROR_RETRY_SECONDS": "0.01",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    session = _Session([_Response(200, {"result": {"signature": "sig"}})])
    client = build_transaction_acquisition(session)
    assert isinstance(client, MirroringTransactionAcquisition)
    try:
        result = await client.request_once(
            http_method="POST", url="https://mainnet.helius-rpc.com/",
            json_payload={"method": "getTransaction", "params": ["sig"]},
            timeout_seconds=30, request_type="json_rpc", method="getTransaction",
        )
        assert result.data == {"result": {"signature": "sig"}}
        assert len(session.calls) == 1
        assert client._mirror.drain()
        assert client._mirror.health()["metrics"]["counters"]["mirror_published"] == 1
    finally:
        reset_mirror_for_tests()


def test_mirror_module_has_no_rpc_client_or_request_execution():
    source = Path("src/evidence/mirror.py").read_text(encoding="utf-8")
    assert "aiohttp" not in source
    assert "requests." not in source
    assert ".session." not in source
