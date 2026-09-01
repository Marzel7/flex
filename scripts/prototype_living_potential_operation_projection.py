#!/usr/bin/env python3
"""Read-only prototype: derive a versioned current C357 assessment from retained evidence."""
from __future__ import annotations
import hashlib,json,sqlite3,time
from pathlib import Path
DB='database/wt_ops_v2.db'; CID='p3r-v2-c357da9d0d4d560311e4'; OUT=Path('docs/audits/living_potential_operation_projection_prototype.v1.json')
def sha(v):return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def main():
 c=sqlite3.connect(DB);c.row_factory=sqlite3.Row
 row=c.execute('select * from potential_operation_workflows where candidate_id=?',(CID,)).fetchone(); members=[x[0] for x in c.execute('select mint from p3r_v2_candidate_membership where candidate_id=?',(CID,))]
 q="SELECT count(DISTINCT mint) n,count(DISTINCT candidate_parent) funders,count(DISTINCT wallet) creators,count(DISTINCT signature) signatures FROM wt_walkback_edge_candidates WHERE selection_status='SELECTED' AND mint IN (%s)"%(','.join('?'*len(members)))
 selected=dict(c.execute(q,members).fetchone()); alt=c.execute("SELECT count(DISTINCT mint) FROM wt_walkback_edge_candidates WHERE selection_status='ALTERNATIVE' AND mint IN (%s)"%(','.join('?'*len(members))),members).fetchone()[0]; flows=c.execute("SELECT count(DISTINCT mint) FROM wt_walkback_atomic_flows WHERE mint IN (%s)"%(','.join('?'*len(members))),members).fetchone()[0];c.close()
 previous={'version':'frozen_c357_cohort','member_tokens':71,'distinct_direct_funders':49,'source':'immutable C357 frozen evidence'}
 current={'version':'derived_current','candidate_id':CID,'candidate_membership_projection':len(members),'selected_edge_members':selected['n'],'selected_direct_funders':selected['funders'],'selected_creators':selected['creators'],'selected_signatures':selected['signatures'],'alternative_edge_members':alt,'atomic_flow_members':flows,'assessment':'RESOLVED_AS_LEVIATHAN_BEHAVIOUR','qualification':'shadow-only; no membership or detector effect','last_evidence_update':int(time.time())}
 out={'schema':'LIVING_POTENTIAL_OPERATION_PROJECTION_PROTOTYPE_V1','read_only':True,'stable_candidate_identity':CID,'previous_assessment_version':previous,'derived_current_assessment':current,'lineage':{'workflow_provenance':json.loads(row['provenance_json']),'immutable_snapshot_run':row['canonical_run_id'],'recompute_scope':'candidate member set plus selected/alternative edges and atomic flows for affected mints'},'propagation_contract':['new edge for mint -> candidate membership resolver','affected candidate recompute only','append immutable assessment version','current projection pointer advances or reverses based on evidence'],'safety':'no database write; no provider call; no membership/promotion/detector change'};out['digest']=sha(out);OUT.write_text(json.dumps(out,indent=2,sort_keys=True)+'\n');print(out['digest'])
if __name__=='__main__':main()
