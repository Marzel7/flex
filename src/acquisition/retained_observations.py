"""Prospective, operation-neutral retained acquisition observations.

This is deliberately independent of Evidence mirroring.  It captures only an
already-completed acquisition and is fail-open to its caller.
"""
from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from src.evidence.artifacts import ArtifactStore
from src.evidence.artifacts import ArtifactReference
from src.evidence.mirror import EvidenceMirrorPublisher
from src.acquisition.transaction import AcquisitionMetadata, AcquisitionResponse


SCHEMA_VERSION = 1
SCHEMA_VERSION_V2 = 2
BUSY_TIMEOUT_MS = 50
GIB_BYTES = 1024 * 1024 * 1024


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
        # Retention is optional and synchronous at acquisition completion: it
        # gets one short local-store attempt, never SQLite's default 5 seconds.
        connection = sqlite3.connect(self.path, timeout=BUSY_TIMEOUT_MS / 1000)
        connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("CREATE TABLE IF NOT EXISTS retained_acquisition_observations (observation_id TEXT PRIMARY KEY, schema_version INTEGER NOT NULL, launch_mint TEXT, acquisition_id TEXT NOT NULL, correlation_id TEXT NOT NULL, payload_json TEXT NOT NULL, retained_at INTEGER NOT NULL)")
        connection.execute("CREATE INDEX IF NOT EXISTS retained_acquisition_by_mint ON retained_acquisition_observations(launch_mint)")
        connection.execute("CREATE TABLE IF NOT EXISTS retained_acquisition_gaps (gap_id TEXT PRIMARY KEY, acquisition_id TEXT, launch_mint TEXT, correlation_id TEXT, purpose TEXT, provider TEXT, method TEXT, reason TEXT NOT NULL, recorded_at INTEGER NOT NULL)")
        connection.execute("CREATE TABLE IF NOT EXISTS retained_acquisition_outcomes (acquisition_id TEXT PRIMARY KEY, outcome TEXT NOT NULL CHECK(outcome IN ('RETAINED','FAILED_WITH_GAP','NOT_RETAINABLE','FAILED_GAP_WRITE_FAILED')), recorded_at INTEGER NOT NULL)")
        return connection

    def _read_connect(self) -> sqlite3.Connection:
        """Open the isolated store strictly read-only for health/reconstruction."""
        return sqlite3.connect(f"file:{self.path.resolve()}?mode=ro", uri=True)

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
        connection = self._read_connect()
        try:
            outcomes = dict(connection.execute("SELECT outcome,count(*) FROM retained_acquisition_outcomes GROUP BY outcome"))
            last_retained = connection.execute("SELECT max(retained_at) FROM retained_acquisition_observations").fetchone()[0]
            eligible = sum(outcomes.values()); retained = outcomes.get("RETAINED", 0)
            failed_gap = outcomes.get("FAILED_WITH_GAP", 0); not_retainable = outcomes.get("NOT_RETAINABLE", 0); gap_failed = outcomes.get("FAILED_GAP_WRITE_FAILED", 0)
            return {"retention_store_healthy": True, "eligible_total": eligible, "retained_total": retained, "failed_with_gap_total": failed_gap, "not_retainable_total": not_retainable, "failed_gap_write_failed_total": gap_failed, "accounting_residual": eligible - retained - failed_gap - not_retainable - gap_failed, "last_retained_at": last_retained}
        finally: connection.close()

    def combined_accounting(self, journal: Any) -> dict[str, int]:
        """Main outcomes win; journal-only acquisition identities are catastrophic failures."""
        connection = self._read_connect()
        try:
            main = dict(connection.execute("SELECT acquisition_id,outcome FROM retained_acquisition_outcomes"))
        finally: connection.close()
        outcomes = dict(main)
        invalid_journal_event_total = 0
        # Treat the journal as an independent durable boundary.  A corrupt or
        # partial file must not prevent reconstruction of its valid neighbours.
        journal_dir = Path(journal.path).parent
        for path in sorted(journal_dir.glob("*.json")) if journal_dir.exists() else ():
            try:
                event = json.loads(path.read_text())
                acquisition_id = event.get("acquisition_identity")
                if not isinstance(acquisition_id, str) or not acquisition_id:
                    invalid_journal_event_total += 1
                    continue
            except (OSError, ValueError, TypeError):
                invalid_journal_event_total += 1
                continue
            if acquisition_id not in outcomes:
                outcomes[acquisition_id] = "FAILED_GAP_WRITE_FAILED"
        counts = {key: list(outcomes.values()).count(key) for key in ("RETAINED", "FAILED_WITH_GAP", "NOT_RETAINABLE", "FAILED_GAP_WRITE_FAILED")}
        return {"eligible_total": len(outcomes), "retained_total": counts["RETAINED"], "failed_with_gap_total": counts["FAILED_WITH_GAP"], "not_retainable_total": counts["NOT_RETAINABLE"], "failed_gap_write_failed_total": counts["FAILED_GAP_WRITE_FAILED"], "unresolved_durable_total": 0, "invalid_journal_event_total": invalid_journal_event_total, "accounting_residual": 0}

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


def _build_v2_hot_payload(
    *,
    value: RetainedObservation,
    metadata: dict[str, Any],
    sanitized_url: str,
) -> tuple[str, str, int]:
    response_headers = dict(value.response_headers or {})
    response_headers_digest = hashlib.sha256(canonical(response_headers)).hexdigest()
    request_payload_digest = _sha256_json(value.request_payload) if value.request_payload is not None else None
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION_V2,
        "observation_id": value.observation_id,
        "acquisition_id": value.metadata.get("acquisition_id"),
        "correlation_id": value.metadata.get("correlation_id"),
        "launch_mint": value.metadata.get("launch"),
        "http_method": value.http_method,
        "url": sanitized_url,
        "request_payload_sha256": request_payload_digest,
        "response_status": value.response_status,
        "response_data_present": value.response_data is not None,
        "response_text_present": value.response_text is not None,
        "response_headers_sha256": response_headers_digest,
        "artifact_representation": value.artifact_representation,
        "artifact_digest": value.artifact_digest,
        "artifact_size_bytes": value.artifact_size_bytes,
        "artifact_compressed_bytes": value.artifact_compressed_bytes,
        "content_type": value.content_type,
        "metadata": {k: metadata.get(k) for k in ("launch", "purpose", "provider", "method", "request_type", "timestamp")},
        "retained_at": int(time.time()),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return payload["observation_id"], encoded.decode(), len(encoded)


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def _observation_from_payload(payload: dict[str, Any], *, fallback_observation_id: str, fallback_metadata: dict[str, Any] | None = None) -> RetainedObservation:
    metadata = dict(payload.get("metadata") or fallback_metadata or {})
    # V2 hot payloads (_build_v2_hot_payload) store acquisition/correlation/mint
    # identity at the TOP LEVEL, not nested under metadata like full payloads do.
    # Backfill from the top level only where metadata doesn't already have them,
    # so full-payload reconstruction (already correct) is unaffected.
    metadata.setdefault("acquisition_id", payload.get("acquisition_id"))
    metadata.setdefault("correlation_id", payload.get("correlation_id"))
    metadata.setdefault("launch", payload.get("launch_mint"))
    return RetainedObservation(
        observation_id=payload.get("observation_id", fallback_observation_id),
        schema_version=int(payload.get("schema_version", SCHEMA_VERSION)),
        metadata=dict(metadata),
        http_method=payload.get("http_method", ""),
        url=payload.get("url", ""),
        request_payload=payload.get("request_payload") or payload.get("request_payload_digest"),
        response_status=int(payload.get("response_status", 0)),
        response_data=payload.get("response_data"),
        response_text=payload.get("response_text"),
        response_headers=payload.get("response_headers", {}),
        raw_body_base64=payload.get("raw_body_base64"),
        artifact_representation=payload.get("artifact_representation", ""),
        artifact_digest=payload.get("artifact_digest", ""),
        artifact_size_bytes=int(payload.get("artifact_size_bytes", 0)),
        artifact_compressed_bytes=int(payload.get("artifact_compressed_bytes", 0)),
        content_type=payload.get("content_type", "application/octet-stream"),
    )


class RetainedAcquisitionStoreV2:
    """Bounded retained store for RA4 shadow activation.

    STORAGE-LIFECYCLE-P2 (SHADOW_RETENTION_PERSISTENT_BUDGET_REQUIRED):
    the retention budget is BOUNDED_RETAINED_RAW_BYTES, not
    PROCESS_LIFETIME_BYTES and not a calendar-day allowance (the prior
    'daily_payload_cap_bytes' name was misleading -- the implementation
    never had calendar-day semantics; it was simply reset by every
    process restart). The budget now reconciles against the durable
    SUM(LENGTH(payload_json)) already stored in the database at
    construction time, so a process restart can NEVER grant a fresh
    allowance -- the accounted usage always reflects actual retained
    bytes on disk, not an in-process counter that starts at zero.
    """

    def __init__(
        self,
        database_path: Path,
        artifacts: ArtifactStore,
        *,
        daily_payload_cap_bytes: int = 1 * GIB_BYTES,
    ) -> None:
        self.path, self.artifacts = Path(database_path), artifacts
        self.daily_payload_cap_bytes = max(1_000_000, daily_payload_cap_bytes)
        self._observation_count, self._payload_bytes = self._reconcile_durable_usage()

    def _reconcile_durable_usage(self) -> tuple[int, int]:
        """STORAGE-LIFECYCLE-P2: reads the durable, already-retained byte
        total directly from the database (SUM(LENGTH(payload_json)) across
        ALL retained rows, not just 'full' payloads -- this is the actual
        retained-bytes-on-disk figure, a strictly more correct measure of
        'bounded retained bytes' than the original per-process full-
        payload-only counter). Fails closed: if the database does not yet
        exist, or the accounting query cannot be completed for any reason,
        usage is treated as UNKNOWN and the budget is reported as fully
        consumed (0 bytes remaining) rather than silently granting a fresh
        allowance -- this is deliberately the SAFE failure direction: a
        write that should have been budget-limited is never allowed
        through merely because reconciliation itself failed.

        This is a read-only query against the SAME file this store will
        later open for writes -- opened here with mode=ro so construction
        never blocks on or contends with an active writer, and never
        creates the file if it doesn't exist yet (a brand-new store
        correctly reconciles to (0, 0))."""
        if not self.path.exists():
            return 0, 0
        try:
            connection = sqlite3.connect(f"file:{self.path.resolve()}?mode=ro", uri=True, timeout=BUSY_TIMEOUT_MS / 1000)
            try:
                connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
                row = connection.execute(
                    "SELECT COUNT(*), COALESCE(SUM(LENGTH(payload_json)), 0) FROM retained_acquisition_observations"
                ).fetchone()
                return int(row[0]), int(row[1])
            finally:
                connection.close()
        except sqlite3.Error:
            # Fail closed: unknown durable usage is treated as "budget
            # already fully consumed" (daily_payload_cap_bytes), never as
            # "budget is fresh" (0) -- see docstring above.
            return 0, self.daily_payload_cap_bytes

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=BUSY_TIMEOUT_MS / 1000)
        connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS retained_acquisition_observations (observation_id TEXT PRIMARY KEY, schema_version INTEGER NOT NULL, launch_mint TEXT, acquisition_id TEXT NOT NULL, correlation_id TEXT NOT NULL, payload_json TEXT NOT NULL, retained_at INTEGER NOT NULL)"
        )
        connection.execute("CREATE INDEX IF NOT EXISTS retained_acquisition_by_mint ON retained_acquisition_observations(launch_mint)")
        connection.execute("CREATE TABLE IF NOT EXISTS retained_acquisition_gaps (gap_id TEXT PRIMARY KEY, acquisition_id TEXT, launch_mint TEXT, correlation_id TEXT, purpose TEXT, provider TEXT, method TEXT, reason TEXT NOT NULL, recorded_at INTEGER NOT NULL)")
        connection.execute("CREATE TABLE IF NOT EXISTS retained_acquisition_outcomes (acquisition_id TEXT PRIMARY KEY, outcome TEXT NOT NULL CHECK(outcome IN ('RETAINED','FAILED_WITH_GAP','NOT_RETAINABLE','FAILED_GAP_WRITE_FAILED')), recorded_at INTEGER NOT NULL)")
        return connection

    def _read_connect(self) -> sqlite3.Connection:
        return sqlite3.connect(f"file:{self.path.resolve()}?mode=ro", uri=True)

    def _is_within_budget(self, payload_bytes: int) -> bool:
        return self._payload_bytes + payload_bytes <= self.daily_payload_cap_bytes

    def _write_budgeted(self, payload_bytes: int) -> tuple[bool, dict[str, int]]:
        usage = shutil.disk_usage(self.path.parent)
        budget_remaining = {"daily_payload_cap_bytes": self.daily_payload_cap_bytes, "used_payload_bytes": self._payload_bytes}
        if usage.free < 10 * GIB_BYTES:
            return False, budget_remaining
        if not self._is_within_budget(payload_bytes):
            return False, budget_remaining
        self._payload_bytes += payload_bytes
        self._observation_count += 1
        return True, {
            **budget_remaining,
            "used_payload_bytes": self._payload_bytes,
            "observation_count": self._observation_count,
        }

    def retain(self, response: AcquisitionResponse, *, http_method: str, url: str, request_payload: Any) -> RetainedObservation:
        metadata = asdict(response.metadata)
        raw = response.raw_body
        if raw is None:
            raw = canonical({"status": response.status, "data": response.data, "text": response.text, "headers": dict(response.headers)})
        content_type = next((v for k, v in response.headers.items() if k.lower() == "content-type"), "application/octet-stream")
        artifact = self.artifacts.put(raw, content_type=content_type, metadata={"source": "retained_acquisition_v2_shadow", "acquisition_id": metadata["acquisition_id"]})

        sanitized_url = sanitize_url(url)
        identity = {
            "schema_version": SCHEMA_VERSION_V2,
            "metadata": {
                "acquisition_id": metadata["acquisition_id"],
                "correlation_id": metadata["correlation_id"],
                "launch": metadata.get("launch"),
                "timestamp": metadata["timestamp"],
                "provider": metadata.get("provider"),
            },
            "http_method": http_method.upper(),
            "url": sanitized_url,
            "artifact_digest": artifact.digest,
            "response_status": int(response.status or 0),
            "request_payload_sha256": _sha256_json(request_payload) if request_payload is not None else None,
        }
        observation_id = hashlib.sha256(canonical(identity)).hexdigest()
        full = RetainedObservation(
            observation_id,
            SCHEMA_VERSION_V2,
            metadata,
            http_method.upper(),
            sanitized_url,
            request_payload,
            int(response.status or 0),
            response.data,
            response.text,
            dict(response.headers),
            base64.b64encode(response.raw_body).decode() if response.raw_body is not None else None,
            response.artifact_representation,
            artifact.digest,
            artifact.size_bytes,
            artifact.compressed_bytes,
            artifact.content_type,
        )

        _, hot_payload, hot_bytes = _build_v2_hot_payload(value=full, metadata=metadata, sanitized_url=sanitized_url)
        full_payload = canonical(asdict(full))
        full_payload_bytes = len(full_payload)
        write_full, budget_state = self._write_budgeted(full_payload_bytes)
        if not write_full:
            payload_json = hot_payload
            schema_version = SCHEMA_VERSION_V2
        else:
            payload_json = full_payload.decode()
            schema_version = SCHEMA_VERSION_V2
        connection = self._connect()
        try:
            connection.execute(
                "INSERT OR IGNORE INTO retained_acquisition_observations VALUES(?,?,?,?,?,?,?)",
                (observation_id, schema_version, metadata.get("launch"), metadata["acquisition_id"], metadata["correlation_id"], payload_json, int(time.time())),
            )
            connection.commit()
        finally:
            connection.close()
        return full

    def record_outcome(self, response: AcquisitionResponse, outcome: str) -> None:
        connection = self._connect()
        try:
            connection.execute("INSERT OR IGNORE INTO retained_acquisition_outcomes VALUES(?,?,?)", (response.metadata.acquisition_id, outcome, int(time.time())))
            connection.commit()
        finally:
            connection.close()

    def health(self) -> dict[str, Any]:
        connection = self._read_connect()
        try:
            outcomes = dict(connection.execute("SELECT outcome,count(*) FROM retained_acquisition_outcomes GROUP BY outcome"))
            last_retained = connection.execute("SELECT max(retained_at) FROM retained_acquisition_observations").fetchone()[0]
            eligible = sum(outcomes.values())
            retained = outcomes.get("RETAINED", 0)
            failed_gap = outcomes.get("FAILED_WITH_GAP", 0)
            not_retainable = outcomes.get("NOT_RETAINABLE", 0)
            gap_failed = outcomes.get("FAILED_GAP_WRITE_FAILED", 0)
            return {
                "retention_store_healthy": True,
                "eligible_total": eligible,
                "retained_total": retained,
                "failed_with_gap_total": failed_gap,
                "not_retainable_total": not_retainable,
                "failed_gap_write_failed_total": gap_failed,
                "accounting_residual": eligible - retained - failed_gap - not_retainable - gap_failed,
                "last_retained_at": last_retained,
                "observation_count": self._observation_count,
                "payload_bytes": self._payload_bytes,
                "daily_payload_cap_bytes": self.daily_payload_cap_bytes,
            }
        finally:
            connection.close()

    def combined_accounting(self, journal: Any) -> dict[str, int]:
        connection = self._read_connect()
        try:
            main = dict(connection.execute("SELECT acquisition_id,outcome FROM retained_acquisition_outcomes"))
        finally:
            connection.close()
        outcomes = dict(main)
        invalid_journal_event_total = 0
        journal_dir = Path(journal.path).parent
        for path in sorted(journal_dir.glob("*.json")) if journal_dir.exists() else ():
            try:
                event = json.loads(path.read_text())
                acquisition_id = event.get("acquisition_identity")
                if not isinstance(acquisition_id, str) or not acquisition_id:
                    invalid_journal_event_total += 1
                    continue
            except (OSError, ValueError, TypeError):
                invalid_journal_event_total += 1
                continue
            if acquisition_id not in outcomes:
                outcomes[acquisition_id] = "FAILED_GAP_WRITE_FAILED"
        counts = {key: list(outcomes.values()).count(key) for key in ("RETAINED", "FAILED_WITH_GAP", "NOT_RETAINABLE", "FAILED_GAP_WRITE_FAILED")}
        return {"eligible_total": len(outcomes), "retained_total": counts["RETAINED"], "failed_with_gap_total": counts["FAILED_WITH_GAP"], "not_retainable_total": counts["NOT_RETAINABLE"], "failed_gap_write_failed_total": counts["FAILED_GAP_WRITE_FAILED"], "unresolved_durable_total": 0, "invalid_journal_event_total": invalid_journal_event_total, "accounting_residual": 0}

    def record_gap(self, response: AcquisitionResponse, reason: str) -> None:
        metadata = asdict(response.metadata)
        identity = {"acquisition_id": metadata["acquisition_id"], "correlation_id": metadata["correlation_id"], "reason": str(reason)}
        connection = self._connect()
        try:
            connection.execute(
                "INSERT OR IGNORE INTO retained_acquisition_gaps VALUES(?,?,?,?,?,?,?,?,?)",
                (hashlib.sha256(canonical(identity)).hexdigest(), metadata["acquisition_id"], metadata.get("launch"), metadata["correlation_id"], metadata["purpose"], metadata["provider"], metadata["method"], str(reason)[:500], int(time.time())),
            )
            connection.commit()
        finally:
            connection.close()

    def get(self, *, mints: Iterable[str] | None = None, observation_ids: Iterable[str] | None = None) -> list[RetainedObservation]:
        connection = self._connect()
        try:
            clauses, params = [], []
            if mints is not None:
                values = sorted(set(mints))
                clauses.append("launch_mint IN (%s)" % ",".join("?" * len(values)))
                params += values
            if observation_ids is not None:
                values = sorted(set(observation_ids))
                clauses.append("observation_id IN (%s)" % ",".join("?" * len(values)))
                params += values
            query = "SELECT payload_json FROM retained_acquisition_observations" + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY observation_id"
            rows = []
            for row in connection.execute(query, params):
                try:
                    payload = json.loads(row[0])
                    rows.append(_observation_from_payload(payload, fallback_observation_id="", fallback_metadata={}))
                except (TypeError, ValueError):
                    continue
            return rows
        finally:
            connection.close()
