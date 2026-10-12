import json
import sqlite3
from pathlib import Path

from src.ops.token_data_provider_bindings import ProviderTransportOutcome
from src.ops.watchtower_dev028_study import _selection_rows, _sha, execute, execute_single_corrected_proof, initialize_journal, prepare_corrected_continuation_manifest, prepare_dev031_manifest, prepare_manifest


def _fixture_sources(root: Path, count: int = 10):
    db = root / "prepeak.db"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE dev020_prepeak_tokens(mint TEXT, selection_json TEXT, state TEXT)")
        for index in range(count):
            mint = f"FixtureMint{index:02d}"
            selection = {"opening_market_cap": 100000 + index, "opening_timestamp": 1_800_000_000 + index * 900,
                         "observed_peak_market_cap": (100000 + index) * (1.1 + index / 10), "observed_peak_timestamp": 1_800_000_900 + index * 900,
                         "baseline_type": "FIRST_FULL_POST_MIGRATION_SECOND_MC" if index == 1 else "FIRST_AVAILABLE_POST_MIGRATION_1M_OPEN_MC"}
            con.execute("INSERT INTO dev020_prepeak_tokens VALUES(?,?,?)", (mint, json.dumps(selection), "OBSERVED_CONTIGUOUS"))
    lifecycle = root / "lifecycle"
    for index in range(count):
        path = lifecycle / "journals" / f"{index:064x}" / "journal.json"
        path.parent.mkdir(parents=True)
        peak = 1_800_000_900 + index * 900
        path.write_text(json.dumps({"mint": f"FixtureMint{index:02d}", "outcome": "COMPLETED", "rank": index + 1,
                                    "lifecycle_metrics": {"last_observed_timestamp": peak + 9000, "coverage_confidence": "PARTIAL_OBSERVED_WINDOW",
                                                          "peak_to_subsequent_trough": {"drawdown_percent": 90.0}}}))
    return db, lifecycle


def test_close_only_monitor_fallback_cannot_qualify_as_an_opening_baseline(tmp_path):
    db, lifecycle = _fixture_sources(tmp_path, count=11)
    with sqlite3.connect(db) as con:
        row = con.execute("SELECT selection_json FROM dev020_prepeak_tokens WHERE mint='FixtureMint10'").fetchone()
        selection = json.loads(row[0])
        selection["baseline_type"] = "FIRST_RETAINED_MONITOR_CANDLE"
        con.execute("UPDATE dev020_prepeak_tokens SET selection_json=? WHERE mint='FixtureMint10'", (json.dumps(selection),))

    rows = _selection_rows(db, lifecycle)

    assert len(rows) == 10
    assert "FixtureMint10" not in {row["mint"] for row in rows}
    assert {row["opening_baseline_type"] for row in rows} == {
        "FIRST_AVAILABLE_POST_MIGRATION_1M_OPEN_MC", "FIRST_FULL_POST_MIGRATION_SECOND_MC"
    }


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


def test_dev031_twenty_record_manifest_is_deterministic_and_executes_with_real_budget(tmp_path):
    db, lifecycle = _fixture_sources(tmp_path, count=24)
    state = tmp_path / "state"
    first = prepare_dev031_manifest(prepeak_db=db, lifecycle_root=lifecycle, destination=state / "first.json")
    second = prepare_dev031_manifest(prepeak_db=db, lifecycle_root=lifecycle, destination=state / "second.json")
    assert first["content_hash"] == second["content_hash"]
    assert len(first["records"]) == 20
    assert len({row["request_identity"] for row in first["records"]}) == 20
    (tmp_path / ".env").write_text("BIRDEYE=synthetic\n")
    budget_root = tmp_path / "budget"; budget_root.mkdir(); (budget_root / "provider_budget.json").write_text('{"calls":[],"version":1}\n')
    clock = [1_900_000_000.0]
    result = execute(manifest_path=state / "first.json", journal_path=state / "journal.json", budget_root=budget_root,
                     env_path=tmp_path / ".env", binding_factory=lambda: _outcome, now=lambda: clock[0],
                     sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    assert sum(row["provider_requests"] for row in result["records"].values()) == 20
    # The real rolling ledger retains the active one-minute window, not an
    # unbounded session history; all twenty durable request records prove the
    # session count while this proves the ten-per-minute ledger ceiling.
    assert len(json.loads((budget_root / "provider_budget.json").read_text())["calls"]) == 10
    restarted = execute(manifest_path=state / "first.json", journal_path=state / "journal.json", budget_root=budget_root,
                        env_path=tmp_path / ".env", binding_factory=lambda: _outcome, now=lambda: clock[0], sleep=lambda _: None)
    assert sum(row["provider_requests"] for row in restarted["records"].values()) == 20


def test_dev031_manifest_rejects_tampering_duplicate_identity_and_request_twenty_one(tmp_path):
    db, lifecycle = _fixture_sources(tmp_path, count=24)
    state = tmp_path / "state"
    manifest = prepare_dev031_manifest(prepeak_db=db, lifecycle_root=lifecycle, destination=state / "manifest.json")
    tampered = dict(manifest); tampered["records"] = [dict(row) for row in manifest["records"]]
    tampered["records"][0]["mint"] = "ChangedMint"
    (state / "tampered.json").write_text(json.dumps(tampered))
    budget_root = tmp_path / "budget"; budget_root.mkdir(); (budget_root / "provider_budget.json").write_text('{"calls":[],"version":1}\n')
    (tmp_path / ".env").write_text("BIRDEYE=synthetic\n")
    duplicate = dict(manifest); duplicate["records"] = [*manifest["records"][:-1], manifest["records"][0]]
    duplicate_payload = dict(duplicate); duplicate_payload.pop("content_hash", None); duplicate["content_hash"] = _sha(duplicate_payload)
    twenty_one = {**manifest, "records": [*manifest["records"], manifest["records"][0]], "maximum_requests": 21}
    twenty_one_payload = dict(twenty_one); twenty_one_payload.pop("content_hash", None); twenty_one["content_hash"] = _sha(twenty_one_payload)
    for invalid in (tampered, duplicate, twenty_one):
        path = state / f"invalid-{invalid['maximum_requests']}.json"; path.write_text(json.dumps(invalid))
        calls = []
        try:
            execute(manifest_path=path, journal_path=state / f"{path.stem}.journal", budget_root=budget_root, env_path=tmp_path / ".env", binding_factory=lambda: calls.append(True))
            assert False, "invalid manifest must fail before transport"
        except ValueError:
            pass
        assert calls == []
