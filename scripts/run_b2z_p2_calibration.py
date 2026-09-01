#!/usr/bin/env python3
"""B2Z-P2: bounded live calibration runner.

Executes the frozen 50-request plan (docs/audits/b2z_p2_local_prediction_freeze.json)
against the qualified durable/resumable B2Z-P1 execution boundary, using a P2
authorization whose max_total_requests is fail-closed at 50 -- strictly
narrower than the module's own 60-request hard ceiling.

For each of the 20 members:
  1. Stage 1 (MIGRATION_TX) is ALWAYS a live, dispatched request.
  2. Stage 2 (CREATOR_HISTORY) is either a live, dispatched request, OR
     seeded from the FROZEN local prediction with zero physical dispatch,
     per the frozen plan -- never decided adaptively at runtime.
  3. Stage 3 (FUNDING_TX) is always a live, dispatched request, targeting
     either the live-discovered signature or the frozen local signature,
     exactly as the plan specifies.

Credential: read ONLY from the isolated B2Z_P2_HELIUS_ENDPOINT environment
variable. Never printed, logged, or persisted by this script.

Usage:
    B2Z_P2_HELIUS_ENDPOINT="https://..." python3 scripts/run_b2z_p2_calibration.py --resume
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.acquisition.b2n_qualification import B2NManifest, B2NMember  # noqa: E402
from src.acquisition.b2w_projection import B2WInputProjection, B2WRequestInput  # noqa: E402
from src.acquisition.b2z_durable_execution import (  # noqa: E402
    B2ZEventLedger,
    B2ZStageOutputLedger,
    build_authorization,
    resume_next,
    seed_frozen_creator_history_from_local_prediction,
)

CREDENTIAL_ENV_VAR = "B2Z_P2_HELIUS_ENDPOINT"
FREEZE_PATH = ROOT / "docs/audits/b2z_p2_local_prediction_freeze.json"
MANIFEST_PATH = ROOT / "docs/evidence_platform/oip_v2_2e_2b2u_b2r_frozen_manifest.json"
PROJECTION_PATH = ROOT / "docs/evidence_platform/oip_v2_2e_2b2bq_b2z_frozen_projection.json"
RUN_ID = "b2z-p1-44d6798563468c8aff747446"
EVENT_LEDGER_PATH = ROOT / f"docs/audits/b2z_p1_event_ledger_{RUN_ID}.jsonl"
STAGE_OUTPUT_LEDGER_PATH = ROOT / f"docs/audits/b2z_p1_stage_outputs_{RUN_ID}.json"
RESULT_PATH = ROOT / f"docs/audits/b2z_p1_live_result_{RUN_ID}.json"
B2N_CLOSURE_DIGEST = "e7623e6070c3e5f7a94fb39988ddd6f21bc77f28f42ef9c8cfe454d5ce67ad54"
P0_PREFLIGHT_DIGEST = "9857b46fbb3a20e02fc35c5188a1fddbbc3b703422b20c1c82cfdb5e1dc1a38b"
MAX_TOTAL_REQUESTS_P2 = 50


class HeliusTransport:
    """Single-endpoint, single-attempt JSON-RPC transport. No retry."""

    def __init__(self, endpoint: str, *, timeout_seconds: float = 30.0) -> None:
        if not endpoint.startswith("https://"):
            raise ValueError("B2Z_P2_HTTPS_ENDPOINT_REQUIRED")
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


def _load_manifest() -> B2NManifest:
    data = json.loads(MANIFEST_PATH.read_text())
    members = tuple(B2NMember(m["sample_ordinal"], m["mint"], m["census_event_id"], m["observation_required"])
                     for m in data["members"])
    return B2NManifest(members)


def _load_projection() -> B2WInputProjection:
    data = json.loads(PROJECTION_PATH.read_text())
    members = tuple(B2WRequestInput(m["sample_ordinal"], m["mint"], m["census_event_id"], m["migration_signature"])
                     for m in data["members"])
    return B2WInputProjection(members)


def _load_freeze() -> dict[int, dict]:
    data = json.loads(FREEZE_PATH.read_text())
    return {p["ordinal"]: p for p in data["predictions"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="B2Z-P2 bounded live calibration runner.")
    parser.add_argument("--resume", action="store_true", help="Process at most one remaining stage.")
    args = parser.parse_args()
    if not args.resume:
        print(json.dumps({"error": "USAGE", "message": "pass --resume"}))
        return 1

    endpoint = os.environ.get(CREDENTIAL_ENV_VAR)
    if not endpoint:
        print(json.dumps({"error": "B2Z_P2_CREDENTIAL_ENV_VAR_MISSING", "message": CREDENTIAL_ENV_VAR}))
        return 1

    manifest = _load_manifest()
    projection = _load_projection()
    freeze = _load_freeze()

    import dataclasses
    auth = build_authorization(
        manifest=manifest, projection=projection,
        b2n_closure_digest=B2N_CLOSURE_DIGEST, p0_preflight_digest=P0_PREFLIGHT_DIGEST,
    )
    auth = dataclasses.replace(auth, max_total_requests=MAX_TOTAL_REQUESTS_P2)
    if auth.run_id != RUN_ID:
        print(json.dumps({"error": "B2Z_P2_RUN_ID_DRIFT", "expected": RUN_ID, "actual": auth.run_id}))
        return 1

    transport = HeliusTransport(endpoint)
    event_ledger = B2ZEventLedger(EVENT_LEDGER_PATH)
    stage_output_ledger = B2ZStageOutputLedger(STAGE_OUTPUT_LEDGER_PATH)

    try:
        result = resume_next(
            manifest=manifest, projection=projection, authorization=auth, transport=transport,
            event_ledger=event_ledger, stage_output_ledger=stage_output_ledger,
        )
    except Exception as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}))
        return 1

    # If the just-completed stage was a live MIGRATION_TX for a Stage-2-skip
    # member, immediately seed the frozen CREATOR_HISTORY stage so the NEXT
    # --resume invocation naturally proceeds to a targeted Stage 3 dispatch,
    # per the frozen plan -- never decided adaptively.
    if result.get("stage") == "MIGRATION_TX" and result.get("status") == "STAGE_COMPLETE":
        ordinal = result["sample_ordinal"]
        pred = freeze.get(ordinal)
        if pred and pred["stage2_skip"]:
            seed_frozen_creator_history_from_local_prediction(
                run_id=auth.run_id, sample_ordinal=ordinal, mint=pred["mint"],
                event_ledger=event_ledger, stage_output_ledger=stage_output_ledger,
                frozen_creator=pred["local_creator"], frozen_migration_time=result["output"]["migration_time"],
                frozen_funding_signature=pred["frozen_stage3_signature_if_skip"],
                frozen_prediction_digest="11357ddcc73ae45d947a3412ef3255952cc491969ae3005b2b81daaef8d487cc",
            )
            result["calibration_seed_applied"] = True

    text = json.dumps(result, indent=2, default=str)
    RESULT_PATH.write_text(text + "\n")
    print(json.dumps({"result_written_to": str(RESULT_PATH)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
