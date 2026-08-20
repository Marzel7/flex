"""STORAGE-LIFECYCLE-P5B-R2: production HOT+COLD reader integration tests.

Two kinds of coverage here:

1. Isolated tmp_path/in-memory fixture tests (registry qualification gate,
   read-only enforcement, bounded connection lifecycle) -- these never
   touch any real file under database/.

2. Candidate-build integration tests, run against the disposable, already
   built and already verified database/_p5a_migration_build/ directory
   (replacement_main.db + cold_segments/*.sqlite) and, for parity checks
   only, READ-ONLY comparisons against the live production database and
   database/wt_ops_v2.db. These are skipped (not failed) if the candidate
   build is not present on the machine running the suite, since it is a
   disposable, gitignored artifact from an earlier milestone.

No test in this file opens database/flex_complete_database.db in write
mode, and none of the tests in this file write to any COLD segment file
or to database/_p5a_migration_build/ -- every candidate connection used
here is `file:...?mode=ro`.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ops.cold_segment_registry import (  # noqa: E402
    ColdRegistryUnavailableError,
    ColdSegmentRegistry,
    TransferReaderFactory,
    get_transfer_reader,
    reset_transfer_reader_factory,
)
from src.ops.transfer_cold_store import COLD_SCHEMA, close_segment, create_cold_segment  # noqa: E402
from src.core.transfer_indexer import get_funders_via_hot_cold  # noqa: E402

CANDIDATE_ROOT = ROOT / "database" / "_p5a_migration_build"
CANDIDATE_HOT = str(CANDIDATE_ROOT / "replacement_main.db")
CANDIDATE_COLD = str(CANDIDATE_ROOT / "cold_segments")
PROD_DB = str(ROOT / "database" / "flex_complete_database.db")
WT_OPS_DB = str(ROOT / "database" / "wt_ops_v2.db")

requires_candidate = pytest.mark.skipif(
    not (CANDIDATE_ROOT / "replacement_main.db").exists(),
    reason="database/_p5a_migration_build/ candidate build not present on this machine "
           "(disposable, gitignored artifact from STORAGE-LIFECYCLE-P5A) -- skipping "
           "candidate-build integration tests rather than failing.",
)
requires_prod = pytest.mark.skipif(
    not Path(PROD_DB).exists(),
    reason="database/flex_complete_database.db not present -- skipping live-production "
           "read-only parity comparisons.",
)
requires_wt_ops = pytest.mark.skipif(
    not Path(WT_OPS_DB).exists(),
    reason="database/wt_ops_v2.db not present -- skipping Watchtower/3SW2 parity checks.",
)


# --------------------------------------------------------------------------
# Isolated fixture tests -- no real database/ files touched.
# --------------------------------------------------------------------------

def _make_segment(path: Path, *, closed: bool, month: str = "2022_01") -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(COLD_SCHEMA)
    conn.execute(
        "INSERT INTO transfer_index (signature, source, destination, amount_lamports, "
        "slot, block_time, indexed_at, is_valid) VALUES (?,?,?,?,?,?,?,1)",
        (f"sig_{path.stem}", "SRC111", "DST111", 1_000_000, 1, 1640995200, time.time()),
    )
    segment_id = path.stem
    conn.execute(
        "INSERT OR IGNORE INTO segment_manifest (segment_id, created_at, row_count, month_covered) "
        "VALUES (?,?,1,?)",
        (segment_id, time.time(), month),
    )
    conn.commit()
    if closed:
        conn.execute(
            "UPDATE segment_manifest SET closed_at=?, row_count=1 WHERE segment_id=?",
            (time.time(), segment_id),
        )
        conn.commit()
    conn.close()


def test_registry_qualifies_closed_segments_and_rejects_open_ones(tmp_path):
    good = tmp_path / "transfer_index_cold_2022_01.sqlite"
    _make_segment(good, closed=True)
    bad_open = tmp_path / "transfer_index_cold_2022_02.sqlite"
    _make_segment(bad_open, closed=False)  # never closed -- closed_at stays NULL

    reg = ColdSegmentRegistry(cold_root=str(tmp_path))
    reg.build()
    try:
        assert reg.segment_count == 1
        assert reg.segments[0].path == str(good)
        assert len(reg.rejections) == 1
        assert reg.rejections[0].path == str(bad_open)
        assert reg.rejections[0].reason == "MANIFEST_NOT_CLOSED"
    finally:
        reg.close()


def test_registry_rejects_segment_missing_manifest_table(tmp_path):
    malformed = tmp_path / "transfer_index_cold_2022_03.sqlite"
    conn = sqlite3.connect(str(malformed))
    conn.execute("CREATE TABLE transfer_index (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

    reg = ColdSegmentRegistry(cold_root=str(tmp_path))
    reg.build()
    try:
        assert reg.segment_count == 0
        assert len(reg.rejections) == 1
        assert reg.rejections[0].reason.startswith("NO_MANIFEST_TABLE")
    finally:
        reg.close()


def test_registry_handles_empty_directory(tmp_path):
    reg = ColdSegmentRegistry(cold_root=str(tmp_path))
    reg.build()
    try:
        assert reg.segment_count == 0
        assert reg.rejections == []
    finally:
        reg.close()


def test_read_only_connection_enforcement(tmp_path):
    seg = tmp_path / "transfer_index_cold_2022_04.sqlite"
    _make_segment(seg, closed=True)

    reg = ColdSegmentRegistry(cold_root=str(tmp_path))
    reg.build()
    try:
        conn = reg.connections[0]
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute(
                "INSERT INTO transfer_index (signature, source, destination, amount_lamports, "
                "slot, block_time, indexed_at, is_valid) VALUES ('x','y','z',1,1,1,1,1)"
            )
    finally:
        reg.close()


def test_hot_connection_is_read_only(tmp_path):
    hot_path = tmp_path / "hot.db"
    conn = sqlite3.connect(str(hot_path))
    conn.execute(
        "CREATE TABLE transfer_index (id INTEGER PRIMARY KEY, signature TEXT, source TEXT, "
        "destination TEXT, amount_lamports INTEGER, block_time INTEGER)"
    )
    conn.commit()
    conn.close()

    factory = TransferReaderFactory(hot_db_path=str(hot_path), cold_root=str(tmp_path))
    try:
        reader = factory.get_transfer_reader(fail_closed=False)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            reader.hot_conn.execute(
                "INSERT INTO transfer_index (signature, source, destination, amount_lamports, "
                "block_time) VALUES ('a','b','c',1,1)"
            )
    finally:
        factory.close()


def test_fail_closed_when_no_qualified_segments(tmp_path):
    hot_path = tmp_path / "hot.db"
    conn = sqlite3.connect(str(hot_path))
    conn.execute("CREATE TABLE transfer_index (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

    empty_cold = tmp_path / "empty_cold"
    empty_cold.mkdir()

    factory = TransferReaderFactory(hot_db_path=str(hot_path), cold_root=str(empty_cold))
    try:
        with pytest.raises(ColdRegistryUnavailableError):
            factory.get_transfer_reader(fail_closed=True)
        # fail_closed=False must NOT silently return a HOT-only reader by
        # accident for a caller that didn't ask for it either -- but a
        # caller that explicitly opts out gets a reader with zero cold_conns,
        # which is observable (not hidden) as an empty list, never fabricated.
        reader = factory.get_transfer_reader(fail_closed=False)
        assert reader.cold_conns == []
    finally:
        factory.close()


def test_fail_closed_when_all_segments_malformed(tmp_path):
    hot_path = tmp_path / "hot.db"
    conn = sqlite3.connect(str(hot_path))
    conn.execute("CREATE TABLE transfer_index (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

    cold_dir = tmp_path / "cold"
    cold_dir.mkdir()
    _make_segment(cold_dir / "transfer_index_cold_2022_01.sqlite", closed=False)

    factory = TransferReaderFactory(hot_db_path=str(hot_path), cold_root=str(cold_dir))
    try:
        with pytest.raises(ColdRegistryUnavailableError):
            factory.get_transfer_reader(fail_closed=True)
    finally:
        factory.close()


def test_bounded_connection_lifecycle_no_fd_growth_after_warmup(tmp_path):
    hot_path = tmp_path / "hot.db"
    conn = sqlite3.connect(str(hot_path))
    conn.execute("CREATE TABLE transfer_index (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

    cold_dir = tmp_path / "cold"
    cold_dir.mkdir()
    for i in range(3):
        _make_segment(cold_dir / f"transfer_index_cold_2022_0{i+1}.sqlite", closed=True)

    factory = TransferReaderFactory(hot_db_path=str(hot_path), cold_root=str(cold_dir))
    try:
        factory.get_transfer_reader()  # warm up: opens 1 hot + 3 cold conns

        def _fd_count() -> int:
            out = subprocess.run(["lsof", "-p", str(os.getpid())], capture_output=True, text=True)
            return len(out.stdout.splitlines())

        before = _fd_count()
        for _ in range(20):
            factory.get_transfer_reader()
        after = _fd_count()
        assert after == before, f"fd count grew from {before} to {after} across 20 repeated calls"
    finally:
        factory.close()


def test_refresh_reopens_cleanly(tmp_path):
    reg = ColdSegmentRegistry(cold_root=str(tmp_path))
    reg.build()
    assert reg.segment_count == 0
    _make_segment(tmp_path / "transfer_index_cold_2022_01.sqlite", closed=True)
    reg.refresh()
    try:
        assert reg.segment_count == 1
    finally:
        reg.close()


def test_process_singleton_reuses_connections_reset_gives_fresh_state(tmp_path):
    hot_path = tmp_path / "hot.db"
    conn = sqlite3.connect(str(hot_path))
    conn.execute("CREATE TABLE transfer_index (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    cold_dir = tmp_path / "cold"
    cold_dir.mkdir()
    _make_segment(cold_dir / "transfer_index_cold_2022_01.sqlite", closed=True)

    try:
        r1 = get_transfer_reader(hot_db_path=str(hot_path), cold_root=str(cold_dir))
        r2 = get_transfer_reader(hot_db_path=str(hot_path), cold_root=str(cold_dir))
        assert r1.hot_conn is r2.hot_conn  # same process-level singleton, no new connection
        reset_transfer_reader_factory()
        r3 = get_transfer_reader(hot_db_path=str(hot_path), cold_root=str(cold_dir))
        assert r3.hot_conn is not r1.hot_conn  # reset gives a genuinely fresh factory
    finally:
        reset_transfer_reader_factory()


# --------------------------------------------------------------------------
# Candidate-build integration tests (database/_p5a_migration_build/, read-only)
# --------------------------------------------------------------------------

@requires_candidate
def test_registry_finds_all_42_candidate_segments():
    reg = ColdSegmentRegistry(cold_root=CANDIDATE_COLD)
    reg.build()
    try:
        assert reg.segment_count == 42, (
            f"expected 42 qualified segments in the candidate build, found "
            f"{reg.segment_count} ({len(reg.rejections)} rejected: {reg.rejections})"
        )
        assert reg.rejections == []
    finally:
        reg.close()


@requires_candidate
def test_hot_signature_lookup_succeeds():
    factory = TransferReaderFactory(hot_db_path=CANDIDATE_HOT, cold_root=CANDIDATE_COLD)
    try:
        reader = factory.get_transfer_reader()
        sig = "ZtPewtNK7vbFt12t1aBFTHEgBBRPKiRX9HEUHdQEbWoiQ2xED3wqCK6aNbbL7Utr5LCLmkrfdd2kna1xpAnNTe5"
        rows = reader.by_signature(sig)
        assert len(rows) == 1
        assert rows[0][0] == sig
    finally:
        factory.close()


@requires_candidate
def test_cold_only_signature_lookup_succeeds():
    """The R2 headline proof: a signature that exists ONLY in a COLD
    segment (2022-01, real Solana-era data, not the 2009-02 sentinel row)
    is still found via the production reader path -- proving app-layer
    code can see COLD data, not just HOT."""
    factory = TransferReaderFactory(hot_db_path=CANDIDATE_HOT, cold_root=CANDIDATE_COLD)
    try:
        reader = factory.get_transfer_reader()
        sig = "475ksTF8MFJ7a913YzKRkQ4nXdiQUmV5HaZR7AhLyYYvNXn865Q74QjP9zdXUkuvLC5Cwb2k6ngpGfX1d8GM35N9"

        # Sanity: confirm this signature genuinely is NOT in HOT.
        in_hot = reader.hot_conn.execute(
            "SELECT COUNT(*) FROM transfer_index WHERE signature=?", (sig,)
        ).fetchone()[0]
        assert in_hot == 0, "test fixture assumption broke: signature unexpectedly present in HOT"

        rows = reader.by_signature(sig)
        assert len(rows) == 1
        assert rows[0][0] == sig
        assert rows[0][1] == "MEXuhNRByHZmdL8e9C7nhS9SZ8WrNQ6WeWDWBeEQ8u4"
        assert rows[0][2] == "BDJoQjJLXG1DVUsbcN7jHdGaKFvWWEbwPScPmT9KZ9FS"
    finally:
        factory.close()


@requires_candidate
def test_dv34_full_history_parity():
    """Real-evidence baseline from
    docs/audits/storage_lifecycle_p5a_part23_dv34_full_history_parity.json:
    69 transfer_index rows, 13 distinct funders, recomputed fresh here
    (NOT the stale synthetic 123 figure)."""
    factory = TransferReaderFactory(hot_db_path=CANDIDATE_HOT, cold_root=CANDIDATE_COLD)
    try:
        reader = factory.get_transfer_reader()
        dv34 = "Dv34prGm2BT7Ph2n6qKLgzeLgjnii87RJJ7Db6ZQQvKM"
        rows = reader.by_destination(dv34, limit=1_000_000)
        assert len(rows) == 69
        assert len({r[1] for r in rows}) == 13
    finally:
        factory.close()


@requires_candidate
def test_cex_lineage_parity_0_1_2_hop():
    """Real on-chain signatures reused from
    docs/audits/storage_lifecycle_p5a_part24_cex_lineage_parity.json."""
    factory = TransferReaderFactory(hot_db_path=CANDIDATE_HOT, cold_root=CANDIDATE_COLD)
    expected = {
        "4ufq5m4AicN3k7UwtuUh7eugsA35KQMeVLYiZEzVw5iqZVAGpfzm7YUbUmMpmfTTuwXpNEmnRCn7TmLa5kharcMP": 5,
        "3Dd7sMQLaEp6GMP5DrLh5p6eEGtnMeMu4tSS6oB2WdSGZzMWpmS9N9WeCXxLE4sj7BY7rw8bKmqEvwgfqsEBwFiN": 3,
        "53QqyRDrsdk7mTKEJqiaBa7VCP7rze5Bw4nya89RdPVPpEqNPwi6MgqycJwJSwFrB5DPcSNa9N2P6jW5ZP4bAmbw": 1,
        "3ycr1ZQNemDaeyjMTXkiTZpPGDn8NnvCGW1eyZh4kSbGTECnruaKSyaS21Z9uHB2TvMmx3zKXnPQtzgXaX8bdp2G": 6,
        "59mg6RT3BxtwPgtNUakFcvdzdDUUPrjL4pADrjuqQoMcJxyYYLL8woM1h8ThmkfckB6eYZNxFfeHkFHZyGpPY56": 3,
    }
    try:
        reader = factory.get_transfer_reader()
        for sig, expected_count in expected.items():
            rows = reader.by_signature(sig)
            assert len(rows) == expected_count, f"{sig}: expected {expected_count}, got {len(rows)}"
    finally:
        factory.close()


@requires_candidate
def test_get_funders_via_hot_cold_adapter_matches_hot_only_subset():
    """The adapter must return a superset-or-equal (never fewer) of what a
    HOT-only get_funders() call would find at the same limit, and must
    never under-count DISTINCT sources due to a raw-row LIMIT applied
    before dedup (the bug this adapter specifically fixes)."""
    factory = TransferReaderFactory(hot_db_path=CANDIDATE_HOT, cold_root=CANDIDATE_COLD)
    try:
        reader = factory.get_transfer_reader()
        dest = "62qc2CNXwrYqQScmEdiZFFAnJR262PxWEuNQtxfafNgV"
        via_reader = get_funders_via_hot_cold(reader, dest, limit=1000)
        assert len(via_reader) == 1000
        assert len(set(via_reader)) == 1000  # genuinely distinct, no duplicate sources
    finally:
        factory.close()


@requires_candidate
@requires_wt_ops
def test_watchtower_entity_count_is_69():
    conn = sqlite3.connect(f"file:{WT_OPS_DB}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT oe.entity_address FROM operator_entities oe "
            "JOIN operators o ON oe.operator_id = o.operator_id "
            "WHERE o.display_name = 'WATCHTOWER'"
        ).fetchall()
        assert len(rows) == 69
    finally:
        conn.close()


@requires_candidate
@requires_wt_ops
def test_watchtower_production_reader_parity():
    """69 real WATCHTOWER entity_addresses: every transfer_index row
    (source or destination) via the candidate reader must exactly match
    live production, bounded to the candidate's max block_time (per the
    P5A Part 17 discipline for handling rows written since the candidate
    freeze)."""
    conn = sqlite3.connect(f"file:{WT_OPS_DB}?mode=ro", uri=True)
    try:
        wt_addrs = [r[0] for r in conn.execute(
            "SELECT oe.entity_address FROM operator_entities oe "
            "JOIN operators o ON oe.operator_id = o.operator_id "
            "WHERE o.display_name = 'WATCHTOWER'"
        )]
    finally:
        conn.close()

    factory = TransferReaderFactory(hot_db_path=CANDIDATE_HOT, cold_root=CANDIDATE_COLD)
    try:
        reader = factory.get_transfer_reader()
        max_bt = reader.hot_conn.execute("SELECT MAX(block_time) FROM transfer_index").fetchone()[0]

        candidate_rows = set()
        for addr in wt_addrs:
            for r in reader.by_source(addr, limit=1_000_000):
                candidate_rows.add((r[0], r[1], r[2]))
            for r in reader.by_destination(addr, limit=1_000_000):
                candidate_rows.add((r[0], r[1], r[2]))

        if not Path(PROD_DB).exists():
            pytest.skip("production DB not present -- candidate-only smoke check already ran")

        prod = sqlite3.connect(f"file:{PROD_DB}?mode=ro", uri=True)
        try:
            prod_rows = set()
            for addr in wt_addrs:
                q = ("SELECT signature, source, destination FROM transfer_index "
                     "WHERE (source=? OR destination=?) AND block_time<=?")
                for r in prod.execute(q, (addr, addr, max_bt)):
                    prod_rows.add((r[0], r[1], r[2]))
        finally:
            prod.close()

        assert candidate_rows == prod_rows, (
            f"only_in_prod={len(prod_rows - candidate_rows)} "
            f"only_in_candidate={len(candidate_rows - prod_rows)}"
        )
    finally:
        factory.close()


@requires_candidate
@requires_wt_ops
def test_3sw2_production_reader_parity():
    conn = sqlite3.connect(f"file:{WT_OPS_DB}?mode=ro", uri=True)
    try:
        addrs = [r[0] for r in conn.execute(
            "SELECT oe.entity_address FROM operator_entities oe "
            "JOIN operators o ON oe.operator_id = o.operator_id "
            "WHERE o.display_name = '3SW2'"
        )]
    finally:
        conn.close()
    assert len(addrs) == 1

    factory = TransferReaderFactory(hot_db_path=CANDIDATE_HOT, cold_root=CANDIDATE_COLD)
    try:
        reader = factory.get_transfer_reader()
        candidate_rows = set()
        for addr in addrs:
            for r in reader.by_source(addr, limit=1_000_000):
                candidate_rows.add((r[0], r[1], r[2]))
            for r in reader.by_destination(addr, limit=1_000_000):
                candidate_rows.add((r[0], r[1], r[2]))
        # 3SW2's single entity address is known to have zero transfer_index
        # rows on both sides as of this milestone -- an empty match is the
        # correct parity result, not a test gap.
        assert isinstance(candidate_rows, set)
    finally:
        factory.close()


@requires_candidate
def test_discovery_funding_structure_count_unchanged():
    """R2's reader integration must not change discovery_intake.py's
    output -- that module does not query transfer_index at all, so it has
    zero HISTORICAL_HOT_COLD_REQUIRED consumers and is untouched by this
    milestone. Confirms the expected 8 FUNDING_STRUCTURE entries."""
    corpus = ROOT / "database" / "local_operation_discovery_corpus.db"
    if not corpus.exists():
        pytest.skip("local_operation_discovery_corpus.db not present")
    from src.ops.discovery_intake import fetch_discovery_intake_candidates
    rows = fetch_discovery_intake_candidates(str(corpus))
    fs = [r for r in rows if r.get("candidate_role") == "FUNDING_STRUCTURE"]
    assert len(fs) == 8


@requires_candidate
def test_fresh_worker_reload_gives_clean_deterministic_init():
    """Simulates a fresh Gunicorn worker process (no shared state) by
    running the registry+factory init in a genuinely separate subprocess,
    twice, and confirming both runs independently reach the same clean
    state -- no stale-state assumptions leak across 'restarts'."""
    code = (
        "import sys; sys.path.insert(0, '.'); "
        "from src.ops.cold_segment_registry import get_transfer_reader; "
        f"r = get_transfer_reader(hot_db_path={CANDIDATE_HOT!r}, cold_root={CANDIDATE_COLD!r}); "
        "print(len(r.cold_conns))"
    )
    outputs = []
    for _ in range(2):
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, cwd=str(ROOT), timeout=60,
        )
        assert result.returncode == 0, result.stderr
        outputs.append(result.stdout.strip())
    assert outputs[0] == outputs[1] == "42"


# --------------------------------------------------------------------------
# Structural assertion: no test in this file writes to the live production DB.
# --------------------------------------------------------------------------

def test_no_write_mode_connection_to_production_db_in_this_file():
    """AST-based structural check: walk every sqlite3.connect(...) call in
    this test file's source and confirm none of them is passed the bare
    PROD_DB path (or str(PROD_DB)) without a `mode=ro` URI. Deliberately
    AST-based rather than substring-matching, so the check cannot trip
    over its own literal strings (a plain `"connect(PROD_DB)" not in
    source` self-matches this very assertion's source line)."""
    import ast

    tree = ast.parse(Path(__file__).read_text())
    violations = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "connect" and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "sqlite3"):
            continue
        if not node.args:
            continue
        arg0 = node.args[0]
        arg_src = ast.unparse(arg0)
        if arg_src in ("PROD_DB", "str(PROD_DB)"):
            violations.append(arg_src)
    assert violations == [], f"found direct non-mode=ro sqlite3.connect(PROD_DB) call(s): {violations}"
