import pytest

from src.acquisition.b2y_creator_funding_contract import B2YCreatorFundingProbe, B2YMember


def migration():
    return {"result": {"blockTime": 20, "transaction": {"message": {"accountKeys": [
        {"pubkey": "mint", "signer": False}, {"pubkey": "creator", "signer": True},
    ]}}}}


def proof(*, destination="creator", source="funder", lamports=10, block_time=19):
    return {"result": {"blockTime": block_time, "transaction": {"message": {"instructions": [{
        "program": "system", "parsed": {"type": "transfer", "info": {
            "source": source, "destination": destination, "lamports": lamports,
        }},
    }]}}}}


def enhanced(*, signature="funding", destination="creator", source="funder", amount=10, timestamp=19):
    return [{"signature": signature, "timestamp": timestamp, "nativeTransfers": [{
        "fromUserAccount": source, "toUserAccount": destination, "amount": amount,
    }]}]


class FakeTransport:
    def __init__(self, rows=None, funding=None):
        self.calls = []
        self.rows = enhanced() if rows is None else rows
        self.funding = proof() if funding is None else funding

    def get_transaction(self, signature):
        self.calls.append(("getTransaction", signature))
        return migration() if signature == "migration" else self.funding

    def get_oldest_enhanced_transaction(self, address):
        self.calls.append(("getOldestEnhancedAddressTransaction", address, 1, "asc", "finalized"))
        return self.rows


def test_transfer_aware_three_request_sequence_and_bounded_projections():
    transport = FakeTransport()
    result = B2YCreatorFundingProbe(transport).probe_once(B2YMember("mint", "migration"))
    assert result.outcome == "SUCCESS"
    assert result.request_count == len(transport.calls) == len(result.projections) == 3
    assert [call[0] for call in transport.calls] == [
        "getTransaction", "getOldestEnhancedAddressTransaction", "getTransaction",
    ]
    assert transport.calls[1][2:] == (1, "asc", "finalized")
    assert result.candidate_signature == "funding"
    assert result.projections[1].response_kind == "ENHANCED_INBOUND_SOL"
    assert result.projections[1].source == "funder"
    assert result.projections[1].destination == "creator"
    assert result.projections[1].lamports == 10 and result.projections[1].page_row_count == 1
    assert all(projection.lineage_valid for projection in result.projections)


def test_oldest_row_must_itself_be_inbound_funding():
    transport = FakeTransport(rows=enhanced(signature="actual-funding", timestamp=18), funding=proof(block_time=18))
    result = B2YCreatorFundingProbe(transport).probe_once(B2YMember("mint", "migration"))
    assert result.outcome == "SUCCESS"
    assert result.candidate_signature == "actual-funding"
    assert transport.calls[-1] == ("getTransaction", "actual-funding")


def test_no_transfer_aware_candidate_stops_after_two_without_proof_request():
    transport = FakeTransport(rows=[{"signature": "ordinary", "timestamp": 19, "nativeTransfers": []}])
    result = B2YCreatorFundingProbe(transport).probe_once(B2YMember("mint", "migration"))
    assert result.outcome == "NO_PRE_MIGRATION_INBOUND_SOL_CANDIDATE"
    assert result.request_count == len(transport.calls) == len(result.projections) == 2
    assert result.candidate_signature is None


@pytest.mark.parametrize("funding", [
    proof(destination="other"),
    proof(source="other"),
    proof(lamports=11),
    proof(block_time=20),
])
def test_exact_transaction_proof_must_match_transfer_aware_projection(funding):
    result = B2YCreatorFundingProbe(FakeTransport(funding=funding)).probe_once(
        B2YMember("mint", "migration")
    )
    assert result.outcome == "CANDIDATE_PROOF_MISMATCH"
    assert result.request_count == 3
    assert result.projections[-1].lineage_valid is False


def test_empty_oldest_response_preserves_page_shape():
    result = B2YCreatorFundingProbe(FakeTransport(rows=[])).probe_once(B2YMember("mint", "migration"))
    assert result.projections[-1].page_row_count == 0
