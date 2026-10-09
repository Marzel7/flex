"""Read-only, fail-closed operational gate for DEV-014 Batch 4.

The gate deliberately derives the API endpoint, canonical database, and
listener log from a caller-supplied installed Supervisor configuration.  It
does not open SQLite, request a checkpoint, mutate a queue, or create a
background worker.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


WAL_LIMIT_BYTES = 500 * 1024 * 1024
DISK_MINIMUM_BYTES = 4 * 1024 * 1024 * 1024
HEARTBEAT_MAX_SECONDS = 120
_CRITICAL_MARKERS = ("CRITICAL_WAL_PINNED", "CrossProcessDatabaseWriteTimeout", "ApplicationWriteLockTimeout")


class RuntimeGateDenied(RuntimeError):
    """The next paid request is not safe to admit."""


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    ppid: int
    started_at: str
    argv: tuple[str, ...]


@dataclass(frozen=True)
class RuntimeAuthority:
    supervisor_config: Path
    api_url: str
    canonical_db: Path
    listener_log: Path
    creator_logs: tuple[Path, ...]


_PS_RECORD = re.compile(r"^\s*(\d+)\s+(\d+)\s+([A-Z][a-z]{2}\s+[A-Z][a-z]{2}\s+\d+\s+\d{2}:\d{2}:\d{2}\s+\d{4})\s+(.+)$")


def _parse_process_line(line: str) -> ProcessIdentity:
    match = _PS_RECORD.match(line)
    if not match:
        raise RuntimeGateDenied("PROCESS_IDENTITY_AMBIGUOUS")
    try:
        argv = tuple(shlex.split(match.group(4)))
    except ValueError as exc:
        raise RuntimeGateDenied("PROCESS_ARGUMENTS_AMBIGUOUS") from exc
    if not argv:
        raise RuntimeGateDenied("PROCESS_EXECUTABLE_UNPROVEN")
    return ProcessIdentity(int(match.group(1)), int(match.group(2)), match.group(3), argv)


def _section(config: str, name: str) -> str:
    match = re.search(rf"^\[program:{re.escape(name)}\]\s*$([\s\S]*?)(?=^\[|\Z)", config, re.MULTILINE)
    if not match:
        raise RuntimeGateDenied(f"SUPERVISOR_STANZA_MISSING:{name}")
    return match.group(1)


def _setting(section: str, name: str) -> str:
    match = re.search(rf"^{re.escape(name)}\s*=\s*(.+)$", section, re.MULTILINE)
    if not match:
        raise RuntimeGateDenied(f"SUPERVISOR_SETTING_MISSING:{name}")
    return match.group(1).strip()


def _environment_value(section: str, name: str) -> str:
    environment = _setting(section, "environment")
    match = re.search(rf'(?:^|,){re.escape(name)}="([^"]*)"', environment)
    if not match or not match.group(1):
        raise RuntimeGateDenied(f"SUPERVISOR_ENV_MISSING:{name}")
    return match.group(1)


def resolve_runtime_authority(supervisor_config: str | Path) -> RuntimeAuthority:
    """Resolve only declared installed bindings; no port fallback is allowed."""
    config_path = Path(supervisor_config)
    try:
        config = config_path.read_text()
    except OSError as exc:
        raise RuntimeGateDenied("SUPERVISOR_CONFIG_UNREADABLE") from exc
    api = _section(config, "watchtower_api")
    listener = _section(config, "watchtower_listener")
    funding = _section(config, "creator_funding_worker")
    resolution = _section(config, "creator_resolution_worker")
    api_root = Path(_environment_value(api, "WATCHTOWER_FINAL_ROOT"))
    canonical_db = Path(_environment_value(api, "DB_PATH"))
    try:
        gunicorn = (api_root / "config" / "gunicorn.conf.py").read_text()
    except OSError as exc:
        raise RuntimeGateDenied("API_BINDING_CONFIG_UNREADABLE") from exc
    binding = re.search(r"^bind\s*=\s*['\"]([^'\"]+)['\"]", gunicorn, re.MULTILINE)
    if not binding:
        raise RuntimeGateDenied("API_BINDING_UNDECLARED")
    host, separator, port = binding.group(1).rpartition(":")
    if not separator or not port.isdecimal() or not 1 <= int(port) <= 65535:
        raise RuntimeGateDenied("API_BINDING_INVALID")
    # A wildcard bind is verified through loopback; it is still the same local
    # Supervisor-owned Gunicorn endpoint, not a guessed alternate port.
    if host == "0.0.0.0":
        host = "127.0.0.1"
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeGateDenied("API_BINDING_NOT_LOCAL")
    return RuntimeAuthority(
        supervisor_config=config_path,
        api_url=f"http://{host}:{port}/healthz",
        canonical_db=canonical_db,
        listener_log=Path(_setting(listener, "stdout_logfile")),
        creator_logs=(Path(_setting(funding, "stdout_logfile")), Path(_setting(resolution, "stdout_logfile"))),
    )


class Batch4RuntimeGate:
    """Bounded evidence checks run immediately before each budget admission."""

    def __init__(self, authority: RuntimeAuthority, *, health_fetch: Callable[[str], tuple[int, dict[str, Any]]] | None = None,
                 process_lines: Callable[[], list[str]] | None = None,
                 disk_usage: Callable[[str | Path], Any] = shutil.disk_usage,
                 wal_size: Callable[[Path], int] | None = None):
        self.authority = authority
        self._health_fetch = health_fetch or self._fetch_health
        self._process_lines = process_lines or self._read_process_lines
        self._disk_usage = disk_usage
        self._wal_size = wal_size or (lambda path: path.stat().st_size)
        self._critical_offsets = {path: self._size(path) for path in authority.creator_logs}

    @staticmethod
    def _size(path: Path) -> int:
        try:
            return path.stat().st_size
        except OSError as exc:
            raise RuntimeGateDenied("RUNTIME_LOG_UNREADABLE") from exc

    @staticmethod
    def _fetch_health(url: str) -> tuple[int, dict[str, Any]]:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                payload = json.loads(response.read())
                return int(response.status), payload
        except Exception as exc:
            raise RuntimeGateDenied("API_HEALTH_UNAVAILABLE") from exc

    @staticmethod
    def _read_process_lines() -> list[str]:
        try:
            output = subprocess.run(["ps", "-axo", "pid=,ppid=,lstart=,command="], check=True, text=True, capture_output=True).stdout
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeGateDenied("PROCESS_TOPOLOGY_UNAVAILABLE") from exc
        return output.splitlines()

    def _health(self) -> dict[str, Any]:
        status, payload = self._health_fetch(self.authority.api_url)
        workers = payload.get("workers") if isinstance(payload, dict) else None
        if status != 200 or not isinstance(payload, dict) or payload.get("healthy") is not True or payload.get("db") != "ok" or payload.get("wal_warn") is not False:
            raise RuntimeGateDenied("API_HEALTH_DENIED")
        if not isinstance(workers, dict):
            raise RuntimeGateDenied("CREATOR_HEARTBEAT_UNAVAILABLE")
        for name in ("creator-funding", "creator-resolution"):
            item = workers.get(name)
            if not isinstance(item, dict) or item.get("stale") is not False or not isinstance(item.get("age_s"), (int, float)) or item["age_s"] >= HEARTBEAT_MAX_SECONDS:
                raise RuntimeGateDenied(f"CREATOR_HEARTBEAT_STALE:{name}")
        return payload

    def _storage(self) -> dict[str, int]:
        try:
            wal_bytes = int(self._wal_size(self.authority.canonical_db.with_name(self.authority.canonical_db.name + "-wal")))
            free_bytes = int(self._disk_usage(self.authority.canonical_db.parent).free)
        except OSError as exc:
            raise RuntimeGateDenied("WAL_OR_DISK_UNAVAILABLE") from exc
        if wal_bytes >= WAL_LIMIT_BYTES:
            raise RuntimeGateDenied("WAL_SIZE_DENIED")
        if free_bytes <= DISK_MINIMUM_BYTES:
            raise RuntimeGateDenied("DISK_HEADROOM_DENIED")
        return {"wal_bytes": wal_bytes, "disk_free_bytes": free_bytes}

    def _checkpoint(self) -> dict[str, int]:
        try:
            lines = self.authority.listener_log.read_text().splitlines()[-4000:]
        except OSError as exc:
            raise RuntimeGateDenied("CHECKPOINT_TELEMETRY_UNAVAILABLE") from exc
        samples: list[dict[str, Any]] = []
        for line in lines:
            if "[WAL_CHECKPOINT]" not in line:
                continue
            try:
                samples.append(json.loads(line.split("[WAL_CHECKPOINT]", 1)[1].strip()))
            except (IndexError, ValueError):
                continue
        if not samples:
            raise RuntimeGateDenied("CHECKPOINT_TELEMETRY_UNAVAILABLE")
        recent = samples[-3:]
        if any(item.get("status") != "ok" or item.get("busy") != 0 for item in recent):
            raise RuntimeGateDenied("CHECKPOINT_OBSTRUCTION")
        progressed = any(int(item.get("checkpointed_frames") or 0) > 0 for item in recent)
        quiescent = int(recent[-1].get("remaining_frames") or 0) == 0
        if not progressed and not quiescent:
            raise RuntimeGateDenied("CHECKPOINT_PROGRESS_UNPROVEN")
        return {"samples": len(recent), "remaining_frames": int(recent[-1].get("remaining_frames") or 0)}

    def _new_critical_event(self) -> None:
        for path, offset in self._critical_offsets.items():
            current = self._size(path)
            if current < offset:
                raise RuntimeGateDenied("CRITICAL_WAL_LOG_ROTATED")
            if current == offset:
                continue
            try:
                appended = path.read_bytes()[offset:]
            except OSError as exc:
                raise RuntimeGateDenied("RUNTIME_LOG_UNREADABLE") from exc
            if any(marker.encode() in appended for marker in _CRITICAL_MARKERS):
                raise RuntimeGateDenied("NEW_CRITICAL_WAL_EVENT")
            self._critical_offsets[path] = current

    @staticmethod
    def _python_module(record: ProcessIdentity, module: str) -> bool:
        return len(record.argv) >= 3 and Path(record.argv[0]).is_absolute() and Path(record.argv[0]).name.startswith("python") and record.argv[1:3] == ("-m", module)

    @staticmethod
    def _option_value(argv: tuple[str, ...], option: str) -> str | None:
        try:
            index = argv.index(option)
        except ValueError:
            return None
        return argv[index + 1] if index + 1 < len(argv) else None

    def _supervisor_daemon(self, record: ProcessIdentity) -> bool:
        argv = record.argv
        executable = Path(argv[0]).name
        if not Path(argv[0]).is_absolute():
            return False
        is_script = len(argv) >= 2 and Path(argv[0]).name.startswith("python") and Path(argv[1]).name == "supervisord"
        is_module = self._python_module(record, "supervisor.supervisord")
        is_direct = executable == "supervisord"
        if not (is_direct or is_script or is_module):
            return False
        # supervisorctl and parser-only `supervisord -t` are not daemons.
        if executable == "supervisorctl" or "-t" in argv or "--test" in argv:
            return False
        config = self._option_value(argv, "-c") or self._option_value(argv, "--configuration")
        if not config:
            raise RuntimeGateDenied("SUPERVISOR_IDENTITY_AMBIGUOUS")
        try:
            if Path(config).resolve() != self.authority.supervisor_config.resolve():
                raise RuntimeGateDenied("SUPERVISOR_CONFIG_AMBIGUOUS")
        except OSError as exc:
            raise RuntimeGateDenied("SUPERVISOR_CONFIG_AMBIGUOUS") from exc
        return True

    def _topology(self) -> None:
        records = [_parse_process_line(line) for line in self._process_lines()]
        supervisors = [record for record in records if self._supervisor_daemon(record)]
        if len(supervisors) != 1:
            raise RuntimeGateDenied("DUPLICATE_OR_MISSING_RUNTIME:supervisord")
        for module in ("src.core.creator_funding_worker", "src.core.creator_resolution_worker"):
            if sum(self._python_module(record, module) for record in records) != 1:
                raise RuntimeGateDenied(f"DUPLICATE_OR_MISSING_RUNTIME:{module}")
        competing = [record for record in records if "run_watchtower_recent_first_price_forensics_batch_4" in record.argv and record.pid != os.getpid()]
        if competing:
            raise RuntimeGateDenied("COMPETING_BATCH4_RESEARCH")

    def check(self) -> dict[str, Any]:
        health = self._health()
        storage = self._storage()
        checkpoint = self._checkpoint()
        self._new_critical_event()
        self._topology()
        return {"api_url": self.authority.api_url, "health": health, "storage": storage, "checkpoint": checkpoint}
