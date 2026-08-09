import sqlite3

import pytest

from src.evidence.compact_provenance import CompactProvenanceRepository


def repository():
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.execute("PRAGMA foreign_keys=ON")
    CompactProvenanceRepository.install(connection)
    return connection, CompactProvenanceRepository(connection)


def test_external_query_contract_and_duplicate_replay():
    connection, compact = repository()
    result = compact.append((("p2", "e1"), ("p1", "e2"), ("p1", "e1"), ("p1", "e1")))
    assert result == {"inserted": 3, "duplicates": 1}
    assert compact.evidence_for_primitive("p1") == ("e1", "e2")
    assert compact.primitives_for_evidence("e1") == ("p1", "p2")
    assert compact.contains("p1", "e2")
    assert not compact.contains("missing", "e2")
    assert tuple(sorted(compact.ordered_pairs())) == (("p1", "e1"), ("p1", "e2"), ("p2", "e1"))
    assert compact.append((("p1", "e1"),)) == {"inserted": 0, "duplicates": 1}
    compact.assert_integrity()
    connection.close()


def test_identity_and_relation_insert_are_atomic():
    connection, compact = repository()
    connection.execute("""CREATE TRIGGER fail_relation BEFORE INSERT ON compact_primitive_evidence_inputs
      WHEN NEW.evidence_key=(SELECT evidence_key FROM evidence_identity WHERE evidence_id='bad')
      BEGIN SELECT RAISE(ABORT,'fixture interruption'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="fixture interruption"):
        compact.append((("new-p", "bad"),))
    assert connection.execute("SELECT COUNT(*) FROM primitive_identity").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM evidence_identity").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM compact_primitive_evidence_inputs").fetchone()[0] == 0
    connection.close()


def test_integrity_check_fails_closed_for_corrupt_mapping():
    connection, compact = repository()
    compact.append((("p1", "e1"),))
    connection.execute("PRAGMA foreign_keys=OFF")
    connection.execute("DELETE FROM evidence_identity")
    with pytest.raises(sqlite3.IntegrityError, match="unresolved identity"):
        compact.assert_integrity()
    connection.close()
