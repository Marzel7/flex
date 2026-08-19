#!/usr/bin/env python3
"""OF-DV34-P1: bounded selective raw-evidence qualification runner.

Executes the frozen 6-representative plan
(docs/audits/of_dv34_p1_local_prediction_freeze.json) against the qualified
durable/resumable B2Z execution boundary, reused directly (not
reimplemented). Each representative requires exactly ONE physical request
(getTransaction against its already-known funding_signature) -- there is no
live discovery of creator or candidate signature, both are frozen from
local evidence.

Hypothesis under test: DV34_DIRECT_CREATOR_FUNDING_EDGES_ARE_RAW_ONCHAIN_SUPPORTED.
Explicitly OUT OF SCOPE: operation identity, Watchtower membership,
canonical attribution.

Credential: read ONLY from the isolated OF_DV34_P1_HELIUS_ENDPOINT
environment variable. Never printed, logged, or persisted by this script.

Usage:
    OF_DV34_P1_HELIUS_ENDPOINT="https://..." python3 scripts/run_of_dv34_p1_selective_verification.py --resume
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.acquisition.b2z_durable_execution import (  # noqa: E402
    B2ZEventLedger,
    B2ZP1Error,
    B2ZStageOutputLedger,
)
from src.acquisition.dv34_p1_selective_verification import (  # noqa: E402
    RUN_ID,
    build_dv34_authorization,
    verify_one_edge,
)

CREDENTIAL_ENV_VAR = "OF_DV34_P1_HELIUS_ENDPOINT"
FREEZE_PATH = ROOT / "docs/audits/of_dv34_p1_local_prediction_freeze.json"
EVENT_LEDGER_PATH = ROOT / f"docs/audits/of_dv34_p1_event_ledger_{RUN_ID}.jsonl"
STAGE_OUTPUT_LEDGER_PATH = ROOT / f"docs/audits/of_dv34_p1_stage_outputs_{RUN_ID}.json"
RESULT_PATH = ROOT / f"docs/audits/of_dv34_p1_live_result_{RUN_ID}.json"


class HeliusTransport:
    """Single-endpoint, single-attempt JSON-RPC transport. No retry."""

    def __init__(self, endpoint: str, *, timeout_seconds: float = 30.0) -> None:
        if not endpoint.startswith("https://"):
            raise ValueError("OF_DV34_P1_HTTPS_ENDPOINT_REQUIRED")
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds
        self.physical_request_count = 0

    def post_json(self, request: dict) -> dict:
        body = json.dumps(request).encode("utf-8")
        outbound = urllib.request.Request(
            self.endpoint, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        self.physical_request_count += 1
        with urllib.request.urlopen(outbound, timeout=self.timeout_seconds) as response:
            return json.loads(response.read())


def _load_predictions() -> list[dict]:
    data = json.loads(FREEZE_PATH.read_text())
    return data["predictions"]


def main() -> int:
    parser = argparse.ArgumentParser(description="OF-DV34-P1 bounded selective raw verification runner.")
    parser.add_argument("--resume", action="store_true", help="Process at most one remaining representative.")
    args = parser.parse_args()
    if not args.resume:
        print(json.dumps({"error": "USAGE", "message": "pass --resume"}))
        return 1

    endpoint = os.environ.get(CREDENTIAL_ENV_VAR)
    if not endpoint:
        print(json.dumps({"error": "OF_DV34_P1_CREDENTIAL_ENV_VAR_MISSING", "message": CREDENTIAL_ENV_VAR}))
        return 1

    freeze_data = json.loads(FREEZE_PATH.read_text())
    predictions = freeze_data["predictions"]
    prediction_freeze_digest = freeze_data["deterministic_digest"]

    auth = build_dv34_authorization(prediction_freeze_digest=prediction_freeze_digest)

    transport = HeliusTransport(endpoint)
    event_ledger = B2ZEventLedger(EVENT_LEDGER_PATH)
    stage_output_ledger = B2ZStageOutputLedger(STAGE_OUTPUT_LEDGER_PATH)

    # find the first prediction (in frozen order) not yet terminally resolved
    from src.acquisition.dv34_p1_selective_verification import STAGE_DIRECT_FUNDING_TX
    reserved = event_ledger.reserved_stage_keys()
    next_ordinal = None
    for i, pred in enumerate(predictions, start=1):
        if (i, STAGE_DIRECT_FUNDING_TX) not in reserved:
            next_ordinal = i
            break
    if next_ordinal is None:
        print(json.dumps({"status": "ALL_REPRESENTATIVES_COMPLETE"}))
        return 0

    pred = predictions[next_ordinal - 1]
    try:
        result = verify_one_edge(
            sample_ordinal=next_ordinal, mint=pred["mint"], prediction=pred, transport=transport,
            event_ledger=event_ledger, stage_output_ledger=stage_output_ledger, authorization=auth,
        )
    except Exception as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}))
        return 1

    text = json.dumps({"sample_ordinal": next_ordinal, "mint": pred["mint"], "result": result}, indent=2, default=str)
    RESULT_PATH.write_text(text + "\n")
    print(json.dumps({"result_written_to": str(RESULT_PATH)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
