#!/usr/bin/env python3
"""PSI0H-E3 fixture-only wrapper qualification."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.run_psi0h_e2_census_transaction_adapter_qualification import event, response
from src.evidence.contracts.psi0h_real_cohort_execution import build_real_cohort_authorization
from src.evidence.contracts.psi0h_real_run_preflight import (
    E2_ADAPTER_SHA256, build_real_run_preflight, execute_preflight_bound_fixture,
)


def run() -> dict:
    with tempfile.TemporaryDirectory(prefix="psi0h-e3-") as directory:
        root = Path(directory)
        preflight = build_real_run_preflight(run_id="psi0h-e3-fixture",
            source_id="pumpportal-migration-census", census_path=Path("/future/census.jsonl"),
            census_device=1, census_inode=2, census_start_offset=100,
            maximum_census_bytes=65536, interval_start=101, interval_end=110, cutoff=100,
            endpoint_class="solana-json-rpc-gettransaction",
            staging_directory=root / "staging", output_directory=root / "output",
            consumption_directory=root / "consumption")
        authorization = build_real_cohort_authorization(
            authorization_id="psi0h-e3-fixture-auth", run_id=preflight.run_id,
            source_id=preflight.source_id, source_kind="migration-census-byte-range",
            interval_start=101, interval_end=110, cutoff=100, maximum_envelopes=20,
            maximum_primitives=20, maximum_provider_requests=20,
            provider_access_allowed=True, service_changes_allowed=False,
            isolated_output_directory=root / "output", collector_contract_digest=E2_ADAPTER_SHA256)
        result = execute_preflight_bound_fixture(preflight=preflight,
            authorization=authorization, events=[event()], transport=response)
        return {"status": result["status"], "fixture_only": True,
                "preflight_digest": preflight.preflight_digest,
                "cohort_artifact_digest": result["artifact_digest"],
                "provider_request_count": result["provider_request_count"],
                "real_provider_requests": 0, "live_census_reads": 0}


if __name__ == "__main__":
    print(json.dumps(run(), sort_keys=True, separators=(",", ":")))
