"""Sprint X19.6 — canonical WATCHTOWER end-to-end control case."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from src.core.database_write_service import execute_script
from src.discovery.service import DiscoveryService
from src.ops.operator_model import DDL as OPERATOR_DDL
from src.ops.watchtower_alignment import (
    WATCHTOWER_OPERATOR_ID,
    TreasuryOwnershipConflict,
    audit_alignment,
    reconcile_all_confirmed_treasuries,
    reconcile_confirmed_treasury,
)
from src.ops.watchtower_funnel import build_watchtower_funnel
from src.ops.walkback_health import build_walkback_health, recover_stalled_running_jobs


ROOT = Path(__file__).resolve().parents[1]
CONTROL_PATH = ROOT / "tests/fixtures/watchtower_control_launches.json"


OPS_SCHEMA = """
CREATE TABLE wt_confirmed_treasuries (
 treasury TEXT PRIMARY KEY, method TEXT, confidence TEXT,
 confirmed_at INTEGER, provenance TEXT
);
CREATE TABLE wt_watchtower_launches (
 mint TEXT PRIMARY KEY, creator_wallet TEXT, subprov_wallet TEXT,
 treasury_wallet TEXT, create_time INTEGER, recorded_at INTEGER,
 confidence TEXT
);
CREATE TABLE wt_walkback_queue (
 mint TEXT PRIMARY KEY, creator TEXT, subprov TEXT, treasury TEXT,
 walkback_class TEXT, status TEXT, attempts INTEGER, last_error TEXT,
 enqueued_at INTEGER, started_at INTEGER, completed_at INTEGER, updated_at INTEGER,
 intelligence_outcome TEXT
);
CREATE TABLE wt_worker_heartbeat (
 worker_name TEXT PRIMARY KEY, last_seen INTEGER, status TEXT, meta_json TEXT
);
"""


def _control_rows() -> list[dict]:
    return json.loads(CONTROL_PATH.read_text())


def _build_control_databases(tmp_path: Path) -> tuple[Path, Path, list[dict]]:
    ops_path = tmp_path / "ops.db"
    core_path = tmp_path / "core.db"
    rows = _control_rows()
    ops = sqlite3.connect(ops_path)
    ops.row_factory = sqlite3.Row
    execute_script(ops, OPERATOR_DDL)
    ops.executescript(OPS_SCHEMA)
    now = max(r["observed_at"] for r in rows) + 30
    ops.execute(
        "INSERT INTO operators "
        "(operator_id,status,confidence,summary,review_state,display_name,created_at,updated_at) "
        "VALUES (?,'CONFIRMED','CERTAIN','Canonical WATCHTOWER','REVIEWED','WATCHTOWER',1,1)",
        (WATCHTOWER_OPERATOR_ID,),
    )
    for treasury in sorted({r["treasury"] for r in rows}):
        ops.execute(
            "INSERT INTO wt_confirmed_treasuries VALUES (?, 'LAUNCH_CHAIN','STRICT',1,'CONFIRMED_LAUNCH_CHAIN')",
            (treasury,),
        )
    for row in rows:
        ops.execute(
            "INSERT INTO wt_watchtower_launches VALUES (?,?,?,?,?,?,?)",
            (row["mint"], row["creator"], row["sub_provisioner"], row["treasury"],
             row["observed_at"], row["observed_at"], "STRICT"),
        )
        ops.execute(
            "INSERT INTO wt_walkback_queue VALUES (?,?,?,?, 'FULL_WALKBACK','complete',1,NULL,?,?,?,?,?)",
            (row["mint"], row["creator"], row["sub_provisioner"], row["treasury"],
             row["observed_at"], row["observed_at"], row["observed_at"] + 1,
             row["observed_at"] + 1, "WATCHTOWER_CONFIRMED"),
        )
    for evidence_id, evidence_type in (
        ("infra", "CONFIRMED_INFRASTRUCTURE_REUSE"),
        ("vanity", "VANITY_ADDRESS_FAMILY"),
    ):
        ops.execute(
            "INSERT INTO operator_evidence VALUES (?,?,?,'IDENTITY','watchtower',NULL,NULL,0.8,'{}',1)",
            (evidence_id, WATCHTOWER_OPERATOR_ID, evidence_type),
        )
    result = reconcile_all_confirmed_treasuries(ops)
    assert result["audit"]["healthy"]
    ops.commit()
    ops.close()

    core = sqlite3.connect(core_path)
    core.execute(
        "CREATE TABLE token_analysis (mint TEXT PRIMARY KEY,pf_ws_creator TEXT,"
        "earliest_tx_creator TEXT,analyzed_at REAL)"
    )
    core.executemany(
        "INSERT INTO token_analysis VALUES (?,?,NULL,?)",
        [(r["mint"], r["creator"], r["observed_at"]) for r in rows],
    )
    core.commit()
    core.close()
    return ops_path, core_path, rows


def test_permanent_control_dataset_is_complete_and_unique():
    rows = _control_rows()
    assert len(rows) == 30
    assert len({r["mint"] for r in rows}) == 30
    assert all(r["creator"] and r["sub_provisioner"] and r["treasury"] for r in rows)


def test_reconciliation_is_exactly_once_and_idempotent(tmp_path):
    ops_path, _, rows = _build_control_databases(tmp_path)
    conn = sqlite3.connect(ops_path)
    conn.row_factory = sqlite3.Row
    before_entities = conn.execute(
        "SELECT COUNT(*) FROM operator_entities WHERE operator_id=?",
        (WATCHTOWER_OPERATOR_ID,),
    ).fetchone()[0]
    before_ledger = conn.execute("SELECT COUNT(*) FROM watchtower_identity_reconciliations").fetchone()[0]
    result = reconcile_all_confirmed_treasuries(conn)
    conn.commit()
    assert result["reconciled"] == 0
    assert result["audit"]["healthy"]
    assert conn.execute(
        "SELECT COUNT(*) FROM operator_entities WHERE operator_id=?",
        (WATCHTOWER_OPERATOR_ID,),
    ).fetchone()[0] == before_entities == len({r["treasury"] for r in rows})
    assert conn.execute("SELECT COUNT(*) FROM watchtower_identity_reconciliations").fetchone()[0] == before_ledger
    conn.close()


def test_conflicting_operator_ownership_fails_instead_of_duplicating(tmp_path):
    ops_path, _, rows = _build_control_databases(tmp_path)
    conn = sqlite3.connect(ops_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "INSERT INTO operators "
        "(operator_id,status,confidence,review_state,display_name,created_at,updated_at) "
        "VALUES ('OTHER','CONFIRMED','CERTAIN','REVIEWED','OTHER',1,1)"
    )
    conn.execute(
        "INSERT INTO operator_entities "
        "(operator_id,entity_address,entity_type,confidence,evidence_count,added_at) "
        "VALUES ('OTHER',?,'TREASURY','HIGH',1,1)",
        (rows[0]["treasury"],),
    )
    with pytest.raises(TreasuryOwnershipConflict):
        reconcile_confirmed_treasury(conn, rows[0]["treasury"])
    assert conn.execute(
        "SELECT COUNT(DISTINCT operator_id) FROM operator_entities WHERE entity_address=?",
        (rows[0]["treasury"],),
    ).fetchone()[0] == 2  # pre-existing conflict remains visible; no third identity is invented
    conn.close()


def test_all_30_launches_reach_discovery_mission_and_operator(tmp_path):
    ops_path, core_path, rows = _build_control_databases(tmp_path)
    service = DiscoveryService(str(ops_path), str(core_path))
    for row in rows:
        result = service.resolve(row["mint"], "token")
        identity = result["canonical_identity"]
        assert identity["operator_id"] == WATCHTOWER_OPERATOR_ID
        assert identity["operator_name"] == "WATCHTOWER"
        assert identity["confidence"] == "HIGH"
        assert identity["identity_signals"] == [
            "Infrastructure reuse", "Vanity family", "Treasury confirmed"
        ]
    mission = service.recent(limit=30)
    assert {e["entity"]["id"] for e in mission["streams"]["watchtower"]} == {
        r["mint"] for r in rows
    }
    conn = sqlite3.connect(ops_path)
    conn.row_factory = sqlite3.Row
    assert audit_alignment(conn)["healthy"]
    assert conn.execute(
        "SELECT COUNT(*) FROM operator_entities WHERE operator_id=? AND entity_type='TREASURY'",
        (WATCHTOWER_OPERATOR_ID,),
    ).fetchone()[0] == len({r["treasury"] for r in rows})
    conn.close()


def test_30_launch_control_funnel_has_no_downstream_loss(tmp_path):
    ops_path, core_path, rows = _build_control_databases(tmp_path)
    now = max(r["observed_at"] for r in rows) + 60
    funnel = build_watchtower_funnel(
        str(ops_path), str(core_path), now=now, window_seconds=30 * 86400
    )
    assert funnel["healthy"]
    assert [stage["count"] for stage in funnel["stages"]] == [30] * 10
    assert all(stage["loss"] in (None, 0) for stage in funnel["stages"])


def test_worker_health_requires_progress_even_with_current_heartbeat(tmp_path):
    path = tmp_path / "health.db"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(OPS_SCHEMA)
    now = 10_000
    conn.execute(
        "INSERT INTO wt_walkback_queue VALUES "
        "('PENDING','C',NULL,NULL,'FULL_WALKBACK','pending',0,NULL,9000,NULL,NULL,9000,NULL)"
    )
    conn.execute(
        "INSERT INTO wt_worker_heartbeat VALUES ('walkback_worker',?,'HEALTHY','{}')",
        (now,),
    )
    health = build_walkback_health(conn, now=now)
    assert not health["healthy"]
    assert health["completed_per_minute"] == 0
    assert "pending work exists" in health["reasons"][0]
    conn.close()


def test_stalled_claims_are_recovered_deterministically(tmp_path):
    path = tmp_path / "recovery.db"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(OPS_SCHEMA)
    conn.execute(
        "INSERT INTO wt_walkback_queue VALUES "
        "('RETRY','C',NULL,NULL,'FULL_WALKBACK','running',1,NULL,8000,8000,NULL,8000,NULL),"
        "('EXHAUSTED','C',NULL,NULL,'FULL_WALKBACK','running',3,NULL,8000,8000,NULL,8000,NULL)"
    )
    recovered = recover_stalled_running_jobs(
        conn, now=10_000, stalled_after_seconds=180, max_attempts=3
    )
    assert recovered == {"requeued": 1, "failed": 1}
    states = dict(conn.execute("SELECT mint,status FROM wt_walkback_queue"))
    assert states == {"RETRY": "pending", "EXHAUSTED": "failed"}
    conn.close()


def test_production_surfaces_name_the_x19_6_control_concepts():
    mission = (ROOT / "templates/ops_shell_index.html").read_text()
    discovery = (ROOT / "templates/discovery.html").read_text()
    dashboard = (ROOT / "templates/watchtower_discovery_assurance.html").read_text()
    assert "Recent WATCHTOWER" in mission
    assert "Recent Promotions" in mission
    assert "Recent Emerging Operators" in mission
    assert "Recent Reviews" in mission
    assert "Recent Walkbacks" in mission
    assert "Recent Discovery" in mission
    assert "Canonical Operator" in discovery
    assert "Confirmed Treasury · Confidence" in discovery
    assert "Live Attribution Funnel" in dashboard
    assert "Walkback Progress Health" in dashboard

