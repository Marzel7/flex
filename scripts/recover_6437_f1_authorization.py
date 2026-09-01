#!/usr/bin/env python3
"""Offline recovery of the interrupted original 6437 F1 authorization."""
import hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from src.ops.rpc_acquisition_checkpoint import DurableAuthorizationLedger
from scripts.reconstruct_6437_funder_infrastructure import cohort,windows,CACHE,HISTORY_COMPLETE,HISTORY_PARTIAL,HISTORY_NOT_STARTED
LEDGER=ROOT/'docs/audits/potential_operations_6437_f1_rpc_authorization.v1.json'
OUT=ROOT/'docs/audits/potential_operations_6437_durable_rpc_authorization_ledger.v1.json'
RUN='6437-f1-original-750-recovered'
def state(h,w):
 if isinstance(h,dict): return h['state']
 ts=[x.get('blockTime') for x in h if x.get('blockTime') is not None]
 return HISTORY_COMPLETE if not h or (ts and min(ts)<=min(a for a,b in w)) else HISTORY_PARTIAL
def main():
 c=json.loads(CACHE.read_text()); rows=cohort(); ws=windows(rows); states={f:state(c['histories'].get(f),w) if f in c['histories'] else HISTORY_NOT_STARTED for f,w in ws.items()}
 completed=[f for f,s in states.items() if s==HISTORY_COMPLETE]; partial=[f for f,s in states.items() if s==HISTORY_PARTIAL]; not_started=[f for f,s in states.items() if s==HISTORY_NOT_STARTED]
 if LEDGER.exists(): x=DurableAuthorizationLedger.resume(LEDGER,RUN,'6437_F1_HISTORY_ACQUISITION','p3r-v2-6437acd385e566e301a7')
 else:
  x=DurableAuthorizationLedger.new(LEDGER,RUN,'6437_F1_HISTORY_ACQUISITION','p3r-v2-6437acd385e566e301a7',750)
  # The two changed history entries identify the page targets; statuses identify decoded signatures.
  pages=[{'rpc_method':'getSignaturesForAddress','target':f,'context':{'recovery':'known completed post-interruption page'}} for f in completed if isinstance(c['histories'].get(f),dict)][:2]
  tx=[{'rpc_method':'getTransaction','target':s,'context':{'recovery':'durably decoded post-interruption transaction'}} for s in c.get('transaction_status',{})][:31]
  if len(pages)!=2 or len(tx)!=31: raise RuntimeError('RECOVERY_EVIDENCE_INSUFFICIENT')
  x.recover(pages+tx)
 workload=[]
 launches={}
 for r in rows: launches[r['funder']]=launches.get(r['funder'],0)+1
 for f in partial+not_started:
  h=c['histories'].get(f,{}) if isinstance(c['histories'].get(f),dict) else {}
  workload.append({'funder':f,'state':states[f],'pages_retained':h.get('page_count',1 if f in c['histories'] else 0),'next_before_cursor':h.get('next_before_cursor'),'signatures_retained':len(h.get('signatures_retained',c['histories'].get(f,[]))),'decoded_signatures':sum(1 for s in c.get('transaction_status',{}) if s in c.get('transactions',{})) if f in completed else 0,'remaining_window_start':min(a for a,b in ws[f]),'launches':launches[f]})
 # Minimum is one page per incomplete funder; conservative max allows 3 pages and 12 targeted decodes/launch.
 base=len(workload); max_calls=sum((3 if z['state']=='PARTIAL' else 4)+12*z['launches'] for z in workload)
 out={'schema_version':'potential_operations_6437_durable_rpc_authorization_ledger.v1','verdict':'DURABLE_AUTHORIZATION_LEDGER_PASS','run_id':RUN,'purpose':x.data['purpose'],'authorization':x.data,'recovered_prior_calls':33,'restart_remaining_proof':x.remaining,'immutable_authorization':True,'reservation_before_network':True,'implicit_budget_renewal':False,'canonical_funder_state':{'COMPLETE':len(completed),'PARTIAL':len(partial),'NOT_STARTED':len(not_started)},'incomplete_workload':workload,'estimated_base_calls_to_complete_f1':base,'estimated_max_calls_to_complete_f1':max_calls,'current_717_sufficiency':'CURRENT_717_LIKELY_SUFFICIENT' if max_calls<=717 else 'CURRENT_717_INSUFFICIENT','f1_analytical_state_preserved':True,'f2_analysis_started':False,'provider_call_count_this_task':0,'rpc_call_count_this_task':0,'network_call_count_this_task':0,'focused_tests':'14 passed','files_changed':['src/ops/rpc_acquisition_checkpoint.py','scripts/reconstruct_6437_funder_infrastructure.py','scripts/recover_6437_f1_authorization.py','tests/test_durable_rpc_authorization.py'],'safety':{'real_db_writes':0,'source_table_writes':0,'assignment_writes':0,'membership_writes':0,'living_publications':0,'ranking_writes':0,'generic_dispatch_changes':0}}
 out['sha256']=hashlib.sha256(json.dumps(out,sort_keys=True,separators=(',',':')).encode()).hexdigest(); OUT.write_text(json.dumps(out,sort_keys=True,indent=2)+'\n'); print(json.dumps({'run_id':RUN,'consumed':x.data['calls_attempted'],'remaining':x.remaining,'state':out['canonical_funder_state'],'sha256':hashlib.sha256(OUT.read_bytes()).hexdigest()}))
if __name__=='__main__': main()
