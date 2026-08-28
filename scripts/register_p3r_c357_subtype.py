#!/usr/bin/env python3
"""Bounded allowlist-only, non-owning C357 subtype registration."""
from __future__ import annotations
import hashlib,json,sqlite3,time
from pathlib import Path
R=Path(__file__).resolve().parents[1]; DB=R/'database/wt_ops_v2.db'; MIG=R/'migrations/003_add_operator_subtypes.sql'; OUT=R/'docs/audits/p3r_c357_subtype_registration.v1.json'
P='777211c3-211e-551b-9310-ff9301570627'; S='p3r-subtype-03f916dfa97fb93a4b9c'; C='p3r-v2-c357da9d0d4d560311e4'
def sha(x):return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def main():
 q=json.loads((R/'docs/audits/c357_operation_qualification_review.v1.json').read_text()); rows=q['population']['supported_launches']; assert len(rows)==56 and len({x['mint'] for x in rows})==56
 c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; c.execute('PRAGMA foreign_keys=ON'); before=c.execute('select count(*) from operator_launch_membership where operator_id=?',(P,)).fetchone()[0]; assert before==109
 c.executescript(MIG.read_text()); now=int(time.time()); evidence={'qualification_path':'docs/audits/c357_operation_qualification_review.v1.json','qualification_digest':q['deterministic_digest'],'allowlist_only':True,'compatible_unresolved_excluded':105,'automatic_attribution':'OFF','trading':'OFF'}; ed=sha(evidence)
 try:
  c.execute('BEGIN'); c.execute("insert into operator_subtypes values(?,?,?,?,?,?,?,?,?,?,?,?) on conflict(subtype_id) do update set evidence_json=excluded.evidence_json,evidence_digest=excluded.evidence_digest",(S,P,C,'100 SOL WSOL Provision Close','BEHAVIOURAL_INFRASTRUCTURE_SUBTYPE','EVIDENCE_BACKED','PARTIAL_REVIEW_ONLY','SHADOW_ONLY','OFF',json.dumps(evidence,sort_keys=True),ed,now))
  for x in rows:
   ref={'DUTB_WALLET_POOL':'docs/audits/c357_dutb_common_funder_rpc.v1.json','A':'docs/audits/c357_remaining_upstream_funders.v1.json','B':'docs/audits/c357_branch_b_chronology.v1.json','C':'docs/audits/c357_remaining_upstream_funders.v1.json'}[x['branch']]
   c.execute('insert into operator_subtype_projection values(?,?,?,?,?,?) on conflict(subtype_id,mint) do update set branch=excluded.branch,evidence_reference=excluded.evidence_reference,evidence_json=excluded.evidence_json',(S,x['mint'],x['branch'],ref,json.dumps({'independent_evidence':x['independent_evidence'],'signature':x['signature']},sort_keys=True),now))
  projected={x[0] for x in c.execute('select mint from operator_subtype_projection where subtype_id=?',(S,))}; primary={x[0] for x in c.execute('select mint from operator_launch_membership where operator_id=?',(P,))}; assert len(projected)==56 and len(projected&primary)==50 and len(projected-primary)==6 and c.execute('select count(*) from operator_launch_membership where operator_id=?',(P,)).fetchone()[0]==109
  c.commit()
 except: c.rollback();raise
 r={'schema_version':'P3R_C357_SUBTYPE_REGISTRATION.v1','subtype_id':S,'parent_operator_id':P,'projection_count':len(projected),'overlap_primary_p3r':len(projected&primary),'projection_only':len(projected-primary),'unresolved_excluded':105,'p3r_primary_before':before,'p3r_primary_after':109,'allowlist_only_projection':True,'detector':'OFF','trading':'OFF','safety':{'primary_membership_changed':False,'workflow_changed':False,'detector_changed':False,'trading_changed':False}};r['digest']=sha(r);OUT.write_text(json.dumps(r,indent=2,sort_keys=True)+'\n');print(json.dumps(r));c.close()
if __name__=='__main__':main()
