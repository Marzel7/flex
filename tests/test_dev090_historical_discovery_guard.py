"""Prospective discovery isolation; compact provider-free fixtures only."""
import importlib.util
import sys
import hashlib
import os
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('_dev090_guard_creator_fixture',ROOT/'tests/test_dev074_creator_authority.py')
authority=importlib.util.module_from_spec(spec);sys.modules[spec.name]=authority;spec.loader.exec_module(authority)
queue=authority.queue
SETTING='CRQ_HISTORICAL_DISCOVERY_ENABLED'

@pytest.mark.parametrize('function',['enqueue_missing_migrated_tokens','enqueue_missing_funding_jobs'])
@pytest.mark.parametrize('value',['0','invalid',''])
def test_disabled_discovery_has_no_schema_reads_or_writes(monkeypatch,function,value):
 from src.core import creator_resolution_queue as q
 monkeypatch.setenv(SETTING,value)
 def forbidden(*a,**k):raise AssertionError('disabled discovery touched database')
 for name in ['initialize_schema','_read_db','_db']:monkeypatch.setattr(q,name,forbidden)
 assert getattr(q,function)('must-not-open',schema_ready=False)==0

@pytest.mark.parametrize('status',['skipped','complete','running'])
def test_existing_historical_rows_and_unqueued_tokens_untouched(queue,monkeypatch,status):
 q,db,boundary,get,*_=queue;monkeypatch.setenv(SETTING,'0')
 with boundary(db) as c:
  c.execute('UPDATE creator_resolution_queue SET status=?,attempts=3',(status,))
  c.execute("INSERT INTO token_analysis(mint,migrated_at,lifecycle_stage) VALUES('old-unqueued',1,'migrated')");c.commit()
 before=get()
 for _ in range(2):
  assert q.enqueue_missing_migrated_tokens(db,source='crq_worker',schema_ready=True)==0
  assert q.enqueue_missing_funding_jobs(db,source='crq_worker',schema_ready=True)==0
 assert get()==before
 with boundary(db) as c:assert c.execute("SELECT COUNT(*) FROM creator_resolution_queue WHERE mint='old-unqueued'").fetchone()[0]==0

def test_direct_migration_enqueue_and_duplicate_still_work(queue,monkeypatch):
 q,db,boundary,get,*_=queue;monkeypatch.setenv(SETTING,'0')
 with boundary(db) as c:
  c.execute("INSERT INTO token_analysis(mint,migrated_at,lifecycle_stage) VALUES('new-migration',200,'migrated')")
  q.enqueue_missing_creator(c,'new-migration',source='store_analysis',schema_ready=True)
  q.enqueue_missing_creator(c,'new-migration',source='store_analysis',schema_ready=True);c.commit()
  rows=c.execute("SELECT status,attempts FROM creator_resolution_queue WHERE mint='new-migration'").fetchall()
  assert len(rows)==1 and tuple(rows[0])==('pending',0)

@pytest.mark.parametrize('preexisting',[False,True])
def test_strict_completion_restart_with_discovery_disabled(queue,monkeypatch,preexisting):
 monkeypatch.setenv(SETTING,'0');authority.test_strict_commit_idempotency_restart(queue,monkeypatch,preexisting)

@pytest.mark.parametrize('point',['before','after'])
def test_creator_crash_contract_with_discovery_disabled(queue,monkeypatch,point):
 monkeypatch.setenv(SETTING,'0');authority.test_crash_atomicity(queue,monkeypatch,point)

def test_pumpportal_direct_birth_unchanged(monkeypatch):
 monkeypatch.setenv(SETTING,'0');authority.test_pumpportal_first_handler_unchanged_and_direct()

def test_exact_minimum_source_reversal():
 source=(ROOT/'src/core/creator_resolution_queue.py').read_text()
 guard='    # Prospective-only restoration disables discovery, never direct migration enqueue.\n    if os.environ.get("CRQ_HISTORICAL_DISCOVERY_ENABLED", "1") != "1":\n        return 0\n'
 assert source.count(guard)==2
 assert hashlib.sha256(source.replace(guard,'').encode()).hexdigest()==__import__('json').loads((ROOT/'docs/audits/dev090/historical-discovery-guard.json').read_text())['preimage_sha256']
