import json

import pytest

from src.acquisition.b2n_qualification import B2NManifest, B2NMember
from src.acquisition.b2w_projection import B2WInputProjection, B2WRequestInput
from src.acquisition.b2z_execution_boundary import (
    B2ZRunner,
    HeliusJsonRpcTransport,
    PhysicalAttemptLedger,
    proves_inbound_sol_funding,
)


def manifest():
    return B2NManifest(tuple(B2NMember(i, f"mint-{i}", f"event-{i}", True) for i in range(1, 21)))


def projection():
    return B2WInputProjection(tuple(
        B2WRequestInput(i, f"mint-{i}", f"event-{i}", f"migration-{i}") for i in range(1, 21)
    ))


def migration(mint):
    return {"result": {"blockTime": 20, "transaction": {"message": {"accountKeys": [
        {"pubkey": mint, "signer": False}, {"pubkey": "creator", "signer": True},
    ]}}}}


def funding(destination="creator", lamports=10):
    return {"result": {"blockTime": 19, "transaction": {"message": {"instructions": [{
        "program": "system", "parsed": {"type": "transfer", "info": {
            "source": "funder", "destination": destination, "lamports": lamports,
        }},
    }]}}}}


class FakeTransport:
    def __init__(self, *, fail_at=None, candidate=True, funding_response=None):
        self.physical_request_count = 0
        self.requests = []
        self.fail_at = fail_at
        self.candidate = candidate
        self.funding_response = funding_response or funding()

    def post_json(self, request):
        self.physical_request_count += 1
        self.requests.append(request)
        if self.physical_request_count == self.fail_at:
            raise TimeoutError("injected")
        method = request["method"]
        if method == "getSignaturesForAddress":
            return {"result": ([{"signature": "funding", "blockTime": 19}] if self.candidate else [])}
        target = request["params"][0]
        return migration(f"mint-{(self.physical_request_count + 2) // 3}") if target.startswith("migration-") else self.funding_response


def runner(tmp_path, transport):
    return B2ZRunner(
        manifest=manifest(), projection=projection(), transport=transport,
        ledger=PhysicalAttemptLedger(tmp_path / "attempts.jsonl"), run_id="run",
    )


def test_full_fake_run_is_exactly_60_ordered_attempts(tmp_path):
    transport = FakeTransport()
    results = runner(tmp_path, transport).run()
    ledger = [json.loads(line) for line in (tmp_path / "attempts.jsonl").read_text().splitlines()]
    assert len(results) == 20
    assert len(transport.requests) == transport.physical_request_count == len(ledger) == 60
    assert [request["method"] for request in transport.requests[:3]] == [
        "getTransaction", "getSignaturesForAddress", "getTransaction",
    ]
    assert [row["member_request_number"] for row in ledger[:6]] == [1, 2, 3, 1, 2, 3]
    assert ledger[-1]["physical_attempt_number"] == 60
    assert all(row["manifest_digest"] == manifest().digest() for row in ledger)


def test_no_candidate_stops_after_two_and_preserves_partial_ledger(tmp_path):
    transport = FakeTransport(candidate=False)
    with pytest.raises(RuntimeError, match="NO_CANDIDATE"):
        runner(tmp_path, transport).run()
    assert transport.physical_request_count == 2
    assert len(PhysicalAttemptLedger(tmp_path / "attempts.jsonl").entries()) == 2


def test_transport_failure_is_ledgered_once_and_stops_without_retry(tmp_path):
    transport = FakeTransport(fail_at=2)
    with pytest.raises(RuntimeError, match="FIRST_NON_SUCCESS"):
        runner(tmp_path, transport).run()
    ledger = PhysicalAttemptLedger(tmp_path / "attempts.jsonl").entries()
    assert transport.physical_request_count == len(transport.requests) == len(ledger) == 2
    assert ledger[-1]["outcome"] == "NON_SUCCESS"
    assert ledger[-1]["error_class"] == "TimeoutError"


def test_existing_ledger_and_nonzero_client_counter_fail_before_request(tmp_path):
    transport = FakeTransport()
    ledger = PhysicalAttemptLedger(tmp_path / "attempts.jsonl")
    ledger.path.write_text('{"physical_attempt_number":1}\n')
    with pytest.raises(RuntimeError, match="LEDGER_MUST_BE_EMPTY"):
        B2ZRunner(manifest=manifest(), projection=projection(), transport=transport, ledger=ledger).run()
    assert transport.requests == []

    ledger.path.write_text("")
    transport.physical_request_count = 1
    with pytest.raises(RuntimeError, match="COUNTER_NOT_ZERO"):
        B2ZRunner(manifest=manifest(), projection=projection(), transport=transport, ledger=ledger).run()
    assert transport.requests == []


def test_funding_parser_requires_positive_inbound_system_sol_transfer():
    assert proves_inbound_sol_funding(funding()["result"], "creator") is True
    assert proves_inbound_sol_funding(funding(destination="other")["result"], "creator") is False
    assert proves_inbound_sol_funding(funding(lamports=0)["result"], "creator") is False
    token_transfer = {"transaction": {"message": {"instructions": [{
        "program": "spl-token", "parsed": {"type": "transfer", "info": {"destination": "creator", "amount": "10"}},
    }]}}}
    assert proves_inbound_sol_funding(token_transfer, "creator") is False
    inner = {"transaction": {"message": {"instructions": []}}, "meta": {"innerInstructions": [{
        "index": 0, "instructions": funding()["result"]["transaction"]["message"]["instructions"],
    }]}}
    assert proves_inbound_sol_funding(inner, "creator") is True


def test_missing_funding_edge_stops_after_three_with_partial_artifacts(tmp_path):
    transport = FakeTransport(funding_response=funding(destination="other"))
    with pytest.raises(RuntimeError, match="NO_FUNDING_EDGE"):
        runner(tmp_path, transport).run()
    assert transport.physical_request_count == 3
    assert len(PhysicalAttemptLedger(tmp_path / "attempts.jsonl").entries()) == 3


def test_real_transport_construction_is_inert_and_requires_https(monkeypatch):
    called = []
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: called.append(args))
    transport = HeliusJsonRpcTransport("https://example.invalid/rpc")
    assert transport.physical_request_count == 0
    assert called == []
    with pytest.raises(ValueError, match="HTTPS_ENDPOINT_REQUIRED"):
        HeliusJsonRpcTransport("http://example.invalid/rpc")
