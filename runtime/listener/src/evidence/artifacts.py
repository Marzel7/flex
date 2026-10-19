from __future__ import annotations

import gzip
import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .errors import ArtifactConflict, ArtifactCorruption, ComponentDisabled
from .metrics import EvidenceMetrics


@dataclass(frozen=True)
class ArtifactReference:
    digest: str
    size_bytes: int
    compressed_bytes: int
    content_type: str
    compression: str = "gzip"


class ArtifactStore:
    def __init__(self, root: Path, *, enabled: bool = False,
                 metrics: EvidenceMetrics | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.root = Path(root)
        self.enabled = enabled
        self.metrics = metrics or EvidenceMetrics()
        self.clock = clock

    def _paths(self, digest: str) -> tuple[Path, Path]:
        directory = self.root / digest[:2] / digest[2:4]
        return directory / f"{digest}.json.gz", directory / f"{digest}.metadata.json"

    @staticmethod
    def _digest(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def _atomic_write(path: Path, writer: Callable[[Any], None]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.parent / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        try:
            with temporary.open("xb") as handle:
                writer(handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def put(self, data: bytes, *, content_type: str = "application/json",
            metadata: dict[str, Any] | None = None) -> ArtifactReference:
        if not self.enabled:
            raise ComponentDisabled("Evidence artifact store is disabled")
        digest = self._digest(data)
        artifact, meta = self._paths(digest)
        if artifact.exists():
            existing = self.get(digest)
            if existing != data:
                raise ArtifactConflict(f"Artifact digest collision: {digest}")
            if not meta.exists():
                self._write_metadata(meta, digest, len(data), artifact.stat().st_size,
                                     content_type, metadata or {})
            self.metrics.increment("artifact_reused")
            return ArtifactReference(digest, len(data), artifact.stat().st_size, content_type)

        def write_compressed(handle: Any) -> None:
            with gzip.GzipFile(filename="", mode="wb", fileobj=handle, mtime=0) as compressed:
                compressed.write(data)

        self._atomic_write(artifact, write_compressed)
        self._write_metadata(meta, digest, len(data), artifact.stat().st_size,
                             content_type, metadata or {})
        self.metrics.increment("artifact_written")
        self.metrics.observe("artifact_bytes", len(data))
        return ArtifactReference(digest, len(data), artifact.stat().st_size, content_type)

    def _write_metadata(self, path: Path, digest: str, size_bytes: int,
                        compressed_bytes: int, content_type: str,
                        metadata: dict[str, Any]) -> None:
        metadata_payload = {
            "digest": digest, "size_bytes": size_bytes, "compressed_bytes": compressed_bytes,
            "content_type": content_type, "compression": "gzip", "created_at": int(self.clock()),
            "metadata": metadata,
        }
        encoded = (json.dumps(metadata_payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
        self._atomic_write(path, lambda handle: handle.write(encoded))

    def get(self, digest: str) -> bytes:
        if not self.enabled:
            raise ComponentDisabled("Evidence artifact store is disabled")
        artifact, _ = self._paths(digest)
        try:
            with gzip.open(artifact, "rb") as handle:
                data = handle.read()
        except (OSError, EOFError) as exc:
            self.metrics.increment("artifact_corrupt")
            raise ArtifactCorruption(f"Cannot decode artifact {digest}") from exc
        if self._digest(data) != digest:
            self.metrics.increment("artifact_corrupt")
            raise ArtifactCorruption(f"Artifact digest mismatch: {digest}")
        return data

    def verify(self, digest: str) -> bool:
        self.get(digest)
        return True

    def retention_candidates(self, older_than: int) -> list[str]:
        """Return candidates only. EP1.0 never deletes immutable artifacts."""
        if not self.enabled or not self.root.exists():
            return []
        result = []
        for path in self.root.glob("*/*/*.metadata.json"):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                if int(value.get("created_at", 0)) < older_than:
                    result.append(str(value["digest"]))
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return sorted(result)

    def health(self) -> dict[str, Any]:
        if not self.enabled:
            return {"status": "DISABLED"}
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            artifacts = list(self.root.glob("*/*/*.json.gz"))
            metadata = list(self.root.glob("*/*/*.metadata.json"))
            return {"status": "HEALTHY", "path": str(self.root),
                    "artifact_count": len(artifacts), "metadata_count": len(metadata),
                    "metadata_missing": max(0, len(artifacts) - len(metadata))}
        except OSError as exc:
            return {"status": "DEGRADED", "error": str(exc)}
