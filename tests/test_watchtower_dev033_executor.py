import copy
import json
from pathlib import Path

import pytest

from src.ops.token_data_provider_bindings import ProviderTransportOutcome
from src.ops.watchtower_dev033_executor import (
    JOURNAL_SCHEMA,
    MAX_REQUESTS,
    _canon,
    _sha,
    _normalize,
    execute,
    load_manifest,
)


def _manifest(path: Path, count: int = MAX_REQUESTS, *, window: int = 120) -> dict:
    records = []
    for ordinal in range(count):
        mint = f"fixture-mint-{ordinal}"
        start = 1_800_000_000 + ordinal * 600
        request_window = {
            "mint": mint, "interval": "1m", "time_from": start, "time_to": start + window,
            "chart_type": "mcap", "currency": "usd", "mode": "range", "padding": False, "x_chain": "solana",
        }
        records.append({
            "mint": mint, "opening_timestamp": start, "observed_peak_timestamp": start + 60,
            "request_window": request_window,
            "request_identity": _sha({"schema": "DEV033_WATCHTOWER_CHRONOLOGICAL_PILOT_MANIFEST_V1", **request_window,
                                      "opening_timestamp": start, "observed_peak_timestamp": start + 60}),
        })
    document = {"schema": "DEV033_WATCHTOWER_CHRONOLOGICAL_PILOT_MANIFEST_V1", "records": records}
    document["content_hash"] = _sha(document)
    path.write_text(_canon(document) + "\n")
    return document


def _env(path: Path) -> None:
    path.write_text("BIRDEYE=fixture-not-a-real-credential\n")


def _clock():
    value = [0.0]
    return lambda: value[0], lambda seconds: value.__setitem__(0, value[0] + seconds)


def _payload(request, *, sparse=False):
    p = request["request_parameters"]
    stamps = list(range(p["time_from"], p["time_to"], 60))
    if sparse:
        stamps = stamps[::2]
    return {"data": {"items": [
        {"unixTime": stamp, "o": 10, "h": 12, "l": 8, "c": 11} for stamp in reversed(stamps)
    ]}}


class _Binding:
    def __init__(self, *, status=200, sparse=False):
        self.calls = []
        self.status = status
        self.sparse = sparse

    def __call__(self, request):
        self.calls.append(request)
        return ProviderTransportOutcome(self.status, _payload(request, sparse=self.sparse) if self.status == 200 else None, {},
                                        None if self.status == 200 else f"HTTP_{self.status}")


def test_real_frozen_manifest_matches_expected_file_hash():
    path = Path("docs/audits/dev033_chronological_backtest_pilot_manifest_20261013.v1.json")
    assert __import__("hashlib").sha256(path.read_bytes()).hexdigest() == "f5a8a2289bb369b95b44896fc6d7f7db3bc79c890a3bba1b0fcd3ec32f3662d3"
    assert len(load_manifest(path)["records"]) == 30


def test_manifest_mismatch_duplicate_and_request_31_are_rejected(tmp_path):
    path = tmp_path / "manifest.json"
    document = _manifest(path)
    changed = copy.deepcopy(document)
    changed["records"][0]["mint"] = "substituted"
    changed["content_hash"] = _sha({key: value for key, value in changed.items() if key != "content_hash"})
    path.write_text(_canon(changed))
    with pytest.raises(RuntimeError, match="MANIFEST_RECORD_INVALID"):
        load_manifest(path)
    _manifest(path, count=31)
    with pytest.raises(RuntimeError, match="RECORD_COUNT"):
        load_manifest(path)


def test_thirty_serial_requests_use_real_budget_pacing_and_ordered_evidence(tmp_path):
    manifest = tmp_path / "manifest.json"
    _manifest(manifest)
    env = tmp_path / ".env"
    _env(env)
    clock, sleep = _clock()
    binding = _Binding()
    journal = execute(manifest_path=manifest, journal_path=tmp_path / "journal.json", budget_root=tmp_path / "budget",
                      env_path=env, binding_factory=lambda: binding, now=clock, sleep=sleep, execution_authorized=True)
    assert len(binding.calls) == MAX_REQUESTS
    assert all(record["state"] == "ORDERED_CONTIGUOUS" for record in journal["records"].values())
    assert all(record["provider_requests"] == 1 for record in journal["records"].values())
    assert all(record["evidence"]["candles"][0]["timestamp"] < record["evidence"]["candles"][1]["timestamp"] for record in journal["records"].values())
    assert clock() == 174
    ledger = json.loads((tmp_path / "budget" / "provider_budget.json").read_text())
    assert len(ledger["calls"]) <= 10


def test_http_429_is_terminal_and_never_retried(tmp_path):
    manifest = tmp_path / "manifest.json"
    _manifest(manifest)
    env = tmp_path / ".env"
    _env(env)
    binding = _Binding(status=429)
    clock, sleep = _clock()
    journal_path = tmp_path / "journal.json"
    journal = execute(manifest_path=manifest, journal_path=journal_path, budget_root=tmp_path / "budget", env_path=env,
                      binding_factory=lambda: binding, now=clock, sleep=sleep, execution_authorized=True)
    assert len(binding.calls) == 30
    assert all(record["state"] == "INSUFFICIENT_EVIDENCE" for record in journal["records"].values())
    assert all(record["provider_requests"] == 1 for record in journal["records"].values())
    repeat = _Binding(status=200)
    execute(manifest_path=manifest, journal_path=journal_path, budget_root=tmp_path / "budget", env_path=env,
            binding_factory=lambda: repeat, now=clock, sleep=sleep, execution_authorized=True)
    assert repeat.calls == []


def test_sparse_gap_and_uncertain_restart_are_excluded_without_transport(tmp_path):
    manifest = tmp_path / "manifest.json"
    document = _manifest(manifest, window=240)
    env = tmp_path / ".env"
    _env(env)
    records = {record["request_identity"]: {"mint": record["mint"], "state": "ORDERED_CONTIGUOUS", "provider_requests": 1}
               for record in document["records"]}
    first = document["records"][0]["request_identity"]
    records[first] = {"mint": document["records"][0]["mint"], "state": "REQUEST_IN_FLIGHT", "provider_requests": 0}
    journal_path = tmp_path / "journal.json"
    journal_path.write_text(_canon({"schema": JOURNAL_SCHEMA, "manifest_content_hash": document["content_hash"], "records": records}))
    clock, sleep = _clock()
    binding = _Binding(sparse=True)
    journal = execute(manifest_path=manifest, journal_path=journal_path, budget_root=tmp_path / "budget", env_path=env,
                      binding_factory=lambda: binding, now=clock, sleep=sleep, execution_authorized=True)
    assert binding.calls == []
    assert journal["records"][first]["state"] == "OUTCOME_UNKNOWN"


def test_budget_denial_restores_pending_before_transport(tmp_path):
    manifest = tmp_path / "manifest.json"
    document = _manifest(manifest)
    env = tmp_path / ".env"
    _env(env)
    clock, sleep = _clock()
    budget_root = tmp_path / "budget"
    from src.ops.dev_provider_budget import DevProviderBudget
    for index in range(10):
        DevProviderBudget(budget_root, now=clock).admit(f"live-{index}", "LIVE", global_limit=10)
    binding = _Binding()
    journal_path = tmp_path / "journal.json"
    with pytest.raises(Exception, match="GLOBAL_PROVIDER_BUDGET_EXHAUSTED"):
        execute(manifest_path=manifest, journal_path=journal_path, budget_root=budget_root, env_path=env,
                binding_factory=lambda: binding, now=clock, sleep=sleep, execution_authorized=True)
    state = json.loads(journal_path.read_text())["records"][document["records"][0]["request_identity"]]
    assert state["state"] == "PENDING"
    assert state["provider_requests"] == 0
    assert binding.calls == []


def test_sparse_normalization_has_ordered_candles_and_explicit_gap():
    outcome = ProviderTransportOutcome(200, {"data": {"items": [
        {"unixTime": 180, "o": 10, "h": 12, "l": 8, "c": 11},
        {"unixTime": 60, "o": 10, "h": 12, "l": 8, "c": 11},
    ]}}, {})
    result = _normalize(outcome, "mint", {"time_from": 60, "time_to": 240}, "identity")
    assert result["state"] == "ORDERED_SPARSE"
    assert [row["timestamp"] for row in result["evidence"]["candles"]] == [60, 180]
    assert result["evidence"]["missing_interval_count"] == 1
    assert result["evidence"]["largest_gap_seconds"] == 60


def test_explicit_authorization_is_required_before_credential_or_budget_use(tmp_path):
    manifest = tmp_path / "manifest.json"
    _manifest(manifest)
    with pytest.raises(RuntimeError, match="EXECUTION_AUTHORIZATION_REQUIRED"):
        execute(manifest_path=manifest, journal_path=tmp_path / "journal.json", budget_root=tmp_path / "budget",
                env_path=tmp_path / "missing.env")
