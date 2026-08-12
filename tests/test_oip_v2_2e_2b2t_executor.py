import json

import pytest

from src.acquisition.b2n_qualification import AppendOnlyLedger, B2NExecutor, B2NManifest, B2NMember, OneRequestResponse


def manifest():
    return B2NManifest(tuple(B2NMember(i, f"mint-{i}", f"event-{i}", True) for i in range(1, 21)))


class Client:
    def __init__(self, outcome="SUCCESS"):
        self.calls = []; self.outcome = outcome
    def acquire_once(self, *, mint):
        self.calls.append(mint)
        return OneRequestResponse(self.outcome, self.outcome == "SUCCESS", self.outcome == "SUCCESS", provider_signature="sig" if self.outcome == "SUCCESS" else None)


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
