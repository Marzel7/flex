"""X25.4 — Operation Identity Resolver & Discovery Integration.

Behavioral tests over src.ops.operation_identity.build_operations() /
operation_for_treasury(), the additive Discovery API field, and the
Discovery UI rendering. Every test uses an isolated on-disk SQLite fixture
(never the live database) so results are fully deterministic.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

HTML = (ROOT / "templates/discovery.html").read_text()

SCHEMA = """
CREATE TABLE wt_confirmed_treasuries (treasury TEXT PRIMARY KEY, confidence TEXT);
CREATE TABLE wt_treasury_funders (funder TEXT, treasury TEXT, fund_count INTEGER,
 total_sol REAL, max_sol REAL, first_seen INTEGER, last_seen INTEGER, is_subprov_sweep INTEGER);
CREATE TABLE wt_watchtower_launches (mint TEXT, creator_wallet TEXT, treasury_wallet TEXT,
 subprov_wallet TEXT, create_time INTEGER, funding_mechanism TEXT);
"""


@pytest.fixture()
def db_factory():
    paths = []

    def _make(extra_sql: str = "") -> str:
        fd, path = tempfile.mkstemp(suffix="_x25_4.db")
        os.close(fd)
        conn = sqlite3.connect(path)
        conn.executescript(SCHEMA)
        if extra_sql:
            conn.executescript(extra_sql)
        conn.commit()
        conn.close()
        paths.append(path)
        return path

    yield _make
    for p in paths:
        os.unlink(p)


def _build(db_path):
    from src.ops.operation_identity import build_operations
    return build_operations(db_path)


# ---------------------------------------------------------------------------
# Phase 10 — core cases
# ---------------------------------------------------------------------------

def test_single_treasury_no_edges(db_factory):
    db = db_factory("""
    INSERT INTO wt_confirmed_treasuries VALUES ('T1','HIGH');
    INSERT INTO wt_watchtower_launches VALUES ('M1','C1','T1','S1',100,'WSOL_WRAP_CLOSE');
    """)
    result = _build(db)
    assert len(result["operations"]) == 1
    op = next(iter(result["operations"].values()))
    assert len(op["treasuries"]) == 1
    assert op["treasuries"][0]["wallet"] == "T1"
    assert op["launch_count"] == 1
    assert op["identity_basis"] == "TREASURY_FUNDING_MESH"
    assert op["confidence"] == "CONFIRMED"


def test_two_treasuries_linked_before_first_launch(db_factory):
    db = db_factory("""
    INSERT INTO wt_confirmed_treasuries VALUES ('A','HIGH'),('B','HIGH');
    INSERT INTO wt_watchtower_launches VALUES ('M1','C1','B','S1',500,'WSOL_WRAP_CLOSE');
    INSERT INTO wt_treasury_funders VALUES ('A','B',1,100.0,100.0,50,50,0);
    """)
    result = _build(db)
    assert len(result["operations"]) == 1
    op = next(iter(result["operations"].values()))
    assert {t["wallet"] for t in op["treasuries"]} == {"A", "B"}
    roles = {t["wallet"]: t["role"] for t in op["treasuries"]}
    assert roles["A"] == "ROOT"
    assert roles["B"] == "MEMBER"


def test_three_treasury_transitive_mesh(db_factory):
    """A funds B before B's first launch; B funds C before C's first launch —
    A, B, C must all resolve to one operation via transitive closure."""
    db = db_factory("""
    INSERT INTO wt_confirmed_treasuries VALUES ('A','HIGH'),('B','HIGH'),('C','HIGH');
    INSERT INTO wt_watchtower_launches VALUES
      ('M1','C1','B',NULL,500,'WSOL_WRAP_CLOSE'),
      ('M2','C2','C',NULL,700,'WSOL_WRAP_CLOSE');
    INSERT INTO wt_treasury_funders VALUES
      ('A','B',1,100.0,100.0,50,50,0),
      ('B','C',1,200.0,200.0,300,300,0);
    """)
    result = _build(db)
    assert len(result["operations"]) == 1
    op = next(iter(result["operations"].values()))
    assert {t["wallet"] for t in op["treasuries"]} == {"A", "B", "C"}
    assert op["launch_count"] == 2


def test_multiple_parents_mesh(db_factory):
    """Two different confirmed treasuries both fund the same treasury before
    its first launch — all three must be one component."""
    db = db_factory("""
    INSERT INTO wt_confirmed_treasuries VALUES ('P1','HIGH'),('P2','HIGH'),('X','HIGH');
    INSERT INTO wt_watchtower_launches VALUES ('M1','C1','X',NULL,1000,'WSOL_WRAP_CLOSE');
    INSERT INTO wt_treasury_funders VALUES
      ('P1','X',1,100.0,100.0,50,50,0),
      ('P2','X',1,200.0,200.0,60,60,0);
    """)
    result = _build(db)
    assert len(result["operations"]) == 1
    op = next(iter(result["operations"].values()))
    assert {t["wallet"] for t in op["treasuries"]} == {"P1", "P2", "X"}
    assert len(op["funding_edges"]) == 2


def test_funding_cycle_resolves_to_one_operation(db_factory):
    """A funds B (before B's first launch), B funds A (before A's first
    launch) — a cycle must still resolve to exactly one component, no crash,
    no fabricated unique root."""
    db = db_factory("""
    INSERT INTO wt_confirmed_treasuries VALUES ('A','HIGH'),('B','HIGH');
    INSERT INTO wt_watchtower_launches VALUES
      ('M1','C1','A',NULL,1000,'WSOL_WRAP_CLOSE'),
      ('M2','C2','B',NULL,2000,'WSOL_WRAP_CLOSE');
    INSERT INTO wt_treasury_funders VALUES
      ('B','A',1,50.0,50.0,10,10,0),
      ('A','B',1,60.0,60.0,20,20,0);
    """)
    result = _build(db)
    assert len(result["operations"]) == 1
    op = next(iter(result["operations"].values()))
    assert {t["wallet"] for t in op["treasuries"]} == {"A", "B"}
    # cycle: every member has an incoming qualifying edge, so no member is
    # uniquely root -- both should be reported as MEMBER.
    roles = {t["role"] for t in op["treasuries"]}
    assert roles == {"MEMBER"}


def test_later_transfer_does_not_merge(db_factory):
    """Funding occurs AFTER the destination treasury's first launch — must
    not qualify as an edge, so the two treasuries do NOT merge into one
    operation (A has no launches of its own, so only B forms an operation;
    the point under test is that A and B are never linked together)."""
    db = db_factory("""
    INSERT INTO wt_confirmed_treasuries VALUES ('A','HIGH'),('B','HIGH');
    INSERT INTO wt_watchtower_launches VALUES
      ('M1','C1','B',NULL,100,'WSOL_WRAP_CLOSE'),
      ('M2','C2','A',NULL,50,'WSOL_WRAP_CLOSE');
    INSERT INTO wt_treasury_funders VALUES ('A','B',1,100.0,100.0,500,500,0);
    """)
    result = _build(db)
    assert len(result["operations"]) == 2
    rejected_reasons = {e["reason"] for e in result["rejected_edges"]}
    assert "FUNDING_NOT_BEFORE_FIRST_LAUNCH" in rejected_reasons


def test_missing_first_launch_time_does_not_merge(db_factory):
    """Destination treasury has no launch with a known create_time at all —
    the precedence condition cannot be established, so the edge is rejected
    as insufficient evidence, never guessed."""
    db = db_factory("""
    INSERT INTO wt_confirmed_treasuries VALUES ('A','HIGH'),('B','HIGH');
    INSERT INTO wt_treasury_funders VALUES ('A','B',1,100.0,100.0,500,500,0);
    """)
    result = _build(db)
    assert len(result["operations"]) == 0  # neither treasury has any launch or qualifying edge
    rejected_reasons = {e["reason"] for e in result["rejected_edges"]}
    assert "MISSING_FIRST_LAUNCH_TIME" in rejected_reasons


def test_unconfirmed_treasury_does_not_expand_mesh(db_factory):
    """The funder is NOT in wt_confirmed_treasuries — must not be treated as
    a treasury-to-treasury edge at all, and must not appear as an operation
    member."""
    db = db_factory("""
    INSERT INTO wt_confirmed_treasuries VALUES ('B','HIGH');
    INSERT INTO wt_watchtower_launches VALUES ('M1','C1','B',NULL,500,'WSOL_WRAP_CLOSE');
    INSERT INTO wt_treasury_funders VALUES ('UNCONFIRMED_WALLET','B',1,999.0,999.0,10,10,0);
    """)
    result = _build(db)
    assert len(result["operations"]) == 1
    op = next(iter(result["operations"].values()))
    assert {t["wallet"] for t in op["treasuries"]} == {"B"}
    assert "UNCONFIRMED_WALLET" not in result["treasury_to_operation"]


def test_subprov_sweep_edge_rejected(db_factory):
    db = db_factory("""
    INSERT INTO wt_confirmed_treasuries VALUES ('A','HIGH'),('B','HIGH');
    INSERT INTO wt_watchtower_launches VALUES
      ('M1','C1','B',NULL,500,'WSOL_WRAP_CLOSE'),
      ('M2','C2','A',NULL,50,'WSOL_WRAP_CLOSE');
    INSERT INTO wt_treasury_funders VALUES ('A','B',1,100.0,100.0,10,10,1);
    """)
    result = _build(db)
    assert len(result["operations"]) == 2
    rejected_reasons = {e["reason"] for e in result["rejected_edges"]}
    assert "SUBPROV_SWEEP_ARTIFACT" in rejected_reasons


def test_vanity_prefix_collision_never_merges(db_factory):
    """Two treasuries sharing a long vanity prefix but with NO qualifying
    funding edge between them must remain separate operations."""
    db = db_factory("""
    INSERT INTO wt_confirmed_treasuries VALUES
      ('43PKjr22AFXtSXqEf5fABjnP3eHEHm2j5hT8VPS5n7vh','HIGH'),
      ('43PKjr22AFXtCMmLtQ1wxYojnjqEB86iFKK5qUYo3y3D','HIGH');
    INSERT INTO wt_watchtower_launches VALUES
      ('M1','C1','43PKjr22AFXtSXqEf5fABjnP3eHEHm2j5hT8VPS5n7vh',NULL,100,'WSOL_WRAP_CLOSE'),
      ('M2','C2','43PKjr22AFXtCMmLtQ1wxYojnjqEB86iFKK5qUYo3y3D',NULL,200,'WSOL_WRAP_CLOSE');
    """)
    result = _build(db)
    assert len(result["operations"]) == 2


# ---------------------------------------------------------------------------
# Phase 3 — deterministic operation IDs
# ---------------------------------------------------------------------------

def test_operation_id_stable_across_row_order():
    from src.ops.operation_identity import operation_id_for
    assert operation_id_for(["b", "a", "c"]) == operation_id_for(["c", "a", "b"]) == operation_id_for(["a", "b", "c"])


def test_operation_id_is_sha256_derived_not_random():
    from src.ops.operation_identity import operation_id_for
    expected = "op_" + hashlib.sha256("a|b".encode("utf-8")).hexdigest()[:16]
    assert operation_id_for(["b", "a"]) == expected


def test_operation_id_stable_across_reconstruction(db_factory):
    """Same underlying data, rebuilt from scratch (simulating a process
    restart) must produce the identical operation_id."""
    db = db_factory("""
    INSERT INTO wt_confirmed_treasuries VALUES ('A','HIGH'),('B','HIGH');
    INSERT INTO wt_watchtower_launches VALUES ('M1','C1','B',NULL,500,'WSOL_WRAP_CLOSE');
    INSERT INTO wt_treasury_funders VALUES ('A','B',1,100.0,100.0,50,50,0);
    """)
    id1 = next(iter(_build(db)["operations"].keys()))
    id2 = next(iter(_build(db)["operations"].keys()))
    assert id1 == id2


def test_display_name_never_uses_wallet_prefix():
    from src.ops.operation_identity import operation_id_for, display_name_for
    op_id = operation_id_for(["9hGcxVHFajR4xMZVUBQBwbxN2J3tt1aXE5KrUB87EZk4"])
    label = display_name_for(op_id)
    assert "9hGcx" not in label
    assert label.startswith("Operation ")


# ---------------------------------------------------------------------------
# Launch assignment / Discovery integration
# ---------------------------------------------------------------------------

def test_launch_assigned_to_correct_mesh(db_factory):
    db = db_factory("""
    INSERT INTO wt_confirmed_treasuries VALUES ('A','HIGH'),('B','HIGH'),('Z','HIGH');
    INSERT INTO wt_watchtower_launches VALUES
      ('M1','C1','B',NULL,500,'WSOL_WRAP_CLOSE'),
      ('M2','C2','Z',NULL,600,'WSOL_WRAP_CLOSE');
    INSERT INTO wt_treasury_funders VALUES ('A','B',1,100.0,100.0,50,50,0);
    """)
    from src.ops.operation_identity import operation_for_treasury
    op_b = operation_for_treasury("B", db)
    op_z = operation_for_treasury("Z", db)
    assert op_b["operation_id"] != op_z["operation_id"]
    assert {t["wallet"] for t in op_b["treasuries"]} == {"A", "B"}
    assert {t["wallet"] for t in op_z["treasuries"]} == {"Z"}


def test_no_confirmed_treasury_returns_no_operation_identity(db_factory):
    from src.ops.operation_identity import operation_for_treasury
    db = db_factory()
    assert operation_for_treasury(None, db) is None
    assert operation_for_treasury("SOME_UNCONFIRMED_WALLET", db) is None


def test_no_database_mutation(db_factory):
    db = db_factory("""
    INSERT INTO wt_confirmed_treasuries VALUES ('A','HIGH'),('B','HIGH');
    INSERT INTO wt_watchtower_launches VALUES ('M1','C1','B',NULL,500,'WSOL_WRAP_CLOSE');
    INSERT INTO wt_treasury_funders VALUES ('A','B',1,100.0,100.0,50,50,0);
    """)
    before = hashlib.sha256(open(db, "rb").read()).digest()
    _build(db)
    after = hashlib.sha256(open(db, "rb").read()).digest()
    assert before == after


# ---------------------------------------------------------------------------
# Discovery service integration (additive field, independence)
# ---------------------------------------------------------------------------

DISCOVERY_SCHEMA = """
CREATE TABLE migrated_tokens (mint TEXT PRIMARY KEY, creator TEXT, migration_tx TEXT,
 migration_time INTEGER, stored_at INTEGER);
CREATE TABLE wt_watchtower_launches (mint TEXT, creator_wallet TEXT, create_signature TEXT,
 create_time INTEGER, treasury_wallet TEXT, subprov_wallet TEXT, wrap_close_signature TEXT,
 birth_to_launch_seconds INTEGER, funding_mechanism TEXT, creator_extraction_method TEXT,
 confidence TEXT, recorded_at INTEGER);
CREATE TABLE wt_wrap_close_candidates (creator TEXT PRIMARY KEY, funding_mechanism TEXT,
 creator_extraction_method TEXT, subprov_wallet TEXT, lineage_source_treasury TEXT,
 base_amount_sol REAL, tx_signature TEXT, funded_at INTEGER, confidence TEXT, detected_at INTEGER);
CREATE TABLE wt_discovered_subprovs (subprov TEXT PRIMARY KEY, creator_count INTEGER,
 treasury TEXT, immediate_funder TEXT, confidence REAL, state TEXT, wrap_close_count INTEGER,
 funding_mechanism TEXT, first_seen INTEGER, last_seen INTEGER);
CREATE TABLE wt_confirmed_treasuries (treasury TEXT PRIMARY KEY, confidence TEXT, method TEXT,
 out_sol REAL, recipients INTEGER, confirmed_at INTEGER);
CREATE TABLE wt_treasury_review (treasury TEXT PRIMARY KEY, status TEXT, confidence TEXT,
 detected_at INTEGER, reviewed_at INTEGER, detected_via TEXT, recipients INTEGER, out_sol REAL);
CREATE TABLE watchtower_token_attribution (mint TEXT PRIMARY KEY, creator TEXT, score REAL,
 tier TEXT, reasons_json TEXT, matched_treasury TEXT, matched_subprov TEXT,
 reviewed_status TEXT, scored_at INTEGER);
CREATE TABLE wt_token_lifecycle (mint TEXT PRIMARY KEY, treasury TEXT, subprov TEXT, creator TEXT,
 lifecycle_state TEXT, launched_at INTEGER, migrated_at INTEGER, recycled_at INTEGER,
 operation_uuid TEXT, updated_at INTEGER);
CREATE TABLE wt_walkback_queue (mint TEXT PRIMARY KEY, creator TEXT, subprov TEXT, treasury TEXT,
 status TEXT, completed_at INTEGER, intelligence_outcome TEXT);
CREATE TABLE wt_ops_v2_treasury_resolution (operation_uuid TEXT PRIMARY KEY,
 current_assigned_treasury TEXT, positional_root_candidate TEXT, confidence REAL,
 evidence_path TEXT, status TEXT, reason TEXT, resolved_at INTEGER);
CREATE TABLE operators (operator_id TEXT PRIMARY KEY, status TEXT, confidence TEXT,
 first_seen INTEGER, last_seen INTEGER, summary TEXT, review_state TEXT, display_name TEXT,
 created_at INTEGER, updated_at INTEGER);
CREATE TABLE operator_entities (operator_id TEXT, entity_address TEXT, entity_type TEXT,
 confidence TEXT, evidence_count INTEGER, first_seen INTEGER, last_seen INTEGER, added_at INTEGER);
CREATE TABLE operator_evidence (evidence_id TEXT PRIMARY KEY, operator_id TEXT, evidence_type TEXT,
 category TEXT, source TEXT, entity_a TEXT, entity_b TEXT, weight REAL, detail_json TEXT,
 recorded_at INTEGER);
CREATE TABLE operator_reviews (review_id TEXT PRIMARY KEY, operator_id TEXT, decision TEXT,
 reviewer TEXT, reviewed_at INTEGER, notes TEXT, superseded_by TEXT);
CREATE TABLE wt_attribution_outcomes (mint TEXT PRIMARY KEY, outcome_type TEXT, stop_reason TEXT,
 terminal_entity TEXT, terminal_entity_type TEXT, confidence TEXT, evidence_json TEXT,
 operator_id TEXT, should_seed_emerging_operator INTEGER, should_retry INTEGER,
 completed_at INTEGER, source_queue_updated_at INTEGER, materialized_at INTEGER);
CREATE TABLE wt_treasury_funders (funder TEXT, treasury TEXT, fund_count INTEGER,
 total_sol REAL, max_sol REAL, first_seen INTEGER, last_seen INTEGER, is_subprov_sweep INTEGER);
"""


@pytest.fixture()
def discovery_db():
    fd, path = tempfile.mkstemp(suffix="_x25_4_discovery.db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.executescript(DISCOVERY_SCHEMA)
    conn.commit()
    conn.close()
    yield path
    os.unlink(path)


def _service(db_path):
    from src.discovery.service import DiscoveryService
    return DiscoveryService(db_path, db_path)


def test_discovery_api_field_additive_and_populated(discovery_db):
    conn = sqlite3.connect(discovery_db)
    conn.executescript("""
    INSERT INTO wt_confirmed_treasuries VALUES ('TREASURY','HIGH','walkback',24.2,8,110);
    INSERT INTO wt_watchtower_launches VALUES
      ('MINT','CREATOR','CREATETX',140,'TREASURY','SUBPROV','WRAPTX',2,
       'WSOL_WRAP_CLOSE','CLOSE_ACCOUNT_DESTINATION','STRICT',141);
    """)
    conn.commit()
    conn.close()
    data = _service(discovery_db).resolve("MINT", "token")
    oi = data["operation_identity"]
    assert oi is not None
    assert oi["treasury_count"] == 1
    assert oi["launch_count"] == 1
    assert oi["subject_treasury"] == "TREASURY"
    assert oi["member_treasuries"] == ["TREASURY"]


def test_discovery_api_field_none_when_no_confirmed_treasury(discovery_db):
    conn = sqlite3.connect(discovery_db)
    conn.executescript("""
    INSERT INTO wt_watchtower_launches VALUES
      ('MINT','CREATOR','CREATETX',140,NULL,'SUBPROV','WRAPTX',2,
       'WSOL_WRAP_CLOSE','CLOSE_ACCOUNT_DESTINATION','STRICT',141);
    """)
    conn.commit()
    conn.close()
    data = _service(discovery_db).resolve("MINT", "token")
    assert data["operation_identity"] is None


def test_canonical_operator_independent_of_operation_identity(discovery_db):
    """A launch with a confirmed operation mesh but NO canonical operator
    must show operation_identity populated and canonical_identity null."""
    conn = sqlite3.connect(discovery_db)
    conn.executescript("""
    INSERT INTO wt_confirmed_treasuries VALUES ('TREASURY','HIGH','walkback',24.2,8,110);
    INSERT INTO wt_watchtower_launches VALUES
      ('MINT','CREATOR','CREATETX',140,'TREASURY','SUBPROV','WRAPTX',2,
       'WSOL_WRAP_CLOSE','CLOSE_ACCOUNT_DESTINATION','STRICT',141);
    """)
    conn.commit()
    conn.close()
    data = _service(discovery_db).resolve("MINT", "token")
    assert data["operation_identity"] is not None
    assert data["canonical_identity"] is None


def test_attribution_outcome_independent_of_operation_identity(discovery_db):
    conn = sqlite3.connect(discovery_db)
    conn.executescript("""
    INSERT INTO wt_confirmed_treasuries VALUES ('TREASURY','HIGH','walkback',24.2,8,110);
    INSERT INTO wt_watchtower_launches VALUES
      ('MINT','CREATOR','CREATETX',140,'TREASURY','SUBPROV','WRAPTX',2,
       'WSOL_WRAP_CLOSE','CLOSE_ACCOUNT_DESTINATION','STRICT',141);
    INSERT INTO wt_attribution_outcomes VALUES
      ('MINT','KNOWN_RELAY_REACHED','Attribution boundary reached.','RELAYADDR','AUTOMATION',
       'HIGH','{}',NULL,0,0,150,NULL,150);
    """)
    conn.commit()
    conn.close()
    data = _service(discovery_db).resolve("MINT", "token")
    assert data["operation_identity"] is not None
    assert data["attribution_outcome"]["outcome_type"] == "KNOWN_RELAY_REACHED"


def test_detection_provenance_independent_of_operation_identity(discovery_db):
    conn = sqlite3.connect(discovery_db)
    conn.executescript("""
    INSERT INTO wt_confirmed_treasuries VALUES ('TREASURY','HIGH','walkback',24.2,8,110);
    INSERT INTO wt_watchtower_launches VALUES
      ('MINT','CREATOR','CREATETX',140,'TREASURY','SUBPROV','WRAPTX',2,
       'WSOL_WRAP_CLOSE','CLOSE_ACCOUNT_DESTINATION','STRICT',141);
    """)
    conn.commit()
    conn.close()
    data = _service(discovery_db).resolve("MINT", "token")
    assert data["operation_identity"] is not None
    # detection_reconciliation may be None (no wt_provisioning_sessions table
    # in this fixture) but must not error or be conflated with operation_identity.
    assert "detection_reconciliation" in data


def test_service_does_not_mutate_database_with_operation_identity(discovery_db):
    conn = sqlite3.connect(discovery_db)
    conn.executescript("""
    INSERT INTO wt_confirmed_treasuries VALUES ('TREASURY','HIGH','walkback',24.2,8,110);
    INSERT INTO wt_watchtower_launches VALUES
      ('MINT','CREATOR','CREATETX',140,'TREASURY','SUBPROV','WRAPTX',2,
       'WSOL_WRAP_CLOSE','CLOSE_ACCOUNT_DESTINATION','STRICT',141);
    """)
    conn.commit()
    conn.close()
    before = hashlib.sha256(open(discovery_db, "rb").read()).digest()
    _service(discovery_db).resolve("MINT", "token")
    after = hashlib.sha256(open(discovery_db, "rb").read()).digest()
    assert before == after


# ---------------------------------------------------------------------------
# Discovery UI rendering
# ---------------------------------------------------------------------------

pytestmark_node = pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")


def _extract_function(name: str, js: str) -> str:
    idx = js.index(f"function {name}(")
    depth = 0
    i = js.index("{", idx)
    while True:
        if js[i] == "{":
            depth += 1
        elif js[i] == "}":
            depth -= 1
        if depth == 0:
            break
        i += 1
    return js[idx : i + 1]


def _extract_var(name: str, js: str) -> str:
    try:
        idx = js.index(f"var {name} ")
    except ValueError:
        idx = js.index(f"var {name}=")
    end = js.index(";\n", idx)
    return js[idx : end + 1]


def _script() -> str:
    m = re.search(r"{% block scripts %}\s*<script>(.*)</script>\s*{% endblock %}", HTML, re.S)
    return m.group(1)


def _run(expr: str, snippet: str) -> str:
    script = snippet + "\nconsole.log(JSON.stringify(" + expr + "));"
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=15, check=True)
    return json.loads(result.stdout)


@pytestmark_node
def test_operation_identity_card_renders_multi_treasury_counts():
    js = _script()
    snippet = "\n".join([_extract_function(fn, js) for fn in ("esc", "abbr", "operationIdentity")])
    oi = {"operation_id": "op_abc123", "display_name": "Operation ABC123",
          "identity_basis": "TREASURY_FUNDING_MESH", "confidence": "CONFIRMED",
          "treasury_count": 3, "launch_count": 22, "subject_treasury": "9hGcxVHFajR4xMZVUBQBwbxN2J3tt1aXE5KrUB87EZk4",
          "member_treasuries": ["A", "B", "C"]}
    html = _run("operationIdentity(" + json.dumps(oi) + ")", snippet)
    assert "Operation ABC123" in html
    assert ">3<" in html
    assert ">22<" in html
    assert "Open Operation" in html
    assert "WATCHTOWER" not in html


@pytestmark_node
def test_operation_identity_card_single_treasury_wording():
    js = _script()
    snippet = "\n".join([_extract_function(fn, js) for fn in ("esc", "abbr", "operationIdentity")])
    oi = {"operation_id": "op_def456", "display_name": "Operation DEF456",
          "identity_basis": "TREASURY_FUNDING_MESH", "confidence": "CONFIRMED",
          "treasury_count": 1, "launch_count": 7, "subject_treasury": "SOLE_TREASURY",
          "member_treasuries": ["SOLE_TREASURY"]}
    html = _run("operationIdentity(" + json.dumps(oi) + ")", snippet)
    assert "Single confirmed treasury" in html
    assert ">7<" in html


@pytestmark_node
def test_operation_identity_card_absent_when_null():
    js = _script()
    snippet = "\n".join([_extract_function(fn, js) for fn in ("esc", "abbr", "operationIdentity")])
    assert _run("operationIdentity(null)", snippet) == ""


@pytestmark_node
def test_launch_summary_includes_operation_sentence_only_when_real():
    js = _script()
    snippet = "\n".join([
        _extract_function("esc", js), _extract_function("typedLabel", js), _extract_function("analystSummary", js),
    ])
    with_op = _run("analystSummary(" + json.dumps({
        "operation_identity": {"operation_id": "op_x", "display_name": "Operation X",
                               "treasury_count": 3, "launch_count": 22},
    }) + ")", snippet)
    assert "belongs to Operation X" in with_op
    assert "3-treasury funding mesh" in with_op
    assert "22 observed launches" in with_op

    without_op = _run("analystSummary(" + json.dumps({}) + ")", snippet)
    assert "belongs to" not in without_op
    assert "operation" not in without_op.lower()


@pytestmark_node
def test_launch_summary_single_treasury_wording():
    js = _script()
    snippet = "\n".join([
        _extract_function("esc", js), _extract_function("typedLabel", js), _extract_function("analystSummary", js),
    ])
    html = _run("analystSummary(" + json.dumps({
        "operation_identity": {"operation_id": "op_y", "display_name": "Operation Y",
                               "treasury_count": 1, "launch_count": 7},
    }) + ")", snippet)
    assert "single-treasury operation with 7 observed launches" in html
    assert "Operation Y" not in html  # single-treasury wording doesn't need the display name


def test_operation_identity_never_labelled_watchtower_unless_canonical():
    assert "if(canonicalIdentity && canonicalIdentity.operator_name){" in HTML
    op_fn_idx = HTML.index("function operationIdentity(")
    op_fn_end = HTML.index("\n  }", op_fn_idx)
    op_fn_body = HTML[op_fn_idx:op_fn_end]
    assert "WATCHTOWER" not in op_fn_body


def test_operation_identity_wired_into_render_pipeline():
    assert "var operationCard=operationIdentity(d.operation_identity);" in HTML
    assert "operationCard+operatorIdentity" in HTML


def test_operation_detail_route_function_name_does_not_collide():
    """Regression: an earlier, unrelated route already defines
    api_operation_detail(uuid) at /api/ops-v2/operation/<uuid> (singular) in
    the same module -- our new Phase 9 endpoint at /api/ops-v2/operations/
    (plural) must use a distinct Python function name so Flask does not
    register a colliding endpoint and misroute operation_id -> uuid."""
    routes_src = (ROOT / "src/core/operation_dashboard_routes.py").read_text()
    assert 'def api_operation_identity_detail(operation_id):' in routes_src
    assert routes_src.count('def api_operation_detail(') == 1  # only the pre-existing uuid-based route
