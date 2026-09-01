#!/usr/bin/env python3
"""Append-only successor observation for the frozen P3R shadow signal."""
import argparse,hashlib,json,os,sqlite3,time
from pathlib import Path
E={'membership':'cfbed26959c0956e7200a614462d9d604572e54e352a2d4a5de8341e1f22bf16','prior_manifest':'0a740a794bebb6738dd021544d39ae6a225bf8a6bebf5ce1fdb1b73ac309d1ab','prior_features':'60e766f9072a8c49ee025eb64a350389fc8e90897297c50bcf780ee58a89952c','prior_results':'192e43669c9c2c98576dc151c739b9183f911fdb7764146d75bd2f8f3ac571d3'}
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for x in iter(lambda:f.read(1048576),b''):h.update(x)
 return h.hexdigest()
def cj(x):return json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=True)
def write(p,x,jsonl=False):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
 if p.exists():raise RuntimeError('immutable output exists')
 fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o644)
 with os.fdopen(fd,'w',encoding='ascii') as f:
  if jsonl:
   for v in x:f.write(cj(v)+'\n')
  else:f.write(cj(x)+'\n')
  f.flush();os.fsync(f.fileno())
def rows(p):return [json.loads(x) for x in open(p,encoding='ascii') if x.strip()]
def main():
 q=argparse.ArgumentParser();q.add_argument('--membership',required=True);q.add_argument('--prior-dir',required=True);q.add_argument('--db',default='database/wt_ops_v2.db');q.add_argument('--output-dir',required=True);a=q.parse_args()
 prior=Path(a.prior_dir); paths={'prior_manifest':prior/'p3r_shadow_run_manifest.v1.json','prior_features':prior/'p3r_shadow_features.v1.jsonl','prior_results':prior/'p3r_shadow_match_results.v1.jsonl','membership':Path(a.membership)}
 for k,p in paths.items():
  if sha(p)!=E[k]:raise RuntimeError('input digest mismatch '+k)
 active=json.load(open(paths['membership']))['candidates'];seen_features=rows(paths['prior_features']);seen_results=rows(paths['prior_results']);seen={x['mint'] for x in seen_results}
 if len(active)!=127 or len(seen)!=1:raise RuntimeError('unexpected frozen accounting')
 start=time.time_ns(); c=sqlite3.connect('file:'+str(Path(a.db).resolve())+'?mode=ro',uri=True);c.execute('begin')
 before=os.stat(a.db); schema=c.execute('pragma schema_version').fetchone()[0]; high=c.execute('select max(id),count(*) from wt_watchtower_launches').fetchone(); max_recorded=c.execute('select max(recorded_at) from wt_watchtower_launches').fetchone()[0]
 launches=list(c.execute('select id,mint,create_time,recorded_at,create_signature from wt_watchtower_launches where mint is not null and id<=? order by id',(high[0],)))
 newlaunch=[x for x in launches if x[1] not in seen]; feats=[];results=[]
 for ident,mint,ct,rt,cs in newlaunch:
  ed=list(c.execute("select candidate_parent,amount_lamports,block_time,signature,mechanism,hop_depth,evidence_key from wt_walkback_edge_candidates where mint=? and selection_status='SELECTED' order by hop_depth,block_time,signature,evidence_key",(mint,)))
  zero=any(x[1]==0 for x in ed);complete=bool(ed) and not zero; fp=None
  if complete:fp=([len(ed),max(x[5] for x in ed),len({x[0] for x in ed})],[x[1] for x in ed],[x[4] for x in ed])
  feats.append({'mint':mint,'launch_rowid':ident,'create_time':ct,'recorded_at':rt,'create_signature':cs,'selected_edges':[{'candidate_parent':x[0],'amount_lamports':x[1],'block_time':x[2],'signature':x[3],'mechanism':x[4],'hop_depth':x[5],'evidence_key':x[6]} for x in ed],'zero_lamport_present':zero,'feature_state':'COMPLETE' if complete else 'ABSTAIN_INCOMPLETE_OR_ZERO','fingerprint':fp})
  ms=[] if not complete else [x for x in active if x['structural_fingerprint']==fp[0] and x['amount_fingerprint_lamports']==fp[1] and x['mechanism_fingerprint']==fp[2]]
  results.append({'mint':mint,'result':'ABSTAIN' if not complete else 'MATCH' if ms else 'NO_MATCH','candidate_ids':[x['candidate_id'] for x in ms],'address_blind':True})
 c.close();end=time.time_ns();after=os.stat(a.db)
 cumulative=seen_results+results; M=sum(x['result']=='MATCH' for x in cumulative);N=sum(x['result']=='NO_MATCH' for x in cumulative);A=sum(x['result']=='ABSTAIN' for x in cumulative); matched={v for x in cumulative for v in x['candidate_ids']}; novel={x['candidate_id'] for x in active if x.get('known_reference_classification')=='NOVEL_BEHAVIOURAL_CANDIDATE'}
 bind={'frozen_membership_sha256':E['membership'],'prior_shadow_manifest_sha256':E['prior_manifest'],'prior_shadow_features_sha256':E['prior_features'],'prior_shadow_results_sha256':E['prior_results'],'evaluator_code_sha256':sha(__file__)}
 snap={'database_path':str(Path(a.db).resolve()),'read_only_mode':True,'transaction':'BEGIN read snapshot','schema_version':schema,'acquisition_start_ns':start,'acquisition_end_ns':end,'launch_rowid_high_water':high[0],'launch_row_count_at_high_water':high[1],'max_recorded_at':max_recorded,'db_before':{'inode':before.st_ino,'size':before.st_size,'mtime_ns':before.st_mtime_ns},'db_after':{'inode':after.st_ino,'size':after.st_size,'mtime_ns':after.st_mtime_ns},'filesystem_metadata_observational_only':True}
 run={'schema_version':'p3r-prospective-shadow-successor-v1','principal_verdict':'P3R_SHADOW_EVALUATION_COMPLETE','signal_status':'SHADOW_PROMISING_MORE_EVIDENCE_REQUIRED','bindings':bind,'snapshot':snap,'new_launches':len(newlaunch),'consistent_snapshot_proven':True,'writer_activity_compatible':True,'reason':'cumulative prospective denominator remains too small for selectivity judgement'}
 ledger={'bindings':bind,'previously_seen_mints':sorted(seen),'newly_evaluated_mints':[x['mint'] for x in results],'cumulative_seen_mints':sorted(seen|{x['mint'] for x in results}),'policy':'mint evaluated once under frozen version; no definition tuning'}
 metrics={'bindings':bind,'new_launches':len(results),'cumulative_unseen':len(cumulative),'cumulative_complete_features':len(cumulative)-A,'MATCH':M,'NO_MATCH':N,'ABSTAIN':A,'watchtower_evaluable':len(cumulative)-A,'watchtower_matched':M,'watchtower_recall':M/(len(cumulative)-A) if len(cumulative)!=A else None,'background_denominator':0,'background_match_rate':None,'novel_candidates_with_recurrence':len(matched&novel),'multiple_prospective_recurrences':sum(sum(i in x['candidate_ids'] for x in cumulative)>=2 for i in matched),'zero_abstentions':A,'additional_evidence_required':True}
 out=Path(a.output_dir);write(out/'p3r_shadow_successor_manifest.v1.json',run);write(out/'p3r_shadow_seen_ledger.v1.json',ledger);write(out/'p3r_shadow_successor_features.v1.jsonl',feats,True);write(out/'p3r_shadow_successor_results.v1.jsonl',results,True);write(out/'p3r_shadow_successor_metrics.v1.json',metrics)
 print(cj({'verdict':run['principal_verdict'],'signal_status':run['signal_status'],'metrics':metrics,'digests':{x.name:sha(x) for x in out.iterdir()}}))
if __name__=='__main__':main()
