"""X78.19 Phase C -- assign_live_network_for_creator no longer holds the
cross-process write lane during its read phase.

Before this change, the function opened ONE db_connect() (a tracked, write-
lane-eligible connection) and held it across: an inline infra_wallets sync
(a real scan+write via sync_infra_wallets), an exclusion-set read, and two
more SELECTs -- before ever reaching the single small INSERT OR IGNORE that
actually needed write ownership. That inline sync_infra_wallets call in
particular duplicated work X78.14 already moved to infra_sync_scheduler,
and was the dominant hold time for a call site invoked on every creator at
birth/funding-extraction time -- directly contributing to the birth-insert
starvation MC1.2B measured.

Fix: read phase runs on a read-only connection (db_connect(..., read_only=
True), bypassing the write lane entirely and skipping the inline infra
resync); only the final INSERT OR IGNORE acquires a write-lane connection,
held for a single statement.

These tests verify the OUTCOME is unchanged (same qualifying-funder
selection, same network-name resolution priority, same INSERT OR IGNORE
semantics) against a real temp SQLite file with the actual schema shape.
"""
import os
import sqlite3
import tempfile

import pytest

from src.core.network_membership_builder import assign_live_network_for_creator


SCHEMA_SQL = """
CREATE TABLE creator_funders (
    creator_address TEXT NOT NULL,
    funder_address TEXT NOT NULL,
    amount_sol REAL NOT NULL,
    is_cex BOOLEAN DEFAULT 0,
    PRIMARY KEY(creator_address, funder_address)
);
CREATE TABLE network_membership (
    network_name TEXT,
    creator_address TEXT, funder_address TEXT,
    PRIMARY KEY (network_name, creator_address)
);
CREATE TABLE funder_network_map (
    funder_address TEXT PRIMARY KEY,
    network_name   TEXT NOT NULL,
    creator_count  INTEGER DEFAULT 0,
    last_built_at  TEXT
);
CREATE TABLE infra_wallets (
    address TEXT PRIMARY KEY,
    type TEXT,
    label TEXT,
    updated_at INTEGER DEFAULT (strftime('%s','now'))
);
"""


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    conn.close()
    yield path
    os.unlink(path)


def _insert_funders(db_path, rows):
    conn = sqlite3.connect(db_path)
    with conn:
        conn.executemany(
            "INSERT INTO creator_funders (creator_address, funder_address, amount_sol, is_cex) VALUES (?,?,?,?)",
            rows,
        )
    conn.close()


def test_no_qualifying_funder_returns_null_result_read_only(temp_db):
    """A creator with only a single-use / non-shared funder must return the
    unassigned null_result without ever touching network_membership."""
    _insert_funders(temp_db, [("CreatorSolo", "FunderOnce", 1.0, 0)])

    result = assign_live_network_for_creator(temp_db, "CreatorSolo")

    assert result["assigned"] is False
    assert result["network_name"] is None

    conn = sqlite3.connect(temp_db)
    count = conn.execute("SELECT COUNT(*) FROM network_membership").fetchone()[0]
    conn.close()
    assert count == 0


def test_shared_funder_creates_provisional_network(temp_db):
    """Two creators funded by the same non-CEX, non-infra wallet -> the
    second creator gets a Provisional_ network assignment, and the write
    (INSERT OR IGNORE) lands correctly even though reads went through a
    separate read-only connection."""
    _insert_funders(temp_db, [
        ("CreatorA", "SharedFunder", 5.0, 0),
        ("CreatorB", "SharedFunder", 3.0, 0),
    ])

    result = assign_live_network_for_creator(temp_db, "CreatorB")

    assert result["assigned"] is True
    assert result["provisional"] is True
    assert result["network_name"] == "Provisional_SharedFu"
    assert result["funder_address"] == "SharedFunder"
    assert result["creators_in_network"] == 2

    conn = sqlite3.connect(temp_db)
    row = conn.execute(
        "SELECT network_name, funder_address FROM network_membership WHERE creator_address=?",
        ("CreatorB",),
    ).fetchone()
    conn.close()
    assert row == ("Provisional_SharedFu", "SharedFunder")


def test_existing_funder_network_map_name_takes_priority(temp_db):
    """Priority 1 (funder_network_map) must still win over the provisional
    name path -- unchanged behavior after the read/write split."""
    _insert_funders(temp_db, [
        ("CreatorC", "KnownFunder", 5.0, 0),
        ("CreatorD", "KnownFunder", 3.0, 0),
    ])
    conn = sqlite3.connect(temp_db)
    with conn:
        conn.execute(
            "INSERT INTO funder_network_map (funder_address, network_name) VALUES (?, ?)",
            ("KnownFunder", "Network_42"),
        )
    conn.close()

    result = assign_live_network_for_creator(temp_db, "CreatorD")

    assert result["network_name"] == "Network_42"
    assert result["provisional"] is False


def test_infra_wallet_funder_excluded_from_qualification(temp_db):
    """An infra-registered funder must still be excluded via build_excluded_set
    -- proves the read-only exclusion-set lookup (no more inline
    sync_infra_wallets call) still sees rows already present in infra_wallets."""
    _insert_funders(temp_db, [
        ("CreatorE", "InfraFunder", 5.0, 0),
        ("CreatorF", "InfraFunder", 3.0, 0),
    ])
    conn = sqlite3.connect(temp_db)
    with conn:
        conn.execute(
            "INSERT INTO infra_wallets (address, type, label) VALUES (?, 'exchange', 'test-infra')",
            ("InfraFunder",),
        )
    conn.close()

    result = assign_live_network_for_creator(temp_db, "CreatorF")

    assert result["assigned"] is False, "infra-registered funder must remain excluded"


def test_insert_or_ignore_does_not_clobber_canonical_builder_result(temp_db):
    """Canonical (30-min) builder's row must always win on conflict -- proves
    the write-lane-scoped INSERT OR IGNORE still has the correct conflict
    semantics after moving to its own short-lived connection."""
    _insert_funders(temp_db, [
        ("CreatorG", "SharedFunder2", 5.0, 0),
        ("CreatorH", "SharedFunder2", 3.0, 0),
    ])
    conn = sqlite3.connect(temp_db)
    with conn:
        conn.execute(
            "INSERT INTO network_membership (network_name, creator_address, funder_address) VALUES (?, ?, ?)",
            ("Network_Canonical", "CreatorH", "SharedFunder2"),
        )
    conn.close()

    result = assign_live_network_for_creator(temp_db, "CreatorH")

    # Function still resolves/returns whatever name it computed...
    assert result["assigned"] is True
    # ...but the DB row itself must remain the canonical builder's, untouched.
    conn = sqlite3.connect(temp_db)
    row = conn.execute(
        "SELECT network_name FROM network_membership WHERE creator_address=?",
        ("CreatorH",),
    ).fetchone()
    conn.close()
    assert row[0] == "Network_Canonical"


def test_repeated_calls_for_same_creator_are_idempotent(temp_db):
    """Calling twice for the same creator/funder pair must not raise or
    create duplicate rows (PRIMARY KEY(network_name, creator_address))."""
    _insert_funders(temp_db, [
        ("CreatorI", "SharedFunder3", 5.0, 0),
        ("CreatorJ", "SharedFunder3", 3.0, 0),
    ])

    r1 = assign_live_network_for_creator(temp_db, "CreatorJ")
    r2 = assign_live_network_for_creator(temp_db, "CreatorJ")

    assert r1["network_name"] == r2["network_name"]

    conn = sqlite3.connect(temp_db)
    count = conn.execute(
        "SELECT COUNT(*) FROM network_membership WHERE creator_address=?", ("CreatorJ",)
    ).fetchone()[0]
    conn.close()
    assert count == 1
