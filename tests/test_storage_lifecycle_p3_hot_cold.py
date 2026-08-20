"""STORAGE-LIFECYCLE-P3: hot/cold segmentation and query-parity tests.

All tests use isolated tmp_path fixtures with synthetic data modeled on
the real schema/scale patterns (including a Dv34-shaped and a
Watchtower-shaped control case). No real production database is written
to. No provider calls.
"""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ops.transfer_cold_store import (  # noqa: E402
    close_segment,
    create_cold_segment,
    is_segment_closed,
    migrate_rows_to_cold,
    segment_name_for_month,
)
from src.ops.unified_transfer_reader import UnifiedTransferReader  # noqa: E402

HOT_SCHEMA = """
CREATE TABLE transfer_index (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signature TEXT NOT NULL,
    source TEXT NOT NULL,
    destination TEXT NOT NULL,
    amount_lamports INTEGER NOT NULL,
    slot INTEGER NOT NULL DEFAULT 0,
    block_time INTEGER NOT NULL,
    indexed_at REAL NOT NULL,
    is_valid BOOLEAN NOT NULL DEFAULT 1,
    transfer_type TEXT DEFAULT 'standard',
    UNIQUE (signature, source, destination)
);
"""

DV34 = "Dv34prGm2BT7Ph2n6qKLgzeLgjnii87RJJ7Db6ZQQvKM"
WATCHTOWER_TREASURY = "5E1RvuNqoAmFhF3rjEUUdMFXkdaZQVJXQTP1RjBcTest"


@pytest.fixture
def hot_db(tmp_path):
    path = str(tmp_path / "hot.db")
    conn = sqlite3.connect(path)
    conn.executescript(HOT_SCHEMA)
    conn.commit()
    conn.close()
    return path


def _insert(conn, *, sig, source, dest, amount, block_time):
    conn.execute(
        "INSERT INTO transfer_index (signature, source, destination, amount_lamports, block_time, indexed_at) "
        "VALUES (?,?,?,?,?,?)",
        (sig, source, dest, amount, block_time, time.time()),
    )


# ── Segment naming / manifest ────────────────────────────────────────────

def test_segment_name_time_partitioned():
    assert segment_name_for_month(2026, 8) == "transfer_index_cold_2026_08.sqlite"
    assert segment_name_for_month(2026, 12) == "transfer_index_cold_2026_12.sqlite"


def test_create_cold_segment_is_idempotent(tmp_path):
    dest = str(tmp_path / "seg.sqlite")
    create_cold_segment(dest, month_covered="2026-08")
    create_cold_segment(dest, month_covered="2026-08")  # must not raise
    conn = sqlite3.connect(dest)
    count = conn.execute("SELECT COUNT(*) FROM segment_manifest").fetchone()[0]
    conn.close()
    assert count == 1


# ── Migration: copy, verify, no source mutation ──────────────────────────

def test_migration_copies_rows_without_deleting_source(hot_db, tmp_path):
    conn = sqlite3.connect(hot_db)
    for i in range(50):
        _insert(conn, sig=f"sig{i}", source="A", dest="B", amount=1_000_000, block_time=1_700_000_000 + i)
    conn.commit()

    dest = str(tmp_path / "cold.sqlite")
    result = migrate_rows_to_cold(conn, dest, where_clause="1=1", params=(), run_id="test-run")

    assert result.rows_migrated == 50
    assert result.rows_verified == 50

    # source untouched
    remaining = conn.execute("SELECT COUNT(*) FROM transfer_index").fetchone()[0]
    assert remaining == 50
    conn.close()


def test_migration_deterministic_digest(hot_db, tmp_path):
    conn = sqlite3.connect(hot_db)
    for i in range(10):
        _insert(conn, sig=f"sig{i}", source="A", dest="B", amount=1_000_000, block_time=1_700_000_000 + i)
    conn.commit()

    dest1 = str(tmp_path / "cold1.sqlite")
    dest2 = str(tmp_path / "cold2.sqlite")
    r1 = migrate_rows_to_cold(conn, dest1, where_clause="1=1", params=(), run_id="run1")
    r2 = migrate_rows_to_cold(conn, dest2, where_clause="1=1", params=(), run_id="run2")
    conn.close()
    assert r1.signatures_digest == r2.signatures_digest


def test_migration_bounded_batches_no_single_giant_transaction(hot_db, tmp_path):
    conn = sqlite3.connect(hot_db)
    for i in range(250):
        _insert(conn, sig=f"sig{i}", source="A", dest="B", amount=1_000_000, block_time=1_700_000_000 + i)
    conn.commit()

    dest = str(tmp_path / "cold.sqlite")
    result = migrate_rows_to_cold(conn, dest, where_clause="1=1", params=(), batch_size=50, run_id="test")
    assert result.rows_migrated == 250
    conn.close()


def test_close_segment_marks_immutable(hot_db, tmp_path):
    conn = sqlite3.connect(hot_db)
    for i in range(5):
        _insert(conn, sig=f"sig{i}", source="A", dest="B", amount=1_000_000, block_time=1_700_000_000 + i)
    conn.commit()

    dest = str(tmp_path / "cold.sqlite")
    migrate_rows_to_cold(conn, dest, where_clause="1=1", params=(), run_id="test")
    conn.close()

    assert not is_segment_closed(dest)
    close_segment(dest, source_run_id="test")
    assert is_segment_closed(dest)


def test_no_vacuum_in_cold_store_module():
    src = (ROOT / "src/ops/transfer_cold_store.py").read_text()
    for line in src.splitlines():
        if ".execute(" in line or "executescript(" in line or "executemany(" in line:
            assert "VACUUM" not in line.upper()


def test_no_delete_from_hot_in_cold_store_module():
    """Migration is copy-only; deletion from the live HOT table is
    explicitly a SEPARATE, later, human-authorized step -- not performed
    by this module at all."""
    src = (ROOT / "src/ops/transfer_cold_store.py").read_text()
    for line in src.splitlines():
        if ".execute(" in line:
            assert "DELETE FROM" not in line.upper()


# ── Query parity: HOT+COLD union matches monolithic source ───────────────

def test_by_signature_finds_row_in_hot(hot_db):
    conn = sqlite3.connect(hot_db)
    _insert(conn, sig="hotsig", source="A", dest="B", amount=1, block_time=1_700_000_000)
    conn.commit()
    reader = UnifiedTransferReader(hot_conn=conn, cold_conns=[])
    rows = reader.by_signature("hotsig")
    assert len(rows) == 1
    conn.close()


def test_by_signature_finds_row_in_cold_only(hot_db, tmp_path):
    hot_conn = sqlite3.connect(hot_db)
    cold_path = str(tmp_path / "cold.sqlite")
    create_cold_segment(cold_path, month_covered="2026-01")
    cold_conn = sqlite3.connect(cold_path)
    cold_conn.execute(
        "INSERT INTO transfer_index (signature, source, destination, amount_lamports, block_time, indexed_at) "
        "VALUES ('coldsig','A','B',1,1600000000,0)"
    )
    cold_conn.commit()

    reader = UnifiedTransferReader(hot_conn=hot_conn, cold_conns=[cold_conn])
    rows = reader.by_signature("coldsig")
    assert len(rows) == 1
    hot_conn.close(); cold_conn.close()


def test_query_union_no_duplicate_when_row_exists_in_both_during_migration_window(hot_db, tmp_path):
    """During a migration window a row might transiently exist in BOTH
    HOT and COLD (before HOT-side deletion, a separate later step) -- the
    unified reader must never emit it twice."""
    hot_conn = sqlite3.connect(hot_db)
    _insert(hot_conn, sig="dupsig", source="A", dest="B", amount=1, block_time=1_700_000_000)
    hot_conn.commit()

    cold_path = str(tmp_path / "cold.sqlite")
    result = migrate_rows_to_cold(hot_conn, cold_path, where_clause="1=1", params=(), run_id="test")
    assert result.rows_migrated == 1  # copied, not deleted from hot

    cold_conn = sqlite3.connect(cold_path)
    reader = UnifiedTransferReader(hot_conn=hot_conn, cold_conns=[cold_conn])
    rows = reader.by_signature("dupsig")
    assert len(rows) == 1, "must deduplicate even though the row exists in both tiers"
    hot_conn.close(); cold_conn.close()


def test_dv34_control_123_historical_relationships_preserved_across_hot_cold(hot_db, tmp_path):
    """Reference control per Part 17: simulate Dv34's 123 historical
    funding relationships split across HOT (recent) and COLD (older),
    prove the unified reader still surfaces all 123, not a HIGH-only
    subset."""
    hot_conn = sqlite3.connect(hot_db)
    now = 1_787_000_000
    # 100 "old" rows go to cold, 23 "recent" rows stay hot -- arbitrary
    # split chosen only to exercise cross-tier aggregation, NOT meant to
    # imply the real 100/23 split has this exact age boundary.
    for i in range(123):
        block_time = now - (200 * 86400) if i < 100 else now - (i * 3600)
        _insert(hot_conn, sig=f"dv34sig{i}", source=DV34, dest=f"creator{i}", amount=10_000_000, block_time=block_time)
    hot_conn.commit()

    cold_path = str(tmp_path / "cold_2026_01.sqlite")
    migrate_rows_to_cold(hot_conn, cold_path, where_clause="block_time < ?", params=(now - 100 * 86400,), run_id="dv34-test")
    # NOTE: rows remain in hot_conn too (copy-only) -- simulate post-
    # deletion state by removing the migrated rows from hot for this
    # parity check, since a real cutover WOULD eventually delete them.
    hot_conn.execute("DELETE FROM transfer_index WHERE block_time < ?", (now - 100 * 86400,))
    hot_conn.commit()

    cold_conn = sqlite3.connect(cold_path)
    reader = UnifiedTransferReader(hot_conn=hot_conn, cold_conns=[cold_conn])
    all_rows = reader.by_source(DV34, limit=1000)
    assert len(all_rows) == 123, "unified HOT+COLD view must preserve all 123 historical relationships, not just the recent/HOT subset"
    hot_conn.close(); cold_conn.close()


def test_watchtower_control_funding_history_complete_across_tiers(hot_db, tmp_path):
    """Reference control per Part 16: Watchtower-shaped funding history
    split across HOT/COLD must remain fully queryable, count unchanged."""
    hot_conn = sqlite3.connect(hot_db)
    now = 1_787_000_000
    for i in range(50):
        block_time = now - (400 * 86400) if i < 40 else now - (i * 3600)
        _insert(hot_conn, sig=f"wtsig{i}", source=WATCHTOWER_TREASURY, dest=f"subprov{i}", amount=50_000_000_000, block_time=block_time)
    hot_conn.commit()

    cold_path = str(tmp_path / "cold_watchtower.sqlite")
    migrate_rows_to_cold(hot_conn, cold_path, where_clause="block_time < ?", params=(now - 200 * 86400,), run_id="wt-test")
    hot_conn.execute("DELETE FROM transfer_index WHERE block_time < ?", (now - 200 * 86400,))
    hot_conn.commit()

    cold_conn = sqlite3.connect(cold_path)
    reader = UnifiedTransferReader(hot_conn=hot_conn, cold_conns=[cold_conn])
    rows = reader.by_source(WATCHTOWER_TREASURY, limit=1000)
    assert len(rows) == 50, "Watchtower funding history must remain complete across HOT+COLD tiers"
    hot_conn.close(); cold_conn.close()


def test_time_range_query_spans_hot_and_cold(hot_db, tmp_path):
    hot_conn = sqlite3.connect(hot_db)
    now = 1_787_000_000
    for i in range(20):
        block_time = now - (300 * 86400) + (i * 86400)
        _insert(hot_conn, sig=f"trsig{i}", source="A", dest="B", amount=1, block_time=block_time)
    hot_conn.commit()

    cold_path = str(tmp_path / "cold_range.sqlite")
    migrate_rows_to_cold(hot_conn, cold_path, where_clause="block_time < ?", params=(now - 150 * 86400,), run_id="range-test")
    hot_conn.execute("DELETE FROM transfer_index WHERE block_time < ?", (now - 150 * 86400,))
    hot_conn.commit()

    cold_conn = sqlite3.connect(cold_path)
    reader = UnifiedTransferReader(hot_conn=hot_conn, cold_conns=[cold_conn])
    rows = reader.by_time_range(now - 300 * 86400, now, limit=1000)
    assert len(rows) == 20
    hot_conn.close(); cold_conn.close()


def test_creator_funding_parity_2hop(hot_db, tmp_path):
    """2-hop lineage: A funds B (hop 1), B funds C (hop 2) -- must remain
    traceable across tiers."""
    hot_conn = sqlite3.connect(hot_db)
    now = 1_787_000_000
    _insert(hot_conn, sig="hop1", source="UPSTREAM", dest="MIDDLE", amount=1, block_time=now - 300 * 86400)
    _insert(hot_conn, sig="hop2", source="MIDDLE", dest="CREATOR", amount=1, block_time=now - 1 * 86400)
    hot_conn.commit()

    cold_path = str(tmp_path / "cold_2hop.sqlite")
    migrate_rows_to_cold(hot_conn, cold_path, where_clause="block_time < ?", params=(now - 150 * 86400,), run_id="2hop-test")
    hot_conn.execute("DELETE FROM transfer_index WHERE block_time < ?", (now - 150 * 86400,))
    hot_conn.commit()

    cold_conn = sqlite3.connect(cold_path)
    reader = UnifiedTransferReader(hot_conn=hot_conn, cold_conns=[cold_conn])
    hop1 = reader.by_destination("MIDDLE")
    hop2 = reader.by_source("MIDDLE")
    assert len(hop1) == 1 and hop1[0][2] == "MIDDLE"
    assert len(hop2) == 1 and hop2[0][1] == "MIDDLE"
    hot_conn.close(); cold_conn.close()


# ── Restart safety / resumability ────────────────────────────────────────

def test_migration_resumable_insert_or_ignore_prevents_duplicates(hot_db, tmp_path):
    """If a migration is interrupted and re-run, INSERT OR IGNORE + the
    UNIQUE constraint prevents duplicate rows in the cold segment."""
    hot_conn = sqlite3.connect(hot_db)
    for i in range(10):
        _insert(hot_conn, sig=f"resumesig{i}", source="A", dest="B", amount=1, block_time=1_700_000_000 + i)
    hot_conn.commit()

    dest = str(tmp_path / "resume.sqlite")
    migrate_rows_to_cold(hot_conn, dest, where_clause="1=1", params=(), run_id="run1")
    result2 = migrate_rows_to_cold(hot_conn, dest, where_clause="1=1", params=(), run_id="run2")  # re-run
    hot_conn.close()

    assert result2.rows_verified == 10, "re-running migration must not create duplicates"


# ── No forbidden operations ───────────────────────────────────────────────

def test_no_vacuum_in_unified_reader():
    src = (ROOT / "src/ops/unified_transfer_reader.py").read_text()
    assert "VACUUM" not in src.upper()


def test_unified_reader_is_read_only_no_write_statements():
    src = (ROOT / "src/ops/unified_transfer_reader.py").read_text()
    for line in src.splitlines():
        if ".execute(" in line:
            upper = line.upper()
            assert "INSERT" not in upper
            assert "UPDATE" not in upper
            assert "DELETE" not in upper


def test_this_test_module_never_targets_real_production_db():
    src = Path(__file__).read_text()
    for line in src.splitlines():
        if "sqlite3.connect(" in line:
            assert "flex_complete_database.db" not in line
            assert "wt_ops_v2.db" not in line
