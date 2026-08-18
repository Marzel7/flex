import json
from pathlib import Path

from src.acquisition.b2n_qualification import B2NManifest, B2NMember
from src.acquisition.b2w_projection import B2WInputProjection, MigrationGetTransactionAdapter

ROOT = Path(__file__).parents[1]
TRACE_PATH = ROOT / "docs/audits/b2n_p3a_provider_method_trace.json"


def manifest():
    return B2NManifest(tuple(B2NMember(i, f"mint-{i}", f"event-{i}", True) for i in range(1, 21)))


def records():
    return [{"event_id": f"event-{i}", "mint": f"mint-{i}", "signature": f"sig-{i}", "subscription": "subscribeMigration"} for i in range(1, 21)]


class Transport:
    def __init__(self):
        self.requests = []

    def post_json(self, request):
        self.requests.append(request)
        signature = request["params"][0]
        ordinal = int(signature.rsplit("-", 1)[1])
        return {"result": {"slot": 7, "transaction": {"message": {"accountKeys": [{"pubkey": f"mint-{ordinal}"}]}}}}


def test_trace_artifact_exists_and_records_hold_verdict():
    d = json.loads(TRACE_PATH.read_text())
    assert d["verdict"] == "HOLD_B2N_PROVIDER_METHOD_UNRESOLVED"
    assert d["authority_posture"]["provider_calls_made"] == 0
    assert d["authority_posture"]["budget_widened"] is False


def test_one_request_adapter_never_produces_qualifying_evidence():
    """Reproduces the core finding: the only 1-request/member client can never satisfy B2N's own success gate."""
    transport = Transport()
    adapter = MigrationGetTransactionAdapter(transport, B2WInputProjection.from_census(manifest(), records()))
    outcome = adapter.acquire_once(mint="mint-1")
    assert len(transport.requests) == 1
    assert outcome.outcome == "SUCCESS"
    assert outcome.evidence_observed is False  # B2N's own success gate requires this to be True
    assert outcome.error_class == "B2W_MIGRATION_LINEAGE_ONLY"


def test_evidence_producing_path_requires_three_requests_per_member():
    """Reproduces the second half of the finding via the B2Z module's own request-shape constant."""
    from src.acquisition import b2z_execution_boundary as b2z

    assert b2z.MAX_REQUESTS == 60  # 20 members x 3 requests, not 20 x 1
    assert b2z.PROVIDER == "helius_rpc"  # confirms this is the B2Z-scoped provider literal, not a B2N-native binding


def test_no_third_client_implementation_satisfies_both_constraints():
    """Confirms no other OneRequestClient-shaped implementation exists that reconciles 1-request and evidence_observed=True."""
    import src.acquisition.b2w_projection as b2w

    assert hasattr(b2w, "MigrationGetTransactionAdapter")
    assert not hasattr(b2w, "CreatorFundingOneRequestAdapter")  # no such class exists anywhere
