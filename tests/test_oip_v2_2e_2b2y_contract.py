from src.acquisition.b2y_creator_funding_contract import B2YCreatorFundingProbe, B2YMember


class FakeTransport:
    def __init__(self, signatures): self.calls = []; self.signatures = signatures
    def get_transaction(self, signature):
        self.calls.append(("getTransaction", signature))
        if signature == "migration":
            return {"blockTime": 20, "transaction": {"message": {"accountKeys": [{"pubkey": "mint", "signer": False}, {"pubkey": "creator", "signer": True}]}}}
        return {"result": {"transaction": {}}}
    def get_signatures_for_address(self, address, *, limit):
        self.calls.append(("getSignaturesForAddress", address, limit))
        return self.signatures


def test_three_request_sequence_is_exact_and_ordered():
    transport = FakeTransport([{"signature": "funding", "blockTime": 19}])
    result = B2YCreatorFundingProbe(transport).probe_once(B2YMember("mint", "migration"))
    assert result.outcome == "SUCCESS"
    assert result.request_count == len(transport.calls) == 3
    assert [call[0] for call in transport.calls] == ["getTransaction", "getSignaturesForAddress", "getTransaction"]
    assert transport.calls[1][2] == 1000


def test_no_candidate_stops_after_two_without_third_request():
    transport = FakeTransport([{"signature": "later", "blockTime": 20}])
    result = B2YCreatorFundingProbe(transport).probe_once(B2YMember("mint", "migration"))
    assert result.outcome == "NO_PRE_MIGRATION_CANDIDATE"
    assert result.request_count == len(transport.calls) == 2
