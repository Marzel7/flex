"""X74.1 Treasury Review & Identity Expansion Workspace.

Treasury review is an analyst investigation workflow, not an approve/reject
queue. This module is the single service surface for it: it composes
analyst-readable evidence for each `wt_treasury_review` row, exposes the
review actions, and bridges an "Approve Treasury" decision into the existing
canonical write paths — `treasury_bank.promote_to_confirmed()` (the
authoritative confirmed-set writer) and, when the analyst is expanding an
already-CONFIRMED Operator Identity such as WATCHTOWER,
`OperatorIdentityGovernanceService.expand()` (the immutable identity-event
ledger). It does not replace either of those; it is presentation +
orchestration on top of what already exists.

Every mutating action is recorded in `wt_treasury_review_actions`, an
immutable (trigger-protected) append-only audit log, mirroring the
`operator_identity_events` convention.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any

try:
    from src.utils.db_locking import db_connect
except Exception:                                        # pragma: no cover
    import sqlite3
    def db_connect(path, timeout=30):
        c = sqlite3.connect(path, timeout=timeout)
        c.row_factory = sqlite3.Row
        return c

from src.core import treasury_bank
from src.ops.operation_matching_profile import get_profile
from src.ops.watchtower_alignment import WATCHTOWER_OPERATOR_ID


AUDIT_DDL = """
CREATE TABLE IF NOT EXISTS wt_treasury_review_actions (
    action_id TEXT PRIMARY KEY,
    treasury TEXT NOT NULL,
    action TEXT NOT NULL,
    analyst TEXT NOT NULL,
    reason TEXT NOT NULL,
    evidence_revision TEXT NOT NULL,
    result_json TEXT NOT NULL DEFAULT '{}',
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_wtra_treasury
ON wt_treasury_review_actions(treasury, created_at);
CREATE TRIGGER IF NOT EXISTS wt_treasury_review_actions_immutable_update
BEFORE UPDATE ON wt_treasury_review_actions BEGIN
 SELECT RAISE(ABORT, 'treasury review action history is immutable');
END;
CREATE TRIGGER IF NOT EXISTS wt_treasury_review_actions_immutable_delete
BEFORE DELETE ON wt_treasury_review_actions BEGIN
 SELECT RAISE(ABORT, 'treasury review action history is immutable');
END;
"""

ACTIONS = frozenset({
    "APPROVE_TREASURY", "REJECT_TREASURY", "NEEDS_MORE_EVIDENCE",
    "LINK_TO_OPERATOR", "CREATE_INVESTIGATION", "CREATE_OPERATOR_CANDIDATE",
})


class WorkspaceError(ValueError):
    def __init__(self, message: str, code: str = "INVALID_ACTION", status: int = 400):
        super().__init__(message)
        self.code, self.status = code, status


def ensure_schema(conn) -> None:
    conn.executescript(AUDIT_DDL)
    conn.commit()


def _load_json(value) -> list:
    try:
        return json.loads(value) if value else []
    except Exception:
        return []


def _related_identity(conn, treasury: str) -> dict[str, Any]:
    """Determine what this treasury is already associated with, if anything."""
    row = conn.execute(
        "SELECT oe.operator_id, o.display_name, o.status "
        "FROM operator_entities oe JOIN operators o ON o.operator_id=oe.operator_id "
        "WHERE oe.entity_address=? AND o.status<>'REJECTED' "
        "ORDER BY (oe.operator_id=?) DESC LIMIT 1",
        (treasury, WATCHTOWER_OPERATOR_ID),
    ).fetchone()
    if row:
        return {
            "kind": "WATCHTOWER" if row["operator_id"] == WATCHTOWER_OPERATOR_ID else "OPERATION",
            "operator_id": row["operator_id"],
            "display_name": row["display_name"],
            "status": row["status"],
        }
    if conn.execute("SELECT 1 FROM wt_discovered_subprovs WHERE treasury=? LIMIT 1", (treasury,)).fetchone():
        return {"kind": "INFRASTRUCTURE", "operator_id": None, "display_name": None, "status": None}
    return {"kind": "UNKNOWN", "operator_id": None, "display_name": None, "status": None}


def _evidence_summary(row: dict[str, Any], counts: dict[str, int]) -> dict[str, Any]:
    """Analyst-readable — no raw SQL/column dumps."""
    supporting: list[str] = []
    contradictions: list[str] = []
    missing: list[str] = []

    transfer_pct = row.get("transfer_pct")
    out_sol = row.get("out_sol") or 0
    recipients = row.get("recipients") or 0
    micro_pings = row.get("micro_pings") or 0
    detected_via = row.get("detected_via") or "unknown"

    why = f"Surfaced via {detected_via.replace('_', ' ')}"
    if row.get("has_walkback_evidence"):
        why += "; confirmed walkback lineage reached this wallet as an unresolved upstream hop."
    else:
        why += "."

    if transfer_pct is not None:
        if transfer_pct >= 95:
            supporting.append(f"{transfer_pct}% of observed activity is plain transfers (treasury-shaped, not a trading wallet).")
        else:
            contradictions.append(f"Only {transfer_pct}% transfer-pure — some swap/trade activity observed, atypical for a treasury.")
    else:
        missing.append("No transfer-purity fingerprint recorded yet.")

    if out_sol >= 50 and recipients >= 3:
        supporting.append(f"Distributed {out_sol:.0f} SOL to {recipients} recipients — matches treasury capital scale.")
    elif recipients:
        missing.append(f"Capital scale below the usual treasury threshold ({out_sol:.0f} SOL to {recipients} recipients).")
    elif out_sol:
        missing.append(f"{out_sol:.0f} SOL of funding observed, but no distributed-recipient breadth recorded yet (recipient count not measured for walkback-sourced leads).")
    else:
        missing.append("No capital-scale signal recorded.")

    if micro_pings:
        supporting.append(f"Carries the coordination-ping signature ({micro_pings} observed).")
    else:
        missing.append("No coordination-ping signature observed yet.")

    if counts.get("wrap_close", 0):
        supporting.append(f"{counts['wrap_close']} wrap-close observation(s) trace lineage back to this wallet.")
    if counts.get("subprovs", 0):
        supporting.append(f"{counts['subprovs']} distinct sub-provisioner(s) reachable downstream of this wallet.")
    if counts.get("launches", 0):
        supporting.append(f"{counts['launches']} token launch(es) attributable through this lineage.")
    else:
        missing.append("No completed launch has yet traced back to this wallet.")

    return {
        "why_proposed": why,
        "supporting_evidence": supporting,
        "contradictions": contradictions,
        "missing_evidence": missing,
    }


def _counts_for_treasury(conn, treasury: str, evidence_subprovs: list[str], evidence_mints: list[str]) -> dict[str, int]:
    wrap_close = 0
    try:
        wrap_close = conn.execute(
            "SELECT COUNT(*) FROM wt_wrap_close_candidates WHERE lineage_source_treasury=?",
            (treasury,),
        ).fetchone()[0]
    except Exception:
        pass
    subprovs = len(evidence_subprovs) if evidence_subprovs else 0
    try:
        row = conn.execute(
            "SELECT COUNT(DISTINCT subprov) FROM wt_discovered_subprovs WHERE treasury=?",
            (treasury,),
        ).fetchone()
        if row and row[0]:
            subprovs = max(subprovs, row[0])
    except Exception:
        pass
    return {"wrap_close": wrap_close, "subprovs": subprovs, "launches": len(evidence_mints)}


def _priority_score(row: dict[str, Any], counts: dict[str, int]) -> float:
    """Analyst-value ranking, not age. Weighted toward what would most likely
    expand WATCHTOWER: launch evidence > wrap-close observations > subprov
    breadth > walkback presence > raw capital scale > recency."""
    score = 0.0
    score += counts.get("launches", 0) * 100
    score += counts.get("wrap_close", 0) * 20
    score += counts.get("subprovs", 0) * 10
    score += 15 if row.get("has_walkback_evidence") else 0
    score += min((row.get("out_sol") or 0) / 10, 20)
    score += 5 if (row.get("distinct_creators") or 1) > 1 else 0
    return score


def _observed_topology(treasury: str, subprovs: list[str], creators: list[str],
                       mints: list[str], counts: dict[str, int]) -> dict[str, Any]:
    """Describe only hops proven by the persisted review projection."""
    nodes = [{"role": "Treasury", "count": 1, "current": True}]
    if subprovs or counts.get("subprovs"):
        nodes.append({"role": "Subprovider / Provisioning Wallet", "count": counts.get("subprovs") or len(subprovs)})
    if creators:
        nodes.append({"role": "Creator", "count": len(set(creators))})
    if mints:
        nodes.append({"role": "Launch", "count": len(set(mints))})
    return {"nodes": nodes, "label": " → ".join(n["role"] for n in nodes),
            "evidence_backed": len(nodes) > 1, "anchor": treasury}


def _reuse_pattern(provisioners: int, launches: int) -> str | None:
    """A small observed behaviour vocabulary shared by both sides."""
    if launches < 2 or provisioners < 1:
        return None
    ratio = provisioners / launches
    if ratio >= 0.75:
        return "ROTATING_PROVISIONERS"
    if ratio <= 0.25:
        return "PERSISTENT_PROVISIONER"
    return "MIXED_PROVISIONER_REUSE"


def _candidate_comparison_evidence(conn, treasury: str, subprovs: list[str],
                                   creators: list[str], mints: list[str]) -> dict[str, Any]:
    """Project only persisted, transaction-derived candidate observations."""
    edges = []
    if _table_exists(conn, "wt_provisioning_edges"):
        edges = [dict(row) for row in conn.execute(
            "SELECT edge_type,funding_mechanism,funding_tx_signature,source_mint "
            "FROM wt_provisioning_edges WHERE from_wallet=? AND edge_type='TREASURY_TO_SUBPROV'",
            (treasury,),
        ).fetchall()]
    mint_set = set(mints)
    scoped = [edge for edge in edges if edge.get("source_mint") in mint_set] if mint_set else []
    mechanisms = {str(edge["funding_mechanism"]).upper() for edge in scoped
                  if edge.get("funding_mechanism") and edge.get("funding_tx_signature")}
    roles = {"TREASURY"}
    if subprovs:
        roles.add("SUB_PROVISIONER")
    if creators:
        roles.add("CREATOR")
    if mints:
        roles.add("LAUNCH")
    return {
        "roles": roles,
        "funding_mechanisms": mechanisms,
        "provisioning_role": "SUB_PROVISIONER" if subprovs else None,
        "behaviour_pattern": _reuse_pattern(len(set(subprovs)), len(set(mints))),
        "transaction_edges": len(scoped),
        "settlement": None,
    }


def _operator_reference_evidence(conn, operator_id: str, display_name: str,
                                 entities: set[str]) -> dict[str, Any]:
    """Build the comparison reference from canonical entities and clean edges."""
    profile = get_profile(operator_id, display_name)
    roles = set(profile.defining_entity_types)
    mechanisms: set[str] = set()
    launch_count = 0
    provisioners: set[str] = set()

    if display_name.upper() == "WATCHTOWER" and _table_exists(conn, "wt_watchtower_launches"):
        rows = conn.execute(
            "SELECT mint,subprov_wallet,funding_mechanism FROM wt_watchtower_launches"
        ).fetchall()
        launch_count = len({row["mint"] for row in rows if row["mint"]})
        provisioners = {row["subprov_wallet"] for row in rows if row["subprov_wallet"]}
        mechanisms.update(str(row["funding_mechanism"]).upper() for row in rows
                          if row["funding_mechanism"])
        if launch_count:
            roles.update({"CREATOR", "LAUNCH"})

    if entities and _table_exists(conn, "wt_provisioning_edges"):
        marks = ",".join("?" for _ in entities)
        edge_rows = conn.execute(
            f"SELECT edge_type,from_wallet,to_wallet,source_mint,funding_mechanism,"
            f"funding_tx_signature FROM wt_provisioning_edges "
            f"WHERE from_wallet IN ({marks}) OR to_wallet IN ({marks})",
            [*entities, *entities],
        ).fetchall()
        verified_edges = [row for row in edge_rows if row["funding_tx_signature"]]
        mechanisms.update(str(row["funding_mechanism"]).upper() for row in verified_edges
                          if row["funding_mechanism"])
        edge_mints = {row["source_mint"] for row in verified_edges if row["source_mint"]}
        launch_count = max(launch_count, len(edge_mints))
        if edge_mints:
            roles.update({"CREATOR", "LAUNCH"})
        for row in verified_edges:
            if row["edge_type"] == "SUBPROV_TO_CREATOR":
                provisioners.add(row["from_wallet"])

    provisioning_role = None
    if "SUB_PROVISIONER" in profile.defining_entity_types:
        provisioning_role = "SUB_PROVISIONER"
    elif "CLIENT" in profile.defining_entity_types:
        provisioning_role = "CLIENT"
    return {
        "roles": roles,
        "funding_mechanisms": mechanisms,
        "provisioning_role": provisioning_role,
        "behaviour_pattern": _reuse_pattern(len(provisioners), launch_count),
        "settlement": None,
        "profile_description": profile.description,
    }


def _comparison_state(candidate, reference, *, partial_on_overlap: bool = True) -> str:
    if not candidate or not reference:
        return "UNKNOWN"
    if isinstance(candidate, set) and isinstance(reference, set):
        overlap = candidate & reference
        if not overlap:
            return "NO_MATCH"
        if candidate <= reference or reference <= candidate:
            return "MATCH"
        return "PARTIAL" if partial_on_overlap else "MATCH"
    return "MATCH" if candidate == reference else "NO_MATCH"


def _confirmed_operation_references(conn) -> list[dict[str, Any]]:
    references = []
    if not (_table_exists(conn, "operators") and _table_exists(conn, "operator_entities")):
        return references
    for op in conn.execute(
        "SELECT operator_id, display_name FROM operators WHERE status='CONFIRMED'"
    ).fetchall():
        entities = {row[0] for row in conn.execute(
            "SELECT entity_address FROM operator_entities WHERE operator_id=?",
            (op["operator_id"],),
        ).fetchall()}
        references.append({
            "operator_id": op["operator_id"],
            "display_name": op["display_name"] or op["operator_id"],
            "entities": entities,
            "evidence": _operator_reference_evidence(
                conn, op["operator_id"], op["display_name"] or op["operator_id"], entities
            ),
        })
    return references


def _operation_matches(conn, treasury: str, subprovs: list[str], creators: list[str],
                       mints: list[str], counts: dict[str, int],
                       operation_references: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Compare compatible persisted evidence; UNKNOWN means not evaluated."""
    if not (_table_exists(conn, "operators") and _table_exists(conn, "operator_entities")):
        return []
    evidence_wallets = {treasury, *subprovs, *creators}
    candidate = _candidate_comparison_evidence(conn, treasury, subprovs, creators, mints)
    matches = []
    for op in operation_references or _confirmed_operation_references(conn):
        entities = op["entities"]
        overlap = sorted(evidence_wallets & entities)
        treasury_known = treasury in entities
        downstream = len(set(subprovs + creators) & entities)
        reference = op["evidence"]
        states = {
            "Behaviour": _comparison_state(candidate["behaviour_pattern"], reference["behaviour_pattern"]),
            "Funding": _comparison_state(candidate["funding_mechanisms"], reference["funding_mechanisms"]),
            "Provisioning": _comparison_state(candidate["provisioning_role"], reference["provisioning_role"]),
            "Settlement": "UNKNOWN",
            "Topology": _comparison_state(candidate["roles"], reference["roles"]),
            # A non-overlap is not a contradiction: this may be a genuinely new
            # expansion treasury. Exact identity evidence is required for MATCH.
            "Treasury": "MATCH" if treasury_known else "PARTIAL" if downstream else "UNKNOWN",
        }
        evaluated = [key for key, value in states.items() if value != "UNKNOWN"]
        matched_dimensions = [key for key, value in states.items() if value == "MATCH"]
        partial_dimensions = [key for key, value in states.items() if value == "PARTIAL"]
        contradicted_dimensions = [key for key, value in states.items() if value == "NO_MATCH"]
        aligned = len(matched_dimensions) + len(partial_dimensions)
        explicit_identity_overlap = bool(overlap)
        if not evaluated:
            overall = "NOT_EVALUATED"
        elif explicit_identity_overlap and aligned:
            overall = "MATCH"
        elif aligned:
            overall = "PARTIAL"
        else:
            overall = "NO_MATCH"
        matches.append({
            "operator_id": op["operator_id"], "display_name": op["display_name"] or op["operator_id"],
            "operator_href": f"/intelligence/operators/{op['operator_id']}",
            "overlap_accounts": overlap,
            "matched": explicit_identity_overlap,
            "comparison_state": overall,
            "states": states,
            "evaluated_dimensions": evaluated,
            "matched_dimensions": matched_dimensions,
            "partial_dimensions": partial_dimensions,
            "contradicted_dimensions": contradicted_dimensions,
            "unknown_dimensions": [key for key, value in states.items() if value == "UNKNOWN"],
            "aligned_dimensions": aligned,
            "reference_description": reference["profile_description"],
        })
    order = {"MATCH": 0, "PARTIAL": 1, "NO_MATCH": 2, "NOT_EVALUATED": 3}
    matches.sort(key=lambda m: (
        order[m["comparison_state"]], -m["aligned_dimensions"],
        m["display_name"],
    ))
    return matches


def _relationship_examples(conn, treasury: str, subprovs: list[str], creators: list[str],
                           mints: list[str], limit: int = 5) -> list[dict[str, Any]]:
    """Newest persisted relationship examples; arrays are evidence context, not inferred joins."""
    rows = []
    if _table_exists(conn, "wt_wrap_close_candidates"):
        rows = [dict(r) for r in conn.execute(
            "SELECT subprov_wallet, creator, tx_signature, funding_mechanism, funded_at "
            "FROM wt_wrap_close_candidates WHERE lineage_source_treasury=? "
            "ORDER BY COALESCE(funded_at, detected_at) DESC LIMIT ?", (treasury, limit)
        ).fetchall()]
    examples = []
    for index, record in enumerate(rows):
        creator = record.get("creator")
        mint = mints[index] if index < len(mints) else None
        examples.append({
            "treasury": treasury, "subprovider": record.get("subprov_wallet"), "creator": creator,
            "launch": mint, "transaction": record.get("tx_signature"),
            "mechanism": record.get("funding_mechanism"), "observed_at": record.get("funded_at"),
        })
    if not examples:
        for index in range(min(limit, max(len(subprovs), len(creators), len(mints)))):
            examples.append({"treasury": treasury,
                "subprovider": subprovs[index] if index < len(subprovs) else None,
                "creator": creators[index] if index < len(creators) else None,
                "launch": mints[index] if index < len(mints) else None,
                "transaction": None, "mechanism": None, "observed_at": None})
    return examples


def _governance_recommendation(row: dict[str, Any], counts: dict[str, int], matches: list[dict[str, Any]]) -> dict[str, str]:
    matched = [item for item in matches if item.get("matched")]
    if matched:
        top = matched[0]
        action = "Expand " + top["display_name"] if top["states"]["Treasury"] != "MATCH" else "Link to " + top["display_name"]
        code = "APPROVE_TREASURY" if top["states"]["Treasury"] != "MATCH" else "LINK_TO_OPERATOR"
    elif counts.get("launches") and counts.get("subprovs"):
        action, code = "Create Investigation", "CREATE_INVESTIGATION"
    elif counts.get("wrap_close") or row.get("has_walkback_evidence"):
        action, code = "Needs More Evidence", "NEEDS_MORE_EVIDENCE"
    else:
        action, code = "Reject", "REJECT_TREASURY"
    return {"label": action, "action": code, "operator_id": matched[0]["operator_id"] if matched else None}


def _comparison_triage(matches: list[dict[str, Any]]) -> dict[str, Any]:
    """Deterministic, categorical ordering; no opaque numeric score."""
    if any(match["comparison_state"] == "MATCH" for match in matches):
        key, label, rank = "CONFIRMED_OPERATION_MATCH", "Confirmed Operation comparison found", 1
    elif any(match["comparison_state"] == "PARTIAL" and match["aligned_dimensions"] >= 3
             for match in matches):
        key, label, rank = "PARTIAL_OPERATION_MATCH", "Partial Operation comparison found", 2
    elif any(match["aligned_dimensions"] >= 2 for match in matches):
        key, label, rank = "MULTI_DIMENSION_ALIGNMENT", "Multiple evaluated dimensions align", 3
    elif any(match["evaluated_dimensions"] for match in matches):
        key, label, rank = "EVALUATED_NO_ALIGNMENT", "Evaluated with no Operation alignment", 4
    else:
        key, label, rank = "NO_COMPARABLE_EVIDENCE", "No comparable evidence", 5
    return {"key": key, "label": label, "sort_rank": rank}


def compose_review_item(conn, row: dict[str, Any], *,
                        operation_references: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    treasury = row["treasury"]
    evidence_subprovs = _load_json(row.get("evidence_subprovs"))
    evidence_creators = _load_json(row.get("evidence_creators"))
    evidence_mints = _load_json(row.get("evidence_mints"))
    counts = _counts_for_treasury(conn, treasury, evidence_subprovs, evidence_mints)
    identity = _related_identity(conn, treasury)
    evidence = _evidence_summary(row, counts)
    topology = _observed_topology(treasury, evidence_subprovs, evidence_creators, evidence_mints, counts)
    matches = _operation_matches(
        conn, treasury, evidence_subprovs, evidence_creators, evidence_mints, counts,
        operation_references,
    )
    comparison_triage = _comparison_triage(matches)
    recommendation = _governance_recommendation(row, counts, matches)
    recent_actions = [dict(r) for r in conn.execute(
        "SELECT action, analyst, reason, created_at FROM wt_treasury_review_actions "
        "WHERE treasury=? ORDER BY created_at DESC LIMIT 5", (treasury,),
    ).fetchall()] if _table_exists(conn, "wt_treasury_review_actions") else []
    return {
        "treasury": treasury,
        "status": row.get("status"),
        "evidence_summary": evidence,
        "observed_topology": topology,
        "operation_matches": matches,
        "comparison_triage": comparison_triage,
        "recommended_action": recommendation,
        "relationship_examples": _relationship_examples(
            conn, treasury, evidence_subprovs, evidence_creators, evidence_mints
        ),
        "walkback_depth": max(len(evidence_subprovs), 1 if row.get("has_walkback_evidence") else 0),
        "subprovider_count": counts["subprovs"],
        "wrap_close_observations": counts["wrap_close"],
        "launch_count": counts["launches"],
        "investigation_population": {
            "subprovs": evidence_subprovs,
            "creators": evidence_creators,
            "mints": evidence_mints,
        },
        "related_identity": identity,
        "first_observed": row.get("detected_at"),
        "last_observed": row.get("last_walkback_at") or row.get("detected_at"),
        "recent_actions": recent_actions,
        "priority_score": _priority_score(row, counts),
        "watchtower_candidate": counts["wrap_close"] > 0 or counts["launches"] > 0 or bool(row.get("has_walkback_evidence")),
        # X75.3A PART 6 -- navigation: every review item links forward to
        # Discovery's per-entity view for this wallet, so an analyst never
        # has to leave the review workspace to see the same wallet's
        # canonical identity / structural-population context.
        "discovery_href": f"/discovery?entity={treasury}&type=treasury",
    }


def _table_exists(conn, table: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone())


def list_review_workspace(conn, *, status: str = "PENDING_REVIEW", sort: str = "actionable",
                          limit: int = 20, offset: int = 0) -> dict[str, Any]:
    ensure_schema(conn)
    treasury_bank._ensure_schema_once(conn)
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM wt_treasury_review WHERE status=? ORDER BY detected_at DESC",
        (status,),
    ).fetchall()]
    operation_references = _confirmed_operation_references(conn)
    items = [compose_review_item(conn, row, operation_references=operation_references) for row in rows]
    if sort == "newest":
        items.sort(key=lambda i: (-(i["last_observed"] or 0), i["treasury"]))
    elif sort == "oldest":
        items.sort(key=lambda i: (i["first_observed"] or 0, i["treasury"]))
    elif sort == "launches":
        items.sort(key=lambda i: (-(i["launch_count"] or 0), -(i["last_observed"] or 0), i["treasury"]))
    else:
        # ACTIONABLE FIRST, then newest evidence within the categorical group.
        items.sort(key=lambda i: (
            i["comparison_triage"]["sort_rank"],
            -(i["last_observed"] or 0),
            i["treasury"],
        ))
    all_items = items
    total_items = len(all_items)
    offset = max(0, int(offset))
    items = all_items[offset:offset + max(limit, 1)]

    counts_row = conn.execute(
        "SELECT status, COUNT(*) FROM wt_treasury_review GROUP BY status"
    ).fetchall()
    status_counts = {r[0]: r[1] for r in counts_row}
    ages = [r[0] for r in conn.execute(
        "SELECT detected_at FROM wt_treasury_review WHERE status='PENDING_REVIEW' AND detected_at IS NOT NULL"
    ).fetchall()]
    now = int(time.time())
    watchtower_candidates = sum(1 for i in items if i["watchtower_candidate"])

    triage_counts: dict[str, int] = {}
    for item in all_items:
        key = item["comparison_triage"]["key"]
        triage_counts[key] = triage_counts.get(key, 0) + 1

    return {
        "items": items,
        "offset": offset,
        "limit": limit,
        "returned": len(items),
        "total_items": total_items,
        "has_more": offset + len(items) < total_items,
        "sort_contract": "ACTIONABLE_FIRST_THEN_NEWEST_WITHIN_GROUP" if sort not in {"newest", "oldest", "launches"} else sort.upper(),
        "triage_counts": triage_counts,
        "status_counts": status_counts,
        "pending_total": status_counts.get("PENDING_REVIEW", 0),
        "newest_pending_age_secs": (now - max(ages)) if ages else None,
        "oldest_pending_age_secs": (now - min(ages)) if ages else None,
        "watchtower_candidates": watchtower_candidates,
        "requires_attention": status_counts.get("PENDING_REVIEW", 0) > 0,
        "generated_at": now,
    }


def _record_action(conn, treasury: str, action: str, analyst: str, reason: str,
                   evidence_revision: str, result: dict[str, Any]) -> str:
    action_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO wt_treasury_review_actions "
        "(action_id, treasury, action, analyst, reason, evidence_revision, result_json, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (action_id, treasury, action, analyst, reason, evidence_revision, json.dumps(result), int(time.time())),
    )
    return action_id


def _metadata(payload: dict[str, Any]) -> tuple[str, str, str]:
    analyst = str(payload.get("analyst") or "").strip()
    reason = str(payload.get("reason") or "").strip()
    revision = str(payload.get("evidence_revision") or f"treasury-review:{int(time.time())}").strip()
    if not analyst or not reason:
        raise WorkspaceError("analyst and reason are required", "ACTION_METADATA_REQUIRED")
    return analyst, reason, revision


def approve_treasury(conn, treasury: str, payload: dict[str, Any], *,
                     governance_service=None) -> dict[str, Any]:
    """Approve a treasury. If the analyst names a target Operator Identity
    (payload['operator_id'], defaulting to WATCHTOWER), the treasury is both
    (a) promoted into the authoritative confirmed set via
    treasury_bank.promote_to_confirmed — which already reconciles ownership
    into operator_entities for WATCHTOWER — and (b) recorded as an explicit
    identity-expansion event via OperatorIdentityGovernanceService.expand,
    so the immutable identity ledger carries this decision too. Never
    recreates or duplicates the Operator; expand() is itself idempotent
    (INSERT OR IGNORE on the asset, always appends a new event)."""
    ensure_schema(conn)
    analyst, reason, revision = _metadata(payload)
    operator_id = str(payload.get("operator_id") or WATCHTOWER_OPERATOR_ID).strip()

    promotion = treasury_bank.promote_to_confirmed(conn, treasury, reviewed_by=analyst, reason=reason)
    if not promotion.get("ok"):
        raise WorkspaceError(promotion.get("error", "promotion failed"), "PROMOTION_FAILED", 409)

    expansion = None
    if operator_id:
        gov = governance_service
        if gov is None:
            from src.core.db import OPS_DB_PATH
            from src.ops.operator_identity_governance import OperatorIdentityGovernanceService
            gov = OperatorIdentityGovernanceService(str(OPS_DB_PATH))
        try:
            expansion = gov.expand(operator_id, {
                "analyst": analyst, "reason": reason, "evidence_revision": revision,
                "asset_type": "TREASURY", "asset_value": treasury,
            })
        except Exception as exc:
            # Promotion (authoritative) already succeeded; identity-ledger
            # expansion is additive and must never roll that back.
            expansion = {"error": str(exc)}

    # X76.2 -- promote_to_confirmed() itself now writes the immutable
    # APPROVE_TREASURY audit event (same transaction as its own mutable
    # status update) -- see src/core/treasury_bank.py. Do not also record
    # it here; that would double the audit row for this one path while
    # every OTHER caller of promote_to_confirmed() (the older
    # operation_dashboard_routes.py surfaces) still gets exactly one.
    result = {"promotion": promotion, "identity_expansion": expansion}
    conn.commit()
    return {"ok": True, "treasury": treasury, **result}


def reject_treasury(conn, treasury: str, payload: dict[str, Any]) -> dict[str, Any]:
    ensure_schema(conn)
    analyst, reason, revision = _metadata(payload)
    # X76.2 -- reject_candidate() itself now writes the immutable
    # REJECT_TREASURY audit event; see the note in approve_treasury() above.
    result = treasury_bank.reject_candidate(conn, treasury, reviewed_by=analyst, reason=reason)
    conn.commit()
    return {"ok": True, "treasury": treasury, **result}


def needs_more_evidence(conn, treasury: str, payload: dict[str, Any]) -> dict[str, Any]:
    ensure_schema(conn)
    analyst, reason, revision = _metadata(payload)
    if not conn.execute("SELECT 1 FROM wt_treasury_review WHERE treasury=?", (treasury,)).fetchone():
        raise WorkspaceError("not in review queue", "NOT_FOUND", 404)
    # Stays PENDING_REVIEW — this is an annotation, not a status transition.
    result = {"noted": True}
    _record_action(conn, treasury, "NEEDS_MORE_EVIDENCE", analyst, reason, revision, result)
    conn.commit()
    return {"ok": True, "treasury": treasury, **result}


def link_to_existing_operator(conn, treasury: str, payload: dict[str, Any], *,
                              governance_service=None) -> dict[str, Any]:
    """Attach this treasury as evidence for a NON-WATCHTOWER confirmed
    Operator — i.e. this treasury belongs to a different, already-known
    Operation, not a rejection and not WATCHTOWER."""
    ensure_schema(conn)
    analyst, reason, revision = _metadata(payload)
    operator_id = str(payload.get("operator_id") or "").strip()
    if not operator_id:
        raise WorkspaceError("operator_id is required", "OPERATOR_ID_REQUIRED")
    gov = governance_service
    if gov is None:
        from src.core.db import OPS_DB_PATH
        from src.ops.operator_identity_governance import OperatorIdentityGovernanceService
        gov = OperatorIdentityGovernanceService(str(OPS_DB_PATH))
    expansion = gov.expand(operator_id, {
        "analyst": analyst, "reason": reason, "evidence_revision": revision,
        "asset_type": "TREASURY", "asset_value": treasury,
    })
    conn.execute(
        "UPDATE wt_treasury_review SET status='LINKED', reviewed_by=?, reviewed_at=? WHERE treasury=?",
        (analyst, int(time.time()), treasury),
    )
    result = {"identity_expansion": expansion}
    _record_action(conn, treasury, "LINK_TO_OPERATOR", analyst, reason, revision, result)
    conn.commit()
    return {"ok": True, "treasury": treasury, **result}


def create_investigation(conn, treasury: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Evidence supports something worth tracking, but not yet an Operator
    Identity confirmation or expansion. Leaves the treasury visibly flagged
    (status=INVESTIGATING) without forcing it into WATCHTOWER or any
    existing Operation."""
    ensure_schema(conn)
    analyst, reason, revision = _metadata(payload)
    if not conn.execute("SELECT 1 FROM wt_treasury_review WHERE treasury=?", (treasury,)).fetchone():
        raise WorkspaceError("not in review queue", "NOT_FOUND", 404)
    conn.execute(
        "UPDATE wt_treasury_review SET status='INVESTIGATING', reviewed_by=?, reviewed_at=? WHERE treasury=?",
        (analyst, int(time.time()), treasury),
    )
    result = {"status": "INVESTIGATING"}
    _record_action(conn, treasury, "CREATE_INVESTIGATION", analyst, reason, revision, result)
    conn.commit()
    return {"ok": True, "treasury": treasury, **result}


def create_operator_candidate(conn, treasury: str, payload: dict[str, Any], *,
                              governance_service=None) -> dict[str, Any]:
    """Evidence supports a NEW Operation rather than an existing one or
    WATCHTOWER. Creates a fresh CANDIDATE operator seeded with this treasury
    as its first asset, via the existing identity-governance machinery
    (ensure_state + expand), never duplicating an existing operator.

    The `operators` insert is committed on `conn` BEFORE the governance
    service touches it: OperatorIdentityGovernanceService writes through
    database_write_service, which opens its own dedicated connection to the
    ops DB file — it cannot see an uncommitted row from `conn`'s own
    transaction."""
    ensure_schema(conn)
    analyst, reason, revision = _metadata(payload)
    display_name = str(payload.get("display_name") or f"Candidate Operation — {treasury[:8]}").strip()
    now = int(time.time())
    operator_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"treasury-review-candidate:{treasury}"))
    existing = conn.execute("SELECT 1 FROM operators WHERE operator_id=?", (operator_id,)).fetchone()
    if not existing:
        conn.execute(
            "INSERT INTO operators(operator_id,status,confidence,first_seen,last_seen,summary,"
            "review_state,display_name,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (operator_id, "CANDIDATE", "REVIEW", now, now,
             f"Candidate operation seeded from treasury review: {reason}",
             "NEEDS_REVIEW", display_name, now, now),
        )
        conn.commit()
    gov = governance_service
    if gov is None:
        from src.core.db import OPS_DB_PATH
        from src.ops.operator_identity_governance import OperatorIdentityGovernanceService
        gov = OperatorIdentityGovernanceService(str(OPS_DB_PATH))
    gov._mutate("operator-identity-ensure-candidate", lambda c: gov._ensure_state(c, operator_id))
    conn.execute(
        "UPDATE wt_treasury_review SET status='LINKED', reviewed_by=?, reviewed_at=? WHERE treasury=?",
        (analyst, now, treasury),
    )
    result = {"operator_id": operator_id, "display_name": display_name}
    _record_action(conn, treasury, "CREATE_OPERATOR_CANDIDATE", analyst, reason, revision, result)
    conn.commit()
    return {"ok": True, "treasury": treasury, **result}


DISPATCH = {
    "APPROVE_TREASURY": approve_treasury,
    "REJECT_TREASURY": reject_treasury,
    "NEEDS_MORE_EVIDENCE": needs_more_evidence,
    "LINK_TO_OPERATOR": link_to_existing_operator,
    "CREATE_INVESTIGATION": create_investigation,
    "CREATE_OPERATOR_CANDIDATE": create_operator_candidate,
}


def perform_action(conn, treasury: str, action: str, payload: dict[str, Any]) -> dict[str, Any]:
    action = str(action or "").upper()
    if action not in DISPATCH:
        raise WorkspaceError(f"Unsupported action: {action}", "UNSUPPORTED_ACTION")
    return DISPATCH[action](conn, treasury, payload)
