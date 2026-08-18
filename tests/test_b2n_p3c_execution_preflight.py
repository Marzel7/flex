import json
from pathlib import Path

import pytest

import scripts.run_b2n_p3c_migration_lineage_run as p3c

ROOT = Path(__file__).parents[1]


def test_default_ledger_and_result_paths_are_inside_repo_docs_audits():
    assert p3c.DEFAULT_LEDGER_PATH.parent == ROOT / "docs/audits"
    assert p3c.DEFAULT_RESULT_PATH.parent == ROOT / "docs/audits"
    assert "/private/tmp" not in str(p3c.DEFAULT_LEDGER_PATH)
    assert "/private/tmp" not in str(p3c.DEFAULT_RESULT_PATH)
    assert p3c.EXPECTED_RUN_ID in p3c.DEFAULT_LEDGER_PATH.name


def test_dry_run_constructs_all_20_requests_with_zero_network_calls(tmp_path):
    ledger_path = tmp_path / "ledger.jsonl"
    result = p3c.dry_run(ledger_path=ledger_path)
    assert result["mode"] == "DRY_RUN"
    assert result["network_calls_made"] == 0
    assert result["constructed_request_count"] == 20
    assert result["constructed_requests_max_20"] is True
    assert result["one_request_per_member"] is True
    assert result["ledger_verified_empty"] is True
    assert result["authorization_digest_verified"] is True
    assert result["run_id_verified"] == p3c.EXPECTED_RUN_ID
    assert result["ledger_path"] == str(ledger_path.resolve())
    assert all(r["method"] == "getTransaction" for r in result["requests"])
    assert all(r["params_shape_valid"] for r in result["requests"])
    assert len({r["sample_ordinal"] for r in result["requests"]}) == 20
    # dry-run must not create the ledger file itself
    assert not ledger_path.exists()


def test_dry_run_uses_default_durable_path_when_unspecified():
    if p3c.DEFAULT_LEDGER_PATH.exists():
        p3c.DEFAULT_LEDGER_PATH.unlink()
    result = p3c.dry_run()
    assert result["ledger_path"] == str(p3c.DEFAULT_LEDGER_PATH.resolve())
    assert not p3c.DEFAULT_LEDGER_PATH.exists()


def test_ledger_path_cli_override(tmp_path):
    override = tmp_path / "custom_ledger.jsonl"
    result = p3c.dry_run(ledger_path=override)
    assert result["ledger_path"] == str(override.resolve())


def test_writable_parent_preflight_creates_missing_parent(tmp_path):
    nested = tmp_path / "does" / "not" / "exist" / "ledger.jsonl"
    ledger = p3c._verify_ledger_readiness(nested)
    assert nested.parent.exists()
    assert ledger.entries() == []
    # no leftover writability-probe files
    assert list(nested.parent.glob(".b2n_p3c_writability_probe_*")) == []


def test_non_writable_ledger_parent_fails_closed(tmp_path):
    readonly_parent = tmp_path / "readonly"
    readonly_parent.mkdir()
    readonly_parent.chmod(0o500)  # r-x, not writable
    try:
        with pytest.raises(p3c.B2NP3CError, match="LEDGER_PARENT_NOT_WRITABLE"):
            p3c._verify_ledger_readiness(readonly_parent / "ledger.jsonl")
    finally:
        readonly_parent.chmod(0o700)  # restore so tmp_path cleanup can remove it


def test_prior_attempt_ledger_fails_closed(tmp_path):
    ledger_path = tmp_path / "ledger.jsonl"
    ledger_path.write_text('{"sample_ordinal": 1, "run_id": "foreign-run"}\n')
    with pytest.raises(RuntimeError, match="MUST_BE_EMPTY"):
        p3c._verify_ledger_readiness(ledger_path)


def test_dry_run_rejects_tampered_authorization_digest(tmp_path, monkeypatch):
    tampered = json.loads(p3c.SUCCESSOR_PREFLIGHT_PATH.read_text())
    tampered["run_id"] = "tampered"
    fake_path = tmp_path / "tampered_preflight.json"
    fake_path.write_text(json.dumps(tampered))
    monkeypatch.setattr(p3c, "SUCCESSOR_PREFLIGHT_PATH", fake_path)
    with pytest.raises(p3c.B2NP3CError, match="AUTHORIZATION_DIGEST_MISMATCH"):
        p3c.dry_run(ledger_path=tmp_path / "ledger.jsonl")


def test_live_run_refuses_without_credential_env_var(monkeypatch, tmp_path):
    monkeypatch.delenv(p3c.CREDENTIAL_ENV_VAR, raising=False)
    with pytest.raises(p3c.B2NP3CError, match="CREDENTIAL_ENV_VAR_MISSING"):
        p3c.live_run(ledger_path=tmp_path / "ledger.jsonl")


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


def test_provenance_closure_gate_enforced(monkeypatch, tmp_path):
    reviewed = p3c._load_reviewed_binding()
    tampered = json.loads(json.dumps(reviewed))
    tampered["closure_state"] = {"COMPLETE": 19, "PARTIAL": 1, "MISSING": 0, "CONFLICTING": 0}
    monkeypatch.setattr(p3c, "_load_reviewed_binding", lambda: tampered)
    with pytest.raises(p3c.B2NP3CError, match="PROVENANCE_CLOSURE_NOT_QUALIFIED"):
        p3c.dry_run(ledger_path=tmp_path / "ledger.jsonl")


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


# --- Part 8: failure durability -------------------------------------------

def test_ledger_entries_durable_after_simulated_mid_run_failure(tmp_path):
    """member 1 succeeds, member 2 raises a transport-style exception (e.g. a
    counter-integrity violation) -- the entry for member 1 must already be
    durable on disk when the exception propagates, because
    B2NAttemptLedger.append() writes synchronously inside B2NExecutor.run()'s
    per-member loop rather than buffering until the run completes. This uses
    a raising client (rather than a non-qualifying outcome) because
    B2NExecutor.run() stops CLEANLY (no exception, partial results returned)
    on outcome!=SUCCESS or evidence_observed=False -- that is the real,
    already-qualified stop path exercised by MigrationGetTransactionAdapter
    itself and covered by test_frozen_b2r_manifest_has_stable_digest_and_qualifies_with_fake_client
    and friends. This test instead covers the EXCEPTION path (counter
    mismatch / raising client), which is the scenario where durability across
    an abrupt failure actually matters."""
    from src.acquisition.b2n_qualification import (
        B2NAttemptLedger,
        B2NExecutor,
        B2NQualificationRunAuthorization,
        OneRequestResponse,
    )

    manifest = p3c._load_frozen_manifest()

    class FlakyClient:
        def __init__(self):
            self.calls = 0
            self.provider_request_count = 0

        def acquire_once(self, *, mint):
            self.calls += 1
            if self.calls == 1:
                self.provider_request_count += 1
                return OneRequestResponse("SUCCESS", True, True, provider_signature="sig", provider_request_count=1)
            # Simulate a client whose internal counter desyncs on the second
            # call -- B2NExecutor.run() raises B2N_CLIENT_REQUEST_COUNTER_INVALID
            # for exactly this condition (delta not in {0, 1}).
            self.provider_request_count += 3
            return OneRequestResponse("SUCCESS", True, True, provider_signature="sig", provider_request_count=3)

    ledger_path = tmp_path / "durability_ledger.jsonl"
    ledger = p3c._verify_ledger_readiness(ledger_path)
    auth = B2NQualificationRunAuthorization(
        provider=p3c.EXPECTED_PROVIDER,
        endpoint_family=p3c.EXPECTED_ENDPOINT_FAMILY,
        run_id=p3c.EXPECTED_RUN_ID,
        manifest_digest=manifest.digest(),
        ledger_path=str(ledger_path),
    )
    executor = B2NExecutor(
        manifest=manifest, ledger=ledger, client=FlakyClient(),
        provider=p3c.EXPECTED_PROVIDER, run_id=p3c.EXPECTED_RUN_ID, authorization=auth,
    )
    with pytest.raises(RuntimeError, match="B2N_CLIENT_REQUEST_COUNTER_INVALID"):
        executor.run()

    durable_entries = B2NAttemptLedger(ledger_path).entries()
    assert len(durable_entries) == 1
    assert durable_entries[0]["sample_ordinal"] == 1
    assert durable_entries[0]["request_count"] == 1


def test_rerun_after_partial_failure_does_not_reset_ledger(tmp_path):
    """Part 9: a second invocation against a ledger with existing attempts
    must fail closed, never silently restart from zero."""
    ledger_path = tmp_path / "partial_ledger.jsonl"
    from src.acquisition.b2n_qualification import B2NAttemptLedger

    manifest = p3c._load_frozen_manifest()
    ledger = B2NAttemptLedger(ledger_path)
    ledger.append({
        "contract_version": p3c.EXPECTED_METHOD, "run_id": p3c.EXPECTED_RUN_ID,
        "manifest_digest": manifest.digest(), "sample_ordinal": 1, "mint": "x",
        "observation_required": True, "provider": "helius", "request_count": 1,
        "request_outcome": "SUCCESS", "request_started_utc_ns": 1, "response_received_utc_ns": 2,
        "elapsed_monotonic_ns": 1, "evidence_observed": False, "provenance_complete": True,
    })

    with pytest.raises(RuntimeError, match="MUST_BE_EMPTY"):
        p3c._verify_ledger_readiness(ledger_path)

    # the prior attempt must still be there -- not wiped by the failed readiness check
    assert len(B2NAttemptLedger(ledger_path).entries()) == 1


def test_cumulative_budget_cannot_be_bypassed_via_rerun(tmp_path):
    """A rerun must never permit 20 NEW requests on top of a ledger that
    already recorded prior attempts under the same run ID -- it must fail
    closed before any of those new requests could be issued."""
    ledger_path = tmp_path / "cumulative_ledger.jsonl"
    from src.acquisition.b2n_qualification import B2NAttemptLedger

    manifest = p3c._load_frozen_manifest()
    ledger = B2NAttemptLedger(ledger_path)
    for ordinal in (1, 2, 3):
        ledger.append({
            "contract_version": p3c.EXPECTED_METHOD, "run_id": p3c.EXPECTED_RUN_ID,
            "manifest_digest": manifest.digest(), "sample_ordinal": ordinal, "mint": f"mint-{ordinal}",
            "observation_required": True, "provider": "helius", "request_count": 1,
            "request_outcome": "SUCCESS", "request_started_utc_ns": 1, "response_received_utc_ns": 2,
            "elapsed_monotonic_ns": 1, "evidence_observed": False, "provenance_complete": True,
        })

    with pytest.raises(RuntimeError, match="MUST_BE_EMPTY"):
        p3c.dry_run(ledger_path=ledger_path)

    # exactly the 3 pre-existing entries remain; nothing was appended, nothing was reset
    assert len(B2NAttemptLedger(ledger_path).entries()) == 3


# --- result path behavior ---------------------------------------------------

def test_default_result_path_is_durable_repo_location():
    assert p3c.DEFAULT_RESULT_PATH == ROOT / "docs/audits" / "b2n_p3c_live_result.json"
