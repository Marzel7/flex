#!/usr/bin/env python3
"""Run PSI0H-D against a synthetic isolated lineage only."""

from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evidence.contracts.psi0h_prospective_derivation import qualify_prospective_derivation


def run() -> dict:
    digest = "a" * 64
    result = qualify_prospective_derivation(
        cutoff=1786109860, interval_start=1786109861, interval_end=1786109920,
        envelopes=[{"envelope_id": "fixture-envelope", "event_time": 1786109870,
                    "acquired_at": 1786109872, "artifact_digest": digest}],
        evidence_rows=[{"evidence_id": "fixture-evidence", "envelope_id": "fixture-envelope",
                        "fact_family": "LaunchFact", "event_time": 1786109870,
                        "payload_digest": digest}],
        primitive_rows=[{"primitive_id": "fixture-primitive", "primitive_type": "LAUNCH_SIGNER",
                         "window_start": 1786109870, "window_end": 1786109870,
                         "generated_at": 1786109873, "evidence_ids": ["fixture-evidence"],
                         "missing_inputs": ["AccountParticipationFact"]}],
    )
    return {"milestone": "PSI0H-D", "qualification": result,
            "fixture_only": True, "production_reads": 0, "production_writes": 0,
            "provider_or_rpc_calls": 0, "service_or_configuration_changes": 0}


if __name__ == "__main__":
    print(json.dumps(run(), sort_keys=True, separators=(",", ":")))
