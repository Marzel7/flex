#!/usr/bin/env python3
"""Read-only direct-transfer linkage check across audited C357 wallets."""
from __future__ import annotations
import json,re,urllib.request
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; AUDIT=ROOT/'docs/audits/c357_remaining_upstream_funders.v1.json'; OUT=ROOT/'docs/audits/c357_direct_transfer_links.v1.json'; CHECK=ROOT/'docs/audits/c357_direct_transfer_links.history_checkpoint.v1.json'
KNOWN={'ByZc7RNeYowEg2jKo2giytWb9WmNyZPrQ1hXhnGSzHTY':'Df8CJQR7fUTYAQSQwtsgUDs5b6JWNULzwhJJXDCJkdya','F5ZCNpw2xRcZNnuwYaFvNBb13Rzk3Pn4CnmSkyRsK229':'3GL5bXdDriApC4J2gn42L9fH2xFxq9Ziifr3pM79hBoi','HS5GjB4KTJbbBdYHkJV8qDpq8gmU9wck2qsxgz3ifgke':'8Bk1fBnoc9Yk3HUz1UWihT2ewgbxMm7LTEoemabUVqmk'}
def url():
 m=re.search(r'^(?:export\s+)?(?:HELIUS_RPC_URL|SOLANA_RPC_URL)=["\']?([^"\'\n]+)',(ROOT/'.env').read_text(),re.M); return m.group(1)
def call(u,method,params,calls):
 q=urllib.request.Request(u,data=json.dumps({'jsonrpc':'2.0','id':len(calls)+1,'method':method,'params':params}).encode(),headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(q,timeout=30) as r:v=json.loads(r.read())
 calls.append(method)
 if v.get('error'):raise RuntimeError(v['error'])
 return v.get('result')
def walk(x):
 if isinstance(x,dict):
  if x.get('parsed',{}).get('type')=='transfer': yield x['parsed'].get('info',{})
  for v in x.values():yield from walk(v)
 elif isinstance(x,list):
  for v in x:yield from walk(v)
def main():
 data=json.loads(AUDIT.read_text())['providers']; fmap=dict(KNOWN)
 for x in data:
  if x['upstream_provisioners']:fmap[x['direct_funder']]=x['upstream_provisioners'][0]['address']
 wallets=set(fmap)|set(fmap.values()); u=url();calls=[]; sigs=defaultdict(set)
 if CHECK.exists():
  histories=json.loads(CHECK.read_text())['histories']
 else:
  def history(w): return w,call(u,'getSignaturesForAddress',[w,{'limit':1000}],calls) or []
  histories={w:rows for w,rows in ThreadPoolExecutor(max_workers=6).map(history,sorted(wallets))}
  CHECK.write_text(json.dumps({'wallet_count':len(wallets),'histories':histories},sort_keys=True))
 for w,rows in histories.items():
  for row in rows:sigs[row['signature']].add(w)
 shared={s:ws for s,ws in sigs.items() if len(ws)>1}; links=[]
 def transaction(item):
  s,ws=item
  try: return s,ws,call(u,'getTransaction',[s,{'encoding':'jsonParsed','maxSupportedTransactionVersion':0}],calls),None
  except Exception as exc: return s,ws,None,type(exc).__name__
 with ThreadPoolExecutor(max_workers=3) as pool:
  inspected=list(pool.map(transaction,sorted(shared.items())))
 for s,ws,tx,error in inspected:
  if error: continue
  for i in walk(tx or {}):
   if i.get('source') in wallets and i.get('destination') in wallets:links.append({'signature':s,'source':i['source'],'destination':i['destination'],'lamports':i.get('lamports'),'shared_history_wallets':sorted(ws)})
 value={'schema_version':'C357_DIRECT_TRANSFER_LINKS.v1','scope':'one history page per audited direct-funder/upstream wallet; inspect only shared signatures; no recursion','wallet_count':len(wallets),'rpc_calls':len(calls),'shared_signatures_checked':len(shared),'direct_transfers':links,'transaction_errors':sum(bool(error) for _,_,_,error in inspected),'safety':{'source_db_writes':0,'workflow_writes':0,'provider_mutations':0}}
 OUT.write_text(json.dumps(value,indent=2,sort_keys=True)+'\n');print(json.dumps({'wallets':len(wallets),'calls':len(calls),'shared':len(shared),'direct_transfers':len(links)}))
if __name__=='__main__':main()
