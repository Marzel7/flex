import importlib.util,sqlite3,json,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'runtime/listener'))
from src.ops import dev004_operational_continuation as m

def fixture(tmp_path,composite=False):
 p=tmp_path.resolve();source=p/'source.sqlite';target=p/'target.sqlite';cp=p/'progress.json'
 s=sqlite3.connect(source);s.row_factory=sqlite3.Row;t=sqlite3.connect(target);t.row_factory=sqlite3.Row
 ddl='create table work(a text,b integer,value text,primary key(a,b))' if composite else 'create table work(id integer primary key,value text)'
 s.execute(ddl);t.execute(ddl)
 if composite:s.executemany('insert into work values(?,?,?)',[(str(x//100).zfill(5),x%100,str(x)) for x in range(13017)])
 else:s.executemany('insert into work values(?,?)',[(x,str(x)) for x in range(13017)])
 s.commit();return source,cp,s,t

def run(source,cp,s,t,*,crash=False,accept=lambda row:True):
 commits=0
 def commit():
  nonlocal commits
  t.commit();commits+=1
  if crash and commits==2:raise RuntimeError('crash after commit before checkpoint')
 for row in m.pages(s,source,cp,'work',commit=commit,accept=accept):
  cols=list(row);old=t.execute('select * from work where '+ ' and '.join(k+'=?' for k in cols if k!='value'),[row[k] for k in cols if k!='value']).fetchone()
  if old is not None and dict(old)!=row:raise RuntimeError('conflicting target')
  t.execute('insert or ignore into work values('+','.join('?' for _ in cols)+')',list(row.values()))
 return commits

@pytest.mark.parametrize('composite',[False,True])
def test_more_than_12000_restart_exact(tmp_path,composite):
 source,cp,s,t=fixture(tmp_path,composite)
 with pytest.raises(RuntimeError,match='crash'):run(source,cp,s,t,crash=True)
 assert t.execute('select count(*) from work').fetchone()[0]==256
 run(source,cp,s,t);state=json.loads(cp.read_text());assert state['rows']==13017 and state['complete']
 assert [tuple(x) for x in s.execute('select * from work order by 1,2')]==[tuple(x) for x in t.execute('select * from work order by 1,2')]
 assert run(source,cp,s,t)==0

def test_source_mutation_fails_closed(tmp_path):
 source,cp,s,t=fixture(tmp_path)
 with pytest.raises(RuntimeError):run(source,cp,s,t,crash=True)
 s.execute('update work set value="changed" where id=0');s.commit()
 with pytest.raises(m.q.TransferHold,match='SOURCE_CHANGED'):run(source,cp,s,t)

def test_conflicting_target_not_overwritten(tmp_path):
 source,cp,s,t=fixture(tmp_path);t.execute('insert into work values(0,"conflict")');t.commit()
 with pytest.raises(RuntimeError,match='conflicting'):run(source,cp,s,t)
 assert t.execute('select value from work where id=0').fetchone()[0]=='conflict'

def test_complete_filtered_population(tmp_path):
 source,cp,s,t=fixture(tmp_path);run(source,cp,s,t,accept=lambda r:r['id']%2==0)
 state=json.loads(cp.read_text());assert state['rows']==13017 and state['accepted']==6509
 assert t.execute('select count(*) from work').fetchone()[0]==6509

def test_interrupted_checkpoint_fail_closed(tmp_path):
 source,cp,s,t=fixture(tmp_path);cp.with_name(cp.name+'.tmp').write_text('partial')
 with pytest.raises(FileExistsError):run(source,cp,s,t)

def test_page_bytes_refusal(tmp_path,monkeypatch):
 source,cp,s,t=fixture(tmp_path);monkeypatch.setattr(m,'MAX_PAGE_BYTES',1)
 with pytest.raises(m.q.TransferHold,match='PAGE_BYTE_LIMIT'):run(source,cp,s,t)
 assert t.execute('select count(*) from work').fetchone()[0]==0


def test_mixed_direction_existing_index(tmp_path):
 source,cp,s,t=fixture(tmp_path);s.execute('update work set value=cast(id%11 as text)');s.execute('create index existing_desc on work(value desc)');s.commit();observed=[]
 for row in m.pages(s,source,cp,'work',commit=lambda:None,order=('-value','rowid')):observed.append(row['id'])
 assert observed==[x[0] for x in s.execute('select id from work order by value desc,rowid')]
 assert len(observed)==len(set(observed))==13017
