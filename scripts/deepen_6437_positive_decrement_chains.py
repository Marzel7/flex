#!/usr/bin/env python3
"""Spend the existing 6437 run balance only on repeated-20,100 chains."""
import json,os,re,urllib.request
from datetime import datetime,timezone
from pathlib import Path
R=Path(__file__).resolve().parents[1];A=R/'docs/audits';LP=A/'potential_operations_6437_deep_funding_run_ledger.v1.json';CP=A/'potential_operations_6437_deep_funding_chains.v1.jsonl';EP=A/'potential_operations_6437_deep_funding_chain_edges.v1.jsonl';RUN='6437-deep-funding-7534ffe978b343df';POS={'Da76omT5pL44rb5XuPSfu16QuAQtWiYAQqpvDoyofX26','Eyb9j2G42H65XD4o6z8jf8rgcUqqG8kpbcK4y7gysYc5'}
def now():return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
def dump(p,x):q=p.with_suffix(p.suffix+'.pending');q.write_text(json.dumps(x,indent=2,sort_keys=True)+'\n');os.replace(q,p)
def snap(S):q=CP.with_suffix('.jsonl.pending');q.write_text(''.join(json.dumps(S[k],sort_keys=True)+'\n' for k in sorted(S)));os.replace(q,CP)
def inbound(t,c):
 ins=list(t.get('transaction',{}).get('message',{}).get('instructions',[])) if t else []
 for g in (t or {}).get('meta',{}).get('innerInstructions',[]) or []:ins+=g.get('instructions',[])
 return [(i['parsed']['info']['lamports'],i['parsed']['info']['source']) for i in ins if (i.get('parsed') or {}).get('type')=='transfer' and (i.get('parsed') or {}).get('info',{}).get('destination')==c and isinstance((i.get('parsed') or {}).get('info',{}).get('lamports'),int) and (i.get('parsed') or {}).get('info',{}).get('lamports')>10000 and (i.get('parsed') or {}).get('info',{}).get('source')]
def main():
 L=json.loads(LP.read_text());assert L['run_id']==RUN and L['calls_remaining']==66;L['status']='RUNNING_POSITIVE_CHAIN_DEEPENING';dump(LP,L)
 S={x['f0']:x for x in (json.loads(z) for z in CP.read_text().splitlines() if z.strip())};m=None
 for p in (R/'.env',R/'config/supervisor/supervisord.conf'):
  if p.exists():m=re.search(r'HELIUS_RPC_URL\s*=\s*["\']?([^"\'\n,]+)',p.read_text());
  if m:break
 url=m.group(1)
 def rpc(method,params):
  if not L['calls_remaining']:raise RuntimeError('BUDGET')
  L['calls_attempted']+=1;L['calls_remaining']-=1;L['last_dispatch']={'at':now(),'method':method};dump(LP,L);q=urllib.request.Request(url,data=json.dumps({'jsonrpc':'2.0','id':L['calls_attempted'],'method':method,'params':params}).encode(),headers={'Content-Type':'application/json'})
  with urllib.request.urlopen(q,timeout=30) as h:return json.loads(h.read()).get('result')
 for f in POS:
  while S[f]['depth_reached']<7 and L['calls_remaining']>=5:
   c=sorted(S[f]['edges'],key=lambda x:x['depth'])[-1]['parent'];d=S[f]['depth_reached']+1;sigs=rpc('getSignaturesForAddress',[c,{'limit':8}]) or [];found=[]
   for it in sigs[:4]:
    t=rpc('getTransaction',[it['signature'],{'encoding':'jsonParsed','maxSupportedTransactionVersion':0}]);found += [(a,p,t.get('blockTime') if t else None,it['signature'],t.get('meta',{}).get('fee') if t else None) for a,p in inbound(t,c)]
   if len({x[1] for x in found})!=1:S[f].update(status='STOPPED',stop_reason='NO_PARENT_FOUND' if not found else 'AMBIGUOUS_PARENT');snap(S);break
   a,p,b,sg,fee=max(found);e={'run_id':RUN,'f0':f,'depth':d,'child':c,'parent':p,'amount_lamports':a,'block_time':b,'signature':sg,'transaction_fee':fee,'asset':'SOL','forwarding_relationship':'inbound SOL transfer to child','selection_reason':'repeated-20,100 chain deepening; sole observed parent','confidence':'HIGH' if len(found)==1 else 'MEDIUM','evidence_source':'positive_decrement_deepening'}
   with EP.open('a') as h:h.write(json.dumps(e,sort_keys=True)+'\n');h.flush();os.fsync(h.fileno())
   S[f]['edges'].append(e);S[f]['depth_reached']=d;snap(S)
 L.update(status='COMPLETE_POSITIVE_CHAIN_DEEPENING',completed_at=now());dump(LP,L);snap(S);print(json.dumps({'calls':L['calls_attempted'],'remaining':L['calls_remaining']}))
if __name__=='__main__':main()
