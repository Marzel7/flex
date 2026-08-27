from src.ops.potential_operations import DECOMPOSED_063E_PARENT, LEGACY_063E_CHILD
import sqlite3
from src.ops import potential_operations as po
def test_063e_child_ids_are_not_interchangeable():
    assert DECOMPOSED_063E_PARENT != LEGACY_063E_CHILD


def test_normalization_is_explicit_and_idempotent(tmp_path, monkeypatch):
    ranking=tmp_path/'ranking.json'; ranking.write_text('{"families":[{"candidate_id":"x","canonical_tier":"T","new_rank":1,"operational_likeness":1,"activity_score":1,"operation_priority_score":1}]}')
    monkeypatch.setattr(po, 'RANKING', ranking)
    conn=sqlite3.connect(':memory:')
    assert po.normalize_potential_operation_workflows(conn, apply=False)['rows_to_create'] == 1
    assert conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='potential_operation_workflows'").fetchone()[0] == 0
    assert po.normalize_potential_operation_workflows(conn, apply=True)['rows_to_create'] == 1
    assert po.normalize_potential_operation_workflows(conn, apply=False)['rows_to_create'] == 0
