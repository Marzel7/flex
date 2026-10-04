import sqlite3,pytest
from src.ops.universal_assignment_bridge_consumer import Authorities,Consumer,TARGETED_PEPEINU
AUTH=Authorities('canonical-fixture','universal-fixture','canonical-fixture','universal-fixture')
def event(op='byzantine',eid='e',at=10):return {'event_id':eid,'operation_id':op,'mint':'m','assigned_at':at,'source_authority':'canonical-fixture'}
def c(db=None,instance='one'):return Consumer(db or sqlite3.connect(':memory:'),AUTH,instance,9,'qualified','qualified')
def opening(adapter,e):return {'qualified':True,'timestamp':1791112830,'mc_usd':3925.9516827750313,'adapter':adapter}
def test_crash_matrix_restart_no_duplicate_work():
 d=sqlite3.connect(':memory:');x=c(d);calls=[];hand=lambda *a:calls.append(a)
 for at,fault in enumerate(('before_projection','after_projection','after_opening','after_handoff'),20):
  with pytest.raises(RuntimeError):x.process(event(eid=fault,at=at),opening,hand,20,fault)
  assert x.process(event(eid=fault,at=at),opening,hand,21)=='UNIVERSAL_HANDED_OFF'
 assert len(calls)==4
def test_boundary_held_and_duplicate():
 x=c();assert x.process(event(eid='old',at=9),opening,lambda *_:None,1)=='ALREADY_BEHIND_CURSOR';assert x.process(event('nexus','held',10),opening,lambda *_:None,2)=='ADAPTER_UNAVAILABLE';assert x.process(event('watchtower','next',11),opening,lambda *_:None,3)=='UNIVERSAL_HANDED_OFF';assert x.cursor()==(11,'next')
def test_ambiguous_durable_not_retried():
 x=c();assert x.process(event(),lambda *_:(_ for _ in ()).throw(TimeoutError()),lambda *_:None,1)=='AMBIGUOUS_ADAPTER';assert x.process(event(),lambda *_:pytest.fail('retry'),lambda *_:None,2)=='ALREADY_BEHIND_CURSOR'
def test_loop_watchtower_byzantine_nexus_future():
 x=c();seen=[];read=lambda cur:[event('watchtower','w',10),event('byzantine','b',11),event('nexus','n',12),event('future','f',13)]
 assert x.run_once(read,opening,lambda *a:seen.append(a),1)==['UNIVERSAL_HANDED_OFF','UNIVERSAL_HANDED_OFF','ADAPTER_UNAVAILABLE','ADAPTER_UNAVAILABLE'];assert len(seen)==2 and x.health()['cursor']==(13,'f')
def test_executable_loop_runs_until_injected_stop():
 x=c();ticks=iter([False,True]);assert x.run_forever(lambda _:[],opening,lambda *_:None,lambda:next(ticks),0) is None
def test_targeted_pepeinu_isolated():
 x=c();before=x.cursor();e=event('byzantine',TARGETED_PEPEINU,999);e['mint']='Ah7xh8F2auwkZWh1KHDEjwuabdhKKJt2sCxqBH8mpump';assert x.reconcile_pepeinu(e,opening,lambda *_:None,1)=='UNIVERSAL_HANDED_OFF';assert x.cursor()==before
def test_singleton_authority_head_guards():
 d=sqlite3.connect(':memory:');c(d).acquire_lease(1)
 with pytest.raises(RuntimeError):c(d,'two').acquire_lease(2)
 with pytest.raises(RuntimeError):Consumer(sqlite3.connect(':memory:'),Authorities('wrong','universal-fixture','canonical-fixture','universal-fixture'),'x',1,'q','q')
 with pytest.raises(RuntimeError):Consumer(sqlite3.connect(':memory:'),AUTH,'x',1,'dirty','qualified')
