from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path


def _module():
    path = Path(__file__).parents[1] / "scripts" / "retire_creator_funding_graph.py"
    spec = importlib.util.spec_from_file_location("retire_creator_funding_graph", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_retirement_preserves_only_frozen_non_authoritative_exceptions(tmp_path):
    module = _module()
    path = tmp_path / "main.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE creator_funders (creator_address TEXT, funder_address TEXT)")
    conn.execute(
        """CREATE TABLE creator_funding_graph (
        creator_address TEXT, funder_address TEXT, first_seen TEXT, last_seen TEXT,
        inbound_sol REAL, inbound_tx_count INTEGER,
        PRIMARY KEY(creator_address, funder_address))"""
    )
    for creator, funder, amount, seen in module.LEGACY_EXCEPTIONS:
        conn.execute(
            "INSERT INTO creator_funding_graph VALUES (?, ?, ?, ?, ?, 1)",
            (creator, funder, seen, seen, amount),
        )
    conn.commit()
    conn.close()

    first = module.retire(path, now="2026-09-04T12:00:00Z")
    assert first["outcome"] == "retired"
    assert first["exception_count"] == 12
    assert first["graph_present_after"] is False

    second = module.retire(path, now="2026-09-04T12:01:00Z")
    assert second["outcome"] == "already_retired"
    assert second["exception_digest"] == first["exception_digest"]

    conn = sqlite3.connect(path)
    assert conn.execute("SELECT COUNT(*) FROM creator_funders").fetchone()[0] == 0
    row = conn.execute(
        "SELECT status, canonical_authority FROM creator_funding_graph_legacy_exceptions LIMIT 1"
    ).fetchone()
    conn.close()
    assert row == ("INSUFFICIENT_EVIDENCE", 0)


def test_retirement_fails_closed_if_frozen_exception_drifted(tmp_path):
    module = _module()
    path = tmp_path / "main.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE creator_funders (creator_address TEXT, funder_address TEXT)")
    conn.execute(
        """CREATE TABLE creator_funding_graph (
        creator_address TEXT, funder_address TEXT, first_seen TEXT, last_seen TEXT,
        inbound_sol REAL, inbound_tx_count INTEGER,
        PRIMARY KEY(creator_address, funder_address))"""
    )
    creator, funder, amount, seen = module.LEGACY_EXCEPTIONS[0]
    conn.execute(
        "INSERT INTO creator_funding_graph VALUES (?, ?, ?, ?, ?, 1)",
        (creator, funder, seen, seen, amount + 1),
    )
    conn.commit()
    conn.close()

    try:
        module.retire(path)
    except RuntimeError as exc:
        assert "drift" in str(exc)
    else:
        raise AssertionError("migration did not fail closed")
