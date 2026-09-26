import sqlite3
import pytest
from src.ops.operation_admission_adapter import *

def db():
 c=sqlite3.connect(':memory:'); c.execute('CREATE TABLE operator_launch_membership(mint TEXT PRIMARY KEY,operator_id TEXT,source_population_id TEXT,assigned_at INTEGER,event_id TEXT)'); ensure_schema(c); return c
def n(m='m'): return {'operation_id':'op','mint':m,'nomination_type':'retained','nomination_semantic_version':'v1','nomination_evidence_id':'e1'}
def a(i='a'): return {'state':'CONFLICT','assessment_id':i,'semantic_version':'v1'}
def p(): return {'policy_id':'p','policy_version':'v1','required_assessment_outcome':'QUALIFIED_PROSPECTIVE_MEMBER','positive_action':'REVIEW'}
def test_compact_input_and_guard_fail_closed(monkeypatch):
 c=db();
 with pytest.raises(ValueError): persist_candidate(c,{**n(),'mint':'x'*300},1)
 monkeypatch.setattr('src.ops.operation_admission_adapter.MAX_LOGICAL_RECORDS',0)
 with pytest.raises(RuntimeError): persist_candidate(c,n(),1)
 assert c.execute('SELECT count(*) FROM operation_admission_candidates').fetchone()[0]==0
def test_batched_pruning_is_idempotent_and_preserves_canonical_provenance():
 c=db()
 for i in range(3):
  x=n(str(i)); cid=persist_candidate(c,x,1); persist_outcome(c,cid,x,a(str(i)),p(),1)
 assert prune_terminal_noncanonical(c,older_than=2,limit=2)==2
 assert prune_terminal_noncanonical(c,older_than=2,limit=2)==1
 assert prune_terminal_noncanonical(c,older_than=2,limit=2)==0
def test_budget_is_well_below_large_file_threshold():
 assert HARD_STORAGE_STOP_BYTES < 500000000
 assert MAX_LOGICAL_RECORDS * MAX_COMPACT_RECORD_BYTES <= HARD_STORAGE_STOP_BYTES
