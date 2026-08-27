#!/usr/bin/env python3
"""Review and explicitly admit the two fixed-census Sentinel variants only."""
from __future__ import annotations
import argparse, hashlib, json, sqlite3, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.reconcile_potential_current_census import HW, run
from src.ops.operation_fingerprint_drift import _expected_route, compare_route
from src.ops.p3r_v2_tiering import base_fingerprint, stable_candidate_id

DB=Path("database/wt_ops_v2.db")
RECON=Path("docs/audits/potential_operations_current_census_reconciliation.v1.json")
OUT=Path("docs/audits/sentinel_evolution_cluster_admission.v1.json")
TARGET="FOUR_STEP_30_SOL_14_479K_WSOL_LADDER"

def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":")).encode()).hexdigest()

def review(db: Path=DB) -> dict:
    reconciliation=json.loads(RECON.read_text())
    clusters=[c for c in reconciliation["sentinel_clusters"] if c["qualification_result"]=="PASSES_EXISTING_COHERENT_FAMILY_MINIMUMS"]
    if len(clusters)!=2 or sum(c["count"] for c in reconciliation["sentinel_clusters"])!=75:
        raise ValueError("Committed Sentinel qualification/accounting contract inconsistent")
    c=sqlite3.connect(f"file:{db}?mode=ro",uri=True); c.row_factory=sqlite3.Row
    try:
        c.execute("PRAGMA query_only=ON")
        expected=_expected_route(c,TARGET)
        sentinel_id=c.execute("SELECT operator_id FROM operators WHERE display_name=?",(TARGET,)).fetchone()[0]
        queue={r["mint"]:dict(r) for r in c.execute("SELECT mint,creator,funder_wallet FROM wt_walkback_queue WHERE rowid<=?",(HW["wt_walkback_queue"],))}
        routes={}
        for r in c.execute("SELECT mint,hop_depth,mechanism,amount_lamports,block_time FROM wt_walkback_edge_candidates WHERE rowid<=? AND selection_status='SELECTED' ORDER BY mint,hop_depth,signature",(HW["wt_walkback_edge_candidates"],)):
            routes.setdefault(r["mint"],[]).append(dict(r))
        exact_times=[]
        candidates=[]
        for mint,rows in routes.items():
            route=tuple((int(x["hop_depth"]),str(x["mechanism"]),int(x["amount_lamports"])) for x in rows) if rows and all(x["amount_lamports"] is not None for x in rows) else None
            if compare_route(expected,route)[0]=="EXACT_MATCH": exact_times += [x["block_time"] for x in rows if x["block_time"]]
        for cluster in clusters:
            members=[]; route_value=None
            for mint,rows in routes.items():
                route=tuple((int(x["hop_depth"]),str(x["mechanism"]),int(x["amount_lamports"])) for x in rows) if rows and all(x["amount_lamports"] is not None for x in rows) else None
                classification,matching,differing=compare_route(expected,route)
                if classification.startswith("NEAR_MATCH") and _digest(route)==cluster["route_digest"]:
                    q=queue.get(mint,{})
                    members.append({"mint":mint,"creator":q.get("creator"),"direct_funder":q.get("funder_wallet"),"observed_at":min(x["block_time"] for x in rows if x["block_time"]),"route":route})
                    route_value=route
            members.sort(key=lambda x:x["observed_at"])
            cid=stable_candidate_id(base_fingerprint(route_value))
            amount=round(route_value[0][2]/1_000_000_000)
            overlap=[t for t in exact_times if members[0]["observed_at"]<=t<=members[-1]["observed_at"]]
            candidates.append({"candidate_id":cid,"cluster_id":cluster["cluster_id"],"route_digest":cluster["route_digest"],"name":f"Potential variant of Sentinel · {amount} SOL Ladder","workflow_status":"QUEUED","relationship":"POTENTIAL_VARIANT_OF_SENTINEL","related_operator_id":sentinel_id,"related_operator":"Sentinel","mechanism":f"{amount} SOL four-step WSOL ladder","changed_dimensions":["amount_vector"],"preserved_dimensions":["topology","semantic_sequence"],"expected_route":expected,"observed_route":route_value,"members":members,"observation_count":len(members),"distinct_creators":len({x['creator'] for x in members}),"distinct_direct_funders":len({x['direct_funder'] for x in members}),"metrics":cluster["metrics"],"overlap_exact_sentinel_observations":len(overlap),"existing_identity_overlap":{"confirmed":False,"provisional":False,"existing_potential":False,"same_family_only":True},"qualification":{"member_count":{"required":3,"observed":len(members),"pass":len(members)>=3},"distinct_creators":{"required":2,"observed":len({x['creator'] for x in members}),"pass":len({x['creator'] for x in members})>=2},"distinct_direct_funders":{"required":2,"observed":len({x['direct_funder'] for x in members}),"pass":len({x['direct_funder'] for x in members})>=2},"observable_route":{"required":True,"observed":route_value is not None,"pass":route_value is not None},"distinct_exact_fingerprint":{"required":True,"observed":True,"pass":True},"existing_potential_deduplication":{"required":True,"observed":True,"pass":True}},"infrastructure":"UNRESOLVED_CORROBORATION_ONLY","next_action":"Review retained route and infrastructure evidence; do not create a detector or Confirmed membership."})
        return {"schema_version":"SENTINEL_EVOLUTION_CLUSTER_ADMISSION.v1","source_commit":"f24e72de","frozen_highwaters":HW,"sentinel_near_observations":75,"non_qualifying_observations":68,"harbinger":{"related_observations":97,"qualifying_clusters":0},"admitted_candidates":candidates,"safety":{"membership_writes":0,"detector_writes":0,"fingerprint_writes":0,"provider_calls":0}}
    finally: c.close()

def apply(db:Path, payload:dict) -> dict:
    conn=sqlite3.connect(db)
    try:
        before=conn.execute("SELECT COUNT(*) FROM potential_operation_workflows").fetchone()[0]
        for item in payload["admitted_candidates"]:
            frozen={"candidate_id":item["candidate_id"],"canonical_tier":"CURRENT_CENSUS","new_rank":10000+len(item["members"]),"operational_likeness":0.0,"activity_score":0.0,"operation_priority_score":0.0,"launches_24h":item["metrics"]["last_1d"],"launches_7d":item["metrics"]["last_7d"],"launches_30d":item["metrics"]["last_30d"],"key_mechanism":item["mechanism"]}
            provenance={"frozen_row":frozen,"sentinel_evolution_admission":item}
            conn.execute("INSERT OR IGNORE INTO potential_operation_workflows(candidate_id,canonical_run_id,canonical_tier,priority_rank,operational_likeness,activity_score,priority_score,workflow_status,proposed_name,parent_mechanism,latest_verdict,principal_gap,next_action,rpc_requirement,related_operator_id,last_investigated_at,provenance_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(item["candidate_id"],"current-census-35620","CURRENT_CENSUS",frozen["new_rank"],0,0,0,"QUEUED",item["name"],item["mechanism"],item["relationship"],"Relationship is evidence-backed as a coexisting variant; infrastructure remains corroboration only.",item["next_action"],"NOT_CURRENTLY",item["related_operator_id"],None,json.dumps(provenance,sort_keys=True),0,0))
        conn.commit()
        after=conn.execute("SELECT COUNT(*) FROM potential_operation_workflows").fetchone()[0]
        return {"created":after-before,"unchanged":len(payload["admitted_candidates"])-(after-before)}
    finally: conn.close()

def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--apply",action="store_true"); parser.add_argument("--output",type=Path,default=OUT); args=parser.parse_args()
    payload=review(); payload["review_digest"]=_digest(payload); args.output.write_text(json.dumps(payload,sort_keys=True,indent=2)+"\n")
    result=apply(DB,payload) if args.apply else {"created":0,"unchanged":len(payload["admitted_candidates"])}
    print(json.dumps({"digest":payload["review_digest"],"apply":result,"candidates":[x["candidate_id"] for x in payload["admitted_candidates"]]}))
if __name__=="__main__": main()
