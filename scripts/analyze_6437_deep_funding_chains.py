#!/usr/bin/env python3
"""One-off, sequential, bounded upstream SOL parent tracer for the 6437 core."""
import argparse,json,os,re,uuid,urllib.request
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; AUDIT=ROOT/'docs/audits'
EP=AUDIT/'potential_operations_6437_deep_funding_chain_edges.v1.jsonl'; CP=AUDIT/'potential_operations_6437_deep_funding_chains.v1.jsonl'; LP=AUDIT/'potential_operations_6437_deep_funding_run_ledger.v1.json'; AP=AUDIT/'potential_operations_6437_deep_funding_architecture.v1.json'; SP=AUDIT/'potential_operations_6437_funder_creator_edges.v1.jsonl'; MAX=600
def now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
def dump(path,obj):
 p=path.with_suffix(path.suffix+'.pending');p.write_text(json.dumps(obj,indent=2,sort_keys=True)+'\n');os.replace(p,path)
def snap(states):
 p=CP.with_suffix('.jsonl.pending')
 with p.open('w') as h:
  for k in sorted(states):h.write(json.dumps(states[k],sort_keys=True)+'\n')
 os.replace(p,CP)
def append(obj):
 with EP.open('a') as h:h.write(json.dumps(obj,sort_keys=True)+'\n');h.flush();os.fsync(h.fileno())
def core():
 rows=[json.loads(x) for x in SP.read_text().splitlines() if x.strip()];v=sorted({x['funder'] for x in rows if x.get('state')=='PROVEN_ASSOCIATED_CREATOR_10K'})
 if len(v)!=56:raise RuntimeError(f'strict F0 denominator drift: expected 56, got {len(v)}')
 return v
def endpoint():
 for path in (ROOT/'.env',ROOT/'config/supervisor/supervisord.conf'):
  if not path.exists():continue
  m=re.search(r'HELIUS_RPC_URL\s*=\s*["\']?([^"\'\n,]+)',path.read_text(),re.M)
  if m:return m.group(1)
 raise RuntimeError('HELIUS_RPC_URL absent')
def inbound(tx,child):
 if not tx:return []
 ins=list(tx.get('transaction',{}).get('message',{}).get('instructions',[]))
 for group in tx.get('meta',{}).get('innerInstructions',[]) or []:ins+=group.get('instructions',[])
 out=[]
 for i in ins:
  parsed=i.get('parsed') or {};info=parsed.get('info') or {}
  if parsed.get('type')=='transfer' and info.get('destination')==child and info.get('source') and isinstance(info.get('lamports'),int) and info['lamports']>10000:out.append((info['lamports'],info['source']))
 return out
def main():
 a=argparse.ArgumentParser();a.add_argument('--resume',action='store_true');args=a.parse_args();f0s=core()
 if args.resume:
  ledger=json.loads(LP.read_text());states={x['f0']:x for x in (json.loads(y) for y in CP.read_text().splitlines() if y.strip())}
  if ledger.get('purpose')!='6437_DEEP_FUNDING_CHAIN_TRAVERSAL' or ledger.get('authorized_calls')!=MAX or set(states)!=set(f0s):raise RuntimeError('not a resumable fresh run')
 else:
  run='6437-deep-funding-'+uuid.uuid4().hex[:16];ledger={'run_id':run,'purpose':'6437_DEEP_FUNDING_CHAIN_TRAVERSAL','authorized_calls':MAX,'calls_attempted':0,'calls_remaining':MAX,'started_at':now(),'status':'RUNNING'};EP.write_text('');states={f:{'run_id':run,'f0':f,'status':'ACTIVE','depth_reached':0,'edges':[],'stop_reason':None} for f in f0s};dump(LP,ledger);snap(states)
 url=endpoint()
 def rpc(method,params):
  if ledger['calls_attempted']>=MAX:raise RuntimeError('BUDGET_EXHAUSTED')
  ledger['calls_attempted']+=1;ledger['calls_remaining']-=1;ledger['last_dispatch']={'at':now(),'method':method};dump(LP,ledger)
  q=urllib.request.Request(url,data=json.dumps({'jsonrpc':'2.0','id':ledger['calls_attempted'],'method':method,'params':params}).encode(),headers={'Content-Type':'application/json'})
  try:
   with urllib.request.urlopen(q,timeout=30) as z:r=json.loads(z.read())
   if r.get('error'):raise RuntimeError('RPC_ERROR:'+str(r['error'].get('code')))
   return r.get('result')
  finally:
   if ledger['calls_attempted']%25==0:ledger['checkpoint_at']=now();dump(LP,ledger)
 for depth in range(1,10):
  active=[f for f in f0s if states[f]['status']=='ACTIVE']
  for f in active:
   s=states[f];child=f if not s['edges'] else s['edges'][-1]['parent']
   try:
    sigs=rpc('getSignaturesForAddress',[child,{'limit':8}]) or []
    if len(sigs)>=8 and depth>1:s.update(status='STOPPED',stop_reason='HIGH_ACTIVITY_SERVICE',depth_reached=depth-1);snap(states);continue
    found=[]
    for item in sigs[:4]:
     tx=rpc('getTransaction',[item['signature'],{'encoding':'jsonParsed','maxSupportedTransactionVersion':0}])
     found += [{'parent':parent,'amount_lamports':amt,'block_time':tx.get('blockTime') if tx else None,'signature':item['signature']} for amt,parent in inbound(tx,child)]
   except RuntimeError as e:
    if str(e)=='BUDGET_EXHAUSTED':s.update(status='STOPPED',stop_reason='BUDGET_EXHAUSTED',depth_reached=depth-1);snap(states);break
    s.update(status='STOPPED',stop_reason='HISTORY_BOUNDARY',detail=str(e),depth_reached=depth-1);snap(states);continue
   parents={x['parent'] for x in found}
   if not found:s.update(status='STOPPED',stop_reason='NO_PARENT_FOUND',depth_reached=depth-1)
   elif len(parents)!=1:s.update(status='STOPPED',stop_reason='AMBIGUOUS_PARENT',candidate_parent_count=len(parents),depth_reached=depth-1)
   else:
    e=max(found,key=lambda x:x['amount_lamports']);e.update(run_id=ledger['run_id'],f0=f,depth=depth,child=child,asset='SOL',forwarding_relationship='inbound SOL transfer to child',selection_reason='sole observed inbound parent in bounded recent window; largest transfer retained',confidence='HIGH' if len(found)==1 else 'MEDIUM');append(e);s['edges'].append(e);s['depth_reached']=depth
    if depth==9:s.update(status='STOPPED',stop_reason='DEPTH_LIMIT')
   snap(states)
   if ledger['calls_remaining']==0:break
  if ledger['calls_remaining']==0:break
 for s in states.values():
  if s['status']=='ACTIVE':s.update(status='STOPPED',stop_reason='BUDGET_EXHAUSTED' if ledger['calls_remaining']==0 else 'HISTORY_BOUNDARY')
 snap(states);depths=Counter(s['depth_reached'] for s in states.values());parents=Counter(e['parent'] for s in states.values() for e in s['edges'])
 out={'run_id':ledger['run_id'],'purpose':ledger['purpose'],'authorized_calls':MAX,'calls_used':ledger['calls_attempted'],'calls_remaining':ledger['calls_remaining'],'core_funder_count':len(f0s),'funders_with_chain_evidence':sum(bool(s['edges']) for s in states.values()),'max_depth_distribution':dict(sorted(depths.items())),'resolved_parent_edges':sum(len(s['edges']) for s in states.values()),'shared_deeper_parents':{p:n for p,n in parents.items() if n>1},'stop_reasons':dict(Counter(s['stop_reason'] for s in states.values())),'completed_at':now(),'safety':'read-only RPC and audit artifacts only; no database/source/assignment/membership/ranking/Living/detector writes'}
 ledger.update(status='COMPLETE',completed_at=now());dump(LP,ledger);dump(AP,out);print(json.dumps(out,sort_keys=True))
if __name__=='__main__':main()
