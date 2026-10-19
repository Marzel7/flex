"""Read-only access to the canonical operator model."""
from __future__ import annotations

import contextlib
import json
import sqlite3
import time
from pathlib import Path


_PROVISIONAL_900B = "p3r-v2-900b89587c6987d582df"
_PROVISIONAL_900B_DETAIL = {
    "selected_funding_pattern": "Hop-1 WSOL_WRAP_CLOSE",
    "exact_amount_lamports": 999985000,
    "human_amount": "1 SOL - 15,000 lamports",
    "atomic_lifecycle": "createAccountWithSeed -> initializeAccount3 -> transfer -> syncNative -> closeAccount",
    "atomic_dominant_coverage": "31/44",
    "wsol_lifecycle_coverage": "44/44",
    "role_pattern": "44 rotating creators; candidate_parent is the direct-funder role; 10 distinct direct funders; top five funders cover 39/44 (88.64%).",
    "profile": "Recurrent near-1-SOL temporary-WSOL provision-and-close variant with rotating creators and recurrent direct-funder infrastructure.",
    "validation": {"h0": "Behaviour-only: precision 74.58%, recall 100.00%.", "h1": "Hybrid recurrent-funder review-confidence contract: precision 90.70%, recall 88.64%; 4 known false positives remain."},
    "analysis": "Behaviour is strongly recurrent and recurrent direct-funder infrastructure improves precision, but the zero-false-positive automatic-attribution gate is not met. Bounded deeper-hop RPC and provenance studies did not close that gap.",
    "history": ["P3R candidate discovery and operation-priority ranking.", "Distinctiveness and bounded RPC discriminator investigations.", "Hybrid recurrent-funder qualification and provisional Active Operations admission."],
}

_CURRENT_CENSUS_PATH = Path(__file__).resolve().parents[2] / "docs/audits/p3r_current_queue_census.v1.json"
_SENTINEL_EVOLUTION_ADMISSIONS_PATH = Path(__file__).resolve().parents[2] / "docs/audits/sentinel_evolution_cluster_admission.v1.json"
_CURRENT_CENSUS_CACHE: dict[str, object] = {}
_BYZANTINE_OPERATOR_ID = "d8ee4d7a-fcd6-5a5b-b897-24f6ab56e334"
_BYZANTINE_SUBPROVIDER = "ByZc7RNeYowEg2jKo2giytWb9WmNyZPrQ1hXhnGSzHTY"
_BYZANTINE_DUAL_LEG_AUDIT = Path(__file__).resolve().parents[2] / "docs/audits/byzantine_dual_funding_leg_ui_read_only_design.v1.json"
_BYZANTINE_TOPOLOGY_AUDIT = Path(__file__).resolve().parents[2] / "docs/audits/byzantine_182_cohort_funding_topology_read_only_audit.v1.json"
_BYZANTINE_BASELINE_AUDIT = Path(__file__).resolve().parents[2] / "docs/audits/byzantine_surfaced_mint_cohort_compatibility_freeze.v1.json"
_BYZANTINE_ENRICHMENT_AUDIT = Path(__file__).resolve().parents[2] / "docs/audits/byzantine_182_dual_leg_enrichment_replay.v1.json"
_BYZANTINE_PAIRING_AUDIT = Path(__file__).resolve().parents[2] / "docs/audits/byzantine_46_missing_upstream_rpc/per_mint_pairing_results.v1.json"
_BYZANTINE_AMBIGUITY_AUDIT = Path(__file__).resolve().parents[2] / "docs/audits/byzantine_12_ambiguous_upstream_disambiguation_read_only_audit.v1.json"
_NEXUS_OPERATOR_ID = "bd7d7479-1454-5d41-9f68-115550348f3e"
_NEXUS_DETECTOR_AUDIT = Path(__file__).resolve().parents[2] / "docs/audits/direct_10k_creator_provisioning_detector_results.v3.json"
_LEVIATHAN_OPERATOR_ID = "777211c3-211e-551b-9310-ff9301570627"
_LEVIATHAN_DETECTOR_AUDIT = Path(__file__).resolve().parents[2] / "docs/audits/leviathan_detector_match_ui.v1.json"


def _byzantine_dual_leg_evidence() -> dict[str, dict]:
    """Read the frozen, additive Byzantine role evidence; never infer a leg.

    ``subprov`` still owns the compatibility-only membership predicate.  This
    adapter only enriches already surfaced rows with independently retained
    economic roles, so it cannot admit or remove a launch.
    """
    try:
        design = json.loads(_BYZANTINE_DUAL_LEG_AUDIT.read_text())
        enrichment = json.loads(_BYZANTINE_ENRICHMENT_AUDIT.read_text())
        pairing = json.loads(_BYZANTINE_PAIRING_AUDIT.read_text())
        ambiguity = json.loads(_BYZANTINE_AMBIGUITY_AUDIT.read_text())
        topology = json.loads(_BYZANTINE_TOPOLOGY_AUDIT.read_text())
        baseline = json.loads(_BYZANTINE_BASELINE_AUDIT.read_text())
        expected = baseline["sorted_mint_membership_digest"]
        if expected != "b9c88cab4b1d7a90deea64b7d03736cce14b1571a5a797eeb807f26867b055e1":
            return {}
        topology_by_mint = {row["MINT"]: row for row in topology.get("mints", [])}
        result: dict[str, dict] = {}
        enriched_by_mint = {row["MINT"]: row for row in enrichment.get("rows", [])}
        serial_by_mint = {row["MINT"]: row for row in pairing.get("rows", []) if row.get("UPSTREAM_DISCOVERY_STATUS") == "PROVEN_SERIAL_UPSTREAM_BYZ_CREATOR"}
        serial_by_mint.update({row["MINT"]: row for row in ambiguity.get("rows", []) if row.get("SERIAL_CHAIN_NOW_PROVEN")})
        for row in design.get("rows", []):
            mint = row.get("MINT")
            if not mint:
                continue
            topo = topology_by_mint.get(mint, {})
            upstream = dict(row.get("UPSTREAM_TO_BYZ_TX") or {})
            creator = dict(row.get("BYZ_TO_CREATOR_TX") or {})
            enriched = enriched_by_mint.get(mint, {})
            if enriched.get("UPSTREAM_LEG_PROVEN"):
                upstream = dict(enriched.get("UPSTREAM") or upstream)
            if enriched.get("CREATOR_PROVISION_LEG_PROVEN"):
                creator = dict(enriched.get("CREATOR_PROVISION") or creator)
            # The design corpus intentionally carries only independently
            # proven fields.  Add topology metadata only to its same role.
            if upstream.get("state") == "PROVEN":
                upstream.update({
                    "timestamp": topo.get("FIRST_LEG_TIME"),
                    "temporary_wsol_account": None,
                    "evidence_source": topo.get("EVIDENCE_PROVENANCE") or upstream.get("provenance"),
                })
            if creator.get("state") == "PROVEN":
                creator.update({
                    "timestamp": topo.get("SECOND_LEG_TIME"),
                    "temporary_wsol_account": topo.get("TEMP_WSOL_ACCOUNT"),
                    "evidence_source": topo.get("EVIDENCE_PROVENANCE") or creator.get("provenance"),
                })
            result[mint] = {
                "upstream_funding": upstream,
                "creator_provisioning": creator,
                "session_chain": {
                    "status": "PROVEN_SERIAL_UPSTREAM_BYZ_CREATOR" if mint in serial_by_mint else enriched.get("FULL_TWO_LEG_CHAIN_STATUS", "NEITHER_PROVEN"),
                    "offset_seconds": serial_by_mint.get(mint, {}).get("UPSTREAM_TO_PROVISION_OFFSET_SECONDS"),
                },
            }
        # Fail closed if the retained design is not the protected cohort.
        return result if len(result) == baseline.get("unique_mint_total") == 182 else {}
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return {}


def _enrich_byzantine_dual_legs(rows: list[dict]) -> list[dict]:
    """Add presentation-only roles and retain the selected Walkback trace."""
    evidence = _byzantine_dual_leg_evidence()
    for row in rows:
        row["selected_walkback_tx"] = row.get("wrap_close_signature")
        row.update(evidence.get(row.get("mint"), {
            "upstream_funding": {"state": "NOT_RETAINED"},
            "creator_provisioning": {"state": "NOT_RETAINED"},
            "session_chain": {"status": "NEITHER_PROVEN", "offset_seconds": None},
        }))
    return rows

def _nexus_detector_projection(conn: sqlite3.Connection | None = None) -> dict:
    payload=json.loads(_NEXUS_DETECTOR_AUDIT.read_text())
    rows=[]
    for item in payload.get("rows",[]):
        result=item["detector_result"]; label={"UNIQUE_MATCH":"Exact","NO_MATCH":"No","INSUFFICIENT_INPUT":"Incomplete","AMBIGUOUS":"Ambiguous"}.get(result,"Incomplete")
        rows.append({"mint":item["mint"],"raw_result":result,"label":label,"reason":item.get("reason",""),"cohort":item.get("population")})
    counts={key:sum(row["raw_result"]==key for row in rows) for key in ("UNIQUE_MATCH","NO_MATCH","INSUFFICIENT_INPUT","AMBIGUOUS")}
    if conn is not None:
        try:
            for mint, result, reason in conn.execute("SELECT mint,detector_result,reason_code FROM operation_detector_results WHERE operation_id=?", (_NEXUS_OPERATOR_ID,)):
                rows=[row for row in rows if row["mint"] != mint]
                rows.append({"mint":mint,"raw_result":result,"label":{"UNIQUE_MATCH":"Exact","NO_MATCH":"No","INSUFFICIENT_INPUT":"Incomplete","AMBIGUOUS":"Ambiguous"}.get(result,"Incomplete"),"reason":reason,"cohort":"CURRENT"})
            counts={key:sum(row["raw_result"]==key for row in rows) for key in counts}
        except sqlite3.Error: pass
    return {"rows":rows,"counts":counts,"reviewed":counts["UNIQUE_MATCH"]+counts["NO_MATCH"]}


def _leviathan_detector_projection(conn: sqlite3.Connection | None = None) -> dict:
    """Leviathan's own detector semantics (P3R unified WSOL_WRAP_CLOSE contract),
    replayed via scripts/generate_leviathan_detector_match_ui.py — NOT the Nexus
    DIRECT_10K contract. Presentation only; never touches membership/detector state.

    Every canonical operator_launch_membership row for Leviathan gets exactly one
    detector-state entry here (EXACT unless retained evidence disagrees), so the
    template can mark the SAME unified launch list — never a separate "recently
    admitted" collection keyed off a stale profile snapshot."""
    payload = json.loads(_LEVIATHAN_DETECTOR_AUDIT.read_text())
    hist = payload["historical_population"]
    live = payload["current_live_observations"]
    rows_by_mint: dict[str, dict] = {}
    for mint in live.get("pending_mints_sample", []):
        rows_by_mint[mint] = {"mint": mint, "raw_result": "PENDING_REPLAY", "label": "Pending"}
    if conn is not None:
        try:
            from src.ops.p3r_profile_candidate_matcher import evaluate_mint
            members = [r[0] for r in conn.execute(
                "SELECT mint FROM operator_launch_membership WHERE operator_id=?",
                (_LEVIATHAN_OPERATOR_ID,),
            ).fetchall()]
            for mint in members:
                if mint in rows_by_mint:
                    continue
                match = evaluate_mint(conn, mint)
                if match and _LEVIATHAN_OPERATOR_ID in match.matching_operator_ids and match.state != "AMBIGUOUS_BEHAVIOURAL_CANDIDATE":
                    rows_by_mint[mint] = {"mint": mint, "raw_result": "EXACT", "label": "Exact"}
                else:
                    # Canonical membership without a current exact replay: surface the
                    # discrepancy rather than assuming green (evidence wins over membership).
                    rows_by_mint[mint] = {"mint": mint, "raw_result": "MEMBER_NOT_CURRENTLY_EXACT", "label": "Review"}
        except (sqlite3.Error, ImportError):
            pass
    exact_count = sum(1 for r in rows_by_mint.values() if r["raw_result"] == "EXACT")
    return {
        "exact_count": exact_count or hist["exact_count"],
        "verified_total": exact_count or hist["exact_count"],
        "pending_count": live["current_pending_replay"],
        "rows": list(rows_by_mint.values()),
        "rejected_lookalike_count": payload["rejected_lookalikes"]["count"],
    }


def _byzantine_infrastructure_activity(conn: sqlite3.Connection, *, now: int | None = None) -> dict | None:
    """Read current completed-launch activity through Byzantine's shared sub-provider.

    This is explicitly infrastructure telemetry, not strict-operation membership
    and never writes or admits a launch.
    """
    try:
        now = int(now or time.time())
        timestamps = [int(row[0]) for row in conn.execute(
            "SELECT DISTINCT funder_block_time FROM wt_walkback_queue "
            "WHERE subprov=? AND status='complete' AND funder_block_time IS NOT NULL",
            (_BYZANTINE_SUBPROVIDER,),
        )]
    except sqlite3.Error:
        return None
    if not timestamps:
        return None
    counts = {days: sum(stamp > now - days * 86400 for stamp in timestamps) for days in (1, 7, 30)}
    state = "VERY_ACTIVE" if counts[1] >= 3 else "ACTIVE" if counts[1] or counts[7] else "COOLING" if counts[30] else "DORMANT"
    return {
        "live_launches_24h": counts[1], "live_launches_7d": counts[7],
        "live_launches_30d": counts[30], "last_live_launch_at": max(timestamps),
        "total_observed_launches": len(timestamps), "activity_state": state,
        "activity_source": "LIVE_BYZANTINE_INFRASTRUCTURE",
        "timestamp_semantics": "Completed launches sharing Byzantine sub-provider infrastructure; strict Byzantine membership is unchanged.",
    }


def activity_read_model(timestamps: list[int], snapshot_metrics: dict, snapshot_as_of: int | None, *, now: int | None = None) -> dict:
    """Read-only current activity over retained launch timestamps.

    Snapshot counts retain their historical meaning, but never masquerade as
    counts relative to the time this method is called.
    """
    now = int(now or time.time())
    values = sorted({int(value) for value in timestamps if value is not None})
    result = {
        "snapshot_launches_24h": snapshot_metrics.get("launches_last_1d"),
        "snapshot_launches_7d": snapshot_metrics.get("launches_last_7d"),
        "snapshot_launches_30d": snapshot_metrics.get("launches_last_30d"),
        "snapshot_as_of": snapshot_as_of,
    }
    if not values:
        result.update({"live_launches_24h": None, "live_launches_7d": None,
                       "live_launches_30d": None,
                       "last_launch_at": snapshot_metrics.get("last_observed_launch_timestamp"),
                       "activity_state_source": "SNAPSHOT_ONLY",
                       "activity_state": "ACTIVITY_UNKNOWN"})
        return result
    counts = {days: sum(value > now - days * 86400 for value in values) for days in (1, 7, 30)}
    state = "VERY_ACTIVE" if counts[1] >= 3 else "ACTIVE" if counts[1] or counts[7] else "COOLING" if counts[30] else "DORMANT"
    result.update({"live_launches_24h": counts[1], "live_launches_7d": counts[7],
                   "live_launches_30d": counts[30], "last_launch_at": values[-1],
                   "activity_state_source": "LIVE_RECALCULATED", "activity_state": state})
    return result


def _sentinel_evolution_presentation() -> dict:
    """Read the explicit fixed-census review; never mutate registry state."""
    try:
        payload=json.loads(_SENTINEL_EVOLUTION_ADMISSIONS_PATH.read_text())
        candidates=payload.get("admitted_candidates", [])
        return {"state":"QUALIFIED_VARIANTS_ADMITTED", "label":f"{len(candidates)} admitted Potential variants",
                "detail":f"75 near observations · 2 qualifying clusters · {len(candidates)} admitted Potential candidates.",
                "links":[{"label":item["name"],"href":f"/intelligence/potential-operations/{item['candidate_id']}"} for item in candidates]}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {"state":"DRIFT_EVIDENCE", "label":"Drift evidence", "detail":"75 near-fingerprint observations. Requires clustering before mutation or variant attribution."}


def _current_census_by_operation() -> dict[str, dict]:
    """Read the committed frozen census artifact; never refresh its source."""
    try:
        stat = _CURRENT_CENSUS_PATH.stat()
        if _CURRENT_CENSUS_CACHE.get("mtime_ns") != stat.st_mtime_ns:
            payload = json.loads(_CURRENT_CENSUS_PATH.read_text())
            first = payload.get("first", {})
            _CURRENT_CENSUS_CACHE.clear()
            _CURRENT_CENSUS_CACHE.update({
                "mtime_ns": stat.st_mtime_ns,
                "run_id": payload.get("run_id"),
                "source": first.get("source_snapshot", {}),
                "operators": {item.get("operation"): item for item in first.get("operators", [])},
            })
        source = _CURRENT_CENSUS_CACHE.get("source") or {}
        context = {"run_id": _CURRENT_CENSUS_CACHE.get("run_id"), "source": "Current Queue Census",
                   "queue_high_water": (source.get("highwaters") or {}).get("wt_walkback_queue"),
                   "database_stat": source.get("database_stat")}
        return {name: {**item, "context": context} for name, item in (_CURRENT_CENSUS_CACHE.get("operators") or {}).items()}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}


def _census_presentation(display_name: str) -> dict | None:
    row = _current_census_by_operation().get(display_name)
    if not row:
        return None
    if display_name == "WATCHTOWER":
        evolution = {"state": "DYNAMIC_ROLE_MONITORING", "label": "Dynamic role monitoring",
                     "detail": "Mutation monitoring: Dynamic role discovery."}
    elif display_name == "FOUR_STEP_30_SOL_14_479K_WSOL_LADDER":
        evolution = _sentinel_evolution_presentation()
    elif display_name == "P3R_13A04":
        evolution = {"state": "RELATED_ACTIVITY_UNRESOLVED", "label": "Related activity unresolved",
                     "detail": f"Related 30 SOL ladder behaviour ({row.get('near', 0)}) requires clustering before attribution."}
    else:
        evolution = {"state": "NONE_OBSERVED", "label": "None observed", "detail": "No meaningful near-fingerprint evidence observed in this frozen census."}
    row["evolution_watch"] = evolution
    row["activity_label"] = "PROVISIONAL" if display_name == "WSOL_PROVISION_CLOSE_1_SOL_MINUS_15K" else row.get("activity_state", "ACTIVITY_UNKNOWN")
    row["near_label"] = "Related observations" if display_name == "P3R_13A04" else "Near"
    row["exact_label"] = "Exact behavioural observations" if display_name == "WSOL_PROVISION_CLOSE_1_SOL_MINUS_15K" else "Exact observations"
    if display_name == "P3R_13A04":
        row["historical_baseline"] = "Not yet measured"
    return row


class OperatorReader:
    """A pure reader: URI read-only, query-only, no DDL or persistent PRAGMAs."""

    def __init__(self, db_path: str) -> None:
        self._path = db_path

    @contextlib.contextmanager
    def _connect(self):
        conn = sqlite3.connect(f"file:{self._path}?mode=ro", uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        try:
            yield conn
        finally:
            conn.close()

    def fetch_operator(self, operator_id: str) -> dict | None:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM operators WHERE operator_id = ?", (operator_id,)
                ).fetchone()
                if not row:
                    return None
                op = dict(row)
                qualification = conn.execute(
                    "SELECT qualification_category,automation_eligibility,detector_version,parent_mechanism,source_candidate_id,benchmark_json "
                    "FROM operation_qualification_contracts WHERE operator_id=? ORDER BY created_at DESC LIMIT 1",
                    (operator_id,),
                ).fetchone()
                if qualification:
                    op["qualification_contract"] = {**dict(qualification), "benchmark": json.loads(qualification["benchmark_json"] or "{}")}
                    op["qualification_category"] = qualification["qualification_category"]
                else:
                    op["qualification_category"] = "CONFIRMED"
                op["entities"] = [dict(r) for r in conn.execute(
                    "SELECT * FROM operator_entities WHERE operator_id = ? "
                    "ORDER BY evidence_count DESC", (operator_id,)
                ).fetchall()]
                op["evidence"] = [
                    {**dict(r), "details": json.loads(r["details"] or "{}")}
                    for r in conn.execute(
                        "SELECT * FROM operator_evidence WHERE operator_id = ? "
                        "ORDER BY created_at DESC", (operator_id,)
                    ).fetchall()
                ]
                op["reviews"] = [dict(r) for r in conn.execute(
                    "SELECT * FROM operator_reviews WHERE operator_id = ? "
                    "ORDER BY timestamp DESC", (operator_id,)
                ).fetchall()]
                op["promotion_history"] = [
                    {**dict(r), "evidence_snapshot": json.loads(r["evidence_snapshot"])}
                    for r in conn.execute(
                        "SELECT * FROM operator_promotion_reviews "
                        "WHERE canonical_operator_id = ? ORDER BY timestamp DESC",
                        (operator_id,),
                    ).fetchall()
                ]
                profile = conn.execute(
                    "SELECT source_candidate_id, profile_version, status, provenance_json, "
                    "member_mints_json, reviewed_at, reviewer "
                    "FROM operation_behavioural_profiles WHERE operator_id=? "
                    "ORDER BY profile_version DESC LIMIT 1",
                    (operator_id,),
                ).fetchone()
                if profile:
                    op["behavioural_profile"] = {
                        **dict(profile),
                        "provenance": json.loads(profile["provenance_json"]),
                        "member_mints": json.loads(profile["member_mints_json"]),
                    }
                    mints = op["behavioural_profile"]["member_mints"]
                    if mints and conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='wt_attribution_outcomes'"
                    ).fetchone():
                        placeholders = ",".join("?" for _ in mints)
                        rows = conn.execute(
                            "SELECT mint, outcome_type, terminal_entity, evidence_json, completed_at "
                            f"FROM wt_attribution_outcomes WHERE mint IN ({placeholders})", mints,
                        ).fetchall()
                        history = [
                            {**dict(item), "details": json.loads(item["evidence_json"] or "{}")}
                            for item in rows
                        ]
                        history_by_mint = {item["mint"]: item for item in history}
                        if history_by_mint and conn.execute(
                            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='wt_provisioning_edges'"
                        ).fetchone():
                            edge_rows = conn.execute(
                                "SELECT source_mint, funding_tx_signature, funding_block_time, funding_mechanism "
                                f"FROM wt_provisioning_edges WHERE edge_type='SUBPROV_TO_CREATOR' AND source_mint IN ({placeholders})",
                                mints,
                            ).fetchall()
                            for edge in edge_rows:
                                history_by_mint[edge["source_mint"]].update(dict(edge))
                        op["retained_funding_history"] = history
                snapshot = conn.execute(
                    "SELECT observed_at, timestamp_semantics, metrics_json, activity_state "
                    "FROM operation_activity_snapshots WHERE operator_id=? "
                    "ORDER BY observed_at DESC LIMIT 1",
                    (operator_id,),
                ).fetchone()
                if snapshot:
                    op["activity_snapshot"] = {
                        **dict(snapshot),
                        "metrics": json.loads(snapshot["metrics_json"]),
                    }
                profile_provenance = (op.get("behavioural_profile") or {}).get("provenance", {})
                source_candidate_id = (op.get("behavioural_profile") or {}).get("source_candidate_id")
                if profile_provenance.get("detector") == "DIRECT_10K_CREATOR_PROVISIONING" and source_candidate_id:
                    try:
                        from src.ops.live_potential_activity import aggregate as aggregate_live_activity
                        live_activity, _ = aggregate_live_activity(self._path)
                        live = live_activity.get(source_candidate_id)
                    except (sqlite3.Error, OSError, ValueError, KeyError):
                        live = None
                    if live and live.get("activity_source") == "LIVE_CURRENT":
                        # Detail pages retain the frozen member transactions as
                        # historical evidence, but their activity panel must use
                        # the same current qualified route telemetry as Active Ops.
                        op["activity_snapshot"] = {
                            "observed_at": int(time.time()),
                            "timestamp_semantics": "Current qualified route-matcher activity; historical membership is unchanged.",
                            "activity_state": live["live_activity_state"],
                            "metrics": {
                                "total_observed_launches": len((op.get("behavioural_profile") or {}).get("member_mints", [])),
                                "launches_last_1d": live["live_launches_24h"],
                                "launches_last_7d": live["live_launches_7d"],
                                "launches_last_30d": live["live_launches_30d"],
                                "last_observed_launch_timestamp": live["last_live_launch_at"],
                                "activity_source": "LIVE_QUALIFIED_ROUTE_MATCHER",
                            },
                        }
                if operator_id == _BYZANTINE_OPERATOR_ID:
                    infrastructure = _byzantine_infrastructure_activity(conn)
                    if infrastructure:
                        op["infrastructure_activity"] = infrastructure
                        strict_total = conn.execute(
                            "SELECT COUNT(*) FROM operator_launch_membership WHERE operator_id=?",
                            (operator_id,),
                        ).fetchone()[0]
                        op["activity_snapshot"] = {
                            "observed_at": int(time.time()),
                            "timestamp_semantics": infrastructure["timestamp_semantics"],
                            "activity_state": infrastructure["activity_state"],
                            "metrics": {
                                "total_observed_launches": strict_total,
                                "launches_last_1d": infrastructure["live_launches_24h"],
                                "launches_last_7d": infrastructure["live_launches_7d"],
                                "launches_last_30d": infrastructure["live_launches_30d"],
                                "last_observed_launch_timestamp": infrastructure["last_live_launch_at"],
                                "activity_source": infrastructure["activity_source"],
                                "strict_member_launches": strict_total,
                                "infrastructure_launches_total": infrastructure["total_observed_launches"],
                            },
                        }
                if operator_id == _NEXUS_OPERATOR_ID:
                    # Authoritative frozen semantic determinations; presentation only.
                    op["nexus_detector"] = _nexus_detector_projection(conn)
                if operator_id == _LEVIATHAN_OPERATOR_ID:
                    # Leviathan's own P3R detector semantics; presentation only, gated to this operator.
                    try:
                        op["leviathan_detector"] = _leviathan_detector_projection(conn)
                    except (OSError, json.JSONDecodeError, KeyError):
                        pass
                if op.get("qualification_category") == "CONFIRMED" and op.get("display_name") != "WATCHTOWER" and conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='operator_launch_membership'"
                ).fetchone():
                    op["recent_launches"] = [dict(r) for r in conn.execute(
                        "SELECT m.mint,COALESCE(q.creator,'') AS creator_wallet,COALESCE(q.create_anchor_block_time,q.funder_block_time,q.completed_at,m.assigned_at) AS create_time,q.treasury AS treasury_wallet,q.subprov AS subprov_wallet,q.funder_sig AS wrap_close_signature,q.funding_mechanism FROM operator_launch_membership m LEFT JOIN wt_walkback_queue q ON q.mint=m.mint WHERE m.operator_id=? ORDER BY create_time DESC LIMIT 250",
                        (operator_id,),
                    ).fetchall()]
                if operator_id == _BYZANTINE_OPERATOR_ID:
                    # Show newly retained ByZc activity alongside strict
                    # historical members. These rows remain observations only.
                    infrastructure_rows = [dict(r) for r in conn.execute(
                        "SELECT mint,COALESCE(creator,'') AS creator_wallet,treasury AS treasury_wallet,"
                        "subprov AS subprov_wallet,funder_sig AS wrap_close_signature,"
                        "funder_block_time AS create_time,funding_mechanism "
                        "FROM wt_walkback_queue WHERE subprov=? AND status='complete' "
                        "AND funder_block_time IS NOT NULL ORDER BY funder_block_time DESC LIMIT 250",
                        (_BYZANTINE_SUBPROVIDER,),
                    ).fetchall()]
                    for row in infrastructure_rows:
                        row["activity_observation_type"] = "CURRENT_BYZANTINE_INFRASTRUCTURE"
                        row["provisional_state"] = "CURRENT_BYZANTINE_INFRASTRUCTURE"
                        row["selected_hop"] = 1
                    historical = op.get("recent_launches", [])
                    by_mint = {row.get("mint"): row for row in historical}
                    by_mint.update({row.get("mint"): row for row in infrastructure_rows})
                    op["recent_launches"] = sorted(
                        by_mint.values(), key=lambda row: row.get("create_time") or 0, reverse=True
                    )
                    # Preserve the exact compatibility surface above, then add
                    # independent retained role evidence.  This does not
                    # change membership, ordering, selected-edge semantics,
                    # or the legacy distinct-funder_block_time activity metric.
                    _enrich_byzantine_dual_legs(op["recent_launches"])
                if profile_provenance.get("detector") == "DIRECT_10K_CREATOR_PROVISIONING" and source_candidate_id:
                    try:
                        from src.ops.live_potential_activity import aggregate as aggregate_live_activity
                        live_activity, _ = aggregate_live_activity(self._path)
                        live = live_activity.get(source_candidate_id) or {}
                        live_mints = [item["mint"] for item in live.get("live_matches", [])]
                        if live_mints:
                            marks = ",".join("?" for _ in live_mints)
                            current = [dict(row) for row in conn.execute(
                                f"SELECT mint, creator AS creator_wallet, funder_wallet AS subprov_wallet, funder_sig AS wrap_close_signature, funder_block_time AS create_time, funding_mechanism FROM wt_walkback_queue WHERE mint IN ({marks})",
                                live_mints,
                            )]
                            for row in current:
                                row["activity_observation_type"] = "CURRENT_QUALIFIED_ROUTE_MATCH"
                            historical = op.get("recent_launches", [])
                            by_mint = {row.get("mint"): row for row in historical}
                            by_mint.update({row.get("mint"): row for row in current})
                            op["recent_launches"] = list(by_mint.values())
                    except (sqlite3.Error, OSError, ValueError, KeyError):
                        pass
                if (op.get("behavioural_profile", {}).get("provenance", {}).get("detector_version")
                        == "D3DE_D0_EXACT_SELECTED_FOUR_STEP_LADDER.v1"):
                    provenance = op["behavioural_profile"]["provenance"]
                    evidence = provenance.get("historical_member_evidence", [])
                    op["summary_model"] = {
                        "activity": {"state": op.get("activity_snapshot", {}).get("activity_state", "ACTIVE"),
                                     "metrics": op.get("activity_snapshot", {}).get("metrics", {})},
                        "fingerprint": {
                            "route": "Parent hop 4 → parent hop 3 → parent hop 2 → direct funder → creator",
                            "funding": "Exact selected ladder: 29,999,985,000 → 29,999,990,000 → 14,479,000 → 2,074,000 lamports",
                            "mechanism": "PLAIN_XFER → WSOL_WRAP_CLOSE → WSOL_WRAP_CLOSE → WSOL_WRAP_CLOSE",
                            "atomic_sequence": provenance.get("atomic_sequence", []),
                            "address_behaviour": "Address-independent: 9 creators, 9 direct funders, and 33 parents rotate across the frozen cohort.",
                            "profile": "D0 exact selected route · frozen validation 9 TP / 0 FP / 0 FN across 12,041 observable retained mints.",
                        },
                        "all_launches": [{"mint": item["mint"], "creator": item.get("creator"),
                                          "intermediary": item.get("direct_funder"), "signature": item.get("selected_edges", [{}])[0].get("signature"),
                                          "create_time": item.get("observed_at"), "mechanism": "D0 CONFIRMED"}
                                         for item in evidence],
                    }
                if op.get("qualification_category") == "PROVISIONAL" and conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='provisional_operation_activity_observations'"
                ).fetchone():
                    rows = conn.execute(
                        "SELECT o.mint,o.observed_at AS create_time,o.state,o.provenance_json,e.wallet AS creator,e.candidate_parent AS direct_funder,e.signature,e.hop_depth AS selected_hop,e.mechanism,e.amount_lamports AS selected_amount_lamports,a.instruction_order_json AS atomic_sequence_json FROM provisional_operation_activity_observations o LEFT JOIN wt_walkback_edge_candidates e ON e.mint=o.mint AND e.selection_status='SELECTED' AND e.hop_depth=1 LEFT JOIN wt_walkback_atomic_flows a ON a.signature=e.signature AND a.mint=o.mint WHERE o.operator_id=? ORDER BY o.observed_at DESC LIMIT 250",
                        (operator_id,),
                    ).fetchall()
                    op["recent_launches"] = []
                    for row in rows:
                        launch = dict(row)
                        launch.update({"intermediary": launch["direct_funder"], "provisional_state": launch["state"], "match": launch["state"].replace("_", " ")})
                        if launch.get("atomic_sequence_json"):
                            launch["atomic_sequence"] = json.loads(launch["atomic_sequence_json"])
                        op["recent_launches"].append(launch)
                    if op.get("qualification_contract", {}).get("source_candidate_id") == _PROVISIONAL_900B:
                        op["provisional_detail"] = _PROVISIONAL_900B_DETAIL
                        op["summary_model"] = {"activity": {"state": "PROVISIONAL", "metrics": op.get("activity_snapshot", {}).get("metrics", {})}, "fingerprint": {"route": "Direct funder -> rotating creator", "funding": "999,985,000 lamports (1 SOL - 15,000 lamports)", "mechanism": "Hop-1 WSOL_WRAP_CLOSE", "atomic_sequence": [_PROVISIONAL_900B_DETAIL["atomic_lifecycle"] + " (31/44)"], "address_behaviour": _PROVISIONAL_900B_DETAIL["role_pattern"] + " Broader WSOL lifecycle 44/44.", "profile": _PROVISIONAL_900B_DETAIL["profile"]}, "recent_launches": op["recent_launches"]}
                        op["evidence"].append({"evidence_type": "PROVISIONAL_OPERATION_CONTRACT", "details": _PROVISIONAL_900B_DETAIL})
                # WATCHTOWER's canonical launch ledger predates generic
                # membership. The display therefore combines ledger entries
                # with strict confirmed membership-only launches, using their
                # retained queue route fields. This is a read-only projection.
                if op.get("display_name") == "WATCHTOWER" and conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='wt_watchtower_launches'"
                ).fetchone():
                    has_membership = conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='operator_launch_membership'"
                    ).fetchone()
                    has_queue = conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='wt_walkback_queue'"
                    ).fetchone()
                    if has_membership and has_queue:
                        op["recent_launches"] = [dict(r) for r in conn.execute(
                            "SELECT mint, creator_wallet, create_time, treasury_wallet, subprov_wallet, "
                            "wrap_close_signature, funding_mechanism FROM ("
                            "SELECT mint, creator_wallet, create_time, treasury_wallet, subprov_wallet, "
                            "wrap_close_signature, funding_mechanism "
                            "FROM wt_watchtower_launches WHERE mint IS NOT NULL "
                            "AND COALESCE(state, 'FIRED_CREATE') != 'PENDING_REVIEW' "
                            "UNION ALL "
                            "SELECT q.mint, q.creator AS creator_wallet, "
                            "COALESCE(q.create_anchor_block_time,q.funder_block_time,q.completed_at) AS create_time, "
                            "q.treasury AS treasury_wallet, q.subprov AS subprov_wallet, "
                            "q.funder_sig AS wrap_close_signature, q.funding_mechanism "
                            "FROM operator_launch_membership m JOIN wt_walkback_queue q ON q.mint=m.mint "
                            "WHERE m.operator_id=? AND q.intelligence_outcome='WATCHTOWER_CONFIRMED' "
                            "AND NOT EXISTS (SELECT 1 FROM wt_watchtower_launches l WHERE l.mint=q.mint)"
                            ") ORDER BY create_time DESC LIMIT 250",
                            (operator_id,),
                        ).fetchall()]
                    else:
                        op["recent_launches"] = [dict(r) for r in conn.execute(
                            "SELECT mint, creator_wallet, create_time, treasury_wallet, subprov_wallet, "
                            "wrap_close_signature, funding_mechanism "
                            "FROM wt_watchtower_launches WHERE mint IS NOT NULL "
                            "AND COALESCE(state, 'FIRED_CREATE') != 'PENDING_REVIEW' "
                            "ORDER BY create_time DESC LIMIT 250"
                        ).fetchall()]
                from src.ops.operator_identity_governance import read_identity_lifecycle
                op["identity_lifecycle"] = read_identity_lifecycle(self._path, operator_id)
                if op["identity_lifecycle"]:
                    op["identity_status"] = op["identity_lifecycle"]["identity_status"]
                    op["activity_status"] = op["identity_lifecycle"]["activity_status"]
                from src.ops.operation_fingerprint_monitoring import build_fingerprint_health
                health = build_fingerprint_health(op)
                if health:
                    from src.ops.operation_fingerprint_drift import latest_health
                    health = build_fingerprint_health(op, latest_health(conn, operator_id, health["fingerprint_id"]))
                op["fingerprint_health"] = health
                from src.ops.operation_identity_metadata import identity_metadata
                member_count = conn.execute("SELECT COUNT(*) FROM operator_launch_membership WHERE operator_id=?", (operator_id,)).fetchone()[0]
                identity = identity_metadata(conn, op["display_name"], int(member_count)) or {}
                op["identity_metadata"] = identity
                # The detail route consumes this one-operation projection,
                # while the registry consumes fetch_active_manual_operators().
                # Keep both read projections consistent without altering the
                # durable registry's stable display_name/ID fields.
                op["human_display_name"] = identity.get("human_name", op["display_name"])
                op["current_queue_census"] = _census_presentation(op["display_name"])
                return op
        except (sqlite3.Error, OSError, json.JSONDecodeError):
            return None

    def fetch_all_operators(self, *, exclude_rejected: bool = True, limit: int = 200) -> list[dict]:
        try:
            with self._connect() as conn:
                where = "WHERE status != 'REJECTED'" if exclude_rejected else ""
                rows = conn.execute(
                    f"SELECT * FROM operators {where} ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                result = []
                from src.ops.operator_identity_governance import read_identity_lifecycle
                for row in rows:
                    value = dict(row)
                    lifecycle = read_identity_lifecycle(self._path, value["operator_id"])
                    value["identity_status"] = lifecycle.get("identity_status", value.get("status"))
                    value["activity_status"] = lifecycle.get("activity_status", "ACTIVE")
                    value["merged_into_operator_id"] = lifecycle.get("merged_into_operator_id")
                    result.append(value)
                return result
        except (sqlite3.Error, OSError):
            return []

    def fetch_active_manual_operators(self, *, limit: int = 200) -> list[dict]:
        """Return only operations explicitly admitted to the active registry."""
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT o.*, d.disposition, d.source_candidate_id AS registry_source_candidate_id, d.updated_at AS disposition_updated_at, "
                    "COALESCE(q.qualification_category,'CONFIRMED') AS qualification_category, q.automation_eligibility, q.detector_version, q.parent_mechanism, q.benchmark_json, s.metrics_json, s.activity_state, s.observed_at AS activity_snapshot_observed_at "
                    "FROM operators o "
                    "JOIN operation_registry_dispositions d "
                    "ON d.operator_id=o.operator_id "
                    "LEFT JOIN operation_qualification_contracts q ON q.operator_id=o.operator_id "
                    "LEFT JOIN operation_activity_snapshots s ON s.snapshot_id=("
                    "SELECT snapshot_id FROM operation_activity_snapshots "
                    "WHERE operator_id=o.operator_id ORDER BY observed_at DESC LIMIT 1) "
                    "WHERE d.disposition='ACTIVE_MANUAL' AND o.status!='MERGED' "
                    "ORDER BY d.updated_at DESC, o.operator_id ASC LIMIT ?",
                    (limit,),
                ).fetchall()
                result = []
                from src.ops.operation_identity_metadata import identity_metadata
                try:
                    from src.ops.live_potential_activity import aggregate as aggregate_live_activity
                    current_route_activity, _ = aggregate_live_activity(self._path)
                except (sqlite3.Error, OSError, ValueError, KeyError):
                    current_route_activity = {}
                for row in rows:
                    value = dict(row)
                    value["qualification_benchmark"] = json.loads(value.pop("benchmark_json") or "{}")
                    metrics = json.loads(value.pop("metrics_json") or "{}")
                    profile = conn.execute(
                        "SELECT member_mints_json, provenance_json FROM operation_behavioural_profiles WHERE operator_id=? ORDER BY profile_version DESC LIMIT 1",
                        (value["operator_id"],),
                    ).fetchone()
                    mints = set(json.loads(profile[0])) if profile else set()
                    try:
                        profile_provenance = json.loads(profile[1] or "{}") if profile else {}
                    except (TypeError, ValueError, json.JSONDecodeError):
                        profile_provenance = {}
                    if profile_provenance.get("detector") == "DIRECT_10K_CREATOR_PROVISIONING":
                        # Historical operation admission and prospective detector
                        # readiness are intentionally independent dimensions.
                        value["prospective_detector_status"] = "HOLD"
                        value["prospective_detector_detail"] = "Awaiting first genuine retained transaction-role evidence"
                    else:
                        value["prospective_detector_status"] = "NOT_APPLICABLE"
                        value["prospective_detector_detail"] = None
                    mints.update(item[0] for item in conn.execute(
                        "SELECT mint FROM operator_launch_membership WHERE operator_id=?", (value["operator_id"],)
                    ))
                    timestamps_by_mint = {}
                    if mints:
                        placeholders = ",".join("?" for _ in mints)
                        timestamps_by_mint.update(dict(conn.execute(
                            f"SELECT mint,COALESCE(create_anchor_block_time,funder_block_time,completed_at) FROM wt_walkback_queue WHERE mint IN ({placeholders}) AND COALESCE(create_anchor_block_time,funder_block_time,completed_at) IS NOT NULL",
                            tuple(mints),
                        )))
                        # Behavioural profiles may retain launches not present in
                        # the current walkback queue. Read their retained core
                        # timestamps without touching either database.
                        try:
                            from src.core.db import DB_PATH
                            core = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
                            core.execute("PRAGMA query_only=ON")
                            try:
                                for mint, created_at in core.execute(
                                    f"SELECT mint,created_at FROM token_analysis WHERE mint IN ({placeholders})", tuple(mints)
                                ):
                                    stamp = core.execute("SELECT strftime('%s', ?)", (created_at,)).fetchone()[0]
                                    if stamp is not None:
                                        timestamps_by_mint[mint] = int(stamp)
                            finally:
                                core.close()
                        except (sqlite3.Error, OSError):
                            pass
                    read_model = activity_read_model(
                        list(timestamps_by_mint.values()), metrics, value.get("activity_snapshot_observed_at")
                    )
                    value.update(read_model)
                    live_route = current_route_activity.get(value.get("registry_source_candidate_id"))
                    if profile_provenance.get("detector") == "DIRECT_10K_CREATOR_PROVISIONING" and live_route and live_route.get("activity_source") == "LIVE_CURRENT":
                        # The existing qualified route matcher is the current
                        # activity source. It may observe new matching launches,
                        # but it never mutates this operation's 84-member
                        # historical registry membership.
                        value.update({
                            "live_launches_24h": live_route["live_launches_24h"],
                            "live_launches_7d": live_route["live_launches_7d"],
                            "live_launches_30d": live_route["live_launches_30d"],
                            "last_launch_at": live_route["last_live_launch_at"],
                            "activity_state_source": "LIVE_QUALIFIED_ROUTE_MATCHER",
                            "activity_state": live_route["live_activity_state"],
                        })
                    if value["operator_id"] == _BYZANTINE_OPERATOR_ID:
                        infrastructure = _byzantine_infrastructure_activity(conn)
                        if infrastructure:
                            # The strict 10-SOL four-step membership remains
                            # immutable.  Only the read-side activity telemetry
                            # is widened to expose its live shared infrastructure.
                            value["infrastructure_activity"] = infrastructure
                            value.update({
                                "live_launches_24h": infrastructure["live_launches_24h"],
                                "live_launches_7d": infrastructure["live_launches_7d"],
                                "live_launches_30d": infrastructure["live_launches_30d"],
                                "last_launch_at": infrastructure["last_live_launch_at"],
                                "activity_state_source": infrastructure["activity_source"],
                                "activity_state": infrastructure["activity_state"],
                            })
                    # A manually admitted profile can have authoritative
                    # membership before its first activity snapshot exists.
                    # Membership is the established-launch count in that case.
                    value["total_launches"] = metrics.get("total_observed_launches") or len(mints)
                    value["launches_last_1d"] = value["live_launches_24h"]
                    value["launches_last_7d"] = value["live_launches_7d"]
                    value["launches_last_30d"] = value["live_launches_30d"]
                    value["average_inter_launch_gap_seconds"] = metrics.get("average_inter_launch_gap_seconds")
                    value["last_observed_launch_timestamp"] = value["last_launch_at"]
                    value["identity_state"] = value["qualification_category"]
                    value["evolution_state"] = "REACTIVATED" if value["activity_state"] == "REACTIVATED" else "STABLE"
                    membership_count = conn.execute(
                        "SELECT COUNT(*) FROM operator_launch_membership WHERE operator_id=?",
                        (value["operator_id"],),
                    ).fetchone()[0]
                    identity = identity_metadata(
                        conn, value["display_name"], int(membership_count)
                    ) or {}
                    value["human_display_name"] = identity.get(
                        "human_name", value["display_name"]
                    )
                    value["operation_family"] = identity.get("family")
                    value["current_queue_census"] = _census_presentation(value["display_name"])
                    result.append(value)
                result.sort(
                    key=lambda value: (
                        value["last_observed_launch_timestamp"] is not None,
                        value["last_observed_launch_timestamp"] or 0,
                    ),
                    reverse=True,
                )
                return result
        except (sqlite3.Error, OSError):
            return []

    def fetch_operation_review_queue(self, *, limit: int = 50) -> list[dict]:
        """Return non-member walkback results that require analyst review.

        This is deliberately a read-only projection.  A row in this queue is
        not an attribution and must never affect an operator's membership or
        activity snapshot.
        """
        try:
            with self._connect() as conn:
                if not conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='wt_watchtower_launches'"
                ).fetchone():
                    return []
                row = conn.execute(
                    "SELECT COUNT(DISTINCT mint) AS pending_launches, "
                    "MAX(COALESCE(create_time, recorded_at)) AS last_observed "
                    "FROM wt_watchtower_launches WHERE state='PENDING_REVIEW'"
                ).fetchone()
                if not row or not row["pending_launches"]:
                    return []
                return [{
                    "candidate_operator_id": "04265d9f-6eb2-568c-a49e-9253091a4dbb",
                    "candidate_operator_name": "WATCHTOWER",
                    "pending_launches": row["pending_launches"],
                    "last_observed": row["last_observed"],
                    "review_state": "PENDING_ROUTE_REVIEW",
                    "review_reason": "Walkback association was retained, but the verified provisioning route required for automatic membership is incomplete.",
                }]
        except (sqlite3.Error, OSError):
            return []

    def fetch_operator_review_candidates(self, operator_id: str, *, limit: int = 500) -> list[dict]:
        """Return pending token evidence for one proposed operation, never membership."""
        if operator_id != "04265d9f-6eb2-568c-a49e-9253091a4dbb":
            return []
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT mint, creator_wallet, create_time, treasury_wallet, subprov_wallet, "
                    "wrap_close_signature, funding_mechanism, recorded_at "
                    "FROM wt_watchtower_launches WHERE state='PENDING_REVIEW' "
                    "ORDER BY COALESCE(create_time, recorded_at) DESC, id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                return [dict(row) for row in rows]
        except (sqlite3.Error, OSError):
            return []

    def fetch_by_entity(self, entity_address: str) -> list[dict]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT o.*,oe.entity_type,oe.confidence AS entity_confidence,"
                    "oe.evidence_count FROM operators o JOIN operator_entities oe "
                    "ON o.operator_id=oe.operator_id WHERE oe.entity_address=? "
                    "AND o.status!='REJECTED' ORDER BY o.updated_at DESC",
                    (entity_address,),
                ).fetchall()
                return [dict(row) for row in rows]
        except (sqlite3.Error, OSError):
            return []

    def fetch_unified_investigation(self, entity_address: str) -> dict:
        """Read-time-only projection combining canonical operator state
        (this DB), main-DB historical funding relationships, and the new
        discovery-corpus evidence-qualification layer for a single entity
        address. Three independent read-only connections, no ATTACH, no
        writes, no schema change -- see docs/audits/ops_ui_p1_*.json for
        the full contract this implements.

        `operators.status` (read via fetch_by_entity, which already only
        reads the operators/operator_entities tables) is the ONLY field
        this method ever surfaces as canonical authority_state; every
        other field below is EVIDENCE_QUALIFICATION / DISCOVERY / FUNDING
        context and can never override it."""
        canonical_matches = self.fetch_by_entity(entity_address)
        authority_state = canonical_matches[0]["status"] if canonical_matches else None
        canonical_operator_id = canonical_matches[0]["operator_id"] if canonical_matches else None

        historical_population = 0
        try:
            from src.core.db import DB_PATH
            main_conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=10)
            main_conn.execute("PRAGMA query_only=ON")
            try:
                row = main_conn.execute(
                    "SELECT COUNT(*) FROM creator_funders WHERE funder_address=?",
                    (entity_address,),
                ).fetchone()
                historical_population = row[0] if row else 0
            finally:
                main_conn.close()
        except (sqlite3.Error, OSError):
            historical_population = None

        discovery = {
            "high_qualified_count": 0, "family_id": None, "classification": None,
            "attribution_state": None, "root_cex_infra_category": None,
            "cex_infra_hop_distance": None, "member_count": 0, "creator_count": 0,
        }
        try:
            from src.discovery.local_operation_discovery_projection import OUTPUT_DB
            disc_conn = sqlite3.connect(f"file:{OUTPUT_DB}?mode=ro", uri=True, timeout=10)
            disc_conn.row_factory = sqlite3.Row
            disc_conn.execute("PRAGMA query_only=ON")
            try:
                edge_count = disc_conn.execute(
                    "SELECT COUNT(*) FROM direct_funding_edges WHERE direct_funder=?",
                    (entity_address,),
                ).fetchone()
                discovery["high_qualified_count"] = edge_count[0] if edge_count else 0
                fam = disc_conn.execute(
                    "SELECT family_id, classification, attribution_state, "
                    "root_cex_infra_category, cex_infra_hop_distance, member_count, creator_count "
                    "FROM candidate_families WHERE root_evidence=? "
                    "ORDER BY member_count DESC LIMIT 1",
                    (entity_address,),
                ).fetchone()
                if fam:
                    discovery.update(dict(fam))
            finally:
                disc_conn.close()
        except (sqlite3.Error, OSError, ImportError):
            pass

        valid_not_high = None
        historical_only = None
        if historical_population is not None:
            accounted = discovery["high_qualified_count"]
            remainder = historical_population - accounted
            if remainder >= 0:
                # Without a per-member classification pass (out of scope for
                # a bounded read-time projection), the remainder is reported
                # as a single VALID_HISTORICAL_ASSOCIATION_OR_HISTORICAL_ONLY
                # bucket rather than guessing an 82/18-style split that would
                # require the full OF-DV34-P3-style reconciliation query.
                valid_not_high = remainder

        candidate_role = None
        if discovery["classification"] == "SERVICE_DISTRIBUTION_CLUSTER":
            candidate_role = "SERVICE_DISTRIBUTION_NETWORK"
        elif discovery["classification"] in ("STRONG_CANDIDATE_FAMILY", "PARTIAL_CANDIDATE_FAMILY"):
            # Funding recurrence is a neutral structural fact.  It cannot
            # establish a provisioner role or common operation identity on
            # its own.
            candidate_role = "FUNDING_STRUCTURE"
        elif discovery["attribution_state"] == "NON_ATTRIBUTIVE_PROVENANCE":
            candidate_role = "CEX_INFRA_PROVENANCE_CLUSTER"

        return {
            "entity_address": entity_address,
            "identity": {
                "canonical_operator_id": canonical_operator_id,
                "authority_state": authority_state,
                "candidate_role": candidate_role,
                "promotion_eligible": authority_state == "CONFIRMED",
            },
            "historical_population": {
                "count": historical_population,
                "source": "creator_funders (main DB)",
            },
            "evidence_qualification": {
                "high_qualified_count": discovery["high_qualified_count"],
                "valid_not_high_or_historical_only": valid_not_high,
                "source": "local_operation_discovery_corpus.db direct_funding_edges",
            },
            "discovery": {
                "family_id": discovery["family_id"],
                "classification": discovery["classification"],
                "attribution_state": discovery["attribution_state"],
                "member_count": discovery["member_count"],
                "creator_count": discovery["creator_count"],
            },
            "funding_topology": {
                "cex_infra_hop_distance": discovery["cex_infra_hop_distance"],
                "root_cex_infra_category": discovery["root_cex_infra_category"],
            },
            "authority_note": "authority_state is read ONLY from operators.status via fetch_by_entity; no field in evidence_qualification or discovery can set or override it.",
        }

    def fetch_summary(self) -> dict:
        empty = {
            "total": 0, "candidates": 0, "review_candidates": 0,
            "provisional": 0, "confirmed": 0, "rejected": 0,
            "review_pending": 0, "merged": 0, "split": 0, "retired": 0,
            "active": 0, "quiet": 0, "dormant": 0, "reactivated": 0,
        }
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS total,"
                    "SUM(CASE WHEN status='CANDIDATE' THEN 1 ELSE 0 END) AS candidates,"
                    "SUM(CASE WHEN status='REVIEW_CANDIDATE' THEN 1 ELSE 0 END) AS review_candidates,"
                    "SUM(CASE WHEN status='PROVISIONAL' THEN 1 ELSE 0 END) AS provisional,"
                    "SUM(CASE WHEN status='CONFIRMED' THEN 1 ELSE 0 END) AS confirmed,"
                    "SUM(CASE WHEN status='REJECTED' THEN 1 ELSE 0 END) AS rejected,"
                    "SUM(CASE WHEN status IN ('REVIEW','MERGE_REVIEW','SPLIT_REVIEW') THEN 1 ELSE 0 END) AS review_pending "
                    "FROM operators"
                ).fetchone()
                if not row:
                    return empty
                result = {**empty, **{key: (value or 0) for key, value in dict(row).items()}}
                tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "operator_identity_state" in tables:
                    lifecycle = conn.execute(
                        "SELECT "
                        "SUM(identity_status='MERGED') merged,SUM(identity_status='SPLIT') split,"
                        "SUM(identity_status='RETIRED') retired,SUM(activity_status='ACTIVE') active,"
                        "SUM(activity_status='QUIET') quiet,SUM(activity_status='DORMANT') dormant,"
                        "SUM(activity_status='REACTIVATED') reactivated "
                        "FROM operator_identity_state"
                    ).fetchone()
                    result.update({key: (value or 0) for key, value in dict(lifecycle).items()})
                return result
        except (sqlite3.Error, OSError):
            return empty
