"""Existing recovery renderer + actual paired owner, isolated/no provider traffic."""
import ast
import configparser
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import resource
import shutil
import socket
import types
import pytest
from supervisor.datatypes import dict_of_key_value_pairs

ROOT=Path(__file__).resolve().parents[1]
RENDERER=ROOT/'docs/dev/lightweight_dev004_first_v2/quarantine_continuation/production_control.py'

def load(name):
    p=ROOT/'runtime/listener/src/core'/f'{name}.py'
    spec=importlib.util.spec_from_file_location('recovery_fixture_'+name,p)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def publish(path,value):
    with path.open('wb') as f:f.write(json.dumps(value,sort_keys=True,separators=(',',':')).encode());f.flush();os.fsync(f.fileno())

@pytest.fixture
def case(tmp_path,monkeypatch):
    def forbidden(*a,**k):raise AssertionError('NETWORK_FORBIDDEN')
    monkeypatch.setattr(socket.socket,'connect',forbidden)
    old=resource.getrlimit(resource.RLIMIT_FSIZE)
    resource.setrlimit(resource.RLIMIT_FSIZE,(499000000,old[1]))
    root=tmp_path;core=root/'core.sqlite';ops=root/'ops.sqlite'
    for p in [core,ops]:
        import sqlite3
        c=sqlite3.connect(p);c.execute('CREATE TABLE canonical_evidence(id TEXT PRIMARY KEY)');c.execute("INSERT INTO canonical_evidence VALUES('UNCHANGED')");c.commit();c.close()
    g=load('paired_binding_guard');jm=load('migration_signature_journal');tm=load('migration_terminal_evidence')
    journals=root/'journals';journals.mkdir();journal=jm.MigrationJournal(journals/'migration.jsonl');journal.initialize()
    (journals/'birth.jsonl').write_bytes(b'')
    authority_root=root/'authority';authority_root.mkdir()
    ownerfile=authority_root/'owner.json';authority={'id':'recovery-contract','path':str(ownerfile),'origin_generation':'source'}
    def activate(paths,generation,predecessor=None):
        pair={'version':1,'authority':authority,'generation':generation,'stores':{k:g.database_identity(str(v),generation,k) for k,v in paths.items()},'predecessor':predecessor}
        path=authority_root/('pair.json' if generation=='source' else generation+'-pair.json');publish(path,pair)
        publish(ownerfile,{'version':1,'authority_id':authority['id'],'origin_generation':'source','generation':generation,'manifest_path':str(path),'manifest_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'phase':'ACTIVE','owner':'runtime','transfer_complete':True})
        g.seal_manifest(path)
        env={'DB_PATH':str(paths['core']),'WT_OPS_DB_PATH':str(paths['ops']),'FLEX_DB_PAIR_MANIFEST':str(path),'FLEX_DB_PAIR_AUTHORITY':str(ownerfile),'DEV004_PAIRED_BINDING_REQUIRED':'1','DEV004_SIGNATURE_FIRST_JOURNAL_ENABLED':'1','MIGRATION_JOURNAL_PATH':str(journal.path),'BIRTH_JOURNAL_PATH':str(journals/'birth.jsonl')}
        for k,v in env.items():monkeypatch.setenv(k,v)
        return env,pair
    env,pair=activate({'core':core,'ops':ops},'source')
    owner=tm.TerminalEvidenceStore(ops,journal.path,generation='source',validate_record=journal._validate,validate_pair=lambda p:g.validate_pair(core_path=env['DB_PATH'],ops_path=str(p)))
    owner.initialize();journal.terminal_owner=owner
    package=root/'listener';(package/'src/core').mkdir(parents=True)
    for name in ['pumpfun_curve_listener.py','migration_signature_journal.py','migration_terminal_evidence.py']:
        shutil.copyfile(ROOT/'runtime/listener/src/core'/name,package/'src/core'/name)
    preimages=root/'source-preimages';preimages.mkdir()
    cfg=configparser.ConfigParser(interpolation=None)
    cfg['supervisord']={'logfile':'unchanged.log'}
    for service in ['watchtower_listener','creator_resolution_worker','walkback_worker']:
        cfg['program:'+service]={'environment':'ACCEPTED_SENTINEL="unchanged"','command':'accepted-wrapper','directory':'accepted-root'}
    cfg['program:dormant']={'environment':'UNCHANGED="1"','command':'never-start'}
    with (preimages/'supervisor.conf').open('w') as f:cfg.write(f)
    live=root/'live-supervisor.conf';shutil.copyfile(preimages/'supervisor.conf',live)
    roles={k:{'package':str(package),'command':['accepted-wrapper'],'runtime_environment_overrides':{},'nonsecret_wrapper_environment':{}} for k in ['listener','creator_resolution','walkback']}
    plan={'planned_production_environment':dict(env),'roles':roles}
    controls=[]
    def ctl(*args):
        controls.append(args)
        return 'RUNNING' if args[0]=='status' else ''
    ns={'P':plan,'S':root,'CONF':live,'os':os,'pathlib':__import__('pathlib'),'json':json,'configparser':configparser,'hashlib':hashlib,'importlib':__import__('importlib'),'g':g,'sha':lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest(),'ctl':ctl,'save':lambda *a:None,'print':lambda *a,**k:None}
    tree=ast.parse(RENDERER.read_text())
    allowed={'atomic','settings','terminal_environment','write_config'}
    for node in tree.body:
        if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id in {'TERMINAL_FLAG','TERMINAL_PAYLOAD','TERMINAL_SCHEMA_CONTRACT','MAX_RECOVERY_CONFIG_BYTES'} for t in node.targets):exec(compile(ast.Module(body=[node],type_ignores=[]),str(RENDERER),'exec'),ns)
        if isinstance(node,ast.FunctionDef) and node.name in allowed:exec(compile(ast.Module(body=[node],type_ignores=[]),str(RENDERER),'exec'),ns)
    publish(authority_root/'auxiliary.json',{'fixture_only':True})
    def output_env():
        c=configparser.ConfigParser(interpolation=None);c.read(live)
        return dict_of_key_value_pairs(c['program:watchtower_listener']['environment'])
    yield types.SimpleNamespace(root=root,core=core,ops=ops,g=g,jm=jm,tm=tm,journal=journal,owner=owner,package=package,env=env,pair=pair,plan=plan,ns=ns,controls=controls,live=live,activate=activate,output_env=output_env)
    assert all(p.stat().st_size<1000000 for p in root.rglob('*') if p.is_file())
    resource.setrlimit(resource.RLIMIT_FSIZE,old)


def seed_and_reclaim(case):
    row=case.journal._new('exhausted-signature','PUMPSWAP',100,'retained-subject')
    row.update(state='RETRY_EXHAUSTED',attempts=3,error_code='RETRY_CEILING')
    with case.journal._lock():case.journal._publish([row])
    assert case.journal.reclaim_protected()==[row['id']]
    pending=case.journal.admit('pending-admission','PUMPSWAP',200)
    return row,pending


def test_normal_v2_repairs_missing_legacy_flag_and_preserves_settings(case):
    assert 'DEV004_MIGRATION_TERMINAL_EVIDENCE_ENABLED' not in case.env
    case.ns['write_config']('v2')
    out=case.output_env()
    assert out['DEV004_MIGRATION_TERMINAL_EVIDENCE_ENABLED']=='1'
    for key in ['MIGRATION_JOURNAL_PATH','BIRTH_JOURNAL_PATH','DB_PATH','WT_OPS_DB_PATH']:assert out[key]==case.env[key]
    assert out['RPC_METRICS_DB']==out['DB_PATH']
    assert out['ACCEPTED_SENTINEL']=='unchanged'
    cfg=configparser.ConfigParser(interpolation=None);cfg.read(case.live)
    assert cfg['program:dormant']['command']=='never-start'
    assert 'DEV004_MIGRATION_TERMINAL_EVIDENCE_ENABLED' not in dict_of_key_value_pairs(cfg['program:walkback_worker']['environment'])


def test_fresh_compact_rollback_and_repeated_restart(case):
    import sqlite3
    row,pending=seed_and_reclaim(case)
    paths={k:case.root/f'fresh-{k}.sqlite' for k in ['core','ops']}
    for key,source in {'core':case.core,'ops':case.ops}.items():
        src=sqlite3.connect(source.as_uri()+'?mode=ro',uri=True);dst=sqlite3.connect(paths[key])
        try:src.backup(dst,pages=20)
        finally:src.close();dst.close()
        publish(Path(str(source)+'.retired'),{'version':1,'path':str(source),'generation':'source','retired':True})
    env,pair=case.activate(paths,'fresh',{'generation':'source','stores':case.pair['stores']})
    case.ns['settings']=lambda mode:dict(env)  # Exact fresh_pair_recovery.py final call contract.
    for _ in range(3):
        case.ns['write_config']('v2')
        configured=case.output_env()
        assert configured['DEV004_MIGRATION_TERMINAL_EVIDENCE_ENABLED']=='1'
        assert configured['RPC_METRICS_DB']==configured['DB_PATH']
        owner=case.tm.TerminalEvidenceStore(paths['ops'],case.journal.path,generation='fresh',validate_record=case.journal._validate,validate_pair=lambda p:case.g.validate_pair(core_path=env['DB_PATH'],ops_path=str(p)))
        j=case.jm.MigrationJournal(case.journal.path,terminal_owner=owner)
        assert j.admit(row['signature'],row['source'],999)==row
        assert not j.begin_attempt(row['signature'])
        assert j.snapshot()==[pending]
    with sqlite3.connect(paths['ops']) as c:
        assert c.execute('SELECT DISTINCT transfer_generation FROM migration_terminal_evidence_v1').fetchall()==[('source',)]
        assert c.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert c.execute('SELECT * FROM canonical_evidence').fetchall()==[('UNCHANGED',)]


@pytest.mark.parametrize('boundary',['planned','role_override','wrapper_override'])
def test_explicit_missing_authority_flag_zero_rejected(case,boundary):
    seed_and_reclaim(case);flag=case.ns['TERMINAL_FLAG']
    target={'planned':case.plan['planned_production_environment'],'role_override':case.plan['roles']['listener']['runtime_environment_overrides'],'wrapper_override':case.plan['roles']['listener']['nonsecret_wrapper_environment']}[boundary]
    target[flag]='0';before=case.live.read_bytes()
    with pytest.raises(RuntimeError,match='TERMINAL_FLAG_DOWNGRADE'):case.ns['write_config']('v2')
    assert case.live.read_bytes()==before and not case.controls


@pytest.mark.parametrize('failure',['generation','schema','source','journal','legacy_mode'])
def test_unqualified_recovery_fails_before_publication(case,failure):
    if failure=='generation':
        seal=Path(str(case.ops)+'.pair-bound');value=json.loads(seal.read_text());value['generation']='wrong-OPS-generation';publish(seal,value)
    elif failure=='schema':
        import sqlite3
        c=sqlite3.connect(case.ops);c.execute('DROP TABLE migration_terminal_evidence_v1');c.commit();c.close()
    elif failure=='source':(case.package/'src/core/migration_signature_journal.py').write_text('# terminal-unaware stale fallback')
    elif failure=='journal':case.plan['planned_production_environment']['MIGRATION_JOURNAL_PATH']=str(case.root/'wrong-journal')
    before=case.live.read_bytes()
    with pytest.raises(RuntimeError):
        case.ns['write_config']('v1' if failure=='legacy_mode' else 'v2')
    assert case.live.read_bytes()==before and not case.controls


@pytest.mark.parametrize('boundary',['file_sync','replace','directory_sync'])
def test_crash_during_configuration_publication_never_activates(case,monkeypatch,boundary):
    seed_and_reclaim(case)
    case.ns['write_config']('v2');before=case.live.read_bytes();case.controls.clear()
    case.plan['planned_production_environment']['RECOVERY_FIXTURE_NOTE']='new-publication'
    oldsync=os.fsync;oldreplace=os.replace;count=[0]
    if boundary=='replace':
        def fail(*a):raise RuntimeError('CRASH')
        monkeypatch.setattr(os,'replace',fail)
    else:
        def fail(fd):
            count[0]+=1
            if count[0]==(1 if boundary=='file_sync' else 2):raise RuntimeError('CRASH')
            return oldsync(fd)
        monkeypatch.setattr(os,'fsync',fail)
    with pytest.raises(RuntimeError,match='CRASH'):case.ns['write_config']('v2')
    assert not case.controls
    if boundary=='directory_sync':
        assert case.live.read_bytes()!=before
        assert case.output_env()['RECOVERY_FIXTURE_NOTE']=='new-publication'
    else:assert case.live.read_bytes()==before
    assert case.output_env()['DEV004_MIGRATION_TERMINAL_EVIDENCE_ENABLED']=='1'
    assert case.journal.snapshot()[0]['signature']=='pending-admission'
