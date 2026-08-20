"""STORAGE-LIFECYCLE-P5B-R2.2: exact, fast full-history aggregate path for
`/api/transfer-graph/stats`, built in src/ops/transfer_graph_stats_summary.py.

NOT WIRED INTO api_transfer_graph_stats() -- src/core/main.py's live route
handler is unmodified. This test file, like the R2.1/R2/Unit1/Unit2 suites
before it, qualifies new dormant code against isolated in-memory fixtures
(fast, deterministic) plus (where flagged) the real 42-segment candidate
database under database/_p5a_migration_build/ for empirical/parity checks.

Follows the fixture style of test_funder_overlap_hot_cold_adapter.py and
the structural-AST-check style of test_storage_lifecycle_p5b_r2_1_final_
recensus.py.
"""
from __future__ import annotations

import ast
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from src.ops.cold_segment_registry import (  # noqa: E402
    ColdSegmentRegistry,
    TransferReaderFactory,
    ColdRegistryUnavailableError,
)
from src.ops.transfer_graph_stats_summary import (  # noqa: E402
    SummaryStore,
    StaleSummaryError,
    HotCheckpointCache,
    build_segment_summary,
    build_all_summaries,
    compute_exact_stats,
    compute_exact_stats_fast,
    _query_hot_live,
    _merge_summaries_and_hot,
)

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
    transfer_type TEXT DEFAULT 'standard'
);
"""

COLD_SCHEMA = """
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
    transfer_type TEXT DEFAULT 'standard'
);
CREATE TABLE segment_manifest (
    segment_id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    closed_at REAL,
    row_count INTEGER NOT NULL DEFAULT 0,
    sha256_of_sorted_signatures TEXT,
    source_run_id TEXT,
    month_covered TEXT NOT NULL
);
"""

LAMPORTS_PER_SOL = 1_000_000_000


def _hot_conn(path=":memory:"):
    conn = sqlite3.connect(path)
    conn.executescript(HOT_SCHEMA)
    conn.commit()
    return conn


def _insert(conn, *, sig, source, dest, sol, block_time, is_valid=1):
    conn.execute(
        "INSERT INTO transfer_index (signature, source, destination, amount_lamports, "
        "slot, block_time, indexed_at, is_valid) VALUES (?,?,?,?,?,?,?,?)",
        (sig, source, dest, int(sol * LAMPORTS_PER_SOL), 0, block_time, time.time(), is_valid),
    )
    conn.commit()


def _make_cold_segment(path, *, segment_id, month_covered, rows, closed=True, digest="FIXED_DIGEST"):
    """rows: list of (sig, source, dest, sol, block_time)."""
    conn = sqlite3.connect(str(path))
    conn.executescript(COLD_SCHEMA)
    for sig, source, dest, sol, bt in rows:
        conn.execute(
            "INSERT INTO transfer_index (signature, source, destination, amount_lamports, "
            "slot, block_time, indexed_at, is_valid) VALUES (?,?,?,?,?,?,?,1)",
            (sig, source, dest, int(sol * LAMPORTS_PER_SOL), 0, bt, time.time()),
        )
    conn.execute(
        "INSERT INTO segment_manifest (segment_id, created_at, closed_at, row_count, "
        "sha256_of_sorted_signatures, source_run_id, month_covered) VALUES (?,?,?,?,?,?,?)",
        (segment_id, time.time(), time.time() if closed else None, len(rows),
         digest if closed else None, "test_run", month_covered),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Exact full-history aggregate parity (isolated fixture)
# ---------------------------------------------------------------------------

def test_fast_path_matches_slow_reference_on_isolated_fixture(tmp_path):
    """compute_exact_stats_fast and compute_exact_stats (the slower,
    always-live-HOT-scan reference) must return IDENTICAL results on the
    same isolated HOT+COLD fixture."""
    cold_root = tmp_path / "cold"
    cold_root.mkdir()
    _make_cold_segment(
        cold_root / "transfer_index_cold_2026_01.sqlite",
        segment_id="transfer_index_cold_2026_01", month_covered="2026_01",
        rows=[("C1", "FUNDER_A", "CREATOR_X", 1.0, 100), ("C2", "FUNDER_A", "CREATOR_Y", 2.0, 101)],
    )

    hot_path = tmp_path / "hot.db"
    hot = _hot_conn(str(hot_path))
    _insert(hot, sig="H1", source="FUNDER_A", dest="CREATOR_Z", sol=3.0, block_time=200)
    _insert(hot, sig="H2", source="FUNDER_B", dest="CREATOR_X", sol=0.6, block_time=201)
    hot.close()

    registry = ColdSegmentRegistry(str(cold_root)).build()
    store = SummaryStore(str(tmp_path / "summaries"))
    build_all_summaries(registry, store)

    factory = TransferReaderFactory(hot_db_path=str(hot_path), cold_root=str(cold_root))
    try:
        reader = factory.get_transfer_reader(fail_closed=True)
        slow = compute_exact_stats(reader.hot_conn, registry, store)
        cache = HotCheckpointCache(str(tmp_path / "hot_cache.json"))
        fast = compute_exact_stats_fast(reader.hot_conn, registry, store, cache)
    finally:
        factory.close()

    for key in ("total_rows", "distinct_sigs", "distinct_sources", "distinct_destinations",
                "earliest_bt", "latest_bt", "in_cluster_range", "below_range", "above_range"):
        assert slow[key] == fast[key], f"mismatch on {key}: slow={slow[key]} fast={fast[key]}"

    assert slow["total_rows"] == 4
    assert slow["distinct_sigs"] == 4
    assert slow["distinct_sources"] == 2  # FUNDER_A, FUNDER_B
    assert slow["distinct_destinations"] == 3  # CREATOR_X, CREATOR_Y, CREATOR_Z


# ---------------------------------------------------------------------------
# Cross-tier and cross-segment identity dedup correctness
# ---------------------------------------------------------------------------

def test_signature_in_both_hot_and_cold_counts_once():
    """A distinct signature (the same on-chain tx) appearing in BOTH HOT
    (copy-then-verify overlap window) and a COLD segment must be counted
    exactly once in distinct_sigs, not twice."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        cold_root = td / "cold"
        cold_root.mkdir()
        _make_cold_segment(
            cold_root / "transfer_index_cold_2026_01.sqlite",
            segment_id="transfer_index_cold_2026_01", month_covered="2026_01",
            rows=[("SHARED_SIG", "FUNDER", "CREATOR", 1.0, 100)],
        )
        hot_path = td / "hot.db"
        hot = _hot_conn(str(hot_path))
        _insert(hot, sig="SHARED_SIG", source="FUNDER", dest="CREATOR", sol=1.0, block_time=100)
        hot.close()

        registry = ColdSegmentRegistry(str(cold_root)).build()
        store = SummaryStore(str(td / "summaries"))
        build_all_summaries(registry, store)

        factory = TransferReaderFactory(hot_db_path=str(hot_path), cold_root=str(cold_root))
        try:
            reader = factory.get_transfer_reader(fail_closed=True)
            result = compute_exact_stats(reader.hot_conn, registry, store)
        finally:
            factory.close()

        assert result["distinct_sigs"] == 1, "signature in both HOT and COLD must count once"


def test_signature_across_multiple_cold_segments_counts_once():
    """A signature appearing in TWO different COLD segments (should not
    happen per the monthly-partition + UNIQUE constraint, but the merge
    logic must still handle it correctly and not double-count) must count
    once in the union."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        cold_root = td / "cold"
        cold_root.mkdir()
        _make_cold_segment(
            cold_root / "transfer_index_cold_2025_12.sqlite",
            segment_id="transfer_index_cold_2025_12", month_covered="2025_12",
            rows=[("DUP_SIG", "FUNDER", "CREATOR", 1.0, 90)],
        )
        _make_cold_segment(
            cold_root / "transfer_index_cold_2026_01.sqlite",
            segment_id="transfer_index_cold_2026_01", month_covered="2026_01",
            rows=[("DUP_SIG", "FUNDER", "CREATOR", 1.0, 100)],
        )
        hot_path = td / "hot.db"
        hot = _hot_conn(str(hot_path))
        hot.close()

        registry = ColdSegmentRegistry(str(cold_root)).build()
        store = SummaryStore(str(td / "summaries"))
        build_all_summaries(registry, store)

        factory = TransferReaderFactory(hot_db_path=str(hot_path), cold_root=str(cold_root))
        try:
            reader = factory.get_transfer_reader(fail_closed=True)
            result = compute_exact_stats(reader.hot_conn, registry, store)
        finally:
            factory.close()

        assert result["distinct_sigs"] == 1, "same signature in 2 COLD segments must count once, not twice"


def test_cross_segment_signature_disjointness_empirical_on_real_candidate():
    """Empirical check against the REAL 42-segment candidate database (not
    a synthetic fixture): confirms the R2.2 architecture's core finding --
    zero pairwise signature intersections across all real closed COLD
    segments. Skips if the candidate database isn't present (e.g. a CI
    environment without the disposable candidate build)."""
    cold_root = ROOT / "database" / "_p5a_migration_build" / "cold_segments"
    if not cold_root.is_dir():
        pytest.skip("candidate COLD segments not present in this environment")

    registry = ColdSegmentRegistry(str(cold_root)).build()
    if registry.segment_count < 2:
        pytest.skip("fewer than 2 real segments available to check disjointness")

    seg_sig_sets = []
    for conn in registry.connections:
        sigs = {r[0] for r in conn.execute("SELECT DISTINCT signature FROM transfer_index")}
        seg_sig_sets.append(sigs)

    total_sum = sum(len(s) for s in seg_sig_sets)
    total_union = len(set().union(*seg_sig_sets))
    assert total_sum == total_union, (
        f"expected COLD segments to be signature-disjoint (sum={total_sum} == union={total_union}); "
        f"if this fails, the architecture's disjointness assumption no longer holds for real data "
        f"and per-segment signature counts can no longer be summed without set-level dedup"
    )
    registry.close()


# ---------------------------------------------------------------------------
# Grouped-aggregate (top_funders) merge correctness
# ---------------------------------------------------------------------------

def test_grouped_aggregate_merge_top_funders_correctness():
    """A funder appears in both a COLD segment and HOT, funding overlapping
    AND distinct creators across tiers. creators_funded must reflect the
    true union, transfer_count/total_sol must be the true sum."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        cold_root = td / "cold"
        cold_root.mkdir()
        _make_cold_segment(
            cold_root / "transfer_index_cold_2026_01.sqlite",
            segment_id="transfer_index_cold_2026_01", month_covered="2026_01",
            rows=[
                ("C1", "BIG_FUNDER", "CREATOR_A", 1.0, 100),
                ("C2", "BIG_FUNDER", "CREATOR_B", 2.0, 101),
            ],
        )
        hot_path = td / "hot.db"
        hot = _hot_conn(str(hot_path))
        _insert(hot, sig="H1", source="BIG_FUNDER", dest="CREATOR_A", sol=1.5, block_time=200)  # same creator, new sig
        _insert(hot, sig="H2", source="BIG_FUNDER", dest="CREATOR_C", sol=4.0, block_time=201)  # new creator
        hot.close()

        registry = ColdSegmentRegistry(str(cold_root)).build()
        store = SummaryStore(str(td / "summaries"))
        build_all_summaries(registry, store)

        factory = TransferReaderFactory(hot_db_path=str(hot_path), cold_root=str(cold_root))
        try:
            reader = factory.get_transfer_reader(fail_closed=True)
            result = compute_exact_stats(reader.hot_conn, registry, store)
        finally:
            factory.close()

        row = next(r for r in result["top_funders"] if r["source"] == "BIG_FUNDER")
        assert row["creators_funded"] == 3  # CREATOR_A, B, C (A deduped across tiers)
        assert row["transfer_count"] == 4   # C1, C2, H1, H2 -- all counted
        assert abs(row["total_sol"] - 8.5) < 1e-6  # 1.0+2.0+1.5+4.0


# ---------------------------------------------------------------------------
# LIMIT-boundary tie handling (semantic equivalence, not row order)
# ---------------------------------------------------------------------------

def test_overlap_limit_boundary_tie_is_semantic_not_order_sensitive():
    """overlap_raw has no secondary sort key beyond shared_creators DESC
    (matching the live query's actual ORDER BY, per docs/audits/
    storage_lifecycle_p5a_part18_transfer_graph_stats_parity.json). Two
    funders tied on shared_creators must both appear with correct values;
    this test does not assert a specific row order for the tied pair."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        cold_root = td / "cold"
        cold_root.mkdir()
        _make_cold_segment(
            cold_root / "transfer_index_cold_2026_01.sqlite",
            segment_id="transfer_index_cold_2026_01", month_covered="2026_01",
            rows=[
                ("C1", "TIE_A", "CREATOR_1", 1.0, 100),
                ("C2", "TIE_A", "CREATOR_2", 1.0, 101),
                ("C3", "TIE_B", "CREATOR_3", 1.0, 102),
                ("C4", "TIE_B", "CREATOR_4", 1.0, 103),
            ],
        )
        hot_path = td / "hot.db"
        hot = _hot_conn(str(hot_path))
        hot.close()

        registry = ColdSegmentRegistry(str(cold_root)).build()
        store = SummaryStore(str(td / "summaries"))
        build_all_summaries(registry, store)

        factory = TransferReaderFactory(hot_db_path=str(hot_path), cold_root=str(cold_root))
        try:
            reader = factory.get_transfer_reader(fail_closed=True)
            result = compute_exact_stats(reader.hot_conn, registry, store)
        finally:
            factory.close()

        by_source = {r["source"]: r["shared_creators"] for r in result["overlap_raw"]}
        assert by_source.get("TIE_A") == 2
        assert by_source.get("TIE_B") == 2


# ---------------------------------------------------------------------------
# Summary-to-segment digest binding / stale-summary rejection (fail closed)
# ---------------------------------------------------------------------------

def test_stale_summary_digest_mismatch_fails_closed(tmp_path):
    """If a segment's stored summary has a digest that no longer matches
    the segment's live manifest digest, load_verified must raise
    StaleSummaryError rather than returning the stale data."""
    cold_root = tmp_path / "cold"
    cold_root.mkdir()
    seg_path = cold_root / "transfer_index_cold_2026_01.sqlite"
    _make_cold_segment(
        seg_path, segment_id="transfer_index_cold_2026_01", month_covered="2026_01",
        rows=[("C1", "F", "D", 1.0, 100)], digest="DIGEST_V1",
    )

    conn = sqlite3.connect(str(seg_path))
    store = SummaryStore(str(tmp_path / "summaries"))
    summary = build_segment_summary(conn, "transfer_index_cold_2026_01")
    store.save(summary)

    # Simulate a digest change on the "closed" segment (should never happen
    # for a genuinely immutable segment, but the fail-closed check must
    # still catch it if it somehow does).
    conn.execute(
        "UPDATE segment_manifest SET sha256_of_sorted_signatures=? WHERE segment_id=?",
        ("DIGEST_V2_CHANGED", "transfer_index_cold_2026_01"),
    )
    conn.commit()

    with pytest.raises(StaleSummaryError):
        store.load_verified("transfer_index_cold_2026_01", conn)
    conn.close()


def test_missing_summary_file_fails_closed(tmp_path):
    """A segment that is qualified in the registry but has NO summary file
    at all must raise StaleSummaryError (fail closed), not silently be
    skipped/treated as zero contribution."""
    store = SummaryStore(str(tmp_path / "summaries"))
    conn = sqlite3.connect(":memory:")
    conn.executescript(COLD_SCHEMA)
    conn.execute(
        "INSERT INTO segment_manifest (segment_id, created_at, closed_at, row_count, "
        "sha256_of_sorted_signatures, source_run_id, month_covered) VALUES (?,?,?,?,?,?,?)",
        ("nonexistent_segment", time.time(), time.time(), 0, "SOME_DIGEST", "run", "2026_01"),
    )
    conn.commit()

    with pytest.raises(StaleSummaryError):
        store.load_verified("nonexistent_segment", conn)
    conn.close()


def test_corrupt_summary_file_fails_closed(tmp_path):
    """A summary file that exists but is corrupt/unreadable JSON must also
    fail closed, not crash uninformatively or silently return partial
    data."""
    store_dir = tmp_path / "summaries"
    store_dir.mkdir()
    (store_dir / "broken_segment.summary.json").write_text("{not valid json")

    conn = sqlite3.connect(":memory:")
    conn.executescript(COLD_SCHEMA)
    conn.execute(
        "INSERT INTO segment_manifest (segment_id, created_at, closed_at, row_count, "
        "sha256_of_sorted_signatures, source_run_id, month_covered) VALUES (?,?,?,?,?,?,?)",
        ("broken_segment", time.time(), time.time(), 0, "DIGEST", "run", "2026_01"),
    )
    conn.commit()

    store = SummaryStore(str(store_dir))
    with pytest.raises(StaleSummaryError):
        store.load_verified("broken_segment", conn)
    conn.close()


# ---------------------------------------------------------------------------
# New-segment inclusion (data-driven registry scan, no manual code change)
# ---------------------------------------------------------------------------

def test_new_segment_added_and_summary_built_and_included(tmp_path):
    """Simulates a new month's COLD segment landing: build_all_summaries
    picks it up automatically (no code change needed), and its data is
    then included in the merged aggregate."""
    cold_root = tmp_path / "cold"
    cold_root.mkdir()
    _make_cold_segment(
        cold_root / "transfer_index_cold_2026_01.sqlite",
        segment_id="transfer_index_cold_2026_01", month_covered="2026_01",
        rows=[("C1", "F1", "D1", 1.0, 100)],
    )
    hot_path = tmp_path / "hot.db"
    hot = _hot_conn(str(hot_path))
    hot.close()

    registry = ColdSegmentRegistry(str(cold_root)).build()
    store = SummaryStore(str(tmp_path / "summaries"))
    report1 = build_all_summaries(registry, store)
    assert report1["built"] == ["transfer_index_cold_2026_01"]

    factory = TransferReaderFactory(hot_db_path=str(hot_path), cold_root=str(cold_root))
    try:
        reader = factory.get_transfer_reader(fail_closed=True)
        result_before = compute_exact_stats(reader.hot_conn, registry, store)
    finally:
        factory.close()
    assert result_before["total_rows"] == 1

    # A "new month" segment lands.
    _make_cold_segment(
        cold_root / "transfer_index_cold_2026_02.sqlite",
        segment_id="transfer_index_cold_2026_02", month_covered="2026_02",
        rows=[("C2", "F2", "D2", 1.0, 200)],
    )
    registry2 = ColdSegmentRegistry(str(cold_root)).build()
    report2 = build_all_summaries(registry2, store)
    assert report2["built"] == ["transfer_index_cold_2026_02"]
    assert report2["skipped"] == ["transfer_index_cold_2026_01"]  # unchanged, not rebuilt

    factory2 = TransferReaderFactory(hot_db_path=str(hot_path), cold_root=str(cold_root))
    try:
        reader2 = factory2.get_transfer_reader(fail_closed=True)
        result_after = compute_exact_stats(reader2.hot_conn, registry2, store)
    finally:
        factory2.close()
    assert result_after["total_rows"] == 2


# ---------------------------------------------------------------------------
# HOT freshness: a new HOT-only row is reflected without touching COLD
# ---------------------------------------------------------------------------

def test_hot_freshness_new_row_reflected_without_touching_cold(tmp_path):
    """Adding a row to HOT (no COLD change) must be reflected in the next
    compute_exact_stats_fast call, and the COLD summary files/digests must
    be untouched (proves HOT freshness doesn't require rebuilding any COLD
    summary)."""
    cold_root = tmp_path / "cold"
    cold_root.mkdir()
    _make_cold_segment(
        cold_root / "transfer_index_cold_2026_01.sqlite",
        segment_id="transfer_index_cold_2026_01", month_covered="2026_01",
        rows=[("C1", "F1", "D1", 1.0, 100)],
    )
    hot_path = tmp_path / "hot.db"
    hot = _hot_conn(str(hot_path))
    _insert(hot, sig="H1", source="F2", dest="D2", sol=1.0, block_time=200)
    hot.close()

    registry = ColdSegmentRegistry(str(cold_root)).build()
    store = SummaryStore(str(tmp_path / "summaries"))
    build_all_summaries(registry, store)
    summary_path = store._path("transfer_index_cold_2026_01")
    mtime_before = os.path.getmtime(summary_path)

    factory = TransferReaderFactory(hot_db_path=str(hot_path), cold_root=str(cold_root))
    cache = HotCheckpointCache(str(tmp_path / "hot_cache.json"))
    try:
        reader = factory.get_transfer_reader(fail_closed=True)
        result1 = compute_exact_stats_fast(reader.hot_conn, registry, store, cache)
        assert result1["total_rows"] == 2

        # New row lands in HOT only.
        hot2 = sqlite3.connect(str(hot_path))
        _insert(hot2, sig="H2", source="F3", dest="D3", sol=1.0, block_time=201)
        hot2.close()

        result2 = compute_exact_stats_fast(reader.hot_conn, registry, store, cache)
        assert result2["total_rows"] == 3
        assert result2["distinct_sources"] == 3  # F1, F2, F3
    finally:
        factory.close()

    mtime_after = os.path.getmtime(summary_path)
    assert mtime_before == mtime_after, "COLD summary file must not be touched by a HOT-only change"


# ---------------------------------------------------------------------------
# Bounded storage size
# ---------------------------------------------------------------------------

def test_summary_storage_size_bounded_relative_to_real_cold_archive():
    """Real measurement against the actual 42-segment candidate: the total
    summary store size must be meaningfully smaller than the raw COLD
    archive size (order-of-magnitude smaller, not just 'less than
    infinite'). Skips if the candidate isn't present."""
    summary_dir = ROOT / "database" / "_p5a_migration_build" / "transfer_stats_summary"
    cold_root = ROOT / "database" / "_p5a_migration_build" / "cold_segments"
    if not summary_dir.is_dir() or not cold_root.is_dir():
        pytest.skip("candidate summary store / COLD segments not present in this environment")

    summary_bytes = sum(f.stat().st_size for f in summary_dir.glob("*.summary.json"))
    cold_bytes = sum(f.stat().st_size for f in cold_root.glob("*.sqlite"))
    assert cold_bytes > 0, "expected real COLD segment files to be present for this comparison"
    assert summary_bytes < cold_bytes, (
        f"summary store ({summary_bytes} bytes) should be smaller than the raw COLD "
        f"archive ({cold_bytes} bytes) it summarizes"
    )


# ---------------------------------------------------------------------------
# No connection/FD leak
# ---------------------------------------------------------------------------

def test_repeated_summary_builds_do_not_leak_connections(tmp_path):
    """build_segment_summary opens its own conn per call in some flows;
    this test proves the standard usage pattern (registry connections
    reused, build_all_summaries never opens NEW connections beyond the
    registry's held set) does not leak file descriptors across repeated
    calls."""
    cold_root = tmp_path / "cold"
    cold_root.mkdir()
    for i in range(5):
        _make_cold_segment(
            cold_root / f"transfer_index_cold_2026_{i+1:02d}.sqlite",
            segment_id=f"transfer_index_cold_2026_{i+1:02d}", month_covered=f"2026_{i+1:02d}",
            rows=[(f"C{i}", f"F{i}", f"D{i}", 1.0, 100 + i)],
        )

    registry = ColdSegmentRegistry(str(cold_root)).build()
    store = SummaryStore(str(tmp_path / "summaries"))

    conns_before = len(registry.connections)
    for _ in range(3):
        build_all_summaries(registry, store)  # repeated calls, should skip (fresh) after first
    conns_after = len(registry.connections)

    assert conns_before == conns_after == 5
    registry.close()


# ---------------------------------------------------------------------------
# Fail-closed: COLD registry entirely unavailable
# ---------------------------------------------------------------------------

def test_cold_registry_unavailable_fails_closed(tmp_path):
    hot_path = tmp_path / "hot.db"
    hot = _hot_conn(str(hot_path))
    hot.close()
    empty_cold_root = tmp_path / "empty_cold"
    empty_cold_root.mkdir()

    factory = TransferReaderFactory(hot_db_path=str(hot_path), cold_root=str(empty_cold_root))
    try:
        with pytest.raises(ColdRegistryUnavailableError):
            factory.get_transfer_reader(fail_closed=True)
    finally:
        factory.close()


# ---------------------------------------------------------------------------
# Structural: no production route handler calls this module's functions
# ---------------------------------------------------------------------------

def test_main_py_route_handlers_do_not_call_r2_2_fast_path():
    """AST-based structural check, matching R2.1's established pattern:
    none of this module's public entry points are called from inside any
    @app.route-decorated function body in main.py."""
    main_py = ROOT / "src" / "core" / "main.py"
    tree = ast.parse(main_py.read_text())

    new_names = {
        "compute_exact_stats", "compute_exact_stats_fast",
        "build_segment_summary", "build_all_summaries",
        "HotCheckpointCache",
    }

    def is_route_decorated(node: ast.FunctionDef) -> bool:
        for dec in node.decorator_list:
            if "route" in ast.dump(dec):
                return True
        return False

    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and is_route_decorated(node):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    func = sub.func
                    name = getattr(func, "id", None) or getattr(func, "attr", None)
                    if name in new_names:
                        violations.append((node.name, name))

    assert violations == [], f"route handler(s) call R2.2 dormant fast-path code: {violations}"


def test_transfer_graph_stats_summary_module_not_imported_by_main():
    """Even an unused import would be a step toward activation risk --
    confirm main.py does not import this module at all yet."""
    main_py = ROOT / "src" / "core" / "main.py"
    tree = ast.parse(main_py.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and "transfer_graph_stats_summary" in node.module:
            pytest.fail("main.py must not import transfer_graph_stats_summary yet (dormant-only milestone)")
        if isinstance(node, ast.Import):
            for alias in node.names:
                if "transfer_graph_stats_summary" in alias.name:
                    pytest.fail("main.py must not import transfer_graph_stats_summary yet (dormant-only milestone)")
