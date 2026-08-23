import hashlib,json,os
from types import SimpleNamespace
from scripts.run_generic_walkback_pilot import preflight,CONTRACT,TRACK
from scripts.run_generic_walkback_pilot import execute_acquire,execute_replay
from scripts.run_generic_walkback_pilot import main
from src.discovery.immutable_jsonrpc_transport import ImmutableJsonRpcTransport
from src.discovery.generic_walkback_rpc_adapter import RetainedRpcAdapter
def args(tmp_path, **x):
 p=tmp_path/'m.json'; seeds=[f's{i}' for i in range(100)];p.write_text(json.dumps({'seeds':seeds}));d=hashlib.sha256(json.dumps(seeds,sort_keys=True,separators=(',',':')).encode()).hexdigest();v=dict(manifest=str(p),manifest_sha=d,run_dir=str(tmp_path/'run'),contract_sha=CONTRACT,track_sha=TRACK,track_count=21594,provider_env='FIXTURE_PROVIDER',depth=2,max_requests=5000,wall_seconds=1800,mock=False,mode='acquire');v.update(x);return SimpleNamespace(**v)
def test_valid_and_binding_failures(tmp_path,monkeypatch):
 monkeypatch.setenv('FIXTURE_PROVIDER','secret');assert preflight(args(tmp_path))['state']=='PASS_PREFLIGHT'
 for k,v in [('contract_sha','x'),('track_sha','x'),('track_count',1),('depth',3),('max_requests',1),('wall_seconds',1),('mock',True)]:assert preflight(args(tmp_path,**{k:v}))['state'].startswith('HOLD')
def test_missing_provider_and_manifest_tamper(tmp_path,monkeypatch):
 monkeypatch.delenv('FIXTURE_PROVIDER',raising=False);assert preflight(args(tmp_path))['provider_calls']==0

def test_replay_never_requires_provider(tmp_path,monkeypatch):
 monkeypatch.delenv('FIXTURE_PROVIDER',raising=False);a=args(tmp_path,mode='replay');__import__('pathlib').Path(a.run_dir).mkdir()
 assert preflight(a)['state']=='PASS_REPLAY_PREFLIGHT'

def test_cli_acquire_composes_adapter_and_two_depths(tmp_path,monkeypatch):
 monkeypatch.setenv('FIXTURE_PROVIDER','present'); a=args(tmp_path)
 class R:
  def __init__(self,b):self.b=b;self.status=200
  def read(self):return self.b
 pages=b'{"result":[{"signature":"S","slot":10}]}' ;tx=b'{"result":{"slot":10,"blockTime":1,"meta":{"preBalances":[1,0],"postBalances":[0,1]},"transaction":{"message":{"accountKeys":["P","s0"]}}}}';owner=b'{"result":{"value":{"owner":"11111111111111111111111111111111"}}}'
 def factory(x):
  def open(req,timeout):return R(pages if b'getSignaturesForAddress' in req.data else tx if b'getTransaction' in req.data else owner)
  return RetainedRpcAdapter(ImmutableJsonRpcTransport(tmp_path,'transport','https://fixture',open))
 out=execute_acquire(a,factory);assert out['state']=='PASS' and out['depth_1_seeds']==100 and (tmp_path/'run'/'lifecycle.jsonl').exists()
 a.mode='replay';assert execute_replay(a)['state']=='PASS_REPLAY'

def test_main_invokes_acquire_factory(tmp_path,monkeypatch):
 monkeypatch.setenv('FIXTURE_PROVIDER','https://fixture');a=args(tmp_path);seen=[]
 class A:
  class T: root=tmp_path/'transport'
  transport=T()
  def extract(self,w): seen.append(w);return type('X',(),{'parent_wallet':None,'signature':None,'slot':None,'block_time':None,'amount_sol':None,'mechanism':None,'state':'NO_QUALIFYING_PARENT'})(),['id-'+w]
 argv=['--manifest',a.manifest,'--manifest-sha',a.manifest_sha,'--run-dir',a.run_dir,'--contract-sha',a.contract_sha,'--track-sha',a.track_sha,'--track-count','21594','--provider-env','FIXTURE_PROVIDER','--mode','acquire']
 main(argv,lambda x:A());assert len(seen)==100
