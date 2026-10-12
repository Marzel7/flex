import importlib.util
import json
import sqlite3
from pathlib import Path


def _module():
    path = Path(__file__).parents[1] / "scripts" / "run_dev020_prepeak_session.py"
    spec = importlib.util.spec_from_file_location("dev020_selector", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def _journal(root, mint, peak_at=200):
    path = root / "journals" / mint
    path.mkdir(parents=True)
    (path / "journal.json").write_text(json.dumps({"mint": mint, "state": "COMPLETED", "lifecycle_metrics": {"observed_maximum_mc_usd": 500, "observed_maximum_timestamp": peak_at}}))


def _dev019(path, mint, opening=100, timestamp=100):
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE watchtower_historical_openings(mint TEXT PRIMARY KEY,state TEXT,result_json TEXT)")
        connection.execute("INSERT INTO watchtower_historical_openings VALUES(?,?,?)", (mint, "QUALIFIED", json.dumps({"entry_mc_usd": opening, "entry_timestamp": timestamp, "entry_method": "FIRST_AVAILABLE_POST_MIGRATION_1M_OPEN_MC", "interval": "1m", "market_cap_field": "open"})))


def test_dev019_fallback_deduplicates_and_preserves_existing_baseline_precedence(tmp_path, monkeypatch):
    module = _module()
    _journal(tmp_path, "dev019-only")
    _journal(tmp_path, "existing")
    journal = tmp_path / "dev019.db"; _dev019(journal, "dev019-only")
    monkeypatch.setattr(module, "COHORT_ROOT", tmp_path)
    monkeypatch.setattr(module, "DEV019_JOURNAL", journal)
    monkeypatch.setattr(module, "_opening_baseline", lambda mint: {"market_cap_usd": 250, "timestamp": 120, "baseline_type": "DEV013"} if mint == "existing" else None)

    rows = {row["mint"]: row for row in module.eligible_rows()}

    assert set(rows) == {"dev019-only", "existing"}
    assert rows["dev019-only"]["opening_market_cap"] == 100
    assert rows["existing"]["opening_market_cap"] == 250


def test_zero_duration_remains_in_selector_for_cohort_reconciliation(tmp_path, monkeypatch):
    module = _module(); _journal(tmp_path, "same-time", peak_at=100)
    monkeypatch.setattr(module, "COHORT_ROOT", tmp_path)
    monkeypatch.setattr(module, "DEV019_JOURNAL", tmp_path / "absent.db")
    monkeypatch.setattr(module, "_opening_baseline", lambda _: {"market_cap_usd": 100, "timestamp": 100, "baseline_type": "EXISTING"})

    assert module.eligible_rows()[0]["duration_seconds"] == 0
