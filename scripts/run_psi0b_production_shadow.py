#!/usr/bin/env python3
"""Path-independent PSI0B-E7 bootstrap; execution remains explicitly injected."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from src.evidence.contracts.production_shadow_launcher import (  # noqa: E402
    LAUNCHER_VERSION,
    validate_bootstrap_inputs,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the PSI0B production-shadow launch boundary")
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--preflight-artifact", type=Path, required=True)
    parser.add_argument("--consumption-directory", type=Path, required=True)
    parser.add_argument("--bootstrap-check", action="store_true", required=True)
    args = parser.parse_args()
    record, preflight, marker = validate_bootstrap_inputs(
        args.authorization, args.preflight_artifact, args.consumption_directory,
    )
    print(
        f"{LAUNCHER_VERSION} BOOTSTRAP_PASS authorization={record.authorization_id} "
        f"run={preflight.run_id} unconsumed_marker={marker}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
