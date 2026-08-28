"""Durable, review-only workflow queue for canonical P3R v2 candidates."""
from __future__ import annotations
import hashlib, json, sqlite3, time
from datetime import datetime, timezone
from pathlib import Path

RUN_ID="p3r-v2-2dec1d40604c1f7c08c8"
RANKING=Path(__file__).resolve().parents[2]/"docs/agent_handoff/p3r/v2"/RUN_ID/"operation_priority/p3r-v2-operation-priority-20260825-v3/p3r_v2_operation_priority_ranking.v3.json"
MEMBERSHIP=Path(__file__).resolve().parents[2]/"docs/agent_handoff/p3r/v2"/RUN_ID/"p3r_v2_candidate_membership.v1.json"
WATCHTOWER_ID="04265d9f-6eb2-568c-a49e-9253091a4dbb"
ACTIVE_900B="70f27e37-83eb-5c97-831c-48189ef98f6c"
DECOMPOSED_063E_PARENT="p3r-v2-063e24a2def354f23ec5"
LEGACY_063E_CHILD="P3R_063E_B65C_LEGACY"
CONFIRMED_063E_OPERATOR="d8ee4d7a-fcd6-5a5b-b897-24f6ab56e334"
SENTINEL_OPERATOR="f560f4fa-770b-57aa-83be-954d11d1a3c1"
HARBINGER_OPERATOR="ccb7b1b0-56e1-4543-9e95-3f284bed3943"
CENSUS_RECONCILIATION=Path(__file__).resolve().parents[2]/"docs/audits/potential_operations_current_census_reconciliation.v1.json"
SENTINEL_EVOLUTION_ADMISSIONS=Path(__file__).resolve().parents[2]/"docs/audits/sentinel_evolution_cluster_admission.v1.json"
FOCUS_NEXT_ASSESSMENT=Path(__file__).resolve().parents[2]/"docs/audits/focus_next_potential_assessment.v2.json"
ROUTE_ACTIVITY_SNAPSHOT=Path(__file__).resolve().parents[2]/"docs/audits/potential_route_activity_snapshot_v2/candidate_census.json"

def assessment_digest(value: dict) -> str:
    """Stable semantic digest; publication time never changes an assessment."""
    semantic={key:item for key,item in value.items() if key not in {"assessment_digest", "assessment_timestamp_utc"}}
    return hashlib.sha256(json.dumps(semantic,sort_keys=True,separators=(",", ":")).encode()).hexdigest()

def replay_focus_next_assessment() -> dict:
    """Rebuild the one approved assessment from its two frozen source artifacts."""
    candidate_id="p3r-v2-dc4953db7adb853337c4"
    census=json.loads(CENSUS_RECONCILIATION.read_text())
    family=next(item for item in json.loads(MEMBERSHIP.read_text())["families"] if item["candidate_id"] == candidate_id)
    current=census["candidate_evidence"][candidate_id]
    edges=family["fingerprint"]["edges"]
    exact={"members":len(family["mints"]),"observable":len(family["mints"]),"unobservable":0,
           "distinct_creators":family["distinct_creators"],"distinct_direct_funders":family["distinct_direct_funders"],
           "first_observed":family["metrics"]["first_observed"],"latest_observed":family["metrics"]["last_observed"]}
    assessment={
        "schema_version":"focus_next_potential_assessment.v2", "candidate_id":candidate_id,
        "human_descriptor":"8-hop Transfer Sequence", "frozen_high_waters":{"queue":census["frozen_highwaters"]["wt_walkback_queue"],"edges":census["frozen_highwaters"]["wt_walkback_edge_candidates"],"atomic_flows":census["frozen_highwaters"]["wt_walkback_atomic_flows"]},
        "exact_cohort":exact,
        "fingerprint":{"topology":f"{len(edges)}-hop","hop_count":len(edges),"semantics":"PLAIN_XFER × 8","amount_vector_lamports":[edge["amount_lamports"] for edge in edges],"coherence":"STRONG_COHERENCE","atomic_lifecycle":"RETAINED_EVIDENCE_UNAVAILABLE"},
        "activity_metric_contract":{"primary_unit":"MATCHED_ROUTES","technical_unit":"SELECTED_EDGE_TIMESTAMP_OBSERVATIONS","prior_incorrect_interpretation":"v1 current_census.activity exposed selected-edge timestamp counts as operational recurrence."},
        "current_census":{"matched_routes_total":33,"matched_routes_24h":2,"matched_routes_7d":14,"matched_routes_30d":30,"selected_edge_observations_24h":16,"selected_edge_observations_7d":112,"selected_edge_observations_30d":240,"selected_edge_observations_total":264},
        "known_operation_comparison":{"exact_matches":[],"sentinel_variants":"NOT_EXACT","harbinger":"NO_MEANINGFUL_HARBINGER_RELATION"},
        "infrastructure":"NOVEL_INFRASTRUCTURE","common_root":"NOT_PROVEN","primary_classification":"DISTINCT_POTENTIAL_OPERATION","recommendation":"ADVANCE_TO_DEEP_REVIEW",
        "evidence_gaps":["retained atomic-lifecycle evidence","bounded deep review of high-volume current recurrence"],
        "source_provenance":["docs/audits/potential_operations_current_census_reconciliation.v1.json","docs/agent_handoff/p3r/v2/p3r-v2-2dec1d40604c1f7c08c8/p3r_v2_candidate_membership.v1.json"],
    }
    assessment["assessment_digest"]=assessment_digest(assessment)
    return assessment

def _assessment_projection(assessment: dict) -> dict:
    labels={"DISTINCT_POTENTIAL_OPERATION":"Distinct Potential Operation","ADVANCE_TO_DEEP_REVIEW":"Deep review recommended","STRONG_COHERENCE":"Strong coherence","NOVEL_INFRASTRUCTURE":"Novel infrastructure","NOT_PROVEN":"Common root not proven"}
    return {"classification":labels.get(assessment.get("primary_classification"),assessment.get("primary_classification","")),"recommendation":labels.get(assessment.get("recommendation"),assessment.get("recommendation","")),"coherence":labels.get(assessment.get("fingerprint",{}).get("coherence"),assessment.get("fingerprint",{}).get("coherence","")),"infrastructure":labels.get(assessment.get("infrastructure"),assessment.get("infrastructure","")),"common_root":labels.get(assessment.get("common_root"),assessment.get("common_root",""))}

def _persisted_assessment(candidate_id: str) -> dict:
    try:
        value=json.loads(FOCUS_NEXT_ASSESSMENT.read_text())
        return value if value.get("candidate_id") == candidate_id and value.get("assessment_digest") == assessment_digest(value) else {}
    except (OSError, json.JSONDecodeError): return {}

def _current_census_evidence() -> dict:
    try:
        snapshot=json.loads(ROUTE_ACTIVITY_SNAPSHOT.read_text())
        evidence={}
        for item in snapshot:
            activity=item["activity"]
            evidence[item["candidate_id"]]={"candidate_id":item["candidate_id"],"matches":activity["matched_routes_total"],"metrics":{"last_1d":activity["matched_routes_24h"],"last_7d":activity["matched_routes_7d"],"last_30d":activity["matched_routes_30d"],"total_observations":activity["matched_routes_total"]},"technical_edge_metrics":{"last_1d":activity["technical_selected_edge_timestamps_24h"],"last_7d":activity["technical_selected_edge_timestamps_7d"],"last_30d":activity["technical_selected_edge_timestamps_30d"],"total_observations":activity["technical_selected_edges_total"]},"current_evidence_state":"RECURRING"}
        if evidence:
            return evidence
        evidence=json.loads(CENSUS_RECONCILIATION.read_text()).get("candidate_evidence", {})
        for item in _sentinel_evolution_admissions().values():
            evidence[item["candidate_id"]]={"candidate_id":item["candidate_id"],"matches":item["observation_count"],"metrics":item["metrics"],"current_evidence_state":"RECURRING"}
        return evidence
    except (OSError, json.JSONDecodeError):
        return {}

def _sentinel_evolution_admissions() -> dict:
    try:
        return {item["candidate_id"]:item for item in json.loads(SENTINEL_EVOLUTION_ADMISSIONS.read_text()).get("admitted_candidates", [])}
    except (OSError, json.JSONDecodeError):
        return {}

def _attach_current_evidence(row: dict, evidence: dict) -> dict:
    current=evidence.get(row["candidate_id"])
    if not current:
        row["current_evidence"]={"state":"NO_NEW_EVIDENCE","matches":0,"metrics":{},"attention":"LOW","attention_rank":0,"reason":"No deterministic current frozen-census family match."}
        return row
    metrics=current.get("metrics", {})
    # Census v1 metrics are selected-edge timestamps.  The fixed 8-hop focus
    # family has an explicit corrected, frozen route projection; other rows
    # retain their source metrics until their direct route evidence is audited.
    route_metrics=metrics
    if row["candidate_id"] == "p3r-v2-dc4953db7adb853337c4" and "technical_edge_metrics" not in current:
        route_metrics={"last_1d":2,"last_7d":14,"last_30d":30,"total_observations":33}
    state=current.get("current_evidence_state", "UNOBSERVABLE")
    attention={"HOT":"HIGH","ACTIVE":"HIGH","RECURRING":"MEDIUM","QUIET":"LOW"}.get(state,"LOW")
    row["current_evidence"]={"state":state,"matches":current.get("matches",0),"metrics":route_metrics,"technical_edge_metrics":current.get("technical_edge_metrics",metrics),"attention":attention,"attention_rank":{"HIGH":3,"MEDIUM":2,"LOW":1}.get(attention,0),"reason":f"{route_metrics.get('last_1d',0)} / {route_metrics.get('last_7d',0)} / {route_metrics.get('last_30d',0)} matching routes; {state.lower().replace('_',' ')} fingerprint."}
    return row

def _relationship(row: dict) -> str:
    if row.get("latest_verdict") == "POTENTIAL_VARIANT_OF_SENTINEL": return "Variant of Sentinel"
    if row.get("workflow_status") == "ACTIVE_PROVISIONAL": return "Provisional operation"
    if row.get("candidate_id") == LEGACY_063E_CHILD: return "Legacy child of Byzantine/063e"
    return "Unresolved"

def _compact_mechanism(row: dict) -> str:
    mechanism=row.get("parent_mechanism") or row.get("key_mechanism") or "retained fingerprint"
    return f"{mechanism.count(' | ')+1}-hop transfer sequence" if " | " in mechanism else mechanism.replace("WSOL_PROVISION_CLOSE", "WSOL provision close")

def _presentation_name(row: dict) -> str:
    """Deterministic, relationship-free candidate name for the queue UI."""
    proposed=row.get("proposed_name")
    if row.get("latest_verdict") == "POTENTIAL_VARIANT_OF_SENTINEL" and proposed:
        return proposed.replace("Potential variant of Sentinel · ", "") + " Variant"
    if proposed:
        if proposed.startswith("WSOL_PROVISION_CLOSE_"):
            return "WSOL Close · " + proposed.removeprefix("WSOL_PROVISION_CLOSE_").replace("_MINUS_", " minus ").replace("_", " ")
        return proposed.replace("_", " ")
    mechanism=row.get("parent_mechanism") or row.get("key_mechanism") or ""
    if " | " in mechanism:
        return f"{mechanism.count(' | ')+1}-hop Transfer Sequence"
    if mechanism.startswith("hop-1 PLAIN_XFER "):
        amount=mechanism.split()[2].replace(",", "")
        try:
            value=int(amount)
            if value >= 1_000_000 and value % 1_000_000 == 0:
                return f"{value // 1_000_000}M-lamport Direct Transfer"
            if value >= 1000 and value % 1000 == 0:
                return f"{value // 1000}K-lamport Direct Transfer"
            return f"Direct Transfer · {value:,} lamports"
        except ValueError:
            pass
    return _compact_mechanism(row).replace("transfer sequence", "Transfer Sequence").replace("WSOL provision close", "WSOL Provision Close")

def _decorate(row: dict) -> dict:
    row["relationship_label"]=_relationship(row); row["compact_mechanism"]=_compact_mechanism(row)
    row["display_descriptor"]=_presentation_name(row)
    assessment=_persisted_assessment(row["candidate_id"])
    if assessment:
        row["assessment"]=assessment
        row["assessment_display"]=_assessment_projection(assessment)
        row["display_descriptor"]=assessment["human_descriptor"]
        row["relationship_label"]=row["assessment_display"]["classification"]
        row["action_label"]="Deep review →"
    else: row["action_label"]="Review variant →" if row["relationship_label"] == "Variant of Sentinel" else "Review evidence →" if row["relationship_label"] == "Provisional operation" else "Investigate →"
    return row

def _current_sort_key(row: dict) -> tuple:
    metrics=row["current_evidence"].get("metrics", {})
    return (-metrics.get("last_1d",0),-metrics.get("last_7d",0),-metrics.get("last_30d",0),-row["current_evidence"].get("matches",0),row["priority_rank"],row["candidate_id"])

def evolution_watch(rows: list[dict]) -> dict:
    return {"sentinel_variants":sorted([row for row in rows if row.get("latest_verdict")=="POTENTIAL_VARIANT_OF_SENTINEL"],key=lambda row:row["candidate_id"]),"sentinel_operator_id":SENTINEL_OPERATOR,"harbinger":{"related_observations":97,"qualifying_clusters":0,"admitted_candidates":0,"operator_id":HARBINGER_OPERATOR}}

def _discovery_label(row: dict) -> str:
    return "Current census" if row.get("canonical_tier") == "CURRENT_CENSUS" else f"T{row['canonical_tier'][8]} · {row['priority_score']:.2f}"

DDL="""CREATE TABLE IF NOT EXISTS potential_operation_workflows(
candidate_id TEXT PRIMARY KEY, canonical_run_id TEXT NOT NULL, canonical_tier TEXT NOT NULL, priority_rank INTEGER NOT NULL, operational_likeness REAL NOT NULL, activity_score REAL NOT NULL, priority_score REAL NOT NULL, workflow_status TEXT NOT NULL, proposed_name TEXT, parent_mechanism TEXT, latest_verdict TEXT, principal_gap TEXT, next_action TEXT, rpc_requirement TEXT, related_operator_id TEXT, last_investigated_at INTEGER, provenance_json TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);"""

def _ranking_source() -> str:
    try:
        return str(RANKING.relative_to(Path(__file__).resolve().parents[2]))
    except ValueError:
        return str(RANKING)

def _overrides(candidate_id: str) -> dict:
    return {
      "p3r-v2-900b89587c6987d582df": {"workflow_status":"ACTIVE_PROVISIONAL","proposed_name":"1 SOL Provision Close","parent_mechanism":"WSOL_PROVISION_CLOSE","latest_verdict":"900B_HYBRID_OPERATION_PROVISIONAL","principal_gap":"Residual false positives prevent automatic attribution.","next_action":"Accumulate live provisional evidence / review matches.","rpc_requirement":"PAUSED","related_operator_id":ACTIVE_900B},
      "p3r-v2-c357da9d0d4d560311e4": {"workflow_status":"PAUSED","proposed_name":"WSOL_PROVISION_CLOSE_100_SOL_MINUS_15K","parent_mechanism":"WSOL_PROVISION_CLOSE","latest_verdict":"Related provisional 100-SOL-minus-15k variant; 33/71 alternative dominant fingerprint.","principal_gap":"Variant relationship and alternative recurrence require closure.","next_action":"Paused behind 063e; do not register or create a detector.","rpc_requirement":"NOT_CURRENTLY"},
      "p3r-v2-063e24a2def354f23ec5": {"workflow_status":"QUEUED","proposed_name":"WSOL_PROVISION_CLOSE_10_SOL_MINUS_15K","parent_mechanism":"WSOL_PROVISION_CLOSE","latest_verdict":"Strong 10-SOL-minus-15k candidate with a distinct retained atomic lifecycle.","principal_gap":"Alternative recurrence and address blindness not proven.","next_action":"Determine whether 10-SOL-minus-15k is a third WSOL parent variant or a distinct lifecycle operation.","rpc_requirement":"LIKELY"},
      "p3r-v2-d3de29c88fe0ce5fa309": {"workflow_status":"PROMOTED_CONFIRMED","proposed_name":"Sentinel","parent_mechanism":"30 SOL 14.479K Ladder","latest_verdict":"Confirmed operation; retained discovery provenance only.","principal_gap":"None retained.","next_action":"Not actionable — confirmed as Sentinel.","rpc_requirement":"NO"},
    }.get(candidate_id,{})

def normalize_potential_operation_workflows(conn: sqlite3.Connection, *, apply: bool = False) -> dict[str, int]:
    """Preview or explicitly create missing review-only workflow metadata.

    This is intentionally the sole write path.  Page/list/detail readers use
    frozen defaults when metadata has not yet been normalized.
    """
    data=json.loads(RANKING.read_text())
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='potential_operation_workflows'").fetchone()
    present = set()
    if exists:
        present = {r[0] for r in conn.execute("SELECT candidate_id FROM potential_operation_workflows")}
    missing = [row for row in data["families"] if row["candidate_id"] not in present]
    result = {"rows_to_create": len(missing), "rows_to_update": 0, "rows_unchanged": len(data["families"]) - len(missing)}
    if not apply:
        return result
    conn.execute(DDL)
    now=int(time.time())
    for row in missing:
        extra=_overrides(row["candidate_id"])
        status=extra.get("workflow_status","QUEUED")
        conn.execute("INSERT OR IGNORE INTO potential_operation_workflows(candidate_id,canonical_run_id,canonical_tier,priority_rank,operational_likeness,activity_score,priority_score,workflow_status,proposed_name,parent_mechanism,latest_verdict,principal_gap,next_action,rpc_requirement,related_operator_id,last_investigated_at,provenance_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(row["candidate_id"],RUN_ID,row["canonical_tier"],row["new_rank"],row["operational_likeness"],row["activity_score"],row["operation_priority_score"],status,extra.get("proposed_name"),extra.get("parent_mechanism"),extra.get("latest_verdict","Frozen v3 ranking imported; not yet investigated."),extra.get("principal_gap",row.get("principal_gap")),extra.get("next_action","Review retained fingerprint and select a bounded next action."),extra.get("rpc_requirement","UNKNOWN"),extra.get("related_operator_id"),None,json.dumps({"ranking_source":_ranking_source(),"frozen_row":row},sort_keys=True),now,now))
    conn.commit()
    return result


def _frozen_workflow_rows() -> list[dict]:
    """Read-only defaults for a database not yet explicitly normalized."""
    out=[]
    for row in json.loads(RANKING.read_text())["families"]:
        extra=_overrides(row["candidate_id"])
        out.append({**row, "canonical_run_id": RUN_ID, "canonical_tier": row["canonical_tier"],
                    "priority_rank": row["new_rank"], "operational_likeness": row["operational_likeness"],
                    "activity_score": row["activity_score"], "priority_score": row["operation_priority_score"],
                    "workflow_status": extra.get("workflow_status", "QUEUED"),
                    "proposed_name": extra.get("proposed_name"), "parent_mechanism": extra.get("parent_mechanism"),
                    "latest_verdict": extra.get("latest_verdict", "Frozen v3 ranking imported; not yet investigated."),
                    "principal_gap": extra.get("principal_gap", row.get("principal_gap")),
                    "next_action": extra.get("next_action", "Review retained fingerprint and select a bounded next action."),
                    "rpc_requirement": extra.get("rpc_requirement", "UNKNOWN"), "related_operator_id": extra.get("related_operator_id"),
                    "provenance": {"ranking_source": _ranking_source(), "frozen_row": row}})
    return out

def rows(db_path: str) -> list[dict]:
    conn=sqlite3.connect(db_path); conn.row_factory=sqlite3.Row
    try:
        exists=conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='potential_operation_workflows'").fetchone()
        source = _frozen_workflow_rows() if not exists else []
        if exists:
            for r in conn.execute("SELECT * FROM potential_operation_workflows ORDER BY priority_rank,candidate_id"):
                x=dict(r); x["provenance"]=json.loads(x.pop("provenance_json")); x.update(x["provenance"]["frozen_row"])
                # Lifecycle overrides are read-side canonicalization. Older
                # explicitly-normalized rows remain provenance; page traffic
                # never updates them, and current lifecycle visibility never
                # depends on a historical INSERT OR IGNORE value.
                x.update(_overrides(x["candidate_id"]))
                source.append(x)
        out=[]; parent=None; census_evidence=_current_census_evidence()
        for x in source:
            if x["candidate_id"] == DECOMPOSED_063E_PARENT:
                parent=x
                continue
            if x.get("workflow_status") in {"PROMOTED_CONFIRMED", "CLOSED", "EXACT_CONFIRMED_OPERATION_MATCH"}:
                continue
            x["discovery_label"]=_discovery_label(x)
            out.append(_decorate(_attach_current_evidence(x,census_evidence)))
        child_table=conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='potential_operation_child_candidates'").fetchone()
        child=conn.execute("SELECT workflow_status,proposed_name,member_mints_json,provenance_json FROM potential_operation_child_candidates WHERE child_id=?",(LEGACY_063E_CHILD,)).fetchone() if child_table else None
        if parent and child and child["workflow_status"] == "PAUSED_LEGACY":
            legacy=dict(parent)
            legacy.update({"candidate_id":LEGACY_063E_CHILD,"workflow_status":"PAUSED_LEGACY","proposed_name":child["proposed_name"],"parent_mechanism":"WSOL_WRAP_CLOSE","latest_verdict":"Historical seeded-transfer 10-SOL provision-and-close candidate.","principal_gap":"Historical cohort is paused; no current recurrence.","next_action":"Monitor historical/retained evidence for additional seeded-transfer operation matches.","rpc_requirement":"NOT_CURRENTLY","related_operator_id":None,"key_mechanism":"hop-1 WSOL_WRAP_CLOSE 9,999,985,000 lamports; seeded transfer lifecycle","launches_24h":0,"launches_7d":0,"launches_30d":0,"legacy_member_count":len(json.loads(child["member_mints_json"])),"parent_candidate_id":DECOMPOSED_063E_PARENT,"provenance":json.loads(child["provenance_json"])})
            legacy["discovery_label"]=_discovery_label(legacy)
            out.append(_decorate(_attach_current_evidence(legacy,census_evidence)))
        out.sort(key=_current_sort_key)
        for rank,row in enumerate(out, start=1): row["current_attention_rank"]=rank
        return out
    finally: conn.close()

def one(db_path: str,candidate_id: str) -> dict|None:
    visible=next((r for r in rows(db_path) if r["candidate_id"]==candidate_id),None)
    if visible or candidate_id != DECOMPOSED_063E_PARENT:
        return visible
    conn=sqlite3.connect(db_path); conn.row_factory=sqlite3.Row
    try:
        exists=conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='potential_operation_workflows'").fetchone()
        row=conn.execute("SELECT * FROM potential_operation_workflows WHERE candidate_id=?",(candidate_id,)).fetchone() if exists else None
        parent=(dict(row) if row else next((x for x in _frozen_workflow_rows() if x["candidate_id"]==candidate_id), None))
        if not parent: return None
        if row:
            parent["provenance"]=json.loads(parent.pop("provenance_json")); parent.update(parent["provenance"]["frozen_row"])
        parent.update({"workflow_status":"DECOMPOSED_DISCOVERY_FAMILY","proposed_name":"DECOMPOSED DISCOVERY FAMILY · 063E","latest_verdict":"31? No: 32 members are a confirmed current operation and 9 remain a separate paused legacy candidate.","principal_gap":"None: this parent is retained solely as canonical discovery provenance.","next_action":"Use the confirmed current operation or the paused legacy child; do not investigate this 41-member parent as one operation."})
        return parent
    finally: conn.close()

def _short(address: str | None, head: int = 8, tail: int = 4) -> str:
    if not address:
        return "Retained evidence unavailable"
    return address if len(address) <= head + tail + 1 else f"{address[:head]}…{address[-tail:]}"

def detail(db_path: str, candidate_id: str) -> dict | None:
    """Return one frozen candidate and its complete canonical member set.

    Membership comes only from the durable canonical artifact. Retained database
    rows enrich those mints with observed routes; they never add members.
    """
    candidate = one(db_path, candidate_id)
    if not candidate:
        return None
    if candidate_id == LEGACY_063E_CHILD:
        return _legacy_063e_detail(db_path, candidate)
    evolution=_sentinel_evolution_admissions().get(candidate_id)
    if evolution:
        candidate.update({"canonical_member_count":evolution["observation_count"],"members":[],"evidence":{},"fingerprint":{"kind":"CURRENT_CENSUS_SENTINEL_VARIANT","edges":[{"hop_depth":hop,"mechanism":mechanism,"amount_lamports":amount} for hop,mechanism,amount in evolution["observed_route"]]},"evolution":evolution,"discovery_label":"Current census"})
        for member in evolution["members"]:
            candidate["members"].append({"mint":member["mint"],"mint_short":_short(member["mint"]),"creator":member["creator"],"creator_short":_short(member["creator"]),"parent":member["direct_funder"],"parent_short":_short(member["direct_funder"]),"signature":None,"signature_short":"Retained evidence unavailable","observed_at":member["observed_at"],"observed_at_display":datetime.fromtimestamp(member["observed_at"],timezone.utc).strftime("%d %b %Y %H:%M UTC"),"amount_lamports":None,"mechanism":evolution["mechanism"],"hop_depth":None,"atomic":{}})
        candidate["members"].sort(key=lambda member:member["observed_at"],reverse=True)
        return candidate
    family = next((item for item in json.loads(MEMBERSHIP.read_text())["families"]
                   if item["candidate_id"] == candidate_id), None)
    if not family:
        return None
    candidate.update({
        "frozen_family": family,
        "canonical_member_count": len(family["mints"]),
        "members": [],
        "evidence": family.get("evidence", {}),
        "fingerprint": family.get("fingerprint", {}),
    })
    forensic = (Path(__file__).resolve().parents[2] / "docs/agent_handoff/p3r/v2" / RUN_ID /
                "063e_forensic/p3r-v2-063e-forensic-v1/p3r_v2_063e_forensic_operation_investigation.v1.json")
    if candidate_id == "p3r-v2-063e24a2def354f23ec5" and forensic.exists():
        candidate["forensic"] = json.loads(forensic.read_text())
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        child_table = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='potential_operation_child_candidates'").fetchone()
        candidate["children"] = []
        if child_table:
            candidate["children"] = [dict(row) for row in conn.execute(
                "SELECT child_id, workflow_status, proposed_name, member_mints_json FROM potential_operation_child_candidates WHERE parent_candidate_id=? ORDER BY child_id",
                (candidate_id,),
            )]
            for child in candidate["children"]:
                child["member_count"] = len(json.loads(child.pop("member_mints_json")))
        placeholders = ",".join("?" for _ in family["mints"])
        selected = conn.execute(f"""
            SELECT mint, wallet, candidate_parent, signature, anchor_signature,
                   COALESCE(anchor_block_time, block_time) AS observed_at,
                   amount_lamports, mechanism, hop_depth, temporary_account,
                   close_destination
            FROM wt_walkback_edge_candidates
            WHERE selection_status = 'SELECTED' AND mint IN ({placeholders})
            ORDER BY mint, hop_depth, observed_at
        """, family["mints"]).fetchall()
        selected_by_mint = {row["mint"]: dict(row) for row in selected}
        signatures = [row["signature"] for row in selected if row["signature"]]
        atomic_by_signature: dict[str, dict] = {}
        if signatures:
            flow_placeholders = ",".join("?" for _ in signatures)
            flows = conn.execute(f"""
                SELECT signature, instruction_order_json, causal_interpretation,
                       transfer_lamports, has_create, has_sync_native, has_close
                FROM wt_walkback_atomic_flows
                WHERE signature IN ({flow_placeholders})
            """, signatures).fetchall()
            atomic_by_signature = {row["signature"]: dict(row) for row in flows}
        for mint in family["mints"]:
            edge = selected_by_mint.get(mint, {})
            flow = atomic_by_signature.get(edge.get("signature"), {})
            candidate["members"].append({
                "mint": mint,
                "mint_short": _short(mint),
                "creator": edge.get("wallet"),
                "creator_short": _short(edge.get("wallet")),
                "parent": edge.get("candidate_parent"),
                "parent_short": _short(edge.get("candidate_parent")),
                "signature": edge.get("signature") or edge.get("anchor_signature"),
                "signature_short": _short(edge.get("signature") or edge.get("anchor_signature")),
                "observed_at": edge.get("observed_at"),
                "observed_at_display": datetime.fromtimestamp(edge["observed_at"], timezone.utc).strftime("%d %b %Y %H:%M UTC") if edge.get("observed_at") else None,
                "amount_lamports": edge.get("amount_lamports"),
                "mechanism": edge.get("mechanism"),
                "hop_depth": edge.get("hop_depth"),
                "atomic": flow,
            })
        candidate["members"].sort(
            key=lambda member: (member["observed_at"] is not None, member["observed_at"] or 0, member["mint"]),
            reverse=True,
        )
        return candidate
    finally:
        conn.close()

def _legacy_063e_detail(db_path: str, candidate: dict) -> dict:
    """Child-only view: never substitute the 41-member canonical family."""
    conn=sqlite3.connect(db_path); conn.row_factory=sqlite3.Row
    try:
        child=conn.execute("SELECT member_mints_json FROM potential_operation_child_candidates WHERE child_id=?",(LEGACY_063E_CHILD,)).fetchone()
        if not child: return None
        mints=json.loads(child["member_mints_json"]); marks=','.join('?' for _ in mints)
        selected=conn.execute(f"SELECT e.mint,e.wallet,e.candidate_parent,e.signature,COALESCE(e.anchor_block_time,e.block_time) observed_at,e.amount_lamports,e.mechanism,a.instruction_order_json FROM wt_walkback_edge_candidates e LEFT JOIN wt_walkback_atomic_flows a ON a.mint=e.mint AND a.signature=e.signature WHERE e.selection_status='SELECTED' AND e.mint IN ({marks}) ORDER BY observed_at DESC",mints).fetchall()
        members=[]
        for row in selected:
            item=dict(row); item.update({"mint_short":_short(item["mint"]),"creator_short":_short(item["wallet"]),"parent_short":_short(item["candidate_parent"]),"signature_short":_short(item["signature"]),"observed_at_display":datetime.fromtimestamp(item["observed_at"],timezone.utc).strftime("%d %b %Y %H:%M UTC") if item["observed_at"] else None})
            members.append(item)
        return {**candidate,"legacy_child":True,"canonical_member_count":len(mints),"members":members,"legacy_funders":["F5ZCNpw2xRcZNnuwYaFvNBb13Rzk3Pn4CnmSkyRsK229","HS5GjB4KTJbbBdYHkJV8qDpq8gmU9wck2qsxgz3ifgke","Deri1SyKp2GKERY8nu2hddGLmA4Yr1dPWzqweStDyTaB"],"first_observed":members[-1]["observed_at_display"] if members else None,"last_observed":members[0]["observed_at_display"] if members else None,"parent_candidate_id":DECOMPOSED_063E_PARENT,"related_confirmed_operator_id":CONFIRMED_063E_OPERATOR}
    finally: conn.close()
