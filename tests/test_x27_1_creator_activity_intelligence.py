"""Regression tests for X27.1 — Creator Activity Intelligence.

Verifies the new Creator Activity section describes only the creator
wallet's own historical launch behaviour, is derived exclusively from
token_analysis, never mentions funding/attribution/infrastructure
concepts, uses fixed measurable classification thresholds, and performs
zero database writes.
"""
import os
import sqlite3
import tempfile
import time

import pytest

from src.ops.creator_activity import (
    CreatorActivityService,
    STATUS_SINGLE_LAUNCH,
    STATUS_REPEAT_CREATOR,
    STATUS_HIGHLY_ACTIVE,
    HIGHLY_ACTIVE_LAUNCH_COUNT,
    HIGHLY_ACTIVE_RECENT_WINDOW_SECONDS,
)


@pytest.fixture
def db_factory(tmp_path):
    def make(rows, columns=None):
        path = str(tmp_path / f"core_{len(rows)}_{time.time_ns()}.db")
        conn = sqlite3.connect(path)
        cols = columns or ["mint", "pf_ws_creator", "earliest_tx_creator", "created_at", "migrated_at", "market_cap_highest"]
        conn.execute(f"CREATE TABLE token_analysis ({', '.join(c + ' TEXT' if c in ('mint','pf_ws_creator','earliest_tx_creator') else c + ' REAL' for c in cols)})")
        for r in rows:
            placeholders = ", ".join("?" for _ in cols)
            conn.execute(f"INSERT INTO token_analysis ({', '.join(cols)}) VALUES ({placeholders})", [r.get(c) for c in cols])
        conn.commit()
        conn.close()
        return path
    return make


def _hash(path):
    import hashlib
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


@pytest.fixture
def mixed_type_db(tmp_path):
    """token_analysis with created_at/migrated_at declared TEXT, mirroring
    the live schema where the column holds unix-epoch integers for most
    rows but ISO-8601 strings ("2026-07-16T21:02:32Z") for ~1.8% of rows
    (confirmed live 2026-07-16). SQLite's dynamic typing preserves whichever
    representation is inserted regardless of the declared column type, so
    this reproduces the real mixed-representation column exactly."""
    path = str(tmp_path / f"mixed_{time.time_ns()}.db")
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE token_analysis (mint TEXT, pf_ws_creator TEXT, "
        "earliest_tx_creator TEXT, created_at TEXT, migrated_at TEXT, market_cap_highest REAL)"
    )
    yield path, conn
    conn.close()


def test_no_creator_returns_none(db_factory):
    path = db_factory([])
    assert CreatorActivityService(path).build(None) is None
    assert CreatorActivityService(path).build("") is None


def test_missing_table_returns_none(tmp_path):
    path = str(tmp_path / "empty.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE unrelated (x TEXT)")
    conn.commit()
    conn.close()
    assert CreatorActivityService(path).build("Creator1") is None


def test_creator_with_zero_launches_returns_explicit_zero_report(db_factory):
    path = db_factory([{"mint": "M1", "pf_ws_creator": "OtherCreator", "created_at": 1000}])
    result = CreatorActivityService(path).build("Creator1")
    assert result is not None
    assert result["launches_created"] == 0
    assert result["status"] is None


def test_single_launch_classification(db_factory):
    path = db_factory([{"mint": "M1", "pf_ws_creator": "Creator1", "created_at": 1000, "migrated_at": None, "market_cap_highest": None}])
    result = CreatorActivityService(path).build("Creator1")
    assert result["launches_created"] == 1
    assert result["status"] == STATUS_SINGLE_LAUNCH
    assert result["average_launch_cadence_seconds"] is None


def test_repeat_creator_classification(db_factory):
    now = time.time()
    rows = [
        {"mint": f"M{i}", "pf_ws_creator": "Creator1", "created_at": now - (5 - i) * 86400, "migrated_at": None, "market_cap_highest": None}
        for i in range(5)
    ]
    path = db_factory(rows)
    result = CreatorActivityService(path).build("Creator1")
    assert result["launches_created"] == 5
    assert result["status"] == STATUS_REPEAT_CREATOR


def test_highly_active_requires_volume_and_recency(db_factory):
    now = time.time()
    # 12 launches, all recent -> HIGHLY_ACTIVE
    rows = [
        {"mint": f"M{i}", "pf_ws_creator": "Creator1", "created_at": now - i * 3600, "migrated_at": None, "market_cap_highest": None}
        for i in range(HIGHLY_ACTIVE_LAUNCH_COUNT + 2)
    ]
    path = db_factory(rows)
    result = CreatorActivityService(path).build("Creator1")
    assert result["status"] == STATUS_HIGHLY_ACTIVE


def test_high_volume_but_stale_activity_is_repeat_not_highly_active(db_factory):
    long_ago = time.time() - HIGHLY_ACTIVE_RECENT_WINDOW_SECONDS * 3
    rows = [
        {"mint": f"M{i}", "pf_ws_creator": "Creator1", "created_at": long_ago + i * 100, "migrated_at": None, "market_cap_highest": None}
        for i in range(HIGHLY_ACTIVE_LAUNCH_COUNT + 5)
    ]
    path = db_factory(rows)
    result = CreatorActivityService(path).build("Creator1")
    # High volume, but last activity is long in the past -> not currently active.
    assert result["status"] == STATUS_REPEAT_CREATOR


def test_migration_rate_and_cadence_computed_correctly(db_factory):
    base = 1_000_000
    rows = [
        {"mint": "M1", "pf_ws_creator": "Creator1", "created_at": base, "migrated_at": base + 500, "market_cap_highest": 50000},
        {"mint": "M2", "pf_ws_creator": "Creator1", "created_at": base + 1000, "migrated_at": None, "market_cap_highest": 90000},
        {"mint": "M3", "pf_ws_creator": "Creator1", "created_at": base + 2000, "migrated_at": base + 2500, "market_cap_highest": None},
    ]
    path = db_factory(rows)
    result = CreatorActivityService(path).build("Creator1")
    assert result["launches_created"] == 3
    assert result["successful_migrations"] == 2
    assert result["migration_rate_pct"] == pytest.approx(66.7, abs=0.1)
    assert result["first_observed"] == base
    assert result["last_activity"] == base + 2000
    assert result["active_lifetime_seconds"] == 2000
    assert result["average_launch_cadence_seconds"] == 1000
    assert result["peak_market_cap"] == 90000
    assert result["peak_market_cap_coverage"] == "2 of 3 launches"


def test_peak_market_cap_honestly_reports_absence(db_factory):
    rows = [
        {"mint": "M1", "pf_ws_creator": "Creator1", "created_at": 1000, "migrated_at": None, "market_cap_highest": None},
        {"mint": "M2", "pf_ws_creator": "Creator1", "created_at": 2000, "migrated_at": None, "market_cap_highest": None},
    ]
    path = db_factory(rows)
    result = CreatorActivityService(path).build("Creator1")
    assert result["peak_market_cap"] is None
    assert result["peak_market_cap_coverage"] == "0 of 2 launches"


def test_pf_ws_creator_preferred_over_earliest_tx_creator(db_factory):
    # A wallet sharing an earliest_tx_creator with 1000s of unrelated launches
    # must NOT have those launches attributed via a COALESCE-style merge --
    # pf_ws_creator is authoritative and used exclusively when present.
    rows = [
        {"mint": "M1", "pf_ws_creator": "Creator1", "earliest_tx_creator": "SharedAuthority", "created_at": 1000},
        {"mint": "M2", "pf_ws_creator": "SomeoneElse", "earliest_tx_creator": "SharedAuthority", "created_at": 2000},
        {"mint": "M3", "pf_ws_creator": "AnotherOne", "earliest_tx_creator": "SharedAuthority", "created_at": 3000},
    ]
    path = db_factory(rows)
    result = CreatorActivityService(path).build("Creator1")
    assert result["launches_created"] == 1
    result_shared = CreatorActivityService(path).build("SharedAuthority")
    assert result_shared["launches_created"] == 0


def test_falls_back_to_earliest_tx_creator_when_pf_ws_creator_column_absent(db_factory):
    rows = [{"mint": "M1", "earliest_tx_creator": "Creator1", "created_at": 1000}]
    path = db_factory(rows, columns=["mint", "earliest_tx_creator", "created_at", "migrated_at", "market_cap_highest"])
    result = CreatorActivityService(path).build("Creator1")
    assert result["launches_created"] == 1


def test_no_database_mutation(db_factory):
    rows = [{"mint": "M1", "pf_ws_creator": "Creator1", "created_at": 1000, "migrated_at": None, "market_cap_highest": None}]
    path = db_factory(rows)
    before = _hash(path)
    CreatorActivityService(path).build("Creator1")
    after = _hash(path)
    assert before == after


def test_report_never_mentions_funding_or_infrastructure_terms(db_factory):
    rows = [{"mint": "M1", "pf_ws_creator": "Creator1", "created_at": 1000, "migrated_at": None, "market_cap_highest": 50000}]
    path = db_factory(rows)
    result = CreatorActivityService(path).build("Creator1")
    serialized = str(result).lower()
    for forbidden in ("treasury", "sub-provisioner", "subprov", "watchtower", "attribution", "wrap-close", "funding"):
        assert forbidden not in serialized


def test_iso8601_created_at_does_not_crash_and_parses_correctly(mixed_type_db):
    """Reproduces a live 500 error: token_analysis.created_at stored as an
    ISO-8601 string ("2026-07-16T21:02:32Z") crashed CreatorActivityService
    with TypeError: unsupported operand type(s) for -: 'str' and 'str' when
    computing active_lifetime_seconds. A blind CAST(created_at AS REAL)
    would silently truncate "2026-07-16..." to 2026.0 rather than erroring
    -- this test asserts the real epoch value, not just "no crash"."""
    path, conn = mixed_type_db
    conn.execute(
        "INSERT INTO token_analysis (mint, pf_ws_creator, created_at, migrated_at, market_cap_highest) "
        "VALUES (?,?,?,?,?)",
        ("M1", "Creator1", "2026-07-16T21:02:32Z", None, 2460.29),
    )
    conn.commit()
    result = CreatorActivityService(path).build("Creator1")
    assert result["launches_created"] == 1
    assert result["first_observed"] == 1784235752  # unix epoch for 2026-07-16T21:02:32Z
    assert result["last_activity"] == 1784235752


def test_mixed_integer_and_iso8601_created_at_in_same_creator(mixed_type_db):
    """A creator with one integer-epoch launch and one ISO-8601-string
    launch must still compute a correct cadence -- the real column mixes
    both representations across rows (confirmed live: 1,199,361 integer /
    3,565 real / 22,641 text rows in the same column)."""
    path, conn = mixed_type_db
    conn.execute(
        "INSERT INTO token_analysis (mint, pf_ws_creator, created_at, migrated_at, market_cap_highest) VALUES (?,?,?,?,?)",
        ("M1", "Creator1", 1784149435, None, None),
    )
    conn.execute(
        "INSERT INTO token_analysis (mint, pf_ws_creator, created_at, migrated_at, market_cap_highest) VALUES (?,?,?,?,?)",
        ("M2", "Creator1", "2026-07-16T21:02:32Z", None, None),
    )
    conn.commit()
    result = CreatorActivityService(path).build("Creator1")
    assert result["launches_created"] == 2
    assert result["first_observed"] == 1784149435
    assert result["last_activity"] == 1784235752
    assert result["active_lifetime_seconds"] == 1784235752 - 1784149435


def test_discovery_service_wires_creator_activity_key():
    from src.discovery.service import DiscoveryService
    svc = DiscoveryService.__new__(DiscoveryService)
    empty = svc._empty("x", "creator", 0)
    assert "creator_activity" in empty
    assert empty["creator_activity"] is None


def test_html_renders_creator_activity_card_independent_of_operational_behaviour():
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1] / "templates/discovery.html").read_text()
    assert "function creatorActivity(ca)" in html
    assert "Creator activity" in html
    assert "creatorAct=creatorActivity(d.creator_activity)" in html
    # Ensures the card is appended to the DOM assembly, not folded into
    # the existing Operational Behaviour block.
    assert "opBehaviour+creatorAct+attribution" in html
