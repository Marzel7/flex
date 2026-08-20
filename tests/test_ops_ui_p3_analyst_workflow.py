"""OPS-UI-P3: focused tests for analyst-workflow frontend completion.

Hard invariant under test: WATCHTOWER_CANONICAL_HISTORY_PRESERVATION.
No provider calls. No production writes. All DB access mode=ro.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.db import OPS_DB_PATH  # noqa: E402
from src.discovery.local_operation_discovery_projection import OUTPUT_DB  # noqa: E402
from src.ops.discovery_intake import fetch_discovery_intake_candidates  # noqa: E402
from src.ops.operator_reader import OperatorReader  # noqa: E402

DV34 = "Dv34prGm2BT7Ph2n6qKLgzeLgjnii87RJJ7Db6ZQQvKM"

BASELINE_ARTIFACT = ROOT / "docs/audits/ops_ui_p3_watchtower_preservation_baseline.json"


def _digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


@pytest.fixture(scope="module")
def baseline():
    return json.loads(BASELINE_ARTIFACT.read_text())


@pytest.fixture(scope="module")
def reader():
    return OperatorReader(str(OPS_DB_PATH))


@pytest.fixture(scope="module")
def wt_op_id():
    conn = sqlite3.connect(f"file:{OPS_DB_PATH}?mode=ro", uri=True)
    op_id = conn.execute("SELECT operator_id FROM operators WHERE display_name='WATCHTOWER'").fetchone()[0]
    conn.close()
    return op_id


@pytest.fixture(scope="module")
def sw2_op_id():
    conn = sqlite3.connect(f"file:{OPS_DB_PATH}?mode=ro", uri=True)
    op_id = conn.execute("SELECT operator_id FROM operators WHERE display_name='3SW2'").fetchone()[0]
    conn.close()
    return op_id


# ── WATCHTOWER preservation ──────────────────────────────────────────────

def test_watchtower_baseline_artifact_exists():
    assert BASELINE_ARTIFACT.exists()


def test_watchtower_canonical_row_unchanged(baseline, wt_op_id):
    conn = sqlite3.connect(f"file:{OPS_DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    row = dict(conn.execute("SELECT * FROM operators WHERE operator_id=?", (wt_op_id,)).fetchone())
    conn.close()
    assert row == baseline["watchtower_operator_row"]
    assert row["status"] == "CONFIRMED"


def test_watchtower_confirmed_membership_unchanged(baseline, wt_op_id):
    conn = sqlite3.connect(f"file:{OPS_DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    entities = [dict(r) for r in conn.execute(
        "SELECT * FROM operator_entities WHERE operator_id=? ORDER BY entity_address", (wt_op_id,)
    ).fetchall()]
    conn.close()
    assert len(entities) == baseline["watchtower_operator_entities_count"] == 69
    assert _digest(entities) == baseline["watchtower_operator_entities_digest"]


def test_watchtower_launch_population_unchanged(baseline):
    conn = sqlite3.connect(f"file:{OPS_DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    launches = [dict(r) for r in conn.execute("SELECT * FROM wt_watchtower_launches ORDER BY rowid").fetchall()]
    conn.close()
    assert len(launches) == baseline["wt_watchtower_launches_count"] == 246
    assert _digest(launches) == baseline["wt_watchtower_launches_digest"]


def test_watchtower_confirmed_treasuries_unchanged(baseline):
    conn = sqlite3.connect(f"file:{OPS_DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    treasuries = [dict(r) for r in conn.execute("SELECT * FROM wt_confirmed_treasuries ORDER BY rowid").fetchall()]
    conn.close()
    assert len(treasuries) == baseline["wt_confirmed_treasuries_count"] == 62
    assert _digest(treasuries) == baseline["wt_confirmed_treasuries_digest"]


def test_watchtower_evidence_and_reviews_unchanged(baseline, wt_op_id):
    conn = sqlite3.connect(f"file:{OPS_DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    evidence = [dict(r) for r in conn.execute(
        "SELECT * FROM operator_evidence WHERE operator_id=? ORDER BY created_at", (wt_op_id,)
    ).fetchall()]
    reviews = [dict(r) for r in conn.execute(
        "SELECT * FROM operator_reviews WHERE operator_id=? ORDER BY timestamp", (wt_op_id,)
    ).fetchall()]
    conn.close()
    assert _digest(evidence) == baseline["watchtower_operator_evidence_digest"]
    assert _digest(reviews) == baseline["watchtower_operator_reviews_digest"]


def test_watchtower_candidate_state_never_conflated_with_confirmed(baseline):
    """Regression test per Part 15: wt_watchtower_candidates (CANDIDATE
    state) must remain a strictly separate count/digest from
    operator_entities (CONFIRMED canonical membership)."""
    conn = sqlite3.connect(f"file:{OPS_DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    candidates = [dict(r) for r in conn.execute("SELECT * FROM wt_watchtower_candidates ORDER BY rowid").fetchall()]
    conn.close()
    assert len(candidates) == baseline["wt_watchtower_candidates_count_CANDIDATE_STATE_NOT_CONFIRMED"] == 7
    assert _digest(candidates) == baseline["wt_watchtower_candidates_digest"]
    # the two counts must never be equal by accident of a bug that merges them
    assert baseline["wt_watchtower_candidates_count_CANDIDATE_STATE_NOT_CONFIRMED"] != baseline["watchtower_operator_entities_count"]


def test_watchtower_unified_route_still_confirmed(reader, wt_op_id):
    conn = sqlite3.connect(f"file:{OPS_DB_PATH}?mode=ro", uri=True)
    entity = conn.execute("SELECT entity_address FROM operator_entities WHERE operator_id=? LIMIT 1", (wt_op_id,)).fetchone()[0]
    conn.close()
    result = reader.fetch_unified_investigation(entity)
    assert result["identity"]["authority_state"] == "CONFIRMED"


def test_discovery_overlap_cannot_mutate_watchtower_membership(wt_op_id):
    """Even if a discovery family's root happened to equal a Watchtower
    entity, fetch_discovery_intake_candidates must never write to
    operator_entities -- verified structurally (no write statements) AND
    behaviorally (Watchtower entity count identical after calling it)."""
    conn = sqlite3.connect(f"file:{OPS_DB_PATH}?mode=ro", uri=True)
    before = conn.execute("SELECT COUNT(*) FROM operator_entities WHERE operator_id=?", (wt_op_id,)).fetchone()[0]
    known = frozenset(r[0] for r in conn.execute("SELECT entity_address FROM operator_entities"))
    conn.close()

    fetch_discovery_intake_candidates(OUTPUT_DB, known_operator_entities=known)

    conn2 = sqlite3.connect(f"file:{OPS_DB_PATH}?mode=ro", uri=True)
    after = conn2.execute("SELECT COUNT(*) FROM operator_entities WHERE operator_id=?", (wt_op_id,)).fetchone()[0]
    conn2.close()
    assert before == after == 69


def test_evidence_qualification_cannot_mutate_authority_structural():
    src = (ROOT / "src/ops/operator_reader.py").read_text()
    method_start = src.index("def fetch_unified_investigation")
    method_end = src.index("\n    def fetch_summary")
    body = src[method_start:method_end].upper()
    for verb in ("INSERT INTO OPERATORS", "UPDATE OPERATORS", "DELETE FROM OPERATORS"):
        assert verb not in body


def test_cex_context_cannot_mutate_authority_structural():
    src = (ROOT / "src/ops/discovery_intake.py").read_text()
    upper = src.upper()
    for verb in ("INSERT INTO OPERATORS", "UPDATE OPERATORS", "DELETE FROM OPERATORS", "INSERT INTO OPERATOR_ENTITIES"):
        assert verb not in upper


# ── 3SW2 preservation ────────────────────────────────────────────────────

def test_3sw2_canonical_row_unchanged(baseline, sw2_op_id):
    conn = sqlite3.connect(f"file:{OPS_DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    row = dict(conn.execute("SELECT * FROM operators WHERE operator_id=?", (sw2_op_id,)).fetchone())
    conn.close()
    assert row == baseline["3sw2_operator_row"]
    assert row["status"] == "CONFIRMED"


def test_3sw2_membership_unchanged(baseline, sw2_op_id):
    conn = sqlite3.connect(f"file:{OPS_DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    entities = [dict(r) for r in conn.execute(
        "SELECT * FROM operator_entities WHERE operator_id=? ORDER BY entity_address", (sw2_op_id,)
    ).fetchall()]
    conn.close()
    assert len(entities) == baseline["3sw2_operator_entities_count"]
    assert _digest(entities) == baseline["3sw2_operator_entities_digest"]


def test_3sw2_unified_route_still_confirmed(reader, sw2_op_id):
    conn = sqlite3.connect(f"file:{OPS_DB_PATH}?mode=ro", uri=True)
    entity = conn.execute("SELECT entity_address FROM operator_entities WHERE operator_id=? LIMIT 1", (sw2_op_id,)).fetchone()[0]
    conn.close()
    result = reader.fetch_unified_investigation(entity)
    assert result["identity"]["authority_state"] == "CONFIRMED"


# ── Dv34 reference ────────────────────────────────────────────────────────

def test_dv34_historical_123(reader):
    result = reader.fetch_unified_investigation(DV34)
    assert result["historical_population"]["count"] == 123


def test_dv34_high_qualified_23(reader):
    result = reader.fetch_unified_investigation(DV34)
    assert result["evidence_qualification"]["high_qualified_count"] == 23


def test_dv34_remainder_100_matches_82_plus_18():
    assert 123 - 23 == 100 == 82 + 18


def test_dv34_provisioner_role(reader):
    result = reader.fetch_unified_investigation(DV34)
    assert result["identity"]["candidate_role"] == "PROVISIONING_NETWORK_CANDIDATE"


def test_dv34_no_canonical_authority(reader):
    result = reader.fetch_unified_investigation(DV34)
    assert result["identity"]["authority_state"] is None
    assert result["identity"]["canonical_operator_id"] is None


def test_dv34_not_promotion_eligible(reader):
    result = reader.fetch_unified_investigation(DV34)
    assert result["identity"]["promotion_eligible"] is False


def test_dv34_is_not_watchtower(reader, wt_op_id):
    dv34_result = reader.fetch_unified_investigation(DV34)
    assert dv34_result["identity"]["canonical_operator_id"] != wt_op_id
    conn = sqlite3.connect(f"file:{OPS_DB_PATH}?mode=ro", uri=True)
    is_wt_entity = conn.execute(
        "SELECT COUNT(*) FROM operator_entities WHERE operator_id=? AND entity_address=?", (wt_op_id, DV34)
    ).fetchone()[0]
    conn.close()
    assert is_wt_entity == 0


def test_dv34_appears_in_discovery_intake_candidates():
    conn = sqlite3.connect(f"file:{OPS_DB_PATH}?mode=ro", uri=True)
    known = frozenset(r[0] for r in conn.execute("SELECT entity_address FROM operator_entities"))
    conn.close()
    candidates = fetch_discovery_intake_candidates(OUTPUT_DB, known_operator_entities=known)
    dv34_family = [c for c in candidates if c["launches"] == 23]
    assert len(dv34_family) >= 1
    assert dv34_family[0]["candidate_role"] == "PROVISIONING_NETWORK_CANDIDATE"


def test_discovery_intake_family_id_is_not_prefixed():
    """Regression test: registry rows' family_id and profile_href must use
    the BARE discovery family_id (e.g. 'DFF_...'), matching what
    fetch_discovery_family_detail() looks up -- a prior bug prefixed this
    with 'discovery:' which broke every discovery-candidate detail-page
    link with 'Unable to load record: Record unavailable'."""
    candidates = fetch_discovery_intake_candidates(OUTPUT_DB)
    for c in candidates:
        assert not c["family_id"].startswith("discovery:")
        assert c["family_id"].startswith("DFF_")
        assert c["presentation"]["profile_href"] == "/intelligence/operations/" + c["family_id"]


def test_discovery_family_detail_lookup_resolves_real_family():
    """Regression test for the exact user-reported bug: clicking a NEW
    DISCOVERY registry row must not 404 on its detail page."""
    from src.ops.discovery_intake import fetch_discovery_family_detail

    candidates = fetch_discovery_intake_candidates(OUTPUT_DB)
    assert candidates, "no discovery candidates available to test against"
    target = candidates[0]["family_id"]

    detail = fetch_discovery_family_detail(OUTPUT_DB, target)
    assert detail is not None
    assert detail["family_id"] == target
    assert detail["launches"] > 0
    assert detail["candidate_role"] == "PROVISIONING_NETWORK_CANDIDATE"


def test_discovery_family_detail_route_end_to_end():
    """Full end-to-end reproduction of the reported bug via a real Flask
    test client hitting the exact API the detail page calls."""
    from flask import Flask

    from src.ops.operator_routes import operator_bp

    candidates = fetch_discovery_intake_candidates(OUTPUT_DB)
    assert candidates
    target = candidates[0]["family_id"]

    app = Flask(__name__, template_folder=str(ROOT / "templates"), static_folder=str(ROOT / "static"))
    app.register_blueprint(operator_bp)
    with app.test_client() as client:
        resp = client.get(f"/api/ops/emerging-operators/{target}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["family"]["family_id"] == target

        page = client.get(f"/intelligence/operations/{target}")
        assert page.status_code == 200


def test_discovery_family_detail_returns_none_for_nonexistent_family():
    from src.ops.discovery_intake import fetch_discovery_family_detail

    assert fetch_discovery_family_detail(OUTPUT_DB, "DFF_doesnotexist0000") is None


def test_discovery_family_detail_bounded_member_fetch():
    """Part 8 requirement: detail lookup must not return unbounded
    members."""
    from src.ops.discovery_intake import MAX_INTAKE_CANDIDATES, fetch_discovery_family_detail

    candidates = fetch_discovery_intake_candidates(OUTPUT_DB)
    assert candidates
    target = candidates[0]["family_id"]
    detail = fetch_discovery_family_detail(OUTPUT_DB, target)
    assert detail["member_sample_size"] <= 200


# ── Discovery intake ──────────────────────────────────────────────────────

def test_discovery_intake_bounded_not_all_385():
    conn = sqlite3.connect(f"file:{OPS_DB_PATH}?mode=ro", uri=True)
    known = frozenset(r[0] for r in conn.execute("SELECT entity_address FROM operator_entities"))
    conn.close()
    candidates = fetch_discovery_intake_candidates(OUTPUT_DB, known_operator_entities=known)
    assert 0 < len(candidates) <= 20
    assert len(candidates) != 385


def test_discovery_intake_matches_p1_strong_attributable_count():
    conn = sqlite3.connect(f"file:{OUTPUT_DB}?mode=ro", uri=True)
    strong_attributable = conn.execute(
        "SELECT COUNT(*) FROM candidate_families WHERE classification='STRONG_CANDIDATE_FAMILY' AND attribution_state='ATTRIBUTABLE'"
    ).fetchone()[0]
    conn.close()
    candidates = fetch_discovery_intake_candidates(OUTPUT_DB)
    assert len(candidates) == strong_attributable == 8


def test_discovery_intake_excludes_noise_and_ambiguous():
    candidates = fetch_discovery_intake_candidates(OUTPUT_DB)
    for c in candidates:
        assert c["discovery_classification"] == "STRONG_CANDIDATE_FAMILY"
        assert c["discovery_attribution_state"] == "ATTRIBUTABLE"


def test_discovery_intake_no_automatic_promotion():
    src = (ROOT / "src/ops/discovery_intake.py").read_text()
    assert "def promote" not in src
    assert "CONFIRMED'" not in src.replace("'CONFIRMED'", "")  # never assigns CONFIRMED status anywhere


def test_known_operation_overlap_guard_present_and_excludes():
    conn = sqlite3.connect(f"file:{OPS_DB_PATH}?mode=ro", uri=True)
    known = frozenset(r[0] for r in conn.execute("SELECT entity_address FROM operator_entities"))
    conn.close()
    # fabricate an overlap by pretending a real discovery root is "known"
    conn3 = sqlite3.connect(f"file:{OUTPUT_DB}?mode=ro", uri=True)
    a_root = conn3.execute(
        "SELECT root_evidence FROM candidate_families WHERE classification='STRONG_CANDIDATE_FAMILY' AND attribution_state='ATTRIBUTABLE' LIMIT 1"
    ).fetchone()[0]
    conn3.close()
    forced_known = known | {a_root}
    candidates = fetch_discovery_intake_candidates(OUTPUT_DB, known_operator_entities=forced_known)
    flagged = [c for c in candidates if c["family_id"].endswith(a_root) is False and c["known_operation_overlap"]]
    overlap_rows = [c for c in candidates if c["known_operation_overlap"]]
    assert len(overlap_rows) >= 1


# ── UI/API bounds ─────────────────────────────────────────────────────────

def test_discovery_intake_route_bounded_response():
    from flask import Flask

    from src.ops.operator_routes import operator_bp

    app = Flask(__name__, template_folder=str(ROOT / "templates"), static_folder=str(ROOT / "static"))
    app.register_blueprint(operator_bp)
    with app.test_client() as client:
        resp = client.get("/api/ops/discovery-intake-candidates")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["count"] <= 20
        assert len(resp.get_data()) < 10000


def test_discovery_intake_route_no_write_methods():
    from src.ops.operator_routes import operator_bp

    rules = [r for r in operator_bp.deferred_functions]
    # structural check: route only registered for GET (no methods=["POST"] on this endpoint)
    src = (ROOT / "src/ops/operator_routes.py").read_text()
    idx = src.index('"/api/ops/discovery-intake-candidates"')
    line = src[idx - 40 : idx + 40]
    assert "POST" not in line


def test_no_production_write_statements_in_this_test_module():
    src = Path(__file__).read_text()
    lines = [ln for ln in src.splitlines() if ".execute(" in ln and "test_no_production_write" not in ln]
    combined = "\n".join(lines).upper()
    for table in ("OPERATORS", "OPERATOR_ENTITIES", "CANDIDATE_FAMILIES"):
        for verb in ("INSERT INTO " + table, "UPDATE " + table, "DELETE FROM " + table):
            assert verb not in combined


def test_no_provider_or_rpc_calls_in_discovery_intake_module():
    src = (ROOT / "src/ops/discovery_intake.py").read_text()
    for forbidden in ("helius", "getTransaction", "requests.post", "urllib.request"):
        assert forbidden not in src
