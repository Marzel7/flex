#!/usr/bin/env python3
"""Corrected P3R shadow successor with complete historical exclusion."""
import argparse, hashlib, json, os, sqlite3, time
from pathlib import Path

EXPECTED={"corpus":"38632f80231e29bfe686360898329331f88cf593e7dbac09c4f08a1aa58da651","membership":"cfbed26959c0956e7200a614462d9d604572e54e352a2d4a5de8341e1f22bf16","prior_manifest":"0a740a794bebb6738dd021544d39ae6a225bf8a6bebf5ce1fdb1b73ac309d1ab","prior_results":"192e43669c9c2c98576dc151c739b9183f911fdb7764146d75bd2f8f3ac571d3"}
def sha(p):
 h=hashlib.sha256()
 with open(p,"rb") as f:
  for b in iter(lambda:f.read(1048576),b""):h.update(b)
 return h.hexdigest()
def canon(x):return json.dumps(x,sort_keys=True,separators=(",",":"),ensure_ascii=True)
def eligible_mints(raw_mints,historical_exclusion,prospective_seen):
 return sorted(set(raw_mints)-set(historical_exclusion)-set(prospective_seen))
def write_new(p,x,jsonl=False):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
 if p.exists():raise RuntimeError("immutable output exists: "+str(p))
 fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o644)
 with os.fdopen(fd,"w",encoding="ascii") as f:
  if jsonl:
   for r in x:f.write(canon(r)+"\n")
  else:f.write(canon(x)+"\n")
  f.flush();os.fsync(f.fileno())
def jsonl(p):return [json.loads(x) for x in open(p,encoding="ascii") if x.strip()]
def main():
 p=argparse.ArgumentParser();p.add_argument("--corpus",required=True);p.add_argument("--membership",required=True);p.add_argument("--prior-dir",required=True);p.add_argument("--quarantined-dir",required=True);p.add_argument("--db",default="database/wt_ops_v2.db");p.add_argument("--output-dir",required=True);a=p.parse_args()
 prior=Path(a.prior_dir); prior_m=prior/'p3r_shadow_run_manifest.v1.json';prior_r=prior/'p3r_shadow_match_results.v1.jsonl'
 for k,v in (("corpus",a.corpus),("membership",a.membership),("prior_manifest",prior_m),("prior_results",prior_r)):
  if sha(v)!=EXPECTED[k]:raise RuntimeError("digest mismatch: "+k)
 historical={r['mint'] for r in jsonl(a.corpus)}
 if len(historical)!=28883:raise RuntimeError("historical exclusion count is not 28883")
 prior_results=jsonl(prior_r); seen={r['mint'] for r in prior_results}
 if len(seen)!=1:raise RuntimeError("valid predecessor seen ledger mismatch")
 active=json.load(open(a.membership))["candidates"]
 if len(active)!=127:raise RuntimeError("active candidate accounting mismatch")
 start=time.time_ns();c=sqlite3.connect('file:'+str(Path(a.db).resolve())+'?mode=ro',uri=True);c.execute('begin');st=os.stat(a.db)
 high=c.execute('select max(id),count(*),max(recorded_at) from wt_watchtower_launches').fetchone();schema=c.execute('pragma schema_version').fetchone()[0]
 raw=list(c.execute('select id,mint,create_time,recorded_at,create_signature from wt_watchtower_launches where mint is not null and id<=? order by id',(high[0],)))
 raw_mints=[r[1] for r in raw]; eligible=eligible_mints(raw_mints,historical,seen)
 if set(eligible)&historical or set(eligible)&seen:raise RuntimeError("unseen exclusion overlap")
 lookup={r[1]:r for r in raw};features=[];results=[]
 for mint in eligible:
  ident,_,ct,rt,cs=lookup[mint]; ed=list(c.execute("select candidate_parent,amount_lamports,block_time,signature,mechanism,hop_depth,evidence_key from wt_walkback_edge_candidates where mint=? and selection_status='SELECTED' order by hop_depth,block_time,signature,evidence_key",(mint,)))
  zero=any(x[1]==0 for x in ed);complete=bool(ed) and not zero;fp=None
  if complete:fp=([len(ed),max(x[5] for x in ed),len({x[0] for x in ed})],[x[1] for x in ed],[x[4] for x in ed])
  ms=[] if not complete else [x for x in active if x['structural_fingerprint']==fp[0] and x['amount_fingerprint_lamports']==fp[1] and x['mechanism_fingerprint']==fp[2]]
  features.append({'mint':mint,'launch_rowid':ident,'create_time':ct,'recorded_at':rt,'create_signature':cs,'fingerprint':fp,'zero_lamport_present':zero,'feature_state':'COMPLETE' if complete else 'ABSTAIN_INCOMPLETE_OR_ZERO','provenance':'single SQLite read-only transaction'})
  results.append({'mint':mint,'result':'ABSTAIN' if not complete else 'MATCH' if ms else 'NO_MATCH','candidate_ids':[x['candidate_id'] for x in ms],'address_blind':True})
 c.close();end=time.time_ns();post=os.stat(a.db)
 cumulative=prior_results+results;M=sum(r['result']=='MATCH' for r in cumulative);N=sum(r['result']=='NO_MATCH' for r in cumulative);A=sum(r['result']=='ABSTAIN' for r in cumulative);matched={v for r in cumulative for v in r['candidate_ids']};novel={x['candidate_id'] for x in active if x.get('known_reference_classification')=='NOVEL_BEHAVIOURAL_CANDIDATE'}
 bindings={'historical_corpus_sha256':EXPECTED['corpus'],'frozen_membership_sha256':EXPECTED['membership'],'valid_predecessor_manifest_sha256':EXPECTED['prior_manifest'],'valid_predecessor_results_sha256':EXPECTED['prior_results'],'quarantined_invalid_successor_path':str(Path(a.quarantined_dir).resolve()),'quarantined_invalid_successor_excluded':True,'evaluator_code_sha256':sha(__file__)}
 manifest={'schema_version':'p3r-corrected-shadow-successor-v1','principal_verdict':'P3R_SHADOW_EVALUATION_COMPLETE' if eligible else 'P3R_SHADOW_EVALUATION_PREFLIGHT_READY','signal_status':'SHADOW_PROMISING_MORE_EVIDENCE_REQUIRED','bindings':bindings,'exclusion_proof':{'historical_exclusion_count':len(historical),'historical_exclusion_sha256':hashlib.sha256(('\n'.join(sorted(historical))+'\n').encode()).hexdigest(),'prospective_seen_count':len(seen),'prospective_seen_sha256':hashlib.sha256(('\n'.join(sorted(seen))+'\n').encode()).hexdigest(),'raw_source_candidate_count':len(raw_mints),'historical_mints_excluded':len(set(raw_mints)&historical),'previously_seen_mints_excluded':len(set(raw_mints)&seen),'truly_unseen_count':len(eligible),'historical_overlap':0,'prior_prospective_overlap':0},'snapshot':{'path':str(Path(a.db).resolve()),'read_only':True,'transaction':'BEGIN','schema_version':schema,'start_ns':start,'end_ns':end,'launch_rowid_high_water':high[0],'launch_row_count':high[1],'max_recorded_at':high[2],'before':{'inode':st.st_ino,'size':st.st_size,'mtime_ns':st.st_mtime_ns},'after':{'inode':post.st_ino,'size':post.st_size,'mtime_ns':post.st_mtime_ns}},'invalid_successor':'INVALID_HISTORICAL_POPULATION_LEAKAGE; immutable, excluded from all metrics and lineage'}
 metrics={'current_pass':{'MATCH':sum(r['result']=='MATCH' for r in results),'NO_MATCH':sum(r['result']=='NO_MATCH' for r in results),'ABSTAIN':sum(r['result']=='ABSTAIN' for r in results)},'valid_lineage_cumulative':{'unseen':len(cumulative),'complete_features':len(cumulative)-A,'MATCH':M,'NO_MATCH':N,'ABSTAIN':A,'watchtower_recall':M/(len(cumulative)-A) if len(cumulative)!=A else None,'background_denominator':0,'background_match_rate':None,'novel_candidate_recurrences':len(matched&novel),'multiple_candidate_recurrences':sum(sum(x in r['candidate_ids'] for r in cumulative)>=2 for x in matched),'zero_abstentions':A},'additional_evidence_required':True,'candidate_definitions_tuned':False}
 out=Path(a.output_dir);write_new(out/'p3r_corrected_shadow_manifest.v1.json',manifest);write_new(out/'p3r_corrected_shadow_features.v1.jsonl',features,True);write_new(out/'p3r_corrected_shadow_results.v1.jsonl',results,True);write_new(out/'p3r_corrected_shadow_metrics.v1.json',metrics)
 print(canon({'verdict':manifest['principal_verdict'],'signal_status':manifest['signal_status'],'metrics':metrics,'digests':{x.name:sha(x) for x in out.iterdir()}}))
if __name__=='__main__':main()
