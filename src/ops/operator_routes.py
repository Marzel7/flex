"""
Operations OS — Operator Resolution API Routes.

GET  /api/ops/operators                      → list all operators
GET  /api/ops/operators/summary              → count summary
GET  /api/ops/operators/<operator_id>        → full operator detail
GET  /api/ops/operators/by-entity/<address>  → operators containing a wallet
GET  /api/ops/investigation/<address>/unified → unified read-time projection (canonical + historical + evidence qualification)
POST /api/ops/operators/resolve              → trigger resolver run
POST /api/ops/operators/<operator_id>/review → record analyst decision

GET  /api/ops/evidence-catalogue             → full evidence type catalogue
"""
from __future__ import annotations

import csv
import os
import time
import sqlite3
from pathlib import Path
from flask import Blueprint, jsonify, redirect, request

from src.ops.operator_model import (
    EVIDENCE_CATALOGUE,
    OPERATOR_STATES,
)
from src.ops.operator_reader import OperatorReader
from src.ops.operator_resolver import OperatorResolver

operator_bp = Blueprint("operators", __name__)


@operator_bp.app_template_filter("datetimeformat")
def _operator_datetimeformat(value):
    """Compact UTC display for intelligence templates that carry epoch seconds."""
    return time.strftime("%d %b %Y %H:%M UTC", time.gmtime(int(value))) if value else "Unavailable"

_store: OperatorReader | None = None
_promotion_service = None
_emerging_service = None
_governance_service = None
_investigation_lifecycle_service = None
_p3r_parent_funder_cache: dict[str, object] = {}


def _p3r_parent_funder_csv_path() -> str | None:
    return (
        os.environ.get("P3R_PARENT_FUNDER_CSV_PATH")
        or "/tmp/p3r-clean-20260824T092959Z/exact_amount_99999985000_v1/p3r_exact_amount_99999985000_mint_creator_initial_funder.v1.csv"
    )


def _load_p3r_parent_funder_rows() -> list[dict[str, str]]:
    global _p3r_parent_funder_cache
    path = _p3r_parent_funder_csv_path()
    if not path:
        return []

    try:
        stat = Path(path).stat()
        c_mtime = _p3r_parent_funder_cache.get("mtime")
        if (
            isinstance(c_mtime, float)
            and c_mtime == stat.st_mtime
            and _p3r_parent_funder_cache.get("path") == path
        ):
            return _p3r_parent_funder_cache.get("rows", [])  # type: ignore[return-value]

        with open(path, "r", encoding="utf-8") as handle:
            rows: list[dict[str, str]] = []
            for row in csv.DictReader(handle):
                candidate = (row.get("stored_candidate_parent_direct_funder") or "").strip()
                if not candidate:
                    continue
                rows.append({
                    "mint": (row.get("mint") or "").strip(),
                    "creator_wallet": (row.get("creator_wallet") or "").strip(),
                    "stored_candidate_parent_direct_funder": candidate,
                    "parent_first_funder_fee_payer": (row.get("parent_first_funder_fee_payer") or "").strip(),
                    "parent_first_funder_signature": (row.get("parent_first_funder_signature") or "").strip(),
                    "parent_first_funder_timestamp": (row.get("parent_first_funder_timestamp") or "").strip(),
                    "parent_first_funder_slot": (row.get("parent_first_funder_slot") or "").strip(),
                    "parent_first_funder_intermediate_source": (row.get("parent_first_funder_intermediate_source") or "").strip(),
                    "parent_first_funder": (row.get("parent_first_funder") or "").strip(),
                    "mechanism": (row.get("mechanism") or "").strip(),
                })
        _p3r_parent_funder_cache = {
            "path": path,
            "mtime": stat.st_mtime,
            "rows": rows,
        }
        return rows
    except OSError:
        return []


def _parent_funder_records_for(
    operator_id: str, member_mints: list[str] | None = None,
) -> list[dict[str, str]]:
    known_mints = set(member_mints or [])
    return [
        row for row in _load_p3r_parent_funder_rows()
        if (
            row.get("stored_candidate_parent_direct_funder") == operator_id
            or row.get("mint") in known_mints
        )
    ]


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
    rows = store.fetch_all_operators(exclude_rejected=not include_rejected, limit=limit) if include_rejected else store.fetch_active_manual_operators(limit=limit)
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


@operator_bp.route("/api/ops/operators/coverage-24h")
def operator_coverage_24h():
    """Read-only coverage of completed mints by confirmed-operation evidence."""
    from src.core.db import OPS_DB_PATH
    cutoff = int(time.time()) - 86400
    conn = sqlite3.connect(OPS_DB_PATH)
    try:
        rows = conn.execute("""
            SELECT q.mint, m.operator_id FROM wt_walkback_queue q
            LEFT JOIN operator_launch_membership m ON m.mint=q.mint
            LEFT JOIN operators o ON o.operator_id=m.operator_id
            LEFT JOIN operation_registry_dispositions d ON d.operator_id=m.operator_id
            WHERE q.status='complete' AND q.completed_at>=?
              AND (m.operator_id IS NULL OR d.disposition='ACTIVE_MANUAL')
        """, (cutoff,)).fetchall()
        eligible = {row[0] for row in rows}
        assigned = {row[0] for row in rows if row[1]}
        # Confirmed operations may surface current qualified telemetry before a
        # strict membership admission.  Include those retained observations in
        # coverage, but only as a deduplicated mint union and only when they
        # are already in the completed-mint denominator.
        from src.ops.live_potential_activity import aggregate as aggregate_live_activity
        from src.ops.potential_operations import CREATOR_PROVISIONING_CANDIDATE
        byzantine = {row[0] for row in conn.execute(
            "SELECT mint FROM wt_walkback_queue WHERE subprov=? AND status='complete' AND funder_block_time>=?",
            ("ByZc7RNeYowEg2jKo2giytWb9WmNyZPrQ1hXhnGSzHTY", cutoff),
        )}
        try:
            live_activity, _ = aggregate_live_activity(str(OPS_DB_PATH))
            nexus = {
                row["mint"] for row in live_activity.get(CREATOR_PROVISIONING_CANDIDATE, {}).get("live_matches", [])
                if row.get("funder_block_time", 0) >= cutoff
            }
        except (sqlite3.Error, OSError, ValueError, KeyError):
            nexus = set()
        covered = assigned | (byzantine & eligible) | (nexus & eligible)
        from src.utils.infra_mapping import get_funder_label
        funders = {row[0]: row[1] for row in conn.execute(
            "SELECT mint,funder_wallet FROM wt_walkback_queue WHERE mint IN (%s)" % ",".join("?" * len(eligible)), tuple(eligible)
        )} if eligible else {}
        cex_infra = {mint for mint in (eligible - covered) if funders.get(mint) and get_funder_label(funders[mint])}
        unknown = eligible - covered - cex_infra
        return jsonify({"ok": True, "cutoff": cutoff, "eligible": len(eligible),
                        "assigned": len(covered), "unassigned": len(eligible-covered),
                        "membership_assigned": len(assigned),
                        "byzantine_infrastructure_covered": len((byzantine & eligible) - assigned),
                        "nexus_telemetry_covered": len((nexus & eligible) - assigned),
                        "cex_infrastructure": len(cex_infra), "unknown": len(unknown),
                        "cex_infrastructure_percentage": round(100 * len(cex_infra) / len(eligible), 1) if eligible else None,
                        "unknown_percentage": round(100 * len(unknown) / len(eligible), 1) if eligible else None,
                        "coverage_semantics": "distinct completed mints with confirmed-operation membership or current qualified confirmed-operation telemetry",
                        "percentage": round(100 * len(covered) / len(eligible), 1) if eligible else None})
    finally:
        conn.close()


@operator_bp.route("/api/ops/operators/review-queue")
def operation_review_queue():
    store = _get_store()
    limit = min(int(request.args.get("limit", 12)), 100)
    rows = store.fetch_operation_review_queue(limit=limit)
    return jsonify({
        "ok": True,
        "candidates": rows,
        "count": len(rows),
        "generated_at": int(time.time()),
    })


@operator_bp.route("/api/ops/operators/<operator_id>/review-queue")
def operator_review_candidates(operator_id: str):
    store = _get_store()
    limit = min(int(request.args.get("limit", 500)), 1000)
    rows = store.fetch_operator_review_candidates(operator_id, limit=limit)
    return jsonify({
        "ok": True,
        "operator_id": operator_id,
        "candidates": rows,
        "count": len(rows),
        "generated_at": int(time.time()),
    })


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


@operator_bp.route("/api/ops/investigation/<path:address>/unified")
def unified_investigation(address: str):
    """Read-time-only unified projection (OPS-UI-P2): combines canonical
    operator state, main-DB historical population, and the new discovery-
    corpus evidence-qualification layer for one entity address. Never
    writes; authority_state always comes solely from operators.status."""
    store = _get_store()
    result = store.fetch_unified_investigation(address)
    result["ok"] = True
    result["generated_at"] = int(time.time())
    return jsonify(result)


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
    profile = op.get("behavioural_profile") or {}
    op["p3r_parent_funder_records"] = _parent_funder_records_for(
        operator_id, profile.get("member_mints"),
    )
    from src.ops.operation_summary import build_operation_summary
    # Provisional operations carry an explicit review-only evidence model
    # sourced from retained selected edges. Do not overwrite it with the
    # legacy behavioural-profile summary, which has no per-launch route data.
    if op.get("qualification_category") != "PROVISIONAL":
        op["summary_model"] = build_operation_summary(op, op["p3r_parent_funder_records"])
    return render_template("operator_intelligence.html",
                           operator_id=operator_id,
                           operator=op, error=None)


@operator_bp.route("/intelligence/operator/<operator_id>/subtypes/<subtype_id>")
def operator_subtype_page(operator_id: str, subtype_id: str):
    """Non-owning subtype projection; never reads or writes primary membership."""
    from flask import render_template
    db_path = Path(__file__).resolve().parents[2] / "database/wt_ops_v2.db"
    conn = sqlite3.connect(db_path); conn.row_factory = sqlite3.Row
    subtype = conn.execute("SELECT * FROM operator_subtypes WHERE subtype_id=? AND parent_operator_id=?", (subtype_id, operator_id)).fetchone()
    if not subtype:
        conn.close(); return render_template("operator_subtype_detail.html", subtype=None, error="Subtype not found"), 404
    overlap_count = conn.execute("SELECT COUNT(*) FROM operator_subtype_projection p JOIN operator_launch_membership m ON m.mint=p.mint AND m.operator_id=? WHERE p.subtype_id=?", (operator_id, subtype_id)).fetchone()[0]
    members = [dict(x) for x in conn.execute("SELECT p.*, 0 AS parent_owned, MAX(COALESCE(e.anchor_block_time,e.block_time)) observed_at FROM operator_subtype_projection p LEFT JOIN operator_launch_membership m ON m.mint=p.mint AND m.operator_id=? LEFT JOIN wt_walkback_edge_candidates e ON e.mint=p.mint WHERE p.subtype_id=? AND m.mint IS NULL GROUP BY p.mint,p.subtype_id,p.branch,p.evidence_reference ORDER BY observed_at DESC,p.mint", (operator_id, subtype_id))]
    now = int(time.time())
    timestamps = [member["observed_at"] for member in members if member.get("observed_at")]
    activity = {"latest": max(timestamps) if timestamps else None,
                "last_1d": sum(value >= now - 86400 for value in timestamps),
                "last_7d": sum(value >= now - 7 * 86400 for value in timestamps),
                "last_30d": sum(value >= now - 30 * 86400 for value in timestamps)}
    conn.close()
    evidence = __import__('json').loads(subtype['evidence_json'])
    return render_template("operator_subtype_detail.html", subtype=dict(subtype), members=members, overlap_count=overlap_count, evidence=evidence, activity=activity, error=None)


@operator_bp.route("/intelligence/operator/<operator_id>/review")
def operator_review_page(operator_id: str):
    from flask import render_template
    store = _get_store()
    op = store.fetch_operator(operator_id)
    if not op:
        return render_template("operator_walkback_review.html", operator_id=operator_id,
                               operator_name="Unknown operation", error="Operator not found"), 404
    return render_template("operator_walkback_review.html", operator_id=operator_id,
                           operator_name=op.get("display_name") or operator_id, error=None)


# ── Operator index page ─────────────────────────────────────────────────────────

@operator_bp.route("/intelligence/operators")
def operators_index():
    from flask import render_template
    return render_template("operators_index.html", active_page="operators")

@operator_bp.route("/intelligence/potential-operations")
def potential_operations_page():
    from flask import render_template
    from src.core.db import OPS_DB_PATH
    from src.ops.potential_operations import C357_CANDIDATE, rows, evolution_watch, activity_label
    from src.ops.generic_living_active_components import generic_dispatch_enabled
    import sqlite3
    projected=rows(str(OPS_DB_PATH))
    activity_by_candidate={row["candidate_id"]: {**row["current_evidence"], "creator_quality": row.get("creator_quality", {})} for row in projected}
    c=sqlite3.connect(str(OPS_DB_PATH)); c.row_factory=sqlite3.Row
    try:
        living=[]
        for op,name,candidate_id in (("potential-wsol-provision-close-100-sol-minus-15k","WSOL Close · 100 SOL minus 15k","p3r-v2-c357da9d0d4d560311e4"),("potential-eight-hop-plain-transfer-sequence","8-hop Plain Transfer Sequence","p3r-v2-dc4953db7adb853337c4")):
            # C357 is a resolved Leviathan subtype, not a potential operation.
            # Its legacy living record must not present Leviathan-owned launches
            # as a second prospective operation.
            if candidate_id == C357_CANDIDATE:
                continue
            current=c.execute("SELECT v.assessment_id,v.freshness_key FROM potential_operation_current x JOIN potential_operation_assessment_version v ON v.assessment_id=x.assessment_id WHERE x.potential_operation_id=?",(op,)).fetchone()
            if not current: continue
            history=c.execute("SELECT count(*) FROM potential_operation_assessment_version WHERE potential_operation_id=?",(op,)).fetchone()[0]
            associations=c.execute("SELECT count(*) FROM potential_operation_evidence_association WHERE potential_operation_id=?",(op,)).fetchone()[0]
            bindings=c.execute("SELECT count(*) FROM potential_operation_assessment_association_binding b JOIN potential_operation_assessment_version v ON v.assessment_id=b.assessment_id WHERE v.potential_operation_id=? AND v.assessment_id=?",(op,current['assessment_id'])).fetchone()[0]
            living.append(dict(operation_id=op,name=name,candidate_id=candidate_id,version=history,generation=current['freshness_key'],associations=associations,bindings=bindings,activity=activity_by_candidate.get(candidate_id)))
    finally: c.close()
    return render_template("potential_operations.html", active_page="potential_operations", rows=projected, evolution_watch=evolution_watch(projected), living=living, living_dispatch=generic_dispatch_enabled(), activity_label=activity_label)

@operator_bp.route("/intelligence/potential-operations/<candidate_id>")
def potential_operation_detail(candidate_id: str):
    from flask import render_template
    from src.core.db import OPS_DB_PATH
    from src.ops.potential_operations import (
        C357_CANDIDATE,
        C357_PARENT_OPERATOR,
        C357_SUBTYPE_ID,
        detail,
    )
    # C357 has been resolved as a Leviathan subtype.  The old potential-detail
    # page contains its frozen discovery cohort, including parent-owned mints;
    # send that legacy URL to the subtype projection, which excludes them.
    if candidate_id == C357_CANDIDATE:
        return redirect(
            f"/intelligence/operator/{C357_PARENT_OPERATOR}/subtypes/{C357_SUBTYPE_ID}",
            code=302,
        )
    candidate=detail(str(OPS_DB_PATH),candidate_id)
    if not candidate: return "Potential operation not found",404
    if candidate.get("legacy_child"):
        return render_template("potential_operation_legacy_child_detail.html", active_page="potential_operations", candidate=candidate)
    return render_template("potential_operation_detail.html", active_page="potential_operations", candidate=candidate)


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
        # OPS-UI-P3 discovery-intake candidates (family_id like "DFF_...")
        # are not registered in the emerging-operator service at all --
        # they are synthesized read-time from the discovery corpus (see
        # src/ops/discovery_intake.py). Without this fallback, clicking a
        # "NEW DISCOVERY" registry row 404s ("Record unavailable").
        from src.discovery.local_operation_discovery_projection import OUTPUT_DB
        from src.ops.discovery_intake import fetch_discovery_family_detail
        candidate = fetch_discovery_family_detail(OUTPUT_DB, entity)
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


# ── Discovery intake (OPS-UI-P3) ─────────────────────────────────────────────────

@operator_bp.route("/api/ops/discovery-intake-candidates")
def discovery_intake_candidates():
    """Read-only, bounded (<=20) discovery-corpus candidates meeting the
    P1-qualified deterministic intake criteria, shaped for the EXISTING
    Analyst Queue rendering path (familyRow). Never writes, never
    promotes, never merges into any canonical operator. Any candidate
    whose root already appears in a known operator's entity list is
    flagged known_operation_overlap=true rather than silently merged or
    hidden (Part 14 guard)."""
    from src.core.db import DB_PATH  # noqa: F401  (kept for parity/documentation of the DB set this route reads from)
    from src.discovery.local_operation_discovery_projection import OUTPUT_DB
    from src.ops.discovery_intake import fetch_discovery_intake_candidates

    store = _get_store()
    known_entities = frozenset()
    try:
        with store._connect() as conn:  # noqa: SLF001 -- read-only reuse of the existing connection helper
            known_entities = frozenset(r[0] for r in conn.execute("SELECT entity_address FROM operator_entities"))
    except Exception:
        known_entities = frozenset()

    candidates = fetch_discovery_intake_candidates(OUTPUT_DB, known_operator_entities=known_entities)
    return jsonify({
        "ok": True,
        "candidates": candidates,
        "count": len(candidates),
        "generated_at": int(time.time()),
    })


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
