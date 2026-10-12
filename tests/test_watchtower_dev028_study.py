import json
import sqlite3
from pathlib import Path

from src.ops.token_data_provider_bindings import ProviderTransportOutcome
from src.ops.watchtower_dev028_study import execute, execute_single_corrected_proof, initialize_journal, prepare_corrected_continuation_manifest, prepare_manifest


def _fixture_sources(root: Path):
    db = root / "prepeak.db"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE dev020_prepeak_tokens(mint TEXT, selection_json TEXT, state TEXT)")
        for index in range(10):
            mint = f"FixtureMint{index:02d}"
            selection = {"opening_market_cap": 100000 + index, "opening_timestamp": 1_800_000_000 + index * 900,
                         "observed_peak_market_cap": (100000 + index) * (1.1 + index / 10), "observed_peak_timestamp": 1_800_000_900 + index * 900}
            con.execute("INSERT INTO dev020_prepeak_tokens VALUES(?,?,?)", (mint, json.dumps(selection), "OBSERVED_CONTIGUOUS"))
    lifecycle = root / "lifecycle"
    for index in range(10):
        path = lifecycle / "journals" / f"{index:064x}" / "journal.json"
        path.parent.mkdir(parents=True)
        peak = 1_800_000_900 + index * 900
        path.write_text(json.dumps({"mint": f"FixtureMint{index:02d}", "outcome": "COMPLETED", "rank": index + 1,
                                    "lifecycle_metrics": {"last_observed_timestamp": peak + 9000, "coverage_confidence": "PARTIAL_OBSERVED_WINDOW",
                                                          "peak_to_subsequent_trough": {"drawdown_percent": 90.0}}}))
    return db, lifecycle


def _outcome(request):
    start, end = request["request_parameters"]["time_from"], request["request_parameters"]["time_to"]
    rows = []
    for timestamp in range(start, end, 30):
        rows.append({"unixTime": timestamp, "o": 100.0, "h": 100.0, "l": 100.0, "c": 100.0})
    return ProviderTransportOutcome(200, {"success": True, "data": {"items": rows}}, {"x-ratelimit-limit": "300"})


def test_prepares_ten_stable_records_and_executes_with_real_budget(tmp_path):
    db, lifecycle = _fixture_sources(tmp_path)
    state = tmp_path / "state"
    manifest = prepare_manifest(prepeak_db=db, lifecycle_root=lifecycle, destination=state / "manifest.json")
    assert len(manifest["records"]) == 10
    journal = initialize_journal(manifest=manifest, destination=state / "journal.json")
    assert {record["state"] for record in journal["records"].values()} == {"PENDING"}
    (tmp_path / ".env").write_text("BIRDEYE=synthetic\n")
    budget_root = tmp_path / "budget"
    budget_root.mkdir()
    (budget_root / "provider_budget.json").write_text('{"calls":[],"version":1}\n')
    clock = [1_900_000_000.0]
    result = execute(manifest_path=state / "manifest.json", journal_path=state / "journal.json", budget_root=budget_root,
                     env_path=tmp_path / ".env", binding_factory=lambda: _outcome,
                     now=lambda: clock[0], sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    assert {record["state"] for record in result["records"].values()} == {"NO_85_PERCENT_CANDLE_OBSERVED"}
    assert sum(record["provider_requests"] for record in result["records"].values()) == 10
    calls = json.loads((budget_root / "provider_budget.json").read_text())["calls"]
    assert len(calls) == 10


def test_post_admission_state_is_never_retried(tmp_path):
    db, lifecycle = _fixture_sources(tmp_path)
    state = tmp_path / "state"
    manifest = prepare_manifest(prepeak_db=db, lifecycle_root=lifecycle, destination=state / "manifest.json")
    journal = initialize_journal(manifest=manifest, destination=state / "journal.json")
    record = next(iter(journal["records"].values()))
    record.update({"state": "ATTEMPTED", "provider_requests": 1})
    (state / "journal.json").write_text(json.dumps(journal))
    (tmp_path / ".env").write_text("BIRDEYE=synthetic\n")
    budget_root = tmp_path / "budget"; budget_root.mkdir(); (budget_root / "provider_budget.json").write_text('{"calls":[],"version":1}\n')
    result = execute(manifest_path=state / "manifest.json", journal_path=state / "journal.json", budget_root=budget_root,
                     env_path=tmp_path / ".env", binding_factory=lambda: _outcome, sleep=lambda _: None)
    assert any(row["state"] == "OUTCOME_UNKNOWN" for row in result["records"].values())
    assert sum(row["provider_requests"] for row in result["records"].values()) == 10


def test_invalid_candle_persists_only_bounded_diagnostic(tmp_path):
    db, lifecycle = _fixture_sources(tmp_path)
    state = tmp_path / "state"
    manifest = prepare_manifest(prepeak_db=db, lifecycle_root=lifecycle, destination=state / "manifest.json")
    initialize_journal(manifest=manifest, destination=state / "journal.json")
    (tmp_path / ".env").write_text("BIRDEYE=synthetic\n")
    budget_root = tmp_path / "budget"; budget_root.mkdir(); (budget_root / "provider_budget.json").write_text('{"calls":[],"version":1}\n')

    def invalid(request):
        response = _outcome(request)
        response.payload["data"]["items"][0]["l"] = 0
        return response

    result = execute(manifest_path=state / "manifest.json", journal_path=state / "journal.json", budget_root=budget_root,
                     env_path=tmp_path / ".env", binding_factory=lambda: invalid, sleep=lambda _: None)
    diagnostic = next(iter(result["records"].values()))["normalization_diagnostic"]
    assert diagnostic["failure_category"] == "INVALID_OHLC_VALUE"
    assert diagnostic["field_path"] == "item.low"
    assert "payload" not in diagnostic and "items" not in diagnostic


def test_single_corrected_proof_is_separate_from_original_outcome(tmp_path):
    db, lifecycle = _fixture_sources(tmp_path)
    state = tmp_path / "state"
    manifest = prepare_manifest(prepeak_db=db, lifecycle_root=lifecycle, destination=state / "manifest.json")
    original = initialize_journal(manifest=manifest, destination=state / "original.json")
    original_record = next(iter(original["records"].values()))
    original_record["state"] = "INSUFFICIENT_EVIDENCE"
    (state / "original.json").write_text(json.dumps(original))
    (tmp_path / ".env").write_text("BIRDEYE=synthetic\n")
    budget_root = tmp_path / "budget"; budget_root.mkdir(); (budget_root / "provider_budget.json").write_text('{"calls":[],"version":1}\n')
    proof = execute_single_corrected_proof(manifest_path=state / "manifest.json", original_journal_path=state / "original.json",
                                           proof_journal_path=state / "proof.json", budget_root=budget_root, env_path=tmp_path / ".env",
                                           binding_factory=lambda: _outcome)
    assert proof["provider_requests"] == 1
    assert proof["state"] == "NO_85_PERCENT_CANDLE_OBSERVED"
    assert json.load(open(state / "original.json"))["records"][manifest["records"][0]["request_identity"]]["state"] == "INSUFFICIENT_EVIDENCE"


def test_continuation_manifest_excludes_only_the_completed_proof(tmp_path):
    db, lifecycle = _fixture_sources(tmp_path)
    state = tmp_path / "state"
    manifest = prepare_manifest(prepeak_db=db, lifecycle_root=lifecycle, destination=state / "manifest.json")
    original = initialize_journal(manifest=manifest, destination=state / "original.json")
    original_record = next(iter(original["records"].values())); original_record["state"] = "INSUFFICIENT_EVIDENCE"
    (state / "original.json").write_text(json.dumps(original))
    (tmp_path / ".env").write_text("BIRDEYE=synthetic\n")
    budget_root = tmp_path / "budget"; budget_root.mkdir(); (budget_root / "provider_budget.json").write_text('{"calls":[],"version":1}\n')
    proof = execute_single_corrected_proof(manifest_path=state / "manifest.json", original_journal_path=state / "original.json",
                                           proof_journal_path=state / "proof.json", budget_root=budget_root, env_path=tmp_path / ".env",
                                           binding_factory=lambda: _outcome)
    continuation = prepare_corrected_continuation_manifest(original_manifest_path=state / "manifest.json", proof_journal_path=state / "proof.json", destination=state / "continuation.json")
    assert continuation["maximum_requests"] == 9
    assert proof["mint"] not in {record["mint"] for record in continuation["records"]}
