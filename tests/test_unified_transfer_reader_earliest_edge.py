"""UnifiedTransferReader.earliest_edge -- true globally-earliest transfer
for an address across HOT and COLD tiers, using per-connection LIMIT 1
pushdown rather than Python-side materialization of full result sets.
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

SCHEMA = """
CREATE TABLE transfer_index (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signature TEXT NOT NULL,
    source TEXT NOT NULL,
    destination TEXT NOT NULL,
    amount_lamports INTEGER NOT NULL,
    block_time INTEGER NOT NULL,
    indexed_at REAL NOT NULL
);
"""


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def _insert(conn, *, sig, source, dest, amount, block_time):
    conn.execute(
        "INSERT INTO transfer_index (signature, source, destination, amount_lamports, block_time, indexed_at) "
        "VALUES (?,?,?,?,?,?)",
        (sig, source, dest, amount, block_time, time.time()),
    )
    conn.commit()


def test_earliest_edge_prefers_cold_when_older():
    hot = _conn()
    cold = _conn()
    _insert(hot, sig="SIG_HOT", source="ADDR1", dest="OTHER1", amount=100, block_time=2000)
    _insert(cold, sig="SIG_COLD", source="OTHER2", dest="ADDR1", amount=200, block_time=1000)
    reader = UnifiedTransferReader(hot_conn=hot, cold_conns=[cold])

    row = reader.earliest_edge("ADDR1")

    assert row is not None
    assert row[4] == 1000
    assert row[0] == "SIG_COLD"


def test_earliest_edge_hot_only():
    hot = _conn()
    cold = _conn()
    _insert(hot, sig="SIG_HOT_ONLY", source="ADDR2", dest="OTHER3", amount=50, block_time=500)
    reader = UnifiedTransferReader(hot_conn=hot, cold_conns=[cold])

    row = reader.earliest_edge("ADDR2")

    assert row is not None
    assert row[4] == 500
    assert row[0] == "SIG_HOT_ONLY"


def test_earliest_edge_not_found_returns_none():
    hot = _conn()
    cold = _conn()
    reader = UnifiedTransferReader(hot_conn=hot, cold_conns=[cold])

    assert reader.earliest_edge("NOWHERE") is None
