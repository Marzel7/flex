import sqlite3
import json

import pytest

from scripts.extract_historical_features import run


def _build_db(connection: sqlite3.Connection) -> None:
    connection.execute("CREATE TABLE wt_walkback_queue (mint TEXT PRIMARY KEY, creator TEXT, funder_wallet TEXT)")
    connection.execute(
        "CREATE TABLE wt_walkback_edge_candidates (mint TEXT, candidate_parent TEXT, mechanism TEXT, hop_depth INTEGER)"
    )
    connection.executemany(
        "INSERT INTO wt_walkback_queue VALUES (?, ?, ?)",
        [("mint_a", "creator_a", "funder_a"), ("mint_b", "creator_b", "funder_b")],
    )
    connection.executemany(
        "INSERT INTO wt_walkback_edge_candidates VALUES (?, ?, ?, ?)",
        [("mint_a", "parent_a", "method_x", 1), ("mint_b", "parent_b", "method_y", 2)],
    )
    connection.commit()


def _line_count(path):
    return len(path.read_text().splitlines()) if path.exists() else 0


def _latest_ck(path):
    return json.loads(path.read_text()) if path.exists() else None


def test_inflight_checkpoint_failure_does_not_advance_checkpoint_or_output(tmp_path):
    db = tmp_path / "ops.sqlite"
    conn = sqlite3.connect(db)
    _build_db(conn)
    out = tmp_path / "p3r_historical_features.jsonl"
    ck = tmp_path / "p3r_historical_features.checkpoint.json"
    inflight = tmp_path / "p3r_historical_features.checkpoint.json.inflight"

    def inject(phase):
        if phase == "checkpoint_inflight":
            raise RuntimeError("inflig")

    with pytest.raises(RuntimeError, match="inflig"):
        run(database=db, output=out, checkpoint_path=ck, chunk_size=1, inject=inject)

    assert _line_count(out) == 0
    assert not ck.exists()
    assert inflight.exists()

    result = run(database=db, output=out, checkpoint_path=ck, chunk_size=1)
    assert result["rows"] == 1
    assert _line_count(out) == 1
    assert _latest_ck(ck) == {"chunk": 1, "last_mint": "mint_a", "rows": 1}
    assert not inflight.exists()


def test_output_boundary_failure_recovers_and_deduplicates(tmp_path):
    db = tmp_path / "ops.sqlite"
    conn = sqlite3.connect(db)
    _build_db(conn)
    out = tmp_path / "p3r_historical_features.jsonl"
    ck = tmp_path / "p3r_historical_features.checkpoint.json"

    def inject(phase):
        if phase == "output_appended":
            raise RuntimeError("output-failed")

    with pytest.raises(RuntimeError, match="output-failed"):
        run(database=db, output=out, checkpoint_path=ck, chunk_size=2, inject=inject)

    assert _line_count(out) == 2
    assert not ck.exists()

    result = run(database=db, output=out, checkpoint_path=ck, chunk_size=2)
    assert result["rows"] == 2
    assert _line_count(out) == 2


def test_checkpoint_commit_boundary_failure_is_idempotent_on_resume(tmp_path):
    db = tmp_path / "ops.sqlite"
    conn = sqlite3.connect(db)
    _build_db(conn)
    out = tmp_path / "p3r_historical_features.jsonl"
    ck = tmp_path / "p3r_historical_features.checkpoint.json"

    def inject(phase):
        if phase == "checkpoint_committed":
            raise RuntimeError("checkpoint-failed")

    with pytest.raises(RuntimeError, match="checkpoint-failed"):
        run(database=db, output=out, checkpoint_path=ck, chunk_size=2, inject=inject)

    assert _line_count(out) == 2
    assert _latest_ck(ck) == {"chunk": 1, "last_mint": "mint_b", "rows": 2}

    result = run(database=db, output=out, checkpoint_path=ck, chunk_size=2)
    assert result["rows"] == 2
    assert _line_count(out) == 2
