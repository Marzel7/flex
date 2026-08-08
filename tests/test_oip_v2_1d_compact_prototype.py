import sqlite3

from scripts.prototype_oip_v2_1d_compact_links import relation_digest, schema


def test_compact_view_preserves_relation_and_immutability(tmp_path):
    database = tmp_path / "prototype.sqlite"
    connection = sqlite3.connect(database)
    schema(connection)
    connection.executemany("INSERT INTO primitive_identity VALUES(?,?)", ((1, "p1"), (2, "p2")))
    connection.executemany("INSERT INTO evidence_identity VALUES(?,?)", ((1, "e1"), (2, "e2")))
    connection.executemany("INSERT INTO compact_primitive_evidence_inputs VALUES(?,?)", ((1, 1), (1, 2), (2, 1)))
    expected = [("p1", "e1"), ("p1", "e2"), ("p2", "e1")]
    assert connection.execute("SELECT * FROM primitive_evidence_inputs ORDER BY 1,2").fetchall() == expected
    assert relation_digest(iter(expected)) == relation_digest(connection.execute(
        "SELECT * FROM primitive_evidence_inputs ORDER BY 1,2"))
    connection.execute("INSERT INTO primitive_evidence_inputs VALUES('p2','e2')")
    assert connection.execute("SELECT COUNT(*) FROM compact_primitive_evidence_inputs").fetchone()[0] == 4
    try:
        connection.execute("DELETE FROM primitive_evidence_inputs")
    except sqlite3.DatabaseError as error:
        assert "immutable" in str(error)
    else:
        raise AssertionError("delete should be rejected")
