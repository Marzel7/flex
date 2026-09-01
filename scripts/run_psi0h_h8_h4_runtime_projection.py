#!/usr/bin/env python3
"""Run PSI0H H8→H4 compatibility runtime projection."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evidence.contracts.psi0h_h8_to_h4_runtime_projection import (
    verify_projection_manifest,
    project_h8_to_h4_runtime,
    read_json_path,
)


def run(*, h8_artifact: str, output_runtime_db: str, output_manifest: str | None = None) -> dict[str, str]:
    h8_payload = read_json_path(h8_artifact)
    manifest_payload, result = project_h8_to_h4_runtime(
        h8_artifact=h8_payload,
        runtime_db_path=output_runtime_db,
        manifest_path=output_manifest,
    )
    verify_projection_manifest(manifest_payload)
    return {
        "runtime_db_path": result["runtime_db_path"],
        "manifest_path": result["manifest_path"],
        "runtime_exists": str(result["runtime_exists"]),
        "manifest_digest": manifest_payload["manifest_digest"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run PSI0H H8 to H4 runtime projection.")
    parser.add_argument("--h8-artifact", required=True, help="Path to PSI0H-H8 artifact JSON.")
    parser.add_argument("--output-runtime-db", required=True, help="Output operation-runtime sqlite path.")
    parser.add_argument("--output-manifest", required=True, help="Output projection manifest path.")
    args = parser.parse_args()

    artifact = run(
        h8_artifact=args.h8_artifact,
        output_runtime_db=args.output_runtime_db,
        output_manifest=args.output_manifest,
    )
    print(json.dumps(artifact))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
