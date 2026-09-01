"""Idempotently resolve C357's former standalone workflow into Leviathan behaviour evidence."""
from __future__ import annotations
import hashlib, json, sqlite3
from pathlib import Path
OP="777211c3-211e-551b-9310-ff9301570627"; CID="p3r-v2-c357da9d0d4d560311e4"; SID="p3r-subtype-03f916dfa97fb93a4b9c"; STATE="RESOLVED_AS_LEVIATHAN_BEHAVIOUR"
def _digest(d):
 x=dict(d); x.pop("artifact_digest",None); return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def resolve(db_path="database/wt_ops_v2.db"):
 c=sqlite3.connect(db_path); c.row_factory=sqlite3.Row
 try:
  before_members=c.execute("select count(*) from operator_launch_membership where operator_id=?",(OP,)).fetchone()[0]; before=dict(c.execute("select * from potential_operation_workflows where candidate_id=?",(CID,)).fetchone()); sub=dict(c.execute("select * from operator_subtypes where subtype_id=? and parent_operator_id=?",(SID,OP)).fetchone()); projection=c.execute("select count(*),sum(case when m.operator_id=? then 1 else 0 end) from operator_subtype_projection p left join operator_launch_membership m on m.mint=p.mint where p.subtype_id=?",(OP,SID)).fetchone()
  if projection[0]!=56 or projection[1]!=50: raise RuntimeError("C357 projection invariant failed")
  if before["workflow_status"] != STATE:
   c.execute("update potential_operation_workflows set workflow_status=?, related_operator_id=?, latest_verdict=?, principal_gap=?, next_action=? where candidate_id=?",(STATE,OP,"Resolved as qualified Leviathan behaviour; C357 lineage retained.","Compatibility remains distinct from attribution.","View Leviathan behaviour examples; automatic attribution remains off.",CID)); c.commit()
  after=dict(c.execute("select * from potential_operation_workflows where candidate_id=?",(CID,)).fetchone()); after_members=c.execute("select count(*) from operator_launch_membership where operator_id=?",(OP,)).fetchone()[0]
  if after_members!=before_members: raise RuntimeError("primary membership changed")
  v={"schema_version":"C357_RESOLVED_AS_LEVIATHAN_BEHAVIOUR.v1","provider_calls":0,"canonical_parent":OP,"c357_lineage":CID,"behaviour_projection":SID,"display_name":"100 SOL WSOL Provision Close","workflow_before":"PAUSED","workflow_after":after["workflow_status"],"relationship":"RESOLVED_AS_LEVIATHAN_BEHAVIOUR","primary_membership_before":before_members,"primary_membership_after":after_members,"supported_projection":projection[0],"primary_overlap":projection[1],"projection_only":projection[0]-projection[1],"compatible_unresolved":json.loads(sub["evidence_json"])["compatible_unresolved_excluded"],"detector":"OFF","monitoring":"SHADOW_ONLY","trading":"OFF","idempotent":True,"ui_category":"LEVIATHAN_BEHAVIOUR_NOT_ACTIONABLE_POTENTIAL"}; v["artifact_digest"]=_digest(v); return v
 finally: c.close()
if __name__=="__main__":
 v=resolve(); Path("docs/audits/c357_resolved_as_leviathan_behaviour.v1.json").write_text(json.dumps(v,indent=2,sort_keys=True)+"\n"); print(v["artifact_digest"])
