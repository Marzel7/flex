#!/usr/bin/env python3
"""PSI0H-E2 qualification uses only the injected fixture transport."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.acquisition.transaction import AcquisitionMetadata, AcquisitionResponse
from src.evidence.contracts.psi0h_census_transaction_adapter import collect_census_transactions
from src.evidence.contracts.psi0h_prospective_derivation import qualify_prospective_derivation


def event() -> dict:
    return {"event_id": "fixture-event", "event_type": "MIGRATION",
            "receive_utc_ns": 103_000_000_000, "signature": "fixture-signature",
            "mint": "fixture-mint"}


def response(signature: str, mint: str) -> AcquisitionResponse:
    body = {"jsonrpc": "2.0", "result": {"slot": 10, "blockTime": 102,
        "version": 0, "confirmationStatus": "finalized",
        "transaction": {"signatures": [signature], "message": {
            "accountKeys": [{"pubkey": "source", "signer": True, "writable": True},
                {"pubkey": "destination", "signer": False, "writable": True},
                {"pubkey": "11111111111111111111111111111111", "signer": False, "writable": False}],
            "recentBlockhash": "hash", "header": {"numRequiredSignatures": 1,
                "numReadonlySignedAccounts": 0, "numReadonlyUnsignedAccounts": 1},
            "instructions": [{"programId": "11111111111111111111111111111111",
                "accounts": [0, 1], "parsed": {"type": "transfer", "info": {
                    "source": "source", "destination": "destination", "lamports": 5}}}]}},
        "meta": {"err": None, "fee": 5000, "innerInstructions": [], "logMessages": [],
                 "preBalances": [10, 0, 1], "postBalances": [5, 5, 1]}}}
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    metadata = AcquisitionMetadata("fixture-acquisition", "fixture-correlation",
        "psi0h_fixture", None, mint, "json_rpc", "injected_provider", "getTransaction",
        None, None, 105.0, "miss", 0)
    return AcquisitionResponse(200, body, None, {"content-type": "application/json"},
                               metadata, 1.0, raw, "EXACT_PROVIDER_ARTIFACT")


def run() -> dict:
    with tempfile.TemporaryDirectory(prefix="psi0h-e2-") as directory:
        result = collect_census_transactions(events=[event()], interval_start=101,
            interval_end=110, staging_root=Path(directory) / "stage",
            transport=lambda signature, mint: response(signature, mint))
        qualification = qualify_prospective_derivation(cutoff=100, interval_start=101,
            interval_end=110, envelopes=result["envelopes"],
            evidence_rows=result["evidence_rows"], primitive_rows=result["primitive_rows"])
        return {"status": qualification["status"], "fixture_only": True,
                "provider_request_count": result["provider_request_count"],
                "attempts_digest": result["attempts_digest"],
                "lineage_digest": qualification["lineage_digest"],
                "replay_digest": qualification["replay_digest"],
                "real_provider_requests": 0, "production_reads": 0, "production_writes": 0}


if __name__ == "__main__":
    print(json.dumps(run(), sort_keys=True, separators=(",", ":")))
