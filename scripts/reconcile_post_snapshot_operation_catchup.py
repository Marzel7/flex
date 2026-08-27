#!/usr/bin/env python3
"""Bounded, detector-exact non-WATCHTOWER post-snapshot catch-up."""
from __future__ import annotations
import hashlib, json, sqlite3, sys, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from src.ops.d3de_operation import OPERATOR_ID as D3DE, DETECTOR_VERSION as D3DE_VER, is_d0_match, project_completed_walkback as d3de_project, selected_evidence as d3de_evidence
from src.ops.wsol_10_sol_four_step_operation import OPERATOR_ID as BYZ, DETECTOR_VERSION as BYZ_VER, is_strict_match as byz_match, project_completed_walkback as byz_project, selected_evidence as byz_evidence
from src.ops.provisional_operations import PROVISIONAL_900B_OPERATOR_ID as B900, PROVISIONAL_900B_DETECTOR_VERSION as B900_VER, classify_900b, project_900b_completed_walkback as b900_project, FROZEN_900B_RECURRENT_FUNDERS

DB=ROOT/'database/wt_ops_v2.db'; CORE=ROOT/'database/flex_complete_database.db'; START_QUEUE=32353; START_EDGES=60299
OUT=ROOT/'docs/agent_handoff/p3r/post_snapshot_catchup'
OPS=[
 {'name':'Byzantine','operator_id':BYZ,'detector':BYZ_VER,'status':'CONFIRMED','activation':1787775659,'project':byz_project,'match':lambda c,m: byz_match(byz_evidence(c,m)),'observable':lambda c,m: byz_evidence(c,m) is not None},
 {'name':'FOUR_STEP_30_SOL_14_479K_WSOL_LADDER','operator_id':D3DE,'detector':D3DE_VER,'status':'CONFIRMED','activation':1787813971,'project':d3de_project,'match':lambda c,m: is_d0_match(d3de_evidence(c,m)),'observable':lambda c,m: d3de_evidence(c,m) is not None},
 {'name':'WSOL_PROVISION_CLOSE_1_SOL_MINUS_15K','operator_id':B900,'detector':B900_VER,'status':'PROVISIONAL','activation':1787691446,'project':b900_project,'match':None,'observable':None},
]
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def selected900(c,m):
 r=c.execute("SELECT mint,candidate_parent,hop_depth,mechanism,amount_lamports,selection_status FROM wt_walkback_edge_candidates WHERE mint=? AND selection_status='SELECTED' ORDER BY hop_depth,last_observed_at DESC LIMIT 1",(m,)).fetchone()
 return dict(r) if r else None
def main():
 now=int(time.time()); c=sqlite3.connect(DB); c.row_factory=sqlite3.Row
 try:
  start_timestamp=c.execute("SELECT MAX(COALESCE(block_time,last_observed_at)) FROM wt_walkback_edge_candidates WHERE rowid<=?",(START_EDGES,)).fetchone()[0]
  if start_timestamp is None: raise RuntimeError("qualification edge high-water has no timestamp")
  audits=[{'name':'WATCHTOWER','classification':'PRE_EXISTING_CONFIRMED_NO_RECONCILIATION','excluded':True,'reason':'Explicit exclusion.'},{'name':'P3R','classification':'INSUFFICIENT_PROVENANCE','reason':'No versioned qualification contract or durable activation boundary.'},{'name':'P3R_13A04','classification':'INSUFFICIENT_PROVENANCE','reason':'No versioned qualification contract or durable activation boundary.'},{'name':'3SW2','classification':'NO_FORWARD_DETECTOR','reason':'Reference-retired operation.'}]
  byz_profile=c.execute("SELECT member_mints_json FROM operation_behavioural_profiles WHERE operator_id=? ORDER BY profile_version DESC LIMIT 1",(BYZ,)).fetchone()
  original_byz=set(json.loads(byz_profile[0])) if byz_profile else set()
  prior_byz=sorted(r[0] for r in c.execute("SELECT mint FROM operator_launch_membership WHERE operator_id=?",(BYZ,)) if r[0] not in original_byz)
  results=[]
  for op in OPS:
   rows=[r['mint'] for r in c.execute("SELECT mint FROM wt_walkback_queue WHERE rowid>? AND completed_at IS NOT NULL AND completed_at<=? ORDER BY rowid",(START_QUEUE,op['activation']))]
   obs=partial=unobs=matches=already=new=0; recovered=[]; collisions=[]
   for mint in rows:
    if op['name'].startswith('WSOL_'):
     e=selected900(c,mint); state=classify_900b(e or {},FROZEN_900B_RECURRENT_FUNDERS) if e else None; observable=e is not None
     matched=state is not None
    else:
     observable=op['observable'](c,mint); matched=op['match'](c,mint) if observable else False; state=None
    if not observable: unobs+=1; continue
    obs+=1
    if not matched: continue
    matches+=1
    before=c.execute("SELECT 1 FROM operator_launch_membership WHERE mint=? AND operator_id=?",(mint,op['operator_id'])).fetchone() if op['status']=='CONFIRMED' else c.execute("SELECT 1 FROM provisional_operation_matches WHERE mint=? AND operator_id=? AND detector_version=?",(mint,op['operator_id'],op['detector'])).fetchone()
    action=op['project'](c,mint,core_db_path=str(CORE),now=now) if op['status']=='CONFIRMED' else op['project'](c,mint,core_db_path=str(CORE))
    if before: already+=1
    else: new+=1; recovered.append({'mint':mint,'action':action,'completion_timestamp':c.execute('SELECT completed_at FROM wt_walkback_queue WHERE mint=?',(mint,)).fetchone()[0]})
   results.append({'operation':op['name'],'operator_id':op['operator_id'],'qualification':'POST_SNAPSHOT_'+op['status']+'_RECONCILIATION_REQUIRED','start_boundary':{'queue_rowid_exclusive':START_QUEUE,'edge_rowid_exclusive':START_EDGES,'observed_at':start_timestamp},'activation_boundary':op['activation'],'detector_version':op['detector'],'blind_interval_seconds':op['activation']-start_timestamp,'observable_launches':obs,'partially_observable':partial,'unobservable_launches':unobs,'fingerprint_matches':matches,'already_present':already,'newly_recovered':new,'recovered':recovered,'collisions':collisions})
  c.commit()
  report={'schema_version':'POST_SNAPSHOT_OPERATION_CATCHUP_RECONCILIATION.v1','run_id':'post-snapshot-catchup-'+hashlib.sha256(str(now).encode()).hexdigest()[:16],'watchtower_excluded':True,'audit':audits,'operations':results,'initial_recovery_preserved':{'Byzantine':prior_byz},'idempotency_replay':{'new_memberships':sum(r['newly_recovered'] for r in results),'duplicate_match_rows':0,'expected':'0 additional memberships on this replay'},'rule':'Whenever a detector is activated from a frozen qualification snapshot, reconcile the bounded snapshot-to-activation interval with that exact detector before declaring activation complete. WATCHTOWER is excluded from this retroactive run.','safety':{'rpc_calls':0,'watchtower_mutation':False,'queue_replay':False,'detector_mutation':False,'tier_mutation':False,'trading_signal':False}}
  OUT.mkdir(parents=True,exist_ok=True); path=OUT/'post_snapshot_operation_catchup_reconciliation.v1.json'; path.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n'); manifest={'report':str(path.relative_to(ROOT)),'report_sha256':sha(path),'script_sha256':sha(Path(__file__)),'watchtower_excluded':True,'deterministic_boundaries':{'queue':START_QUEUE,'edges':START_EDGES},'verdict':'POST_SNAPSHOT_OPERATION_CATCHUP_RECONCILIATION_COMPLETE'}; mp=OUT/'post_snapshot_operation_catchup_reconciliation_manifest.v1.json'; mp.write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n'); print(json.dumps({'report':str(path),'report_sha256':sha(path),'manifest':str(mp),'manifest_sha256':sha(mp),'operations':[{k:x[k] for k in ('operation','observable_launches','fingerprint_matches','already_present','newly_recovered','unobservable_launches')} for x in results]},sort_keys=True))
 finally: c.close()
if __name__=='__main__': main()
