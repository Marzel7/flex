#!/usr/bin/env python3
"""Record forward-only detector versions after P3R activation provenance recovery."""
from __future__ import annotations
import hashlib,json,sqlite3,time,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; DB=ROOT/'database/wt_ops_v2.db'; OUT=ROOT/'docs/agent_handoff/p3r/legacy_detector_provenance'
OPS=[
 ('777211c3-211e-551b-9310-ff9301570627','P3R','P3R_UNIFIED_WSOL_WRAP_CLOSE_99_999985_SOL.v1',{'selected_route':[[1,'WSOL_WRAP_CLOSE',99999985000]],'principal_atomic_transfer_lamports':99997955720}),
 ('ccb7b1b0-56e1-4543-9e95-3f284bed3943','P3R_13A04','P3R_13A04_FOUR_STEP_30_SOL_LADDER.v1',{'selected_route':[[1,'PLAIN_XFER',29999975000],[2,'WSOL_WRAP_CLOSE',29999980000],[3,'PLAIN_XFER',29999985000],[4,'WSOL_WRAP_CLOSE',29999990000]]}),
]
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 now=int(time.time()); c=sqlite3.connect(DB); rows=[]
 try:
  for oid,name,version,contract in OPS:
   op=c.execute('select created_at,updated_at from operators where operator_id=?',(oid,)).fetchone()
   memberships=c.execute('select count(*),min(assigned_at),max(assigned_at),group_concat(distinct source_population_id) from operator_launch_membership where operator_id=?',(oid,)).fetchone()
   runtime='EARLIEST_PROVEN_ACTIVE_ONLY' if name=='P3R' else 'ACTIVATION_PROVENANCE_UNRECOVERABLE'
   start='CATCHUP_BLOCKED_START_BOUNDARY' if name=='P3R' else 'CATCHUP_BLOCKED_ACTIVATION_BOUNDARY'
   cid=str(uuid.uuid5(uuid.NAMESPACE_URL,f'forward-version:{oid}:{version}'))
   c.execute("INSERT OR IGNORE INTO operation_qualification_contracts(contract_id,operator_id,qualification_category,automation_eligibility,detector_version,parent_mechanism,source_candidate_id,benchmark_json,contract_json,evidence_lineage_json,frozen_edge_highwater,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(cid,oid,'CONFIRMED','ELIGIBLE',version,None,None,json.dumps({'retroactive_activation_claim':False}),json.dumps(contract,sort_keys=True),json.dumps({'effective_from':now,'basis':'version record created after provenance recovery; not a retroactive activation claim'},sort_keys=True),None,now))
   rows.append({'operation':name,'operator_id':oid,'current_detector':contract,'detector_version':version,'registry':{'created_at':op[0],'updated_at':op[1]},'membership':{'count':memberships[0],'earliest':memberships[1],'latest':memberships[2],'sources':memberships[3]},'activation_provenance':runtime,'catchup_eligibility':start,'catchup_executed':False})
  c.commit()
 finally:c.close()
 report={'schema_version':'P3R_LEGACY_DETECTOR_ACTIVATION_PROVENANCE_RECOVERY.v1','operations':rows,'git_history':[{'commit':'26e528b0ae1fa5ed465c4fe9059a259d903fef29','timestamp':'2026-08-25T20:27:40+01:00','significance':'first recoverable history hit for P3R matcher/walkback text; code commit alone does not prove runtime activation'}],'runtime_evidence':{'P3R':'retained worker log contains successful P3R membership admission; DB contains forward source walkback_p3r_unified_matcher_v1','P3R_13A04':'no retained membership or runtime execution record'},'catchup':{'P3R':'not run: legitimate frozen start high-water unrecovered','P3R_13A04':'not run: activation and historical start boundary unrecovered'},'safety':{'rpc_calls':0,'queue_replay':False,'detector_semantics_changed':False,'watchtower_mutation':False,'other_operation_mutation':False,'trading_signal':False}}
 OUT.mkdir(parents=True,exist_ok=True); p=OUT/'p3r_legacy_detector_activation_provenance_recovery.v1.json';p.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n'); m=OUT/'p3r_legacy_detector_activation_provenance_recovery_manifest.v1.json';m.write_text(json.dumps({'report':str(p.relative_to(ROOT)),'report_sha256':sha(p),'script_sha256':sha(Path(__file__)),'verdict':'P3R_LEGACY_DETECTOR_ACTIVATION_PROVENANCE_RECOVERY_COMPLETE'},indent=2,sort_keys=True)+'\n');print(json.dumps({'report':str(p),'report_sha256':sha(p),'manifest':str(m),'manifest_sha256':sha(m)}))
if __name__=='__main__':main()
