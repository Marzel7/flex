import json, os, re, urllib.request
from pathlib import Path
p=Path('docs/audits/canonical_birth_recovery_run_ledger.v1.json'); d=json.loads(p.read_text())
assert d['run_id']=='canonical-birth-4d1c3142547b4896' and d['calls_remaining']>0
d['calls_used']+=1; d['calls_remaining']-=1
q=p.with_suffix('.pending'); q.write_text(json.dumps(d,indent=2)+'\n'); os.replace(q,p)
t=Path('config/supervisor/supervisord.conf').read_text(); u=re.search(r'HELIUS_RPC_URL\s*=\s*["\']?([^"\'\n,]+)',t).group(1)
mint='4QoTTjqGmKAURDpVCmpFb8WrkAWJ76crpd67T3KEsXrM'
r=urllib.request.Request(u,data=json.dumps({'jsonrpc':'2.0','id':d['calls_used'],'method':'getSignaturesForAddress','params':[mint,{'limit':1000}]}).encode(),headers={'Content-Type':'application/json'})
with urllib.request.urlopen(r,timeout=30) as h: body=json.loads(h.read())
Path('docs/audits/canonical_birth_recovery_first_response.json').write_text(json.dumps({'mint':mint,'result':body.get('result',[])},indent=2))
print(d['calls_used'],len(body.get('result',[])))
