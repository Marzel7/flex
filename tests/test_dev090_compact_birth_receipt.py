"""Source-bound compact receipts: no providers, raw bytes, schema or worker."""
import ast,json,os,types,time
from pathlib import Path
import pytest
from src.core.pumpportal_birth_audit import PumpPortalBirthAudit,configured_birth_audit
from tests.test_dev090_handoff import encoded
from tests.test_dev090_compact_birth_recovery import f
M=encoded(109);C=encoded(99);S=encoded(115,64);T=time.time_ns()
def actor(root):return PumpPortalBirthAudit(enabled=False,path=str(root/'raw-forbidden'),compact_path=str(root/'compact'))
def record(a,creator=C,signature=S,received=T):return a.record(receive_utc_ns=received,receive_monotonic_ns=1,parser_utc_ns=received+1,mint=M,creator=creator,signature=signature,raw_payload={'SECRET_RAW':'must never persist'})
def read(a,ident):return json.loads((a.compact_path/(ident+'.json')).read_text())
def test_normal_receipt_and_confirmed_outcome(tmp_path):
 a=actor(tmp_path);i=record(a);r=read(a,i)
 assert (r['mint'],r['supplied_creator'],r['supplied_birth_signature'],r['receipt_utc_ns'])==(M,C,S,T)
 assert r['source']=='PUMPPORTAL_NEW_TOKEN' and r['persistence_outcome']=='UNCONFIRMED'
 assert a.record_outcome(i,outcome='COMMITTED',canonical=(C,C,S));r=read(a,i);assert r['accepted_at_utc_ns']>=T and r['persistence_outcome']=='COMMITTED'
 assert 'SECRET_RAW' not in json.dumps(r) and not (tmp_path/'raw-forbidden').exists() and not a._started
@pytest.mark.parametrize('field',['creator','signature'])
def test_incomplete_birth_has_original_receipt_without_false_acceptance(tmp_path,field):
 a=actor(tmp_path);kw={field:None};i=record(a,**kw);r=read(a,i);assert r['validation_result']=='MISSING_'+('CREATOR' if field=='creator' else 'SIGNATURE');assert 'accepted_at_utc_ns' not in r
 assert a.record_outcome(i,outcome='INCOMPLETE');assert read(a,i)['persistence_outcome']=='INCOMPLETE'
def test_duplicate_restart_preserves_original_time_and_receipt_identity(tmp_path):
 a=actor(tmp_path);i=record(a);b=actor(tmp_path);assert record(b,received=T+100)==i;assert read(b,i)['receipt_utc_ns']==T;assert len(list(b.compact_path.iterdir()))==1
@pytest.mark.parametrize('before_birth',[True,False])
def test_handoff_marker_is_separate_and_first_boundary_immutable(tmp_path,before_birth):
 a=actor(tmp_path)
 if before_birth:assert a.note_handoff(M,S,at_utc_ns=T-1)
 i=record(a);a.record_outcome(i,outcome='COMMITTED',canonical=(C,C,S))
 if not before_birth:assert a.note_handoff(M,S,at_utc_ns=time.time_ns()+1)
 assert a.note_handoff(M,S,at_utc_ns=T+999);markers=[json.loads(p.read_text()) for p in a.compact_path.glob('migration-*.json')];assert len(markers)==1 and markers[0]['boundary']=='BEFORE_OPS_SUBMIT'
 assert markers[0]['handoff_utc_ns']==T-1 if before_birth else markers[0]['handoff_utc_ns']>T
 assert read(a,i)['source']=='PUMPPORTAL_NEW_TOKEN'
def test_failed_persistence_cannot_become_success(tmp_path):
 a=actor(tmp_path);i=record(a);assert a.record_outcome(i,outcome='RETRY_PENDING');assert read(a,i)['persistence_outcome']=='RETRY_PENDING';assert not a.record_outcome(i,outcome='COMMITTED');assert 'accepted_at_utc_ns' not in read(a,i)
 assert a.record_outcome(i,outcome='UNRECOVERABLE');assert read(a,i)['persistence_outcome']=='UNRECOVERABLE'
def test_rpc_conflict_preserves_supplied_creator_and_signature(tmp_path):
 a=actor(tmp_path);i=record(a);assert a.record_outcome(i,outcome='COMMITTED',canonical=(encoded(98),encoded(98),S));r=read(a,i);assert r['persistence_outcome']=='COMMITTED_CONFLICT' and r['canonical_conflict'] and r['supplied_creator']==C and r['supplied_birth_signature']==S and 'accepted_at_utc_ns' not in r
@pytest.mark.parametrize('crash',['before-receipt-commit','after-receipt-commit'])
def test_crash_and_restart(tmp_path,monkeypatch,crash):
 a=actor(tmp_path);original=os.replace
 if crash=='before-receipt-commit':
  monkeypatch.setattr(os,'replace',lambda *a:(_ for _ in ()).throw(OSError('fixture interrupted rename')));assert record(a) is None;assert not list(a.compact_path.glob('*.json'));monkeypatch.setattr(os,'replace',original)
 else:assert record(a)
 b=actor(tmp_path);i=record(b,received=T+100);assert read(b,i)['receipt_utc_ns']==T and read(b,i)['persistence_outcome']=='UNCONFIRMED';assert len(list(b.compact_path.glob('*.json')))==1
 assert all(p.stat().st_size<=4096 for p in b.compact_path.iterdir())
def test_capacity_exhaustion_is_explicit_without_deletion_or_raw_fallback(tmp_path,monkeypatch):
 a=actor(tmp_path);monkeypatch.setattr(a,'MAX_RECEIPT_FILES',2);i=record(a);assert a.note_handoff(M,S);assert record(a,creator=encoded(98)) is None;assert a.last_receipt_error=='COMPACT_RECEIPT_UNAVAILABLE';assert len(list(a.compact_path.iterdir()))==2
 assert a.record_outcome(i,outcome='COMMITTED',canonical=(C,C,S));assert len(list(a.compact_path.iterdir()))==2
 assert not a._started and not (tmp_path/'raw-forbidden').exists()
def test_oversized_or_unsafe_fields_fail_closed(tmp_path):
 a=actor(tmp_path);assert record(a,creator='x'*45) is None;assert not a.compact_path.exists()
def test_unknown_raw_fields_in_interrupted_file_are_rejected(tmp_path):
 a=actor(tmp_path);i=record(a);p=a.compact_path/(i+'.json');r=read(a,i);r['raw_payload']={'secret':'do not copy'};p.write_text(json.dumps(r));assert record(a) is None

def wire(f,monkeypatch):
 import tests.test_dev090_compact_birth_recovery as old
 monkeypatch.setattr(old,'M',M);monkeypatch.setattr(old,'C',C);monkeypatch.setattr(old,'S',S);monkeypatch.setattr(old,'DATA',{**old.DATA,'mint':M,'traderPublicKey':C})
 monkeypatch.setenv('PUMPPORTAL_COMPACT_BIRTH_RECEIPTS_ENABLED','1');monkeypatch.setenv('PUMPPORTAL_COMPACT_BIRTH_RECEIPTS_PATH',str(f.tmp/'compact'))
 source=Path(__file__).resolve().parents[1]/'src/core/pumpfun_curve_listener.py';n=next(n for n in ast.walk(ast.parse(source.read_text())) if isinstance(n,ast.AsyncFunctionDef) and n.name=='_record_pp_birth_outcome');exec(compile(ast.Module(body=[n],type_ignores=[]),str(source),'exec'),f.ns)
 make=f.actor
 def actor():
  a=make();a._record_pp_birth_outcome=types.MethodType(f.ns['_record_pp_birth_outcome'],a);return a
 return actor

def test_actual_normal_birth_receipt_and_core_commit(f,monkeypatch):
 make=wire(f,monkeypatch);a=make();f.produce(a);rows=list((f.tmp/'compact').glob('*.json'));assert len(rows)==1;r=json.loads(rows[0].read_text());assert r['persistence_outcome']=='COMMITTED' and r['validation_result']=='IDENTITIES_VALID_UNVERIFIED_CHAIN' and r['supplied_creator']==C and r['supplied_birth_signature']==S

def test_actual_retry_preserves_original_receipt_and_requires_core_success(f,monkeypatch):
 make=wire(f,monkeypatch);a=make();a._insert_bonding_curve_token=f.fail;f.produce(a);p=next((f.tmp/'compact').glob('*.json'));assert json.loads(p.read_text())['persistence_outcome']=='RETRY_PENDING';assert f.rows()[0][3]==0
 b=make();f.consume(b);r=json.loads(p.read_text());assert r['persistence_outcome']=='COMMITTED' and r['receipt_utc_ns']==old_time(f) and f.rows()[0][3]==1

def old_time(f):
 import tests.test_dev090_compact_birth_recovery as old
 return old.T

def test_actual_failed_retry_never_records_commit(f,monkeypatch):
 make=wire(f,monkeypatch);a=make();a._insert_bonding_curve_token=f.fail;f.produce(a);b=make();b._insert_bonding_curve_token=f.fail;f.consume(b);r=json.loads(next((f.tmp/'compact').glob('*.json')).read_text());assert r['persistence_outcome']=='RETRY_PENDING' and 'accepted_at_utc_ns' not in r and f.rows()[0][3]==0

def test_config_disallows_raw_and_requires_explicit_compact_authority(tmp_path,monkeypatch):
 monkeypatch.setenv('PUMPPORTAL_COMPACT_BIRTH_RECEIPTS_ENABLED','1');monkeypatch.delenv('PUMPPORTAL_COMPACT_BIRTH_RECEIPTS_PATH',raising=False)
 with pytest.raises(ValueError):configured_birth_audit()
 monkeypatch.setenv('PUMPPORTAL_COMPACT_BIRTH_RECEIPTS_PATH',str(tmp_path/'compact'));monkeypatch.setenv('PUMPPORTAL_CANONICAL_BIRTH_RETENTION_ENABLED','1')
 with pytest.raises(ValueError):configured_birth_audit()


def test_future_cohort_denominator_provenance_and_rpc_are_independent(tmp_path):
 from src.core.pumpportal_birth_audit import summarize_birth_coverage
 from src.ops.walkback_create_handoff import Proof
 a=actor(tmp_path);i=record(a);a.record_outcome(i,outcome='COMMITTED',canonical=(C,C,S));r=read(a,i);a.note_handoff(M,S,at_utc_ns=r['accepted_at_utc_ns']+1);markers=[json.loads(p.read_text()) for p in a.compact_path.glob('migration-*.json')]
 cohort=[{'mint':M},{'mint':M},{'mint':encoded(108)}];rpc=Proof(M,C,S,'STRICT_RPC','DEV074_STRICT_CREATE',S,100)
 result=summarize_birth_coverage(cohort,[r,r],markers,cohort_complete=True,rpc_proofs=[rpc]);assert result['total_unique_migrations']==2 and result['pp_birth_receipt_retained']==1 and result['pp_birth_qualified_at_migration']==1 and result['qualified_pp_birth_coverage_percent']==50 and result['rpc_create_recovered']==1 and result['pp_birth_provenance_unknown']==1
 assert summarize_birth_coverage(cohort,[r],markers)['qualified_pp_birth_coverage_percent']=='UNKNOWN'
 assert summarize_birth_coverage([],[],[],cohort_complete=True)['qualified_pp_birth_coverage_percent']=='UNKNOWN'

def test_late_or_conflicting_receipt_not_counted_as_pre_handoff_qualified(tmp_path):
 from src.core.pumpportal_birth_audit import summarize_birth_coverage
 a=actor(tmp_path);a.note_handoff(M,S,at_utc_ns=T-1);i=record(a);a.record_outcome(i,outcome='COMMITTED',canonical=(C,C,S));r=read(a,i);markers=[json.loads(p.read_text()) for p in a.compact_path.glob('migration-*.json')];result=summarize_birth_coverage([{'mint':M}],[r],markers,cohort_complete=True);assert result['pp_birth_qualified_at_migration']==0 and result['pp_birth_receipt_retained']==1
 a.record_outcome(i,outcome='COMMITTED',canonical=(encoded(98),encoded(98),S));r=read(a,i);markers[0]['handoff_utc_ns']=time.time_ns()+1;assert summarize_birth_coverage([{'mint':M}],[r],markers,cohort_complete=True)['pp_birth_qualified_at_migration']==0


def test_actual_both_persistence_paths_fail_explicitly(f,monkeypatch):
 make=wire(f,monkeypatch);a=make();a._insert_bonding_curve_token=f.fail
 def unavailable(*a,**k):raise RuntimeError('fixture retry authority unavailable')
 f.ns['_compact_birth_retry_storage_check']=unavailable;f.produce(a);r=json.loads(next((f.tmp/'compact').glob('*.json')).read_text());assert r['persistence_outcome']=='UNRECOVERABLE' and 'accepted_at_utc_ns' not in r and not f.rows();assert any('UNRECOVERABLE_BIRTH_RETRY' in x for x in f.logs)


@pytest.mark.parametrize('missing',['creator','signature'])
def test_actual_incomplete_normal_birth_retains_supplied_fields(f,monkeypatch,missing):
 import tests.test_dev090_compact_birth_recovery as old
 make=wire(f,monkeypatch)
 if missing=='creator':monkeypatch.setattr(old,'DATA',{**old.DATA,'traderPublicKey':None})
 else:monkeypatch.setattr(old,'S',None)
 a=make();f.produce(a);r=json.loads(next((f.tmp/'compact').glob('*.json')).read_text());assert r['validation_result']=='MISSING_'+missing.upper() and 'accepted_at_utc_ns' not in r
 if missing=='signature':assert r['persistence_outcome']=='INCOMPLETE' and r['supplied_birth_signature'] is None
 else:assert r['supplied_creator'] is None

def test_actual_receipt_storage_failure_is_unknown_not_birth_success_provenance(f,monkeypatch):
 make=wire(f,monkeypatch);a=make()
 def unavailable(*args,**kw):raise OSError('fixture receipt storage unavailable')
 monkeypatch.setattr(a._eb_birth_audit,'_store_receipt',unavailable);f.produce(a)
 with __import__('sqlite3').connect(f.db) as c:assert c.execute('select create_tx_signature from token_analysis where mint=?',(M,)).fetchone()[0]==S
 assert any('COMPACT_BIRTH_PROVENANCE_UNAVAILABLE' in x for x in f.logs) and not list((f.tmp/'compact').glob('*.json'))

def test_compact_collector_starts_no_background_thread(tmp_path,monkeypatch):
 import threading
 monkeypatch.setattr(threading.Thread,'start',lambda *a:(_ for _ in ()).throw(AssertionError('new background worker')))
 a=actor(tmp_path);assert record(a) and not a._started


def test_future_coverage_rejects_incomplete_or_unbounded_inputs():
 from src.core.pumpportal_birth_audit import summarize_birth_coverage
 with pytest.raises(ValueError,match='COVERAGE_INPUT_BUDGET'):summarize_birth_coverage(({'mint':M} for _ in range(257)),[],[],cohort_complete=True)


def test_compact_cannot_reuse_historical_raw_authority(tmp_path):
 with pytest.raises(ValueError,match='COMPACT_AUTHORITY_MUST_DIFFER_FROM_RAW'):PumpPortalBirthAudit(enabled=False,path=str(tmp_path),compact_path=str(tmp_path))


def test_delivery_and_ack_are_separate_canonical_dimensions():
 from src.core.pumpportal_birth_audit import summarize_birth_coverage
 delivery={'mint':M,'creator':C,'signature':S,'state':'PENDING'};job={'mint':M,'creator':C,'create_anchor_signature':S,'create_anchor_source':'CORE_QUALIFIED_CREATE'}
 r=summarize_birth_coverage([{'mint':M}],[],[],cohort_complete=True,walkback=[job],deliveries=[delivery]);assert r['late_create_handoff_delivered']==1 and r['late_create_handoff_acknowledged']==0
 delivery['state']='ACK';r=summarize_birth_coverage([{'mint':M}],[],[],cohort_complete=True,walkback=[job],deliveries=[delivery]);assert r['late_create_handoff_acknowledged']==1
 delivery['state']='PENDING';job['create_anchor_signature']=None;assert summarize_birth_coverage([{'mint':M}],[],[],cohort_complete=True,walkback=[job],deliveries=[delivery])['late_create_handoff_delivered']==0


def test_restart_qualification_uses_persisted_original_pp_receipt(tmp_path):
 a=actor(tmp_path);i=record(a);assert a.accepted_birth(M,C,S) is None;a.record_outcome(i,outcome='COMMITTED',canonical=(C,C,S));b=actor(tmp_path);assert b.accepted_birth(M,C,S)==(C,S);assert b.accepted_birth(M,encoded(98),S) is None
