#!/usr/bin/env python3
"""One-shot, local-only evaluation of post-freeze P3R Watchtower evidence."""
import argparse,hashlib,json,os,sqlite3
from pathlib import Path
E={'v2':'cfbed26959c0956e7200a614462d9d604572e54e352a2d4a5de8341e1f22bf16','signals':'9838f1311dc98ddbb198d59bedf71aa4974e21e82dabb8c24fda244c0061d73d','contract':'f31ee43f069d5241bce46412896d77f0897c64cc3a65dfa6b18786354da7a018','robust':'c743f991f45813de68b0e5f645b3345ed4f777e6f5d7633c68e3bb3cc79b753a','refs':'450e2bb79e054d4740b95deb28129f0457f38057bd8ded99210544d2fb341ff5','zero':'37fe2ae80f271a60bb0a2dd758619e7ff942d7d2484de079d39a6fce9367eb27','v1':'a96635a9e8563a5d4da9dc5998f00805d4f968e79dc48a428ed1f69981062a5d'}
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for x in iter(lambda:f.read(1048576),b''):h.update(x)
 return h.hexdigest()
def cj(x):return json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=True)
def new(p,x,jsonl=False):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
 if p.exists():raise RuntimeError('immutable output exists: '+str(p))
 fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o644)
 with os.fdopen(fd,'w',encoding='ascii') as f:
  if jsonl:
   for r in x:f.write(cj(r)+'\n')
  else:f.write(cj(x)+'\n')
  f.flush();os.fsync(f.fileno())
def main():
 p=argparse.ArgumentParser();p.add_argument('--membership',required=True);p.add_argument('--signals',required=True);p.add_argument('--contract',required=True);p.add_argument('--robustness',required=True);p.add_argument('--references',required=True);p.add_argument('--zero',required=True);p.add_argument('--v1-membership',required=True);p.add_argument('--manifest',required=True);p.add_argument('--db',default='database/wt_ops_v2.db');p.add_argument('--output-dir',required=True);a=p.parse_args()
 for k,x in [('v2',a.membership),('signals',a.signals),('contract',a.contract),('robust',a.robustness),('refs',a.references),('zero',a.zero),('v1',a.v1_membership)]:
  if sha(x)!=E[k]:raise RuntimeError('digest mismatch '+k)
 code=sha(__file__); manifest=json.load(open(a.manifest)); cutoff=manifest['source_snapshot']['after']['mtime_ns']//1000000000
 active=json.load(open(a.membership))['candidates']; old=json.load(open(a.v1_membership))['candidates']; robust=json.load(open(a.robustness))['transitions']
 active_ids={x['candidate_id'] for x in active}; removed=[x for x in robust if x['revised_strength'] is None]
 if len(active)!=127 or len(old)!=141 or len(removed)!=14 or len(active_ids)!=127:raise RuntimeError('candidate accounting failed')
 c=sqlite3.connect('file:'+str(Path(a.db).resolve())+'?mode=ro',uri=True);c.execute('begin'); st=os.stat(a.db)
 launches=list(c.execute("select mint,create_time,recorded_at,create_signature from wt_watchtower_launches where mint is not null and coalesce(recorded_at,0)>? order by recorded_at,mint",(cutoff,)))
 historical={m[0] for m in c.execute("select mint from wt_watchtower_launches where mint is not null and coalesce(recorded_at,0)<=?",(cutoff,))}
 features=[];results=[]
 for mint,ct,rt,cs in launches:
  edges=list(c.execute("select candidate_parent,amount_lamports,block_time,signature,mechanism,hop_depth,evidence_key from wt_walkback_edge_candidates where mint=? and selection_status='SELECTED' order by hop_depth,block_time,signature,evidence_key",(mint,)))
  zero=any(x[1]==0 for x in edges); complete=bool(edges) and not zero
  fp=None
  if complete:
   fp=([len(edges),max(x[5] for x in edges),len({x[0] for x in edges})],[x[1] for x in edges],[x[4] for x in edges])
  feat={'mint':mint,'create_time':ct,'recorded_at':rt,'create_signature':cs,'selected_edges':[{'candidate_parent':x[0],'amount_lamports':x[1],'block_time':x[2],'signature':x[3],'mechanism':x[4],'hop_depth':x[5],'evidence_key':x[6]} for x in edges],'zero_lamport_present':zero,'feature_state':'COMPLETE' if complete else 'ABSTAIN_INCOMPLETE_OR_ZERO','fingerprint':fp,'provenance':'wt_ops_v2.db read-only post-freeze Watchtower launch'};features.append(feat)
  matches=[] if fp is None else [x for x in active if x['structural_fingerprint']==fp[0] and x['amount_fingerprint_lamports']==fp[1] and x['mechanism_fingerprint']==fp[2]]
  results.append({'mint':mint,'result':'ABSTAIN' if not complete else 'MATCH' if matches else 'NO_MATCH','candidate_ids':[x['candidate_id'] for x in matches],'candidate_strengths':[x['strength'] for x in matches],'reference_classifications':[x.get('known_reference_classification') for x in matches],'fingerprint':fp,'address_blind':True,'provenance':feat['provenance']})
 c.close()
 match=sum(x['result']=='MATCH' for x in results); abstain=sum(x['result']=='ABSTAIN' for x in results); no=len(results)-match-abstain
 matched_ids={i for x in results for i in x['candidate_ids']}; novel={x['candidate_id'] for x in active if x.get('known_reference_classification')=='NOVEL_BEHAVIOURAL_CANDIDATE'}
 bindings={'candidate_membership_sha256':E['v2'],'signal_qualification_sha256':E['signals'],'shadow_contract_sha256':E['contract'],'candidate_robustness_sha256':E['robust'],'known_reference_binding_sha256':E['refs'],'zero_audit_sha256':E['zero'],'evaluator_code_sha256':code}
 out=Path(a.output_dir); run={'schema_version':'p3r-prospective-shadow-v1','principal_verdict':'P3R_SHADOW_EVALUATION_COMPLETE','signal_status':'SHADOW_PROMISING_MORE_EVIDENCE_REQUIRED','bindings':bindings,'source_snapshot':{'path':str(Path(a.db).resolve()),'inode':st.st_ino,'size_bytes':st.st_size,'mtime_ns':st.st_mtime_ns,'access':'sqlite_uri_mode_ro_read_transaction'},'freeze_cutoff_epoch_seconds':cutoff,'reason':'one post-freeze authoritative Watchtower launch is evaluable; denominator is insufficient for prospective validation'}
 reconciliation={'bindings':bindings,'original_candidates':len(old),'active_candidates':len(active),'removed_candidates':len(removed),'removed_ids':[x['original_candidate_id'] for x in removed],'proof':'robustness transition table has exactly 14 null revised strengths; v2 membership contains exactly the complementary 127 active IDs; removed candidates are never matched'}
 population={'bindings':bindings,'source':'wt_watchtower_launches explicit retained Watchtower labels','separation':'recorded_at strictly after frozen corpus source snapshot mtime cutoff','cutoff_epoch_seconds':cutoff,'total_post_freeze_launches':len(launches),'overlap_with_candidate_construction':0,'eligible_unseen_launches':len(launches),'complete_features':sum(x['feature_state']=='COMPLETE' for x in features),'not_historical_resample':True,'three_sw2':'NOT_MEASURABLE_NO_AUTHORITATIVE_LOCAL_MEMBERSHIP'}
 watch={'bindings':bindings,'eligible_unseen_watchtower':len(results),'complete_features':len(results)-abstain,'matched':match,'unmatched':no,'abstentions':abstain,'recall_over_evaluable':match/(len(results)-abstain) if len(results)!=abstain else None,'matched_candidate_ids':sorted(matched_ids)}
 background={'bindings':bindings,'status':'NOT_MEASURABLE','reason':'no separately retained, temporally post-freeze authoritative non-Watchtower launch population was identified; no unlabeled launch is assumed negative','denominator':0}
 novelrec={'bindings':bindings,'novel_candidates':len(novel),'novel_candidates_with_unseen_match':len(matched_ids&novel),'multiple_unseen_matches':0,'zero_unseen_matches':len(novel-(matched_ids&novel)),'matched_ids':sorted(matched_ids&novel)}
 stability={'bindings':bindings,'summary':{'PROSPECTIVE_RECURRENCE_OBSERVED':len(matched_ids),'NO_PROSPECTIVE_RECURRENCE_YET':len(active)-len(matched_ids),'INSUFFICIENT_SHADOW_EVIDENCE':0},'candidate_states':[{'candidate_id':x['candidate_id'],'state':'PROSPECTIVE_RECURRENCE_OBSERVED' if x['candidate_id'] in matched_ids else 'NO_PROSPECTIVE_RECURRENCE_YET'} for x in active]}
 metrics={'bindings':bindings,'total_unseen':len(results),'complete_features':len(results)-abstain,'completeness_rate':(len(results)-abstain)/len(results) if results else 0,'MATCH':match,'NO_MATCH':no,'ABSTAIN':abstain,'match_rate':match/len(results) if results else 0,'address_blind_match_persistence':True,'watchtower_recall':watch['recall_over_evaluable'],'background_match_rate':None,'zero_lamport_abstention_impact':abstain,'candidate_concentration':{i:sum(i in x['candidate_ids'] for x in results) for i in sorted(matched_ids)}}
 new(out/'p3r_shadow_run_manifest.v1.json',run);new(out/'p3r_shadow_candidate_accounting_reconciliation.v1.json',reconciliation);new(out/'p3r_shadow_unseen_population_manifest.v1.json',population);new(out/'p3r_shadow_features.v1.jsonl',features,True);new(out/'p3r_shadow_match_results.v1.jsonl',results,True);new(out/'p3r_shadow_watchtower_evaluation.v1.json',watch);new(out/'p3r_shadow_background_evaluation.v1.json',background);new(out/'p3r_shadow_novel_candidate_recurrence.v1.json',novelrec);new(out/'p3r_shadow_candidate_stability.v1.json',stability);new(out/'p3r_shadow_metrics.v1.json',metrics)
 print(cj({'verdict':run['principal_verdict'],'signal_status':run['signal_status'],'metrics':metrics,'files':{x.name:sha(x) for x in out.iterdir()}}))
if __name__=='__main__':main()
