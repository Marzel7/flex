"""
Operations OS — Operator Resolution API Routes.

GET  /api/ops/operators                      → list all operators
GET  /api/ops/operators/summary              → count summary
GET  /api/ops/operators/<operator_id>        → full operator detail
GET  /api/ops/operators/by-entity/<address>  → operators containing a wallet
POST /api/ops/operators/resolve              → trigger resolver run
POST /api/ops/operators/<operator_id>/review → record analyst decision

GET  /api/ops/evidence-catalogue             → full evidence type catalogue
"""
from __future__ import annotations

import time
from flask import Blueprint, jsonify, redirect, request

from src.ops.operator_model import (
    EVIDENCE_CATALOGUE,
    OPERATOR_STATES,
)
from src.ops.operator_reader import OperatorReader
from src.ops.operator_resolver import OperatorResolver

operator_bp = Blueprint("operators", __name__)

_store: OperatorReader | None = None
_promotion_service = None
_emerging_service = None
_governance_service = None
_investigation_lifecycle_service = None


def _get_store() -> OperatorReader:
    global _store
    if _store is None:
        from src.core.db import OPS_DB_PATH
        _store = OperatorReader(str(OPS_DB_PATH))
    return _store


def _get_promotion_service():
    global _promotion_service
    if _promotion_service is None:
        from src.core.db import OPS_DB_PATH, DB_PATH
        from src.ops.promotion_service import PromotionService
        _promotion_service = PromotionService(str(OPS_DB_PATH), str(DB_PATH))
    return _promotion_service


def _get_emerging_service():
    global _emerging_service
    if _emerging_service is None:
        from src.core.db import OPS_DB_PATH, DB_PATH
        from src.ops.emerging_operator_service import EmergingOperatorService
        _emerging_service = EmergingOperatorService(str(OPS_DB_PATH), str(DB_PATH))
    return _emerging_service


def _get_governance_service():
    global _governance_service
    if _governance_service is None:
        from src.core.db import OPS_DB_PATH
        from src.ops.operator_identity_governance import OperatorIdentityGovernanceService
        _governance_service = OperatorIdentityGovernanceService(str(OPS_DB_PATH))
    return _governance_service


def _get_investigation_lifecycle_service():
    global _investigation_lifecycle_service
    if _investigation_lifecycle_service is None:
        from src.core.db import OPS_DB_PATH
        from src.ops.investigation_lifecycle import InvestigationLifecycleService
        _investigation_lifecycle_service = InvestigationLifecycleService(str(OPS_DB_PATH), _get_emerging_service())
    return _investigation_lifecycle_service


def _investigation_lifecycle_response(exc):
    from src.ops.investigation_lifecycle import InvestigationLifecycleError
    if isinstance(exc, InvestigationLifecycleError):
        return jsonify({"ok": False, "error": str(exc), "code": exc.code}), exc.status
    return jsonify({"ok": False, "error": str(exc), "code": "INVESTIGATION_LIFECYCLE_ERROR"}), 500


def _governance_response(exc):
    from src.ops.operator_identity_governance import GovernanceError
    if isinstance(exc, GovernanceError):
        return jsonify({"ok": False, "error": str(exc), "code": exc.code}), exc.status
    return jsonify({"ok": False, "error": str(exc), "code": "GOVERNANCE_ERROR"}), 500


def _confirmation_response(exc):
    from src.ops.operation_confirmation import ConfirmationError
    if isinstance(exc, ConfirmationError):
        return jsonify({"ok": False, "error": str(exc), "code": exc.code}), exc.status
    return jsonify({"ok": False, "error": str(exc), "code": "CONFIRMATION_ERROR"}), 500


# ── List & summary ─────────────────────────────────────────────────────────────

@operator_bp.route("/api/ops/operators")
def list_operators():
    store = _get_store()
    limit = min(int(request.args.get("limit", 100)), 500)
    include_rejected = request.args.get("include_rejected", "false").lower() == "true"
    rows = store.fetch_all_operators(exclude_rejected=not include_rejected, limit=limit)
    return jsonify({
        "ok":          True,
        "operators":   rows,
        "count":       len(rows),
        "generated_at": int(time.time()),
    })


@operator_bp.route("/api/ops/operators/summary")
def operator_summary():
    store = _get_store()
    summary = store.fetch_summary()
    summary["ok"]           = True
    summary["generated_at"] = int(time.time())
    return jsonify(summary)


# ── Single operator ────────────────────────────────────────────────────────────

@operator_bp.route("/api/ops/operators/<operator_id>")
def get_operator(operator_id: str):
    store = _get_store()
    op = store.fetch_operator(operator_id)
    if not op:
        return jsonify({"ok": False, "error": "Operator not found"}), 404
    return jsonify({"ok": True, "operator": op, "generated_at": int(time.time())})


# ── Identity lifecycle governance ────────────────────────────────────────────

@operator_bp.route("/api/ops/operators/<operator_id>/identity")
def get_operator_identity(operator_id: str):
    from src.core.db import OPS_DB_PATH
    from src.ops.operator_identity_governance import read_identity_lifecycle
    lifecycle = read_identity_lifecycle(str(OPS_DB_PATH), operator_id)
    if not lifecycle:
        return jsonify({"ok": False, "error": "Operator Identity not found"}), 404
    return jsonify({"ok": True, "operator_id": operator_id, "identity": lifecycle})


@operator_bp.route("/api/ops/operators/<operator_id>/identity/expand", methods=["POST"])
def expand_operator_identity(operator_id: str):
    try:
        result = _get_governance_service().expand(operator_id, request.get_json(silent=True) or {})
        return jsonify({"ok": True, **result})
    except Exception as exc:
        return _governance_response(exc)


@operator_bp.route("/api/ops/operators/<operator_id>/identity/activity", methods=["POST"])
def set_operator_activity(operator_id: str):
    try:
        body = request.get_json(silent=True) or {}
        result = _get_governance_service().set_activity(operator_id, body.get("activity_status"), body)
        return jsonify({"ok": True, **result})
    except Exception as exc:
        return _governance_response(exc)


@operator_bp.route("/api/ops/operators/<operator_id>/identity/review", methods=["POST"])
def move_operator_to_review(operator_id: str):
    try:
        result = _get_governance_service().move_to_review(operator_id, request.get_json(silent=True) or {})
        return jsonify({"ok": True, **result})
    except Exception as exc:
        return _governance_response(exc)


@operator_bp.route("/api/ops/operators/<operator_id>/identity/resolve", methods=["POST"])
def resolve_operator_review(operator_id: str):
    try:
        result = _get_governance_service().resolve_review(operator_id, request.get_json(silent=True) or {})
        return jsonify({"ok": True, **result})
    except Exception as exc:
        return _governance_response(exc)


@operator_bp.route("/api/ops/operators/<operator_id>/identity/retire", methods=["POST"])
def retire_operator_identity(operator_id: str):
    try:
        result = _get_governance_service().retire(operator_id, request.get_json(silent=True) or {})
        return jsonify({"ok": True, **result})
    except Exception as exc:
        return _governance_response(exc)


@operator_bp.route("/api/ops/operators/<operator_id>/identity/merge", methods=["POST"])
def merge_operator_identity(operator_id: str):
    try:
        body = request.get_json(silent=True) or {}
        result = _get_governance_service().merge(operator_id, body.get("source_operator_ids") or [], body)
        return jsonify({"ok": True, **result})
    except Exception as exc:
        return _governance_response(exc)


@operator_bp.route("/api/ops/operators/<operator_id>/identity/split", methods=["POST"])
def split_operator_identity(operator_id: str):
    try:
        body = request.get_json(silent=True) or {}
        result = _get_governance_service().split(operator_id, body.get("children") or [], body)
        return jsonify({"ok": True, **result})
    except Exception as exc:
        return _governance_response(exc)


# ── By entity ──────────────────────────────────────────────────────────────────

@operator_bp.route("/api/ops/operators/by-entity/<path:address>")
def operators_by_entity(address: str):
    store = _get_store()
    rows = store.fetch_by_entity(address)
    return jsonify({
        "ok":           True,
        "entity":       address,
        "operators":    rows,
        "count":        len(rows),
        "generated_at": int(time.time()),
    })


# ── Resolver ────────────────────────────────────────────────────────────────────

@operator_bp.route("/api/ops/operators/resolve", methods=["POST"])
def trigger_resolve():
    from src.core.db import OPS_DB_PATH, DB_PATH
    try:
        # X16B identity evaluation is deliberately detached from OperatorStore;
        # this endpoint cannot populate or mutate canonical operators.
        resolver = OperatorResolver(None, str(OPS_DB_PATH), str(DB_PATH))
        report   = resolver.run()
        return jsonify({"ok": True, "report": report, "generated_at": int(time.time())})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


# ── Review ──────────────────────────────────────────────────────────────────────

@operator_bp.route("/api/ops/operators/<operator_id>/review", methods=["POST"])
def record_review(operator_id: str):
    # X16C closes the legacy write path. Canonical identity decisions must be
    # fingerprint-bound and made through the promotion governance endpoint.
    data = request.get_json(silent=True) or {}
    if data.get("decision") not in {"CONFIRMED", "REJECTED", "MERGE", "SPLIT", "FLAGGED"}:
        return jsonify({"ok": False, "error": f"Invalid decision: {data.get('decision')!r}"}), 400
    return jsonify({
        "ok": False,
        "code": "PROMOTION_REVIEW_REQUIRED",
        "error": "Direct operator review writes are disabled; use the Investigation Population review workflow.",
    }), 409


# ── Search ─────────────────────────────────────────────────────────────────────

@operator_bp.route("/api/ops/operators/search")
def search_operators():
    """
    Search operators by entity address, status, confidence, or display_name/summary.
    Returns lightweight rows — no evidence/entity detail.
    """
    store = _get_store()
    q      = (request.args.get("q", "") or "").strip()
    status = request.args.get("status", "")
    conf   = request.args.get("confidence", "")
    limit  = min(int(request.args.get("limit", 50)), 200)

    # Entity-address search: find operators containing this wallet
    if q and len(q) >= 6:
        rows = store.fetch_by_entity(q)
        if rows:
            return jsonify({
                "ok":      True,
                "query":   q,
                "mode":    "entity",
                "results": rows[:limit],
                "count":   min(len(rows), limit),
            })

    # Fallback: list with optional status/confidence filter
    all_ops = store.fetch_all_operators(limit=500)
    if q:
        ql = q.lower()
        all_ops = [o for o in all_ops
                   if ql in (o.get("operator_id") or "").lower()
                   or ql in (o.get("display_name") or "").lower()
                   or ql in (o.get("summary") or "").lower()]
    if status:
        all_ops = [o for o in all_ops if o.get("status") == status]
    if conf:
        all_ops = [o for o in all_ops if o.get("confidence") == conf]

    return jsonify({
        "ok":      True,
        "query":   q,
        "mode":    "full",
        "results": all_ops[:limit],
        "count":   min(len(all_ops), limit),
    })


# ── Operator Intelligence page ──────────────────────────────────────────────────

@operator_bp.route("/intelligence/operator/<operator_id>")
def operator_page(operator_id: str):
    from flask import render_template
    store = _get_store()
    op = store.fetch_operator(operator_id)
    if not op:
        return render_template("operator_intelligence.html",
                               operator_id=operator_id,
                               operator=None, error="Operator not found"), 404
    return render_template("operator_intelligence.html",
                           operator_id=operator_id,
                           operator=op, error=None)


# ── Operator index page ─────────────────────────────────────────────────────────

@operator_bp.route("/intelligence/operators")
def operators_index():
    from flask import render_template
    return render_template("operators_index.html", active_page="operators")


# ── Emerging operators (read-only X20 projection) ───────────────────────────

@operator_bp.route("/intelligence/emerging-operators")
@operator_bp.route("/intelligence/operations")
def emerging_operators_page():
    query = request.query_string.decode("utf-8")
    target = "/intelligence/operators"
    if query:
        target += f"?{query}"
    return redirect(target, code=302)


@operator_bp.route("/intelligence/operations/<path:entity>")
def operation_profile_page(entity: str):
    from flask import render_template
    return render_template(
        "operation_profile.html", entity=entity, active_page="operation_profile"
    )


@operator_bp.route("/api/ops/emerging-operators")
def list_emerging_operators():
    limit = min(max(int(request.args.get("limit", 200)), 1), 500)
    debug = request.args.get("debug", "0").lower() in {"1", "true", "yes"}
    return jsonify({"ok": True, **_get_emerging_service().list(limit=limit, debug=debug)})


@operator_bp.route("/api/ops/emerging-operators/<path:entity>")
def get_emerging_operator(entity: str):
    candidate = _get_emerging_service().get(entity)
    if not candidate:
        return jsonify({"ok": False, "error": "Operation family not found"}), 404
    return jsonify({"ok": True, "family": candidate, "candidate": candidate, "read_only": True})


@operator_bp.route("/api/ops/investigations/<path:family_id>/dismiss", methods=["POST"])
def dismiss_investigation(family_id: str):
    try:
        family = _get_investigation_lifecycle_service().dismiss(family_id, request.get_json(silent=True) or {})
        return jsonify({"ok": True, "family": family, "attribution_unchanged": True})
    except Exception as exc:
        return _investigation_lifecycle_response(exc)


@operator_bp.route("/api/ops/investigations/<path:family_id>/reopen", methods=["POST"])
def reopen_investigation(family_id: str):
    try:
        family = _get_investigation_lifecycle_service().reopen(family_id, request.get_json(silent=True) or {})
        return jsonify({"ok": True, "family": family, "attribution_unchanged": True})
    except Exception as exc:
        return _investigation_lifecycle_response(exc)


@operator_bp.route("/api/ops/operations/<path:family_id>/confirm", methods=["POST"])
def confirm_operation(family_id: str):
    try:
        from src.core.db import OPS_DB_PATH
        from src.ops.operation_confirmation import OperationConfirmationService
        body = request.get_json(silent=True) or {}
        family = OperationConfirmationService(
            str(OPS_DB_PATH), _get_emerging_service()
        ).confirm(family_id, analyst=body.get("confirmed_by"),
                  reason=body.get("confirmation_reason"), notes=body.get("confirmation_notes", ""))
        return jsonify({"ok": True, "family": family, "lifecycle_changed_only": True})
    except Exception as exc:
        return _confirmation_response(exc)


@operator_bp.route("/api/ops/operations/<path:family_id>/reverse-confirmation", methods=["POST"])
def reverse_operation_confirmation(family_id: str):
    try:
        from src.core.db import OPS_DB_PATH
        from src.ops.operation_confirmation import OperationConfirmationService
        body = request.get_json(silent=True) or {}
        family = OperationConfirmationService(
            str(OPS_DB_PATH), _get_emerging_service()
        ).reverse(family_id, analyst=body.get("reversed_by"),
                  reason=body.get("reversal_reason"), notes=body.get("reversal_notes", ""))
        return jsonify({"ok": True, "family": family, "lifecycle_changed_only": True})
    except Exception as exc:
        return _confirmation_response(exc)


# ── Promotion governance ──────────────────────────────────────────────────────

def _promotion_error(exc):
    from src.ops.promotion_service import PromotionError
    if isinstance(exc, PromotionError):
        return jsonify({"ok": False, "error": str(exc), "code": exc.code}), exc.status
    return jsonify({"ok": False, "error": str(exc), "code": "PROMOTION_ERROR"}), 500


@operator_bp.route("/intelligence/operator-promotions")
def operator_promotions_page():
    # X73.4 retires the duplicate standalone promotion UI. Historical
    # bookmarks remain valid and land in the reconciled review workspace;
    # the promotion APIs below remain unchanged for compatibility.
    return redirect("/intelligence/operators?focus=review", code=302)


@operator_bp.route("/api/operators/promotions")
def list_operator_promotions():
    try:
        return jsonify({"ok": True, **_get_promotion_service().list()})
    except Exception as exc:
        return _promotion_error(exc)


@operator_bp.route("/api/operators/promotions/<proposal_id>")
def get_operator_promotion(proposal_id: str):
    try:
        return jsonify({"ok": True, "proposal": _get_promotion_service().detail(proposal_id)})
    except Exception as exc:
        return _promotion_error(exc)


def _decide_promotion(proposal_id: str, decision: str):
    try:
        result = _get_promotion_service().decide(
            proposal_id, decision, request.get_json(silent=True) or {}
        )
        return jsonify({"ok": True, **result})
    except Exception as exc:
        return _promotion_error(exc)


@operator_bp.route("/api/operators/promotions/<proposal_id>/approve", methods=["POST"])
def approve_operator_promotion(proposal_id: str):
    return _decide_promotion(proposal_id, "APPROVE")


@operator_bp.route("/api/operators/promotions/<proposal_id>/reject", methods=["POST"])
def reject_operator_promotion(proposal_id: str):
    return _decide_promotion(proposal_id, "REJECT")


@operator_bp.route("/api/operators/promotions/<proposal_id>/defer", methods=["POST"])
def defer_operator_promotion(proposal_id: str):
    return _decide_promotion(proposal_id, "DEFER")


# ── Evidence catalogue ──────────────────────────────────────────────────────────

@operator_bp.route("/api/ops/evidence-catalogue")
def evidence_catalogue():
    rows = [
        {
            "evidence_type": et,
            "category":      v["category"],
            "weight":        v["weight"],
            "notes":         v["notes"],
        }
        for et, v in EVIDENCE_CATALOGUE.items()
    ]
    return jsonify({
        "ok":       True,
        "evidence": rows,
        "count":    len(rows),
    })


# ── Registration ────────────────────────────────────────────────────────────────

def register_operator_routes(app) -> None:
    app.register_blueprint(operator_bp)
    from src.intelligence.operational_landscape_routes import register_operational_landscape_routes
    register_operational_landscape_routes(app)
    # X69.3: gated, unlinked developer diagnostics. Registration is inert until
    # explicitly enabled (or Flask debug/testing mode) and never builds shadow data.
    from src.ops.reconciliation_diagnostics_routes import register_reconciliation_diagnostics_routes
    register_reconciliation_diagnostics_routes(app)
    # Seed schema on startup (non-blocking)
    try:
        _get_store()
        print("[OPERATORS] Operator Resolution registered.")
    except Exception as exc:
        print(f"[OPERATORS] Startup failed (non-fatal): {exc}")
