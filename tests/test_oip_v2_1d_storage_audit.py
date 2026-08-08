import sqlite3

from scripts.analyze_oip_v2_1d_storage import (
    ensure_family_matrix, ensure_primitive_aggregate, ensure_transaction_aggregate, quantiles,
)


def test_quantiles_are_nearest_rank_and_deterministic():
    values = list(range(1, 101))
    result = quantiles(list(reversed(values)))
    assert result == {"mean": 50.5, "median": 50.5, "p90": 90, "p95": 95, "p99": 99, "max": 100}


def test_empty_quantiles_are_explicit():
    assert all(value is None for value in quantiles([]).values())


def test_aggregate_is_one_row_per_primitive_and_reused(tmp_path):
    corpus = tmp_path / "corpus.sqlite"
    connection = sqlite3.connect(corpus)
    connection.execute("CREATE TABLE primitive_evidence_inputs(primitive_id TEXT,evidence_id TEXT,PRIMARY KEY(primitive_id,evidence_id))")
    connection.executemany("INSERT INTO primitive_evidence_inputs VALUES(?,?)",
                           (("p1", "e1"), ("p1", "e2"), ("p2", "e3")))
    connection.commit(); connection.close()
    analysis = tmp_path / "analysis.sqlite"
    first = ensure_primitive_aggregate(corpus, analysis)
    second = ensure_primitive_aggregate(corpus, analysis)
    assert first["rows"] == 2 and not first["reused"]
    assert second == {"reused": True, "rows": 2, "runtime_seconds": 0.0}
    connection = sqlite3.connect(analysis)
    assert connection.execute("SELECT * FROM primitive_input_counts ORDER BY primitive_id").fetchall() == [("p1", 2), ("p2", 1)]


def test_family_matrix_is_bounded_and_reused(tmp_path):
    corpus = tmp_path / "corpus.sqlite"
    connection = sqlite3.connect(corpus)
    connection.execute("CREATE TABLE primitive_evidence_inputs(primitive_id TEXT,evidence_id TEXT)")
    connection.execute("CREATE TABLE normalized_evidence_records(evidence_id TEXT,fact_family TEXT)")
    connection.execute("CREATE TABLE primitive_observations(primitive_id TEXT,primitive_type TEXT)")
    connection.executemany("INSERT INTO primitive_evidence_inputs VALUES(?,?)", (("p1", "e1"), ("p1", "e2"), ("p2", "e1")))
    connection.executemany("INSERT INTO normalized_evidence_records VALUES(?,?)", (("e1", "A"), ("e2", "B")))
    connection.executemany("INSERT INTO primitive_observations VALUES(?,?)", (("p1", "X"), ("p2", "Y")))
    connection.commit(); connection.close()
    analysis = tmp_path / "analysis.sqlite"
    assert ensure_family_matrix(corpus, analysis)["rows"] == 3
    assert ensure_family_matrix(corpus, analysis)["reused"]


def test_transaction_aggregate_streams_and_reuses(tmp_path):
    corpus = tmp_path / "corpus.sqlite"
    connection = sqlite3.connect(corpus)
    connection.execute("CREATE TABLE normalized_evidence_records(evidence_id TEXT,raw_artifact_digest TEXT)")
    connection.execute("CREATE TABLE primitive_evidence_inputs(primitive_id TEXT,evidence_id TEXT)")
    connection.executemany("INSERT INTO normalized_evidence_records VALUES(?,?)", (("e1", "d1"), ("e2", "d1")))
    connection.executemany("INSERT INTO primitive_evidence_inputs VALUES(?,?)", (("p1", "e1"), ("p1", "e2"), ("p2", "e2")))
    connection.commit(); connection.close()
    attempts = [{"raw_artifact_digest": "d1", "target_signature": "sig", "launch_id": "mint", "dependency_type": "CREATION"}]
    analysis = tmp_path / "analysis.sqlite"
    first = ensure_transaction_aggregate(corpus, analysis, attempts)
    second = ensure_transaction_aggregate(corpus, analysis, attempts)
    assert first["mapped_primitive_links"] == 3
    assert second == {"reused": True, "rows": 1, "runtime_seconds": 0.0}
