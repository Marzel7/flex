"""Fail-closed isolation checks for DEV Supervisor configuration work.

This module deliberately does *not* invoke ``supervisorctl``. It validates
that an offline configuration is confined to an explicitly supplied temporary
directory before a caller may use it for syntax/config inspection.
"""
from __future__ import annotations

import configparser
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path


class SupervisorIsolationError(RuntimeError):
    """Raised before a configuration can be used for DEV validation."""


@dataclass(frozen=True)
class IsolatedSupervisorConfig:
    config_path: Path
    temp_root: Path
    server_socket: Path
    pidfile: Path
    control_socket: Path


_INTERPOLATION = re.compile(r"%\(([^)]+)\)s")


def _resolved(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _value(parser: configparser.RawConfigParser, section: str, option: str) -> str:
    if not parser.has_option(section, option):
        raise SupervisorIsolationError(f"SUPERVISOR_ISOLATION_MISSING_{section}_{option}")
    value = parser.get(section, option).strip()
    if not value:
        raise SupervisorIsolationError(f"SUPERVISOR_ISOLATION_EMPTY_{section}_{option}")
    return value


def _expand_here(value: str, config_path: Path) -> Path:
    names = set(_INTERPOLATION.findall(value))
    if names - {"here"}:
        raise SupervisorIsolationError("SUPERVISOR_ISOLATION_UNVERIFIABLE_INTERPOLATION")
    expanded = value.replace("%(here)s", str(config_path.parent))
    if "%" in expanded or "$" in expanded:
        raise SupervisorIsolationError("SUPERVISOR_ISOLATION_ENVIRONMENT_FALLBACK_REJECTED")
    path = Path(expanded)
    if not path.is_absolute():
        raise SupervisorIsolationError("SUPERVISOR_ISOLATION_RELATIVE_ENDPOINT_REJECTED")
    return _resolved(path)


def _unix_socket_from_url(value: str, config_path: Path) -> Path:
    if not value.startswith("unix://") or any(marker in value for marker in ("?", "#")):
        raise SupervisorIsolationError("SUPERVISOR_ISOLATION_CONTROL_URL_REJECTED")
    # Supervisor accepts ``unix://%(here)s/socket``. Parse after expansion so
    # the interpolation does not look like a URL hostname to urllib.
    path_text = value[len("unix://"):]
    if not path_text:
        raise SupervisorIsolationError("SUPERVISOR_ISOLATION_CONTROL_URL_REJECTED")
    return _expand_here(path_text, config_path)


def _reject_protected(candidate: Path, protected: tuple[Path, ...]) -> None:
    for item in protected:
        protected_path = _resolved(item)
        if candidate == protected_path:
            raise SupervisorIsolationError("SUPERVISOR_ISOLATION_PROTECTED_ENDPOINT_REJECTED")
        try:
            if candidate.exists() and protected_path.exists() and os.path.samefile(candidate, protected_path):
                raise SupervisorIsolationError("SUPERVISOR_ISOLATION_PROTECTED_ENDPOINT_REJECTED")
        except OSError:
            raise SupervisorIsolationError("SUPERVISOR_ISOLATION_ENDPOINT_IDENTITY_UNVERIFIABLE")


def validate_offline_config(config_path: Path | None, *, temp_root: Path,
                            protected_config_paths: tuple[Path, ...] = (),
                            protected_endpoint_paths: tuple[Path, ...] = ()) -> IsolatedSupervisorConfig:
    """Validate a flattened temporary config without opening any socket."""
    if config_path is None:
        raise SupervisorIsolationError("SUPERVISOR_ISOLATION_EXPLICIT_CONFIG_REQUIRED")
    root = _resolved(temp_root)
    config = _resolved(config_path)
    if not _inside(config, root):
        raise SupervisorIsolationError("SUPERVISOR_ISOLATION_CONFIG_OUTSIDE_TEMP_ROOT")
    _reject_protected(config, protected_config_paths)
    if not config.is_file():
        raise SupervisorIsolationError("SUPERVISOR_ISOLATION_CONFIG_UNAVAILABLE")
    parser = configparser.RawConfigParser(interpolation=None, strict=True)
    try:
        parser.read_string(config.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, configparser.Error) as exc:
        raise SupervisorIsolationError("SUPERVISOR_ISOLATION_CONFIG_PARSE_FAILED") from exc
    if parser.has_section("include"):
        raise SupervisorIsolationError("SUPERVISOR_ISOLATION_INCLUDE_REJECTED")
    for required in ("supervisord", "unix_http_server", "supervisorctl"):
        if not parser.has_section(required):
            raise SupervisorIsolationError(f"SUPERVISOR_ISOLATION_MISSING_{required}")
    server_socket = _expand_here(_value(parser, "unix_http_server", "file"), config)
    pidfile = _expand_here(_value(parser, "supervisord", "pidfile"), config)
    control_socket = _unix_socket_from_url(_value(parser, "supervisorctl", "serverurl"), config)
    for endpoint in (server_socket, pidfile, control_socket):
        if not _inside(endpoint, root):
            raise SupervisorIsolationError("SUPERVISOR_ISOLATION_ENDPOINT_OUTSIDE_TEMP_ROOT")
        _reject_protected(endpoint, protected_endpoint_paths)
    if server_socket != control_socket:
        raise SupervisorIsolationError("SUPERVISOR_ISOLATION_SERVER_CONTROL_MISMATCH")
    if pidfile == server_socket:
        raise SupervisorIsolationError("SUPERVISOR_ISOLATION_ENDPOINT_COLLISION")
    return IsolatedSupervisorConfig(config, root, server_socket, pidfile, control_socket)


def validate_safe_daemon_config(config_path: Path | None, *, temp_root: Path,
                                protected_config_paths: tuple[Path, ...] = (),
                                protected_endpoint_paths: tuple[Path, ...] = ()) -> IsolatedSupervisorConfig:
    """Reject a copied production config before any isolated daemon is created.

    Parser-only validation should use :func:`validate_offline_config`.  This
    stricter entry point is mandatory when a test genuinely needs an isolated
    ``supervisord`` process: every program must be a disabled ``/bin/true``
    probe, so a copied config cannot autostart real services, bind a live port,
    open a database, or receive provider credentials.
    """
    isolated = validate_offline_config(
        config_path,
        temp_root=temp_root,
        protected_config_paths=protected_config_paths,
        protected_endpoint_paths=protected_endpoint_paths,
    )
    parser = configparser.RawConfigParser(interpolation=None, strict=True)
    parser.read_string(isolated.config_path.read_text(encoding="utf-8"))
    for section in parser.sections():
        if not section.startswith("program:"):
            continue
        autostart = parser.get(section, "autostart", fallback="").strip().lower()
        autorestart = parser.get(section, "autorestart", fallback="").strip().lower()
        command = parser.get(section, "command", fallback="").strip()
        environment = parser.get(section, "environment", fallback="").upper()
        if autostart != "false" or autorestart != "false":
            raise SupervisorIsolationError("SUPERVISOR_ISOLATION_DAEMON_AUTOSTART_REJECTED")
        if command != "/bin/true":
            raise SupervisorIsolationError("SUPERVISOR_ISOLATION_DAEMON_COMMAND_REJECTED")
        if any(name in environment for name in ("BIRDEYE", "HELIUS", "DB_PATH", "DATABASE")):
            raise SupervisorIsolationError("SUPERVISOR_ISOLATION_DAEMON_ENVIRONMENT_REJECTED")
    return isolated


def require_verified_control_socket(config: IsolatedSupervisorConfig) -> None:
    """Verify a temporary UNIX socket before a caller constructs an argv."""
    socket_path = config.control_socket
    if not socket_path.exists():
        raise SupervisorIsolationError("SUPERVISOR_ISOLATION_CONTROL_SOCKET_UNAVAILABLE")
    try:
        mode = socket_path.stat().st_mode
    except OSError as exc:
        raise SupervisorIsolationError("SUPERVISOR_ISOLATION_ENDPOINT_IDENTITY_UNVERIFIABLE") from exc
    if not stat.S_ISSOCK(mode):
        raise SupervisorIsolationError("SUPERVISOR_ISOLATION_CONTROL_SOCKET_NOT_UNIX")


def isolated_supervisorctl_argv(config: IsolatedSupervisorConfig, *args: str) -> tuple[str, ...]:
    """Return a validated argv only; this module never executes it."""
    require_verified_control_socket(config)
    return ("supervisorctl", "-c", str(config.config_path), *args)
