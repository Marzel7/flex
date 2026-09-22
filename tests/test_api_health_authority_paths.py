"""Health diagnostics must keep reading existing state after source cutover."""

import ast
from pathlib import Path


MAIN = Path(__file__).resolve().parents[1] / "src/core/main.py"


def _environment_reads(function_name: str) -> set[str]:
    tree = ast.parse(MAIN.read_text())
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == function_name
    )
    return {
        node.args[0].value
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "environ"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    }


def test_listener_recovery_diagnostics_use_pinned_log_and_audit_paths():
    assert {
        "SUPERVISOR_LISTENER_LOG_PATH",
        "MIGRATION_COVERAGE_AUDIT_PATH",
    } <= _environment_reads("api_listener_recovery_status")


def test_full_health_uses_pinned_serializer_metrics_path():
    assert {
        "DB_SERIALIZER_METRICS_PATH",
        "SUPERVISOR_LISTENER_LOG_PATH",
        "GUNICORN_ERROR_LOG_PATH",
    } <= _environment_reads("api_health_full")
