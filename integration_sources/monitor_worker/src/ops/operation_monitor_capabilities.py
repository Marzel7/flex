"""Read-only monitor capability declarations for the Operations Registry.

This is deliberately a projection configuration, not a second operation-state
authority.  Canonical operation identity and active/manual admission remain in
the Operations database; this manifest only binds an already-qualified runtime
monitor contract to an existing canonical operation ID.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable


_PATH = Path(__file__).resolve().parents[2] / "docs/operations/operation_monitor_capabilities.v1.json"


def load_monitor_capabilities(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Return validated capability declarations keyed by canonical operator ID."""
    raw = json.loads((path or _PATH).read_text(encoding="utf-8"))
    if raw.get("schema_version") != "operation-monitor-capabilities.v1":
        raise ValueError("UNKNOWN_OPERATION_MONITOR_CAPABILITY_SCHEMA")
    operations = raw.get("operations")
    if not isinstance(operations, dict):
        raise ValueError("INVALID_OPERATION_MONITOR_CAPABILITIES")
    result: dict[str, dict[str, Any]] = {}
    for operator_id, declaration in operations.items():
        if not isinstance(operator_id, str) or not isinstance(declaration, dict):
            raise ValueError("INVALID_OPERATION_MONITOR_CAPABILITY")
        enabled = declaration.get("enabled")
        monitor_operation_id = declaration.get("monitor_operation_id")
        if not isinstance(enabled, bool) or (enabled and not isinstance(monitor_operation_id, str)):
            raise ValueError("INVALID_OPERATION_MONITOR_CAPABILITY")
        result[operator_id] = dict(declaration)
    return result


def monitor_capable_operations(
    active_operations: Iterable[dict[str, Any]], *, capabilities: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Join active canonical registry rows to declared monitor contracts.

    Unknown or non-capable rows remain registry-visible but cannot enter the
    monitor projection.  The result is generic: callers supply registry rows
    and declarations; no operation-name branch exists here.
    """
    declarations = capabilities if capabilities is not None else load_monitor_capabilities()
    result: list[dict[str, Any]] = []
    for operation in active_operations:
        operator_id = str(operation.get("operator_id") or "")
        declaration = declarations.get(operator_id, {})
        if declaration.get("enabled") is not True:
            continue
        monitor_operation_id = declaration.get("monitor_operation_id")
        if not isinstance(monitor_operation_id, str) or not monitor_operation_id:
            continue
        result.append({**operation, "monitor_operation_id": monitor_operation_id,
                       "monitor_profile": declaration.get("profile", "GENERIC")})
    return result


def monitor_capability_for_operation(
    monitor_operation_id: str, *, capabilities: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Resolve one declared Monitor capability by runtime operation ID.

    Runtime consumers use this instead of embedding operation-name decisions.
    The manifest remains a capability declaration, not membership authority.
    """
    target = str(monitor_operation_id).lower()
    for operator_id, declaration in (capabilities or load_monitor_capabilities()).items():
        if declaration.get("enabled") is True and str(declaration.get("monitor_operation_id", "")).lower() == target:
            return {"operator_id": operator_id, **dict(declaration)}
    return None


def monitor_capability_operation_ids(*, capabilities: dict[str, dict[str, Any]] | None = None) -> set[str]:
    """Return every enabled runtime Monitor operation ID from the declaration."""
    return {
        str(declaration["monitor_operation_id"]).lower()
        for declaration in (capabilities or load_monitor_capabilities()).values()
        if declaration.get("enabled") is True and declaration.get("monitor_operation_id")
    }
