import sqlite3


def test_retained_farm_evidence_remains_readable_without_a_production_writer():
    """Retirement preserves historical rows; it does not recreate a Farm producer."""
    c = sqlite3.connect(":memory:")
    c.executescript("""
        CREATE TABLE wt_farm_launch_evidence (
            mint TEXT PRIMARY KEY, funder TEXT, creator TEXT, role_evidence_key TEXT
        );
        INSERT INTO wt_farm_launch_evidence VALUES ('mint', 'source', 'creator', 'role');
    """)
    assert c.execute(
        "SELECT funder, creator, role_evidence_key FROM wt_farm_launch_evidence WHERE mint='mint'"
    ).fetchone() == ("source", "creator", "role")
