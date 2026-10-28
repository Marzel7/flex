"""Exact recovery-only claim loop on tiny disposable SQLite stores; no providers."""
import ast, json, sqlite3, types
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/'scripts/flex_v2_fresh_pair_recovery.py'

def loop():
    tree=ast.parse(SCRIPT.read_text());fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='execute')
    return next(n for n in fn.body if isinstance(n,ast.For) and isinstance(n.target,ast.Tuple) and any(isinstance(z,ast.Constant) and z.value=='wt_walkback_queue' for z in ast.walk(n.iter)))

def run(root,connect=sqlite3.connect):
    exec(compile(ast.Module(body=[loop()],type_ignores=[]),str(SCRIPT),'exec'),{'root':root,'dws':types.SimpleNamespace(_native_connect=connect),'sqlite3':sqlite3})

@pytest.fixture
def stores(tmp_path,monkeypatch):
    import socket
    monkeypatch.setattr(socket.socket,'connect',lambda *a,**k:(_ for _ in ()).throw(AssertionError('NETWORK_FORBIDDEN')))
    with sqlite3.connect(tmp_path/'core.sqlite') as q:
        for t in ['creator_resolution_queue','creator_funding_queue']:
            q.execute('CREATE TABLE '+t+'(id TEXT PRIMARY KEY,status TEXT,attempts INT,next_retry_at INT,locked_until INT,locked_by TEXT)')
            q.execute('INSERT INTO '+t+' VALUES(?,?,?,?,?,?)',('core','running',2,500,9999,'exited-owner'))
        q.execute('CREATE TABLE canonical_evidence(id TEXT PRIMARY KEY,payload TEXT)');q.execute('INSERT INTO canonical_evidence VALUES(?,?)',('CREATE','UNCHANGED_PROVENANCE'))
    with sqlite3.connect(tmp_path/'ops.sqlite') as q:
        q.execute('CREATE TABLE wt_walkback_queue(mint TEXT PRIMARY KEY,status TEXT,attempts INT,next_retry_at INT,lease_expires_at INT,claimed_by TEXT,create_anchor_signature TEXT,intelligence_outcome TEXT,attribution_source TEXT)')
        for i,status in enumerate(['running','complete','failed','waiting','quarantined','RETRY_EXHAUSTED','pending']):
            q.execute('INSERT INTO wt_walkback_queue VALUES(?,?,?,?,?,?,?,?,?)',(status,status,1,0,9999,'exited-owner','retained-signature','retained-outcome','retained-provenance'))
        for table in ['migration_terminal_evidence_v1','migration_deferred_evidence_v1','wt_create_event_ledger']:
            q.execute('CREATE TABLE '+table+'(id TEXT PRIMARY KEY,payload TEXT)');q.execute('INSERT INTO '+table+' VALUES(?,?)',('protected','EXACT_IDENTITY_AND_PROVENANCE'))
    return tmp_path

def rows(root):
    with sqlite3.connect(root/'ops.sqlite') as q:return q.execute('SELECT * FROM wt_walkback_queue ORDER BY mint').fetchall()

def due(root,now):
    with sqlite3.connect(root/'ops.sqlite') as q:return q.execute("SELECT mint FROM wt_walkback_queue WHERE (status='pending' OR (status='running' AND COALESCE(lease_expires_at,0)<?)) AND attempts<3 AND COALESCE(next_retry_at,0)<=? ORDER BY mint",(now,now)).fetchall()

def test_exact_running_transition_preserves_fields_and_core_retry(stores):
    before={r[0]:r for r in rows(stores)};run(stores);after={r[0]:r for r in rows(stores)}
    expected=list(before['running']);expected[1]='pending';expected[4]=0;expected[5]=None
    assert after['running']==tuple(expected)
    for mint,row in before.items():
        if mint!='running':assert after[mint]==row
    assert ('running',) in due(stores,1)
    with sqlite3.connect(stores/'core.sqlite') as q:
        for t in ['creator_resolution_queue','creator_funding_queue']:assert q.execute('SELECT status,attempts,next_retry_at FROM '+t).fetchone()==('retry',2,500)
        assert q.execute('SELECT payload FROM canonical_evidence').fetchone()==('UNCHANGED_PROVENANCE',)

@pytest.mark.parametrize('attempts,next_at,selectable',[(0,0,True),(2,0,True),(3,0,False),(1,100,False)])
def test_retry_ceiling_and_future_eligibility_unchanged(stores,attempts,next_at,selectable):
    with sqlite3.connect(stores/'ops.sqlite') as q:q.execute("UPDATE wt_walkback_queue SET attempts=?,next_retry_at=? WHERE mint='running'",(attempts,next_at))
    run(stores)
    with sqlite3.connect(stores/'ops.sqlite') as q:assert q.execute("SELECT attempts,next_retry_at FROM wt_walkback_queue WHERE mint='running'").fetchone()==(attempts,next_at)
    assert (('running',) in due(stores,1)) is selectable
    if next_at and attempts<3:assert ('running',) in due(stores,next_at)

def test_repeated_recovery_and_evidence_unchanged(stores):
    run(stores);before=rows(stores)
    for _ in range(3):run(stores);assert rows(stores)==before
    with sqlite3.connect(stores/'ops.sqlite') as q:
        for t in ['migration_terminal_evidence_v1','migration_deferred_evidence_v1','wt_create_event_ledger']:assert q.execute('SELECT * FROM '+t).fetchall()==[('protected','EXACT_IDENTITY_AND_PROVENANCE')]

@pytest.mark.parametrize('after_commit',[False,True])
def test_crash_and_restart_at_claim_commit(stores,after_commit):
    class Crash(sqlite3.Connection):
        def execute(self,sql,*a,**k):
            result=super().execute(sql,*a,**k)
            if not after_commit and sql.lower().startswith('update "wt_walkback_queue"'):raise RuntimeError('FIXTURE_CRASH_BEFORE_COMMIT')
            return result
        def commit(self):
            if after_commit:
                super().commit();raise RuntimeError('FIXTURE_CRASH_AFTER_COMMIT')
            return super().commit()
    def connect(path):return sqlite3.connect(path,factory=Crash if str(path).endswith('ops.sqlite') else sqlite3.Connection)
    with pytest.raises(RuntimeError,match='FIXTURE_CRASH'):run(stores,connect)
    with sqlite3.connect(stores/'ops.sqlite') as q:assert q.execute("SELECT status FROM wt_walkback_queue WHERE mint='running'").fetchone()[0]==('pending' if after_commit else 'running')
    run(stores);first=rows(stores);run(stores);assert rows(stores)==first and ('running',) in due(stores,1)

def test_current_script_retains_authority_quiescence_and_gate_sequence():
    s=SCRIPT.read_text();assert "c.g.validate_pair()==c.P['generation']" in s
    assert s.index("c.freeze_helius_recovery(helius_prepared)")<s.index("for k,table,expiry")<s.index('c.finish_helius_recovery(env)')
    assert "for name in ['watchtower_listener','creator_resolution_worker','walkback_worker']:assert 'STOPPED'" in s
    assert 'never un-retires or copies v1' in s.lower() and 'src.backup(dst,pages=128' in s
