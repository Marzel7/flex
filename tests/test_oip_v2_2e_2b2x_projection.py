import pytest

from src.acquisition.b2n_qualification import B2NManifest, B2NMember
from src.acquisition.b2w_projection import B2WInputProjection, MigrationGetTransactionAdapter


def manifest():
    return B2NManifest(tuple(B2NMember(i, f"mint-{i}", f"event-{i}", True) for i in range(1, 21)))


def records():
    return [{"event_id": f"event-{i}", "mint": f"mint-{i}", "signature": f"sig-{i}", "subscription": "subscribeMigration"} for i in range(1, 21)]


class Transport:
    def __init__(self): self.requests = []
    def post_json(self, request):
        self.requests.append(request)
        signature = request["params"][0]
        ordinal = int(signature.rsplit("-", 1)[1])
        return {"result": {"slot": 7, "transaction": {"message": {"accountKeys": [{"pubkey": f"mint-{ordinal}"}]}}}}


def test_projection_requires_exact_migration_signature_lineage():
    projection = B2WInputProjection.from_census(manifest(), records())
    assert len(projection.members) == 20
    broken = records(); broken[0].pop("signature")
    with pytest.raises(ValueError, match="SIGNATURE_NOT_FROZEN"):
        B2WInputProjection.from_census(manifest(), broken)


def test_adapter_issues_one_exact_get_transaction_and_never_claims_funding_evidence():
    transport = Transport()
    adapter = MigrationGetTransactionAdapter(transport, B2WInputProjection.from_census(manifest(), records()))
    outcome = adapter.acquire_once(mint="mint-1")
    assert len(transport.requests) == 1
    assert transport.requests[0]["method"] == "getTransaction"
    assert transport.requests[0]["params"][0] == "sig-1"
    assert outcome.outcome == "SUCCESS"
    assert outcome.provenance_complete is True
    assert outcome.evidence_observed is False
    assert outcome.error_class == "B2W_MIGRATION_LINEAGE_ONLY"


def test_unknown_mint_cannot_trigger_a_request():
    transport = Transport()
    adapter = MigrationGetTransactionAdapter(transport, B2WInputProjection.from_census(manifest(), records()))
    with pytest.raises(ValueError, match="NOT_IN_FROZEN"):
        adapter.acquire_once(mint="other")
    assert transport.requests == []
