"""Prospective, operation-neutral retained acquisition observations.

This is deliberately independent of Evidence mirroring.  It captures only an
already-completed acquisition and is fail-open to its caller.
"""
from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from src.acquisition.transaction import AcquisitionResponse
from src.evidence.artifacts import ArtifactStore
from src.evidence.artifacts import ArtifactReference
from src.evidence.mirror import EvidenceMirrorPublisher
from src.acquisition.transaction import AcquisitionMetadata, AcquisitionResponse


SCHEMA_VERSION = 1


def canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def sanitize_url(url: str) -> str:
    value = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(value.query, keep_blank_values=True)
             if k.lower() not in {"api-key", "apikey", "api_key", "key", "token"}]
    return urlunsplit((value.scheme, value.netloc, value.path, urlencode(query), ""))


@dataclass(frozen=True)
class RetainedObservation:
    observation_id: str
    schema_version: int
    metadata: dict[str, Any]
    http_method: str
    url: str
    request_payload: Any
    response_status: int
    response_data: Any
    response_text: str | None
    response_headers: dict[str, str]
    raw_body_base64: str | None
    artifact_representation: str
    artifact_digest: str
    artifact_size_bytes: int
    artifact_compressed_bytes: int
    content_type: str


class RetainedAcquisitionStore:
    """Separate append-only store; caller catches all retention failures."""
    def __init__(self, database_path: Path, artifacts: ArtifactStore) -> None:
        self.path, self.artifacts = Path(database_path), artifacts

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("CREATE TABLE IF NOT EXISTS retained_acquisition_observations (observation_id TEXT PRIMARY KEY, schema_version INTEGER NOT NULL, launch_mint TEXT, acquisition_id TEXT NOT NULL, correlation_id TEXT NOT NULL, payload_json TEXT NOT NULL, retained_at INTEGER NOT NULL)")
        connection.execute("CREATE INDEX IF NOT EXISTS retained_acquisition_by_mint ON retained_acquisition_observations(launch_mint)")
        connection.execute("CREATE TABLE IF NOT EXISTS retained_acquisition_gaps (gap_id TEXT PRIMARY KEY, acquisition_id TEXT, launch_mint TEXT, correlation_id TEXT, purpose TEXT, provider TEXT, method TEXT, reason TEXT NOT NULL, recorded_at INTEGER NOT NULL)")
        connection.execute("CREATE TABLE IF NOT EXISTS retained_acquisition_outcomes (acquisition_id TEXT PRIMARY KEY, outcome TEXT NOT NULL CHECK(outcome IN ('RETAINED','FAILED_WITH_GAP','NOT_RETAINABLE','FAILED_GAP_WRITE_FAILED')), recorded_at INTEGER NOT NULL)")
        return connection

    def retain(self, response: AcquisitionResponse, *, http_method: str, url: str, request_payload: Any) -> RetainedObservation:
        metadata = asdict(response.metadata)
        raw = response.raw_body
        if raw is None:
            raw = canonical({"status": response.status, "data": response.data, "text": response.text, "headers": dict(response.headers)})
        content_type = next((v for k, v in response.headers.items() if k.lower() == "content-type"), "application/octet-stream")
        artifact = self.artifacts.put(raw, content_type=content_type, metadata={"source": "retained_acquisition", "acquisition_id": metadata["acquisition_id"]})
        identity = {"schema_version": SCHEMA_VERSION, "metadata": metadata, "http_method": http_method.upper(), "url": sanitize_url(url), "request_payload": request_payload, "response_status": int(response.status or 0), "artifact_digest": artifact.digest}
        observation_id = hashlib.sha256(canonical(identity)).hexdigest()
        value = RetainedObservation(observation_id, SCHEMA_VERSION, metadata, http_method.upper(), sanitize_url(url), request_payload, int(response.status or 0), response.data, response.text, dict(response.headers), base64.b64encode(response.raw_body).decode() if response.raw_body is not None else None, response.artifact_representation, artifact.digest, artifact.size_bytes, artifact.compressed_bytes, artifact.content_type)
        connection = self._connect()
        try:
            connection.execute("INSERT OR IGNORE INTO retained_acquisition_observations VALUES(?,?,?,?,?,?,?)", (observation_id, SCHEMA_VERSION, metadata.get("launch"), metadata["acquisition_id"], metadata["correlation_id"], canonical(asdict(value)).decode(), int(time.time())))
            connection.commit()
        finally:
            connection.close()
        return value

    def record_outcome(self, response: AcquisitionResponse, outcome: str) -> None:
        connection = self._connect()
        try:
            connection.execute("INSERT OR IGNORE INTO retained_acquisition_outcomes VALUES(?,?,?)", (response.metadata.acquisition_id, outcome, int(time.time())))
            connection.commit()
        finally: connection.close()

    def health(self) -> dict[str, Any]:
        connection = self._connect()
        try:
            outcomes = dict(connection.execute("SELECT outcome,count(*) FROM retained_acquisition_outcomes GROUP BY outcome"))
            eligible = sum(outcomes.values()); retained = outcomes.get("RETAINED", 0)
            failed_gap = outcomes.get("FAILED_WITH_GAP", 0); not_retainable = outcomes.get("NOT_RETAINABLE", 0); gap_failed = outcomes.get("FAILED_GAP_WRITE_FAILED", 0)
            return {"retention_store_healthy": True, "eligible_total": eligible, "retained_total": retained, "failed_with_gap_total": failed_gap, "not_retainable_total": not_retainable, "failed_gap_write_failed_total": gap_failed, "accounting_residual": eligible - retained - failed_gap - not_retainable - gap_failed}
        finally: connection.close()

    def record_gap(self, response: AcquisitionResponse, reason: str) -> None:
        metadata = asdict(response.metadata)
        identity = {"acquisition_id": metadata["acquisition_id"], "correlation_id": metadata["correlation_id"], "reason": str(reason)}
        connection = self._connect()
        try:
            connection.execute("INSERT OR IGNORE INTO retained_acquisition_gaps VALUES(?,?,?,?,?,?,?,?,?)", (hashlib.sha256(canonical(identity)).hexdigest(), metadata["acquisition_id"], metadata.get("launch"), metadata["correlation_id"], metadata["purpose"], metadata["provider"], metadata["method"], str(reason)[:500], int(time.time())))
            connection.commit()
        finally: connection.close()

    def get(self, *, mints: Iterable[str] | None = None, observation_ids: Iterable[str] | None = None) -> list[RetainedObservation]:
        connection = self._connect()
        try:
            clauses, params = [], []
            if mints is not None:
                values = sorted(set(mints)); clauses.append("launch_mint IN (%s)" % ",".join("?" * len(values))); params += values
            if observation_ids is not None:
                values = sorted(set(observation_ids)); clauses.append("observation_id IN (%s)" % ",".join("?" * len(values))); params += values
            query = "SELECT payload_json FROM retained_acquisition_observations" + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY observation_id"
            return [RetainedObservation(**json.loads(row[0])) for row in connection.execute(query, params)]
        finally: connection.close()

    def dry_run_envelope(self, observation: RetainedObservation) -> dict[str, Any]:
        required = ("acquisition_id", "correlation_id", "purpose", "provider", "method", "launch", "timestamp")
        missing = [key for key in required if not observation.metadata.get(key)]
        if missing:
            return {"observation_id": observation.observation_id, "state": "NOT_REPLAYABLE", "reason": "MISSING_RETAINED_INPUT:" + ",".join(missing)}
        response = AcquisitionResponse(observation.response_status, observation.response_data, observation.response_text, observation.response_headers, AcquisitionMetadata(**observation.metadata), 0.0, base64.b64decode(observation.raw_body_base64) if observation.raw_body_base64 else None, observation.artifact_representation)
        item = EvidenceMirrorPublisher.item_from_response(response, http_method=observation.http_method, url=observation.url, request_payload=observation.request_payload, handoff_at=float(observation.metadata["timestamp"]))
        artifact = ArtifactReference(observation.artifact_digest, observation.artifact_size_bytes, observation.artifact_compressed_bytes, observation.content_type)
        envelope = EvidenceMirrorPublisher._acquisition_envelope(None, item, artifact)  # type: ignore[arg-type]
        return {"observation_id": observation.observation_id, "state": "REPLAYABLE", "envelope": envelope}
