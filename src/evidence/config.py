from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .errors import IsolationError


def _flag(env: dict[str, str], name: str) -> bool:
    return env.get(name, "0").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class EvidenceConfig:
    platform_enabled: bool = False
    writer_enabled: bool = False
    queue_enabled: bool = False
    artifact_store_enabled: bool = False
    health_enabled: bool = False
    mirror_enabled: bool = False
    normalization_enabled: bool = False
    primitive_engine_enabled: bool = False
    database_path: Path = Path("database/evidence_platform/evidence.db")
    queue_path: Path = Path("database/evidence_platform/intake")
    artifact_path: Path = Path("database/evidence_platform/artifacts")
    mirror_spool_path: Path = Path("database/evidence_platform/mirror_spool")
    queue_max_messages: int = 10_000
    queue_max_bytes: int = 256 * 1024 * 1024
    writer_batch_size: int = 100
    writer_poll_seconds: float = 1.0
    max_attempts: int = 5
    mirror_buffer_size: int = 1_000
    mirror_retry_seconds: float = 1.0
    mirror_cohort_enabled: bool = False
    mirror_cohort_manifest_path: Path | None = None
    retained_acquisition_observations_enabled: bool = False
    retained_acquisition_database_path: Path = Path("database/evidence_platform/production/retained_acquisition.db")

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "EvidenceConfig":
        values = dict(os.environ if env is None else env)
        return cls(
            platform_enabled=_flag(values, "EVIDENCE_PLATFORM_ENABLED"),
            writer_enabled=_flag(values, "EVIDENCE_WRITER_ENABLED"),
            queue_enabled=_flag(values, "EVIDENCE_QUEUE_ENABLED"),
            artifact_store_enabled=_flag(values, "EVIDENCE_ARTIFACT_STORE_ENABLED"),
            health_enabled=_flag(values, "EVIDENCE_HEALTH_ENABLED"),
            mirror_enabled=_flag(values, "EVIDENCE_MIRROR_ENABLED"),
            normalization_enabled=_flag(values, "EVIDENCE_NORMALIZATION_ENABLED"),
            primitive_engine_enabled=_flag(values, "EVIDENCE_PRIMITIVE_ENGINE_ENABLED"),
            database_path=Path(values.get("EVIDENCE_DATABASE_PATH", "database/evidence_platform/evidence.db")),
            queue_path=Path(values.get("EVIDENCE_QUEUE_PATH", "database/evidence_platform/intake")),
            artifact_path=Path(values.get("EVIDENCE_ARTIFACT_PATH", "database/evidence_platform/artifacts")),
            mirror_spool_path=Path(values.get(
                "EVIDENCE_MIRROR_SPOOL_PATH", "database/evidence_platform/mirror_spool"
            )),
            queue_max_messages=max(1, int(values.get("EVIDENCE_QUEUE_MAX_MESSAGES", "10000"))),
            queue_max_bytes=max(1024, int(values.get("EVIDENCE_QUEUE_MAX_BYTES", str(256 * 1024 * 1024)))),
            writer_batch_size=max(1, min(1000, int(values.get("EVIDENCE_WRITER_BATCH_SIZE", "100")))),
            writer_poll_seconds=max(0.01, float(values.get("EVIDENCE_WRITER_POLL_SECONDS", "1.0"))),
            max_attempts=max(1, int(values.get("EVIDENCE_MAX_ATTEMPTS", "5"))),
            mirror_buffer_size=max(1, int(values.get("EVIDENCE_MIRROR_BUFFER_SIZE", "1000"))),
            mirror_retry_seconds=max(
                0.01, float(values.get("EVIDENCE_MIRROR_RETRY_SECONDS", "1.0"))
            ),
            mirror_cohort_enabled=_flag(values, "EVIDENCE_MIRROR_COHORT_ENABLED"),
            mirror_cohort_manifest_path=(
                Path(values["EVIDENCE_MIRROR_COHORT_MANIFEST"])
                if values.get("EVIDENCE_MIRROR_COHORT_MANIFEST") else None
            ),
            retained_acquisition_observations_enabled=_flag(values, "RETAINED_ACQUISITION_OBSERVATIONS_ENABLED"),
            retained_acquisition_database_path=Path(values.get(
                "RETAINED_ACQUISITION_DATABASE_PATH", "database/evidence_platform/production/retained_acquisition.db"
            )),
        )

    @property
    def completely_off(self) -> bool:
        return not any((self.platform_enabled, self.writer_enabled, self.queue_enabled,
                        self.artifact_store_enabled, self.health_enabled,
                        self.mirror_enabled, self.normalization_enabled,
                        self.primitive_engine_enabled))

    def validate_isolation(self, production_paths: tuple[Path, ...] | None = None) -> None:
        targets = {
            "evidence database": self.database_path.resolve(),
            "evidence queue": self.queue_path.resolve(),
            "evidence artifacts": self.artifact_path.resolve(),
            "evidence mirror spool": self.mirror_spool_path.resolve(),
            "retained acquisition observations": self.retained_acquisition_database_path.resolve(),
        }
        if len(set(targets.values())) != len(targets):
            raise IsolationError(
                "Evidence database, queue, artifact, and mirror spool paths must be distinct"
            )
        candidates = list(production_paths or ())
        for key in ("DB_PATH", "WT_OPS_DB_PATH"):
            if os.environ.get(key):
                candidates.append(Path(os.environ[key]))
        forbidden = {path.resolve() for path in candidates}
        for name, target in targets.items():
            if target in forbidden:
                raise IsolationError(f"{name.title()} path aliases a production database")
