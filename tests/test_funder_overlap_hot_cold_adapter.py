"""UnifiedTransferReader.funder_creator_pairs / funder_creator_map --
HOT+COLD reproduction of FunderCreatorExtractor.extract_funder_creator_pairs's
SQL (src/core/funder_overlap_analysis.py):

    SELECT DISTINCT source AS funder_wallet, destination AS creator_wallet
    FROM transfer_index
    WHERE amount_sol BETWEEN 0.5 AND 10 AND is_valid = 1

Follows the fixture style of test_unified_transfer_reader_earliest_edge.py.
"""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ops.unified_transfer_reader import UnifiedTransferReader  # noqa: E402
from src.ops.cold_segment_registry import ColdSegmentRegistry, TransferReaderFactory  # noqa: E402

SCHEMA = """
CREATE TABLE transfer_index (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signature TEXT NOT NULL,
    source TEXT NOT NULL,
    destination TEXT NOT NULL,
    amount_lamports INTEGER NOT NULL,
    block_time INTEGER NOT NULL,
    indexed_at REAL NOT NULL,
    is_valid BOOLEAN NOT NULL DEFAULT 1
);
"""

LAMPORTS_PER_SOL = 1_000_000_000


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def _insert(conn, *, sig, source, dest, sol, block_time, is_valid=1):
    conn.execute(
        "INSERT INTO transfer_index (signature, source, destination, amount_lamports, "
        "block_time, indexed_at, is_valid) VALUES (?,?,?,?,?,?,?)",
        (sig, source, dest, int(sol * LAMPORTS_PER_SOL), block_time, time.time(), is_valid),
    )
    conn.commit()


def test_funder_in_both_tiers_funding_different_creators_sums_distinct_sets():
    """Same funder appears in HOT and COLD, funding DIFFERENT creators in
    each tier. The merged distinct-destination count must be the sum of
    the two tiers' distinct sets (no overlap, so no double-counting risk
    here -- this is the baseline case)."""
    hot = _conn()
    cold = _conn()
    _insert(hot, sig="H1", source="FUNDER1", dest="CREATOR_HOT_A", sol=1.0, block_time=2000)
    _insert(hot, sig="H2", source="FUNDER1", dest="CREATOR_HOT_B", sol=2.0, block_time=2001)
    _insert(cold, sig="C1", source="FUNDER1", dest="CREATOR_COLD_A", sol=3.0, block_time=1000)
    reader = UnifiedTransferReader(hot_conn=hot, cold_conns=[cold])

    creators = reader.funder_creator_map()["FUNDER1"]

    assert creators == {"CREATOR_HOT_A", "CREATOR_HOT_B", "CREATOR_COLD_A"}
    assert len(creators) == 3  # 2 (HOT) + 1 (COLD), no overlap


def test_funder_funds_same_creator_in_both_tiers_counts_once():
    """Critical correctness case: the SAME funder funds the SAME creator
    in both HOT (recent transfer) and COLD (older transfer) -- two
    separate signatures/timestamps, same (source, destination) pair. A
    naive 'sum the per-tier distinct-destination counts' implementation
    would double-count this creator (1 + 1 = 2); the correct merged
    distinct count is 1."""
    hot = _conn()
    cold = _conn()
    _insert(hot, sig="H_RECENT", source="FUNDER2", dest="CREATOR_SHARED", sol=1.5, block_time=9000)
    _insert(cold, sig="C_OLD", source="FUNDER2", dest="CREATOR_SHARED", sol=1.5, block_time=1000)
    reader = UnifiedTransferReader(hot_conn=hot, cold_conns=[cold])

    creators = reader.funder_creator_map()["FUNDER2"]

    assert creators == {"CREATOR_SHARED"}
    assert len(creators) == 1  # NOT 2


def test_amount_and_validity_filters_pushed_down_per_connection():
    """WHERE amount_sol BETWEEN 0.5 AND 10 AND is_valid=1 must be applied
    identically to hot_conn and every cold_conn -- rows outside the amount
    band or with is_valid=0 must not appear, in either tier."""
    hot = _conn()
    cold = _conn()
    _insert(hot, sig="H_OK", source="FUNDER3", dest="CREATOR_OK", sol=5.0, block_time=100)
    _insert(hot, sig="H_TOO_SMALL", source="FUNDER3", dest="CREATOR_TOO_SMALL", sol=0.1, block_time=101)
    _insert(hot, sig="H_TOO_BIG", source="FUNDER3", dest="CREATOR_TOO_BIG", sol=50.0, block_time=102)
    _insert(hot, sig="H_INVALID", source="FUNDER3", dest="CREATOR_INVALID", sol=2.0, block_time=103, is_valid=0)
    _insert(cold, sig="C_OK", source="FUNDER3", dest="CREATOR_COLD_OK", sol=0.5, block_time=50)
    _insert(cold, sig="C_TOO_SMALL", source="FUNDER3", dest="CREATOR_COLD_TOO_SMALL", sol=0.49, block_time=51)
    reader = UnifiedTransferReader(hot_conn=hot, cold_conns=[cold])

    creators = reader.funder_creator_map()["FUNDER3"]

    assert creators == {"CREATOR_OK", "CREATOR_COLD_OK"}


def test_limit_boundary_tie_does_not_crash_and_returns_sensible_result():
    """Two funders tied at the same shared-creator count. The original
    production query (FunderCreatorExtractor.extract_funder_creator_pairs)
    has no ORDER BY / LIMIT / secondary sort key at all -- it is an
    unordered DISTINCT extraction consumed into a Python dict-of-sets, so
    this adapter deliberately does not assert any particular row order or
    tie-break; it only asserts the two tied funders both come back with
    correct, complete creator sets."""
    hot = _conn()
    cold = _conn()
    _insert(hot, sig="H1", source="FUNDER_TIE_A", dest="CREATOR_X", sol=1.0, block_time=10)
    _insert(hot, sig="H2", source="FUNDER_TIE_A", dest="CREATOR_Y", sol=1.0, block_time=11)
    _insert(cold, sig="C1", source="FUNDER_TIE_B", dest="CREATOR_Z", sol=1.0, block_time=5)
    _insert(cold, sig="C2", source="FUNDER_TIE_B", dest="CREATOR_W", sol=1.0, block_time=6)
    reader = UnifiedTransferReader(hot_conn=hot, cold_conns=[cold])

    fmap = reader.funder_creator_map()

    assert len(fmap["FUNDER_TIE_A"]) == 2
    assert len(fmap["FUNDER_TIE_B"]) == 2
    assert fmap["FUNDER_TIE_A"] == {"CREATOR_X", "CREATOR_Y"}
    assert fmap["FUNDER_TIE_B"] == {"CREATOR_Z", "CREATOR_W"}


def test_factory_reuses_connections_across_calls(tmp_path):
    """TransferReaderFactory / ColdSegmentRegistry must not open a new
    connection per request -- calling get_transfer_reader() twice on the
    same factory instance should hand back the SAME underlying
    sqlite3.Connection objects both times."""
    hot_db = tmp_path / "hot.db"
    conn = sqlite3.connect(str(hot_db))
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()

    cold_root = tmp_path / "cold"
    cold_root.mkdir()
    cold_path = cold_root / "transfer_index_cold_2026_01.sqlite"
    cold_conn = sqlite3.connect(str(cold_path))
    cold_conn.executescript(SCHEMA)
    cold_conn.execute(
        "CREATE TABLE segment_manifest (segment_id TEXT, month_covered TEXT, "
        "row_count INTEGER, closed_at REAL)"
    )
    cold_conn.execute(
        "INSERT INTO segment_manifest VALUES ('seg1', '2026-01', 0, ?)", (time.time(),)
    )
    cold_conn.commit()
    cold_conn.close()

    factory = TransferReaderFactory(hot_db_path=str(hot_db), cold_root=str(cold_root))
    try:
        reader1 = factory.get_transfer_reader(fail_closed=True)
        reader2 = factory.get_transfer_reader(fail_closed=True)

        assert reader1.hot_conn is reader2.hot_conn
        assert len(reader1.cold_conns) == 1
        assert reader1.cold_conns[0] is reader2.cold_conns[0]

        # Same check directly against the registry, independent of the
        # factory-level reader wrapping.
        registry = factory._get_registry()
        conns_a = registry.connections
        conns_b = registry.connections
        assert conns_a == conns_b
        assert conns_a[0] is conns_b[0]
    finally:
        factory.close()
