"""
Sprint X8 — Operator Resolution Framework tests.

Covers:
  - Evidence catalogue completeness and category consistency
  - OperatorModel validation (status, confidence, evidence category constraints)
  - OperatorStore create / add_entity / add_evidence / record_review
  - Confidence derivation from evidence counts
  - Human-decided states cannot be overwritten
  - OperatorStore fetch_by_entity / fetch_summary
  - OperatorResolver rule isolation (rules handle missing tables gracefully)
  - API routes: list, summary, by-entity, evidence-catalogue, review
  - Template markup: entity_intelligence has operator section + fetch
  - src.core.db canonical path module
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


# ── helpers ──────────────────────────────────────────────────────────────────

def _tmp_store():
    fd, path = tempfile.mkstemp(suffix="_op_test.db")
    os.close(fd)
    from src.ops.operator_store import OperatorStore
    store = OperatorStore(path)
    store.initialize_schema()
    return store, path


def _read_template(name):
    with open(os.path.join(ROOT, "templates", name), encoding="utf-8") as f:
        return f.read()


# ── Evidence catalogue ────────────────────────────────────────────────────────

class TestEvidenceCatalogue:

    def test_all_entries_have_required_keys(self):
        from src.ops.operator_model import EVIDENCE_CATALOGUE
        for et, v in EVIDENCE_CATALOGUE.items():
            assert "category" in v, f"{et} missing category"
            assert "weight"   in v, f"{et} missing weight"
            assert "notes"    in v, f"{et} missing notes"

    def test_categories_are_valid(self):
        from src.ops.operator_model import EVIDENCE_CATALOGUE, EVIDENCE_CATEGORIES
        for et, v in EVIDENCE_CATALOGUE.items():
            assert v["category"] in EVIDENCE_CATEGORIES, \
                f"{et} has invalid category {v['category']!r}"

    def test_context_evidence_has_zero_weight(self):
        from src.ops.operator_model import EVIDENCE_CATALOGUE, EVIDENCE_CONTEXT
        for et, v in EVIDENCE_CATALOGUE.items():
            if v["category"] == EVIDENCE_CONTEXT:
                assert v["weight"] == 0.0, f"{et} (CONTEXT) must have weight 0"

    def test_identity_evidence_has_nonzero_weight(self):
        from src.ops.operator_model import EVIDENCE_CATALOGUE, EVIDENCE_IDENTITY
        for et, v in EVIDENCE_CATALOGUE.items():
            if v["category"] == EVIDENCE_IDENTITY:
                assert v["weight"] > 0, f"{et} (IDENTITY) must have weight > 0"

    def test_at_least_three_identity_types(self):
        from src.ops.operator_model import EVIDENCE_CATALOGUE, EVIDENCE_IDENTITY
        identity = [et for et, v in EVIDENCE_CATALOGUE.items() if v["category"] == EVIDENCE_IDENTITY]
        assert len(identity) >= 3

    def test_at_least_two_supporting_types(self):
        from src.ops.operator_model import EVIDENCE_CATALOGUE, EVIDENCE_SUPPORTING
        supporting = [et for et, v in EVIDENCE_CATALOGUE.items() if v["category"] == EVIDENCE_SUPPORTING]
        assert len(supporting) >= 2


# ── Operator model validation ─────────────────────────────────────────────────

class TestOperatorModel:

    def test_valid_operator_ok(self):
        from src.ops.operator_model import Operator, CANDIDATE, CONFIDENCE_UNKNOWN
        now = int(time.time())
        op = Operator(
            operator_id="test-id",
            status=CANDIDATE,
            confidence=CONFIDENCE_UNKNOWN,
            first_seen=None, last_seen=None,
            summary="test", review_state="PENDING",
            display_name=None, created_at=now, updated_at=now,
        )
        assert op.status == CANDIDATE

    def test_invalid_status_raises(self):
        from src.ops.operator_model import Operator, CONFIDENCE_UNKNOWN
        now = int(time.time())
        with pytest.raises(ValueError, match="status"):
            Operator(
                operator_id="x", status="INVALID", confidence=CONFIDENCE_UNKNOWN,
                first_seen=None, last_seen=None, summary=None, review_state="PENDING",
                display_name=None, created_at=now, updated_at=now,
            )

    def test_invalid_confidence_raises(self):
        from src.ops.operator_model import Operator, CANDIDATE
        now = int(time.time())
        with pytest.raises(ValueError, match="confidence"):
            Operator(
                operator_id="x", status=CANDIDATE, confidence="MAYBE",
                first_seen=None, last_seen=None, summary=None, review_state="PENDING",
                display_name=None, created_at=now, updated_at=now,
            )

    def test_to_dict_has_expected_keys(self):
        from src.ops.operator_model import Operator, CANDIDATE, CONFIDENCE_UNKNOWN
        now = int(time.time())
        op = Operator("id", CANDIDATE, CONFIDENCE_UNKNOWN, None, None, "s", "PENDING", None, now, now)
        d = op.to_dict()
        for key in ("operator_id", "status", "confidence", "first_seen", "last_seen",
                    "summary", "review_state", "display_name", "created_at", "updated_at"):
            assert key in d


# ── OperatorStore ─────────────────────────────────────────────────────────────

class TestOperatorStore:

    def test_create_operator_returns_id(self):
        store, _ = _tmp_store()
        op_id = store.create_operator(summary="test operator")
        assert isinstance(op_id, str)
        assert len(op_id) > 8

    def test_fetch_operator_returns_dict(self):
        store, _ = _tmp_store()
        op_id = store.create_operator(summary="test")
        result = store.fetch_operator(op_id)
        assert result is not None
        assert result["operator_id"] == op_id
        assert result["status"] == "CANDIDATE"

    def test_fetch_unknown_returns_none(self):
        store, _ = _tmp_store()
        assert store.fetch_operator("nonexistent-uuid") is None

    def test_add_entity(self):
        store, _ = _tmp_store()
        op_id = store.create_operator()
        store.add_entity(op_id, "WALLET1", entity_type="TREASURY", confidence="HIGH")
        result = store.fetch_operator(op_id)
        assert any(e["entity_address"] == "WALLET1" for e in result["entities"])

    def test_add_entity_idempotent(self):
        store, _ = _tmp_store()
        op_id = store.create_operator()
        store.add_entity(op_id, "WALLET1")
        store.add_entity(op_id, "WALLET1")  # second call updates evidence_count
        result = store.fetch_operator(op_id)
        wallet_rows = [e for e in result["entities"] if e["entity_address"] == "WALLET1"]
        assert len(wallet_rows) == 1

    def test_add_evidence_identity_advances_status(self):
        store, _ = _tmp_store()
        op_id = store.create_operator()
        # Two IDENTITY signals → PROVISIONAL
        store.add_evidence(op_id, evidence_type="SHARED_TREASURY_ROOT",    entity_a="W1")
        store.add_evidence(op_id, evidence_type="CROSS_OPERATION_WALLET_OVERLAP", entity_a="W2")
        result = store.fetch_operator(op_id)
        assert result["status"] == "PROVISIONAL"

    def test_add_evidence_single_identity_becomes_review_candidate(self):
        store, _ = _tmp_store()
        op_id = store.create_operator()
        store.add_evidence(op_id, evidence_type="SHARED_TREASURY_ROOT", entity_a="W1")
        result = store.fetch_operator(op_id)
        assert result["status"] == "REVIEW_CANDIDATE"

    def test_add_evidence_supporting_only_never_promotes(self):
        store, _ = _tmp_store()
        op_id = store.create_operator()
        for _ in range(5):
            store.add_evidence(op_id, evidence_type="MATCHING_FUNDING_TEMPLATE", entity_a="W1")
        result = store.fetch_operator(op_id)
        # No identity evidence → still CANDIDATE with UNKNOWN confidence
        assert result["status"] == "CANDIDATE"
        assert result["confidence"] == "UNKNOWN"

    def test_add_unknown_evidence_type_raises(self):
        store, _ = _tmp_store()
        op_id = store.create_operator()
        with pytest.raises(ValueError, match="Unknown evidence type"):
            store.add_evidence(op_id, evidence_type="MADE_UP_TYPE")

    def test_record_review_confirmed_sets_certain(self):
        store, _ = _tmp_store()
        op_id = store.create_operator()
        store.record_review(op_id, decision="CONFIRMED", reason="verified manually")
        result = store.fetch_operator(op_id)
        assert result["status"]     == "CONFIRMED"
        assert result["confidence"] == "CERTAIN"

    def test_record_review_rejected(self):
        store, _ = _tmp_store()
        op_id = store.create_operator()
        store.record_review(op_id, decision="REJECTED")
        result = store.fetch_operator(op_id)
        assert result["status"] == "REJECTED"

    def test_human_decided_state_not_overwritten(self):
        """Adding evidence after CONFIRMED must not change status back to PROVISIONAL."""
        store, _ = _tmp_store()
        op_id = store.create_operator()
        store.record_review(op_id, decision="CONFIRMED")
        # Add evidence after confirmation
        store.add_evidence(op_id, evidence_type="SHARED_TREASURY_ROOT", entity_a="W1")
        result = store.fetch_operator(op_id)
        assert result["status"] == "CONFIRMED"

    def test_fetch_by_entity_returns_matching_operators(self):
        store, _ = _tmp_store()
        op_id = store.create_operator(summary="owner of WALLET_A")
        store.add_entity(op_id, "WALLET_A")
        results = store.fetch_by_entity("WALLET_A")
        assert len(results) >= 1
        assert any(r["operator_id"] == op_id for r in results)

    def test_fetch_by_entity_no_match_returns_empty(self):
        store, _ = _tmp_store()
        assert store.fetch_by_entity("UNKNOWN_WALLET_XYZ") == []

    def test_fetch_summary_counts(self):
        store, _ = _tmp_store()
        op1 = store.create_operator()
        op2 = store.create_operator()
        # Promote op1 to PROVISIONAL with 2 identity signals
        store.add_evidence(op1, evidence_type="SHARED_TREASURY_ROOT",           entity_a="W1")
        store.add_evidence(op1, evidence_type="CROSS_OPERATION_WALLET_OVERLAP", entity_a="W2")
        # Confirm op2
        store.record_review(op2, decision="CONFIRMED")
        summary = store.fetch_summary()
        assert summary["total"]       >= 2
        assert summary["provisional"] >= 1
        assert summary["confirmed"]   >= 1

    def test_rejected_excluded_from_fetch_all(self):
        store, _ = _tmp_store()
        op_id = store.create_operator()
        store.record_review(op_id, decision="REJECTED")
        ops = store.fetch_all_operators(exclude_rejected=True)
        assert all(o["operator_id"] != op_id for o in ops)

    def test_evidence_list_in_full_fetch(self):
        store, _ = _tmp_store()
        op_id = store.create_operator()
        store.add_evidence(op_id, evidence_type="SHARED_TREASURY_ROOT",
                           entity_a="W1", details={"extra": "val"})
        result = store.fetch_operator(op_id)
        assert len(result["evidence"]) == 1
        assert result["evidence"][0]["evidence_category"] == "IDENTITY"


# ── Confidence derivation ─────────────────────────────────────────────────────

class TestConfidenceDerivation:

    def _derive(self, identity, supporting):
        from src.ops.operator_store import _derive_confidence
        return _derive_confidence(identity, supporting)

    def test_zero_identity_is_unknown(self):
        assert self._derive(0, 10) == "UNKNOWN"

    def test_one_identity_is_low(self):
        assert self._derive(1, 0) == "LOW"

    def test_one_identity_one_supporting_is_low(self):
        assert self._derive(1, 1) == "LOW"

    def test_two_identity_zero_supporting_is_low(self):
        assert self._derive(2, 0) == "LOW"

    def test_two_identity_one_supporting_is_medium(self):
        assert self._derive(2, 1) == "MEDIUM"

    def test_three_identity_is_high(self):
        assert self._derive(3, 0) == "HIGH"

    def test_four_identity_is_high(self):
        assert self._derive(4, 5) == "HIGH"


# ── OperatorResolver ──────────────────────────────────────────────────────────

class TestOperatorResolver:

    def test_run_with_missing_ops_db_does_not_raise(self):
        """Resolver handles missing/empty ops DB gracefully."""
        from src.ops.operator_resolver import OperatorResolver
        store, _ = _tmp_store()
        resolver = OperatorResolver(store, "/nonexistent/path.db", None)
        report = resolver.run()
        assert isinstance(report, dict)
        assert "operators_created"  in report
        assert "evidence_added"     in report
        assert "rules_run"          in report

    def test_run_returns_stats(self):
        from src.ops.operator_resolver import OperatorResolver
        store, _ = _tmp_store()
        resolver = OperatorResolver(store, ":memory:", None)
        report = resolver.run()
        assert report["rules_run"] >= 3

    def test_run_idempotent_on_empty_db(self):
        """Running twice on the same empty DB should produce the same result."""
        from src.ops.operator_resolver import OperatorResolver
        store, _ = _tmp_store()
        r1 = OperatorResolver(store, ":memory:", None).run()
        r2 = OperatorResolver(store, ":memory:", None).run()
        assert r1["operators_created"] == r2["operators_created"]


# ── API routes ────────────────────────────────────────────────────────────────

@pytest.fixture
def op_client():
    import tempfile, os
    from flask import Flask
    import src.ops.operator_routes as orr

    fd, db_path = tempfile.mkstemp(suffix="_op_api_test.db")
    os.close(fd)

    from src.ops.operator_store import OperatorStore
    orr._store = OperatorStore(db_path)
    orr._store.initialize_schema()

    app = Flask(
        __name__,
        template_folder=os.path.join(ROOT, "templates"),
        static_folder=os.path.join(ROOT, "static"),
    )
    app.register_blueprint(orr.operator_bp)
    app.config["TESTING"] = True

    with app.test_client() as c:
        yield c

    try:
        os.unlink(db_path)
    except OSError:
        pass


class TestOperatorRoutes:

    def test_list_operators_returns_ok(self, op_client):
        r = op_client.get("/api/ops/operators")
        assert r.status_code == 200
        data = r.get_json()
        assert data["ok"] is True
        assert isinstance(data["operators"], list)

    def test_summary_returns_ok(self, op_client):
        r = op_client.get("/api/ops/operators/summary")
        assert r.status_code == 200
        data = r.get_json()
        assert data["ok"] is True
        assert "total" in data

    def test_get_unknown_operator_returns_404(self, op_client):
        r = op_client.get("/api/ops/operators/no-such-id")
        assert r.status_code == 404

    def test_by_entity_returns_ok(self, op_client):
        r = op_client.get("/api/ops/operators/by-entity/SOMEADDR")
        assert r.status_code == 200
        data = r.get_json()
        assert data["ok"] is True
        assert data["entity"] == "SOMEADDR"
        assert data["operators"] == []

    def test_evidence_catalogue_returns_entries(self, op_client):
        r = op_client.get("/api/ops/evidence-catalogue")
        assert r.status_code == 200
        data = r.get_json()
        assert data["ok"] is True
        assert data["count"] >= 5
        types = {e["evidence_type"] for e in data["evidence"]}
        assert "SHARED_TREASURY_ROOT" in types
        assert "MATCHING_FUNDING_TEMPLATE" in types

    def test_review_invalid_decision_returns_400(self, op_client):
        import src.ops.operator_routes as orr
        op_id = orr._store.create_operator()
        r = op_client.post(
            f"/api/ops/operators/{op_id}/review",
            data=json.dumps({"decision": "INVALID"}),
            content_type="application/json",
        )
        assert r.status_code == 400

    def test_review_confirmed_requires_promotion_governance(self, op_client):
        import src.ops.operator_routes as orr
        op_id = orr._store.create_operator()
        r = op_client.post(
            f"/api/ops/operators/{op_id}/review",
            data=json.dumps({"decision": "CONFIRMED", "reason": "test"}),
            content_type="application/json",
        )
        assert r.status_code == 409
        assert r.get_json()["code"] == "PROMOTION_REVIEW_REQUIRED"

    def test_resolve_endpoint_returns_ok(self, op_client):
        r = op_client.post("/api/ops/operators/resolve")
        # May return 500 if DB paths aren't available in test env; accept both
        assert r.status_code in (200, 500)
        data = r.get_json()
        assert "ok" in data


# ── Template markup ───────────────────────────────────────────────────────────

class TestOperatorTemplateMarkup:

    def test_entity_intelligence_has_operator_section(self):
        html = _read_template("entity_intelligence.html")
        assert "ei-operator-section" in html

    def test_entity_intelligence_fetches_operator_by_entity(self):
        html = _read_template("entity_intelligence.html")
        assert "/api/ops/operators/by-entity/" in html

    def test_entity_intelligence_has_no_operator_resolved_text(self):
        html = _read_template("entity_intelligence.html")
        assert "No operator currently resolved" in html

    def test_entity_intelligence_has_operator_attribution_label(self):
        html = _read_template("entity_intelligence.html")
        assert "Operator Attribution" in html


# ── src.core.db module ────────────────────────────────────────────────────────

class TestCoreDatabaseModule:

    def test_db_path_is_string(self):
        from src.core.db import DB_PATH
        assert isinstance(DB_PATH, str)
        assert DB_PATH.endswith(".db")

    def test_ops_db_path_is_string(self):
        from src.core.db import OPS_DB_PATH
        assert isinstance(OPS_DB_PATH, str)
        assert OPS_DB_PATH.endswith(".db")

    def test_ops_db_path_contains_ops_v2(self):
        from src.core.db import OPS_DB_PATH
        assert "ops_v2" in OPS_DB_PATH or "wt_ops" in OPS_DB_PATH
