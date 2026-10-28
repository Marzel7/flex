import copy,hashlib,importlib.util,json,multiprocessing,os,pathlib,socket,time
import pytest
ROOT=pathlib.Path(__file__).resolve().parents[1]
S=ROOT/'docs/dev/lightweight_dev004_first_v2/helius_investigation_budget/rpc_completion_safety/gate_publication_recovery.py'
spec=importlib.util.spec_from_file_location('orphan_recovery',S);r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
def H(p):return hashlib.sha256(p.read_bytes()).hexdigest()
@pytest.fixture
def case(tmp_path,monkeypatch):
 monkeypatch.setattr(socket.socket,'connect',lambda *a,**k:(_ for _ in ()).throw(AssertionError('NETWORK_FORBIDDEN')))
 p=ROOT/'runtime/listener/src/core/helius_denial_gate.py';spec=importlib.util.spec_from_file_location('fixture_gate',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);gate=m.Gate(tmp_path/'gate.json','fixture',clock=lambda:1000);gate.monotonic=lambda:1000;gate.initialize('PAYMENT_DENIED');policy=json.loads((ROOT/'docs/dev/lightweight_dev004_first_v2/helius_investigation_budget/POLICY.json').read_text());gate.configure_budget(policy,H(gate.path));origin=gate.register_origin('mint:fixture','a'*64)
 with gate.locked():
  state=gate.read();state['state']='OPEN';q=state['rpc_budget']['scopes'][origin];q.update(credits=87591,requests=1000,warned=True);q['workers']['listener']=87591
  for i in range(5):state['rpc_budget']['keys'][hashlib.sha256(str(i).encode()).hexdigest()]={'attempts':1,'status':'OWNER_EXIT_UNCERTAIN','at':10,'selector':'rpc:getTransaction','role':'listener','pid':99999999,'credits':1,'owner_exit':{'ticket':str(i)*32,'root':origin,'pid':99999999,'original_at':10,'retired_at':11,'reason':'PROCESS_GONE_OUTCOME_UNKNOWN'}}
  state['rpc_budget']['keys']['b'*64]={'attempts':14,'status':'RETRYABLE','at':0,'selector':'rpc:getAccountInfo','role':'walkback','pid':os.getpid(),'credits':14}
  gate.save(state)
 ticket=gate.reserve_http(role='walkback',scopes=(origin,),key='b'*64,selector='rpc:getAccountInfo',origin='c'*64)
 process=multiprocessing.get_context('fork').Process(target=time.sleep,args=(.01,));process.start();process.join(timeout=2);assert process.exitcode==0;pid=process.pid
 with gate.locked():state=gate.read();key=state['rpc_budget']['inflight'][ticket]['key'];state['rpc_budget']['keys'][key]['pid']=pid;gate.save(state)
 before=gate.read();target=copy.deepcopy(before);target['rpc_budget']['inflight'].pop(ticket);target['rpc_budget']['keys'][key]['status']='HTTP_COMPLETED';target['outcomes']+=1;target['statuses']['200']=target['statuses'].get('200',0)+1;stage=pathlib.Path(str(gate.path)+'.stage');stage.write_text(json.dumps(target,sort_keys=True)+'\n')
 original_gate_class=m.Gate
 def gate_factory(path,generation):
  instance=original_gate_class(path,generation,clock=lambda:1000);instance.monotonic=lambda:1000;return instance
 m.Gate=gate_factory
 return {'m':m,'g':gate,'pid':pid,'ticket':ticket,'before':before,'before_sha':H(gate.path),'stage_sha':H(stage),'stage':stage,'evidence':tmp_path/'evidence'}
def recover(c,crash=None):return r.recover_orphan_stage(c['m'],c['g'].path,c.get('generation','fixture'),expected_sha256=c['before_sha'],expected_stage_sha256=c['stage_sha'],ticket=c['ticket'],expected_pid=c['pid'],retired_pids=[c['pid']],evidence_dir=c['evidence'],crash=crash)
def test_exact_state_preserves_spending_and_five_prior_uncertain(case):
 c=case;raw=c['g'].path.read_bytes();stage=c['stage'].read_bytes();result=recover(c);after=c['g'].read();assert result['status']=='RECOVERED_UNCERTAIN_NO_REFUND' and result['original_request_credits']==15;assert sum(v['credits'] for v in after['rpc_budget']['scopes'].values())==87592;assert after['rpc_budget']['policy']['lifetime_credits']==100000;assert after['outcomes']==c['before']['outcomes'] and after['statuses']==c['before']['statuses'];assert sum(v['status']=='OWNER_EXIT_UNCERTAIN' for v in after['rpc_budget']['keys'].values())==6;assert (c['evidence']/'ledger.before.json').read_bytes()==raw and (c['evidence']/'stage.before.json').read_bytes()==stage;assert not c['stage'].exists()
 raw=c['g'].path.read_bytes();assert recover(c)['idempotent'] and c['g'].path.read_bytes()==raw
 entry=c['before']['rpc_budget']['inflight'][c['ticket']]
 with pytest.raises(c['m'].BudgetDeferred,match='REQUIRES_REVIEW'):c['g'].reserve_http(role='walkback',scopes=(entry['root'],),key=entry['key'],selector='rpc:getAccountInfo',origin='c'*64)
 assert c['g'].path.read_bytes()==raw
@pytest.mark.parametrize('point',['evidence_durable','stage_quarantined','owner_reconciled'])
def test_crash_restart_is_safe_and_idempotent(case,point):
 def crash(at):
  if at==point:raise RuntimeError('CRASH')
 with pytest.raises(RuntimeError,match='CRASH'):recover(case,crash)
 result=recover(case);assert result['spending_and_ownership_preserved'];assert not case['g'].read()['rpc_budget']['inflight'];assert recover(case)['idempotent']
@pytest.mark.parametrize('fault',['before_hash','stage_hash','corrupt','mixed_spending','ownership','owner_alive','wrong_pid','generation','pending','new_writer','conflicting_evidence'])
def test_unknown_or_ambiguous_state_is_not_cleared(case,fault,monkeypatch):
 c=case
 if fault=='before_hash':c['before_sha']='0'*64
 elif fault=='stage_hash':c['stage_sha']='0'*64
 elif fault=='corrupt':c['stage'].write_bytes(b'{');c['stage_sha']=H(c['stage'])
 elif fault in ['mixed_spending','ownership']:
  v=json.loads(c['stage'].read_text())
  if fault=='mixed_spending':next(iter(v['rpc_budget']['scopes'].values()))['credits']-=1
  else:next(iter(v['rpc_budget']['owners'].values()))['proof']='d'*64
  c['stage'].write_text(json.dumps(v,sort_keys=True)+'\n');c['stage_sha']=H(c['stage'])
 elif fault=='owner_alive':c['pid']=os.getpid()
 elif fault=='wrong_pid':c['pid']=99999999
 elif fault=='generation':c['generation']='wrong'
 elif fault=='pending':pathlib.Path(str(c['g'].path)+'.lock').write_bytes(b'PENDING_V1\n')
 elif fault=='new_writer':
  staged=c['stage'].read_bytes();c['stage'].unlink();state=c['g'].read();state['failures']+=1;c['g'].save(state);c['stage'].write_bytes(staged)
 else:c['evidence'].mkdir();(c['evidence']/'ledger.before.json').write_text('{}')
 before=c['g'].path.read_bytes();stage=c['stage'].read_bytes()
 with pytest.raises(Exception):recover(c)
 assert c['g'].path.read_bytes()==before and c['stage'].read_bytes()==stage
