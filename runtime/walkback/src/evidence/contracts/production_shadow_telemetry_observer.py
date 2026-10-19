"""PSI0B-E9 committed telemetry observer for E8 production-shadow provenance."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time
from typing import Callable, Mapping

from .production_shadow_health_gate import (
    HEALTHY, RUNNING, HealthCheckpoint, HealthGateDecision,
    build_health_checkpoint, build_production_shadow_health_gate_contract,
    evaluate_active_health_gate, evaluate_prestart_health_gate,
)
from .production_shadow_observer_provenance import ObserverAttemptRecorder


OBSERVER_VERSION = "psi0b-e13.v1"
AUTHORITY_CLASS = "QUERY_FREE_PRODUCTION_TELEMETRY_OBSERVER"
REQUIRED_SERVICES = ("walkback_worker", "watchtower_listener", "ws_cascade")
_SUPERVISOR_LINE = re.compile(r"^(?P<name>\S+)\s+(?P<state>[A-Z]+)(?:\s+pid\s+(?P<pid>\d+),)?(?:\s+.*)?$")


class ProductionShadowTelemetryObserverError(RuntimeError):
    """Named fail-closed telemetry collection or health-gate violation."""


@dataclass(frozen=True)
class SupervisorResult:
    return_code: int
    stdout: str
    stderr: str = ""


@dataclass(frozen=True)
class TelemetryDependencies:
    supervisor_status: Callable[[], SupervisorResult]
    serializer_snapshot: Callable[[], Mapping[str, object]]
    primary_fd_count: Callable[[int], int]
    critical_listener_count: Callable[[], int]
    authoritative_write_lease_present: Callable[[], bool]
    release_pending_metadata: Callable[[], object]
    database_wal_metadata: Callable[[], object]
    feed_states: Callable[[], Mapping[str, str]]
    clock: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep


@dataclass(frozen=True)
class ParsedSupervisorStatus:
    return_code: int
    stdout_digest: str
    service_identities: Mapping[str, Mapping[str, object]]


def _digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _validated_release_pending_components(value: object) -> tuple[tuple[str, int, int], ...]:
    if not isinstance(value, (tuple, list)):
        raise ProductionShadowTelemetryObserverError("PSI0B_E11_RELEASE_PENDING_METADATA_MALFORMED")
    rows = []
    for item in value:
        if not isinstance(item, (tuple, list)) or len(item) != 3:
            raise ProductionShadowTelemetryObserverError("PSI0B_E11_RELEASE_PENDING_METADATA_MALFORMED")
        path, mtime_ns, size = item
        if (
            not isinstance(path, str) or not path.endswith(".tmp")
            or isinstance(mtime_ns, bool) or not isinstance(mtime_ns, int) or mtime_ns < 0
            or isinstance(size, bool) or not isinstance(size, int) or size < 0
        ):
            raise ProductionShadowTelemetryObserverError("PSI0B_E11_RELEASE_PENDING_METADATA_MALFORMED")
        rows.append((path, mtime_ns, size))
    normalized = tuple(sorted(rows))
    if len({row[0] for row in normalized}) != len(normalized):
        raise ProductionShadowTelemetryObserverError("PSI0B_E11_RELEASE_PENDING_METADATA_MALFORMED")
    return normalized


_DATABASE_FILES = {
    "main": "flex_complete_database.db",
    "ops": "wt_ops_v2.db",
}
_FILESYSTEM_KEYS = {
    "database_id", "database_path", "database_exists", "database_type",
    "database_inode", "database_size", "database_mtime_ns", "wal_path",
    "wal_exists", "wal_type", "wal_inode", "wal_size", "wal_mtime_ns",
}


def _validated_database_wal_components(value: object) -> tuple[dict[str, object], ...]:
    if not isinstance(value, (tuple, list)) or len(value) != len(_DATABASE_FILES):
        raise ProductionShadowTelemetryObserverError("PSI0B_E13_DATABASE_WAL_METADATA_MALFORMED")
    rows: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) != _FILESYSTEM_KEYS:
            raise ProductionShadowTelemetryObserverError("PSI0B_E13_DATABASE_WAL_METADATA_MALFORMED")
        row = dict(item)
        database_id = row["database_id"]
        if database_id not in _DATABASE_FILES or any(existing["database_id"] == database_id for existing in rows):
            raise ProductionShadowTelemetryObserverError("PSI0B_E13_DATABASE_WAL_METADATA_MALFORMED")
        database_path = row["database_path"]
        wal_path = row["wal_path"]
        if (
            not isinstance(database_path, str) or not Path(database_path).is_absolute()
            or Path(database_path).name != _DATABASE_FILES[database_id]
            or not isinstance(wal_path, str) or wal_path != database_path + "-wal"
        ):
            raise ProductionShadowTelemetryObserverError("PSI0B_E13_DATABASE_PATH_UNKNOWN")
        for name in ("database_exists", "wal_exists"):
            if not isinstance(row[name], bool):
                raise ProductionShadowTelemetryObserverError("PSI0B_E13_DATABASE_WAL_METADATA_MALFORMED")
        if not row["database_exists"] or row["database_type"] != "REGULAR":
            raise ProductionShadowTelemetryObserverError("PSI0B_E13_DATABASE_FILE_INVALID")
        for name in ("database_inode", "database_size", "database_mtime_ns"):
            if isinstance(row[name], bool) or not isinstance(row[name], int) or row[name] < (1 if name.endswith("inode") else 0):
                raise ProductionShadowTelemetryObserverError("PSI0B_E13_DATABASE_WAL_METADATA_MALFORMED")
        if row["wal_exists"]:
            if row["wal_type"] != "REGULAR":
                raise ProductionShadowTelemetryObserverError("PSI0B_E13_WAL_FILE_INVALID")
            for name in ("wal_inode", "wal_size", "wal_mtime_ns"):
                if isinstance(row[name], bool) or not isinstance(row[name], int) or row[name] < (1 if name.endswith("inode") else 0):
                    raise ProductionShadowTelemetryObserverError("PSI0B_E13_DATABASE_WAL_METADATA_MALFORMED")
        elif row["wal_type"] != "ABSENT" or any(row[name] is not None for name in ("wal_inode", "wal_size", "wal_mtime_ns")):
            raise ProductionShadowTelemetryObserverError("PSI0B_E13_DATABASE_WAL_METADATA_MALFORMED")
        rows.append(row)
    return tuple(sorted(rows, key=lambda row: str(row["database_id"])))


def telemetry_observer_contract_digest() -> str:
    return _digest({
        "observer_version": OBSERVER_VERSION,
        "authority_class": AUTHORITY_CLASS,
        "required_services": REQUIRED_SERVICES,
        "accepted_supervisor_return_codes": (0, 3),
        "checkpoint_spacing_seconds": 30.0,
        "required_prestart_checkpoints": 3,
        "failure_policy": "RECORD_BEFORE_RAISE",
        "release_pending_semantics": "NON_AUTHORITATIVE_COMPONENTS_RECORDED_NOT_GATING",
        "database_wal_semantics": "PER_DATABASE_REGULAR_DB_AND_ABSENT_OR_REGULAR_WAL",
        "database_wal_provenance": "EXISTENCE_TYPE_INODE_SIZE_MTIME_AND_CANONICAL_DIGEST",
        "active_checkpoint_provenance": "APPEND_ONLY_FSYNCED_BEFORE_SOURCE_OPEN",
        "authority": (False, False, False),
    })


def parse_supervisor_status(result: SupervisorResult) -> ParsedSupervisorStatus:
    if result.return_code not in (0, 3):
        raise ProductionShadowTelemetryObserverError("PSI0B_E9_SUPERVISOR_TRANSPORT_FAILED")
    if not isinstance(result.stdout, str) or not result.stdout.strip():
        raise ProductionShadowTelemetryObserverError("PSI0B_E9_SUPERVISOR_OUTPUT_MISSING")
    parsed = {}
    for line in result.stdout.splitlines():
        match = _SUPERVISOR_LINE.fullmatch(line.strip())
        if not match:
            raise ProductionShadowTelemetryObserverError("PSI0B_E9_SUPERVISOR_OUTPUT_MALFORMED")
        name = match.group("name")
        if name in parsed:
            raise ProductionShadowTelemetryObserverError("PSI0B_E9_SUPERVISOR_DUPLICATE_SERVICE")
        parsed[name] = {
            "state": match.group("state"),
            "pid": int(match.group("pid")) if match.group("pid") else None,
        }
    missing = set(REQUIRED_SERVICES) - set(parsed)
    if missing:
        raise ProductionShadowTelemetryObserverError("PSI0B_E9_SUPERVISOR_REQUIRED_SERVICE_MISSING")
    required = {name: parsed[name] for name in REQUIRED_SERVICES}
    if any(row["state"] != RUNNING or not isinstance(row["pid"], int) or row["pid"] <= 0 for row in required.values()):
        raise ProductionShadowTelemetryObserverError("PSI0B_E9_SUPERVISOR_REQUIRED_SERVICE_NOT_RUNNING")
    return ParsedSupervisorStatus(
        return_code=result.return_code,
        stdout_digest=sha256(result.stdout.encode()).hexdigest(),
        service_identities=required,
    )


class ProductionTelemetryObserver:
    def __init__(self, dependencies: TelemetryDependencies) -> None:
        self.dependencies = dependencies
        self.contract = build_production_shadow_health_gate_contract()
        self.expected_pids = None
        self.baseline_lock_errors = None
        self.baseline_critical = None
        self.baseline_release_pending_digest = None
        self.previous_checkpoint = None
        self.checkpoint_attempts = 0
        self.recorder = None

    def _record_failure(
        self, recorder: ObserverAttemptRecorder, reason: str, *, supervisor_result=None,
        phase: str = "PRESTART", query_id: str | None = None,
    ) -> None:
        self.checkpoint_attempts += 1
        result = supervisor_result or SupervisorResult(-1, "", "collection failed")
        recorder.record_checkpoint_attempt(
            checkpoint_sequence=self.checkpoint_attempts,
            phase=phase,
            query_id=query_id,
            observed_at_epoch=float(self.dependencies.clock()),
            supervisor_service_identities={"_transport": {
                "return_code": result.return_code,
                "stdout_digest": sha256(result.stdout.encode()).hexdigest(),
                "stderr_digest": sha256(result.stderr.encode()).hexdigest(),
            }},
            primary_fd_count=-1,
            serializer_snapshot_digest="0" * 64,
            serializer_lock_error_baseline=-1,
            serializer_queue_depth=-1,
            authoritative_write_lease_state="UNKNOWN",
            release_pending_metadata_digest="0" * 64,
            release_pending_metadata_components=(),
            database_wal_metadata_digest="0" * 64,
            database_wal_metadata_components=(),
            database_wal_state="UNKNOWN",
            pumpportal_state="UNKNOWN",
            pumpswap_state="UNKNOWN",
            ingestion_state="UNKNOWN",
            gate_reason_code=reason,
        )

    def checkpoint(
        self, recorder: ObserverAttemptRecorder | None = None, *,
        phase: str = "PRESTART", query_id: str | None = None,
    ) -> HealthCheckpoint:
        result = None
        try:
            result = self.dependencies.supervisor_status()
            supervisor = parse_supervisor_status(result)
        except Exception as exc:
            reason = str(exc) if isinstance(exc, ProductionShadowTelemetryObserverError) else "PSI0B_E9_SUPERVISOR_COLLECTION_EXCEPTION"
            if recorder is not None:
                self._record_failure(
                    recorder, reason, supervisor_result=result, phase=phase, query_id=query_id,
                )
            raise ProductionShadowTelemetryObserverError(reason) from exc

        identities = dict(supervisor.service_identities)
        identities["_transport"] = {
            "return_code": supervisor.return_code,
            "stdout_digest": supervisor.stdout_digest,
            "stderr_digest": sha256(result.stderr.encode()).hexdigest(),
        }
        reason = None
        metrics = {}; now = float(self.dependencies.clock())
        primary_fds = -1; critical = -1; lease_present = False
        release_pending = None; release_digest = "0" * 64
        wal_components = None; wal_digest = "0" * 64
        wal_ok = False; feeds = {"pumpportal": "UNKNOWN", "pumpswap": "UNKNOWN", "ingestion": "UNKNOWN"}
        try:
            metrics = dict(self.dependencies.serializer_snapshot())
            primary_fds = int(self.dependencies.primary_fd_count(int(identities["watchtower_listener"]["pid"])))
            critical = int(self.dependencies.critical_listener_count())
            lease_present = bool(self.dependencies.authoritative_write_lease_present())
            release_pending = _validated_release_pending_components(
                self.dependencies.release_pending_metadata()
            )
            release_digest = _digest(release_pending)
            wal_components = _validated_database_wal_components(
                self.dependencies.database_wal_metadata()
            )
            wal_digest = _digest(wal_components)
            wal_ok = True
            feeds = dict(self.dependencies.feed_states())
        except Exception as exc:
            reason = (
                str(exc) if isinstance(exc, ProductionShadowTelemetryObserverError)
                else "PSI0B_E9_TELEMETRY_COLLECTION_EXCEPTION"
            )
            collection_exc = exc
        else:
            collection_exc = None
            pids = {name: identities[name]["pid"] for name in REQUIRED_SERVICES}
            if self.expected_pids is not None and pids != self.expected_pids:
                reason = "PSI0B_E9_REQUIRED_SERVICE_PID_DRIFT"
            elif now - float(metrics.get("_snapshot_at", -1)) > self.contract.maximum_checkpoint_age_seconds:
                reason = "PSI0A_F_TELEMETRY_STALE"
            elif primary_fds >= self.contract.primary_fd_warning_threshold_exclusive:
                reason = "PSI0A_F_PRIMARY_FD_WARNING_THRESHOLD_REACHED"
            elif critical < 0 or (self.baseline_critical is not None and critical != self.baseline_critical):
                reason = "PSI0A_F_CRITICAL_LISTENER_DB_HANDLE"
            elif float(metrics.get("p99_wait_ms", -1)) >= self.contract.serializer_p99_threshold_ms_exclusive:
                reason = "PSI0A_F_SERIALIZER_P99_THRESHOLD_REACHED"
            elif self.baseline_lock_errors is not None and int(metrics.get("lock_errors_24h", -1)) != self.baseline_lock_errors:
                reason = "PSI0A_F_SERIALIZER_LOCK_ERROR_INCREMENT"
            elif lease_present:
                reason = "PSI0B_E9_AUTHORITATIVE_WRITE_LEASE_PRESENT"
            elif not wal_ok:
                reason = "PSI0A_F_DATABASE_WAL_UNHEALTHY_OR_UNKNOWN"
            elif any(feeds.get(name) != HEALTHY for name in ("pumpportal", "pumpswap", "ingestion")):
                reason = "PSI0A_F_FEED_OR_INGESTION_UNHEALTHY_OR_UNKNOWN"

        self.checkpoint_attempts += 1
        if recorder is not None:
            recorder.record_checkpoint_attempt(
                checkpoint_sequence=self.checkpoint_attempts,
                phase=phase,
                query_id=query_id,
                observed_at_epoch=now,
                supervisor_service_identities=identities,
                primary_fd_count=primary_fds,
                serializer_snapshot_digest=_digest(metrics),
                serializer_lock_error_baseline=int(metrics.get("lock_errors_24h", -1)) if self.baseline_lock_errors is None else self.baseline_lock_errors,
                serializer_queue_depth=int(metrics.get("queue_depth", -1)),
                authoritative_write_lease_state="PRESENT" if lease_present else "ABSENT",
                release_pending_metadata_digest=release_digest,
                release_pending_metadata_components=release_pending or (),
                database_wal_metadata_digest=wal_digest,
                database_wal_metadata_components=wal_components or (),
                database_wal_state=HEALTHY if wal_ok else "UNKNOWN",
                pumpportal_state=feeds.get("pumpportal", "UNKNOWN"),
                pumpswap_state=feeds.get("pumpswap", "UNKNOWN"),
                ingestion_state=feeds.get("ingestion", "UNKNOWN"),
                gate_reason_code=reason,
            )
        if reason is not None:
            raise ProductionShadowTelemetryObserverError(reason) from collection_exc

        pids = {name: identities[name]["pid"] for name in REQUIRED_SERVICES}
        if self.expected_pids is None: self.expected_pids = pids
        if self.baseline_lock_errors is None: self.baseline_lock_errors = int(metrics["lock_errors_24h"])
        if self.baseline_critical is None: self.baseline_critical = critical
        if self.baseline_release_pending_digest is None: self.baseline_release_pending_digest = release_digest
        checkpoint = build_health_checkpoint(
            observed_at_epoch=now,
            listener_pid=int(pids["watchtower_listener"]), listener_service_state=RUNNING,
            primary_fd_count=primary_fds,
            critical_listener_db_handle_count=max(0, critical - self.baseline_critical),
            serializer_p99_wait_ms=float(metrics["p99_wait_ms"]),
            serializer_lock_errors=int(metrics["lock_errors_24h"]),
            serializer_queue_depth=int(metrics["queue_depth"]),
            database_wal_state=HEALTHY, write_lease_state=HEALTHY,
            pumpportal_state=HEALTHY, pumpswap_state=HEALTHY, ingestion_state=HEALTHY,
            worker_state=HEALTHY, queue_state=HEALTHY, service_state=HEALTHY,
            telemetry_complete=True,
        )
        return checkpoint

    def prestart(self, recorder: ObserverAttemptRecorder) -> HealthGateDecision:
        self.recorder = recorder
        rows = []
        for position in range(3):
            if position: self.dependencies.sleep(30.0)
            rows.append(self.checkpoint(recorder, phase="PRESTART"))
        decision = evaluate_prestart_health_gate(
            self.contract, tuple(rows), now_epoch=float(self.dependencies.clock()),
            baseline_lock_errors=int(self.baseline_lock_errors),
        )
        self.previous_checkpoint = rows[-1]
        return decision

    def active(self, query_id: str) -> HealthGateDecision:
        try:
            current = self.checkpoint(self.recorder, phase="ACTIVE", query_id=query_id)
        except ProductionShadowTelemetryObserverError as exc:
            if self.recorder is not None:
                reason_codes = (str(exc),)
                self.recorder.record_decision(
                    status="STOP",
                    decision_digest=_digest({
                        "phase": "ACTIVE", "query_id": query_id,
                        "status": "STOP", "reason_codes": reason_codes,
                    }),
                    reason_codes=reason_codes, phase="ACTIVE", query_id=query_id,
                )
            raise
        decision = evaluate_active_health_gate(
            self.contract, self.previous_checkpoint, current,
            now_epoch=float(self.dependencies.clock()),
            expected_listener_pid=int(self.expected_pids["watchtower_listener"]),
            baseline_lock_errors=int(self.baseline_lock_errors),
        )
        self.previous_checkpoint = current
        if self.recorder is not None:
            self.recorder.record_decision(
                status=decision.status, decision_digest=decision.decision_digest,
                reason_codes=decision.reason_codes, phase="ACTIVE", query_id=query_id,
            )
        return decision


def production_telemetry_dependencies(root: Path) -> TelemetryDependencies:
    """Build metadata/log/process-only dependencies; no SQLite connection is opened."""
    root = Path(root).resolve()
    supervisor = "/Users/kevinkeaveney/anaconda3/envs/algotrader/bin/supervisorctl"
    supervisor_config = root / "config/supervisor/supervisord.conf"
    metrics_path = root / "logs/db_serializer_metrics.json"
    listener_log = root / "logs/supervisor/listener.log"
    main_db = root / "database/flex_complete_database.db"
    ops_db = root / "database/wt_ops_v2.db"

    def supervisor_status() -> SupervisorResult:
        result = subprocess.run(
            [supervisor, "-c", str(supervisor_config), "status"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        return SupervisorResult(result.returncode, result.stdout, result.stderr)

    def primary_fd_count(pid: int) -> int:
        result = subprocess.run(["lsof", "-nP", "-p", str(pid)], capture_output=True, text=True, timeout=5, check=False)
        if result.returncode not in (0, 1):
            raise ProductionShadowTelemetryObserverError("PSI0B_E9_DESCRIPTOR_PROBE_FAILED")
        return sum(str(main_db) in line for line in result.stdout.splitlines())

    def tail() -> str:
        with listener_log.open("rb") as handle:
            size = handle.seek(0, os.SEEK_END); handle.seek(max(0, size - 2_000_000))
            return handle.read().decode(errors="replace")

    def release_pending_metadata():
        return tuple(sorted(
            (str(path), path.stat().st_mtime_ns, path.stat().st_size)
            for path in (root / "database").glob("*.write.lock.owner.*.tmp")
        ))

    def lease_present() -> bool:
        return any(path.exists() for path in (
            root / "flex_complete_database.db.write.lock.owner",
            root / "database/flex_complete_database.db.write.lock.owner",
            root / "database/wt_ops_v2.db.write.lock.owner",
        ))

    def filesystem_component(database_id: str, database_path: Path) -> dict[str, object]:
        def metadata(path: Path, *, absent_allowed: bool) -> tuple[bool, str, int | None, int | None, int | None]:
            try:
                observed = path.lstat()
            except FileNotFoundError:
                if absent_allowed:
                    return False, "ABSENT", None, None, None
                return False, "ABSENT", None, None, None
            kind = "REGULAR" if stat.S_ISREG(observed.st_mode) else "SYMLINK" if stat.S_ISLNK(observed.st_mode) else "NONREGULAR"
            return True, kind, observed.st_ino, observed.st_size, observed.st_mtime_ns

        wal_path = database_path.with_name(database_path.name + "-wal")
        db_exists, db_type, db_inode, db_size, db_mtime = metadata(database_path, absent_allowed=False)
        wal_exists, wal_type, wal_inode, wal_size, wal_mtime = metadata(wal_path, absent_allowed=True)
        return {
            "database_id": database_id, "database_path": str(database_path),
            "database_exists": db_exists, "database_type": db_type,
            "database_inode": db_inode, "database_size": db_size, "database_mtime_ns": db_mtime,
            "wal_path": str(wal_path), "wal_exists": wal_exists, "wal_type": wal_type,
            "wal_inode": wal_inode, "wal_size": wal_size, "wal_mtime_ns": wal_mtime,
        }

    def database_wal_metadata():
        return tuple(filesystem_component(database_id, path) for database_id, path in (
            ("main", main_db), ("ops", ops_db),
        ))

    def feed_states():
        content = tail(); fresh = time.time() - listener_log.stat().st_mtime <= 45.0
        return {
            "pumpportal": HEALTHY if fresh and "[PUMPPORTAL]" in content else "UNKNOWN",
            "pumpswap": HEALTHY if "[WEBSOCKET][PUMPSWAP] ✓ Connected" in content and "[WEBSOCKET][PUMPSWAP] ✓ Subscription confirmed" in content else "UNKNOWN",
            "ingestion": HEALTHY if fresh and "[PUMPPORTAL]" in content else "UNKNOWN",
        }

    return TelemetryDependencies(
        supervisor_status=supervisor_status,
        serializer_snapshot=lambda: json.loads(metrics_path.read_text()),
        primary_fd_count=primary_fd_count,
        critical_listener_count=lambda: tail().count("CRITICAL_LISTENER_DB_HANDLE"),
        authoritative_write_lease_present=lease_present,
        release_pending_metadata=release_pending_metadata,
        database_wal_metadata=database_wal_metadata,
        feed_states=feed_states,
    )
