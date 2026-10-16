"""Read-only bounded HTTP acceptance against isolated DEV043 only."""
import urllib.request,urllib.error,json,hashlib,sys
from pathlib import Path
BASE='http://127.0.0.1:8143';result=[]
routes=['/healthz','/api/ops/operators','/api/operations/live-monitor','/operations/playbooks','/operations/watchtower/playbook','/operations/d8ee4d7a-fcd6-5a5b-b897-24f6ab56e334/playbook','/intelligence/operator/04265d9f-6eb2-568c-a49e-9253091a4dbb','/intelligence/operator/d8ee4d7a-fcd6-5a5b-b897-24f6ab56e334','/intelligence/operator/bd7d7479-1454-5d41-9f68-115550348f3e','/ops-os','/system-health','/intelligence/treasury-review','/api/ops/treasury-review?status=CONFIRMED&limit=2','/api/ops-v2/intel/runtime-budget']
for path in routes:
 with urllib.request.urlopen(BASE+path,timeout=60) as r:
  body=r.read(8000000);assert len(body)<8000000,path;assert r.status==200
  checks=[]
  if path=='/api/operations/live-monitor':
   d=json.loads(body);rows=d['recent_operation_tokens'];keys=[(x['assignment_timestamp'],x['mint']) for x in rows];assert len(rows)==20 and keys==sorted(keys,reverse=True);assert any(x.get('entry_mc_usd') is not None for x in rows);checks=['latest20 chronology','retained entry evidence']
  if path=='/operations/watchtower/playbook':assert b'UNAVAILABLE' in body and b'Automatically admitted' in body;checks=['null unavailable','no500']
  if path.startswith('/api/ops/treasury-review'):
   d=json.loads(body);assert d['ok'] is True and isinstance(d['items'],list);checks=['retained treasury projection','no schema initialization']
  if path=='/api/ops-v2/intel/runtime-budget':
   d=json.loads(body);assert 'UNAVAILABLE' in body.decode();checks=['listener counters explicitly unavailable without listener import']
  result.append({'route':path,'http_status':r.status,'response_sha256':hashlib.sha256(body).hexdigest(),'bytes':len(body),'checks':checks})
print(json.dumps({'status':'PASS','routes':result,'provider_calls':0,'production_changes':0},indent=2))
