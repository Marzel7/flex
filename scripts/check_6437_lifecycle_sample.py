#!/usr/bin/env python3
import json,re,urllib.request,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from scripts.reconstruct_6437_funder_infrastructure import cohort
WALLETS=['Bvrc31ZYRAPeuhnkWY8wzUSt57N5CtHCQrxJqaCPAuqN','4ae7rX1CekEAvEG4LzHbRpG6HzSWxuSNq2w8qjZHdBaC','3nTEKyixfWGcUSjAHsUZwad8QHrDoNQDWdKVHRXqkHkR','DwAbdjwrYi7dVjf96ahE3cKm7wCBzmRog78GviWSAfKz']; OUT=ROOT/'docs/audits/6437_remaining_funders_rpc_evidence.v1.jsonl'
url=re.search(r'HELIUS_RPC_URL=["\']?([^"\'\n]+)',(ROOT/'.env').read_text()).group(1); calls=0
def rpc(m,p):
 global calls
 if calls>=405: raise RuntimeError('BUDGET')
 calls+=1; q=urllib.request.Request(url,data=json.dumps({'jsonrpc':'2.0','id':calls,'method':m,'params':p}).encode(),headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(q,timeout=30) as r:return json.loads(r.read())['result']
launch={w:[] for w in WALLETS}
for x in cohort():
 if x['funder'] in launch: launch[x['funder']].append(x)
rows=[]
for w in WALLETS:
 sigs=rpc('getSignaturesForAddress',[w,{'limit':100}])
 for s in sigs:
  if calls>=405:break
  tx=rpc('getTransaction',[s['signature'],{'encoding':'jsonParsed','maxSupportedTransactionVersion':0}])
  if not tx:continue
  keys=[x if isinstance(x,str) else x.get('pubkey') for x in tx['transaction']['message'].get('accountKeys',[])]
  ins=tx['transaction']['message'].get('instructions',[])+sum([z.get('instructions',[]) for z in tx.get('meta',{}).get('innerInstructions',[])],[])
  for i in ins:
   p=i.get('parsed',{}); info=p.get('info',{}) if isinstance(p,dict) else {}
   if p.get('type')=='transfer' and int(info.get('lamports',-1))==41505000:
    rows.append({'sample_funder':w,'signature':s['signature'],'block_time':tx.get('blockTime'),'fee_payer':keys[0] if keys else None,'source':info.get('source'),'destination':info.get('destination'),'lamports':41505000,'direction':'OUTBOUND' if info.get('source')==w else 'INBOUND' if info.get('destination')==w else 'OTHER','launches':launch[w]})
OUT.write_text(''.join(json.dumps(x,sort_keys=True)+'\n' for x in rows)); print(json.dumps({'calls':calls,'events':len(rows),'rows':rows},default=str))
