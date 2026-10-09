"""Byte-preserving in-place Supervisor composition for the Watchtower candidate.

This module is an offline renderer only.  It neither contacts Supervisor nor
starts a process.  It rewrites exactly the API and worker program ranges while
leaving the listener and every unrelated root-config byte intact.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping


TARGETS = (
    "watchtower_api",
    "watchtower_listener",
    "operation_monitor_worker",
    "operation_monitor_bridge",
)
MONITOR_TOPOLOGY_TARGETS = ("operation_monitor_worker", "operation_monitor_bridge")


@dataclass(frozen=True)
class RuntimePaths:
    root: str
    sha: str
    python: str
    canonical_db: str
    api_operations_db: str
    worker_operations_db: str
    api_queue: str
    worker_queue: str
    api_monitor_ui_db: str
    monitor_state_root: str
    monitor_env_file: str
    audit_ledger: str
    shadow_ledger: str
    api_stdout_log: str
    api_stderr_log: str
    worker_stdout_log: str
    worker_stderr_log: str
    canonical_birth_db: str = ""
    bridge_source_db: str = ""
    bridge_health_path: str = ""
    bridge_stdout_log: str = ""
    bridge_stderr_log: str = ""
    monitor_selection: str = ""
    monitor_max_iterations: str = ""
    monitor_provider_global_limit: str = ""
    monitor_provider_token_limit: str = ""
    bridge_max_iterations: str = ""


def compose(source: bytes, paths: RuntimePaths) -> bytes:
    """Return a complete candidate config with only API/worker ranges changed."""
    text = source.decode("utf-8")
    if re.search(r"^\[include\]", text, re.MULTILINE):
        raise ValueError("SUPERVISOR_INCLUDE_UNEXPECTED")
    ranges = _program_ranges(text)
    if any(len(ranges.get(name, ())) != 1 for name in TARGETS):
        raise ValueError("SUPERVISOR_TARGET_CARDINALITY_INVALID")
    replacements = {}
    for name, stanza in (
        ("watchtower_api", _api_stanza(paths)),
        ("operation_monitor_worker", _worker_stanza(paths)),
        ("operation_monitor_bridge", _bridge_stanza(paths)),
    ):
        start, end = ranges[name][0]
        # The final line's newline is target-owned; blank/comment separators
        # following it are source-owned.  This also preserves final-EOF newline
        # semantics and makes a second composition byte-identical.
        replacements[name] = stanza.rstrip("\n") + ("\n" if text[start:end].endswith("\n") else "")
    rendered = text
    for name in sorted(replacements, key=lambda item: ranges[item][0][0], reverse=True):
        start, end = ranges[name][0]
        rendered = rendered[:start] + replacements[name] + rendered[end:]
    return rendered.encode("utf-8")


def compose_api_only(source: bytes, paths: RuntimePaths) -> bytes:
    """Return a candidate replacing only the API stanza.

    This narrower transition is for a root configuration with no
    operation-monitor program. It requires exactly one API and listener
    definition and never materializes a worker stanza.
    """
    text = source.decode("utf-8")
    if re.search(r"^\[include\]", text, re.MULTILINE):
        raise ValueError("SUPERVISOR_INCLUDE_UNEXPECTED")
    ranges = _program_ranges(text)
    if any(len(ranges.get(name, ())) != 1 for name in ("watchtower_api", "watchtower_listener")):
        raise ValueError("SUPERVISOR_API_ONLY_TARGET_CARDINALITY_INVALID")
    start, end = ranges["watchtower_api"][0]
    replacement = _api_stanza(paths).rstrip("\n") + ("\n" if text[start:end].endswith("\n") else "")
    return (text[:start] + replacement + text[end:]).encode("utf-8")


def compose_monitor_only(source: bytes, paths: RuntimePaths) -> bytes:
    """Replace only the existing monitor worker and bridge program ranges.

    This is the API-preserving finite-soak composition.  It intentionally
    leaves the API, listener, and all unrelated program bytes untouched.
    """
    text = source.decode("utf-8")
    if re.search(r"^\[include\]", text, re.MULTILINE):
        raise ValueError("SUPERVISOR_INCLUDE_UNEXPECTED")
    ranges = _program_ranges(text)
    if any(len(ranges.get(name, ())) != 1 for name in (
        "watchtower_api", "watchtower_listener", *MONITOR_TOPOLOGY_TARGETS,
    )):
        raise ValueError("SUPERVISOR_MONITOR_ONLY_TARGET_CARDINALITY_INVALID")
    rendered = text
    for name, stanza in reversed((
        ("operation_monitor_worker", _worker_stanza(paths)),
        ("operation_monitor_bridge", _bridge_stanza(paths)),
    )):
        start, end = ranges[name][0]
        replacement = stanza.rstrip("\n") + ("\n" if text[start:end].endswith("\n") else "")
        rendered = rendered[:start] + replacement + rendered[end:]
    return rendered.encode("utf-8")


def compose_api_and_worker(source: bytes, paths: RuntimePaths) -> bytes:
    """Replace only the API and monitor-worker ranges.

    The bridge remains byte-identical: Policy C changes its downstream worker
    contract and API projection, not the sole outbox/queue producer.
    """
    text = source.decode("utf-8")
    if re.search(r"^\[include\]", text, re.MULTILINE):
        raise ValueError("SUPERVISOR_INCLUDE_UNEXPECTED")
    ranges = _program_ranges(text)
    if any(len(ranges.get(name, ())) != 1 for name in (
        "watchtower_api", "watchtower_listener", "operation_monitor_worker", "operation_monitor_bridge",
    )):
        raise ValueError("SUPERVISOR_API_AND_WORKER_TARGET_CARDINALITY_INVALID")
    replacements = (("watchtower_api", _api_stanza(paths)), ("operation_monitor_worker", _worker_stanza(paths)))
    rendered = text
    for name, stanza in reversed(replacements):
        start, end = ranges[name][0]
        replacement = stanza.rstrip("\n") + ("\n" if text[start:end].endswith("\n") else "")
        rendered = rendered[:start] + replacement + rendered[end:]
    return rendered.encode("utf-8")


def compose_monitoring_topology(source: bytes, paths: RuntimePaths) -> bytes:
    """Append the qualified disabled monitor topology to a current root config.

    This is deliberately a preparation-only renderer: it replaces the API
    stanza with the already-qualified candidate contract, preserves every
    existing program, and adds the previously qualified worker and bridge only
    when both are absent.  It never enables either process.
    """
    text = source.decode("utf-8")
    if re.search(r"^\[include\]", text, re.MULTILINE):
        raise ValueError("SUPERVISOR_INCLUDE_UNEXPECTED")
    ranges = _program_ranges(text)
    if any(len(ranges.get(name, ())) != 1 for name in ("watchtower_api", "watchtower_listener")):
        raise ValueError("SUPERVISOR_MONITOR_TOPOLOGY_API_LISTENER_CARDINALITY_INVALID")
    topology_counts = tuple(len(ranges.get(name, ())) for name in MONITOR_TOPOLOGY_TARGETS)
    if topology_counts == (1, 1):
        # A second render remains byte-stable: ``compose`` owns the API and
        # worker contracts; the bridge already has its unique qualified range.
        return compose(source, paths)
    if topology_counts != (0, 0):
        raise ValueError("SUPERVISOR_MONITOR_TOPOLOGY_ALREADY_PRESENT_OR_PARTIAL")
    if not paths.bridge_source_db or not paths.bridge_health_path:
        raise ValueError("SUPERVISOR_MONITOR_TOPOLOGY_BRIDGE_PATHS_REQUIRED")
    start, end = ranges["watchtower_api"][0]
    api = _api_stanza(paths).rstrip("\n") + ("\n" if text[start:end].endswith("\n") else "")
    rendered = text[:start] + api + text[end:]
    separator = "" if rendered.endswith("\n\n") else "\n"
    return (rendered + separator + _worker_stanza(paths) + _bridge_stanza(paths)).encode("utf-8")


def listener_bytes(source: bytes) -> bytes:
    """Return the exact listener section for parity/rollback checks."""
    text = source.decode("utf-8")
    ranges = _program_ranges(text)
    if len(ranges.get("watchtower_listener", ())) != 1:
        raise ValueError("SUPERVISOR_LISTENER_CARDINALITY_INVALID")
    start, end = ranges["watchtower_listener"][0]
    return source[start:end]


def _program_ranges(text: str) -> dict[str, list[tuple[int, int]]]:
    headers = list(re.finditer(r"^\[program:([^\]]+)\][^\n]*(?:\n|$)", text, re.MULTILINE))
    ranges: dict[str, list[tuple[int, int]]] = {}
    for index, header in enumerate(headers):
        raw_end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        # A program owns its header and option/body lines, not the blank/comment
        # separator introducing the following program.  Retaining that trailer
        # preserves all unrelated presentation bytes in a two-program splice.
        body = text[header.start() : raw_end]
        lines = body.splitlines(keepends=True)
        while len(lines) > 1 and (
            not lines[-1].strip()
            or lines[-1].lstrip().startswith((";", "#"))
        ):
            lines.pop()
        end = header.start() + len("".join(lines))
        ranges.setdefault(header.group(1), []).append((header.start(), end))
    return ranges


def _api_stanza(p: RuntimePaths) -> str:
    return f'''[program:watchtower_api]
command={p.root}/scripts/launch_watchtower_final.sh api {p.sha}
directory={p.root}
environment=PYTHONPATH="{p.root}",WATCHTOWER_FINAL_ROOT="{p.root}",WATCHTOWER_FINAL_SHA="{p.sha}",WATCHTOWER_OFFSET_AUDIT_LEDGER_PATH="{p.audit_ledger}",DB_PATH="{p.canonical_db}",FLEX_DB_PATH="{p.canonical_db}",WT_OPS_DB_PATH="{p.api_operations_db}",OPS_V2_DB_PATH="{p.api_operations_db}",WATCHTOWER_MONITOR_UI_DB_PATH="{p.api_monitor_ui_db}",OPERATION_MONITOR_QUEUE_PATH="{p.api_queue}",WATCHTOWER_MONITOR_QUEUE_PATH="{p.worker_queue}",FLEX_WS_DISABLED="1",FLEX_UI_RECOVERY_MODE="0",DB_WRITE_SERIALIZE="1",FLEX_ENABLE_FLASK_BACKGROUND_WORKERS="0",WATCHTOWER_SHADOW_EVALUATION_ENABLED="0",WATCHTOWER_SHADOW_CAPTURE_ENABLED="0",WATCHTOWER_SHADOW_CAPTURE_LEDGER_PATH="{p.shadow_ledger}"
autostart=true
autorestart=true
startretries=999
startsecs=8
stopwaitsecs=15
stopasgroup=true
killasgroup=true
stdout_logfile={p.api_stdout_log}
stdout_logfile_maxbytes=20MB
stdout_logfile_backups=3
stderr_logfile={p.api_stderr_log}
stderr_logfile_maxbytes=10MB
stderr_logfile_backups=2

'''


def _worker_stanza(p: RuntimePaths) -> str:
    # A finite soak deliberately exits after one or more bounded iterations.
    # Supervisor must accept a clean immediate exit in that explicitly opted-in
    # mode; the normal continuously-running worker keeps its startup contract.
    lifecycle = _finite_iteration_lifecycle(p.monitor_max_iterations)
    bounded = "".join(
        f',{name}="{value}"' for name, value in (
            ('MONITOR_MAX_ITERATIONS', p.monitor_max_iterations),
            ('MONITOR_PROVIDER_GLOBAL_LIMIT', p.monitor_provider_global_limit),
            ('MONITOR_PROVIDER_TOKEN_LIMIT', p.monitor_provider_token_limit),
        ) if value
    )
    canonical_birth = (f',OPERATION_MONITOR_CANONICAL_BIRTH_DB_PATH="{p.canonical_birth_db}"'
                       if p.canonical_birth_db else "")
    return f'''[program:operation_monitor_worker]
command={p.root}/scripts/launch_watchtower_final.sh worker {p.sha} {p.monitor_selection}
directory={p.root}
environment=PYTHONPATH="{p.root}",WATCHTOWER_FINAL_ROOT="{p.root}",WATCHTOWER_FINAL_SHA="{p.sha}",WATCHTOWER_OFFSET_AUDIT_LEDGER_PATH="{p.audit_ledger}",MONITOR_RUNTIME_STATE_ROOT="{p.monitor_state_root}",MONITOR_ENV_FILE="{p.monitor_env_file}",DB_PATH="{p.canonical_db}",FLEX_DB_PATH="{p.canonical_db}",WT_OPS_DB_PATH="{p.worker_operations_db}",DATABASE_PATH="{p.worker_operations_db}",OPS_V2_DB_PATH="{p.worker_operations_db}",OPERATION_MONITOR_QUEUE_PATH="{p.worker_queue}",OPERATIONS_MODE="MONITOR",MONITOR_RUNTIME="dev",WATCHTOWER_SHADOW_EVALUATION_ENABLED="0",WATCHTOWER_SHADOW_CAPTURE_ENABLED="0",WATCHTOWER_SHADOW_CAPTURE_LEDGER_PATH="{p.shadow_ledger}"{canonical_birth}{bounded}
autostart=false
autorestart=false
startretries=0
{lifecycle}stopwaitsecs=15
stopsignal=TERM
stdout_logfile={p.worker_stdout_log}
stdout_logfile_maxbytes=5MB
stdout_logfile_backups=2
stderr_logfile={p.worker_stderr_log}
stderr_logfile_maxbytes=2MB
stderr_logfile_backups=1

'''


def _bridge_stanza(p: RuntimePaths) -> str:
    lifecycle = _finite_iteration_lifecycle(p.bridge_max_iterations)
    bounded = "".join(
        f',{name}="{value}"' for name, value in (
            ('MONITOR_BRIDGE_SELECTION_PATH', p.monitor_selection),
            ('MONITOR_BRIDGE_MAX_ITERATIONS', p.bridge_max_iterations),
        ) if value
    )
    return f'''[program:operation_monitor_bridge]
command={p.python} -m src.ops.operation_monitor_bridge_service
directory={p.root}
environment=PYTHONPATH="{p.root}",WATCHTOWER_FINAL_ROOT="{p.root}",WATCHTOWER_FINAL_SHA="{p.sha}",MONITOR_BRIDGE_SOURCE_DB="{p.bridge_source_db}",MONITOR_BRIDGE_MONITOR_DB="{p.worker_operations_db}",MONITOR_BRIDGE_QUEUE_PATH="{p.worker_queue}",MONITOR_BRIDGE_CADENCE_SECONDS="60",MONITOR_BRIDGE_HEALTH_PATH="{p.bridge_health_path}",WATCHTOWER_SHADOW_EVALUATION_ENABLED="0",WATCHTOWER_SHADOW_CAPTURE_ENABLED="0"{bounded}
autostart=false
autorestart=false
startretries=0
{lifecycle}stopwaitsecs=15
stopsignal=TERM
stdout_logfile={p.bridge_stdout_log}
stdout_logfile_maxbytes=5MB
stdout_logfile_backups=2
stderr_logfile={p.bridge_stderr_log}
stderr_logfile_maxbytes=2MB
stderr_logfile_backups=1

'''


def _finite_iteration_lifecycle(iterations: str) -> str:
    """Render a Supervisor lifecycle contract for explicit finite execution."""
    return "startsecs=0\nexitcodes=0\n" if iterations else "startsecs=5\n"
