#!/usr/bin/env python3
"""Fail-closed local development runtime for the FLEX API/UI.

This module intentionally wraps the existing Flask application instead of
changing its production startup contract.  Persistent databases outside the
dedicated DEV state root are opened read-only and outbound network connections
are denied.  The runtime is suitable for UI and read-path development only.
"""

from __future__ import annotations

import os
from pathlib import Path
import socket
import sqlite3
import threading
from html import escape
from urllib.parse import unquote


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEV_ROOT = Path(os.environ.get("FLEX_DEV_STATE_ROOT", PROJECT_ROOT / ".dev_runtime")).resolve()
DEV_HOST = os.environ.get("FLEX_DEV_HOST", "127.0.0.1")
DEV_PORT = int(os.environ.get("FLEX_DEV_PORT", "8100"))
DEV_MONITOR_DB = DEV_ROOT / "monitor" / "wt_ops_v2.dev.db"
MISSION_CONTROL_MONITOR_DB = Path(os.environ.get(
    "MISSION_CONTROL_MONITOR_UI_DB_PATH",
    DEV_ROOT / "monitor" / "dev_005a" / "wt_ops_v2.dev.db",
)).resolve()

for directory in (DEV_ROOT, DEV_ROOT / "logs", DEV_ROOT / "run", DEV_ROOT / "state", DEV_ROOT / "database"):
    directory.mkdir(parents=True, exist_ok=True)

os.environ.update(
    {
        "FLEX_DEV_RUNTIME": "1",
        "FLEX_DEV_UNIVERSAL_FORWARD_MONITOR": "1",
        "FLEX_ENVIRONMENT_MARKER": "DEVELOPMENT — READ ONLY",
        "FLEX_UI_RECOVERY_MODE": "1",
        "FLEX_ENABLE_FLASK_BACKGROUND_WORKERS": "0",
        "FLEX_WS_DISABLED": "1",
        "ENABLE_CREATE_INTERCEPTOR": "false",
        "OPERATIONS_MODE": os.environ.get("FLEX_DEV_OPERATIONS_MODE", "MONITOR"),
        "WT_PREWARM_START_DELAY_SECONDS": "86400",
        "DB_PATH": str((PROJECT_ROOT / "database" / "flex_complete_database.db").resolve()),
        # Registry/read-model queries continue to use the established
        # operations authority read-only.  A clean runtime worktree does not
        # contain that database, so retain an explicitly supplied binding
        # instead of silently resolving a nonexistent worktree-relative path.
        # Only the Monitor projection reads this DEV-owned DB.
        "WT_OPS_DB_PATH": os.environ.get(
            "WT_OPS_DB_PATH",
            str((PROJECT_ROOT / "database" / "wt_ops_v2.db").resolve()),
        ),
        "DEV_MONITOR_DB_PATH": str(DEV_MONITOR_DB),
        "MISSION_CONTROL_MONITOR_UI_DB_PATH": str(MISSION_CONTROL_MONITOR_DB),
        "OPERATION_MONITOR_QUEUE_PATH": str(DEV_ROOT / "monitor" / "queue"),
        "SQLITE_LIFECYCLE_LOG": str(DEV_ROOT / "logs" / "sqlite_lifecycle.jsonl"),
        "DB_CONNECTION_LIFECYCLE_LOG": str(DEV_ROOT / "logs" / "db_connection_lifecycle.jsonl"),
        "DB_WRITE_DIAGNOSTICS_PATH": str(DEV_ROOT / "logs" / "db_write_diagnostics.jsonl"),
    }
)


_sqlite_connect = sqlite3.connect


def _sqlite_path(database: object, *, uri: bool) -> Path | None:
    text = os.fspath(database) if isinstance(database, (str, bytes, os.PathLike)) else str(database)
    if isinstance(text, bytes):
        text = os.fsdecode(text)
    if text == ":memory:" or text.startswith("file::memory:"):
        return None
    if text.startswith("file:"):
        text = unquote(text[5:].split("?", 1)[0])
    return Path(text).expanduser().resolve()


def _dev_guarded_connect(database, *args, **kwargs):
    """Allow writable SQLite only below .dev_runtime; force all else read-only."""
    uri = bool(kwargs.get("uri", False))
    path = _sqlite_path(database, uri=uri)
    if path is None:
        return _sqlite_connect(database, *args, **kwargs)
    try:
        path.relative_to(DEV_ROOT)
        in_dev_root = True
    except ValueError:
        in_dev_root = False
    if in_dev_root:
        return _sqlite_connect(database, *args, **kwargs)
    if not path.exists():
        raise sqlite3.OperationalError(
            f"DEV runtime refuses to create SQLite state outside {DEV_ROOT}: {path}"
        )
    guarded_kwargs = dict(kwargs)
    guarded_kwargs["uri"] = True
    guarded_database = f"file:{path}?mode=ro"
    connection = _sqlite_connect(guarded_database, *args, **guarded_kwargs)
    connection.execute("PRAGMA query_only=ON")
    return connection


sqlite3.connect = _dev_guarded_connect


_socket_connect = socket.socket.connect
_socket_connect_ex = socket.socket.connect_ex
_create_connection = socket.create_connection


def _is_loopback(address: object) -> bool:
    if isinstance(address, str):  # AF_UNIX
        return True
    if not isinstance(address, tuple) or not address:
        return False
    return str(address[0]).lower() in {"127.0.0.1", "::1", "localhost"}


def _blocked_connect(sock, address):
    if _is_loopback(address):
        return _socket_connect(sock, address)
    raise OSError(f"DEV runtime blocks outbound network connection to {address!r}")


def _blocked_connect_ex(sock, address):
    if _is_loopback(address):
        return _socket_connect_ex(sock, address)
    return 101  # ENETUNREACH on macOS/Linux for fail-closed callers.


def _blocked_create_connection(address, *args, **kwargs):
    if _is_loopback(address):
        return _create_connection(address, *args, **kwargs)
    raise OSError(f"DEV runtime blocks outbound network connection to {address!r}")


socket.socket.connect = _blocked_connect
socket.socket.connect_ex = _blocked_connect_ex
socket.create_connection = _blocked_create_connection


_thread_start = threading.Thread.start
_SUPPRESSED_IMPORT_THREADS = {"db-wal-watchdog", "operational-intelligence-prewarm"}


def _dev_import_thread_start(thread):
    if thread.name in _SUPPRESSED_IMPORT_THREADS:
        print(f"[DEV_RUNTIME] suppressed import-time thread: {thread.name}", flush=True)
        return None
    return _thread_start(thread)


threading.Thread.start = _dev_import_thread_start
from flask import jsonify, request  # noqa: E402
from src.core.main import app  # noqa: E402
from src.dev_work_registry import load_registry  # noqa: E402
from src.ops.universal_monitor_runtime import enable_forward_monitoring, ensure_runtime_schema  # noqa: E402
from src.ops.universal_provider_executor import UniversalProviderExecutor  # noqa: F401,E402
from src.ops.universal_birdeye_market_window import acquire_ledgered_market_window  # noqa: F401,E402
threading.Thread.start = _thread_start

# The universal bridge is intentionally persistence-only in this DEV runtime.
# It creates compact schema/policy/budget state in the same DEV authority used
# by Mission Control, but owns no provider transport and cannot dispatch.
with sqlite3.connect(MISSION_CONTROL_MONITOR_DB) as _universal_bridge_db:
    ensure_runtime_schema(_universal_bridge_db)
    _universal_forward_state = enable_forward_monitoring(_universal_bridge_db, now=__import__('time').time_ns() // 1_000_000_000)


app.config.update(
    ENV="development",
    TEMPLATES_AUTO_RELOAD=True,
    DEV_RUNTIME=True,
    DEV_STATE_ROOT=str(DEV_ROOT),
)


@app.before_request
def _dev_runtime_request_guard():
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        return jsonify(
            error="DEV runtime is read-only",
            environment="DEVELOPMENT",
            production_mutation=False,
        ), 403


@app.get("/health")
@app.get("/dev-runtime/health")
def dev_runtime_health():
    if request.args.get("request_stacks") == "1":
        import sys
        import traceback
        return jsonify(stacks=[[
            {"file": item.filename, "line": item.lineno, "function": item.name}
            for item in traceback.extract_stack(frame)[-18:]
        ] for frame in list(sys._current_frames().values())[:32]])
    return jsonify(
        status="ok",
        environment="DEVELOPMENT",
        mode="read-only",
        source_root=str(PROJECT_ROOT),
        state_root=str(DEV_ROOT),
        outbound_network="blocked",
        production_databases="read-only",
        universal_monitor_bridge="forward-enabled-policy-c",
    )


@app.get("/dev/work")
def dev_work_registry_page():
    """DEV-only read view; this route is never registered by production."""
    registry = load_registry()
    rows = []
    for item in registry["work_items"]:
        intent = escape(item["prod_intent"])
        intent_style = "color:#fb7185;font-weight:800" if intent in {"DEV_ONLY", "DO_NOT_DEPLOY"} else ""
        candidate = escape(item.get("prod_candidate_commit") or "—")
        if len(candidate) > 14:
            candidate = candidate[:12] + "…"
        rows.append(
            "<tr>"
            f'<td><strong>{escape(item["work_id"])}</strong><br><span>{escape(item["title"])}</span></td>'
            f'<td>{escape(item["status"])}</td>'
            f'<td style="{intent_style}">{intent}</td>'
            f'<td>{escape(item["prod_approval"])}</td>'
            f'<td>{escape(item["test_status"])}</td>'
            f'<td>{"YES" if item.get("dev_ui_verified") else "NO"}</td>'
            f'<td><code>{candidate}</code></td>'
            "</tr>"
        )
    if not rows:
        rows.append('<tr><td colspan="7">No new work items yet. Existing edits remain UNMAPPED_EXISTING_DEV_WORK.</td></tr>')
    counts = {
        "in_progress": sum(item["status"] == "IN_PROGRESS" for item in registry["work_items"]),
        "complete": sum(item["status"] == "DEV_COMPLETE" for item in registry["work_items"]),
        "approved": sum(item["prod_approval"] == "APPROVED" for item in registry["work_items"]),
    }
    html = f"""<!doctype html><html><head><meta charset="utf-8"><title>DEV work registry</title>
<style>body{{margin:0;background:#071018;color:#dce7f5;font:14px -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}}main{{padding:28px}}h1{{margin-bottom:4px}}.rule{{color:#fbbf24;font-weight:800}}.counts{{color:#93a4ba;margin:14px 0 22px}}table{{width:100%;border-collapse:collapse;background:#101d2d}}th,td{{padding:12px;border:1px solid #2b3b50;text-align:left;vertical-align:top}}th{{background:#17263a;color:#93c5fd}}code{{color:#a7f3d0}}</style></head>
<body><main><h1>DEV work registry</h1><p class="rule">DEV WORK != PROD CANDIDATE</p>
<p class="counts">{counts['in_progress']} in progress · {counts['complete']} DEV complete · {counts['approved']} approved for PROD</p>
<table><thead><tr><th>Work</th><th>Status</th><th>Prod intent</th><th>Prod approval</th><th>Tests</th><th>DEV verified</th><th>Candidate commit</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
<p>Read-only view. Manage locally with <code>./scripts/dev_work.sh</code>. Approval never deploys.</p></main></body></html>"""
    return html


_DEV_BANNER = """
<div id="flex-dev-environment-banner" role="status" style="position:sticky;top:0;z-index:100000;background:#7c2d12;color:#fff7ed;border-bottom:2px solid #fb923c;padding:9px 16px;text-align:center;font:800 13px/1.2 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;letter-spacing:.09em">DEVELOPMENT — READ ONLY — PRODUCTION DATA CANNOT BE MODIFIED</div>
""".encode("utf-8")


@app.after_request
def _inject_dev_banner(response):
    response.headers["X-FLEX-Environment"] = "development-read-only"
    if response.mimetype == "text/html" and not response.direct_passthrough:
        body = response.get_data()
        marker = b"<body>"
        if marker in body and b"flex-dev-environment-banner" not in body:
            response.set_data(body.replace(marker, marker + _DEV_BANNER, 1))
            response.headers.pop("Content-Length", None)
    return response


def main() -> None:
    from werkzeug.serving import run_simple

    use_reloader = os.environ.get("FLEX_DEV_RELOAD", "1").lower() in {"1", "true", "yes"}
    print(f"[DEV_RUNTIME] source={PROJECT_ROOT}", flush=True)
    print(f"[DEV_RUNTIME] state={DEV_ROOT}", flush=True)
    print(f"[DEV_RUNTIME] url=http://{DEV_HOST}:{DEV_PORT}", flush=True)
    print("[DEV_RUNTIME] production SQLite=read-only; HTTP mutations=blocked; outbound network=blocked", flush=True)
    run_simple(
        DEV_HOST,
        DEV_PORT,
        app,
        use_reloader=use_reloader,
        use_debugger=False,
        threaded=True,
    )


if __name__ == "__main__":
    main()
