"""Exact preserved listener availability contract; compact provider-free fixtures."""
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import sqlite3
import threading
import pytest
from src.ops import acquisition_availability as a

EXPECTED='135907c90de858ac403da6ab92cf1a00b5714b5c0c61acc730dd917aed7458ad'

@pytest.fixture(autouse=True)
def no_acquisition(monkeypatch):
    def forbid(*args,**kwargs):raise AssertionError('unexpected acquisition/background work')
    monkeypatch.setattr(socket.socket,'connect',forbid)
    monkeypatch.setattr(threading.Thread,'start',forbid)


def test_exact_source_stdlib_and_listener_symbol_contract():
    path=Path(a.__file__)
    assert hashlib.sha256(path.read_bytes()).hexdigest()==EXPECTED
    tree=ast.parse(path.read_text())
    names={n.module.split('.')[0] for n in tree.body if isinstance(n,ast.ImportFrom)}
    names|={z.name.split('.')[0] for n in tree.body if isinstance(n,ast.Import) for z in n.names}
    assert names=={'__future__','hashlib','json','os','sqlite3','time','pathlib'}
    listener=path.parents[1]/'core/pumpfun_curve_listener.py'
    t=ast.parse(listener.read_text())
    assert any(isinstance(n,ast.ImportFrom) and n.module=='src.ops.acquisition_availability' and [z.name for z in n.names]==['AcquisitionAvailability'] for n in t.body)
    assert {'UNKNOWN','AVAILABLE','DOWN','DEGRADED'}==a.STATES


def test_actual_import_and_constructor_have_no_io_or_workers(tmp_path,monkeypatch):
    def forbid(*args,**kwargs):raise AssertionError('import/constructor requested SQLite')
    monkeypatch.setattr(sqlite3,'connect',forbid)
    spec=importlib.util.spec_from_file_location('_dev090_availability_import',a.__file__)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    obj=module.AcquisitionAvailability(str(tmp_path/'never-created.db'),'fixture')
    assert obj.runtime_id=='fixture' and not list(tmp_path.iterdir())


def test_normal_operation_dedup_and_persistence(tmp_path):
    path=tmp_path/'availability.db';obj=a.AcquisitionAvailability(str(path),'fixture')
    assert not path.exists()
    assert obj.transition('BIRTH','UNKNOWN','listener startup',now=100)
    assert obj.transition('BIRTH','AVAILABLE','subscription sent','id1',now=110)
    assert obj.transition('BIRTH','AVAILABLE','duplicate','id1',now=111)
    assert obj.transition('BIRTH','DOWN','disconnect',now=120)
    assert obj.transition('BIRTH','AVAILABLE','reconnected','id2',now=125)
    with sqlite3.connect(path) as c:
        rows=c.execute('SELECT state,previous_state,runtime_id,connection_id,provenance FROM acquisition_availability_transitions ORDER BY effective_at').fetchall()
    assert len(rows)==4
    assert rows[1][:4]==('AVAILABLE','UNKNOWN','fixture','id1')
    assert json.loads(rows[1][4])=={'writer':'listener_control_flow'}
    result=a.rolling_summary(str(path),'BIRTH',now=130)
    assert result['down_seconds']==5 and result['available_seconds']==15
    assert result['current_state']=='AVAILABLE' and result['full_24h_qualified'] is False
    assert a.AcquisitionAvailability(str(path),'restart').transition('BIRTH','AVAILABLE','same after restart',now=131)
    with sqlite3.connect(path) as c:assert c.execute('SELECT COUNT(*) FROM acquisition_availability_transitions').fetchone()[0]==4
    assert path.stat().st_size<1048576

@pytest.mark.parametrize('cap,state',[('OTHER','AVAILABLE'),('BIRTH','BOGUS')])
def test_invalid_input_no_database(tmp_path,cap,state):
    p=tmp_path/'invalid.db'
    assert a.AcquisitionAvailability(str(p),'fixture').transition(cap,state,'invalid',now=1) is False
    assert not p.exists()


def test_unavailable_storage_and_summary_fail_explicitly(tmp_path):
    p=tmp_path/'absent'/'state.db'
    assert a.AcquisitionAvailability(str(p),'fixture').transition('MIGRATION','AVAILABLE','test',now=1) is False
    result=a.rolling_summary(str(p),'BIRTH',now=86400)
    assert result['current_state']=='UNKNOWN'
    assert result['observed_availability_percent'] is None
    assert result['unknown_seconds']==86400
    assert not p.exists()

@pytest.mark.parametrize('failure_stage',['connect','read','insert','commit'])
def test_failures_are_isolated(tmp_path,monkeypatch,failure_stage):
    obj=a.AcquisitionAvailability(str(tmp_path/'failure.db'),'fixture')
    class Conn:
        def execute(self,sql,*args):
            if failure_stage=='read' and sql.startswith('SELECT'):raise sqlite3.OperationalError('read failed')
            if failure_stage=='insert' and sql.startswith('INSERT'):raise sqlite3.OperationalError('insert failed')
            return self
        def fetchone(self):return None
        def commit(self):
            if failure_stage=='commit':raise sqlite3.OperationalError('commit failed')
        def close(self):pass
    def connect():
        if failure_stage=='connect':raise sqlite3.OperationalError('connection failed')
        return Conn()
    monkeypatch.setattr(obj,'_connect',connect)
    assert obj.transition('MIGRATION','AVAILABLE','failure',now=2) is False
