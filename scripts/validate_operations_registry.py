#!/usr/bin/env python3
"""
Operations OS — Registry Validation CLI.

Loads every operation definition in src/ops/registry/ and reports its
lifecycle state and declared capabilities.

Exit codes:
  0  — all definitions valid
  1  — one or more definitions invalid, or registry unreadable

No Flask. No database. No HTTP calls.
"""

import sys
import os

_REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.ops.registry_loader import (
    load_registry,
    RegistryLoadError,
    OperationDefinitionError,
)
from src.ops.contracts import CAPABILITY_CONTRACTS


def _cap_line(cap_name: str, decl: dict) -> str:
    state = decl.get("state", "NOT_DECLARED")
    if state == "SUPPORTED":
        contract  = decl.get("contract", "?")
        provider  = decl.get("provider", "?")
        return f"  {cap_name}: SUPPORTED ({contract} → {provider})"
    elif state == "UNSUPPORTED":
        reason = decl.get("reason", "")
        suffix = f" — {reason}" if reason else ""
        return f"  {cap_name}: UNSUPPORTED{suffix}"
    else:
        return f"  {cap_name}: NOT_DECLARED"


def main() -> int:
    try:
        registry = load_registry()
    except (RegistryLoadError, OperationDefinitionError) as exc:
        print(f"Operations Registry: INVALID\n\n{exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"Operations Registry: ERROR\n\n{exc}", file=sys.stderr)
        return 1

    print("Operations Registry: VALID\n")

    for op_id in sorted(registry):
        op = registry[op_id]
        print(op_id)
        print(f"  display_name:       {op.display_name}")
        print(f"  status:             {op.status}")
        print(f"  infrastructure:     {op.infrastructure_model}")
        print(f"  framework_schema:   v{op.framework_schema_version}")
        print(f"  definition_version: v{op.definition_version}")
        print(f"  operation_version:  {op.operation_version or '(not set)'}")
        print()
        print("  Capabilities:")
        caps = op.capabilities
        if not caps:
            print("    (none declared)")
        else:
            for cap_name, decl in caps.items():
                if decl is None:
                    decl = {}
                print(_cap_line(cap_name, decl))
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
