#!/usr/bin/env python3
"""Materialize the read-only frozen-source coverage audit."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.ops.generic_living_source_coverage import ROOT, build_coverage_report


def main() -> None:
    report = build_coverage_report()
    report["tests"] = "python -m pytest -q tests/test_generic_living_pipeline_v2.py"
    output = ROOT / "docs/audits/generic_living_association_source_coverage.v1.json"
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    output.write_text(encoded)
    print(hashlib.sha256(encoded.encode()).hexdigest())


if __name__ == "__main__":
    main()
