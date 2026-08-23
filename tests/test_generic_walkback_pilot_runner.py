import json
import pytest
from src.discovery.generic_walkback_pilot_runner import PilotRunner

def test_namespace_manifest_and_request(tmp_path):
 r=PilotRunner(tmp_path,'r',['a','b'],lambda w:{'wallet':w},max_requests=2); r.start(); x,_=r.request('a',1); assert x['status']=='SUCCESS'; assert r.replay_digest()==r.replay_digest()
def test_collision(tmp_path):
 r=PilotRunner(tmp_path,'r',['a'],lambda w:{});r.start()
 with pytest.raises(RuntimeError): PilotRunner(tmp_path,'r',['a'],lambda w:{}).start()
def test_bound(tmp_path):
 r=PilotRunner(tmp_path,'r',['a'],lambda w:{},max_requests=0);r.start()
 with pytest.raises(RuntimeError): r.request('a',1)
def test_deadline(tmp_path):
 r=PilotRunner(tmp_path,'r',['a'],lambda w:{},wall_seconds=-1);r.start()
 with pytest.raises(TimeoutError): r.request('a',1)
def test_failure_is_recorded(tmp_path):
 r=PilotRunner(tmp_path,'r',['a'],lambda w:(_ for _ in ()).throw(ValueError()));r.start()
 with pytest.raises(ValueError):r.request('a',1)
 assert json.loads(next((tmp_path/'r'/'requests').glob('*')).read_text())['status']=='RPC_ERROR'

def test_depth_two_is_deduplicated_and_no_depth_three(tmp_path):
 calls=[]
 def provider(w): calls.append(w); return {'parent': {'a':'x','b':'x','x':'y'}[w]}
 def extract(w, raw, depth): return {'child_wallet':w,'parent_wallet':raw['parent'],'depth':depth}
 edges, replay=PilotRunner(tmp_path,'r',['b','a'],provider).run(extract)
 assert calls == ['b','a','x'] and all(e['depth'] in (1,2) for e in edges) and replay

def test_replay_artifact_is_provider_disabled(tmp_path):
 r=PilotRunner(tmp_path,'r',['a'],lambda w:{'parent':None}); r.run(lambda w,x,d:{'child_wallet':w,'parent_wallet':x['parent'],'depth':d})
 assert json.loads((tmp_path/'r'/'replay.json').read_text())['provider_disabled'] is True

def test_convergence_is_factual(tmp_path):
 r=PilotRunner(tmp_path,'r',['a','b'],lambda w:{'parent':'x'} if w in {'a','b'} else {'parent':None})
 edges,_=r.run(lambda w,x,d:{'child_wallet':w,'parent_wallet':x['parent'],'depth':d})
 assert sum(e['parent_wallet']=='x' for e in edges)==2

def test_retry_then_success_and_exhaustion(tmp_path):
 n=[0]
 def p(w): n[0]+=1; raise TimeoutError() if n[0]<2 else RuntimeError('nonretry')
 r=PilotRunner(tmp_path,'r',['a'],p);r.start()
 with pytest.raises(RuntimeError): r.request_with_retry('a',1)
 assert n[0]==2

def test_replay_missing_response_fails_closed(tmp_path):
 r=PilotRunner(tmp_path,'r',['a'],lambda w:{});r.start(); rec,_=r.request('a',1)
 (tmp_path/'r'/'responses'/(rec['request_id']+'.json')).unlink()
 with pytest.raises(RuntimeError): r.replay()

def test_cache_states_and_ambiguous_resume_hold(tmp_path):
 r=PilotRunner(tmp_path,'r',['a'],lambda w:{});r.start()
 assert r.cache_result({'complete':True,'response_sha256':'x','response':{}})=='COMPLETE_REPLAYABLE'
 assert r.cache_result(None)=='PARTIAL' and r.cache_result({'complete':True})=='UNVERIFIABLE'
 r._event('REQUEST_STARTED',request_id='lost')
 with pytest.raises(RuntimeError): r.resume_guard()

def test_canonical_replay_digests_detect_edge_tamper(tmp_path):
 r=PilotRunner(tmp_path,'r',['a'],lambda w:{'parent':None});_, acquired=r.run(lambda w,x,d:{'child_wallet':w,'parent_wallet':x['parent'],'depth':d})
 assert r.replay() == acquired
 edges=json.loads((tmp_path/'r'/'edges.json').read_text()); edges[0]['parent_wallet']='tampered'; (tmp_path/'r'/'edges.json').write_text(json.dumps(edges))
 with pytest.raises(RuntimeError, match='HOLD_REPLAY_MISMATCH'): r.replay()

def test_retained_response_tamper_fails_closed(tmp_path):
 r=PilotRunner(tmp_path,'r',['a'],lambda w:{'parent':None});r.start();rec,_=r.request('a',1)
 (tmp_path/'r'/'responses'/(rec['request_id']+'.json')).write_text('{"changed":true}')
 with pytest.raises(RuntimeError):r.replay()

def test_resume_response_persisted_without_provider_repeat(tmp_path):
 calls=[]; r=PilotRunner(tmp_path,'r',['a'],lambda w:(calls.append(w) or {'parent':'x'})); r.start(); rec,_=r.request('a',1)
 edges=r.resume(lambda w, raw, d:{'child_wallet':w,'parent_wallet':raw['parent'],'depth':d})
 assert calls == ['a'] and edges[0]['request_id']==rec['request_id']

def test_resume_started_only_fails_closed(tmp_path):
 r=PilotRunner(tmp_path,'r',['a'],lambda w:{});r.start();r._event('REQUEST_STARTED',request_id='unknown')
 with pytest.raises(RuntimeError, match='ambiguous in-flight'):r.resume(lambda *x:{})

def test_depth_one_complete_reconstructs_sorted_depth_two(tmp_path):
 r=PilotRunner(tmp_path,'r',['b','a'],lambda w:{});r.start()
 r._write('requests/1-a-000000.json',{'request_id':'1-a-000000','wallet':'a','depth':1,'status':'SUCCESS'})
 r._write('responses/1-a-000000.json',{'parent':'z'});r._write('edges.json',[{'request_id':'1-a-000000','child_wallet':'a','parent_wallet':'z','depth':1}]);r._event('DEPTH_1_COMPLETE')
 assert sorted({e['parent_wallet'] for e in json.loads((tmp_path/'r'/'edges.json').read_text()) if e['depth']==1}) == ['z']

def test_complete_cache_reuses_verified_response_without_provider(tmp_path):
 calls=[]; raw={'parent':'x'}; sha=__import__('hashlib').sha256((json.dumps(raw,sort_keys=True,separators=(',',':'))+'\n').encode()).hexdigest()
 r=PilotRunner(tmp_path,'r',['a'],lambda w:(calls.append(w) or {}));r.start()
 edge,meta=r.lookup_with_cache('a',1,{'a':{'complete':True,'response':raw,'response_sha256':sha}},lambda w,x,d:{'child_wallet':w,'parent_wallet':x['parent'],'depth':d})
 assert edge['parent_wallet']=='x' and not meta['provider_called'] and not calls

def test_partial_and_unverifiable_cache_do_not_become_negative(tmp_path):
 calls=[];r=PilotRunner(tmp_path,'r',['a'],lambda w:(calls.append(w) or {'parent':'x'}));r.start()
 for cache in ({}, {'a':{'complete':False}}, {'a':{'complete':True}}):
  edge,meta=r.lookup_with_cache('a',1,cache,lambda w,x,d:{'child_wallet':w,'parent_wallet':x['parent'],'depth':d})
  assert edge['parent_wallet']=='x' and meta['provider_called']
 assert len(calls)==3

def test_exact_wallet_cache_reuses_across_branches(tmp_path):
 calls=[]; raw={'parent':'x'};sha=__import__('hashlib').sha256((json.dumps(raw,sort_keys=True,separators=(',',':'))+'\n').encode()).hexdigest()
 r=PilotRunner(tmp_path,'r',['a'],lambda w:(calls.append(w) or {}));r.start(); cache={'same':{'complete':True,'response':raw,'response_sha256':sha}}
 assert not r.lookup_with_cache('same',1,cache,lambda w,x,d:x)[1]['provider_called']; assert not r.lookup_with_cache('same',2,cache,lambda w,x,d:x)[1]['provider_called']; assert not calls

def test_request_metadata_tamper_fails_replay(tmp_path):
 r=PilotRunner(tmp_path,'r',['a'],lambda w:{'parent':None});r.run(lambda w,x,d:{'child_wallet':w,'parent_wallet':x['parent'],'depth':d})
 p=next((tmp_path/'r'/'requests').glob('*')); x=json.loads(p.read_text());x['wallet']='tampered';p.write_text(json.dumps(x))
 with pytest.raises(RuntimeError, match='HOLD_REPLAY_MISMATCH'):r.replay()

def test_frozen_wrapper_equivalence(monkeypatch):
 from src.core import walkback_worker
 from src.discovery.generic_wallet_walkback import find_funding_parent
 pages=[{'signature':'S','slot':10}];tx={'slot':10,'blockTime':1010,'meta':{'preBalances':[100,0],'postBalances':[0,100]},'transaction':{'message':{'accountKeys':['PARENT','CHILD']}}}
 def rpc(method, params):
  if method=='getSignaturesForAddress':return pages
  if method=='getTransaction':return tx
  if method=='getAccountInfo':return {'value':{'owner':'11111111111111111111111111111111'}}
 monkeypatch.setattr(walkback_worker,'_rpc',rpc)
 result=find_funding_parent('CHILD')
 assert (result.parent_wallet,result.signature,result.slot,result.block_time,result.amount_sol,result.state)==('PARENT','S',10,1010,0.0,'PARENT_FOUND')
