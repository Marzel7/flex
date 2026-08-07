from __future__ import annotations

import hashlib
import time
import uuid
from typing import Any

from .artifacts import ArtifactStore
from .config import EvidenceConfig
from .database import EvidenceDatabase
from .metrics import EvidenceMetrics
from .mirror import EvidenceMirrorPublisher
from .queue import EvidenceIntakeQueue
from .writer import EvidenceWriter


class EvidencePlatform:
    """Explicit composition root. Production does not construct this in EP1.0."""

    def __init__(self, config: EvidenceConfig) -> None:
        config.validate_isolation()
        self.config = config
        self.metrics = EvidenceMetrics()
        self.artifacts = ArtifactStore(config.artifact_path,
                                       enabled=config.platform_enabled and config.artifact_store_enabled,
                                       metrics=self.metrics)
        self.queue = EvidenceIntakeQueue(
            config.queue_path, enabled=config.platform_enabled and config.queue_enabled,
            max_messages=config.queue_max_messages, max_bytes=config.queue_max_bytes,
            max_attempts=config.max_attempts, metrics=self.metrics,
        )
        self.mirror = EvidenceMirrorPublisher(
            config,
            artifacts=self.artifacts,
            intake=self.queue,
            metrics=self.metrics,
        )
        self.writer = EvidenceWriter(config, self.queue, self.artifacts, metrics=self.metrics)

    def synthetic_message(self, data: bytes, *, observed_at: int, acquired_at: int | None = None,
                          source: str = "synthetic", provider: str = "synthetic",
                          message_id: str | None = None) -> str:
        """Test-only/manual intake. No production caller is connected in EP1.0."""
        artifact = self.artifacts.put(data, metadata={"synthetic": True})
        digest = hashlib.sha256(data).hexdigest()
        envelope_id = f"env-{digest}"
        envelope = {
            "envelope_id": envelope_id, "observed_at": int(observed_at),
            "acquired_at": int(acquired_at if acquired_at is not None else time.time()),
            "source": source, "source_version": "ep1.0-synthetic", "provider": provider,
            "evidence_digest": digest, "replay_version": "1", "parser_version": "raw-v1",
            "payload_type": "synthetic/raw",
            "artifact": {
                "digest": artifact.digest, "size_bytes": artifact.size_bytes,
                "compressed_bytes": artifact.compressed_bytes,
                "content_type": artifact.content_type, "compression": artifact.compression,
            },
            "provenance": {
                "provider_request_id": None, "rpc_verification_state": "NOT_APPLICABLE",
                "acquisition_method": "SYNTHETIC_TEST", "source_metadata": {"synthetic": True},
            },
        }
        return self.queue.enqueue(envelope, message_id=message_id or uuid.uuid4().hex)

    def health(self) -> dict[str, Any]:
        if not self.config.platform_enabled:
            return {"status": "DISABLED", "components": {
                "writer": {"status": "DISABLED"}, "queue": {"status": "DISABLED"},
                "database": {"status": "DISABLED"}, "artifact_store": {"status": "DISABLED"},
                "mirror": self.mirror.health(),
            }}
        components = {
            "writer": self.writer.health(), "queue": self.queue.health(),
            "database": EvidenceDatabase.read_health(self.config.database_path),
            "artifact_store": self.artifacts.health(),
            "mirror": self.mirror.health(),
        }
        states = {item["status"] for item in components.values()}
        status = "HEALTHY" if states <= {"HEALTHY", "DISABLED"} else (
            "BACKLOG" if "BACKLOG" in states else "DEGRADED"
        )
        return {"status": status, "components": components}
