import json
import sqlite3
from pathlib import Path

from src.evidence.database import EvidenceDatabase
from src.intelligence.migrated_coverage import census, recovery_plan


def _production(path: Path):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE token_analysis(mint TEXT, create_tx_signature TEXT, migration_tx TEXT, lifecycle_stage TEXT, pf_ws_creator TEXT, earliest_tx_creator TEXT, migrated_at INTEGER, created_at INTEGER, migration_source TEXT, source_platform TEXT, watchtower_related INTEGER)")
    conn.executemany("INSERT INTO token_analysis VALUES(?,?,?,?,?,?,?,?,?,?,?)", [
        ("complete", "create-1", "migrate-1", "migrated", "c1", None, 1, 1, "rpc", None, 1),
        ("pending", "create-2", "migrate-2", "migrated", "c2", None, 2, 2, "webhook", None, 0),
        ("unavailable", None, "migrate-3", "migrated", "c3", None, 3, 3, None, "pump", 0),
        ("ignored", "create-x", None, "birth", "c4", None, 4, 4, None, None, 0),
    ])
    conn.commit(); conn.close()


def _evidence(path: Path):
    database = EvidenceDatabase(path)
    database.open_writer()
    database.close()
    conn = sqlite3.connect(path)
    base = ("id", "logical", "TransactionFact", "1", "solana", "mainnet-beta")
    for i, sig in enumerate(("create-1", "migrate-1", "migrate-3")):
        payload = json.dumps({"signature": sig})
        conn.execute("""INSERT INTO normalized_evidence_records
          (evidence_id,logical_fact_id,fact_family,fact_schema_version,chain,network,natural_key,payload_json,payload_digest,raw_artifact_digest,observed_at,acquired_at,source_id,source_version,provider,parser_id,parser_version,replay_version,verification_state,provenance_quality,created_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (f"id{i}",f"logical{i}",base[2],base[3],base[4],base[5],f"transaction/{sig}",payload,f"pd{i}",f"raw{i}",1,1,"s","v","p","parser","1","1","VERIFIED","DIRECT",1))
    launch = json.dumps({"mint":"complete","creation_signature":"create-1"})
    conn.execute("""INSERT INTO normalized_evidence_records
      (evidence_id,logical_fact_id,fact_family,fact_schema_version,chain,network,natural_key,payload_json,payload_digest,raw_artifact_digest,observed_at,acquired_at,source_id,source_version,provider,parser_id,parser_version,replay_version,verification_state,provenance_quality,created_at)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
      ("launch","launch-logical","LaunchFact","1","solana","mainnet-beta","launch/complete",launch,"lpd","lraw",1,1,"s","v","p","parser","1","1","VERIFIED","DIRECT",1))
    conn.commit();conn.close()


def test_census_and_plan_are_deterministic_and_missing_only(tmp_path):
    production, evidence = tmp_path/"prod.db", tmp_path/"evidence.db"
    _production(production); _evidence(evidence)
    rows = census(production, evidence)
    assert [row.mint for row in rows] == ["complete", "pending", "unavailable"]
    assert [row.state for row in rows] == ["COMPLETE", "PENDING", "UNAVAILABLE"]
    plan = recovery_plan(rows)
    assert plan == recovery_plan(rows)
    assert plan["signatures"] == ["create-2", "migrate-2"]
    assert plan["no_duplicate_rpc"] is True


def test_hard_limit_is_enforced_without_reclassification(tmp_path):
    production, evidence = tmp_path/"prod.db", tmp_path/"evidence.db"
    _production(production); _evidence(evidence)
    rows = census(production, evidence)
    plan = recovery_plan(rows, hard_call_limit=1)
    assert plan["planned_calls"] == 1
    assert plan["deferred_calls"] == 1
    assert plan["states"]["PENDING"] == 1


def test_census_can_freeze_a_live_population_by_source_rowid(tmp_path):
    production, evidence = tmp_path/"prod.db", tmp_path/"evidence.db"
    _production(production); _evidence(evidence)
    with sqlite3.connect(production) as conn:
        conn.execute("INSERT INTO token_analysis VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("late", "create-late", "migrate-late", "migrated", "c5", None, 5, 5, "rpc", None, 0))
        cutoff = conn.execute("SELECT rowid FROM token_analysis WHERE mint='late'").fetchone()[0] - 1
    rows = census(production, evidence, max_source_rowid=cutoff)
    assert "late" not in {row.mint for row in rows}
