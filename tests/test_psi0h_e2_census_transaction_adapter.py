import json

import pytest

from src.acquisition.transaction import AcquisitionMetadata, AcquisitionResponse
from src.evidence.contracts.psi0h_census_transaction_adapter import (
    Psi0hCensusTransactionAdapterError, collect_census_transactions,
)
from src.evidence.contracts.psi0h_prospective_derivation import qualify_prospective_derivation


def event(signature="sig-1", mint="mint-1", received=103):
    return {"event_id": f"event-{signature}", "event_type": "MIGRATION",
            "receive_utc_ns": received * 1_000_000_000,
            "signature": signature, "mint": mint}


def response(signature="sig-1", mint="mint-1", *, block_time=102, retry=0):
    body = {"jsonrpc": "2.0", "result": {
        "slot": 10, "blockTime": block_time, "version": 0,
        "confirmationStatus": "finalized",
        "transaction": {"signatures": [signature], "message": {
            "accountKeys": [
                {"pubkey": "source", "signer": True, "writable": True},
                {"pubkey": "destination", "signer": False, "writable": True},
                {"pubkey": "11111111111111111111111111111111", "signer": False, "writable": False},
            ], "recentBlockhash": "hash", "header": {"numRequiredSignatures": 1,
                "numReadonlySignedAccounts": 0, "numReadonlyUnsignedAccounts": 1},
            "instructions": [{"programId": "11111111111111111111111111111111",
                "accounts": [0, 1], "parsed": {"type": "transfer",
                    "info": {"source": "source", "destination": "destination", "lamports": 5}}}] }},
        "meta": {"err": None, "fee": 5000, "innerInstructions": [],
                 "logMessages": [], "preBalances": [10, 0, 1], "postBalances": [5, 5, 1]}}}
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    metadata = AcquisitionMetadata(
        acquisition_id=f"acq-{signature}", correlation_id=f"corr-{signature}",
        purpose="psi0h_prospective_migration", creator=None, launch=mint,
        request_type="json_rpc", provider="injected_provider", method="getTransaction",
        page_number=None, cursor=None, timestamp=105.0, cache_state="miss", retry_count=retry)
    return AcquisitionResponse(200, body, None, {"content-type": "application/json"},
                               metadata, 1.0, raw, "EXACT_PROVIDER_ARTIFACT")


def test_injected_adapter_runs_exactly_once_and_emits_psi0h_d_lineage(tmp_path):
    calls = []
    def transport(signature, mint):
        calls.append((signature, mint)); return response(signature, mint)
    result = collect_census_transactions(events=[event()], interval_start=101,
        interval_end=110, staging_root=tmp_path / "stage", transport=transport)
    assert calls == [("sig-1", "mint-1")] and result["provider_request_count"] == 1
    ledger = (tmp_path / "stage/physical_attempts.jsonl").read_text().splitlines()
    assert len(ledger) == 1 and json.loads(ledger[0])["signature"] == "sig-1"
    assert result["primitive_rows"] and result["evidence_rows"] and result["envelopes"]
    qualified = qualify_prospective_derivation(
        cutoff=100, interval_start=101, interval_end=110,
        envelopes=result["envelopes"], evidence_rows=result["evidence_rows"],
        primitive_rows=result["primitive_rows"])
    assert qualified["status"] == "PASS" and not any(qualified["authority"].values())


def test_order_is_deterministic_and_one_call_per_unique_event(tmp_path):
    calls = []
    def transport(signature, mint):
        calls.append(signature); return response(signature, mint, block_time=102 if signature == "a" else 103)
    result = collect_census_transactions(events=[event("b", "mb", 104), event("a", "ma", 103)],
        interval_start=101, interval_end=110, staging_root=tmp_path / "stage", transport=transport)
    assert calls == ["a", "b"] and len(result["attempts"]) == 2


def test_duplicate_historical_or_signature_drift_fails_closed(tmp_path):
    with pytest.raises(Psi0hCensusTransactionAdapterError, match="CENSUS_EVENT_INVALID"):
        collect_census_transactions(events=[event(), event()], interval_start=101,
            interval_end=110, staging_root=tmp_path / "a", transport=lambda *args: response())
    with pytest.raises(Psi0hCensusTransactionAdapterError, match="CENSUS_EVENT_INVALID"):
        collect_census_transactions(events=[event(received=99)], interval_start=101,
            interval_end=110, staging_root=tmp_path / "b", transport=lambda *args: response())
    with pytest.raises(Psi0hCensusTransactionAdapterError, match="EVENT_TIME_OR_SIGNATURE_DRIFT"):
        collect_census_transactions(events=[event()], interval_start=101,
            interval_end=110, staging_root=tmp_path / "c",
            transport=lambda *args: response("other"))


def test_retry_or_nonexact_response_fails_after_one_attempt(tmp_path):
    with pytest.raises(Psi0hCensusTransactionAdapterError, match="RESPONSE_INVALID"):
        collect_census_transactions(events=[event()], interval_start=101,
            interval_end=110, staging_root=tmp_path / "a",
            transport=lambda *args: response(retry=1))
    assert len((tmp_path / "a/physical_attempts.jsonl").read_text().splitlines()) == 1
    value = response()
    value = AcquisitionResponse(value.status, value.data, value.text, value.headers,
        value.metadata, value.latency_ms, None, "RAW_BYTES_UNAVAILABLE")
    with pytest.raises(Psi0hCensusTransactionAdapterError, match="RESPONSE_INVALID"):
        collect_census_transactions(events=[event()], interval_start=101,
            interval_end=110, staging_root=tmp_path / "b", transport=lambda *args: value)


def test_existing_or_overbound_destination_fails_before_transport(tmp_path):
    existing = tmp_path / "stage"; existing.mkdir()
    with pytest.raises(Psi0hCensusTransactionAdapterError, match="BOUND_OR_DESTINATION"):
        collect_census_transactions(events=[event()], interval_start=101,
            interval_end=110, staging_root=existing, transport=lambda *args: response())
    with pytest.raises(Psi0hCensusTransactionAdapterError, match="BOUND_OR_DESTINATION"):
        collect_census_transactions(events=[event(str(i), str(i), 103) for i in range(21)],
            interval_start=101, interval_end=110, staging_root=tmp_path / "other",
            transport=lambda *args: response())
