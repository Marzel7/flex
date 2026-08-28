#!/usr/bin/env python3
"""Read-only, one-page upstream audit for the remaining C357 funders."""
from __future__ import annotations
import json, os, re, sqlite3, urllib.request
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]; DB=ROOT/'database/wt_ops_v2.db'; OUT=ROOT/'docs/audits/c357_remaining_upstream_funders.v1.json'
CID='p3r-v2-c357da9d0d4d560311e4'
KNOWN={'ByZc7RNeYowEg2jKo2giytWb9WmNyZPrQ1hXhnGSzHTY','F5ZCNpw2xRcZNnuwYaFvNBb13Rzk3Pn4CnmSkyRsK229','HS5GjB4KTJbbBdYHkJV8qDpq8gmU9wck2qsxgz3ifgke'}
def rpc_url():
 t=(ROOT/'.env').read_text(); m=re.search(r'^(?:export\s+)?(?:HELIUS_RPC_URL|SOLANA_RPC_URL)=["\']?([^"\'\n]+)',t,re.M)
 if not m: raise RuntimeError('RPC URL unavailable')
 return m.group(1)
def call(url,method,params,calls):
 q=urllib.request.Request(url,data=json.dumps({'jsonrpc':'2.0','id':len(calls)+1,'method':method,'params':params}).encode(),headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(q,timeout=30) as r: value=json.loads(r.read())
 calls.append(method)
 if value.get('error'): raise RuntimeError(value['error'])
 return value.get('result')
def keys(tx): return [x.get('pubkey') if isinstance(x,dict) else x for x in tx['transaction']['message']['accountKeys']]
def source(tx,funder):
 if not tx:return None
 k=keys(tx)
 if funder not in k:return None
 i=k.index(funder); m=tx.get('meta') or {}; d=(m.get('postBalances') or [])[i]-(m.get('preBalances') or [])[i]
 if d<=0:return None
 neg=[((a-b),k[j]) for j,(a,b) in enumerate(zip(m.get('preBalances') or [],m.get('postBalances') or [])) if b-a<0]
 return max(neg,default=(0,None))[1]
def main():
 c=sqlite3.connect(f'file:{DB}?mode=ro',uri=True);c.row_factory=sqlite3.Row
 try:
  family=next(x for x in json.loads((ROOT/'docs/agent_handoff/p3r/v2/p3r-v2-2dec1d40604c1f7c08c8/p3r_v2_candidate_membership.v1.json').read_text())['families'] if x['candidate_id']==CID)
  ph=','.join('?' for _ in family['mints']); raw=c.execute(f"select mint,candidate_parent,signature,block_time from wt_walkback_edge_candidates where selection_status='SELECTED' and hop_depth=1 and mint in ({ph})",family['mints']).fetchall()
 finally:c.close()
 launches=[dict(x) for x in raw]; by=defaultdict(list)
 for x in launches: by[x['candidate_parent']].append(x)
 target=sorted(set(by)-KNOWN); url=rpc_url(); calls=[]; result=[]
 for funder in target:
  history=call(url,'getSignaturesForAddress',[funder,{'limit':1000}],calls) or []; pos={x['signature']:i for i,x in enumerate(history)}; predecessors={}
  for launch in by[funder]:
   i=pos.get(launch['signature']); prev=history[i+1] if i is not None and i+1<len(history) else None
   if prev: predecessors[prev['signature']]=prev
  tx={s:call(url,'getTransaction',[s,{'encoding':'json','maxSupportedTransactionVersion':0}],calls) for s in predecessors}
  sources=defaultdict(int); linked=0
  for launch in by[funder]:
   i=pos.get(launch['signature']); prev=history[i+1] if i is not None and i+1<len(history) else None
   s=source(tx.get(prev['signature']) if prev else None,funder)
   if s: sources[s]+=1; linked+=1
  result.append({'direct_funder':funder,'members':len(by[funder]),'history_entries':len(history),'launch_linked_material_inbounds':linked,'upstream_provisioners':[{'address':a,'count':n} for a,n in sorted(sources.items(),key=lambda x:(-x[1],x[0]))]})
 value={'schema_version':'C357_REMAINING_UPSTREAM_FUNDER_RPC.v1','candidate_id':CID,'scope':'remaining direct funders; one getSignaturesForAddress page and launch-linked getTransaction only; no recursion','remaining_funders':len(target),'rpc_calls':len(calls),'providers':result,'safety':{'source_db_writes':0,'workflow_writes':0,'provider_mutations':0}}
 OUT.write_text(json.dumps(value,indent=2,sort_keys=True)+'\n'); print(json.dumps({'funders':len(target),'calls':len(calls),'resolved':sum(bool(x['upstream_provisioners']) for x in result)}))
if __name__=='__main__':main()
