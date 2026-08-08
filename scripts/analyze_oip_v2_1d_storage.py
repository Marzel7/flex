#!/usr/bin/env python3
"""Read-only OIP v2.1D provenance and SQLite storage audit."""
from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import statistics
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CURRENT = ROOT / "database/evidence_platform/oip_v2_1c_retry_failover/evidence.db"
BEFORE = ROOT / "database/evidence_platform/oip_v2_1a_pilot/evidence.db"
RUN = ROOT / "database/evidence_platform/oip_v2_1d_storage_audit"
DOCS = ROOT / "docs/evidence_platform"
ATTEMPTS = DOCS / "oip_v2_1c_physical_attempts.jsonl"
ANALYSIS_DB = RUN / "analysis.sqlite"


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")
    os.replace(temporary, path)


def read_json(path: Path):
    return json.loads(path.read_text())


def quantiles(values: list[int]) -> dict:
    if not values:
        return {key: None for key in ("mean", "median", "p90", "p95", "p99", "max")}
    ordered = sorted(values)
    def pick(fraction: float) -> int:
        return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]
    return {"mean": sum(values) / len(values), "median": statistics.median(values),
            "p90": pick(.90), "p95": pick(.95), "p99": pick(.99), "max": ordered[-1]}


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def db_physical(path: Path) -> dict:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
    objects = {row[0]: {"bytes": int(row[1]), "pages": int(row[2]),
                        "payload_bytes": int(row[3]), "unused_bytes": int(row[4])}
               for row in connection.execute(
                   "SELECT name,SUM(pgsize),COUNT(*),SUM(payload),SUM(unused) "
                   "FROM dbstat GROUP BY name")}
    result = {"path": str(path.relative_to(ROOT)), "file_bytes": path.stat().st_size,
              "page_size": page_size, "page_count": int(connection.execute("PRAGMA page_count").fetchone()[0]),
              "freelist_count": int(connection.execute("PRAGMA freelist_count").fetchone()[0]),
              "wal_bytes": path.with_name(path.name + "-wal").stat().st_size if path.with_name(path.name + "-wal").exists() else 0,
              "shm_bytes": path.with_name(path.name + "-shm").stat().st_size if path.with_name(path.name + "-shm").exists() else 0,
              "objects": objects}
    connection.close()
    return result


def query_plan(connection, sql: str, parameters=()) -> list[str]:
    return [row[3] for row in connection.execute("EXPLAIN QUERY PLAN " + sql, parameters)]


def ensure_primitive_aggregate(corpus: Path, analysis: Path,
                               table: str = "primitive_input_counts") -> dict:
    """Persist one row per Primitive using one covering-index provenance scan."""
    analysis.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(analysis)
    existing = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if existing:
        count = int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        connection.close()
        return {"reused": True, "rows": count, "runtime_seconds": 0.0}
    connection.execute("ATTACH DATABASE ? AS corpus", (str(corpus),))
    started = time.perf_counter()
    connection.execute(
        f"CREATE TABLE {table} AS "
        "SELECT primitive_id,COUNT(*) evidence_input_count "
        "FROM corpus.primitive_evidence_inputs GROUP BY primitive_id"
    )
    connection.execute(f"CREATE UNIQUE INDEX {table}_id ON {table}(primitive_id)")
    connection.commit()
    elapsed = time.perf_counter() - started
    count = int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    connection.close()
    return {"reused": False, "rows": count, "runtime_seconds": round(elapsed, 6)}


def ensure_family_matrix(corpus: Path, analysis: Path) -> dict:
    connection = sqlite3.connect(analysis)
    existing = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='evidence_primitive_matrix'"
    ).fetchone()
    if existing:
        count = int(connection.execute("SELECT COUNT(*) FROM evidence_primitive_matrix").fetchone()[0])
        connection.close()
        return {"reused": True, "rows": count, "runtime_seconds": 0.0}
    connection.execute("ATTACH DATABASE ? AS corpus", (str(corpus),))
    started = time.perf_counter()
    connection.execute(
        "CREATE TABLE evidence_primitive_matrix AS "
        "SELECT e.fact_family evidence_family,p.primitive_type primitive_family,COUNT(*) link_count "
        "FROM corpus.primitive_evidence_inputs i "
        "JOIN corpus.normalized_evidence_records e USING(evidence_id) "
        "JOIN corpus.primitive_observations p USING(primitive_id) "
        "GROUP BY e.fact_family,p.primitive_type"
    )
    connection.commit()
    elapsed = time.perf_counter() - started
    count = int(connection.execute("SELECT COUNT(*) FROM evidence_primitive_matrix").fetchone()[0])
    connection.close()
    return {"reused": False, "rows": count, "runtime_seconds": round(elapsed, 6)}


def ensure_transaction_aggregate(corpus: Path, analysis: Path, attempts: list[dict]) -> dict:
    connection = sqlite3.connect(analysis)
    existing = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='transaction_amplification'"
    ).fetchone()
    if existing:
        count = int(connection.execute("SELECT COUNT(*) FROM transaction_amplification").fetchone()[0])
        connection.close()
        return {"reused": True, "rows": count, "runtime_seconds": 0.0}
    digest_to_attempt = {row["raw_artifact_digest"]: row for row in attempts}
    corpus_connection = sqlite3.connect(f"file:{corpus}?mode=ro", uri=True)
    started = time.perf_counter()
    evidence_to_digest: dict[str, str] = {}
    fact_counts = Counter()
    for evidence_id, digest in corpus_connection.execute(
            "SELECT evidence_id,raw_artifact_digest FROM normalized_evidence_records"):
        if digest in digest_to_attempt:
            evidence_to_digest[evidence_id] = digest
            fact_counts[digest] += 1
    link_counts = Counter()
    primitives: dict[str, set[str]] = {digest: set() for digest in digest_to_attempt}
    for primitive_id, evidence_id in corpus_connection.execute(
            "SELECT primitive_id,evidence_id FROM primitive_evidence_inputs"):
        digest = evidence_to_digest.get(evidence_id)
        if digest is not None:
            link_counts[digest] += 1
            primitives[digest].add(primitive_id)
    corpus_connection.close()
    connection.execute("CREATE TABLE transaction_amplification("
                       "raw_artifact_digest TEXT PRIMARY KEY,signature TEXT,launch TEXT,dependency_type TEXT,"
                       "evidence_facts INTEGER,primitive_links INTEGER,primitives_reached INTEGER)")
    connection.execute("CREATE TABLE transaction_primitive_map("
                       "raw_artifact_digest TEXT,primitive_id TEXT,PRIMARY KEY(raw_artifact_digest,primitive_id))")
    rows = []
    for digest, attempt in digest_to_attempt.items():
        rows.append((digest, attempt["target_signature"], attempt["launch_id"], attempt["dependency_type"],
                     fact_counts[digest], link_counts[digest], len(primitives[digest])))
    connection.executemany("INSERT INTO transaction_amplification VALUES(?,?,?,?,?,?,?)", rows)
    connection.executemany("INSERT INTO transaction_primitive_map VALUES(?,?)",
                           ((digest, primitive_id) for digest, values in primitives.items() for primitive_id in values))
    connection.commit(); connection.close()
    return {"reused": False, "rows": len(rows),
            "mapped_evidence_ids": len(evidence_to_digest),
            "mapped_primitive_links": sum(link_counts.values()),
            "runtime_seconds": round(time.perf_counter() - started, 6)}


def ensure_incremental_matrix(before: Path, current: Path, analysis: Path) -> dict:
    connection = sqlite3.connect(analysis)
    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='incremental_matrix'").fetchone():
        total = int(connection.execute("SELECT SUM(link_count) FROM incremental_matrix").fetchone()[0])
        connection.close(); return {"reused": True, "links": total, "runtime_seconds": 0.0}
    old = sqlite3.connect(f"file:{before}?mode=ro", uri=True)
    old_ids = {row[0] for row in old.execute("SELECT primitive_id FROM primitive_observations")}; old.close()
    corpus = sqlite3.connect(f"file:{current}?mode=ro", uri=True)
    new_families = {primitive_id: family for primitive_id, family in corpus.execute(
        "SELECT primitive_id,primitive_type FROM primitive_observations") if primitive_id not in old_ids}
    evidence_families = dict(corpus.execute("SELECT evidence_id,fact_family FROM normalized_evidence_records"))
    counts = Counter(); started = time.perf_counter()
    for primitive_id, evidence_id in corpus.execute("SELECT primitive_id,evidence_id FROM primitive_evidence_inputs"):
        primitive_family = new_families.get(primitive_id)
        if primitive_family is not None:
            counts[(evidence_families[evidence_id], primitive_family)] += 1
    corpus.close()
    connection.execute("CREATE TABLE incremental_matrix(evidence_family TEXT,primitive_family TEXT,link_count INTEGER)")
    connection.executemany("INSERT INTO incremental_matrix VALUES(?,?,?)",
                           ((evidence, primitive, count) for (evidence, primitive), count in counts.items()))
    connection.commit(); connection.close()
    return {"reused": False, "links": sum(counts.values()), "new_primitives": len(new_families),
            "runtime_seconds": round(time.perf_counter() - started, 6)}


def main() -> int:
    RUN.mkdir(parents=True, exist_ok=True)
    checkpoint = RUN / "checkpoint.json"
    state = {"milestone": "OIP v2.1D", "rpc_calls": 0, "production_interaction": False,
             "current_db_sha256": file_digest(CURRENT), "phases": []}
    write_json(checkpoint, state)

    baseline_path = RUN / "01_frozen_storage_baseline.json"
    if baseline_path.exists():
        baseline = json.loads(baseline_path.read_text())
        physical_before, physical_after, object_delta = baseline["before"], baseline["after"], baseline["object_delta"]
        state["baseline_reused"] = True
    else:
        physical_before, physical_after = db_physical(BEFORE), db_physical(CURRENT)
        names = set(physical_before["objects"]) | set(physical_after["objects"])
        object_delta = {name: {"before_bytes": physical_before["objects"].get(name, {}).get("bytes", 0),
                               "after_bytes": physical_after["objects"].get(name, {}).get("bytes", 0),
                               "delta_bytes": physical_after["objects"].get(name, {}).get("bytes", 0) -
                                              physical_before["objects"].get(name, {}).get("bytes", 0)}
                        for name in names}
        baseline = {"before": physical_before, "after": physical_after, "object_delta": object_delta,
                    "database_file_delta": physical_after["file_bytes"] - physical_before["file_bytes"]}
        write_json(baseline_path, baseline)
        state["baseline_reused"] = False
    state["phases"].append("baseline"); write_json(checkpoint, state)

    connection = sqlite3.connect(f"file:{CURRENT}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("ATTACH DATABASE ? AS before_db", (str(BEFORE),))

    slow_sql = ("SELECT p.primitive_type,p.primitive_id,COUNT(i.evidence_id) links "
                "FROM primitive_observations p LEFT JOIN primitive_evidence_inputs i USING(primitive_id) "
                "GROUP BY p.primitive_id")
    bounded_sql = ("SELECT primitive_id,COUNT(*) evidence_input_count "
                   "FROM primitive_evidence_inputs GROUP BY primitive_id")
    recovery = {"stopped_process": {"pid": 62704, "elapsed": "00:06:45", "cpu_time": "00:00:52.33",
                    "cpu_percent": 8.7, "phase": "family_census", "checkpoint": ["baseline"]},
        "slow_sql": slow_sql, "slow_plan": query_plan(connection, slow_sql),
        "bounded_sql": bounded_sql, "bounded_plan": query_plan(connection, bounded_sql),
        "index_shape": {"primitive_evidence_inputs_pk": ["primitive_id", "evidence_id"],
                        "aligned_with_grouping_prefix": True},
        "analysis_only_acceleration": False,
        "completed_checkpoints_reused": ["dbstat/page-allocation baseline"],
        "remaining_large_scans": ["per-Primitive aggregate", "Evidence×Primitive matrix", "transaction/artifact mapping"]}
    write_json(RUN / "00_query_recovery.json", recovery)

    print(json.dumps({"phase": "primitive_input_counts", "expected_source_rows": 3495337,
        "plan": recovery["bounded_plan"], "checkpoint": str(ANALYSIS_DB)}), flush=True)
    aggregate_status = ensure_primitive_aggregate(CURRENT, ANALYSIS_DB)
    write_json(RUN / "00_primitive_aggregate_status.json", aggregate_status)
    connection.execute("ATTACH DATABASE ? AS analysis", (str(ANALYSIS_DB),))
    before_aggregate = ensure_primitive_aggregate(BEFORE, ANALYSIS_DB, "before_primitive_input_counts")
    write_json(RUN / "00_before_primitive_aggregate_status.json", before_aggregate)

    family_path = RUN / "02_primitive_family_census.json"
    if family_path.exists():
        family_census = json.loads(family_path.read_text())
        total_links = sum(row["evidence_input_links"] for row in family_census)
    else:
        family_counts: dict[str, list[int]] = {}
        for row in connection.execute(
            "SELECT p.primitive_type,a.primitive_id,a.evidence_input_count links "
            "FROM analysis.primitive_input_counts a JOIN primitive_observations p USING(primitive_id)"):
            family_counts.setdefault(row["primitive_type"], []).append(int(row["links"]))
        family_census = [{"primitive_family": family, "primitive_count": len(counts),
                          "evidence_input_links": sum(counts), **quantiles(counts)}
                         for family, counts in family_counts.items()]
        family_census.sort(key=lambda row: (-row["evidence_input_links"], row["primitive_family"]))
        total_links = sum(row["evidence_input_links"] for row in family_census)
        running = 0
        for row in family_census:
            running += row["evidence_input_links"]
            row["cumulative_share"] = running / total_links
        thresholds = {str(level): next((index + 1 for index, row in enumerate(family_census)
                                        if row["cumulative_share"] >= level), None)
                      for level in (.5, .75, .9, .95)}
        write_json(family_path, family_census)
        write_json(RUN / "03_heavy_hitters.json", {"total_links": total_links, "families_for_share": thresholds,
                                                    "ranked_families": family_census})

    top_path = RUN / "04_top_primitives.json"
    if top_path.exists():
        top_primitives = json.loads(top_path.read_text())
    else:
        top_primitives = [dict(row) for row in connection.execute(
            "WITH top AS (SELECT primitive_id,evidence_input_count FROM analysis.primitive_input_counts "
            "ORDER BY evidence_input_count DESC,primitive_id LIMIT 100) "
            "SELECT p.primitive_type,t.primitive_id,t.evidence_input_count input_links,"
            "COUNT(DISTINCT i.evidence_id) unique_evidence_ids,COUNT(DISTINCT e.raw_artifact_digest) unique_artifacts,"
            "COUNT(DISTINCT CASE WHEN e.fact_family='TransactionFact' THEN e.logical_fact_id END) transactions "
            "FROM top t JOIN primitive_observations p USING(primitive_id) "
            "JOIN primitive_evidence_inputs i USING(primitive_id) JOIN normalized_evidence_records e USING(evidence_id) "
            "GROUP BY t.primitive_id ORDER BY input_links DESC,t.primitive_id")]
        write_json(top_path, top_primitives)
    state["phases"].append("primitive_cardinality"); write_json(checkpoint, state)

    matrix_sql = ("SELECT e.fact_family evidence_family,p.primitive_type primitive_family,COUNT(*) link_count "
        "FROM primitive_evidence_inputs i JOIN normalized_evidence_records e USING(evidence_id) "
        "JOIN primitive_observations p USING(primitive_id) GROUP BY e.fact_family,p.primitive_type")
    print(json.dumps({"phase": "evidence_primitive_matrix", "expected_source_rows": total_links,
        "plan": query_plan(connection, matrix_sql), "checkpoint": str(ANALYSIS_DB)}), flush=True)
    matrix_status = ensure_family_matrix(CURRENT, ANALYSIS_DB)
    write_json(RUN / "00_matrix_status.json", matrix_status)
    matrix = [dict(row) for row in connection.execute(
        "SELECT * FROM analysis.evidence_primitive_matrix ORDER BY link_count DESC")]
    fact_counts = {row["fact_family"]: int(row["evidence_facts"]) for row in connection.execute(
        "SELECT fact_family,COUNT(*) evidence_facts FROM normalized_evidence_records GROUP BY fact_family")}
    family_links = Counter(); family_reach: dict[str, set[str]] = {}
    for row in matrix:
        family_links[row["evidence_family"]] += row["link_count"]
        family_reach.setdefault(row["evidence_family"], set()).add(row["primitive_family"])
    evidence_family = [{"fact_family": family, "evidence_facts": facts,
                        "primitive_links": family_links[family],
                        "primitive_families_reached": len(family_reach.get(family, set()))}
                       for family, facts in fact_counts.items()]
    evidence_family.sort(key=lambda row: (-row["primitive_links"], row["fact_family"]))
    for row in evidence_family:
        row["mean_primitive_links_per_evidence"] = row["primitive_links"] / row["evidence_facts"]
    write_json(RUN / "05_evidence_family_census.json", evidence_family)
    write_json(RUN / "06_evidence_primitive_matrix.json", matrix)

    print(json.dumps({"phase": "incremental_evidence_primitive_matrix",
        "expected_provenance_rows": total_links, "plan": "one streaming scan with bounded identity maps",
        "checkpoint": str(ANALYSIS_DB)}), flush=True)
    incremental_status = ensure_incremental_matrix(BEFORE, CURRENT, ANALYSIS_DB)
    write_json(RUN / "00_incremental_matrix_status.json", incremental_status)
    incremental_matrix = [dict(row) for row in connection.execute(
        "SELECT * FROM analysis.incremental_matrix ORDER BY link_count DESC")]
    incremental_families = [dict(row) for row in connection.execute(
        "SELECT p.primitive_type primitive_family,COUNT(*) primitive_count,SUM(a.evidence_input_count) evidence_input_links "
        "FROM analysis.primitive_input_counts a JOIN primitive_observations p USING(primitive_id) "
        "LEFT JOIN analysis.before_primitive_input_counts b USING(primitive_id) WHERE b.primitive_id IS NULL "
        "GROUP BY p.primitive_type ORDER BY evidence_input_links DESC")]
    write_json(RUN / "06b_incremental_provenance.json", {"status": incremental_status,
        "primitive_families": incremental_families, "evidence_primitive_matrix": incremental_matrix})

    attempts = [json.loads(line) for line in ATTEMPTS.read_text().splitlines()]
    print(json.dumps({"phase": "transaction_amplification", "expected_evidence_rows": 267648,
        "expected_provenance_rows": total_links, "plan": "one streaming scan per table",
        "checkpoint": str(ANALYSIS_DB)}), flush=True)
    transaction_status = ensure_transaction_aggregate(CURRENT, ANALYSIS_DB, attempts)
    write_json(RUN / "00_transaction_status.json", transaction_status)
    transaction_rows = [dict(row) for row in connection.execute(
        "SELECT signature,launch,dependency_type,evidence_facts,primitive_links,primitives_reached "
        "FROM analysis.transaction_amplification ORDER BY signature")]
    def distribution(rows, key): return quantiles([int(row[key]) for row in rows])
    transaction_report = {"transactions": transaction_rows, "distribution": {
        key: distribution(transaction_rows, key) for key in ("evidence_facts", "primitives_reached", "primitive_links")}}
    launches = {}
    for row in transaction_rows:
        item = launches.setdefault(row["launch"], {"launch": row["launch"], "transactions": 0,
                                                   "evidence_facts": 0, "primitives_reached": 0, "primitive_links": 0})
        item["transactions"] += 1; item["evidence_facts"] += row["evidence_facts"]
        item["primitive_links"] += row["primitive_links"]
    launch_primitive_counts = {row["launch"]: int(row["count"]) for row in connection.execute(
        "SELECT t.launch,COUNT(DISTINCT m.primitive_id) count FROM analysis.transaction_amplification t "
        "JOIN analysis.transaction_primitive_map m USING(raw_artifact_digest) GROUP BY t.launch")}
    launch_rows = [{**item, "primitives_reached": launch_primitive_counts.get(item["launch"], 0)}
                   for item in launches.values()]
    launch_report = {"launches": launch_rows, "distribution": {
        key: distribution(launch_rows, key) for key in ("evidence_facts", "primitives_reached", "primitive_links")}}
    write_json(RUN / "07_transaction_amplification.json", transaction_report)
    write_json(RUN / "08_launch_amplification.json", launch_report)
    state["phases"].append("evidence_contribution"); write_json(checkpoint, state)

    duplicate = {"total_rows": total_links, "unique_tuples": total_links, "exact_duplicates": 0,
                 "proof": "PRIMARY KEY(primitive_id,evidence_id) enforces the intended semantic tuple."}
    multiplicity = {
        "logical_facts": int(connection.execute("SELECT COUNT(DISTINCT logical_fact_id) FROM normalized_evidence_records").fetchone()[0]),
        "logical_facts_with_versions": int(connection.execute(
            "SELECT COUNT(*) FROM (SELECT logical_fact_id FROM normalized_evidence_records GROUP BY logical_fact_id HAVING COUNT(*)>1)").fetchone()[0]),
        "evidence_versions": [dict(row) for row in connection.execute(
            "SELECT logical_fact_id,COUNT(*) evidence_versions,COUNT(DISTINCT parser_version) parser_versions,"
            "COUNT(DISTINCT provider) providers,COUNT(DISTINCT replay_version) replay_versions,"
            "COUNT(DISTINCT raw_artifact_digest) artifacts FROM normalized_evidence_records "
            "GROUP BY logical_fact_id HAVING COUNT(*)>1 ORDER BY evidence_versions DESC LIMIT 100")],
        "same_artifact_evidence_facts": int(connection.execute(
            "SELECT COUNT(*) FROM normalized_evidence_records WHERE raw_artifact_digest IN "
            "(SELECT raw_artifact_digest FROM normalized_evidence_records GROUP BY raw_artifact_digest HAVING COUNT(*)>1)").fetchone()[0]),
    }
    write_json(RUN / "09_exact_duplicates.json", duplicate)
    write_json(RUN / "10_version_logical_fact_multiplicity.json", multiplicity)

    replay = {"first_generated": 132886, "first_inserted_primitives": 14546,
              "second_generated": 132886, "second_inserted_primitives": 0,
              "second_links_inserted": 0,
              "proof": "append_primitives inserts evidence links only when INSERT OR IGNORE inserts a new primitive row.",
              "digest": "33346f90a48f6fb2086da557ec2d7889d119e5b69db524cd4cf6fca7b62b8219"}
    write_json(RUN / "11_replay_amplification.json", replay)
    state["phases"].append("duplication_replay"); write_json(checkpoint, state)

    schema = {"primitive_observations": {
        "columns": [dict(row) for row in connection.execute("PRAGMA table_info(primitive_observations)")],
        "indexes": [dict(row) for row in connection.execute("PRAGMA index_list(primitive_observations)")]},
        "primitive_evidence_inputs": {
        "columns": [dict(row) for row in connection.execute("PRAGMA table_info(primitive_evidence_inputs)")],
        "indexes": [dict(row) for row in connection.execute("PRAGMA index_list(primitive_evidence_inputs)")],
        "foreign_keys": [dict(row) for row in connection.execute("PRAGMA foreign_key_list(primitive_evidence_inputs)")]}}
    queries = {
        "load_all_for_discovery": "SELECT primitive_id,evidence_id FROM primitive_evidence_inputs ORDER BY primitive_id,evidence_id",
        "evidence_coverage_health": "SELECT COUNT(DISTINCT evidence_id) FROM primitive_evidence_inputs",
        "primitive_reconstruction": "SELECT evidence_id FROM primitive_evidence_inputs WHERE primitive_id=?",
        "evidence_reverse_lookup": "SELECT primitive_id FROM primitive_evidence_inputs WHERE evidence_id=?",
    }
    utility = {name: {"plan": query_plan(connection, sql, (top_primitives[0]["primitive_id"],) if "?" in sql else ()),
                      "classification": "REQUIRED" if name in ("load_all_for_discovery", "primitive_reconstruction") else
                                        "USEFUL" if name == "evidence_coverage_health" else "UNSUPPORTED_WITH_CURRENT_INDEX"}
               for name, sql in queries.items()}
    write_json(RUN / "12_schema_index_inventory.json", schema)
    write_json(RUN / "13_index_utility.json", utility)

    link_table = physical_after["objects"]["primitive_evidence_inputs"]["bytes"]
    link_index = physical_after["objects"]["sqlite_autoindex_primitive_evidence_inputs_1"]["bytes"]
    bytes_per_link = {"links": total_links, "table_bytes": link_table, "primary_key_index_bytes": link_index,
                      "table_bytes_per_link": link_table / total_links,
                      "index_bytes_per_link": link_index / total_links,
                      "effective_bytes_per_link": (link_table + link_index) / total_links,
                      "logical_identifier_text_bytes_per_link": 128,
                      "representation": "two 64-character TEXT content identities per table row and repeated in the composite index"}
    write_json(RUN / "14_bytes_per_link.json", bytes_per_link)

    categories = {
        "primitive_evidence_inputs_table": object_delta["primitive_evidence_inputs"]["delta_bytes"],
        "provenance_primary_key_index": object_delta["sqlite_autoindex_primitive_evidence_inputs_1"]["delta_bytes"],
        "primitive_observations_table": object_delta["primitive_observations"]["delta_bytes"],
        "primitive_observations_index": object_delta["sqlite_autoindex_primitive_observations_1"]["delta_bytes"],
        "evidence_facts_table": object_delta["normalized_evidence_records"]["delta_bytes"],
        "evidence_indexes": object_delta["normalized_evidence_logical_fact"]["delta_bytes"] + object_delta["sqlite_autoindex_normalized_evidence_records_1"]["delta_bytes"],
        "evidence_provenance_table_and_index": object_delta["normalized_evidence_provenance"]["delta_bytes"] + object_delta["sqlite_autoindex_normalized_evidence_provenance_1"]["delta_bytes"],
    }
    categories["other_database"] = baseline["database_file_delta"] - sum(categories.values())
    external = read_json(DOCS / "oip_v2_1c_physical_storage.json")
    categories["artifacts_reports_and_attempt_telemetry"] = external["artifact_and_report_bytes"] + external["attempt_telemetry_bytes"]
    categories["total_explained"] = sum(value for key, value in categories.items() if key != "total_explained")
    decomposition = {"categories": categories, "target_incremental_bytes": external["total_incremental_physical_bytes"],
                     "residual_bytes": external["total_incremental_physical_bytes"] - categories["total_explained"],
                     "freelist_delta_bytes": (physical_after["freelist_count"] - physical_before["freelist_count"]) * physical_after["page_size"]}
    write_json(RUN / "15_storage_decomposition.json", decomposition)
    state["phases"].append("schema_storage"); write_json(checkpoint, state)

    link_delta = object_delta["primitive_evidence_inputs"]["delta_bytes"]
    index_delta = object_delta["sqlite_autoindex_primitive_evidence_inputs_1"]["delta_bytes"]
    classification = {"verdict": "E — MIXED", "components": {
        "SEMANTICALLY_REQUIRED": {"links": total_links, "reason": "Exact Evidence-to-Primitive provenance is consumed by replay, Discovery and audit."},
        "VERSION_COEXISTENCE_REQUIRED": {"logical_facts_with_versions": multiplicity["logical_facts_with_versions"]},
        "REPRESENTATION_OVERHEAD": {"bytes": link_delta, "reason": "Repeated 64-character TEXT identities in every link table row."},
        "INDEX_OVERHEAD": {"bytes": index_delta, "reason": "Composite TEXT primary-key B-tree duplicates both identifiers."},
        "DUPLICATE_PHYSICAL_STORAGE": {"links": duplicate["exact_duplicates"]},
        "AUDIT_REQUIRED": {"links": total_links}, "UNKNOWN": {"bytes": decomposition["residual_bytes"]}}}
    candidates = [{"candidate": "INTEGER surrogate link representation with compatibility view/triggers",
                   "expected_effect": "Remove repeated TEXT identities from link table and composite index while preserving relation set.",
                   "semantic_risk": "LOW only if referential integrity, immutable triggers, insert compatibility and all query plans are proven in shadow.",
                   "prototype": "JUSTIFIED"},
                  {"candidate": "Drop provenance links", "prototype": "REJECTED", "reason": "Violates exact provenance."},
                  {"candidate": "Collapse evidence versions", "prototype": "REJECTED", "reason": "Violates EP1.3 coexistence."},
                  {"candidate": "Artifact compression", "prototype": "DEFERRED", "reason": "Not the dominant database cost."}]
    write_json(RUN / "16_semantic_necessity.json", classification)
    write_json(RUN / "17_optimization_candidates.json", candidates)

    current_per_attempt = external["bytes_per_physical_attempt"]
    projections = {str(scale): {"point_bytes": round(current_per_attempt * scale),
                                "range_bytes": [round(current_per_attempt * scale * .75), round(current_per_attempt * scale * 1.25)]}
                   for scale in (1000, 5000, 10000, 26283)}
    useful = {"storage_per_recovered_transaction": external["bytes_per_recovered_transaction"],
              "storage_per_completed_launch": external["bytes_per_completed_launch"],
              "storage_per_new_evidence_fact": external["total_incremental_physical_bytes"] / 36435,
              "storage_per_new_primitive": external["total_incremental_physical_bytes"] / 14546,
              "storage_per_relationship_gain": external["total_incremental_physical_bytes"] / 82,
              "storage_per_motif_gain": external["total_incremental_physical_bytes"] / 156}
    write_json(RUN / "18_current_scaling_model.json", projections)
    write_json(RUN / "19_cost_per_outcome.json", useful)
    verdict = {"storage_verdict": "E — MIXED", "acquisition_verdict": "HOLD_ACQUISITION_FOR_STORAGE_OPTIMIZATION",
               "reason": "All links are unique and semantically used, but repeated TEXT identifiers and their composite index dominate physical cost. Prototype before authorizing the next 1,000."}
    write_json(RUN / "20_preprototype_verdict.json", verdict)
    state["phases"].append("classification"); state["complete"] = True; write_json(checkpoint, state)
    connection.close()
    print(json.dumps({"links": total_links, "families": len(family_census), "duplicate_links": duplicate["exact_duplicates"],
                      "link_subsystem_bytes": link_table + link_index, "verdict": verdict["storage_verdict"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
