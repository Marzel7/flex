"""Focused receipt-retirement safety boundary; no production state or providers."""
import hashlib,importlib.util,json,os,resource,socket,subprocess,sys,types
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
NOW=1800000000000000000
DAY=86400*1000000000
M='1'*32
C='1'*31+'2'
S='1'*64

def module(path,name):
 spec=importlib.util.spec_from_file_location(name,path)
 value=importlib.util.module_from_spec(spec);sys.modules[name]=value;spec.loader.exec_module(value);return value

@pytest.fixture
def case(tmp_path,monkeypatch):
 for name in list(sys.modules):
  if name=='src' or name.startswith('src.'):monkeypatch.delitem(sys.modules,name)
 monkeypatch.syspath_prepend(str(ROOT/'runtime/listener'))
 def forbid(*a,**k):raise AssertionError('NETWORK_FORBIDDEN')
 monkeypatch.setattr(socket.socket,'connect',forbid)
 monkeypatch.setattr(socket,'create_connection',forbid)
 old=resource.getrlimit(resource.RLIMIT_FSIZE);resource.setrlimit(resource.RLIMIT_FSIZE,(499000000,old[1]))
 from src.core.pumpportal_birth_audit import PumpPortalBirthAudit
 from src.core.pumpportal_receipt_maintenance import CompactReceiptMaintenance,DependencyResult
 class Checker:
  def check(self,mint):return DependencyResult('ELIGIBLE','SYNTHETIC_COMPLETED_DEPENDENCIES')
 audit=PumpPortalBirthAudit(enabled=False,path=str(tmp_path/'raw-forbidden'),compact_path=str(tmp_path/'receipts'))
 def seed(age=8,outcome='COMMITTED',conflict=False):
  ident=audit._compact_record(M,C,S,NOW-age*DAY);assert ident
  path=audit._receipt_target(ident);row=audit._read_receipt(path)
  row.update(persistence_outcome=outcome,accepted_at_utc_ns=NOW-age*DAY+1)
  if conflict:row['canonical_conflict']=True
  audit._store_receipt(ident,row,target=path)
  return ident,path,row
 value=types.SimpleNamespace(root=tmp_path,audit=audit,seed=seed,owner=CompactReceiptMaintenance(audit.compact_path,checker=Checker()),checker=Checker(),maintenance=CompactReceiptMaintenance)
 yield value
 assert not (tmp_path/'raw-forbidden').exists()
 assert all(p.stat().st_size<500000000 for p in tmp_path.rglob('*') if p.is_file())
 resource.setrlimit(resource.RLIMIT_FSIZE,old)

@pytest.mark.parametrize('age',[6,7,8])
def test_age_does_not_grant_disposal_of_required_birth_proof(case,age):
 ident,path,row=case.seed(age)
 assert case.audit.accepted_birth(M,C,S)==(C,S)
 before=path.read_bytes()
 result=case.owner.run(start_bucket=int(case.audit._bucket_name(ident),16),now_ns=NOW)
 assert result['archived']==[] and path.read_bytes()==before
 assert len(result['protected'])==1
 assert result['protected'][0]['reason']==('REQUIRED_BIRTH_PROVENANCE' if age==8 else 'YOUNGER_THAN_SEVEN_DAYS')
 assert case.audit.accepted_birth(M,C,S)==(C,S)

@pytest.mark.parametrize('outcome,conflict',[('RETRY_PENDING',False),('UNCONFIRMED',False),('COMMITTED_CONFLICT',True),('UNRECOVERABLE',False)])
def test_pending_unknown_and_conflicting_receipts_remain_protected(case,outcome,conflict):
 ident,path,row=case.seed(outcome=outcome,conflict=conflict)
 before=path.read_bytes();result=case.owner.run(start_bucket=int(case.audit._bucket_name(ident),16),now_ns=NOW)
 assert not result['archived'] and path.read_bytes()==before


def test_repeated_maintenance_restart_and_duplicate_preserve_original(case):
 ident,path,row=case.seed();before=path.read_bytes()
 for _ in range(3):
  case.maintenance(case.audit.compact_path,checker=case.checker).run(start_bucket=int(case.audit._bucket_name(ident),16),now_ns=NOW)
  assert case.audit._compact_record(M,C,S,NOW)==ident
  assert path.read_bytes()==before
 assert len(list(case.audit.compact_path.rglob('*.json')))==1


def test_baseline_archive_loses_proof_and_recreates_same_identity(case):
 # Exact frozen baseline counterexample, not a deliberately weakened new owner.
 source=subprocess.check_output(['git','show','14158e4e:runtime/listener/src/core/pumpportal_receipt_maintenance.py'],cwd=ROOT,text=True)
 path=case.root/'baseline_owner.py';path.write_text(source)
 baseline=module(path,'receipt_baseline_counterexample')
 ident,receipt,row=case.seed();assert case.audit.accepted_birth(M,C,S)==(C,S)
 owner=baseline.CompactReceiptMaintenance(case.audit.compact_path,checker=case.checker)
 result=owner.run(start_bucket=int(case.audit._bucket_name(ident),16),now_ns=NOW)
 assert len(result['archived'])==1
 archive=case.audit.compact_path/result['archived'][0]['archive']
 assert archive.exists() and not receipt.exists()
 assert case.audit.accepted_birth(M,C,S) is None
 assert case.audit._compact_record(M,C,S,NOW)==ident
 recreated=case.audit._read_receipt(receipt)
 assert recreated['receipt_utc_ns']==NOW and recreated['persistence_outcome']=='UNCONFIRMED'
 assert case.audit._read_receipt(archive)==row
 assert len(list(case.audit.compact_path.rglob('*.json')))==2
