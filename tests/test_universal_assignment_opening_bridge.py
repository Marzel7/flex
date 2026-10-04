import sqlite3
from src.ops.universal_assignment_opening_bridge import trigger,commit_qualified
PEPE={'qualified':True,'timestamp':1791112830,'mc_usd':3925.9516827750313,'provenance':'BYZANTINE_ACTUAL_ENTRY_V2','event_mint_match':'MATCH'}
def a(op='byzantine',id='a'): return {'id':id,'operation_id':op,'mint':'m'}
def test_registry_and_idempotent_trigger():
 d=sqlite3.connect(':memory:'); x=trigger(d,a()); y=trigger(d,a()); assert x==y and d.execute('select count(*) from universal_opening_work').fetchone()[0]==1
def test_nexus_and_unknown_visible():
 d=sqlite3.connect(':memory:'); assert trigger(d,a('nexus','nexus'))['status']=='ADAPTER_UNAVAILABLE'; assert trigger(d,a('future','future'))['status']=='ADAPTER_UNAVAILABLE'; assert d.execute('select count(*) from universal_opening_visible').fetchone()[0]==2
def test_watchtower_and_byzantine_handoff_once():
 for op,opening in [('watchtower',{**PEPE,'provenance':'FIRST_AVAILABLE'}),('byzantine',PEPE)]:
  d=sqlite3.connect(':memory:'); work=trigger(d,a(op,op))['work_id']; calls=[]; handoff=lambda assignment,entry: calls.append((assignment,entry)); assert commit_qualified(d,work,opening,handoff)['status']=='QUALIFIED_AND_HANDED_OFF'; assert commit_qualified(d,work,opening,handoff)['status']=='ALREADY_QUALIFIED'; assert len(calls)==1
def test_crash_restart_after_work_or_opening_commit():
 d=sqlite3.connect(':memory:'); w=trigger(d,a())['work_id']; assert trigger(d,a())['work_id']==w; calls=[]; commit_qualified(d,w,PEPE,lambda assignment,entry: calls.append((assignment,entry))); assert d.execute("select state from universal_opening_visible where assignment_id='a'").fetchone()[0]=='LIVE'
