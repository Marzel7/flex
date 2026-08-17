#!/usr/bin/env python3
"""PSI0H-E entrypoint; intentionally has no built-in live collector."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evidence.contracts.psi0h_real_cohort_execution import (
    Psi0hRealCohortExecutionError, RealCohortAuthorization,
    verify_real_cohort_authorization,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    values = json.loads(args.authorization.read_text())
    record = RealCohortAuthorization(**values)
    verify_real_cohort_authorization(record)
    if args.preflight_only:
        print(json.dumps({"status": "READY", "authorization_digest": record.authorization_digest,
                          "execution_performed": False}, sort_keys=True))
        return 0
    raise Psi0hRealCohortExecutionError("PSI0H_E_LIVE_COLLECTOR_NOT_CONFIGURED")


if __name__ == "__main__":
    raise SystemExit(main())
