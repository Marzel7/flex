import hashlib,json,os
from types import SimpleNamespace
from scripts.run_generic_walkback_pilot import preflight,CONTRACT,TRACK
def args(tmp_path, **x):
 p=tmp_path/'m.json'; seeds=[f's{i}' for i in range(100)];p.write_text(json.dumps({'seeds':seeds}));d=hashlib.sha256(json.dumps(seeds,sort_keys=True,separators=(',',':')).encode()).hexdigest();v=dict(manifest=str(p),manifest_sha=d,run_dir=str(tmp_path/'run'),contract_sha=CONTRACT,track_sha=TRACK,track_count=21594,provider_env='FIXTURE_PROVIDER',depth=2,max_requests=5000,wall_seconds=1800,mock=False);v.update(x);return SimpleNamespace(**v)
def test_valid_and_binding_failures(tmp_path,monkeypatch):
 monkeypatch.setenv('FIXTURE_PROVIDER','secret');assert preflight(args(tmp_path))['state']=='PASS_PREFLIGHT'
 for k,v in [('contract_sha','x'),('track_sha','x'),('track_count',1),('depth',3),('max_requests',1),('wall_seconds',1),('mock',True)]:assert preflight(args(tmp_path,**{k:v}))['state'].startswith('HOLD')
def test_missing_provider_and_manifest_tamper(tmp_path,monkeypatch):
 monkeypatch.delenv('FIXTURE_PROVIDER',raising=False);assert preflight(args(tmp_path))['provider_calls']==0
