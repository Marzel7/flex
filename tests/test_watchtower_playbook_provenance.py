"""Provider-free end-to-end Watchtower Playbook provenance contract."""
from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path

from src.ops.operator_lifecycle_projection import ensure_schema


PLUS1 = "FIRST_FULL_POST_MIGRATION_SECOND_MC"
FALLBACK = "MIGRATION_SECOND_MC_FALLBACK"


def _insert_fact(db: str, mint: str, method: str | None, exactness: str) -> None:
    with sqlite3.connect(db) as conn:
        ensure_schema(conn)
        conn.execute(
            """INSERT INTO operation_monitor_facts(
                operation_id,mint,cohort_class,assignment_timestamp,entry_method,
                entry_timestamp,entry_mc_usd,entry_status,entry_exactness,monitor_state,
                provider_call_count,candles_retained,evidence_status,provenance_digest,
                created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("watchtower", mint, "PROSPECTIVE_MONITOR_COHORT", 100, method,
             101, 100.0, "QUALIFIED", exactness, "MONITORING_ACTIVE", 0, 0,
             "QUALIFIED", f"digest-{mint}", 100, 100),
        )


def _clean_product(monkeypatch, tmp_path):
    db = str(tmp_path / "ops.sqlite")
    _insert_fact(db, "plus-one", PLUS1, "NORMAL_PLUS_ONE_EXACT")
    _insert_fact(db, "fallback", FALLBACK, "MIGRATION_SECOND_FALLBACK_EXACT")
    monkeypatch.setenv("OPERATION_RESEARCH_ROOT", str(Path(__file__).resolve().parents[1]))
    monkeypatch.setenv("WT_OPS_DB_PATH", db)
    monkeypatch.setenv("WATCHTOWER_MONITOR_UI_DB_PATH", db)
    import src.ops.watchtower_research_cohorts as cohorts
    import src.ops.operation_playbook_registry as registry
    importlib.reload(cohorts)
    importlib.reload(registry)
    return registry, db


def test_two_row_product_path_preserves_plus_one_and_fallback_provenance(monkeypatch, tmp_path):
    registry, _ = _clean_product(monkeypatch, tmp_path)
    playbook = registry.artifact_playbook("watchtower")
    assert playbook is not None
    prospective = {row["mint"]: row for row in playbook["prospective_tokens"]}
    rendered = {row["mint"]: row for row in playbook["tokens"] if row.get("cohort") == "prospective"}
    assert set(prospective) == set(rendered) == {"plus-one", "fallback"}
    assert prospective["plus-one"]["entry_method"] == rendered["plus-one"]["entry_method"] == PLUS1
    assert prospective["plus-one"]["entry_exactness"] == rendered["plus-one"]["entry_exactness"] == "NORMAL_PLUS_ONE_EXACT"
    assert prospective["fallback"]["entry_method"] == rendered["fallback"]["entry_method"] == FALLBACK
    assert prospective["fallback"]["entry_exactness"] == rendered["fallback"]["entry_exactness"] == "MIGRATION_SECOND_FALLBACK_EXACT"
    assert sum(row["entry_method"] == PLUS1 for row in rendered.values()) == 1
    assert sum(row["entry_method"] == FALLBACK for row in rendered.values()) == 1


def test_playbook_never_defaults_missing_method_to_plus_one(monkeypatch, tmp_path):
    registry, _ = _clean_product(monkeypatch, tmp_path)
    import src.ops.watchtower_research_cohorts as cohorts
    monkeypatch.setattr(cohorts, "read_cohorts", lambda: {
        "statistics": {}, "prospective_rows": [{
            "mint": "missing", "entry_method": None, "entry_exactness": None,
            "entry_timestamp": 100, "entry_mc_usd": 1.0, "max_proven_mc_usd": None,
            "entry_to_max_multiple": None, "ath_bucket_start": None, "reached_2x": False,
            "reached_5x": False, "reached_10x": False, "terminal_state": None,
            "ath_finalization_status": "PENDING", "provenance_digest": "d", "final_ath_evidence": None,
        }],
    })
    row = next(row for row in registry.artifact_playbook("watchtower")["tokens"] if row.get("mint") == "missing")
    assert row["entry_method"] is None
    assert row["entry_exactness"] is None


def test_clean_registry_artifact_loads_without_mutation(monkeypatch, tmp_path):
    registry, _ = _clean_product(monkeypatch, tmp_path)
    playbook = registry.artifact_playbook("watchtower")
    assert playbook["contract_version"] == "WATCHTOWER_51_GENERIC_PRICE_FACT_REPLAY_V1"
    assert len(playbook["tokens"]) == 53


def test_live_monitor_projection_keeps_row_level_method_and_exactness(monkeypatch, tmp_path):
    _, db = _clean_product(monkeypatch, tmp_path)
    import src.core.db as core_db
    import src.ops.operator_routes as routes
    importlib.reload(core_db)
    projection = routes._monitor_live_projection()
    rows = {row["mint"]: row for row in projection["rows"]}
    assert rows["plus-one"]["entry_method"] == PLUS1
    assert rows["plus-one"]["entry_exactness"] == "NORMAL_PLUS_ONE_EXACT"
    assert rows["fallback"]["entry_method"] == FALLBACK
    assert rows["fallback"]["entry_exactness"] == "MIGRATION_SECOND_FALLBACK_EXACT"
