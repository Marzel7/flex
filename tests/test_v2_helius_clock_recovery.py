import copy,hashlib,importlib.util,pathlib
import pytest
from tests.test_v2_helius_investigation_budget import v,legacy
ROOT=pathlib.Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('qualified_clock_recovery',ROOT/'scripts/recover_helius_budget_clock.py');recovery=importlib.util.module_from_spec(spec);spec.loader.exec_module(recovery)

@pytest.fixture(autouse=True)
def no_network(monkeypatch):
 import socket
 def denied(*a,**k):raise AssertionError('No provider connections')
 monkeypatch.setattr(socket.socket,'connect',denied);monkeypatch.setattr(socket,'create_connection',denied)

@pytest.fixture
def case(v):
 e=v;e.root_id('root');e.open();t=e.reserve2();e.g.finish_http(t,200)
 e.old=e.g.read();e.now[0]+=28040;e.mono=[e.old['rpc_budget']['mono']+1431];e.g.monotonic=lambda:e.mono[0]
 e.sample=recovery.clock_sample(e.g);e.digest=hashlib.sha256(e.g.path.read_bytes()).hexdigest();e.now[0]+=6;e.mono[0]+=6
 return e


def test_exact_counterexample_then_anchor_only_recovery_and_restart(case):
 e=case
 with pytest.raises(e.m.Integrity,match='CLOCK_AMBIGUOUS'):e.reserve2(key='blocked')
 before=e.g.read();out=recovery.reconcile_forward_clock(e.g,expected_sha256=e.digest,first_sample=e.sample)
 after=e.g.read();expected=copy.deepcopy(before);expected['rpc_budget'].update(wall=e.now[0],mono=e.mono[0]);assert after==expected
 assert out['status']=='RECONCILED_ANCHORS_ONLY' and after['rpc_budget']['policy']['lifetime_credits']==100000
 fresh=e.m.Gate(e.g.path,'fixture',clock=lambda:e.now[0]);fresh.monotonic=lambda:e.mono[0]
 t=fresh.reserve_http(role='walkback',scopes=(e.h._hash('mint:root'),),key=e.h._hash('eligible'),selector='rpc:getTransaction',origin=e.h._hash('endpoint'))
 assert fresh.read()['rpc_budget']['scopes'][e.h._hash('mint:root')]['credits']==before['rpc_budget']['scopes'][e.h._hash('mint:root')]['credits']+1
 fresh.finish_http(t,200)

@pytest.mark.parametrize('defect',['checksum','sample_hash','sample_generation','short_stability','moving_clock','reboot','backward','cooldown','pending','denied','active_timer','future_request'])
def test_refusals_never_change_state(case,defect):
 e=case;digest=e.digest;sample=copy.deepcopy(e.sample)
 if defect=='checksum':digest='0'*64
 elif defect=='sample_hash':sample['state_sha256']='0'*64
 elif defect=='sample_generation':sample['generation']='wrong'
 elif defect=='short_stability':e.now[0]-=5;e.mono[0]-=5
 elif defect=='moving_clock':e.now[0]+=10
 elif defect=='reboot':e.mono[0]=1
 elif defect=='backward':e.now[0]=1
 else:
  s=e.g.read()
  if defect=='cooldown':s['rpc_budget']['mono']=e.mono[0]-30
  elif defect=='pending':
   # Make an actual protected reservation under qualified clocks, then jump.
   e.now[0]=e.old['rpc_budget']['wall']+120;e.mono[0]=e.old['rpc_budget']['mono']+120;e.g.reserve_http(role='listener',scopes=(e.h._hash('mint:root'),),key=e.h._hash('pending'),selector='rpc:getTransaction',origin=e.h._hash('endpoint'));s=e.g.read();e.now[0]+=28040;e.mono[0]+=1431
  elif defect=='denied':s['state']='PAYMENT_DENIED'
  elif defect=='active_timer':s['next_at']=e.now[0]+100
  else:next(iter(s['rpc_budget']['keys'].values()))['at']=s['rpc_budget']['wall']+1
  e.g.save(s);sample=recovery.clock_sample(e.g);digest=sample['state_sha256'];e.now[0]+=6;e.mono[0]+=6
 before=e.g.path.read_bytes()
 with pytest.raises((RuntimeError,e.m.Integrity)):recovery.reconcile_forward_clock(e.g,expected_sha256=digest,first_sample=sample)
 assert e.g.path.read_bytes()==before


def test_no_change_when_clock_already_valid(case):
 e=case;s=e.g.read();s['rpc_budget']['wall']=e.now[0];s['rpc_budget']['mono']=e.mono[0];e.g.save(s);sample=recovery.clock_sample(e.g);e.now[0]+=6;e.mono[0]+=6;before=e.g.path.read_bytes()
 assert recovery.reconcile_forward_clock(e.g,expected_sha256=sample['state_sha256'],first_sample=sample)['status']=='ALREADY_VALID'
 assert e.g.path.read_bytes()==before


def test_crash_before_publication_preserves_original(case,monkeypatch):
 e=case;before=e.g.path.read_bytes()
 def crash(_):raise RuntimeError('SIMULATED_BEFORE_PUBLICATION')
 monkeypatch.setattr(e.g,'save',crash)
 with pytest.raises(RuntimeError):recovery.reconcile_forward_clock(e.g,expected_sha256=e.digest,first_sample=e.sample)
 assert e.g.path.read_bytes()==before


def test_crash_during_existing_publication_fails_closed(case,monkeypatch):
 e=case;original=e.m.os.replace
 def crash(*a,**k):raise OSError('SIMULATED_BEFORE_RENAME')
 monkeypatch.setattr(e.m.os,'replace',crash)
 with pytest.raises(OSError):recovery.reconcile_forward_clock(e.g,expected_sha256=e.digest,first_sample=e.sample)
 with pytest.raises(e.m.Integrity,match='PUBLICATION_AMBIGUOUS'):e.g.read()
 monkeypatch.setattr(e.m.os,'replace',original)
 # Original durable file remains intact and charged; no marker clearing.
 assert hashlib.sha256(e.g.path.read_bytes()).hexdigest()==e.digest


def test_stale_repeat_cannot_reapply_or_reset_allowance(case):
 e=case;recovery.reconcile_forward_clock(e.g,expected_sha256=e.digest,first_sample=e.sample);before=e.g.path.read_bytes()
 with pytest.raises(RuntimeError,match='STATE_CHANGED'):recovery.reconcile_forward_clock(e.g,expected_sha256=e.digest,first_sample=e.sample)
 assert e.g.path.read_bytes()==before


def test_lifetime_ceiling_stays_exhausted(case):
 e=case;s=e.g.read();q=s['rpc_budget']['scopes'][e.h._hash('mint:root')];q.update(credits=100000,warned=True);q['workers']={k:100000 if k=='listener' else 0 for k in q['workers']};e.g.save(s);sample=recovery.clock_sample(e.g);e.now[0]+=6;e.mono[0]+=6
 recovery.reconcile_forward_clock(e.g,expected_sha256=sample['state_sha256'],first_sample=sample);before=e.g.path.read_bytes()
 with pytest.raises(e.m.BudgetDeferred,match='CREDIT_LIMIT'):e.reserve2(key='over-cap')
 assert e.g.path.read_bytes()==before


def test_review_held_signature_remains_blocked(case):
 e=case;s=e.g.read();key=next(iter(s['rpc_budget']['keys'].values()));root=e.h._hash('mint:root');key['status']='OWNER_EXIT_UNCERTAIN';key['owner_exit']={'ticket':'a'*32,'root':root,'pid':key['pid'],'original_at':key['at'],'retired_at':s['rpc_budget']['wall'],'reason':'PROCESS_GONE_OUTCOME_UNKNOWN'};e.g.save(s);sample=recovery.clock_sample(e.g);e.now[0]+=6;e.mono[0]+=6;before=e.g.read()
 recovery.reconcile_forward_clock(e.g,expected_sha256=sample['state_sha256'],first_sample=sample);after=e.g.read()
 assert after['rpc_budget']['keys']==before['rpc_budget']['keys']
 with pytest.raises(e.m.BudgetDeferred,match='UNCERTAIN'):e.reserve2()
 assert e.g.read()==after


def test_live_owner_publication_can_advance_without_losing_history(case):
 e=case;first_raw=e.g.path.read_bytes();e.g.register_origin('mint:new-legitimate-subject','2'*64);digest=hashlib.sha256(e.g.path.read_bytes()).hexdigest();before=e.g.read()
 result=recovery.reconcile_forward_clock(e.g,expected_sha256=digest,first_sample=e.sample,first_state_raw=first_raw)
 assert result['mutated'];after=e.g.read();expected=copy.deepcopy(before);expected['rpc_budget'].update(wall=e.now[0],mono=e.mono[0]);assert after==expected

@pytest.mark.parametrize('defect',['wrong_preimage','refund','owner_rebind'])
def test_changed_ledger_history_cannot_bypass_sample_authority(case,defect):
 e=case;first_raw=e.g.path.read_bytes();s=e.g.read()
 if defect=='wrong_preimage':first_raw=b'wrong'
 elif defect=='refund':q=s['rpc_budget']['scopes'][e.h._hash('mint:root')];q['credits']=0;q['workers']={r:0 for r in q['workers']}
 else:s['rpc_budget']['owners'][e.h._hash('mint:root')]['proof']='3'*64
 s['outcomes']+=1;e.g.save(s);digest=hashlib.sha256(e.g.path.read_bytes()).hexdigest();before=e.g.path.read_bytes()
 with pytest.raises(RuntimeError):recovery.reconcile_forward_clock(e.g,expected_sha256=digest,first_sample=e.sample,first_state_raw=first_raw)
 assert e.g.path.read_bytes()==before
