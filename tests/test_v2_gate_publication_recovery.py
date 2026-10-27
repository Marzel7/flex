import pathlib,importlib.util,json,hashlib,copy,os,socket
import pytest
import sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'docs/dev/lightweight_dev004_first_v2/helius_investigation_budget/rpc_completion_safety'))
from gate_publication_recovery import recover
ROOT=pathlib.Path(__file__).resolve().parents[1]
def load(p):
 s=importlib.util.spec_from_file_location('existing_gate',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
@pytest.fixture
def e(tmp_path,monkeypatch):
 monkeypatch.setattr(socket.socket,'connect',lambda *a,**k:(_ for _ in ()).throw(AssertionError('NETWORK_FORBIDDEN')))
 m=load(ROOT/'runtime/listener/src/core/helius_denial_gate.py');p=tmp_path/'gate.json';g=m.Gate(p,'fixture');g.initialize('PAYMENT_DENIED');policy=json.loads((ROOT/'docs/dev/lightweight_dev004_first_v2/helius_investigation_budget/POLICY.json').read_bytes());g.configure_budget(policy,hashlib.sha256(p.read_bytes()).hexdigest());root=g.register_origin('mint:fixture','a'*64);state=g.read();state['state']='OPEN';q=state['rpc_budget']['scopes'][root];q.update(credits=627,requests=627);q['workers']['listener']=627
 for i in range(3):
  key=hashlib.sha256(str(i).encode()).hexdigest();state['rpc_budget']['keys'][key]={'attempts':1,'status':'OWNER_EXIT_UNCERTAIN','at':10,'selector':'rpc:getTransaction','role':'listener','pid':99999999,'credits':1,'owner_exit':{'ticket':str(i)*32,'root':root,'pid':99999999,'original_at':10,'retired_at':11,'reason':'PROCESS_GONE_OUTCOME_UNKNOWN'}}
 g.save(state);raw=p.read_bytes();pathlib.Path(str(p)+'.lock').write_bytes(b'PENDING_V1\n');snap=tmp_path/'snapshot.json';snap.write_bytes(raw)
 return m,p,snap

def call(e,pids=(99999999,)):
 m,p,s=e;return recover(m,p,json.loads(p.read_bytes())['generation'],hashlib.sha256(p.read_bytes()).hexdigest(),s,hashlib.sha256(s.read_bytes()).hexdigest(),pids)
def test_exact_conservative_recovery(e):
 m,p,s=e;before=json.loads(p.read_bytes());r=call(e);after=m.Gate(p,before['generation']).read();assert r['accounting_preserved'];before['state']='PAYMENT_DENIED';assert before==after
 with pytest.raises(m.Deferred):m.Gate(p,before['generation']).reserve('getTransaction')
 assert after==m.Gate(p,before['generation']).read()
def test_restart_and_no_repeated_recovery(e):
 call(e)
 with pytest.raises(RuntimeError,match='EXACT_PENDING'):call(e)
def test_active_owner_refusal(e):
 with pytest.raises(RuntimeError,match='OWNER_STILL'):call(e,(os.getpid(),))
@pytest.mark.parametrize('fault',['stage','snapshot','inflight','generation','corrupt','quota','missingowners'])
def test_fail_closed(e,fault):
 m,p,s=e;state=json.loads(p.read_bytes())
 if fault=='stage':p.with_name(p.name+'.stage').write_bytes(b'unknown')
 elif fault=='snapshot':s.write_text('{}')
 elif fault=='inflight':state['rpc_budget']['inflight']['a'*32]={};p.write_text(json.dumps(state));s.write_bytes(p.read_bytes())
 elif fault=='generation':state['generation']='wrong';p.write_text(json.dumps(state));s.write_bytes(p.read_bytes())
 elif fault=='corrupt':p.write_bytes(b'{')
 elif fault=='quota':state['rpc_budget']['policy']['lifetime_credits']=100001;p.write_text(json.dumps(state));s.write_bytes(p.read_bytes())
 before=p.read_bytes()
 with pytest.raises(Exception):
  if fault=='generation':recover(m,p,'expected',hashlib.sha256(before).hexdigest(),s,hashlib.sha256(s.read_bytes()).hexdigest(),(99999999,))
  else:call(e,() if fault=='missingowners' else (99999999,))
 assert p.read_bytes()==before
@pytest.mark.parametrize('boundary',[1,2,3,4])
def test_interrupted_recovery_retains_accounting(e,monkeypatch,boundary):
 m,p,s=e;before=json.loads(p.read_bytes());real=m.os.fsync;n=[0]
 def fail(fd):
  n[0]+=1
  if n[0]==boundary:raise OSError('FAULT')
  return real(fd)
 monkeypatch.setattr(m.os,'fsync',fail)
 with pytest.raises(OSError):call(e)
 after=json.loads(p.read_bytes());assert after['rpc_budget']==before['rpc_budget'];assert after['state'] in ('OPEN','PAYMENT_DENIED');assert pathlib.Path(str(p)+'.lock').read_bytes().startswith((b'PENDING_V1',b'READY_V1'))
