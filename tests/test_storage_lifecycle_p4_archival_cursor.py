"""STORAGE-LIFECYCLE-P4: keyset archival cursor + conflict-aware reader
tests. All tests use isolated tmp_path/in-memory fixtures. Never touches
real production databases. No provider calls.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ops.transfer_archival_cursor import (  # noqa: E402
    CursorCheckpoint,
    load_checkpoint,
    next_batch,
    run_keyset_archival,
    save_checkpoint,
    start_or_resume_run,
)
from src.ops.transfer_cold_store import COLD_SCHEMA  # noqa: E402
from src.ops.unified_transfer_reader import EvidenceConflict, UnifiedTransferReader  # noqa: E402

SRC_SCHEMA = """
CREATE TABLE transfer_index (
    id INTEGER PRIMARY KEY AUTOINCREMENT, signature TEXT, source TEXT, destination TEXT,
    amount_lamports INTEGER, slot INTEGER DEFAULT 0, block_time INTEGER, indexed_at REAL,
    is_valid INTEGER DEFAULT 1, transfer_type TEXT DEFAULT 'standard'
);
"""


@pytest.fixture
def source_conn():
    conn = sqlite3.connect(":memory:")
    conn.executescript(SRC_SCHEMA)
    return conn


def _seed(conn, n, *, start_id_hint=0):
    for i in range(n):
        conn.execute(
            "INSERT INTO transfer_index (signature,source,destination,amount_lamports,block_time,indexed_at) "
            "VALUES (?,?,?,?,?,?)",
            (f"sig{start_id_hint + i}", "A", "B", 1000, 1_700_000_000 + i, 0),
        )
    conn.commit()


def _cold_conn():
    conn = sqlite3.connect(":memory:")
    conn.executescript(COLD_SCHEMA)
    return conn


# ── Checkpoint persistence ────────────────────────────────────────────────

def test_checkpoint_round_trips(tmp_path):
    path = str(tmp_path / "ckpt.json")
    cp = CursorCheckpoint(run_id="r1", high_water_id=100, last_processed_id=50, rows_processed=50)
    save_checkpoint(path, cp)
    loaded = load_checkpoint(path)
    assert loaded == cp


def test_missing_checkpoint_returns_none(tmp_path):
    assert load_checkpoint(str(tmp_path / "nope.json")) is None


def test_checkpoint_write_is_atomic_no_partial_file(tmp_path):
    """Structural guard: save_checkpoint must write to a temp file and
    os.replace(), never write directly to the target path (which could
    leave a torn/partial file if interrupted mid-write)."""
    src = (ROOT / "src/ops/transfer_archival_cursor.py").read_text()
    fn_start = src.index("def save_checkpoint")
    fn_end = src.index("\ndef ", fn_start + 10)
    body = src[fn_start:fn_end]
    assert "os.replace(" in body
    assert ".tmp." in body


# ── High-water freeze ─────────────────────────────────────────────────────

def test_high_water_freezes_at_run_start(source_conn, tmp_path):
    _seed(source_conn, 10)
    checkpoint_path = str(tmp_path / "ckpt.json")
    cp = start_or_resume_run(source_conn, run_id="run1", checkpoint_path=checkpoint_path)
    assert cp.high_water_id == 10

    # rows added AFTER the run started must not raise the frozen ceiling
    _seed(source_conn, 5, start_id_hint=100)
    cp2 = start_or_resume_run(source_conn, run_id="run1", checkpoint_path=checkpoint_path)
    assert cp2.high_water_id == 10, "resuming the SAME run must not re-freeze a later ceiling"


def test_new_run_id_freezes_fresh_ceiling(source_conn, tmp_path):
    _seed(source_conn, 10)
    checkpoint_path1 = str(tmp_path / "ckpt1.json")
    cp1 = start_or_resume_run(source_conn, run_id="run1", checkpoint_path=checkpoint_path1)
    assert cp1.high_water_id == 10

    _seed(source_conn, 5, start_id_hint=100)
    checkpoint_path2 = str(tmp_path / "ckpt2.json")
    cp2 = start_or_resume_run(source_conn, run_id="run2", checkpoint_path=checkpoint_path2)
    assert cp2.high_water_id == 15, "a genuinely NEW run_id gets a fresh ceiling reflecting current state"


def test_rows_after_high_water_excluded_from_current_run(source_conn, tmp_path):
    _seed(source_conn, 10)
    checkpoint_path = str(tmp_path / "ckpt.json")
    cp = start_or_resume_run(source_conn, run_id="run1", checkpoint_path=checkpoint_path)
    _seed(source_conn, 5, start_id_hint=100)  # arrives after freeze

    batch = next_batch(source_conn, cp, batch_size=100)
    assert len(batch) == 10, "only rows up to the frozen high-water id are returned"


# ── Bounded batches, no skips, no duplicates ─────────────────────────────

def test_bounded_batch_size_respected(source_conn, tmp_path):
    _seed(source_conn, 23)
    checkpoint_path = str(tmp_path / "ckpt.json")
    cp = start_or_resume_run(source_conn, run_id="run1", checkpoint_path=checkpoint_path)
    batch = next_batch(source_conn, cp, batch_size=5)
    assert len(batch) == 5


def test_full_archival_no_skips_no_duplicates(source_conn, tmp_path):
    _seed(source_conn, 250)
    dest = _cold_conn()
    checkpoint_path = str(tmp_path / "ckpt.json")
    result = run_keyset_archival(source_conn, dest, run_id="run1", checkpoint_path=checkpoint_path, batch_size=37)
    assert result.total_rows_processed == 250
    assert result.completed
    count = dest.execute("SELECT COUNT(*) FROM transfer_index").fetchone()[0]
    assert count == 250
    distinct_sigs = dest.execute("SELECT COUNT(DISTINCT signature) FROM transfer_index").fetchone()[0]
    assert distinct_sigs == 250


# ── Crash-safe resume ──────────────────────────────────────────────────────

def test_resume_after_partial_run_continues_not_restarts(source_conn, tmp_path):
    _seed(source_conn, 100)
    dest = _cold_conn()
    checkpoint_path = str(tmp_path / "ckpt.json")

    # simulate a crash: only 2 batches complete
    result1 = run_keyset_archival(source_conn, dest, run_id="run1", checkpoint_path=checkpoint_path, batch_size=10, max_batches=2)
    assert result1.total_rows_processed == 20
    assert not result1.completed

    # resume: must continue from row 21, not restart from row 1
    result2 = run_keyset_archival(source_conn, dest, run_id="run1", checkpoint_path=checkpoint_path, batch_size=10)
    assert result2.total_rows_processed == 80  # remaining 80 rows
    assert result2.completed

    total_in_dest = dest.execute("SELECT COUNT(*) FROM transfer_index").fetchone()[0]
    assert total_in_dest == 100, "resumed run must not duplicate already-migrated rows"


def test_rerun_completed_run_is_idempotent_zero_new_rows(source_conn, tmp_path):
    _seed(source_conn, 30)
    dest = _cold_conn()
    checkpoint_path = str(tmp_path / "ckpt.json")
    run_keyset_archival(source_conn, dest, run_id="run1", checkpoint_path=checkpoint_path, batch_size=10)

    result2 = run_keyset_archival(source_conn, dest, run_id="run1", checkpoint_path=checkpoint_path, batch_size=10)
    assert result2.total_rows_processed == 0
    assert result2.completed


# ── No source mutation ────────────────────────────────────────────────────

def test_archival_never_deletes_from_source(source_conn, tmp_path):
    _seed(source_conn, 15)
    dest = _cold_conn()
    checkpoint_path = str(tmp_path / "ckpt.json")
    run_keyset_archival(source_conn, dest, run_id="run1", checkpoint_path=checkpoint_path, batch_size=5)
    remaining = source_conn.execute("SELECT COUNT(*) FROM transfer_index").fetchone()[0]
    assert remaining == 15


def test_no_delete_or_vacuum_in_archival_cursor_module():
    src = (ROOT / "src/ops/transfer_archival_cursor.py").read_text()
    for line in src.splitlines():
        if ".execute(" in line:
            upper = line.upper()
            assert "DELETE FROM" not in upper
            assert "VACUUM" not in upper


def test_immutable_key_used_not_mutable_timestamp():
    """Structural guard: the cursor must be bound to `id`, not block_time
    or indexed_at (both mutable/backfillable event-time fields)."""
    src = (ROOT / "src/ops/transfer_archival_cursor.py").read_text()
    fn_start = src.index("def next_batch")
    fn_end = src.index("\n@dataclass", fn_start)
    body = src[fn_start:fn_end]
    assert "id > ?" in body
    assert "id <= ?" in body


# ── Conflict detection (Part 12) ─────────────────────────────────────────

def test_benign_agreement_dedupes_silently():
    hot = sqlite3.connect(":memory:")
    hot.execute("CREATE TABLE transfer_index (signature TEXT, source TEXT, destination TEXT, amount_lamports INTEGER, block_time INTEGER)")
    hot.execute("INSERT INTO transfer_index VALUES ('sig1','A','B',1000,100)")
    cold = sqlite3.connect(":memory:")
    cold.execute("CREATE TABLE transfer_index (signature TEXT, source TEXT, destination TEXT, amount_lamports INTEGER, block_time INTEGER)")
    cold.execute("INSERT INTO transfer_index VALUES ('sig1','A','B',1000,100)")

    reader = UnifiedTransferReader(hot_conn=hot, cold_conns=[cold])
    rows = reader.by_signature("sig1")
    assert len(rows) == 1
    assert reader.get_conflicts() == []


def test_conflicting_amount_surfaced_not_silently_resolved():
    hot = sqlite3.connect(":memory:")
    hot.execute("CREATE TABLE transfer_index (signature TEXT, source TEXT, destination TEXT, amount_lamports INTEGER, block_time INTEGER)")
    hot.execute("INSERT INTO transfer_index VALUES ('sig1','A','B',1000,100)")
    cold = sqlite3.connect(":memory:")
    cold.execute("CREATE TABLE transfer_index (signature TEXT, source TEXT, destination TEXT, amount_lamports INTEGER, block_time INTEGER)")
    cold.execute("INSERT INTO transfer_index VALUES ('sig1','A','B',9999,100)")

    reader = UnifiedTransferReader(hot_conn=hot, cold_conns=[cold])
    rows = reader.by_signature("sig1")
    conflicts = reader.get_conflicts()
    assert rows == [], "a conflicting row must not appear in the normal result list"
    assert len(conflicts) == 1
    assert isinstance(conflicts[0], EvidenceConflict)
    assert conflicts[0].conflict_type == "HOT_COLD_EVIDENCE_CONFLICT"
    assert conflicts[0].hot_row[3] == 1000
    assert conflicts[0].cold_row[3] == 9999


def test_conflicting_block_time_also_surfaced():
    hot = sqlite3.connect(":memory:")
    hot.execute("CREATE TABLE transfer_index (signature TEXT, source TEXT, destination TEXT, amount_lamports INTEGER, block_time INTEGER)")
    hot.execute("INSERT INTO transfer_index VALUES ('sig1','A','B',1000,100)")
    cold = sqlite3.connect(":memory:")
    cold.execute("CREATE TABLE transfer_index (signature TEXT, source TEXT, destination TEXT, amount_lamports INTEGER, block_time INTEGER)")
    cold.execute("INSERT INTO transfer_index VALUES ('sig1','A','B',1000,999)")

    reader = UnifiedTransferReader(hot_conn=hot, cold_conns=[cold])
    rows = reader.by_signature("sig1")
    conflicts = reader.get_conflicts()
    assert rows == []
    assert len(conflicts) == 1


def test_conflicts_reset_between_queries():
    hot = sqlite3.connect(":memory:")
    hot.execute("CREATE TABLE transfer_index (signature TEXT, source TEXT, destination TEXT, amount_lamports INTEGER, block_time INTEGER)")
    hot.execute("INSERT INTO transfer_index VALUES ('sig1','A','B',1000,100)")
    hot.execute("INSERT INTO transfer_index VALUES ('sig2','A','B',2000,200)")
    cold = sqlite3.connect(":memory:")
    cold.execute("CREATE TABLE transfer_index (signature TEXT, source TEXT, destination TEXT, amount_lamports INTEGER, block_time INTEGER)")
    cold.execute("INSERT INTO transfer_index VALUES ('sig1','A','B',9999,100)")  # conflict

    reader = UnifiedTransferReader(hot_conn=hot, cold_conns=[cold])
    reader.by_signature("sig1")
    assert len(reader.get_conflicts()) == 1

    reader.by_signature("sig2")  # no conflict for sig2
    assert len(reader.get_conflicts()) == 0, "conflicts from a prior query must not leak into a subsequent unrelated query"


def test_no_write_statements_in_unified_reader_p4_additions():
    src = (ROOT / "src/ops/unified_transfer_reader.py").read_text()
    for line in src.splitlines():
        if ".execute(" in line:
            upper = line.upper()
            assert "INSERT" not in upper
            assert "UPDATE" not in upper
            assert "DELETE" not in upper


def test_this_module_never_targets_real_production_db():
    src = Path(__file__).read_text()
    for line in src.splitlines():
        if "sqlite3.connect(" in line:
            assert "flex_complete_database.db" not in line
            assert "wt_ops_v2.db" not in line


# ── CEX lineage parity (Part 5) using REAL addresses/signatures ──────────

def test_real_2hop_cex_chain_reconstructs_across_hot_cold(tmp_path):
    """Uses REAL addresses and signatures found by live investigation of
    this repo's actual local_operation_discovery_corpus.db + transfer_index
    (docs/audits/storage_lifecycle_p4_exact_pinning_reconciliation.json
    task3_cex_lineage_proof) -- a genuine CEX -> intermediary_2 ->
    intermediary_1 -> creator chain, split across HOT/COLD, proving
    real-world CEX provenance survives tiering intact."""
    from src.ops.transfer_cold_store import migrate_rows_to_cold

    HOT_SCHEMA_LOCAL = (
        "CREATE TABLE transfer_index (id INTEGER PRIMARY KEY AUTOINCREMENT, signature TEXT, "
        "source TEXT, destination TEXT, amount_lamports INTEGER, slot INTEGER DEFAULT 0, "
        "block_time INTEGER, indexed_at REAL, is_valid INTEGER DEFAULT 1, transfer_type TEXT DEFAULT 'standard')"
    )
    hot = sqlite3.connect(":memory:")
    hot.execute(HOT_SCHEMA_LOCAL)

    CEX = "2AQdpHJ2JpcEgPiATUXjQxA8QmafFegfQwSLWSprPicm"
    INTER2 = "FFLeWB1qD581VPpJh9bw5T9bxdHPvQjfd9Rbbh4vWri4"
    INTER1 = "5TtfGFYNJZekNsN6QE9FeHigxhhTwy1NnisMTUSXEujs"
    CREATOR = "9vY2sDbJLVvUKMorvFo7kZJWPG7VNUR3umJPH2VC33Hi"
    real_rows = [
        ("53QqyRDrsdk7mTKEJqiaBa7VCP7rze5Bw4nya89RdPVPpEqNPwi6MgqycJwJSwFrB5DPcSNa9N2P6jW5ZP4bAmbw", CEX, INTER2, 4520195000, 1776836642),
        ("3ycr1ZQNemDaeyjMTXkiTZpPGDn8NnvCGW1eyZh4kSbGTECnruaKSyaS21Z9uHB2TvMmx3zKXnPQtzgXaX8bdp2G", INTER2, INTER1, 2281482, 1777503488),
        ("59mg6RT3BxtwPgtNUakFcvdzdDUUPrjL4pADrjuqQoMcJxyYYLL8woM1h8ThmkfckB6eYZNxFfeHkFHZyGpPY56", INTER1, CREATOR, 21384718, 1777503504),
    ]
    for sig, src, dst, amt, bt in real_rows:
        hot.execute(
            "INSERT INTO transfer_index (signature,source,destination,amount_lamports,block_time,indexed_at) VALUES (?,?,?,?,?,?)",
            (sig, src, dst, amt, bt, 0),
        )
    hot.commit()

    cold_path = str(tmp_path / "cex_2hop_cold.sqlite")
    migrate_rows_to_cold(hot, cold_path, where_clause="block_time < ?", params=(1777000000,), run_id="cex-parity-test")
    hot.execute("DELETE FROM transfer_index WHERE block_time < ?", (1777000000,))
    hot.commit()

    cold_conn = sqlite3.connect(cold_path)
    reader = UnifiedTransferReader(hot_conn=hot, cold_conns=[cold_conn])

    hop1 = reader.by_source(CEX)
    hop2 = reader.by_destination(INTER2)
    hop3 = reader.by_destination(CREATOR)

    assert len(hop1) == 1 and hop1[0][2] == INTER2
    assert len(hop2) == 1 and hop2[0][1] == CEX
    assert len(hop3) == 1 and hop3[0][1] == INTER1
    assert reader.get_conflicts() == []
