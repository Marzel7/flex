"""PSI0B-E8 durable observer-attempt provenance for the E7 launcher."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from typing import Mapping


PROVENANCE_VERSION = "psi0b-e13.v1"
AUTHORITY_CLASS = "NON_EXECUTING_OBSERVER_FAILURE_PROVENANCE"
LEDGER_NAME = "observer_attempt.jsonl"
TERMINAL_NAME = "observer_attempt.json"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_TERMINAL = {"OBSERVER_PASS", "OBSERVER_FAILED", "PRESTART_DO_NOT_START"}
_CHECKPOINT_KEYS = {
    "checkpoint_sequence", "phase", "query_id", "observed_at_epoch", "supervisor_service_identities",
    "primary_fd_count", "serializer_snapshot_digest", "serializer_lock_error_baseline",
    "serializer_queue_depth", "authoritative_write_lease_state",
    "release_pending_metadata_digest", "release_pending_metadata_components",
    "database_wal_metadata_digest", "database_wal_metadata_components",
    "database_wal_state", "pumpportal_state",
    "pumpswap_state", "ingestion_state", "gate_reason_code",
}


class ProductionShadowObserverProvenanceError(RuntimeError):
    """Named fail-closed observer provenance violation."""


@dataclass(frozen=True)
class ObserverAttemptTerminal:
    provenance_version: str
    authority_class: str
    authorization_id: str
    authorization_digest: str
    preflight_digest: str
    launcher_contract_digest: str
    terminal_status: str
    terminal_reason_code: str
    exact_exception_type: str | None
    exact_exception_message: str | None
    checkpoint_attempt_count: int
    transition_count: int
    final_transition_digest: str
    grants_extraction_authority: bool
    grants_integration_authority: bool
    grants_activation_authority: bool
    terminal_digest: str


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"


def _digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def observer_provenance_contract_digest() -> str:
    return _digest({
        "provenance_version": PROVENANCE_VERSION,
        "authority_class": AUTHORITY_CLASS,
        "files": (LEDGER_NAME, TERMINAL_NAME),
        "events": ("STARTED", "CHECKPOINT_ATTEMPT", "OBSERVER_DECISION", "OBSERVER_EXCEPTION", "TERMINAL"),
        "checkpoint_keys": tuple(sorted(_CHECKPOINT_KEYS)),
        "durability": ("APPEND_ONLY", "OPEN_XB", "FLUSH_EACH_TRANSITION", "FSYNC_EACH_TRANSITION"),
        "authority": (False, False, False),
    })


class ObserverAttemptRecorder:
    """Append-only, fsynced recorder created in one caller-supplied empty directory."""

    def __init__(
        self, directory: Path, *, authorization_id: str, authorization_digest: str,
        preflight_digest: str, launcher_contract_digest: str,
    ) -> None:
        directory = Path(directory)
        if not directory.is_dir() or any(directory.iterdir()):
            raise ProductionShadowObserverProvenanceError("PSI0B_E8_ATTEMPT_DIRECTORY_NOT_NEW_EMPTY")
        for value in (authorization_digest, preflight_digest, launcher_contract_digest):
            if not _DIGEST.fullmatch(value):
                raise ProductionShadowObserverProvenanceError("PSI0B_E8_LINEAGE_DIGEST_INVALID")
        self.directory = directory
        self.ledger_path = directory / LEDGER_NAME
        self.terminal_path = directory / TERMINAL_NAME
        self.authorization_id = authorization_id
        self.authorization_digest = authorization_digest
        self.preflight_digest = preflight_digest
        self.launcher_contract_digest = launcher_contract_digest
        self._sequence = 0
        self._previous_digest = "0" * 64
        self._checkpoint_attempt_count = 0
        self._terminal = False
        self._append("STARTED", {
            "authorization_id": authorization_id,
            "authorization_digest": authorization_digest,
            "preflight_digest": preflight_digest,
            "launcher_contract_digest": launcher_contract_digest,
            "observer_provenance_contract_digest": observer_provenance_contract_digest(),
        })

    def _append(self, event: str, payload: Mapping[str, object]) -> None:
        if self._terminal:
            raise ProductionShadowObserverProvenanceError("PSI0B_E8_APPEND_AFTER_TERMINAL")
        body = {
            "provenance_version": PROVENANCE_VERSION,
            "sequence": self._sequence,
            "event": event,
            "payload": dict(payload),
            "previous_transition_digest": self._previous_digest,
        }
        row = {**body, "transition_digest": _digest(body)}
        mode = "xb" if self._sequence == 0 else "ab"
        with self.ledger_path.open(mode) as handle:
            handle.write(_canonical(row))
            handle.flush()
            os.fsync(handle.fileno())
        self._sequence += 1
        self._previous_digest = row["transition_digest"]

    def record_checkpoint_attempt(self, **payload: object) -> None:
        if set(payload) != _CHECKPOINT_KEYS:
            raise ProductionShadowObserverProvenanceError("PSI0B_E8_CHECKPOINT_SHAPE_DRIFT")
        position = payload["checkpoint_sequence"]
        if isinstance(position, bool) or not isinstance(position, int) or position != self._checkpoint_attempt_count + 1:
            raise ProductionShadowObserverProvenanceError("PSI0B_E8_CHECKPOINT_SEQUENCE_DRIFT")
        if not isinstance(payload["supervisor_service_identities"], dict):
            raise ProductionShadowObserverProvenanceError("PSI0B_E8_SERVICE_IDENTITY_SHAPE_DRIFT")
        if payload["phase"] not in {"PRESTART", "ACTIVE"}:
            raise ProductionShadowObserverProvenanceError("PSI0B_E11_CHECKPOINT_PHASE_INVALID")
        if payload["phase"] == "PRESTART" and payload["query_id"] is not None:
            raise ProductionShadowObserverProvenanceError("PSI0B_E11_PRESTART_QUERY_ID_INVALID")
        if payload["phase"] == "ACTIVE" and not isinstance(payload["query_id"], str):
            raise ProductionShadowObserverProvenanceError("PSI0B_E11_ACTIVE_QUERY_ID_INVALID")
        if not isinstance(payload["release_pending_metadata_components"], (tuple, list)):
            raise ProductionShadowObserverProvenanceError("PSI0B_E11_RELEASE_PENDING_COMPONENTS_INVALID")
        if not isinstance(payload["database_wal_metadata_components"], (tuple, list)):
            raise ProductionShadowObserverProvenanceError("PSI0B_E13_DATABASE_WAL_COMPONENTS_INVALID")
        for name in ("serializer_snapshot_digest", "release_pending_metadata_digest", "database_wal_metadata_digest"):
            if not isinstance(payload[name], str) or not _DIGEST.fullmatch(payload[name]):
                raise ProductionShadowObserverProvenanceError("PSI0B_E8_CHECKPOINT_DIGEST_INVALID")
        if payload["database_wal_metadata_digest"] != _digest(payload["database_wal_metadata_components"]):
            if payload["database_wal_metadata_digest"] != "0" * 64 or payload["database_wal_metadata_components"]:
                raise ProductionShadowObserverProvenanceError("PSI0B_E13_DATABASE_WAL_DIGEST_MISMATCH")
        self._checkpoint_attempt_count += 1
        self._append("CHECKPOINT_ATTEMPT", payload)

    def record_decision(
        self, *, status: str, decision_digest: str, reason_codes: tuple[str, ...],
        phase: str = "PRESTART", query_id: str | None = None,
    ) -> None:
        if not _DIGEST.fullmatch(decision_digest):
            raise ProductionShadowObserverProvenanceError("PSI0B_E8_DECISION_DIGEST_INVALID")
        self._append("OBSERVER_DECISION", {
            "status": status, "decision_digest": decision_digest,
            "reason_codes": tuple(reason_codes), "phase": phase, "query_id": query_id,
        })

    def record_exception(self, exc: Exception) -> None:
        self._append("OBSERVER_EXCEPTION", {
            "exception_type": type(exc).__name__, "exception_message": str(exc),
        })

    def finalize(
        self, *, terminal_status: str, terminal_reason_code: str,
        exception: Exception | None = None,
    ) -> ObserverAttemptTerminal:
        if terminal_status not in _TERMINAL:
            raise ProductionShadowObserverProvenanceError("PSI0B_E8_TERMINAL_STATUS_INVALID")
        self._append("TERMINAL", {
            "terminal_status": terminal_status,
            "terminal_reason_code": terminal_reason_code,
            "exact_exception_type": type(exception).__name__ if exception else None,
            "exact_exception_message": str(exception) if exception else None,
        })
        self._terminal = True
        values = {
            "provenance_version": PROVENANCE_VERSION,
            "authority_class": AUTHORITY_CLASS,
            "authorization_id": self.authorization_id,
            "authorization_digest": self.authorization_digest,
            "preflight_digest": self.preflight_digest,
            "launcher_contract_digest": self.launcher_contract_digest,
            "terminal_status": terminal_status,
            "terminal_reason_code": terminal_reason_code,
            "exact_exception_type": type(exception).__name__ if exception else None,
            "exact_exception_message": str(exception) if exception else None,
            "checkpoint_attempt_count": self._checkpoint_attempt_count,
            "transition_count": self._sequence,
            "final_transition_digest": self._previous_digest,
            "grants_extraction_authority": False,
            "grants_integration_authority": False,
            "grants_activation_authority": False,
        }
        terminal = ObserverAttemptTerminal(**values, terminal_digest=_digest(values))
        with self.terminal_path.open("xb") as handle:
            handle.write(_canonical(asdict(terminal)))
            handle.flush()
            os.fsync(handle.fileno())
        verify_observer_attempt_bundle(self.directory)
        return terminal


def verify_observer_attempt_bundle(directory: Path) -> ObserverAttemptTerminal:
    directory = Path(directory)
    if not directory.is_dir() or {path.name for path in directory.iterdir()} != {LEDGER_NAME, TERMINAL_NAME}:
        raise ProductionShadowObserverProvenanceError("PSI0B_E8_FILE_SET_MISMATCH")
    try:
        raw_rows = (directory / LEDGER_NAME).read_bytes().splitlines(keepends=True)
        rows = [json.loads(row) for row in raw_rows]
        terminal_raw = (directory / TERMINAL_NAME).read_bytes()
        terminal_values = json.loads(terminal_raw)
        terminal = ObserverAttemptTerminal(**terminal_values)
    except Exception as exc:
        raise ProductionShadowObserverProvenanceError("PSI0B_E8_INVALID_CANONICAL_JSON") from exc
    if any(raw != _canonical(row) for raw, row in zip(raw_rows, rows)) or terminal_raw != _canonical(terminal_values):
        raise ProductionShadowObserverProvenanceError("PSI0B_E8_NONCANONICAL_BYTES")
    previous = "0" * 64
    for sequence, row in enumerate(rows):
        expected_keys = {"provenance_version", "sequence", "event", "payload", "previous_transition_digest", "transition_digest"}
        if set(row) != expected_keys or row["sequence"] != sequence or row["previous_transition_digest"] != previous:
            raise ProductionShadowObserverProvenanceError("PSI0B_E8_TRANSITION_CHAIN_DRIFT")
        body = {key: row[key] for key in expected_keys - {"transition_digest"}}
        if row["transition_digest"] != _digest(body):
            raise ProductionShadowObserverProvenanceError("PSI0B_E8_TRANSITION_DIGEST_DRIFT")
        previous = row["transition_digest"]
    terminal_body = asdict(terminal); digest = terminal_body.pop("terminal_digest")
    if digest != _digest(terminal_body) or terminal.final_transition_digest != previous or terminal.transition_count != len(rows):
        raise ProductionShadowObserverProvenanceError("PSI0B_E8_TERMINAL_REPLAY_DRIFT")
    if terminal.terminal_status not in _TERMINAL or any((terminal.grants_extraction_authority, terminal.grants_integration_authority, terminal.grants_activation_authority)):
        raise ProductionShadowObserverProvenanceError("PSI0B_E8_AUTHORITY_DRIFT")
    if not rows or rows[0]["event"] != "STARTED" or rows[-1]["event"] != "TERMINAL":
        raise ProductionShadowObserverProvenanceError("PSI0B_E8_EVENT_ORDER_DRIFT")
    if terminal.checkpoint_attempt_count != sum(row["event"] == "CHECKPOINT_ATTEMPT" for row in rows):
        raise ProductionShadowObserverProvenanceError("PSI0B_E8_CHECKPOINT_ACCOUNTING_DRIFT")
    return terminal
