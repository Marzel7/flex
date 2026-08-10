def test_autocommit_ddl_releases_write_lane_before_next_statement(tmp_path, monkeypatch):
    """No-op/autocommit DDL must not leave the thread guard owned."""
    from src.utils import db_locking

    monkeypatch.setattr(db_locking, "_DB_WRITE_SERIALIZE", True)
    path = tmp_path / "autocommit.db"
    conn = db_locking.db_connect(str(path), timeout=2)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE IF NOT EXISTS first_table (id INTEGER PRIMARY KEY)")
        cursor.execute("CREATE TABLE IF NOT EXISTS second_table (id INTEGER PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()
