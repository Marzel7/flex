#!/usr/bin/env python3
"""Idempotently register the approved 063e current child and project B1 history."""
from __future__ import annotations
import hashlib, json, sqlite3, sys, time, uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from src.ops.manual_registry import refresh_operator_activity_snapshot
from src.ops.provisional_operations import ensure_schema as ensure_qualification_schema
from src.ops.wsol_10_sol_four_step_operation import AMOUNT_LAMPORTS,ATOMIC_SEQUENCE,DETECTOR_VERSION,DISPLAY_NAME,OPERATOR_ID,SOURCE_CHILD_ID,ensure_schema,project_completed_walkback
OPS_DB=ROOT/'database'/'wt_ops_v2.db'; CORE_DB=ROOT/'database'/'flex_complete_database.db'
QUAL=ROOT/'docs/agent_handoff/p3r/v2/p3r-v2-2dec1d40604c1f7c08c8/063e_child_qualification/p3r-v2-063e-child-qualification-v1/p3r_v2_063e_child_operation_qualification.v1.json'
OUT=ROOT/'docs/agent_handoff/p3r/v2/p3r-v2-2dec1d40604c1f7c08c8/063e_child_qualification/confirmed_registration/wsol_10_sol_four_step_confirmed_registration.v1.json'
def digest(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 source=json.loads(QUAL.read_text()); child=next(x for x in source['children'] if x['child_id']==SOURCE_CHILD_ID); mints=child['members']; now=int(time.time()); conn=sqlite3.connect(OPS_DB); conn.row_factory=sqlite3.Row
 try:
  ensure_schema(conn); ensure_qualification_schema(conn)
  conn.execute("INSERT INTO operators(operator_id,status,confidence,summary,review_state,display_name,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(operator_id) DO UPDATE SET status='CONFIRMED',confidence='CERTAIN',summary=excluded.summary,review_state='REVIEWED',display_name=excluded.display_name,updated_at=excluded.updated_at",(OPERATOR_ID,'CONFIRMED','CERTAIN','Confirmed from P3R 063e B1: exact 10-SOL-minus-15K selected WSOL close plus four-step atomic lifecycle.','REVIEWED',DISPLAY_NAME,now,now))
  conn.execute("INSERT INTO operation_registry_dispositions(operator_id,disposition,manual_reviewer,reason,source_candidate_id,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(operator_id) DO UPDATE SET disposition=excluded.disposition,manual_reviewer=excluded.manual_reviewer,reason=excluded.reason,source_candidate_id=excluded.source_candidate_id,updated_at=excluded.updated_at",(OPERATOR_ID,'ACTIVE_MANUAL','approved_063e_registration','Approved B1 address-independent structural operation',SOURCE_CHILD_ID,now))
  provenance={'parent_candidate_id':'p3r-v2-063e24a2def354f23ec5','child_id':SOURCE_CHILD_ID,'detector_version':DETECTOR_VERSION,'predicate':{'selection_status':'SELECTED','hop_depth':1,'mechanism':'WSOL_WRAP_CLOSE','amount_lamports':AMOUNT_LAMPORTS,'atomic_sequence':ATOMIC_SEQUENCE},'controls':{'historical_positive':32,'legacy_five_step_rejected':9,'same_amount_non_four_step_rejected':14}}
  profile_id=str(uuid.uuid5(uuid.NAMESPACE_URL,f'profile:{OPERATOR_ID}:1'))
  conn.execute("INSERT INTO operation_behavioural_profiles(profile_id,operator_id,source_candidate_id,profile_version,status,provenance_json,member_mints_json,created_at,reviewed_at,reviewer) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(operator_id,profile_version) DO UPDATE SET provenance_json=excluded.provenance_json,member_mints_json=excluded.member_mints_json,status=excluded.status,reviewed_at=excluded.reviewed_at,reviewer=excluded.reviewer",(profile_id,OPERATOR_ID,SOURCE_CHILD_ID,1,'CONFIRMED',json.dumps(provenance,sort_keys=True),json.dumps(mints),now,now,'approved_063e_registration'))
  contract_id=str(uuid.uuid5(uuid.NAMESPACE_URL,f'contract:{OPERATOR_ID}:{DETECTOR_VERSION}'))
  conn.execute("INSERT INTO operation_qualification_contracts(contract_id,operator_id,qualification_category,automation_eligibility,detector_version,parent_mechanism,source_candidate_id,benchmark_json,contract_json,evidence_lineage_json,frozen_edge_highwater,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(operator_id,detector_version) DO UPDATE SET benchmark_json=excluded.benchmark_json,contract_json=excluded.contract_json,evidence_lineage_json=excluded.evidence_lineage_json",(contract_id,OPERATOR_ID,'CONFIRMED','ELIGIBLE',DETECTOR_VERSION,'WSOL_WRAP_CLOSE',SOURCE_CHILD_ID,json.dumps(provenance['controls'],sort_keys=True),json.dumps(provenance['predicate'],sort_keys=True),json.dumps({'qualification_artifact':str(QUAL.relative_to(ROOT)),'sha256':digest(QUAL)},sort_keys=True),60299,now))
  conn.commit(); actions={mint:project_completed_walkback(conn,mint,core_db_path=str(CORE_DB),now=now) for mint in mints}
  if set(actions.values())-{'admitted','already_present'}: raise RuntimeError(f'historical B1 projection failed: {actions}')
  conn.execute("UPDATE potential_operation_child_candidates SET workflow_status='CONFIRMED_REGISTERED',updated_at=? WHERE child_id=?",(now,SOURCE_CHILD_ID)); conn.commit(); activity=refresh_operator_activity_snapshot(conn,OPERATOR_ID,core_db_path=str(CORE_DB),now=now)
  output={'schema_version':'WSOL_10_SOL_FOUR_STEP_CONFIRMED_REGISTRATION.v1','registered_at':now,'operator_id':OPERATOR_ID,'display_name':DISPLAY_NAME,'source_child_id':SOURCE_CHILD_ID,'historical_member_count':len(mints),'projection_actions':actions,'activity':activity,'source_artifact':str(QUAL.relative_to(ROOT)),'source_artifact_sha256':digest(QUAL),'detector':provenance['predicate'],'controls':provenance['controls']}; OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(output,indent=2,sort_keys=True)+'\n'); print(json.dumps({'operator_id':OPERATOR_ID,'members':len(mints),'artifact':str(OUT),'sha256':digest(OUT)},sort_keys=True))
 finally: conn.close()
if __name__=='__main__': main()
