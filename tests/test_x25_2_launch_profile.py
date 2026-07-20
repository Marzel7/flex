"""X25.2 — Launch Profile implementation in Discovery.

Behavioral tests proving:
- PROVISIONED / OBSERVED_ONLY are derived correctly and read-only from
  src.discovery.service.DiscoveryService._launch_profile / _entity.
- The removed WATCHTOKEN label never appears in visible Discovery output.
- Launch Profile coexists independently with Funding Lineage (treasury reuse),
  Infrastructure Attribution (KNOWN_RELAY_REACHED), Detection Provenance, and
  Canonical Operator identity — none of those axes are altered by this sprint.
"""
from __future__ import annotations

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
"""


@pytest.fixture()
def db_factory():
    paths = []

    def _make(extra_sql: str = "") -> str:
        fd, path = tempfile.mkstemp(suffix="_x25_2.db")
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


def _service(db_path):
    from src.discovery.service import DiscoveryService
    return DiscoveryService(db_path, db_path)


# ---------------------------------------------------------------------------
# Backend: launch_profile derivation (additive, read-only)
# ---------------------------------------------------------------------------

def test_verified_wrap_close_launch_renders_provisioned(db_factory):
    db = db_factory("""
    INSERT INTO wt_watchtower_launches VALUES
      ('MINT','CREATOR','CREATETX',140,'TREASURY','SUBPROV','WRAPTX',2,
       'WSOL_WRAP_CLOSE','CLOSE_ACCOUNT_DESTINATION','STRICT',141);
    """)
    data = _service(db).resolve("MINT", "token")
    lp = data["launch_profile"]
    assert lp["classification"] == "PROVISIONED"
    assert "WSOL_WRAP_CLOSE".lower().replace("_", " ") in lp["reason"].lower()
    assert lp["facts"]["funding_mechanism"] == "WSOL_WRAP_CLOSE"
    assert lp["facts"]["birth_to_launch_seconds"] == 2


def test_verified_seeded_close_launch_renders_provisioned(db_factory):
    db = db_factory("""
    INSERT INTO wt_watchtower_launches VALUES
      ('MINT','CREATOR','CREATETX',140,'TREASURY','SUBPROV','WRAPTX',1,
       'SEEDED_ACCOUNT_CLOSE','CLOSE_ACCOUNT_DESTINATION','STRICT',141);
    """)
    data = _service(db).resolve("MINT", "token")
    lp = data["launch_profile"]
    assert lp["classification"] == "PROVISIONED"
    assert lp["facts"]["funding_mechanism"] == "SEEDED_ACCOUNT_CLOSE"


def test_walkback_only_launch_renders_observed_only(db_factory):
    """No wt_watchtower_launches row at all — only reachable via attribution outcome."""
    db = db_factory("""
    INSERT INTO wt_attribution_outcomes VALUES
      ('MINT','KNOWN_RELAY_REACHED','Attribution boundary reached.','RELAYADDR','AUTOMATION',
       'HIGH','{}',NULL,0,0,150,NULL,150);
    """)
    data = _service(db).resolve("MINT", "token")
    lp = data["launch_profile"]
    assert lp["classification"] == "OBSERVED_ONLY"
    assert "No verified provisioning session was recorded" in lp["reason"]
    assert "reconstructed retrospectively" in lp["reason"]
    assert lp["facts"] == {}


def test_missing_subprov_wallet_degrades_to_observed_only(db_factory):
    """A launch row exists but lacks subprov_wallet — must not be misclassified
    PROVISIONED merely because SOME row exists."""
    db = db_factory("""
    INSERT INTO wt_watchtower_launches VALUES
      ('MINT','CREATOR','CREATETX',140,'TREASURY',NULL,NULL,NULL,
       NULL,NULL,'STRICT',141);
    """)
    data = _service(db).resolve("MINT", "token")
    assert data["launch_profile"]["classification"] == "OBSERVED_ONLY"


def test_unrecognised_funding_mechanism_degrades_to_observed_only(db_factory):
    db = db_factory("""
    INSERT INTO wt_watchtower_launches VALUES
      ('MINT','CREATOR','CREATETX',140,'TREASURY','SUBPROV','WRAPTX',2,
       'SOME_OTHER_MECHANISM','CLOSE_ACCOUNT_DESTINATION','STRICT',141);
    """)
    data = _service(db).resolve("MINT", "token")
    assert data["launch_profile"]["classification"] == "OBSERVED_ONLY"


# ---------------------------------------------------------------------------
# PROVISIONED coexists independently with other axes
# ---------------------------------------------------------------------------

def test_provisioned_coexists_with_known_relay_reached(db_factory):
    db = db_factory("""
    INSERT INTO wt_watchtower_launches VALUES
      ('MINT','CREATOR','CREATETX',140,'TREASURY','SUBPROV','WRAPTX',2,
       'WSOL_WRAP_CLOSE','CLOSE_ACCOUNT_DESTINATION','STRICT',141);
    INSERT INTO wt_attribution_outcomes VALUES
      ('MINT','KNOWN_RELAY_REACHED','Attribution boundary reached.','RELAYADDR','AUTOMATION',
       'HIGH','{}',NULL,0,0,150,NULL,150);
    """)
    data = _service(db).resolve("MINT", "token")
    assert data["launch_profile"]["classification"] == "PROVISIONED"
    assert data["attribution_outcome"]["outcome_type"] == "KNOWN_RELAY_REACHED"


def test_observed_only_coexists_with_canonical_operator(db_factory):
    """OBSERVED_ONLY must not preclude a genuinely-established canonical operator."""
    from src.ops.watchtower_alignment import WATCHTOWER_OPERATOR_ID
    db = db_factory(f"""
    INSERT INTO wt_confirmed_treasuries VALUES
      ('TREASURY','HIGH','walkback',24.2,8,110);
    INSERT INTO operators VALUES
      ('{WATCHTOWER_OPERATOR_ID}','CONFIRMED','CERTAIN',100,170,'Known operation','REVIEWED','WATCHTOWER',90,170);
    INSERT INTO operator_entities VALUES
      ('{WATCHTOWER_OPERATOR_ID}','TREASURY','TREASURY','HIGH',2,100,170,165);
    INSERT INTO watchtower_token_attribution VALUES
      ('MINT','CREATOR',95,'STRONG','[]','TREASURY','SUBPROV','CONFIRMED',160);
    """)
    data = _service(db).resolve("MINT", "token")
    assert data["launch_profile"]["classification"] == "OBSERVED_ONLY"
    assert data["canonical_identity"] is not None
    assert data["canonical_identity"]["operator_name"] == "WATCHTOWER"


def test_provisioned_coexists_with_treasury_reuse(db_factory):
    """Funding Lineage (treasury launch count) is independent of Launch Profile;
    PROVISIONED launches under a treasury that has funded multiple launches."""
    db = db_factory("""
    INSERT INTO wt_watchtower_launches VALUES
      ('MINT','CREATOR','CREATETX',140,'TREASURY','SUBPROV','WRAPTX',2,
       'WSOL_WRAP_CLOSE','CLOSE_ACCOUNT_DESTINATION','STRICT',141);
    INSERT INTO wt_watchtower_launches VALUES
      ('MINT2','CREATOR2','CREATETX2',100,'TREASURY','SUBPROV2','WRAPTX2',3,
       'WSOL_WRAP_CLOSE','CLOSE_ACCOUNT_DESTINATION','STRICT',101);
    """)
    conn = sqlite3.connect(db)
    treasury_launch_count = conn.execute(
        "SELECT COUNT(*) FROM wt_watchtower_launches WHERE treasury_wallet='TREASURY'"
    ).fetchone()[0]
    conn.close()
    assert treasury_launch_count == 2
    data = _service(db).resolve("MINT", "token")
    assert data["launch_profile"]["classification"] == "PROVISIONED"


# ---------------------------------------------------------------------------
# Detection Provenance / Infrastructure Attribution unchanged by this sprint
# ---------------------------------------------------------------------------

def test_detection_provenance_wording_unchanged():
    """X25.6 replaced the operator-specific 'WATCHTOWER-tracked operation'
    wording with operator-neutral wording; X25.7 replaced the process-centric
    framing with pure outcome wording. The classification key remains
    unchanged throughout."""
    assert "WALKBACK_RECOVERED" in HTML
    assert "A complete funding lineage was established for this launch after the fact" in HTML


def test_infrastructure_attribution_wording_unchanged():
    assert "KNOWN_RELAY_REACHED" in HTML
    assert "Funding Traced to Known Infrastructure" in HTML


def test_missing_launch_profile_degrades_honestly_without_inventing_one(db_factory):
    """Non-token entities (e.g. a creator lookup) must not fabricate a
    launch_profile — it is only computed for subject_type == 'token'."""
    db = db_factory("""
    INSERT INTO wt_watchtower_launches VALUES
      ('MINT','CREATOR','CREATETX',140,'TREASURY','SUBPROV','WRAPTX',2,
       'WSOL_WRAP_CLOSE','CLOSE_ACCOUNT_DESTINATION','STRICT',141);
    """)
    data = _service(db).resolve("CREATOR", "creator")
    assert data["launch_profile"] is None


def test_service_does_not_mutate_database_with_launch_profile(db_factory):
    import hashlib
    db = db_factory("""
    INSERT INTO wt_watchtower_launches VALUES
      ('MINT','CREATOR','CREATETX',140,'TREASURY','SUBPROV','WRAPTX',2,
       'WSOL_WRAP_CLOSE','CLOSE_ACCOUNT_DESTINATION','STRICT',141);
    """)
    before = hashlib.sha256(open(db, "rb").read()).digest()
    _service(db).resolve("MINT", "token")
    after = hashlib.sha256(open(db, "rb").read()).digest()
    assert after == before


# ---------------------------------------------------------------------------
# Presentation layer: WATCHTOKEN removed, Launch + badge rendered instead
# ---------------------------------------------------------------------------

def test_watchtoken_label_absent_from_visible_output():
    """WATCHTOKEN must never appear as a rendered role/label string. The only
    permitted occurrence is inside code comments explaining its removal."""
    for line in HTML.splitlines():
        stripped = line.strip()
        if stripped.startswith("//"):
            continue
        assert "WATCHTOKEN" not in line, f"WATCHTOKEN leaked into non-comment line: {line!r}"


def test_root_node_role_is_launch_not_watchtoken():
    assert "role:'Launch'" in HTML
    assert "role:'WATCHTOKEN'" not in HTML


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


@pytestmark_node
def test_walkback_lead_node_renders_provisioned_badge_beneath_launch_role():
    import json
    js = _script()
    snippet = _extract_var("ROLE_ICON", js) + "\n" + "\n".join(
        _extract_function(fn, js) for fn in ("esc", "abbr", "href", "walkbackLeadNodes")
    )
    launch_profile = {"classification": "PROVISIONED", "reason": "x", "facts": {}}
    script = (
        snippet
        + "\nconsole.log(JSON.stringify(walkbackLeadNodes("
        + json.dumps({"type": "token", "id": "MINT111"})
        + ", null, null, true, " + json.dumps(launch_profile) + ")));"
    )
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=15, check=True)
    html = json.loads(result.stdout)
    assert ">Launch<" in html
    assert "PROVISIONED" in html
    assert "WATCHTOKEN" not in html
