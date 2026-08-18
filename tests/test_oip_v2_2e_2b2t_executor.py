import json
from pathlib import Path

import pytest

from src.acquisition.b2n_qualification import (
    B2NQualificationRunAuthorization,
    AppendOnlyLedger,
    B2NExecutor,
    B2NManifest,
    B2NMember,
    OneRequestResponse,
)


def manifest():
    return B2NManifest(tuple(B2NMember(i, f"mint-{i}", f"event-{i}", True) for i in range(1, 21)))


class Client:
    def __init__(self, outcome="SUCCESS"):
        self.calls = []
        self.outcome = outcome
        self.provider_request_count = 0
    def acquire_once(self, *, mint):
        self.calls.append(mint)
        request_count = 0 if self.outcome == "CACHE_HIT" else 1
        self.provider_request_count += request_count
        return OneRequestResponse(
            self.outcome, self.outcome == "SUCCESS", self.outcome == "SUCCESS",
            provider_signature="sig" if self.outcome == "SUCCESS" else None,
            provider_request_count=request_count,
        )


def test_exactly_one_call_per_frozen_member_and_append_only_ledger(tmp_path):
    client = Client(); ledger = AppendOnlyLedger(tmp_path / "ledger.jsonl")
    entries = B2NExecutor(manifest=manifest(), ledger=ledger, client=client, provider="helius", run_id="run").run()
    assert len(client.calls) == len(entries) == 20
    assert all(row["request_count"] == 1 for row in entries)
    assert [row["sample_ordinal"] for row in entries] == list(range(1, 21))
    assert len([json.loads(x) for x in (tmp_path / "ledger.jsonl").read_text().splitlines()]) == 20


def test_non_success_stops_without_retry(tmp_path):
    client = Client("TIMEOUT")
    entries = B2NExecutor(manifest=manifest(), ledger=AppendOnlyLedger(tmp_path / "ledger.jsonl"), client=client, provider="helius").run()
    assert len(client.calls) == len(entries) == 1
    assert entries[0]["request_outcome"] == "TIMEOUT"


def test_manifest_rejects_unmarked_or_non_twenty_members(tmp_path):
    with pytest.raises(ValueError, match="EXACTLY_20"):
        B2NExecutor(manifest=B2NManifest(()), ledger=AppendOnlyLedger(tmp_path / "x"), client=Client(), provider="helius")
    bad = list(manifest().members); bad[0] = B2NMember(1, "mint-1", "event-1", False)
    with pytest.raises(ValueError, match="NOT_MARKED"):
        B2NExecutor(manifest=B2NManifest(tuple(bad)), ledger=AppendOnlyLedger(tmp_path / "x"), client=Client(), provider="helius")


def test_existing_ledger_fails_closed(tmp_path):
    ledger = AppendOnlyLedger(tmp_path / "ledger.jsonl")
    ledger.append({"sample_ordinal": 1})
    with pytest.raises(RuntimeError, match="MUST_BE_EMPTY"):
        B2NExecutor(manifest=manifest(), ledger=ledger, client=Client(), provider="helius").run()


def test_frozen_b2r_manifest_has_stable_digest_and_qualifies_with_fake_client(tmp_path):
    payload = json.loads(Path("docs/evidence_platform/oip_v2_2e_2b2u_b2r_frozen_manifest.json").read_text())
    frozen = B2NManifest(tuple(B2NMember(**member) for member in payload["members"]))
    assert frozen.digest() == payload["manifest_digest"]
    client = Client()
    entries = B2NExecutor(manifest=frozen, ledger=AppendOnlyLedger(tmp_path / "ledger.jsonl"), client=client, provider="fake-helius").run()
    assert len(entries) == len(client.calls) == 20


def test_cache_hit_stops_before_success_claim(tmp_path):
    client = Client("CACHE_HIT")
    entries = B2NExecutor(manifest=manifest(), ledger=AppendOnlyLedger(tmp_path / "ledger.jsonl"), client=client, provider="helius").run()
    assert len(entries) == 1
    assert entries[0]["request_outcome"] == "CACHE_HIT"
    assert entries[0]["request_count"] == 0


def test_authorization_fails_missing_provider_requirements(tmp_path):
    manifest_value = manifest()
    auth_run_id = "run_invalid"
    bad = B2NQualificationRunAuthorization(
        provider="",
        endpoint_family="",
        run_id=auth_run_id,
        manifest_digest=manifest_value.digest(),
        ledger_path=str(tmp_path / "ledger.jsonl"),
    )
    with pytest.raises(ValueError, match="AUTH_PROVIDER_REQUIRED"):
        B2NExecutor(manifest=manifest_value, ledger=AppendOnlyLedger(tmp_path / "ledger.jsonl"), client=Client(), provider="helius", run_id=auth_run_id, authorization=bad).run()
    # legacy assertion moved with explicit run-id binding context


def test_authorization_catches_manifest_drift_and_counters(tmp_path):
    class DriftClient:
        def __init__(self):
            self.calls = 0
            self.provider_request_count = 0
        def acquire_once(self, *, mint):
            self.calls += 1
            self.provider_request_count += 2
            return OneRequestResponse("SUCCESS", True, True, provider_signature="sig", provider_request_count=2)

    client = DriftClient()
    bad_auth = B2NQualificationRunAuthorization(
        provider="helius",
        endpoint_family="helius-mainnet-json-rpc",
        run_id="run-1",
        manifest_digest=manifest().digest(),
        ledger_path=str(tmp_path / "ledger.jsonl"),
    )
    with pytest.raises(RuntimeError, match="COUNTER_INVALID"):
        B2NExecutor(
            manifest=manifest(),
            ledger=AppendOnlyLedger(tmp_path / "ledger.jsonl"),
            client=client,
            provider="helius",
            run_id=bad_auth.run_id,
            authorization=bad_auth,
        ).run()
