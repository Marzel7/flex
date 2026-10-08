#!/usr/bin/env python3
"""Offline-only Supervisor isolation/config validation entry point."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.utils.supervisor_isolation import (
    SupervisorIsolationError,
    validate_offline_config,
    validate_safe_daemon_config,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="validate a temporary Supervisor config without control access")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--temp-root", type=Path, required=True)
    parser.add_argument("--protect-config", type=Path, action="append", default=[])
    parser.add_argument("--protect-endpoint", type=Path, action="append", default=[])
    parser.add_argument(
        "--daemon-safe",
        action="store_true",
        help="also require every program to be an inert /bin/true probe before any isolated daemon is created",
    )
    args = parser.parse_args()
    try:
        validator = validate_safe_daemon_config if args.daemon_safe else validate_offline_config
        validated = validator(args.config, temp_root=args.temp_root,
            protected_config_paths=tuple(args.protect_config), protected_endpoint_paths=tuple(args.protect_endpoint))
    except SupervisorIsolationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print("SUPERVISOR_OFFLINE_ISOLATION_VALID", validated.config_path.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
