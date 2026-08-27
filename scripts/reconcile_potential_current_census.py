#!/usr/bin/env python3
"""Read-only Potential Operations reconciliation against P3R_CURRENT_QUEUE_CENSUS.

The fixed high-waters are the census contract.  This tool writes only its
immutable report, never the source database or workflow tables.
"""
from __future__ import annotations
import argparse, hashlib, json, sqlite3, sys
from collections import defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.ops.operation_fingerprint_drift import compare_route, _expected_route
from src.ops.p3r_v2_tiering import activity_metrics, base_fingerprint, stable_candidate_id

HW={"wt_walkback_queue":35620,"wt_walkback_edge_candidates":67599,"wt_walkback_atomic_flows":7747}
DB=Path("database/wt_ops_v2.db")
OUT=Path("docs/audits/potential_operations_current_census_reconciliation.v1.json")
RANKING=Path("docs/agent_handoff/p3r/v2/p3r-v2-2dec1d40604c1f7c08c8/operation_priority/p3r-v2-operation-priority-20260825-v3/p3r_v2_operation_priority_ranking.v3.json")

def canon(x): return json.dumps(x,sort_keys=True,separators=(",",":"))
def digest(x): return hashlib.sha256(canon(x).encode()).hexdigest()
def state(m):
    s=m.get("activity_state")
    return {"VERY_HIGH_ACTIVITY":"HOT","HIGH_ACTIVITY":"ACTIVE","REGULAR_ACTIVITY":"RECURRING","DORMANT":"QUIET"}.get(s,"UNOBSERVABLE")

def run(db:Path=DB):
    c=sqlite3.connect(f"file:{db}?mode=ro",uri=True); c.row_factory=sqlite3.Row
    try:
        c.execute("PRAGMA query_only=ON"); c.execute("BEGIN")
        queue={r["mint"]:dict(r) for r in c.execute("select mint,creator,funder_wallet from wt_walkback_queue where rowid<=?",(HW["wt_walkback_queue"],))}
        selected=defaultdict(list); times=defaultdict(list)
        for r in c.execute("select mint,hop_depth,mechanism,amount_lamports,block_time from wt_walkback_edge_candidates where rowid<=? and selection_status='SELECTED' order by mint,hop_depth,signature",(HW["wt_walkback_edge_candidates"],)):
            selected[r["mint"]].append((r["hop_depth"],r["mechanism"],r["amount_lamports"]))
            if r["block_time"] is not None: times[r["mint"]].append(int(r["block_time"]))
        cutoff=max((t for v in times.values() for t in v),default=0)
        groups={}
        for mint,edges in selected.items():
            q=queue.get(mint)
            if not q or not q.get("creator") or not q.get("funder_wallet"): continue
            fp=base_fingerprint(edges)
            if not fp["edges"] or not any(e["amount_lamports"] is not None for e in fp["edges"]): continue
            cid=stable_candidate_id(fp); g=groups.setdefault(cid,{"mints":[],"creators":set(),"funders":set(),"times":[],"fingerprint":fp})
            g["mints"].append(mint);g["creators"].add(q["creator"]);g["funders"].add(q["funder_wallet"]);g["times"].extend(times[mint])
        families={}
        for cid,g in groups.items():
            m=sorted(set(g["mints"])); metrics=activity_metrics(g["times"],cutoff) if g["times"] else {"activity_state":"UNOBSERVABLE"}
            if len(m) < 3 or len(g["creators"]) < 2 or len(g["funders"]) < 2: continue
            families[cid]={"candidate_id":cid,"matches":len(m),"distinct_creators":len(g["creators"]),"distinct_direct_funders":len(g["funders"]),"metrics":metrics,"current_evidence_state":state(metrics),"fingerprint":g["fingerprint"]}
        existing={row["candidate_id"] for row in json.loads(RANKING.read_text())["families"]}
        # Clusters use only route dimensions, never wallet addresses.
        clusters={}
        for target in ("FOUR_STEP_30_SOL_14_479K_WSOL_LADDER","P3R_13A04"):
            expected=_expected_route(c,target); found=defaultdict(list)
            for mint,edges in selected.items():
                observed=tuple((int(a),str(b),int(d)) for a,b,d in edges) if edges and all(d is not None for _,_,d in edges) else None
                classification,matching,differing=compare_route(expected,observed)
                if classification.startswith("NEAR_MATCH"):
                    found[canon({"route":observed,"differing":differing,"matching":matching})].append(mint)
            result=[]
            for key,mints in sorted(found.items(),key=lambda x:(-len(x[1]),x[0])):
                vals=[min(times[m]) for m in mints if times[m]]; metrics=activity_metrics(vals,cutoff) if vals else {"activity_state":"UNOBSERVABLE"}
                creators={queue[m]["creator"] for m in mints if m in queue and queue[m].get("creator")}; funders={queue[m]["funder_wallet"] for m in mints if m in queue and queue[m].get("funder_wallet")}
                route_value=json.loads(key)["route"]
                candidate_id=stable_candidate_id(base_fingerprint(route_value or [])) if route_value else None
                qualifies=bool(len(mints)>=3 and len(creators)>=2 and len(funders)>=2 and metrics.get("activity_state")!="UNOBSERVABLE")
                relationship=("INSUFFICIENT_EVIDENCE" if not qualifies else ("POTENTIAL_MUTATION" if target=="FOUR_STEP_30_SOL_14_479K_WSOL_LADDER" else "RELATED_30_SOL_LADDER_OPERATION"))
                result.append({"cluster_id":target+":"+digest(json.loads(key))[:16],"count":len(mints),"changed":json.loads(key)["differing"],"route_digest":digest(route_value),"metrics":metrics,"distinct_creators":len(creators),"distinct_direct_funders":len(funders),"existing_candidate_id":candidate_id if candidate_id in existing else None,"qualification_result":"PASSES_EXISTING_COHERENT_FAMILY_MINIMUMS" if qualifies else "DOES_NOT_PASS_EXISTING_COHERENT_FAMILY_MINIMUMS","relationship":relationship})
            clusters[target]=result
        evidence={cid:family for cid,family in families.items() if cid in existing}
        return {"schema_version":"POTENTIAL_OPERATIONS_CURRENT_CENSUS_RECONCILIATION.v1","frozen_highwaters":HW,"family_summary":{"coherent_families":len(families),"existing_potential_matches":len(evidence)},"candidate_evidence":evidence,"sentinel_clusters":clusters["FOUR_STEP_30_SOL_14_479K_WSOL_LADDER"],"harbinger_clusters":clusters["P3R_13A04"],"safety":{"source_writes":0,"workflow_writes":0,"membership_writes":0,"provider_calls":0}}
    finally: c.close()

def main():
    p=argparse.ArgumentParser();p.add_argument("--output",type=Path,default=OUT);a=p.parse_args()
    result=run(); result["reconciliation_digest"]=digest(result); a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,sort_keys=True,indent=2)+"\n")
    print(json.dumps({"digest":result["reconciliation_digest"],"families":result["family_summary"]["coherent_families"],"sentinel_clusters":len(result["sentinel_clusters"]),"harbinger_clusters":len(result["harbinger_clusters"])}))
if __name__=="__main__": main()
