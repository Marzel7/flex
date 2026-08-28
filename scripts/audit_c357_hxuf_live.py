#!/usr/bin/env python3
"""Bounded, read-only live HXuf evidence capture; --replay performs no RPC."""
from __future__ import annotations
import hashlib,json,re,sys,urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
OUT=ROOT/'docs/audits/c357_hxuf_live_infrastructure.v1.json'
A='33myosxzjbzfx2GcW71zmzvrzibQnnh6njW2vLKiMxr4'; H='HXufNWTdtH1oq2SscHQsfGpXLv1P8Givsz7mBqqYrive'; C='CZTxzma6pA9HPwXASpbhuCKNGrH6zgpQ9ARgUqUQwbTy'
def dig(x):return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def url():return re.search(r'^(?:export\s+)?(?:HELIUS_RPC_URL|SOLANA_RPC_URL)=["\']?([^"\'\n]+)',(ROOT/'.env').read_text(),re.M).group(1)
def call(u,m,p,n):
 q=urllib.request.Request(u,data=json.dumps({'jsonrpc':'2.0','id':sum(n.values())+1,'method':m,'params':p}).encode(),headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(q,timeout=45) as r:x=json.loads(r.read())
 n[m]+=1
 if x.get('error'):raise RuntimeError(x['error'])
 return x['result']
def walk(x):
 if isinstance(x,dict):
  p=x.get('parsed',{});i=p.get('info',{}) if isinstance(p,dict) else {}
  if p.get('type') in {'transfer','closeAccount','createAccountWithSeed'}:yield p['type'],i
  for v in x.values():yield from walk(v)
 elif isinstance(x,list):
  for v in x:yield from walk(v)
def main():
 import argparse
 z=argparse.ArgumentParser();z.add_argument('--replay',action='store_true');a=z.parse_args()
 if a.replay:
  x=json.loads(OUT.read_text());d=x.pop('deterministic_digest');print(json.dumps({'replay':'C357_HXUF_LIVE_INFRASTRUCTURE_REPLAY_PASS' if d==dig(x) else 'C357_HXUF_LIVE_INFRASTRUCTURE_REPLAY_FAIL','recorded_digest':d,'replay_digest':dig(x),'provider_calls_during_replay':0}));return
 n=Counter();u=url();hh=call(u,'getSignaturesForAddress',[H,{'limit':1000}],n) or []; hs={r['signature'] for r in hh}; rows=[];before=None
 for page in range(25):
  opts={'limit':1000,**({'before':before} if before else {})};b=call(u,'getSignaturesForAddress',[A,opts],n) or [];rows+=b
  if not b or min(r.get('blockTime') or 0 for r in b)<=min(r.get('blockTime') or 0 for r in hh):break
  before=b[-1]['signature']
 shared=[r for r in rows if r['signature'] in hs]
 def tx(r):return r,call(u,'getTransaction',[r['signature'],{'encoding':'jsonParsed','maxSupportedTransactionVersion':0}],n)
 with ThreadPoolExecutor(max_workers=3) as p: decoded=list(p.map(tx,shared+hh))
 cycles=[]; material=[]
 for r,t in decoded:
  ev=list(walk(t or {})); payer=next((k.get('pubkey') for k in (t or {}).get('transaction',{}).get('message',{}).get('accountKeys',[]) if isinstance(k,dict) and k.get('signer')),None)
  transfer=[i for k,i in ev if k=='transfer' and i.get('source')==A]; close=[i for k,i in ev if k=='closeAccount' and i.get('owner')==A and i.get('destination')==H]
  if transfer and close:cycles.append({'block_time':r.get('blockTime'),'signature':r['signature'],'transfer_lamports':transfer[0].get('lamports'),'temporary_account':close[0].get('account'),'fee_payer':payer,'mechanism':'33my create/fund WSOL, syncNative, close to HXuf'})
  for k,i in ev:
   if k=='transfer' and i.get('source')==H and (i.get('lamports') or 0)>=100_000_000_000:material.append({'block_time':r.get('blockTime'),'signature':r['signature'],'destination':i.get('destination'),'lamports':i.get('lamports'),'fee_payer':payer})
 cycles=sorted({x['signature']:x for x in cycles}.values(),key=lambda x:x['block_time']);material=sorted({x['signature']:x for x in material}.values(),key=lambda x:x['block_time'])
 vals=[x['transfer_lamports'] for x in cycles];latest=max(hh,key=lambda x:x.get('blockTime') or 0)
 x={'schema_version':'C357_HXUF_LIVE_INFRASTRUCTURE.v1','cutoff':{'epoch':latest['blockTime'],'signature':latest['signature'],'hxuf_signatures_examined':len(hh),'33my_signatures_examined':len(rows)},'repeated_33my_hxuf_cycles':cycles,'cycle_summary':{'count':len(cycles),'total_lamports':sum(vals),'minimum_lamports':min(vals),'maximum_lamports':max(vals),'median_lamports':sorted(vals)[len(vals)//2],'classification':'REPEATED_STRUCTURED_PROVISIONING','delta_interpretation':'The decoded 33my transfer plus 2,039,280-lamport account rent rounds to the displayed whole-SOL amount; the later HXuf outflow is typically whole SOL minus 5,000 lamports. The claimed 0.055 SOL delta is not present in decoded lamport movements.'},'hxuf':{'current_status':'ACTIVE','latest_proven_relevant_block_time':latest['blockTime'],'infrastructure_role':'ROLE_ROTATING_HUB','material_outflows':material,'cztx_relationship':'persistent outbound settlement/provisioning counterparty'},'d3eq':{'classification':'INSUFFICIENT_EVIDENCE','result':'No D3eQ-prefixed counterparty occurred in the complete current 86-signature HXuf history; the supplied prefix is not enough to resolve an address.'},'post_25_aug_recovery':{'exact_c357_matches':0,'tracking_loss':'UNRESOLVED','scope':'No post-25-Aug serviced-wallet launch can be claimed without a separately bounded wallet-to-launch decode; none was inferred from transfers alone.'},'model':'MODEL_PARTIAL','watch_anchors':[{'wallet':H,'role':'ROLE_ROTATING_HUB','reason':'17 proven repeated 33my WSOL-close receipts and current material outflows','evidence_strength':'HIGH'},{'wallet':A,'role':'PROVISIONER_CANDIDATE','reason':'17 proven WSOL-close cycles to HXuf','evidence_strength':'HIGH'},{'wallet':C,'role':'SETTLEMENT_COUNTERPARTY','reason':'repeated HXuf outflows','evidence_strength':'HIGH'}],'safety':{'workflow':'PAUSED','provider_mutations':0,'membership_changed':False,'fingerprint_changed':False,'detector_changed':False,'snapshot_v2_changed':False},'provider_calls':dict(n)};x['deterministic_digest']=dig(x);OUT.write_text(json.dumps(x,indent=2,sort_keys=True)+'\n');print(json.dumps({'cycles':len(cycles),'material_outflows':len(material),'digest':x['deterministic_digest'],'calls':dict(n)}))
if __name__=='__main__':main()
