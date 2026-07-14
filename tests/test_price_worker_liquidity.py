import sqlite3

from src.core.price_worker import record_low_liquidity


def test_record_low_liquidity_sets_sticky_flags(tmp_path):
    db_path = tmp_path / "liquidity.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE token_pool_accounts (
            mint TEXT,
            is_active INTEGER,
            quote_liquidity REAL,
            updated_at INTEGER,
            liquidity_removed BOOLEAN DEFAULT 0,
            liquidity_removed_at INTEGER
        )
        """
    )
    conn.execute(
        """
        INSERT INTO token_pool_accounts (
            mint, is_active, quote_liquidity, updated_at, liquidity_removed, liquidity_removed_at
        ) VALUES (?, 1, 0, 0, 0, NULL)
        """,
        ("mint-a",),
    )
    conn.commit()
    conn.close()

    record_low_liquidity("mint-a", 0.42, db_path=str(db_path))

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        """
        SELECT quote_liquidity, liquidity_removed, liquidity_removed_at
        FROM token_pool_accounts
        WHERE mint = ?
        """,
        ("mint-a",),
    ).fetchone()
    conn.close()

    assert row is not None
    assert row[0] == 0.42
    assert row[1] == 1
    assert row[2] is not None


def test_record_low_liquidity_preserves_original_removed_timestamp(tmp_path):
    db_path = tmp_path / "liquidity.db"
    original_ts = 1710000000

    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE token_pool_accounts (
            mint TEXT,
            is_active INTEGER,
            quote_liquidity REAL,
            updated_at INTEGER,
            liquidity_removed BOOLEAN DEFAULT 0,
            liquidity_removed_at INTEGER
        )
        """
    )
    conn.execute(
        """
        INSERT INTO token_pool_accounts (
            mint, is_active, quote_liquidity, updated_at, liquidity_removed, liquidity_removed_at
        ) VALUES (?, 1, 2.5, 0, 1, ?)
        """,
        ("mint-a", original_ts),
    )
    conn.commit()
    conn.close()

    record_low_liquidity("mint-a", 0.11, db_path=str(db_path))

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        """
        SELECT quote_liquidity, liquidity_removed, liquidity_removed_at
        FROM token_pool_accounts
        WHERE mint = ?
        """,
        ("mint-a",),
    ).fetchone()
    conn.close()

    assert row is not None
    assert row[0] == 0.11
    assert row[1] == 1
    assert row[2] == original_ts
