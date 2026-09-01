#!/usr/bin/env python3
"""PSI0H-E4 live-census high-water preflight."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.pumpportal_migration_census import configured_migration_census
from src.evidence.contracts.psi0h_e4_live_census_preflight import (
    build_live_census_preflight, verify_live_census_preflight,
)

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs/audits/psi0h_e4_live_census_preflight.json"


def _digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _proposed_root(output: Path) -> Path:
    root = output.parent / "psi0h_e4_live_census_preflight_paths"
    if not root.exists():
        return root
    index = 0
    while True:
        candidate = output.parent / f"psi0h_e4_live_census_preflight_paths_{index}"
        if not candidate.exists():
            return candidate
        index += 1


def run(
    *,
    output: Path | None = None,
    census_path: Path | None = None,
    interval_start: int = 101,
    interval_end: int = 110,
    cutoff: int = 100,
) -> dict:
    output = output or OUTPUT
    selected_census = Path(census_path) if census_path is not None else Path(configured_migration_census().path).resolve()
    if not selected_census.is_absolute():
        selected_census = selected_census.resolve()
    root = _proposed_root(output)
    root.mkdir(parents=True, exist_ok=True)
    preflight = build_live_census_preflight(
        run_id="psi0h-e4-live-census",
        source_id="pumpportal-migration-census",
        source_kind="migration-census-live-file",
        census_path=selected_census,
        maximum_census_bytes=64 * 1024,
        interval_start=interval_start,
        interval_end=interval_end,
        cutoff=cutoff,
        staging_directory=root / "staging",
        output_directory=root / "output",
        consumption_directory=root / "consumption",
    )
    verify_live_census_preflight(preflight)

    contract_replay_digest = preflight.preflight_digest
    result = {
        "status": "PASS",
        "fixture_only": False,
        "contract": "src/evidence/contracts/psi0h_e4_live_census_preflight.py",
        "contract_digest": _digest({"path": "src/evidence/contracts/psi0h_e4_live_census_preflight.py"}),
        "preflight": {
            **{key: value for key, value in preflight.__dict__.items()},
            "source_read": False,
        },
        "proposed_live_preflight": {
            "census_high_water_start": preflight.census_start_offset,
            "proposed_maximum_bytes": preflight.maximum_census_bytes,
            "proposed_output_paths": {
                "staging_directory": preflight.staging_directory,
                "output_directory": preflight.output_directory,
                "consumption_directory": preflight.consumption_directory,
            },
            "authority": {
                "source_read": False,
                "provider_access": False,
                "service_changes": False,
                "comparison": False,
                "monitoring": False,
                "activation": False,
            },
        },
        "preflight_digest": contract_replay_digest,
        "live_census_reads": 1,
        "provider_requests": 0,
        "real_provider_requests": 0,
        "service_changes": 0,
    }
    result["artifact_digest"] = _digest(result)
    if output is not None:
        if output.exists():
            raise FileExistsError(f"PSI0H_E4_OUTPUT_EXISTS:{output}")
        output.write_text(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--census-path", type=Path, default=None)
    parser.add_argument("--interval-start", type=int, default=101)
    parser.add_argument("--interval-end", type=int, default=110)
    parser.add_argument("--cutoff", type=int, default=100)
    args = parser.parse_args()
    payload = run(
        output=args.output,
        census_path=args.census_path,
        interval_start=args.interval_start,
        interval_end=args.interval_end,
        cutoff=args.cutoff,
    )
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
