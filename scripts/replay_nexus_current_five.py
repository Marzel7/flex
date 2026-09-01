import json, os, re, urllib.request
from pathlib import Path
root=Path(__file__).resolve().parents[1]; audit=root/'docs/audits'
ledger=json.loads((audit/'nexus_current_detector_replay_ledger.v1.json').read_text())
env=(root/'.env').read_text(); url=re.search(r'HELIUS_RPC_URL=["\']?([^"\'\n]+)',env).group(1)
out=[]
for e in ledger['entries']:
    body=json.dumps({'jsonrpc':'2.0','id':e['mint'],'method':'getTransaction','params':[e['signature'],{'encoding':'jsonParsed','maxSupportedTransactionVersion':0}]}).encode()
    try:
        r=json.loads(urllib.request.urlopen(urllib.request.Request(url,data=body,headers={'Content-Type':'application/json'}),timeout=30).read())
        e['status']='FETCHED' if r.get('result') else 'NO_RESULT'; out.append({'mint':e['mint'],'signature':e['signature'],'provider_result':r.get('result')})
    except Exception as x: e['status']='ERROR'; e['error']=type(x).__name__
ledger['calls_used']=5; ledger['calls_remaining']=0
(audit/'nexus_current_detector_replay_ledger.v1.json').write_text(json.dumps(ledger,indent=2)+'\n')
(audit/'nexus_current_detector_raw_cache.v1.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in out))
print([(e['mint'],e['status']) for e in ledger['entries']])
