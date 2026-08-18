import json
from pathlib import Path

import pytest

import scripts.run_b2n_p3c_migration_lineage_run as p3c
from src.acquisition.b2n_qualification import (
    AppendOnlyLedger,
    B2NExecutor,
    B2NManifest,
    B2NMember,
    B2NQualificationRunAuthorization,
)
from src.acquisition.b2w_projection import B2WInputProjection, B2WRequestInput, MigrationGetTransactionAdapter

ROOT = Path(__file__).parents[1]


def manifest():
    return B2NManifest(tuple(B2NMember(i, f"mint-{i}", f"event-{i}", True) for i in range(1, 21)))


def projection():
    return B2WInputProjection(tuple(B2WRequestInput(i, f"mint-{i}", f"event-{i}", f"sig-{i}") for i in range(1, 21)))


class SuccessTransport:
    def __init__(self):
        self.physical_request_count = 0

    def post_json(self, request):
        self.physical_request_count += 1
        sig = request["params"][0]
        ordinal = int(sig.rsplit("-", 1)[1])
        return {"result": {"slot": 1, "transaction": {"message": {"accountKeys": [{"pubkey": f"mint-{ordinal}"}]}}}}


class ErrorResponseTransport:
    """Simulates an RPC-level error field in an otherwise-valid HTTP response."""

    def __init__(self):
        self.physical_request_count = 0

    def post_json(self, request):
        self.physical_request_count += 1
        return {"error": {"code": -1, "message": "simulated rpc error"}}


class HTTPErrorTransport:
    """Simulates a transport that dispatches (increments its counter) and then
    raises -- exactly the original RedactingJsonRpcTransport behavior."""

    def __init__(self):
        self.physical_request_count = 0

    def post_json(self, request):
        self.physical_request_count += 1
        raise p3c.B2NP3CError("B2N_P3C_TRANSPORT_ERROR:HTTPError")


class MalformedResponseTransport:
    def __init__(self):
        self.physical_request_count = 0

    def post_json(self, request):
        self.physical_request_count += 1
        return {"result": "not-a-dict"}


class TimeoutTransport:
    def __init__(self):
        self.physical_request_count = 0

    def post_json(self, request):
        self.physical_request_count += 1
        raise TimeoutError("simulated timeout")


def _build(transport, event_ledger_path):
    adapter = MigrationGetTransactionAdapter(transport, projection())
    event_ledger = p3c.PreDispatchEventLedger(event_ledger_path)
    client = p3c.DurableAccountingClient(
        adapter=adapter, transport=transport, event_ledger=event_ledger,
        run_id="test-run", provider="helius", method="getTransaction",
    )
    return client, event_ledger


# --- reproduction of the original defect -----------------------------------

def test_reproduces_original_counter_mismatch_defect_with_real_adapter(tmp_path):
    """Confirms the ROOT CAUSE: the unwrapped MigrationGetTransactionAdapter,
    used directly as B2NExecutor's client (the original wiring), deterministically
    raises B2N_CLIENT_REQUEST_COUNTER_MISMATCH on the very first member -- with
    zero real network access, using only a fake transport."""
    transport = SuccessTransport()
    adapter = MigrationGetTransactionAdapter(transport, projection())
    assert not hasattr(adapter, "provider_request_count")

    ledger_path = tmp_path / "ledger.jsonl"
    auth = B2NQualificationRunAuthorization(
        provider="helius", endpoint_family="helius-mainnet-json-rpc", run_id="test-run",
        manifest_digest=manifest().digest(), ledger_path=str(ledger_path),
    )
    executor = B2NExecutor(
        manifest=manifest(), ledger=AppendOnlyLedger(ledger_path), client=adapter,
        provider="helius", run_id="test-run", authorization=auth,
    )
    with pytest.raises(RuntimeError, match="B2N_CLIENT_REQUEST_COUNTER_MISMATCH"):
        executor.run()
    # and confirms it left NO durable evidence -- the original bug
    assert AppendOnlyLedger(ledger_path).entries() == []


# --- the fix -----------------------------------------------------------------

def test_durable_accounting_client_fixes_the_mismatch(tmp_path):
    transport = SuccessTransport()
    client, event_ledger = _build(transport, tmp_path / "events.jsonl")
    ledger_path = tmp_path / "ledger.jsonl"
    auth = B2NQualificationRunAuthorization(
        provider="helius", endpoint_family="helius-mainnet-json-rpc", run_id="test-run",
        manifest_digest=manifest().digest(), ledger_path=str(ledger_path),
    )
    executor = B2NExecutor(
        manifest=manifest(), ledger=AppendOnlyLedger(ledger_path), client=client,
        provider="helius", run_id="test-run", authorization=auth,
    )
    results = executor.run()
    assert len(results) == 1
    assert results[0]["request_outcome"] == "SUCCESS"
    assert results[0]["request_count"] == 1


# --- Part 4: pre-dispatch durable reservation --------------------------------

def test_reservation_written_before_dispatch(tmp_path):
    transport = SuccessTransport()
    client, event_ledger = _build(transport, tmp_path / "events.jsonl")
    client.acquire_once(mint="mint-1")
    events = event_ledger.events()
    assert events[0]["event"] == "ATTEMPT_RESERVED"
    assert events[0]["sample_ordinal"] == 1
    assert events[-1]["event"] in ("ATTEMPT_SUCCEEDED", "ATTEMPT_FAILED", "ATTEMPT_NOT_DISPATCHED")


# --- Part 6: A-F accounting scenarios -----------------------------------------

def test_scenario_A_http_success_accounted(tmp_path):
    transport = SuccessTransport()
    client, event_ledger = _build(transport, tmp_path / "events.jsonl")
    client.acquire_once(mint="mint-1")
    events = event_ledger.events()
    assert [e["event"] for e in events] == ["ATTEMPT_RESERVED", "ATTEMPT_SUCCEEDED"]
    assert event_ledger.physical_requests_attempted() == 1


def test_scenario_B_rpc_error_response_accounted(tmp_path):
    """An RPC-level error field (valid HTTP response, error inside the JSON body)."""
    transport = ErrorResponseTransport()
    client, event_ledger = _build(transport, tmp_path / "events.jsonl")
    response = client.acquire_once(mint="mint-1")
    assert response.outcome == "RPC_ERROR"
    events = event_ledger.events()
    assert [e["event"] for e in events] == ["ATTEMPT_RESERVED", "ATTEMPT_SUCCEEDED"]
    assert event_ledger.physical_requests_attempted() == 1


def test_scenario_C_timeout_accounted(tmp_path):
    transport = TimeoutTransport()
    client, event_ledger = _build(transport, tmp_path / "events.jsonl")
    with pytest.raises(TimeoutError):
        client.acquire_once(mint="mint-1")
    events = event_ledger.events()
    assert [e["event"] for e in events] == ["ATTEMPT_RESERVED", "ATTEMPT_FAILED"]
    assert events[-1]["error_class"] == "TimeoutError"
    assert event_ledger.physical_requests_attempted() == 1


def test_scenario_D_malformed_response_accounted(tmp_path):
    transport = MalformedResponseTransport()
    client, event_ledger = _build(transport, tmp_path / "events.jsonl")
    response = client.acquire_once(mint="mint-1")
    assert response.outcome == "MALFORMED_RESPONSE"
    events = event_ledger.events()
    assert [e["event"] for e in events] == ["ATTEMPT_RESERVED", "ATTEMPT_SUCCEEDED"]
    assert event_ledger.physical_requests_attempted() == 1


def test_scenario_E_transport_exception_after_dispatch_accounted(tmp_path):
    """This is exactly the historical HTTPError condition: the transport's own
    counter increments BEFORE it raises."""
    transport = HTTPErrorTransport()
    client, event_ledger = _build(transport, tmp_path / "events.jsonl")
    with pytest.raises(p3c.B2NP3CError, match="TRANSPORT_ERROR"):
        client.acquire_once(mint="mint-1")
    events = event_ledger.events()
    assert [e["event"] for e in events] == ["ATTEMPT_RESERVED", "ATTEMPT_FAILED"]
    assert event_ledger.physical_requests_attempted() == 1


def test_scenario_F_exception_before_dispatch_not_accounted(tmp_path):
    """An unknown mint is rejected by DurableAccountingClient's own ordinal
    lookup BEFORE any reservation is written and BEFORE the adapter (and thus
    transport.post_json()) is ever reached -- so it must NOT count as a
    physical request and must leave no event ledger entry at all."""
    transport = SuccessTransport()
    client, event_ledger = _build(transport, tmp_path / "events.jsonl")
    with pytest.raises(p3c.B2NP3CError, match="B2N_P3E_MINT_NOT_IN_PROJECTION"):
        client.acquire_once(mint="totally-unknown-mint")
    assert event_ledger.events() == []
    assert event_ledger.physical_requests_attempted() == 0
    assert transport.physical_request_count == 0


def test_scenario_F_variant_adapter_raises_after_reservation_but_before_dispatch(tmp_path):
    """A stricter variant of Part 6.F: a client whose adapter itself raises
    BEFORE calling transport.post_json() (simulating some other pre-dispatch
    validation failure inside the adapter layer, distinct from
    DurableAccountingClient's own mint check). transport.physical_request_count
    must remain 0, and the event ledger must record ATTEMPT_NOT_DISPATCHED,
    not ATTEMPT_FAILED."""
    class PreDispatchRaisingAdapter:
        by_mint = {"mint-1": projection().members[0]}

        def acquire_once(self, *, mint):
            raise ValueError("SIMULATED_PRE_DISPATCH_VALIDATION_FAILURE")

    transport = SuccessTransport()
    event_ledger = p3c.PreDispatchEventLedger(tmp_path / "events.jsonl")
    client = p3c.DurableAccountingClient(
        adapter=PreDispatchRaisingAdapter(), transport=transport, event_ledger=event_ledger,
        run_id="test-run", provider="helius", method="getTransaction",
    )
    with pytest.raises(ValueError, match="SIMULATED_PRE_DISPATCH_VALIDATION_FAILURE"):
        client.acquire_once(mint="mint-1")
    events = event_ledger.events()
    assert [e["event"] for e in events] == ["ATTEMPT_RESERVED", "ATTEMPT_NOT_DISPATCHED"]
    assert event_ledger.physical_requests_attempted() == 0
    assert transport.physical_request_count == 0


# --- Part 5: canonical counter contract ---------------------------------------

def test_max_one_attempted_request_per_member(tmp_path):
    transport = SuccessTransport()
    client, event_ledger = _build(transport, tmp_path / "events.jsonl")
    client.acquire_once(mint="mint-1")
    with pytest.raises(RuntimeError, match="DUPLICATE_ORDINAL_ATTEMPT"):
        client.acquire_once(mint="mint-1")


def test_max_twenty_attempted_requests_across_run(tmp_path):
    transport = SuccessTransport()
    client, event_ledger = _build(transport, tmp_path / "events.jsonl")
    for i in range(1, 21):
        client.acquire_once(mint=f"mint-{i}")
    assert event_ledger.physical_requests_attempted() == 20
    assert len(event_ledger.reserved_ordinals()) == 20


# --- Part 3: historical reconciliation artifact --------------------------------

def test_historical_reconciliation_artifact_records_both_attempts():
    d = json.loads((ROOT / "docs/audits/b2n_p3e_historical_live_attempt_reconciliation.json").read_text())
    assert len(d["historical_attempts"]) == 2
    assert d["historical_attempts"][0]["reported_error"] == "B2N_P3C_TRANSPORT_ERROR:HTTPError"
    assert d["historical_attempts"][1]["reported_error"] == "B2N_CLIENT_REQUEST_COUNTER_MISMATCH"
    assert d["historical_attempts"][1]["root_cause_identified"] is True
    assert d["durable_ledger_state"]["entries"] == 0
    assert d["authorization_reuse_recommendation"]["reuse_old_authorization_as_is"] is False


# --- Part 7: rerun safety / fresh authorization ---------------------------------

def test_fresh_run_id_differs_from_compromised_prior_run_id():
    assert p3c.EXPECTED_RUN_ID != p3c.PRIOR_RUN_ID_DO_NOT_REUSE
    assert p3c.PRIOR_RUN_ID_DO_NOT_REUSE == "b2n-p3b-c39499f523e42083ce045d70"
    assert p3c.EXPECTED_RUN_ID == "b2n-p3e-98695fbdf44146c93ff08c8c"


def test_successor_p3e_authorization_artifact_supersedes_p3b():
    p = json.loads(p3c.SUCCESSOR_PREFLIGHT_PATH.read_text())
    assert p["run_id"] == p3c.EXPECTED_RUN_ID
    assert p["supersedes"]["prior_run_id"] == p3c.PRIOR_RUN_ID_DO_NOT_REUSE
    assert p["supersedes"]["prior_run_id_reused"] is False


def test_dry_run_binds_to_new_run_id_and_ledgers(tmp_path):
    result = p3c.dry_run(
        ledger_path=tmp_path / "ledger.jsonl",
        event_ledger_path=tmp_path / "events.jsonl",
    )
    assert result["run_id_verified"] == "b2n-p3e-98695fbdf44146c93ff08c8c"
    assert result["event_ledger_verified_empty"] is True
    assert result["network_calls_made"] == 0
