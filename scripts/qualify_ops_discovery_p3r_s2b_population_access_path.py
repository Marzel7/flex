"""Fixture and local qualification for the mint-ordered S2B population access path."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import time
from pathlib import Path

REFERENCE_QUERY = """SELECT ta.mint, ta.pf_ws_creator FROM token_analysis AS ta INDEXED BY idx_ta_pf_ws_creator WHERE ta.pf_ws_creator IS NOT NULL AND EXISTS (SELECT 1 FROM pumpfun_migration_verification AS pmv WHERE pmv.mint = ta.mint) ORDER BY ta.mint ASC, ta.pf_ws_creator ASC"""
CANDIDATE_QUERY = """SELECT ta.mint, ta.pf_ws_creator FROM pumpfun_migration_verification AS pmv INDEXED BY sqlite_autoindex_pumpfun_migration_verification_1 JOIN token_analysis AS ta ON ta.mint = pmv.mint WHERE ta.pf_ws_creator IS NOT NULL ORDER BY pmv.mint ASC"""
SNAPSHOT = Path("docs/audits/ops_discovery_p3r_s2b_runs/s2b-source-snapshot-20260822T221819272628000Z-16a03978948f3881020440ceeb080e8a/snapshot.sqlite")
EXPECTED_ARTIFACT_SHA256 = "d1c4d546e06a5a72a6b134e82509afa3fcd5faba85b223876b70cde2532417f2"
EXPECTED_BOUNDARY_SEMANTIC_SHA256 = "33df1667c5343793e1a9500a24f10117ce21f30fa0fc97306ff1e3cc4eeb84a4"


def canonical(rows: list[tuple[str, str]]) -> tuple[int, str]:
    digest = hashlib.sha256()
    for ordinal, (mint, creator) in enumerate(rows, 1):
        digest.update((json.dumps({"population_ordinal": ordinal, "mint": mint, "create_creator": creator,
                                  "source_table": "token_analysis",
                                  "migration_verification_table": "pumpfun_migration_verification"},
                                 sort_keys=True, separators=(",", ":")) + "\n").encode())
    return len(rows), digest.hexdigest()


def plan(conn: sqlite3.Connection, query: str) -> list[str]:
    return [row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + query)]


def fixture_equivalence() -> dict:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "fixture.sqlite"
        conn = sqlite3.connect(path)
        conn.executescript("""
            CREATE TABLE token_analysis (mint TEXT PRIMARY KEY, pf_ws_creator TEXT);
            CREATE TABLE pumpfun_migration_verification (mint TEXT PRIMARY KEY);
            CREATE INDEX idx_ta_pf_ws_creator ON token_analysis(pf_ws_creator);
        """)
        conn.executemany("INSERT INTO token_analysis VALUES (?, ?)", [
            ("mint-a", "creator-a"), ("mint-b", None), ("mint-c", "creator-c"), ("mint-d", "creator-d")])
        conn.executemany("INSERT INTO pumpfun_migration_verification VALUES (?)", [("mint-a",), ("mint-b",), ("mint-d",)])
        duplicate_rejected = False
        try:
            conn.execute("INSERT INTO pumpfun_migration_verification VALUES ('mint-a')")
        except sqlite3.IntegrityError:
            duplicate_rejected = True
        reference_rows = list(conn.execute(REFERENCE_QUERY))
        candidate_rows = list(conn.execute(CANDIDATE_QUERY))
        result = {
            "reference_plan": plan(conn, REFERENCE_QUERY), "candidate_plan": plan(conn, CANDIDATE_QUERY),
            "reference": {"count": canonical(reference_rows)[0], "sha256": canonical(reference_rows)[1]},
            "candidate": {"count": canonical(candidate_rows)[0], "sha256": canonical(candidate_rows)[1]},
            "membership_equal": set(reference_rows) == set(candidate_rows), "ordering_equal": reference_rows == candidate_rows,
            "null_excluded": ("mint-b", None) not in candidate_rows,
            "unmatched_token_excluded": ("mint-c", "creator-c") not in candidate_rows,
            "duplicate_cardinality_preserved": duplicate_rejected and len(candidate_rows) == len(set(candidate_rows)),
        }
        conn.close()
    result["reference_temp_btree"] = any("USE TEMP B-TREE" in value for value in result["reference_plan"])
    result["candidate_temp_btree"] = any("USE TEMP B-TREE" in value for value in result["candidate_plan"])
    result["pass"] = (result["membership_equal"] and result["ordering_equal"] and
                      result["reference"]["sha256"] == result["candidate"]["sha256"] and
                      result["null_excluded"] and result["unmatched_token_excluded"] and
                      result["duplicate_cardinality_preserved"] and result["reference_temp_btree"] and
                      not result["candidate_temp_btree"])
    return result


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def benchmark(snapshot: Path, wall_seconds: float) -> dict:
    started, deadline = time.monotonic(), time.monotonic() + wall_seconds
    conn = sqlite3.connect(f"file:{snapshot.resolve()}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only=ON")
    conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
    candidate_plan = plan(conn, CANDIDATE_QUERY)
    if any("USE TEMP B-TREE" in value for value in candidate_plan):
        raise RuntimeError("CANDIDATE_TEMP_BTREE_PRESENT")
    sql_started, first_row_seconds, sql_count = time.monotonic(), None, 0
    for row in conn.execute(CANDIDATE_QUERY):
        if first_row_seconds is None:
            first_row_seconds = time.monotonic() - sql_started
        sql_count += 1
    sql_completion_seconds = time.monotonic() - sql_started
    materialize_started, rows = time.monotonic(), []
    for row in conn.execute(CANDIDATE_QUERY):
        rows.append(row)
    count, digest = canonical(rows)
    materialization_digest_seconds = time.monotonic() - materialize_started
    conn.close()
    one_pass_seconds = materialization_digest_seconds
    conservative_two_pass_seconds = (max(sql_completion_seconds, one_pass_seconds) * 2 * 1.5) + 10.0
    return {"candidate_plan": candidate_plan, "time_to_first_row_seconds": first_row_seconds,
            "sql_completion_seconds": sql_completion_seconds, "sql_rows_per_second": sql_count / sql_completion_seconds,
            "materialization_digest_seconds": materialization_digest_seconds, "candidate_count": count,
            "candidate_population_sha256": digest, "one_pass_seconds": one_pass_seconds,
            "nominal_two_pass_seconds": one_pass_seconds * 2 + 2.0,
            "conservative_two_pass_seconds": conservative_two_pass_seconds,
            "margin_under_120_seconds": 120.0 - conservative_two_pass_seconds,
            "elapsed_seconds": time.monotonic() - started}


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def main() -> int:
    audit = Path("docs/audits/ops_discovery_p3r_s2b_population_access_path_qualification.json")
    audit.parent.mkdir(parents=True, exist_ok=True)
    result = {"milestone": "OPS-DISCOVERY-P3R-S2B-POPULATION-ACCESS-PATH-REPAIR",
              "status": "STARTED", "provider_calls": 0, "production_source_writes": 0,
              "candidate": "mint-primary-key outer scan plus token_analysis mint-primary-key lookup; ORDER BY pmv.mint ASC",
              "canonical_order_proof": "Both joined relations have unique mint primary keys, so one candidate row exists per mint and the removed creator tie-breaker is unreachable.",
              "source_boundary_semantic_sha256": EXPECTED_BOUNDARY_SEMANTIC_SHA256,
              "source_artifact_file_sha256": file_sha256(SNAPSHOT), "wall_seconds": 120.0,
              "no_authoritative_two_pass_reproduction": True}
    try:
        if result["source_artifact_file_sha256"] != EXPECTED_ARTIFACT_SHA256:
            raise RuntimeError("FROZEN_SNAPSHOT_ARTIFACT_SHA256_MISMATCH")
        result["fixture"] = fixture_equivalence()
        if not result["fixture"]["pass"]:
            raise RuntimeError("FIXTURE_SEMANTIC_EQUIVALENCE_FAILED")
        result["reference_diagnostic"] = {"count_non_authoritative": 42664, "time_to_first_row_seconds": 402.998,
                                             "sql_completion_seconds": 376.315, "two_pass_projection_seconds": 1554.173}
        result["benchmark"] = benchmark(SNAPSHOT, 120.0)
        if result["benchmark"]["margin_under_120_seconds"] <= 30.0:
            raise RuntimeError("INSUFFICIENT_CONSERVATIVE_MARGIN")
        result["status"] = "PASS_P3R_S2B_POPULATION_ACCESS_PATH_REPAIR_QUALIFIED"
        atomic_json(audit, result)
        return 0
    except Exception as exc:
        result.update(status="HOLD_P3R_S2B_POPULATION_ACCESS_PATH_SEMANTIC_MISMATCH" if "SEMANTIC" in str(exc) else
                      "HOLD_P3R_S2B_POPULATION_ACCESS_PATH_INSUFFICIENT_120S",
                      exception_type=type(exc).__name__, exception=str(exc))
        atomic_json(audit, result)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
