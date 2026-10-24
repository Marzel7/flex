import importlib.util,json,os,pathlib,resource,sqlite3,socket
import pytest
ROOT=pathlib.Path(__file__).resolve().parents[2]
s=importlib.util.spec_from_file_location('quarantine_transfer',ROOT/'runtime/listener/src/ops/dev004_quarantine_transfer.py');q=importlib.util.module_from_spec(s);s.loader.exec_module(q)
DDL=next(x['sql'] for x in json.load(open(ROOT/'docs/dev/lightweight_dev004_first_v2/production_profile_bootstrap/accepted_fixture_schema.json'))['ops'] if x['name']=='wt_lineage_quarantine')
@pytest.fixture(autouse=True)
def safety(monkeypatch):
 resource.setrlimit(resource.RLIMIT_FSIZE,(16_000_000,16_000_000))
 monkeypatch.setattr(socket.socket,'connect',lambda *a,**k:(_ for _ in ()).throw(AssertionError('NETWORK_FORBIDDEN')))
@pytest.fixture
def pair(tmp_path):
 a=tmp_path/'source.sqlite';b=tmp_path/'target.sqlite';cp=tmp_path/'continuation.json'
 for p in [a,b]:
  with sqlite3.connect(p) as c:c.execute(DDL)
 return a,b,cp

def seed(p,n,start=0):
 with sqlite3.connect(p) as c:
  for i in range(start,start+n):
   c.execute('insert into wt_lineage_quarantine values (?,?,?,?,?,?,?,?,?,?,?,?)',(f'{i:064x}','retained_source',i,'wallet'+str(i),'parent'+str(i),'sig'+str(i),'UNQUALIFIED','COMPETING_PARENT','RETAINED',json.dumps({'negative':i,'retry':i%3}),1,1700000000+i))

def init(pair):return q.initialize(*pair,generation='frozen-fixture')
def finish(pair):
 steps=0
 while True:
  result=q.step(pair[2]);steps+=1
  if result['phase']=='COMPLETE':return result,steps
  assert steps<2000

def test_over_12000_exact_complete_and_duplicate_replay(pair):
 seed(pair[0],13017)
 # Preexisting exact selected-seed record must be deduplicated, never duplicated.
 with sqlite3.connect(pair[0]) as src,sqlite3.connect(pair[1]) as dst:
  row=src.execute('select * from wt_lineage_quarantine where source_row_id=7').fetchone();dst.execute('insert into wt_lineage_quarantine values('+','.join('?' for _ in row)+')',row)
 init(pair);result,steps=finish(pair)
 assert result['rows']==result['verified_rows']==13017 and result['digest']==result['verified_digest']
 assert result['batches']>100 and steps>200
 with sqlite3.connect(pair[0]) as a,sqlite3.connect(pair[1]) as b:
  assert a.execute('select * from wt_lineage_quarantine order by quarantine_id').fetchall()==b.execute('select * from wt_lineage_quarantine order by quarantine_id').fetchall()
  assert not b.execute('pragma foreign_key_check').fetchall()
 assert q.step(pair[2])==result

def test_restart_midway(pair):
 seed(pair[0],600);init(pair);first=q.step(pair[2]);assert first['rows']==128
 assert q.initialize(*pair,generation='frozen-fixture')==first
 r,_=finish(pair);assert r['rows']==600

def test_crash_after_batch_commit(pair):
 seed(pair[0],301);init(pair)
 def crash():raise RuntimeError('after commit')
 with pytest.raises(RuntimeError):q.step(pair[2],after_commit=crash)
 assert q.load(pair[2])['rows']==0
 with sqlite3.connect(pair[1]) as c:assert c.execute('select count(*) from wt_lineage_quarantine').fetchone()[0]==128
 r,_=finish(pair);assert r['rows']==r['verified_rows']==301

def test_missing_source_record_fails_closed(pair):
 seed(pair[0],260);init(pair);q.step(pair[2])
 with sqlite3.connect(pair[0]) as c:c.execute('delete from wt_lineage_quarantine where source_row_id=200')
 with pytest.raises(q.TransferHold,match='SOURCE_RECORD_OR_GENERATION_CHANGED'):q.step(pair[2])
 assert q.load(pair[2])['phase']!='COMPLETE'

def test_conflicting_target_preserved(pair):
 seed(pair[0],200);seed(pair[1],1)
 with sqlite3.connect(pair[1]) as c:c.execute("update wt_lineage_quarantine set quarantine_reason='DIFFERENT'")
 init(pair)
 with pytest.raises(q.TransferHold,match='CONFLICTING_TARGET'):q.step(pair[2])
 with sqlite3.connect(pair[1]) as c:assert c.execute('select quarantine_reason from wt_lineage_quarantine').fetchone()[0]=='DIFFERENT';assert c.execute('select count(*) from wt_lineage_quarantine').fetchone()[0]==1

def test_extra_target_missing_from_source_refuses_complete(pair):
 seed(pair[0],3);seed(pair[1],1,start=999);init(pair)
 with pytest.raises(q.TransferHold,match='MISSING_SOURCE_RECORD'):finish(pair)

def test_storage_refusal_no_cursor_advance(pair,monkeypatch):
 seed(pair[0],300);init(pair);old=pair[2].read_bytes()
 monkeypatch.setattr(q,'MAX_MAIN_BYTES',4096)
 with pytest.raises(q.TransferHold,match='DATABASE_CAPACITY'):q.step(pair[2])
 assert pair[2].read_bytes()==old

def test_disk_floor_refusal(pair,monkeypatch):
 seed(pair[0],1);init(pair);old=pair[2].read_bytes()
 monkeypatch.setattr(os,'statvfs',lambda p:type('V',(),{'f_bavail':0,'f_frsize':4096})())
 with pytest.raises(q.TransferHold,match='DISK_FLOOR'):q.step(pair[2])
 assert pair[2].read_bytes()==old

def test_row_capacity_refusal(pair):
 seed(pair[0],1)
 with sqlite3.connect(pair[0]) as c:c.execute('update wt_lineage_quarantine set evidence_json=?',('x'*70000,))
 init(pair)
 with pytest.raises(q.TransferHold,match='ROW_CAPACITY'):q.step(pair[2])
 assert q.load(pair[2])['rows']==0

def test_fk_integrity_refusal(pair):
 seed(pair[0],1)
 with sqlite3.connect(pair[1]) as c:
  c.execute('create table parent(id integer primary key)');c.execute('create table child(id integer references parent(id))');c.execute('insert into child values(99)')
 init(pair)
 with pytest.raises(q.TransferHold,match='FOREIGN_KEY_INTEGRITY'):finish(pair)

def test_generation_rebind_refused(pair):
 seed(pair[0],2);init(pair)
 with pytest.raises(q.TransferHold,match='CHECKPOINT_GENERATION'):q.initialize(*pair,generation='other-generation')

def test_interrupted_checkpoint_fails_closed(pair,monkeypatch):
 seed(pair[0],200);init(pair);old=pair[2].read_bytes();real=os.replace
 monkeypatch.setattr(os,'replace',lambda *a:(_ for _ in ()).throw(OSError('checkpoint interruption')))
 with pytest.raises(OSError):q.step(pair[2])
 assert pair[2].read_bytes()==old
 monkeypatch.setattr(os,'replace',real)
 with pytest.raises(q.TransferHold,match='INTERRUPTED_CHECKPOINT'):q.step(pair[2])

def test_production_requires_retirement_and_owner(pair):
 seed(pair[0],1)
 with pytest.raises(q.TransferHold,match='TRANSFER_OWNER_REQUIRED'):q.initialize(*pair,generation='g',production=True)
 with pytest.raises(q.TransferHold,match='SOURCE_RETIREMENT_REQUIRED'):q.initialize(*pair,generation='g',production=True,authorize=lambda:'TRANSFER')

def test_target_identity_replacement_refused(pair):
 seed(pair[0],2);init(pair);pair[1].rename(pair[1].with_suffix('.old'))
 with sqlite3.connect(pair[1]) as c:c.execute(DDL)
 with pytest.raises(q.TransferHold,match='TARGET_IDENTITY_CHANGED'):q.step(pair[2])


def test_checkpoint_owner_excludes_parallel_step(pair):
 seed(pair[0],1);init(pair)
 with q.checkpoint_owner(pair[2]):
  with pytest.raises(q.TransferHold,match='CHECKPOINT_OWNER_BUSY'):q.step(pair[2])
 assert q.load(pair[2])['rows']==0

def test_production_rejects_false_authority(pair):
 seed(pair[0],1)
 with pytest.raises(q.TransferHold,match='TRANSFER_OWNER_REQUIRED'):q.initialize(*pair,generation='g',production=True,authorize=lambda:False)
