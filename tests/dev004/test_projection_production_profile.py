import importlib.util,sys,resource
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'runtime/listener'))
from src.core import paired_binding_guard as guard
p=Path(__file__).resolve().parents[2]/'runtime/walkback/src/ops/dev004_walkback_projection_recovery.py'
spec=importlib.util.spec_from_file_location('recovery_profile',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class Rows:
 def __init__(self,r):self.r=r
 def __iter__(self):return iter(self.r)
 def fetchone(self):return self.r[0]
class Store:
 def __init__(self,p):self.p=p;self.limit=None
 def execute(self,sql):
  if sql=='PRAGMA database_list':return Rows([(0,'main',str(self.p))])
  if sql=='PRAGMA page_size':return Rows([(4096,)])
  if sql=='PRAGMA page_count':return Rows([(35000,)])
  if sql=='PRAGMA max_page_count':return Rows([(117187,)])
  self.limit=int(sql.split('=')[1]);return Rows([])
def setup(tmp_path,monkeypatch):
 p=tmp_path/'ops.sqlite';p.touch();c=Store(p);monkeypatch.setenv('DEV004_PAIRED_BINDING_REQUIRED','1');monkeypatch.setattr(resource,'getrlimit',lambda _: (499000000,499000000));return c

def test_valid_production_budget(tmp_path,monkeypatch):
 c=setup(tmp_path,monkeypatch);seen=[];monkeypatch.setattr(guard,'validate_pair',lambda **kw:seen.append(kw) or 'paired')
 m._storage(c);assert c.limit==117187 and seen==[{'write_path':str(c.p),'purpose':'write'}]
def test_invalid_pair_rejected(tmp_path,monkeypatch):
 c=setup(tmp_path,monkeypatch)
 def fail(**kw):raise RuntimeError('PAIR_BINDING_MISMATCH')
 monkeypatch.setattr(guard,'validate_pair',fail)
 with pytest.raises(RuntimeError,match='PAIR_BINDING_MISMATCH'):m._storage(c)
def test_kernel_limit_required(tmp_path,monkeypatch):
 c=setup(tmp_path,monkeypatch);monkeypatch.setattr(guard,'validate_pair',lambda **kw:'paired');monkeypatch.setattr(resource,'getrlimit',lambda _: (resource.RLIM_INFINITY,resource.RLIM_INFINITY))
 with pytest.raises(ValueError,match='KERNEL_FILE_LIMIT'):m._storage(c)
def test_dev_budget_unchanged(tmp_path,monkeypatch):
 c=setup(tmp_path,monkeypatch);monkeypatch.delenv('DEV004_PAIRED_BINDING_REQUIRED')
 with pytest.raises(ValueError,match='DATABASE_BUDGET'):m._storage(c)
def test_wal_limit_unchanged(tmp_path,monkeypatch):
 c=setup(tmp_path,monkeypatch);monkeypatch.setattr(guard,'validate_pair',lambda **kw:'paired');Path(str(c.p)+'-wal').write_bytes(b'x'*m.MAX_WAL_BYTES)
 with pytest.raises(ValueError,match='WAL_PRESSURE'):m._storage(c)
