"""Critical acceptance regression: an optional lookup must not precede admission.

Qualification of durable-before-lookup and dispatch refusal.
No production DB, provider, source fence or Supervisor action occurs here.
"""
import ast,importlib.util,pathlib,resource
ROOT=pathlib.Path(__file__).resolve().parents[2]
s=importlib.util.spec_from_file_location('fenced_admission_journal',ROOT/'runtime/listener/src/core/migration_signature_journal.py');journal=importlib.util.module_from_spec(s);s.loader.exec_module(journal)

def test_signature_is_durable_when_completion_lookup_rejects_transfer(tmp_path):
 resource.setrlimit(resource.RLIMIT_FSIZE,(16_000_000,16_000_000))
 def fence():raise RuntimeError('PAIR_TRANSFER_ONLY')
 scope={'_validate_active_pair':fence,'DB_PATH':'DISPOSABLE_NEVER_OPENED'}
 tree=ast.parse((ROOT/'runtime/listener/src/core/pumpfun_curve_listener.py').read_text());fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_migration_completed_mint')
 exec(compile(ast.Module(body=[fn],type_ignores=[]),'actual-completion-lookup','exec'),scope)
 work=journal.MigrationJournal(tmp_path/'migration.jsonl',resolve_completed=scope['_migration_completed_mint']);work.initialize()
 try:work.admit('synthetic-transfer-signature','PUMPSWAP_MIGRATION_ACCOUNT',1700000000)
 except journal.CompletionLookupDeferred as error:assert error.admission_durable
 assert [r['signature'] for r in work.snapshot()]==['synthetic-transfer-signature'], 'Required signature was not durably admitted before completion lookup failed'


def test_lookup_observes_durable_input_and_restart(tmp_path):
 path=tmp_path/'work.jsonl'
 def unavailable(row):
  assert journal.MigrationJournal(path).snapshot()[0]['signature']==row['signature']
  raise OSError('unavailable')
 work=journal.MigrationJournal(path,resolve_completed=unavailable);work.initialize()
 import pytest
 with pytest.raises(journal.CompletionLookupDeferred):work.admit('sig','PUMPSWAP',1)
 rows=journal.MigrationJournal(path).pending();assert len(rows)==1 and rows[0]['state']=='PENDING' and rows[0]['attempts']==0
 resumed=journal.MigrationJournal(path,resolve_completed=lambda r:'mint')
 assert resumed.admit('sig','PUMPSWAP',2)['mint']=='mint'
 assert len(resumed.snapshot())==1 and resumed.snapshot()[0]['received_at']==1

def test_failed_publication_never_calls_lookup(tmp_path,monkeypatch):
 work=journal.MigrationJournal(tmp_path/'work.jsonl',resolve_completed=lambda r:(_ for _ in ()).throw(AssertionError('LOOKUP_BEFORE_PUBLICATION')));work.initialize()
 monkeypatch.setattr(work,'_publish',lambda rows:(_ for _ in ()).throw(OSError('disk refusal')))
 import pytest
 with pytest.raises(OSError):work.admit('sig','PUMPSWAP',1)
 assert work.snapshot()==[]

def test_fenced_scheduler_keeps_durable_pending_without_dispatch(tmp_path):
 import types,time
 work=journal.MigrationJournal(tmp_path/'work.jsonl',resolve_completed=lambda r:(_ for _ in ()).throw(RuntimeError('PAIR_TRANSFER_ONLY')));work.initialize()
 scope={'_signature_migration_journal':lambda:work,'time':time,'_validate_active_pair':lambda:(_ for _ in ()).throw(AssertionError('DISPATCH_AFTER_DEFERRED'))}
 tree=ast.parse((ROOT/'runtime/listener/src/core/pumpfun_curve_listener.py').read_text());fn=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='_schedule_journal_migration');exec(compile(ast.Module(body=[fn],type_ignores=[]),'actual-scheduler','exec'),scope)
 assert scope['_schedule_journal_migration'](types.SimpleNamespace(),'sig',[]) is None
 assert work.pending()[0]['signature']=='sig' and work.pending()[0]['attempts']==0


def test_birth_frame_stays_admitted_during_fence(tmp_path):
 import asyncio,json,sys,types,importlib.util,time
 # Use the actual birth journal and actual frame interface; no transport.
 sys.modules['src.core.migration_signature_journal']=journal
 spec=importlib.util.spec_from_file_location('birth_order_test',ROOT/'runtime/listener/src/core/birth_admission_journal.py');b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
 work=b.BirthJournal(tmp_path/'birth.jsonl');work.initialize()
 payload={'_birth_retry_schema':1,'mint':'mint','signature':'sig','traderPublicKey':'creator','bondingCurveKey':None,'symbol':None,'name':None,'receive_utc_ns':1,'provenance':'PUMPPORTAL_NEW_TOKEN'}
 scope={'json':json,'time':time,'asyncio':asyncio,'_birth_admission_journal':lambda:work,'_compact_pumpportal_birth_retry':lambda *a,**k:json.dumps(payload)}
 tree=ast.parse((ROOT/'runtime/listener/src/core/pumpfun_curve_listener.py').read_text());fn=next(n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name=='_handle_pumpportal_create_frame');exec(compile(ast.Module(body=[fn],type_ignores=[]),'actual-birth-frame','exec'),scope)
 async def persist(row):
  assert work.pending()[0]['signature']=='sig'
  raise RuntimeError('PAIR_TRANSFER_ONLY')
 async def forbidden(*a,**k):raise AssertionError('ACK_DURING_FENCE')
 obj=types.SimpleNamespace(_eb_birth_audit=types.SimpleNamespace(record=lambda **k:'receipt',compact_path=tmp_path),_pumpfun_delivery_latency=types.SimpleNamespace(observe_pumpportal=lambda **k:None),_byzantine_birth_signal=None,_portal_vsol={},_persist_durable_birth=persist,_record_pp_birth_outcome=forbidden)
 asyncio.run(scope['_handle_pumpportal_create_frame'](obj,{'traderPublicKey':'creator'},signature='sig',mint='mint',receive_utc_ns=1,receive_monotonic_ns=1,tracked_trade_mints=set(),migration_sol_threshold=1,ws=None))
 assert work.pending()[0]['state']=='RESOLVED' and work.pending()[0]['ack'] is None


def test_receiver_retains_input_when_old_pair_is_no_longer_owner(tmp_path):
 import types,time
 work=journal.MigrationJournal(tmp_path/'work.jsonl');work.initialize()
 scope={'_signature_migration_journal':lambda:work,'time':time,'_validate_active_pair':lambda:(_ for _ in ()).throw(RuntimeError('PAIR_AUTHORITY_MANIFEST'))}
 tree=ast.parse((ROOT/'runtime/listener/src/core/pumpfun_curve_listener.py').read_text());fn=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='_schedule_journal_migration');exec(compile(ast.Module(body=[fn],type_ignores=[]),'receiver','exec'),scope)
 assert scope['_schedule_journal_migration'](types.SimpleNamespace(),'signature',[]) is None
 assert work.pending()[0]['attempts']==0 and work.pending()[0]['ack'] is None
