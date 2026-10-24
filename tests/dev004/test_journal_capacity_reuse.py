"""Compact provider-free completion reclamation and duplicate oracle."""
import ast,importlib.util,json,os,pathlib,sqlite3,sys,types
import pytest
ROOT=pathlib.Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('capacity_journal',ROOT/'runtime/listener/src/core/migration_signature_journal.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
# Birth imports the same exact journal class, without importing runtime services.
@pytest.fixture(params=['birth','migration'])
def case(request,tmp_path,monkeypatch):
 for key in list(os.environ):
  if key.startswith('FLEX_DB_') or key=='DEV004_PAIRED_BINDING_REQUIRED':monkeypatch.delenv(key)
 monkeypatch.setattr(__import__('socket').socket,'connect',lambda *a,**k:(_ for _ in ()).throw(AssertionError('NETWORK_FORBIDDEN')))
 monkeypatch.setitem(sys.modules,'src.core.migration_signature_journal',m)
 if request.param=='birth':
  s=importlib.util.spec_from_file_location('capacity_birth',ROOT/'runtime/listener/src/core/birth_admission_journal.py');b=importlib.util.module_from_spec(s);s.loader.exec_module(b);cls=b.BirthJournal
 else:cls=m.MigrationJournal
 c=sqlite3.connect(tmp_path/'canonical.sqlite');c.execute('create table canonical(signature text primary key,mint text,creator text,handoff integer)');c.commit()
 class Case:
  kind=request.param
  db=c
  def verify(self,row):
   found=c.execute('select mint,creator,handoff from canonical where signature=?',(row['signature'],)).fetchone()
   return bool(found and found[0]==row['mint'] and found[2]==1 and (self.kind!='birth' or found[1]==row['payload']['traderPublicKey']))
  def resolve(self,row):
   x=c.execute('select mint from canonical where signature=?',(row['signature'],)).fetchone();return x[0] if x else None
  def reopen(self):self.j=cls(tmp_path/'work.jsonl',verify_completed=self.verify,resolve_completed=self.resolve)
  def admit(self,i,creator='creator',mint=None):
   sig='sig'+str(i)
   if self.kind=='birth':return self.j.admit_birth({'_birth_retry_schema':1,'mint':mint or 'mint'+str(i),'signature':sig,'traderPublicKey':creator,'bondingCurveKey':None,'symbol':None,'name':None,'receive_utc_ns':1000000000+i,'provenance':'PUMPPORTAL_NEW_TOKEN'})
   return self.j.admit(sig,'PUMPSWAP',1000000000+i,mint)
  def complete(self,i):
   r=self.admit(i)
   if self.kind=='migration':r=self.j.resolve(r['signature'],'mint'+str(i))
   c.execute('insert or ignore into canonical values (?,?,?,1)',(r['signature'],r['mint'],'creator'));c.commit()
   assert self.j.acknowledge(r['id'] if self.kind=='birth' else r['signature'],'binding',self.verify)
   return r
 x=Case();x.reopen();x.j.initialize();yield x;c.close()

def test_513_and_duplicate_after_reclamation(case):
 for i in range(512):case.complete(i)
 assert len(case.j.snapshot())==512
 case.reopen();r=case.admit(512);assert r['state'] in {'RESOLVED','PENDING'};assert len(case.j.snapshot())<=512
 replay=case.admit(0);assert replay['mint']=='mint0' and case.verify(replay)
 assert case.j.acknowledge(replay['id'] if case.kind=='birth' else replay['signature'],'binding',case.verify)
 assert not case.j.begin_attempt(replay['id'] if case.kind=='birth' else replay['signature'])

def test_thousands_bounded(case):
 largest=0
 for i in range(2500):
  case.complete(i)
  if i%128==0:case.reopen()
  largest=max(largest,case.j.path.stat().st_size)
 assert len(case.j.snapshot())<=512 and largest<m.MAX_JOURNAL_BYTES
 assert len(list(case.j.path.parent.glob('work*')))<=2
 assert case.verify(case.admit(0))

def test_protected_and_conflict_survive(case):
 case.admit(0)
 r=case.admit(1);key=r['id'] if case.kind=='birth' else r['signature'];case.j.quarantine(key,'PROTECTED')
 r=case.admit(2);key2=r['id'] if case.kind=='birth' else r['signature']
 for _ in range(4):case.j.begin_attempt(key2)
 case.complete(3)
 removed=case.j.reclaim_completed();assert len(removed)==1
 case.reopen();rows=case.j.snapshot();assert {r['state'] for r in rows}=={'QUARANTINED','RETRY_EXHAUSTED','RESOLVED' if case.kind=='birth' else 'PENDING'}
 if case.kind=='migration':
  r=case.admit(3,mint='conflicting-mint');assert r['state']=='QUARANTINED' and r['conflicting_mint']=='mint3'
 else:
  r=case.admit(3,creator='conflicting-creator');assert not case.verify(r)
 assert len(case.j.reclaim_completed())==0

def test_unverified_completion_not_removed(case):
 case.complete(0);before=case.j.path.read_bytes();assert case.j.reclaim_completed(lambda r:False)==[];assert case.j.path.read_bytes()==before

def test_crash_reclamation_and_explicit_recovery(case,monkeypatch):
 case.complete(0);case.admit(1);before=case.j.path.read_bytes();real=os.replace
 monkeypatch.setattr(os,'replace',lambda *a:(_ for _ in ()).throw(OSError('crash before rename')))
 with pytest.raises(OSError):case.j.reclaim_completed()
 assert case.j.path.read_bytes()==before and case.j.tmp.exists()
 monkeypatch.setattr(os,'replace',real);case.reopen()
 assert len(case.j.snapshot())==1 and case.j.snapshot()[0]['signature']=='sig1'
 assert case.verify(case.admit(0))

def test_crash_after_ack_commit(case):
 case.complete(0);case.reopen();assert case.j.snapshot()[0]['state']=='ACKED';assert len(case.j.reclaim_completed())==1;case.reopen();assert case.j.snapshot()==[]

def test_recovery_cannot_drop_pending(case):
 case.admit(0);case.j.tmp.write_text('')
 with pytest.raises(m.JournalHold,match='PROTECTED'):case.j.recover_reclamation()

def test_disk_floor_refusal(case,monkeypatch):
 case.complete(0);before=case.j.path.read_bytes();monkeypatch.setattr(os,'statvfs',lambda p:types.SimpleNamespace(f_bavail=0,f_frsize=4096))
 with pytest.raises(m.JournalHold,match='DISK_FLOOR'):case.j.reclaim_completed()
 assert case.j.path.read_bytes()==before

def test_short_write_refusal(case,monkeypatch):
 case.complete(0);before=case.j.path.read_bytes();case.admit(1);before=case.j.path.read_bytes();write=os.write
 monkeypatch.setattr(os,'write',lambda fd,data:write(fd,data[:max(0,len(data)//2)]))
 with pytest.raises(m.JournalHold,match='SHORT_WRITE'):case.j.reclaim_completed()
 assert case.j.path.read_bytes()==before

def test_pair_change_refusal(case,monkeypatch):
 case.complete(0);before=case.j.path.read_bytes();n=iter(['g1','g2']);monkeypatch.setattr(m,'_paired_ack_generation',lambda:next(n))
 with pytest.raises(m.JournalHold,match='PAIR_CHANGED'):case.j.reclaim_completed()
 assert case.j.path.read_bytes()==before

def test_runtime_migration_lookup_index_and_conflict(tmp_path,monkeypatch):
 code=ast.parse((ROOT/'runtime/listener/src/core/pumpfun_curve_listener.py').read_text());fn=next(x for x in code.body if isinstance(x,ast.FunctionDef) and x.name=='_migration_completed_mint');scope={'DB_PATH':str(tmp_path/'core.sqlite'),'_validate_active_pair':lambda:None};exec(compile(ast.Module(body=[fn],type_ignores=[]),'lookup','exec'),scope)
 monkeypatch.setitem(sys.modules,'src.core.migration_signature_journal',m)
 c=sqlite3.connect(scope['DB_PATH']);c.execute('create table token_analysis(mint text primary key,migration_tx text)');c.commit()
 with pytest.raises(m.JournalHold,match='INDEX_REQUIRED'):scope['_migration_completed_mint']({'signature':'s'})
 c.execute('create index idx_psi0a_token_analysis_migration_tx on token_analysis(migration_tx)');c.execute("insert into token_analysis values('mint','s')");c.commit();assert scope['_migration_completed_mint']({'signature':'s'})=='mint'
 c.execute("insert into token_analysis values('other','s')");c.commit()
 with pytest.raises(m.JournalHold,match='CONFLICTING'):scope['_migration_completed_mint']({'signature':'s'})
 c.close()


def test_kernel_file_limit_refuses_publication(case):
 import subprocess
 case.complete(0);case.admit(1);before=case.j.path.read_bytes()
 module=ROOT/'runtime/listener/src/core/migration_signature_journal.py'
 birth=ROOT/'runtime/listener/src/core/birth_admission_journal.py'
 code="""import importlib.util,sys,resource,signal,json
s=importlib.util.spec_from_file_location('src.core.migration_signature_journal',sys.argv[1]);m=importlib.util.module_from_spec(s);sys.modules[s.name]=m;s.loader.exec_module(m)
cls=m.MigrationJournal
if sys.argv[3]=='birth':
 s=importlib.util.spec_from_file_location('birth',sys.argv[2]);b=importlib.util.module_from_spec(s);s.loader.exec_module(b);cls=b.BirthJournal
j=cls(sys.argv[4],verify_completed=lambda r:True)
signal.signal(signal.SIGXFSZ,signal.SIG_IGN);resource.setrlimit(resource.RLIMIT_FSIZE,(64,64))
try:j.reclaim_completed()
except (OSError,m.JournalHold):print('FAIL_CLOSED')
else:raise AssertionError('FILE_LIMIT_NOT_ENFORCED')
"""
 child=subprocess.run([sys.executable,'-B','-c',code,str(module),str(birth),case.kind,str(case.j.path)],capture_output=True,text=True,timeout=10)
 assert child.returncode==0 and 'FAIL_CLOSED' in child.stdout,child.stderr
 assert case.j.path.read_bytes()==before and case.j.tmp.stat().st_size<=64


def test_missing_handoff_blocks_retirement(case):
 case.complete(0);case.db.execute("update canonical set handoff=0 where signature='sig0'");case.db.commit()
 before=case.j.path.read_bytes();assert case.j.reclaim_completed()==[];assert case.j.path.read_bytes()==before

def test_conflicting_ack_is_protected(case):
 case.complete(0)
 rows=case.j.snapshot()
 if case.kind=='birth':rows[0]['conflicting_payload']=dict(rows[0]['payload'],traderPublicKey='other')
 else:rows[0]['conflicting_mint']='other'
 case.j._publish(rows);before=case.j.path.read_bytes();assert case.j.reclaim_completed()==[];assert case.j.path.read_bytes()==before
