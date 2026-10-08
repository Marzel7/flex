from pathlib import Path
from types import SimpleNamespace
import stat

import pytest

from src.utils.supervisor_isolation import (
    SupervisorIsolationError,
    isolated_supervisorctl_argv,
    validate_offline_config,
)


def _config(root: Path, *, server="%(here)s/server.sock", control="unix://%(here)s/server.sock") -> Path:
    path = root / "isolated.conf"
    path.write_text(
        "[unix_http_server]\nfile=" + server + "\n\n"
        "[supervisord]\npidfile=%(here)s/supervisord.pid\n\n"
        "[supervisorctl]\nserverurl=" + control + "\n"
    )
    return path


def _policy(tmp_path: Path, live_root: Path):
    return {"temp_root": tmp_path, "protected_config_paths": (live_root / "supervisord.conf",),
            "protected_endpoint_paths": (live_root / "supervisor.sock",)}


def test_inherited_live_control_endpoint_is_blocked(tmp_path):
    live = tmp_path / "live"; live.mkdir()
    config = _config(tmp_path, control="unix://" + str(live / "supervisor.sock"))
    with pytest.raises(SupervisorIsolationError, match="PROTECTED|OUTSIDE"):
        validate_offline_config(config, **_policy(tmp_path, live))


def test_both_isolated_endpoints_allow_offline_validation(tmp_path):
    live = tmp_path / "live"; live.mkdir()
    validated = validate_offline_config(_config(tmp_path), **_policy(tmp_path, live))
    assert validated.server_socket == validated.control_socket
    assert validated.server_socket.parent == tmp_path.resolve()


def test_missing_control_endpoint_is_blocked(tmp_path):
    live = tmp_path / "live"; live.mkdir()
    with pytest.raises(SupervisorIsolationError, match="EMPTY"):
        validate_offline_config(_config(tmp_path, control=""), **_policy(tmp_path, live))


def test_default_config_fallback_and_uninspected_include_are_blocked(tmp_path):
    live = tmp_path / "live"; live.mkdir()
    with pytest.raises(SupervisorIsolationError, match="EXPLICIT_CONFIG"):
        validate_offline_config(None, **_policy(tmp_path, live))
    config = _config(tmp_path)
    config.write_text(config.read_text() + "\n[include]\nfiles=../live/*.conf\n")
    with pytest.raises(SupervisorIsolationError, match="INCLUDE"):
        validate_offline_config(config, **_policy(tmp_path, live))


def test_production_config_path_and_relative_endpoint_are_blocked(tmp_path):
    live = tmp_path / "live"; live.mkdir()
    production = live / "supervisord.conf"
    _config(live).rename(production)
    with pytest.raises(SupervisorIsolationError, match="OUTSIDE|PROTECTED"):
        validate_offline_config(production, **_policy(tmp_path, live))
    with pytest.raises(SupervisorIsolationError, match="RELATIVE"):
        validate_offline_config(_config(tmp_path, server="server.sock"), **_policy(tmp_path, live))


def test_symlink_alias_and_environment_fallback_are_blocked(tmp_path):
    live = tmp_path / "live"; live.mkdir()
    protected = live / "supervisor.sock"; protected.touch()
    alias = tmp_path / "alias.sock"; alias.symlink_to(protected)
    with pytest.raises(SupervisorIsolationError, match="PROTECTED"):
        validate_offline_config(_config(tmp_path, server=str(alias), control="unix://" + str(alias)), **_policy(tmp_path, live))
    with pytest.raises(SupervisorIsolationError, match="ENVIRONMENT"):
        validate_offline_config(_config(tmp_path, server="$SUPERVISOR_SOCKET"), **_policy(tmp_path, live))


def test_unavailable_or_non_socket_control_endpoint_never_yields_argv(tmp_path):
    live = tmp_path / "live"; live.mkdir()
    config = validate_offline_config(_config(tmp_path), **_policy(tmp_path, live))
    with pytest.raises(SupervisorIsolationError, match="UNAVAILABLE"):
        isolated_supervisorctl_argv(config, "status")
    config.control_socket.touch()
    with pytest.raises(SupervisorIsolationError, match="NOT_UNIX"):
        isolated_supervisorctl_argv(config, "status")


def test_verified_temporary_socket_only_constructs_isolated_argv(tmp_path, monkeypatch):
    live = tmp_path / "live"; live.mkdir()
    config = validate_offline_config(_config(tmp_path), **_policy(tmp_path, live))
    monkeypatch.setattr(Path, "exists", lambda self: self == config.control_socket)
    monkeypatch.setattr(Path, "stat", lambda self: SimpleNamespace(st_mode=stat.S_IFSOCK))
    argv = isolated_supervisorctl_argv(config, "status")
    assert argv[0:3] == ("supervisorctl", "-c", str(config.config_path))
    assert argv[-1] == "status"
