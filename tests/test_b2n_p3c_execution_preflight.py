import json
import os
import shutil
from pathlib import Path

import pytest

import scripts.run_b2n_p3c_migration_lineage_run as p3c

ROOT = Path(__file__).parents[1]


@pytest.fixture(autouse=True)
def _clean_ledger_dir():
    ledger_dir = ROOT.parent / "flex-b2n-p3c-ledger"
    if ledger_dir.exists():
        shutil.rmtree(ledger_dir)
    yield
    if ledger_dir.exists():
        shutil.rmtree(ledger_dir)


def test_dry_run_constructs_all_20_requests_with_zero_network_calls():
    result = p3c.dry_run()
    assert result["mode"] == "DRY_RUN"
    assert result["network_calls_made"] == 0
    assert result["constructed_request_count"] == 20
    assert result["constructed_requests_max_20"] is True
    assert result["one_request_per_member"] is True
    assert result["ledger_verified_empty"] is True
    assert result["authorization_digest_verified"] is True
    assert result["run_id_verified"] == p3c.EXPECTED_RUN_ID
    assert all(r["method"] == "getTransaction" for r in result["requests"])
    assert all(r["params_shape_valid"] for r in result["requests"])
    assert len({r["sample_ordinal"] for r in result["requests"]}) == 20


def test_dry_run_rejects_tampered_authorization_digest(tmp_path, monkeypatch):
    tampered = json.loads(p3c.SUCCESSOR_PREFLIGHT_PATH.read_text())
    tampered["run_id"] = "tampered"
    fake_path = tmp_path / "tampered_preflight.json"
    fake_path.write_text(json.dumps(tampered))
    monkeypatch.setattr(p3c, "SUCCESSOR_PREFLIGHT_PATH", fake_path)
    with pytest.raises(p3c.B2NP3CError, match="AUTHORIZATION_DIGEST_MISMATCH"):
        p3c.dry_run()


def test_live_run_refuses_without_credential_env_var(monkeypatch):
    monkeypatch.delenv(p3c.CREDENTIAL_ENV_VAR, raising=False)
    with pytest.raises(p3c.B2NP3CError, match="CREDENTIAL_ENV_VAR_MISSING"):
        p3c.live_run()


def test_credential_env_var_is_distinct_from_production_env_files():
    assert p3c.CREDENTIAL_ENV_VAR == "B2N_P3C_HELIUS_ENDPOINT"
    assert p3c.CREDENTIAL_ENV_VAR not in ("HELIUS_API_KEY", "HELIUS_RPC_URL")


def test_redacting_transport_never_exposes_endpoint_via_repr_or_str():
    transport = p3c.RedactingJsonRpcTransport(
        "https://mainnet.helius-rpc.com/?api-key=FAKE-CANARY-SECRET-DO-NOT-LEAK"
    )
    assert "FAKE-CANARY-SECRET-DO-NOT-LEAK" not in repr(transport)
    assert "FAKE-CANARY-SECRET-DO-NOT-LEAK" not in str(transport)
    public_attrs = {k: v for k, v in vars(transport).items() if not k.startswith("_")}
    assert "FAKE-CANARY-SECRET-DO-NOT-LEAK" not in json.dumps(public_attrs)


def test_redacting_transport_rejects_wrong_endpoint_prefix():
    with pytest.raises(p3c.B2NP3CError, match="ENDPOINT_PREFIX_MISMATCH"):
        p3c.RedactingJsonRpcTransport("https://wrong-provider.example.com/?api-key=x")


def test_transport_error_is_redacted_even_with_secret_in_url(monkeypatch):
    """Simulates a network failure WITHOUT making any real network call."""
    import urllib.error

    def fake_urlopen(*args, **kwargs):
        raise urllib.error.URLError(
            "simulated failure containing https://mainnet.helius-rpc.com/?api-key=FAKE-CANARY-SECRET-DO-NOT-LEAK"
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    transport = p3c.RedactingJsonRpcTransport(
        "https://mainnet.helius-rpc.com/?api-key=FAKE-CANARY-SECRET-DO-NOT-LEAK"
    )
    with pytest.raises(p3c.B2NP3CError) as exc_info:
        transport.post_json({"jsonrpc": "2.0", "id": 1, "method": "getTransaction", "params": ["sig", {}]})
    assert "FAKE-CANARY-SECRET-DO-NOT-LEAK" not in str(exc_info.value)
    assert str(exc_info.value) == "B2N_P3C_TRANSPORT_ERROR:URLError"


def test_build_projection_rejects_member_mismatch():
    manifest = p3c._load_frozen_manifest()
    reviewed = p3c._load_reviewed_binding()
    tampered_reviewed = json.loads(json.dumps(reviewed))
    tampered_reviewed["members"][0]["mint"] = "TAMPERED_MINT"
    with pytest.raises(p3c.B2NP3CError, match="REVIEWED_MEMBER_MISMATCH"):
        p3c.build_projection(manifest, tampered_reviewed)


def test_build_projection_rejects_non_approved_member():
    manifest = p3c._load_frozen_manifest()
    reviewed = p3c._load_reviewed_binding()
    tampered_reviewed = json.loads(json.dumps(reviewed))
    tampered_reviewed["members"][0]["human_approval_decision_record"]["decision"] = "DENIED"
    with pytest.raises(p3c.B2NP3CError, match="MEMBER_NOT_APPROVED"):
        p3c.build_projection(manifest, tampered_reviewed)


def test_provenance_closure_gate_enforced(monkeypatch):
    reviewed = p3c._load_reviewed_binding()
    tampered = json.loads(json.dumps(reviewed))
    tampered["closure_state"] = {"COMPLETE": 19, "PARTIAL": 1, "MISSING": 0, "CONFLICTING": 0}
    monkeypatch.setattr(p3c, "_load_reviewed_binding", lambda: tampered)
    with pytest.raises(p3c.B2NP3CError, match="PROVENANCE_CLOSURE_NOT_QUALIFIED"):
        p3c.dry_run()


def test_stale_ledger_fails_closed():
    ledger_dir = ROOT.parent / "flex-b2n-p3c-ledger"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    stale_path = ledger_dir / f"{p3c.EXPECTED_RUN_ID}.jsonl"
    stale_path.write_text('{"sample_ordinal": 1}\n')
    with pytest.raises(RuntimeError, match="MUST_BE_EMPTY"):
        p3c.dry_run()


def test_expected_authorization_constants_match_p3b_artifact():
    p = json.loads(p3c.SUCCESSOR_PREFLIGHT_PATH.read_text())
    assert p["run_id"] == p3c.EXPECTED_RUN_ID
    assert p["provider_endpoint_method_binding"]["provider"] == p3c.EXPECTED_PROVIDER
    assert p["provider_endpoint_method_binding"]["endpoint_family"] == p3c.EXPECTED_ENDPOINT_FAMILY
    assert p["provider_endpoint_method_binding"]["method"] == p3c.EXPECTED_METHOD
    assert p["request_budget"]["max_total_requests"] == 20
    assert p["request_budget"]["max_requests_per_member"] == 1
    assert p["request_budget"]["retry_budget"] == 0
    assert p["request_budget"]["pagination_budget"] == 0
    assert p["request_budget"]["fallback_budget"] == 0
