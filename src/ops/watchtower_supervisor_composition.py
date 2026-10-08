"""Byte-preserving in-place Supervisor composition for the Watchtower candidate.

This module is an offline renderer only.  It neither contacts Supervisor nor
starts a process.  It rewrites exactly the API and worker program ranges while
leaving the listener and every unrelated root-config byte intact.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping


TARGETS = ("watchtower_api", "watchtower_listener", "operation_monitor_worker")


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


def compose(source: bytes, paths: RuntimePaths) -> bytes:
    """Return a complete candidate config with only API/worker ranges changed."""
    text = source.decode("utf-8")
    if re.search(r"^\[include\]", text, re.MULTILINE):
        raise ValueError("SUPERVISOR_INCLUDE_UNEXPECTED")
    ranges = _program_ranges(text)
    if any(len(ranges.get(name, ())) != 1 for name in TARGETS):
        raise ValueError("SUPERVISOR_TARGET_CARDINALITY_INVALID")
    replacements = {}
    for name, stanza in (("watchtower_api", _api_stanza(paths)),
                         ("operation_monitor_worker", _worker_stanza(paths))):
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
command={p.python} -m gunicorn --config {p.root}/config/gunicorn.conf.py src.core.main:app
directory={p.root}
environment=PYTHONPATH="{p.root}",WATCHTOWER_FINAL_ROOT="{p.root}",WATCHTOWER_FINAL_SHA="{p.sha}",WATCHTOWER_OFFSET_AUDIT_LEDGER_PATH="{p.audit_ledger}",DB_PATH="{p.canonical_db}",FLEX_DB_PATH="{p.canonical_db}",WT_OPS_DB_PATH="{p.api_operations_db}",OPS_V2_DB_PATH="{p.api_operations_db}",WATCHTOWER_MONITOR_UI_DB_PATH="{p.api_monitor_ui_db}",OPERATION_MONITOR_QUEUE_PATH="{p.api_queue}",FLEX_WS_DISABLED="1",FLEX_UI_RECOVERY_MODE="0",DB_WRITE_SERIALIZE="1",FLEX_ENABLE_FLASK_BACKGROUND_WORKERS="0",WATCHTOWER_SHADOW_EVALUATION_ENABLED="0",WATCHTOWER_SHADOW_CAPTURE_ENABLED="0",WATCHTOWER_SHADOW_CAPTURE_LEDGER_PATH="{p.shadow_ledger}"
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
    return f'''[program:operation_monitor_worker]
command={p.python} -m src.ops.operation_monitor_service
directory={p.root}
environment=PYTHONPATH="{p.root}",WATCHTOWER_FINAL_ROOT="{p.root}",WATCHTOWER_FINAL_SHA="{p.sha}",WATCHTOWER_OFFSET_AUDIT_LEDGER_PATH="{p.audit_ledger}",MONITOR_RUNTIME_STATE_ROOT="{p.monitor_state_root}",MONITOR_ENV_FILE="{p.monitor_env_file}",DB_PATH="{p.canonical_db}",FLEX_DB_PATH="{p.canonical_db}",WT_OPS_DB_PATH="{p.worker_operations_db}",DATABASE_PATH="{p.worker_operations_db}",OPS_V2_DB_PATH="{p.worker_operations_db}",OPERATION_MONITOR_QUEUE_PATH="{p.worker_queue}",OPERATIONS_MODE="MONITOR",MONITOR_RUNTIME="dev",WATCHTOWER_SHADOW_EVALUATION_ENABLED="0",WATCHTOWER_SHADOW_CAPTURE_ENABLED="0",WATCHTOWER_SHADOW_CAPTURE_LEDGER_PATH="{p.shadow_ledger}"
autostart=false
autorestart=false
startretries=0
startsecs=5
stopwaitsecs=15
stopsignal=TERM
stdout_logfile={p.worker_stdout_log}
stdout_logfile_maxbytes=5MB
stdout_logfile_backups=2
stderr_logfile={p.worker_stderr_log}
stderr_logfile_maxbytes=2MB
stderr_logfile_backups=1

'''
