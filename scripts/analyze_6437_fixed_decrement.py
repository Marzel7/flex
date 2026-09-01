#!/usr/bin/env python3
"""One-off continuation of 6437-deep-funding-7534ffe978b343df; no new budget."""
import json,os,re,urllib.request
from collections import defaultdict,Counter
from datetime import datetime,timezone
from pathlib import Path
R=Path(__file__).resolve().parents[1];A=R/'docs/audits';LP=A/'potential_operations_6437_deep_funding_run_ledger.v1.json';CP=A/'potential_operations_6437_deep_funding_chains.v1.jsonl';EP=A/'potential_operations_6437_deep_funding_chain_edges.v1.jsonl';FP=A/'potential_operations_6437_funder_infrastructure_evidence.v2.jsonl';DP=A/'potential_operations_6437_fixed_decrement_edges.v1.jsonl';AP=A/'potential_operations_6437_fixed_decrement_funding_analysis.v1.json';RUN='6437-deep-funding-7534ffe978b343df';MAX=600
def now():return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
def dump(p,x):
 q=p.with_suffix(p.suffix+'.pending');q.write_text(json.dumps(x,indent=2,sort_keys=True)+'\n');os.replace(q,p)
def snap(S):
 q=CP.with_suffix('.jsonl.pending');q.write_text(''.join(json.dumps(S[k],sort_keys=True)+'\n' for k in sorted(S)));os.replace(q,CP)
def append(p,x):
 with p.open('a') as h:h.write(json.dumps(x,sort_keys=True)+'\n');h.flush();os.fsync(h.fileno())
def endpoint():
 for p in (R/'.env',R/'config/supervisor/supervisord.conf'):
  if p.exists():
   m=re.search(r'HELIUS_RPC_URL\s*=\s*["\']?([^"\'\n,]+)',p.read_text())
   if m:return m.group(1)
 raise RuntimeError('RPC endpoint absent')
def inbound(tx,child):
 if not tx:return []
 ins=list(tx.get('transaction',{}).get('message',{}).get('instructions',[]))
 for g in tx.get('meta',{}).get('innerInstructions',[]) or []:ins+=g.get('instructions',[])
 out=[]
 for i in ins:
  z=i.get('parsed') or {};v=z.get('info') or {}
  if z.get('type')=='transfer' and v.get('destination')==child and v.get('source') and isinstance(v.get('lamports'),int) and v['lamports']>10000:out.append((v['lamports'],v['source']))
 return out
def main():
 L=json.loads(LP.read_text());assert L['run_id']==RUN and L['calls_attempted']==316 and L['calls_remaining']==284 and L['authorized_calls']==MAX
 L['status']='RUNNING_FIXED_DECREMENT_CONTINUATION';dump(LP,L)
 S={x['f0']:x for x in (json.loads(z) for z in CP.read_text().splitlines() if z.strip())};assert len(S)==56
 existing={(x['f0'],x['depth'],x['child'],x['parent']) for x in (json.loads(z) for z in EP.read_text().splitlines() if z.strip())}
 # Add only locally retained, single-parent F1 evidence to previously unresolved F0s.
 retained=defaultdict(list)
 for z in (json.loads(x) for x in FP.read_text().splitlines() if x.strip()):
  if z.get('asset')=='SOL' and z['funder'] in S and S[z['funder']]['depth_reached']==0:retained[z['funder']].append(z)
 local=[]
 for f,rows in retained.items():
  parents={x['upstream'] for x in rows}
  if len(parents)!=1:continue
  x=max(rows,key=lambda y:y['amount']);e={'run_id':RUN,'f0':f,'depth':1,'child':f,'parent':x['upstream'],'amount_lamports':round(x['amount']*1e9),'block_time':x['provisioning_time'],'signature':x['transaction_signature'],'asset':'SOL','forwarding_relationship':'retained inbound SOL transfer to child','selection_reason':'single upstream address in retained F1 evidence','confidence':'MEDIUM','evidence_source':'retained_f1'}
  if (f,1,f,e['parent']) not in existing:append(EP,e);existing.add((f,1,f,e['parent']))
  S[f]['edges'].append(e);S[f]['depth_reached']=1;S[f]['status']='ACTIVE';S[f]['stop_reason']=None;local.append(f);snap(S)
 # New breadth: first newly-local F1 parents, then unresolved F0 later signature slice (4..7), max four tx each.
 targets=[(f,S[f]['edges'][-1]['parent'],1,0) for f in local]+[(f,f,0,4) for f in sorted(S) if S[f]['depth_reached']==0]
 url=endpoint()
 def rpc(m,p):
  if L['calls_remaining']<=0:raise RuntimeError('BUDGET_EXHAUSTED')
  L['calls_attempted']+=1;L['calls_remaining']-=1;L['last_dispatch']={'at':now(),'method':m};dump(LP,L)
  q=urllib.request.Request(url,data=json.dumps({'jsonrpc':'2.0','id':L['calls_attempted'],'method':m,'params':p}).encode(),headers={'Content-Type':'application/json'})
  try:
   with urllib.request.urlopen(q,timeout=30) as h:r=json.loads(h.read())
   if r.get('error'):raise RuntimeError('RPC_ERROR')
   return r.get('result')
  finally:
   if L['calls_attempted'] in (416,516) or L['calls_attempted']%25==0:dump(LP,L);snap(S)
 for f,child,base,start in targets:
  if L['calls_remaining']<5:break
  try:
   sigs=rpc('getSignaturesForAddress',[child,{'limit':20}]) or [];found=[]
   for item in sigs[start:start+4]:
    tx=rpc('getTransaction',[item['signature'],{'encoding':'jsonParsed','maxSupportedTransactionVersion':0}])
    found += [{'parent':p,'amount_lamports':a,'block_time':tx.get('blockTime') if tx else None,'signature':item['signature'],'transaction_fee':tx.get('meta',{}).get('fee') if tx else None} for a,p in inbound(tx,child)]
  except RuntimeError:break
  P={x['parent'] for x in found}
  if len(P)==1:
   x=max(found,key=lambda y:y['amount_lamports']);depth=base+1 if base else 1;e={'run_id':RUN,'f0':f,'depth':depth,'child':child,**x,'asset':'SOL','forwarding_relationship':'inbound SOL transfer to child','selection_reason':'sole independently observed inbound parent in bounded signature slice','confidence':'HIGH' if len(found)==1 else 'MEDIUM','evidence_source':'fixed_decrement_continuation'}
   if (f,depth,child,e['parent']) not in existing:append(EP,e);existing.add((f,depth,child,e['parent']));S[f]['edges'].append(e);S[f]['depth_reached']=max(S[f]['depth_reached'],depth);S[f]['status']='ACTIVE';S[f]['stop_reason']=None
  elif len(P)>1:S[f].update(status='STOPPED',stop_reason='AMBIGUOUS_PARENT')
  snap(S)
 # Deepen only chains already positive (two same consecutive decrements), subject to remaining budget.
 def decs(s):
  q=sorted(s['edges'],key=lambda x:x['depth']);return [q[i]['amount_lamports']-q[i-1]['amount_lamports'] for i in range(1,len(q))]
 positive=[f for f in S if len(decs(S[f]))>=2 and len(set(decs(S[f])))==1 and decs(S[f])[0] in (10100,20100)]
 for f in positive:
  while S[f]['depth_reached']<7 and L['calls_remaining']>=5:
   child=S[f]['edges'][-1]['parent'];d=S[f]['depth_reached']+1
   try:
    sigs=rpc('getSignaturesForAddress',[child,{'limit':8}]) or [];found=[]
    for item in sigs[:4]:
     tx=rpc('getTransaction',[item['signature'],{'encoding':'jsonParsed','maxSupportedTransactionVersion':0}]);found += [{'parent':p,'amount_lamports':a,'block_time':tx.get('blockTime') if tx else None,'signature':item['signature'],'transaction_fee':tx.get('meta',{}).get('fee') if tx else None} for a,p in inbound(tx,child)]
   except RuntimeError:break
   P={x['parent'] for x in found}
   if len(P)!=1:S[f].update(status='STOPPED',stop_reason='NO_PARENT_FOUND' if not P else 'AMBIGUOUS_PARENT');snap(S);break
   x=max(found,key=lambda y:y['amount_lamports']);e={'run_id':RUN,'f0':f,'depth':d,'child':child,**x,'asset':'SOL','forwarding_relationship':'inbound SOL transfer to child','selection_reason':'positive fixed-decrement chain deepening; sole observed parent','confidence':'HIGH' if len(found)==1 else 'MEDIUM','evidence_source':'fixed_decrement_continuation'};append(EP,e);S[f]['edges'].append(e);S[f]['depth_reached']=d;snap(S)
 # Compact exact decrement evidence; pre-existing fees are intentionally unknown because raw metas were not retained.
 DP.write_text('');events=[]
 for f,s in S.items():
  q=sorted(s['edges'],key=lambda x:x['depth'])
  for i in range(1,len(q)):
   parent,child=q[i],q[i-1];v=parent['amount_lamports']-child['amount_lamports'];dt=(child.get('block_time') or 0)-(parent.get('block_time') or 0);rec={'core_funder':f,'upstream_depth':parent['depth'],'parent_amount':parent['amount_lamports'],'child_forward_amount':child['amount_lamports'],'decrement':v,'transaction_fee':parent.get('transaction_fee'),'time_delta_seconds':dt if dt>=0 else None,'sparsity_class':'UNKNOWN','linearity':'STRICT_LINEAR','pattern_class':'INSUFFICIENT_DEPTH'};events.append(rec)
 for f in S:
  ds=decs(S[f]);pc='REPEATED_20100' if len(ds)>=2 and all(x==20100 for x in ds) else 'REPEATED_10100' if len(ds)>=2 and all(x==10100 for x in ds) else 'MIXED_FIXED_DECREMENT' if len(ds)>=2 and len(set(ds))==1 else 'OTHER_FIXED_DECREMENT' if ds else 'INSUFFICIENT_DEPTH'
  for e in events:
   if e['core_funder']==f:e['pattern_class']=pc
 for e in events:append(DP,e)
 census={}
 for d in sorted({e['decrement'] for e in events}):
  z=[e for e in events if e['decrement']==d];census[str(d)]={'event_count':len(z),'distinct_chain_count':len({e['core_funder'] for e in z}),'distinct_core_funder_count':len({e['core_funder'] for e in z}),'depths_observed':sorted({e['upstream_depth'] for e in z})}
 enough=sum(len(s['edges'])>=2 for s in S.values());f201={e['core_funder'] for e in events if e['decrement']==20100};f101={e['core_funder'] for e in events if e['decrement']==10100};fixed={e['core_funder'] for e in events if e['decrement'] in (10100,20100)}
 result={'run_id':RUN,'calls_before':316,'calls_after':L['calls_attempted'],'additional_calls':L['calls_attempted']-316,'calls_remaining':L['calls_remaining'],'chain_coverage_before':12,'chain_coverage_after':sum(bool(s['edges']) for s in S.values()),'funders_with_2plus_edges':enough,'exact_decrement_census':census,'decrement_20100_funders':sorted(f201),'decrement_10100_funders':sorted(f101),'fixed_decrement_funders':sorted(fixed),'fee_explanation':'UNKNOWN for retained edges: decoded transaction meta was not locally retained and was not refetched. Newly acquired parent edges retain fee where available. No inference that decrement equals fees.','timing_seconds':[e['time_delta_seconds'] for e in events if e['time_delta_seconds'] is not None],'control_result':'INSUFFICIENT_LOCAL_CONTROL','interpretation':'INSUFFICIENT_CHAIN_COVERAGE','safety':'read-only RPC and audit artifacts only; no production/database/source/assignment/membership/ranking/Living/promotion/detector writes','completed_at':now()}
 L.update(status='COMPLETE_FIXED_DECREMENT_CONTINUATION',completed_at=now());dump(LP,L);snap(S);dump(AP,result);print(json.dumps(result,sort_keys=True))
if __name__=='__main__':main()
