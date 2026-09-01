#!/usr/bin/env python3
"""Resumable read-only, label-blind funding-architecture census for unassigned launches."""
from __future__ import annotations
import concurrent.futures, hashlib, json, os, sqlite3, time, urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from audit_watchtower_funding_architecture import _decode, _rpc_url
from audit_confirmed_operation_funding_architecture import family_key

DB='database/wt_ops_v2.db'; ROOT=Path('docs/audits')
FREEZE=ROOT/'funding_architecture_unassigned_freeze.v1.json'; FACTS=ROOT/'funding_architecture_unassigned_facts.v1.jsonl'; CENSUS=ROOT/'funding_architecture_unassigned_census.v1.json'; DISC=ROOT/'funding_architecture_potential_discovery.v1.json'

def fetch(url,sig):
 p={'jsonrpc':'2.0','id':1,'method':'getTransaction','params':[sig,{'encoding':'jsonParsed','maxSupportedTransactionVersion':0}]}
 q=urllib.request.Request(url,data=json.dumps(p).encode(),headers={'Content-Type':'application/json'})
 return json.loads(urllib.request.urlopen(q,timeout=45).read()).get('result')
def frozen():
 if FREEZE.exists(): return json.load(open(FREEZE))['rows']
 c=sqlite3.connect(DB);c.row_factory=sqlite3.Row
 rows=[]; seen=set()
 for e in c.execute("""SELECT e.mint,e.signature,e.block_time launch_time,e.mechanism,e.wallet direct_funder,e.candidate_parent subprov_wallet,e.close_destination creator,q.completed_at
 FROM wt_walkback_edge_candidates e JOIN wt_walkback_queue q ON q.mint=e.mint
 LEFT JOIN operator_launch_membership m ON m.mint=e.mint
 WHERE e.selection_status='SELECTED' AND e.hop_depth=1 AND q.status='complete' AND m.mint IS NULL
 ORDER BY e.mint,e.block_time DESC,e.evidence_key DESC"""):
  r=dict(e)
  if not r['signature']: continue
  r['signature_source']='RETAINED_SELECTED_HOP1';r['treasury_wallet']=None
  if r['mint'] not in seen: rows.append(r);seen.add(r['mint'])
 c.close(); FREEZE.write_text(json.dumps({'schema':'UNASSIGNED_FUNDING_FREEZE_V1','frozen_at':int(time.time()),'rows':rows},sort_keys=True,indent=2)+'\n');return rows
def known_keys():
 p=json.load(open('docs/audits/confirmed_operation_funding_architecture_matrix.v1.json'))
 keys={}
 for row in p['operation_family_matrix']:
  k=json.dumps(row['architecture'],sort_keys=True,separators=(',',':'))
  for op in row['counts']: keys[k]=op
 return keys
def main():
 rows=frozen(); prior=[json.loads(x) for x in FACTS.open()] if FACTS.exists() else []
 # A mint is terminal if an OK decode exists, or after a bounded terminal
 # provider classification.  Old duplicate/transient checkpoint rows remain
 # preserved, but never inflate the population denominator.
 best={}
 for x in prior:
  old=best.get(x.get('mint')); rank=lambda y: 2 if y.get('status')=='OK' else (1 if y.get('reason')!='URLError' else 0)
  if old is None or rank(x)>=rank(old): best[x.get('mint')]=x
 todo=[r for r in rows if r['mint'] not in best or (best[r['mint']].get('status')!='OK' and best[r['mint']].get('reason')=='URLError')]
 batch=todo[:int(os.environ.get('FUNDING_DISCOVERY_BATCH','75'))]; url=_rpc_url(); calls=0
 def one(r):
  try:return r,fetch(url,r['signature']),None
  except Exception as e:return r,None,type(e).__name__
 with FACTS.open('a') as out, concurrent.futures.ThreadPoolExecutor(max_workers=128) as ex:
  for i,(r,tx,err) in enumerate(ex.map(one,batch),1):
   calls+=1
   if tx: record=_decode(r,tx,'RPC');record['direct_funder']=r.get('direct_funder');record['completed_at']=r.get('completed_at');record['status']='OK'
   else: record={'mint':r['mint'],'funding_signature':r['signature'],'status':'UNRESOLVED','reason':err or 'RPC_RETURNED_NULL','launch_timestamp':r.get('launch_time'),'retained_mechanism':r.get('mechanism'),'direct_funder':r.get('direct_funder')}
   out.write(json.dumps(record,sort_keys=True)+'\n')
   if i%100==0: out.flush()
 facts=[json.loads(x) for x in FACTS.open()]
 terminal={}
 for x in facts:
  old=terminal.get(x.get('mint')); rank=lambda y: 2 if y.get('status')=='OK' else (1 if y.get('reason')!='URLError' else 0)
  if old is None or rank(x)>=rank(old): terminal[x.get('mint')]=x
 if len(terminal)<len(rows):
  okc=sum(x.get('status')=='OK' for x in terminal.values());un=len(terminal)-okc
  print(f'FUNDING_FACTS_PROGRESS terminal={len(terminal)}/{len(rows)} ok={okc} unresolved={un} remaining={len(rows)-len(terminal)} batch={len(batch)} provider_calls_this_batch={calls}');return
 facts=list(terminal.values())
 source_by_mint={r['mint']:r for r in rows}
 meta_conn=sqlite3.connect(DB)
 queue_meta={m:(c,f) for m,c,f in meta_conn.execute("SELECT mint,creator,funder_wallet FROM wt_walkback_queue")}
 meta_conn.close()
 for fact in facts:
  source=source_by_mint.get(fact.get('mint'),{})
  meta=queue_meta.get(fact.get('mint'),(None,None))
  fact['creator']=source.get('creator') or meta[0]
  fact['direct_funder']=fact.get('direct_funder') or source.get('direct_funder') or meta[1]
 ok=[x for x in facts if x['status']=='OK']; bad=[x for x in facts if x['status']!='OK']; clusters=defaultdict(list)
 for x in ok: clusters[family_key(x)].append(x)
 now=int(time.time()); known=known_keys(); candidate={}
 c=sqlite3.connect(DB)
 for run,cid,mint in c.execute('SELECT run_id,candidate_id,mint FROM p3r_v2_candidate_membership'): candidate[mint]=cid
 c.close(); entries=[]
 for k,xs in clusters.items():
  times=[x.get('launch_timestamp') or 0 for x in xs]; creators={x.get('creator') for x in xs if x.get('creator')};funders={x.get('direct_funder') for x in xs if x.get('direct_funder')}; cids={candidate[x['mint']] for x in xs if x['mint'] in candidate}
  d=hashlib.sha256(k.encode()).hexdigest()[:16]; n=len(xs); recent=lambda s:sum(t>=now-s for t in times)
  entries.append({'family_id':'FAU_'+d,'architecture_digest':d,'architecture':json.loads(k),'launches':n,'creators':len(creators),'funders':len(funders),'first_seen':min(times),'latest':max(times),'activity':[recent(86400),recent(604800),recent(2592000)],'candidate_ids':sorted(cids),'known_operation':known.get(k),'address_blind_persistence':'HIGH' if len(creators)>=3 and len(funders)>=3 else ('MODERATE' if len(creators)>=3 else 'LOW'),'temporal_persistence':'HIGH' if max(times)-min(times)>=604800 else ('MODERATE' if max(times)-min(times)>=86400 else 'LOW')})
 entries.sort(key=lambda x:(-x['launches'],-x['creators'],x['family_id']))
 dist=Counter('1' if x['launches']==1 else '2' if x['launches']==2 else '3-4' if x['launches']<5 else '5-9' if x['launches']<10 else '10-24' if x['launches']<25 else '25-49' if x['launches']<50 else '50+' for x in entries)
 recurrent=[x for x in entries if x['launches']>=3 and x['creators']>=3]; high=[x for x in recurrent if x['funders']>=3]
 census={'schema':'FUNDING_ARCHITECTURE_UNASSIGNED_CENSUS_V1','freeze':str(FREEZE),'population_cutoff':json.load(open(FREEZE))['frozen_at'],'denominators':{'WALKBACK_EVALUABLE_UNASSIGNED':len(rows),'FUNDING_PATH_AVAILABLE':len(rows),'FUNDING_SIGNATURE_AVAILABLE':len(rows),'LOCAL_DECODED':0,'RPC_DECODED':len(ok),'FUNDING_ARCHITECTURE_EVALUABLE':len(ok),'UNRESOLVED':len(bad)},'normalized_facts':ok,'unresolved':bad,'provider_calls':calls}; census['digest']=hashlib.sha256(json.dumps(census,sort_keys=True,separators=(',',':')).encode()).hexdigest();CENSUS.write_text(json.dumps(census,sort_keys=True,indent=2)+'\n')
 discovery={'schema':'FUNDING_ARCHITECTURE_POTENTIAL_DISCOVERY_V1','census_digest':census['digest'],'clusters':entries,'distribution':dict(dist),'recurrent':len(recurrent),'higher_confidence':len(high),'known_exact':sum(x['launches'] for x in entries if x['known_operation']),'new_funding_only':[x for x in high if not x['known_operation'] and not x['candidate_ids']],'provider_calls':calls}; discovery['digest']=hashlib.sha256(json.dumps(discovery,sort_keys=True,separators=(',',':')).encode()).hexdigest();DISC.write_text(json.dumps(discovery,sort_keys=True,indent=2)+'\n');print(json.dumps({'rows':len(rows),'ok':len(ok),'bad':len(bad),'clusters':len(entries),'recurrent':len(recurrent),'high':len(high),'calls':calls,'digest':discovery['digest']}))
if __name__=='__main__': main()
