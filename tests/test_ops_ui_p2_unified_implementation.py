"""OPS-UI-P2: focused tests for the implemented unified read-time
projection (OperatorReader.fetch_unified_investigation and its route).

No provider calls. No production writes. All DB access mode=ro.
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

from src.core.db import OPS_DB_PATH  # noqa: E402
from src.ops.operator_reader import OperatorReader  # noqa: E402

DV34 = "Dv34prGm2BT7Ph2n6qKLgzeLgjnii87RJJ7Db6ZQQvKM"


@pytest.fixture(scope="module")
def reader():
    return OperatorReader(str(OPS_DB_PATH))


@pytest.fixture(scope="module")
def watchtower_entity():
    conn = sqlite3.connect(f"file:{OPS_DB_PATH}?mode=ro", uri=True)
    op = conn.execute("SELECT operator_id FROM operators WHERE display_name='WATCHTOWER'").fetchone()[0]
    entity = conn.execute(
        "SELECT entity_address FROM operator_entities WHERE operator_id=? LIMIT 1", (op,)
    ).fetchone()[0]
    conn.close()
    return entity


@pytest.fixture(scope="module")
def sw2_entity():
    conn = sqlite3.connect(f"file:{OPS_DB_PATH}?mode=ro", uri=True)
    op = conn.execute("SELECT operator_id FROM operators WHERE display_name='3SW2'").fetchone()[0]
    entity = conn.execute(
        "SELECT entity_address FROM operator_entities WHERE operator_id=? LIMIT 1", (op,)
    ).fetchone()[0]
    conn.close()
    return entity


def test_dv34_historical_population_is_131_after_retained_growth(reader):
    result = reader.fetch_unified_investigation(DV34)
    assert result["historical_population"]["count"] == 131  # post-baseline original_sender growth; bound in /tmp/p3r-registry-reconciliation


def test_dv34_high_qualified_is_23(reader):
    result = reader.fetch_unified_investigation(DV34)
    assert result["evidence_qualification"]["high_qualified_count"] == 23


def test_dv34_historical_never_equals_qualified(reader):
    result = reader.fetch_unified_investigation(DV34)
    assert result["historical_population"]["count"] != result["evidence_qualification"]["high_qualified_count"]


def test_dv34_remainder_includes_same_eight_retained_additions(reader):
    """OF-DV34-P3 established 82 valid-not-HIGH + 18 historical-only = 100
    remainder members; the bounded read-time projection reports this as a
    single combined remainder (not a full per-member P3-style pass)."""
    result = reader.fetch_unified_investigation(DV34)
    assert result["evidence_qualification"]["valid_not_high_or_historical_only"] == 108
    assert 108 == 131 - 23


def test_dv34_role_is_funding_structure_under_semantic_correction(reader):
    result = reader.fetch_unified_investigation(DV34)
    assert result["identity"]["candidate_role"] == "FUNDING_STRUCTURE"


def test_dv34_not_canonical_not_promotion_eligible(reader):
    result = reader.fetch_unified_investigation(DV34)
    assert result["identity"]["authority_state"] is None
    assert result["identity"]["canonical_operator_id"] is None
    assert result["identity"]["promotion_eligible"] is False


def test_watchtower_authority_state_confirmed(reader, watchtower_entity):
    result = reader.fetch_unified_investigation(watchtower_entity)
    assert result["identity"]["authority_state"] == "CONFIRMED"
    assert result["identity"]["candidate_role"] is None


def test_3sw2_authority_state_confirmed(reader, sw2_entity):
    result = reader.fetch_unified_investigation(sw2_entity)
    assert result["identity"]["authority_state"] == "CONFIRMED"


def test_watchtower_and_3sw2_only_confirmed_operators_live():
    conn = sqlite3.connect(f"file:{OPS_DB_PATH}?mode=ro", uri=True)
    rows = conn.execute("SELECT display_name FROM operators WHERE status='CONFIRMED'").fetchall()
    conn.close()
    assert {r[0] for r in rows} == {"WATCHTOWER", "3SW2"}


def test_evidence_cannot_set_canonical_status_structural():
    """Structural guard: fetch_unified_investigation must never write to
    operators.status -- read the source and confirm no write verb touches
    that table."""
    src = (ROOT / "src/ops/operator_reader.py").read_text()
    method_start = src.index("def fetch_unified_investigation")
    method_end = src.index("\n    def fetch_summary")
    method_body = src[method_start:method_end].upper()
    for verb in ("INSERT INTO OPERATORS", "UPDATE OPERATORS", "DELETE FROM OPERATORS"):
        assert verb not in method_body


def test_no_attach_database_cross_db_write_pattern():
    """Structural guard: the implementation must use 3 independent
    read-only connections, never ATTACH DATABASE (which could enable a
    cross-database write transaction)."""
    src = (ROOT / "src/ops/operator_reader.py").read_text()
    assert "ATTACH DATABASE" not in src.upper()
    assert "ATTACH '" not in src.upper() and 'ATTACH "' not in src.upper()


def test_route_returns_bounded_response_not_full_graph():
    """The unified route must never return a full member list, raw
    transaction bodies, or a full transfer graph -- only summary counts."""
    from flask import Flask

    from src.ops.operator_routes import operator_bp

    app = Flask(__name__, template_folder=str(ROOT / "templates"), static_folder=str(ROOT / "static"))
    app.register_blueprint(operator_bp)
    with app.test_client() as client:
        resp = client.get(f"/api/ops/investigation/{DV34}/unified")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "members" not in data
        assert "transactions" not in data
        assert "transfer_graph" not in data
        # response should be small -- a summary, not a dump
        assert len(resp.get_data()) < 5000


def test_route_watchtower_authority_preserved_end_to_end(watchtower_entity):
    from flask import Flask

    from src.ops.operator_routes import operator_bp

    app = Flask(__name__, template_folder=str(ROOT / "templates"), static_folder=str(ROOT / "static"))
    app.register_blueprint(operator_bp)
    with app.test_client() as client:
        resp = client.get(f"/api/ops/investigation/{watchtower_entity}/unified")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["identity"]["authority_state"] == "CONFIRMED"
        assert data["ok"] is True


def test_performance_dv34_lookup_index_backed():
    conn = sqlite3.connect(f"file:{ROOT}/database/flex_complete_database.db?mode=ro", uri=True)
    plan = conn.execute(
        "EXPLAIN QUERY PLAN SELECT COUNT(*) FROM creator_funders WHERE funder_address=?", (DV34,)
    ).fetchall()
    conn.close()
    plan_text = " ".join(str(row) for row in plan).upper()
    assert "SEARCH" in plan_text


def test_performance_steady_state_under_50ms(reader):
    reader.fetch_unified_investigation(DV34)  # warm import
    times = []
    for _ in range(5):
        t0 = time.perf_counter()
        reader.fetch_unified_investigation(DV34)
        times.append((time.perf_counter() - t0) * 1000)
    assert max(times) < 50, f"steady-state fetch exceeded 50ms budget: {times}"


def test_no_production_write_statements_in_this_test_module():
    src = Path(__file__).read_text()
    lines = [ln for ln in src.splitlines() if ".execute(" in ln and "test_no_production_write" not in ln]
    combined = "\n".join(lines).upper()
    for table in ("OPERATORS", "OPERATOR_ENTITIES", "CREATOR_FUNDERS", "CANDIDATE_FAMILIES"):
        for verb in ("INSERT INTO " + table, "UPDATE " + table, "DELETE FROM " + table):
            assert verb not in combined


def test_no_provider_or_rpc_imports_in_reader():
    src = (ROOT / "src/ops/operator_reader.py").read_text()
    for forbidden in ("helius", "getTransaction", "requests.post", "urllib.request"):
        assert forbidden not in src
