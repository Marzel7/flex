from __future__ import annotations

from copy import deepcopy

import src.core.token_prediction_builder as prediction_module
from src.core.token_prediction_builder import TokenPredictionBuilder


class _RowsCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return deepcopy(self._rows)


class _RecordingConnection:
    in_transaction = False

    def __init__(self, rows, *, fail_batch=None):
        self.rows = rows
        self.fail_batch = fail_batch
        self.writes = []
        self.commits = 0

    def execute(self, sql, parameters=()):
        assert sql.lstrip().startswith("SELECT")
        return _RowsCursor(self.rows)

    def executemany(self, sql, parameters):
        batch = list(parameters)
        if self.fail_batch == len(self.writes) + 1:
            raise RuntimeError("fixture publication failure")
        self.writes.append((" ".join(sql.split()), batch))

    def commit(self):
        self.commits += 1


def _source_rows(count: int):
    return [
        {
            "mint": f"mint-{index:04d}",
            "prediction_score": 80 if index % 2 else 20,
            "prediction_label": "WATCH",
            "risk_level": "HIGH" if index % 2 else "LOW",
            "migrated_at": 1_700_000_000,
            "created_at": 1_699_999_900,
            "market_cap_current": 10_000,
            "peak_mc": 20_000,
            "market_cap_highest_at_ts": 1_700_000_400,
            "liquidity_removed": 0,
        }
        for index in range(count)
    ]


def test_timer_publication_is_bounded_and_semantically_equal(monkeypatch):
    monkeypatch.setattr(prediction_module.time, "time", lambda: 1_700_010_000)
    monkeypatch.setattr(prediction_module, "record_token_prediction_phase", lambda *a, **k: None)
    builder = TokenPredictionBuilder("unused")
    rows = _source_rows(401)

    old_boundary = _RecordingConnection(rows)
    builder._resolve_outcomes(old_boundary)

    repaired_boundary = _RecordingConnection(rows)
    builder._resolve_outcomes(repaired_boundary, commit_batches=True, batch_size=200)

    old_output = [row for _, batch in old_boundary.writes for row in batch]
    repaired_output = [row for _, batch in repaired_boundary.writes for row in batch]
    assert repaired_output == old_output
    assert [len(batch) for _, batch in repaired_boundary.writes] == [200, 200, 1]
    assert repaired_boundary.commits == 3
    assert old_boundary.commits == 0


def test_main_builder_keeps_existing_single_transaction_boundary(monkeypatch):
    monkeypatch.setattr(prediction_module.time, "time", lambda: 1_700_010_000)
    monkeypatch.setattr(prediction_module, "record_token_prediction_phase", lambda *a, **k: None)
    conn = _RecordingConnection(_source_rows(401))

    TokenPredictionBuilder("unused")._resolve_outcomes(conn)

    assert len(conn.writes) == 1
    assert len(conn.writes[0][1]) == 401
    assert conn.commits == 0


def test_failed_timer_batch_never_publishes_a_partial_row(monkeypatch):
    monkeypatch.setattr(prediction_module.time, "time", lambda: 1_700_010_000)
    monkeypatch.setattr(prediction_module, "record_token_prediction_phase", lambda *a, **k: None)
    conn = _RecordingConnection(_source_rows(401), fail_batch=2)

    try:
        TokenPredictionBuilder("unused")._resolve_outcomes(
            conn, commit_batches=True, batch_size=200
        )
    except RuntimeError as exc:
        assert str(exc) == "fixture publication failure"
    else:
        raise AssertionError("publication failure was not propagated")

    # The first 200 complete, independently keyed outcomes were committed;
    # the failed batch committed no rows and can be retried idempotently.
    assert len(conn.writes) == 1
    assert len(conn.writes[0][1]) == 200
    assert conn.commits == 1

