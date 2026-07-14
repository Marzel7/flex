"""X21E — OperationalBehaviourService backend tests.

Covers the required test categories from the sprint brief that apply at the
service layer (template-layer coverage lives in
test_ops_x21e_operational_behaviour_rendering.py):
  - behaviour cards disappear gracefully when data is absent
  - timing is shown only when X21B sessions exist
  - no inferred/fabricated behaviour is rendered (no "fresh wallet", no
    composite "token-specific infrastructure" label, no percentages)
  - unresolved investigations honestly report missing evidence

Uses real on-disk temp SQLite files (not :memory:) since the service opens
connections via `file:{path}?mode=ro` URIs, which require a real path.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest

from src.ops.operational_behaviour import OperationalBehaviourService


@pytest.fixture
def dbs():
    ops_fd, ops_path = tempfile.mkstemp(suffix=".db")
    core_fd, core_path = tempfile.mkstemp(suffix=".db")
    os.close(ops_fd)
    os.close(core_fd)

    ops_conn = sqlite3.connect(ops_path)
    ops_conn.executescript("""
        CREATE TABLE wt_provisioning_sessions (
            source_mint TEXT, treasury TEXT, subprov TEXT, creator TEXT,
            treasury_to_subprov_latency_seconds INTEGER,
            subprov_to_creator_latency_seconds INTEGER,
            creator_to_launch_latency_seconds INTEGER,
            recorded_at INTEGER
        );
        CREATE TABLE wt_provisioning_edges (
            edge_type TEXT, from_wallet TEXT, to_wallet TEXT,
            observation_count INTEGER, funding_mechanism TEXT,
            funding_amount_sol REAL, funding_block_time INTEGER, source_mint TEXT
        );
        CREATE TABLE wt_discovered_subprovs (
            subprov TEXT PRIMARY KEY, first_creator TEXT, creator_count INTEGER DEFAULT 1,
            treasury TEXT, treasury_known INTEGER, funding_mechanism TEXT, wrap_close_count INTEGER
        );
        CREATE TABLE wt_treasury_review (
            treasury TEXT PRIMARY KEY, distinct_subprovs INTEGER DEFAULT 1,
            distinct_creators INTEGER DEFAULT 1, status TEXT
        );
    """)
    ops_conn.commit()
    ops_conn.close()

    core_conn = sqlite3.connect(core_path)
    core_conn.executescript("""
        CREATE TABLE wt_known_operator_hubs (hub_wallet TEXT PRIMARY KEY, operator_identity TEXT);
        CREATE TABLE wt_provisioning_hubs (hub_address TEXT PRIMARY KEY, status TEXT);
    """)
    core_conn.commit()
    core_conn.close()

    yield ops_path, core_path

    os.unlink(ops_path)
    os.unlink(core_path)


def _insert(path: str, table: str, **cols):
    conn = sqlite3.connect(path)
    keys = ",".join(cols.keys())
    placeholders = ",".join("?" for _ in cols)
    conn.execute(f"INSERT INTO {table} ({keys}) VALUES ({placeholders})", tuple(cols.values()))
    conn.commit()
    conn.close()


def test_returns_none_when_no_identifiers_given(dbs):
    ops_path, core_path = dbs
    svc = OperationalBehaviourService(ops_path, core_path)
    assert svc.build() is None


def test_completely_unknown_mint_degrades_gracefully(dbs):
    ops_path, core_path = dbs
    svc = OperationalBehaviourService(ops_path, core_path)
    result = svc.build(source_mint="UNKNOWN_MINT_NOT_IN_ANY_TABLE")
    assert result is not None
    assert result["behaviour_summary"] == []
    assert result["timing"] == {"available": False}
    assert result["infrastructure_pattern"] == []
    # every consistency signal degrades to "Not yet available", never a percentage
    statuses = {row["status"] for row in result["operational_consistency"]}
    assert statuses <= {"Not yet available", "Not observed"}
    assert result["missing_evidence"]  # honestly reports gaps


def test_timing_only_shown_when_session_has_latency_fields(dbs):
    ops_path, core_path = dbs
    _insert(ops_path, "wt_provisioning_sessions", source_mint="MINT1", treasury="T1",
            subprov="S1", creator="C1", treasury_to_subprov_latency_seconds=None,
            subprov_to_creator_latency_seconds=None, creator_to_launch_latency_seconds=None,
            recorded_at=1)
    svc = OperationalBehaviourService(ops_path, core_path)
    result = svc.build(source_mint="MINT1")
    assert result["timing"]["available"] is False

    _insert(ops_path, "wt_provisioning_sessions", source_mint="MINT2", treasury="T2",
            subprov="S2", creator="C2", treasury_to_subprov_latency_seconds=120,
            subprov_to_creator_latency_seconds=5, creator_to_launch_latency_seconds=None,
            recorded_at=2)
    result2 = svc.build(source_mint="MINT2")
    assert result2["timing"]["available"] is True
    stages = {o["stage"] for o in result2["timing"]["observations"]}
    assert stages == {"Treasury → Sub-Provisioner", "Sub-Provisioner → Creator"}


def test_no_fresh_wallet_or_composite_claims_in_any_output(dbs):
    ops_path, core_path = dbs
    _insert(ops_path, "wt_provisioning_sessions", source_mint="MINT3", treasury="T3",
            subprov="S3", creator="C3", treasury_to_subprov_latency_seconds=10,
            subprov_to_creator_latency_seconds=5, creator_to_launch_latency_seconds=2, recorded_at=1)
    _insert(ops_path, "wt_provisioning_edges", edge_type="TREASURY_TO_SUBPROV", from_wallet="T3",
            to_wallet="S3", observation_count=1, funding_mechanism="WSOL_WRAP_CLOSE",
            funding_amount_sol=5.0, funding_block_time=100, source_mint="MINT3")
    _insert(ops_path, "wt_provisioning_edges", edge_type="SUBPROV_TO_CREATOR", from_wallet="S3",
            to_wallet="C3", observation_count=1, funding_mechanism="WSOL_WRAP_CLOSE",
            funding_amount_sol=1.0, funding_block_time=110, source_mint="MINT3")
    _insert(ops_path, "wt_discovered_subprovs", subprov="S3", first_creator="C3", creator_count=1,
            treasury="T3", treasury_known=1, funding_mechanism="WSOL_WRAP_CLOSE", wrap_close_count=1)

    svc = OperationalBehaviourService(ops_path, core_path)
    result = svc.build(source_mint="MINT3")
    flat = " ".join(result["behaviour_summary"]) + " " + " ".join(p["label"] for p in result["infrastructure_pattern"])
    assert "fresh" not in flat.lower()
    assert "Token-specific infrastructure" not in flat
    import re
    assert re.search(r"\d+%", flat) is None
    for row in result["operational_consistency"]:
        assert row["status"] in {"Observed", "Not observed", "Not yet available"}


def test_unresolved_investigation_honestly_reports_missing_evidence(dbs):
    ops_path, core_path = dbs
    _insert(ops_path, "wt_discovered_subprovs", subprov="S4", first_creator="C4", creator_count=1,
            treasury=None, treasury_known=0, funding_mechanism=None, wrap_close_count=0)
    svc = OperationalBehaviourService(ops_path, core_path)
    result = svc.build(subprov="S4")
    assert "Repeated treasury" in " ".join(result["missing_evidence"])
    assert "Provisioning hub reuse" in result["missing_evidence"]


def test_every_infrastructure_pattern_entry_cites_a_source_column(dbs):
    ops_path, core_path = dbs
    _insert(ops_path, "wt_discovered_subprovs", subprov="S5", first_creator="C5", creator_count=3,
            treasury="T5", treasury_known=1, funding_mechanism="WSOL_WRAP_CLOSE", wrap_close_count=3)
    svc = OperationalBehaviourService(ops_path, core_path)
    result = svc.build(subprov="S5")
    assert result["infrastructure_pattern"]
    for entry in result["infrastructure_pattern"]:
        assert "source" in entry and entry["source"]
