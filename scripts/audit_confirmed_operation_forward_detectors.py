#!/usr/bin/env python3
"""Durable read-only matrix for active confirmed-operation forward detectors."""
from __future__ import annotations
import hashlib,json,sqlite3
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; DB=ROOT/'database/wt_ops_v2.db'; OUT=ROOT/'docs/agent_handoff/confirmed_operation_detector_audit'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 c=sqlite3.connect(DB); c.row_factory=sqlite3.Row
 try:
  rows=[]
  for r in c.execute("SELECT o.operator_id,o.display_name,o.status,d.disposition,q.detector_version,q.qualification_category FROM operators o JOIN operation_registry_dispositions d USING(operator_id) LEFT JOIN operation_qualification_contracts q USING(operator_id) WHERE d.disposition='ACTIVE_MANUAL' AND o.status='CONFIRMED' ORDER BY o.display_name"):
   x=dict(r);x['membership_count']=c.execute('SELECT count(*) FROM operator_launch_membership WHERE operator_id=?',(x['operator_id'],)).fetchone()[0];rows.append(x)
 finally:c.close()
 details={
 'P3R':{'detector':'P3R_UNIFIED_WSOL_WRAP_CLOSE_99_999985_SOL.v1','fingerprint':'selected WSOL_WRAP_CLOSE 99,999,985,000 + atomic transfer 99,997,955,720','style':'PURE_BEHAVIOURAL','hook':'admit_unambiguous_p3r_match in completed-walkback hook','projector':'operator_launch_membership + activity snapshot'},
 'P3R_13A04':{'detector':'P3R_13A04_FOUR_STEP_30_SOL_LADDER.v1','fingerprint':'PLAIN 29,999,975,000 → WSOL 29,999,980,000 → PLAIN 29,999,985,000 → WSOL 29,999,990,000','style':'PURE_BEHAVIOURAL','hook':'same P3R matcher in completed-walkback hook','projector':'operator_launch_membership + activity snapshot'},
 'Byzantine':{'detector':'WSOL_10_SOL_FOUR_STEP_PROVISION_CLOSE.v1','style':'PURE_BEHAVIOURAL','hook':'project_completed_walkback','projector':'confirmed_operation_matches + membership + activity'},
 'FOUR_STEP_30_SOL_14_479K_WSOL_LADDER':{'detector':'D3DE_D0_EXACT_SELECTED_FOUR_STEP_LADDER.v1','style':'PURE_BEHAVIOURAL','hook':'project_d3de_completed_walkback','projector':'confirmed_operation_matches + membership + activity'},
 'WATCHTOWER':{'detector':'strict canonical verified-route predicate','style':'HYBRID_BEHAVIOUR_INFRASTRUCTURE','hook':'canonical WATCHTOWER integration','projector':'strict membership + activity'},
 }
 for row in rows: row.update(details.get(row['display_name'],{'detector':'UNVERSIONED_OR_UNCLEAR','style':'UNVERSIONED_OR_UNCLEAR'}));row['invariant']='PASS' if row.get('detector')!='UNVERSIONED_OR_UNCLEAR' else 'FAIL'
 report={'schema_version':'CONFIRMED_OPERATIONS_FORWARD_DETECTOR_AUDIT.v1','confirmed_operations':rows,'collision_matrix':[{'a':'P3R','b':'P3R_13A04','classification':'MUTUALLY_DISCRIMINATING','reason':'non-overlapping exact selected route'},{'a':'P3R_13A04','b':'FOUR_STEP_30_SOL_14_479K_WSOL_LADDER','classification':'MUTUALLY_DISCRIMINATING','reason':'established 0/9 cross-detector result'},{'a':'Byzantine','b':'FOUR_STEP_30_SOL_14_479K_WSOL_LADDER','classification':'MUTUALLY_DISCRIMINATING','reason':'different selected amount/atomic route'}],'provisional_900b':{'status':'PROVISIONAL','hook':'project_900b_completed_walkback','promotion':False},'safety':{'historical_catchup':False,'queue_replay':False,'rpc_calls':0,'trading_signal':False},'verdict':'CONFIRMED_OPERATIONS_FORWARD_DETECTOR_AUDIT_COMPLETE'};OUT.mkdir(parents=True,exist_ok=True);p=OUT/'confirmed_operations_forward_detector_audit.v1.json';p.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n');m=OUT/'confirmed_operations_forward_detector_audit_manifest.v1.json';m.write_text(json.dumps({'report':str(p.relative_to(ROOT)),'report_sha256':sha(p),'script_sha256':sha(Path(__file__))},indent=2,sort_keys=True)+'\n');print(json.dumps({'report':str(p),'report_sha256':sha(p),'manifest':str(m),'manifest_sha256':sha(m),'rows':rows}))
if __name__=='__main__':main()
