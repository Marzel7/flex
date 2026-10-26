"""Exact existing renderer, disposable paired stores, blocked networking."""
import ast,hashlib,json,os,shutil,sqlite3
from pathlib import Path
import pytest
from tests.test_v2_terminal_recovery_config import case as base_case,seed_and_reclaim
ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture
def case(base_case,monkeypatch):
 e=base_case
 for role,files in e.ns['HELIUS_GATE_PAYLOAD'].items():
  package=e.root/('gate-'+role)
  for relative in files:
   dst=package/relative;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/'runtime'/role/relative,dst)
  e.plan['roles'][role]['package']=str(package)
 listener=Path(e.plan['roles']['listener']['package'])
 for name in ('migration_signature_journal.py','migration_terminal_evidence.py'):shutil.copyfile(ROOT/'runtime/listener/src/core'/name,listener/'src/core'/name)
 e.package=listener
 e.running=[False]
 def control(*args):
  e.controls.append(args)
  if args[0]=='status':return 'RUNNING' if e.running[0] else 'STOPPED'
  if args[0] in ('update','start'):e.running[0]=True
  return ''
 e.ns['ctl']=control
 e.ns['save']=lambda path,value:e.ns['atomic'](path,(json.dumps(value,sort_keys=True,indent=2)+'\n').encode())
 e.env.update(FLEX_HELIUS_DENIAL_GATE_ENABLED='1',HELIUS_DENIAL_GATE_PATH=str(e.root/'journals/helius-denial-v1.json'))
 e.plan['planned_production_environment'].update(e.env)
 preimage=e.root/'source-preimages/supervisor.conf';e.plan.update(recovery_supervisor_preimage_path=str(preimage),recovery_supervisor_preimage_sha256=hashlib.sha256(preimage.read_bytes()).hexdigest())
 for k,v in e.env.items():monkeypatch.setenv(k,v)
 e.module=e.ns['helius_module']('listener');e.gate=e.module.Gate(e.env['HELIUS_DENIAL_GATE_PATH'],'source',clock=lambda:1000);e.gate.initialize('PAYMENT_DENIED')
 return e

def test_all_roles_normal_restart_denial_and_counter_preservation(case):
 e=case;e.gate.result(429) # Must retain independently established denial.
 before=e.gate.read();e.ns['write_config']('v2')
 for role in e.plan['roles']:assert e.ns['helius_environment'](role,e.env)['FLEX_HELIUS_DENIAL_GATE_ENABLED']=='1'
 assert e.gate.read()==before and e.gate.blocked()
 e.ns['write_config']('v2');assert e.gate.read()==before
 with pytest.raises(e.module.Deferred):e.gate.reserve('getTransaction')
 assert e.gate.read()['reservations']==0

@pytest.mark.parametrize('defect',['missing_flag','flag0','path','missing_state','corrupt_state','marker','wrong_generation','source','legacy_source','missing_module'])
def test_fail_closed_before_config_publication(case,defect):
 e=case;role=e.plan['roles']['walkback'];control=Path(e.env['HELIUS_DENIAL_GATE_PATH'])
 if defect=='missing_flag':e.plan['roles']['walkback']['runtime_environment_overrides']['FLEX_HELIUS_DENIAL_GATE_ENABLED']='0'
 elif defect=='flag0':e.plan['planned_production_environment']['FLEX_HELIUS_DENIAL_GATE_ENABLED']='0'
 elif defect=='path':e.plan['roles']['walkback']['runtime_environment_overrides']['HELIUS_DENIAL_GATE_PATH']=str(e.root/'wrong.json')
 elif defect=='missing_state':control.unlink()
 elif defect=='corrupt_state':control.write_bytes(b'{')
 elif defect=='marker':Path(str(control)+'.lock').write_bytes(b'PENDING_V1\n')
 elif defect=='wrong_generation':e.gate.generation='wrong';s=json.loads(control.read_text());s['generation']='wrong';e.gate.save(s)
 elif defect=='source':(Path(role['package'])/'src/core/walkback_worker.py').write_bytes(b'# stale')
 elif defect=='legacy_source':(e.package/'src/core/pumpfun_curve_listener.py').write_bytes(b'# old gate-unaware listener')
 else:(Path(role['package'])/'src/core/helius_denial_gate.py').unlink()
 before=e.live.read_bytes()
 with pytest.raises(Exception):e.ns['write_config']('v2')
 assert e.live.read_bytes()==before and not e.controls


def target(e):
 paths={'core':e.root/'fresh-core.sqlite','ops':e.root/'fresh-ops.sqlite'}
 for role,p in paths.items():
  # Only tiny synthetic fixture stores; never copies a live or V1 database.
  with sqlite3.connect(e.core if role=='core' else e.ops) as src,sqlite3.connect(p) as dst:src.backup(dst,pages=20)
 manifest=dict(e.pair,generation='fresh',stores={role:e.g.database_identity(str(p),'fresh',role) for role,p in paths.items()},predecessor={'generation':'source','stores':e.pair['stores']})
 destination=e.root/'authority/frozen-fresh-pair.json';destination.write_text(json.dumps(manifest,sort_keys=True,separators=(',',':')))
 return paths,manifest,destination


def activate(e,paths,manifest,destination,monkeypatch):
 state={'version':1,'authority_id':manifest['authority']['id'],'origin_generation':'source','generation':'fresh','manifest_path':str(destination),'manifest_sha256':hashlib.sha256(destination.read_bytes()).hexdigest(),'phase':'ACTIVE','owner':'runtime','transfer_complete':True}
 for path in (e.core,e.ops):Path(str(path)+'.retired').write_text(json.dumps({'version':1,'path':str(path),'generation':'source','retired':True}))
 (e.root/'authority/owner.json').write_text(json.dumps(state));e.g.seal_manifest(destination)
 env=dict(e.env,DB_PATH=str(paths['core']),WT_OPS_DB_PATH=str(paths['ops']),FLEX_DB_PAIR_MANIFEST=str(destination))
 for k,v in env.items():monkeypatch.setenv(k,v)
 return env


def test_fresh_pair_generation_only_move_and_repeat(case,monkeypatch):
 e=case;protected,pending=seed_and_reclaim(e);paths,m,dest=target(e);prepared=e.ns['prepare_helius_recovery'](e.env,dest);e.ns['freeze_helius_recovery'](prepared);old=e.gate.read();env=activate(e,paths,m,dest,monkeypatch)
 # Crash between paired rebind and gate move is explicitly refused.
 with pytest.raises(Exception,match='HELIUS_GATE_STATE'):e.ns['helius_environment']('listener',env)
 e.ns['finish_helius_recovery'](env);fresh=e.module.Gate(e.gate.path,'fresh');new=fresh.read();assert new==dict(old,generation='fresh') and fresh.blocked()
 e.ns['finish_helius_recovery'](env);assert fresh.read()==new
 with pytest.raises(e.module.Deferred):fresh.reserve('getTransaction')
 assert fresh.read()['reservations']==old['reservations']
 for role in e.plan['roles']:e.ns['helius_environment'](role,env)
 e.ns['settings']=lambda mode:dict(env);e.ns['write_config']('v2')
 assert e.output_env()['FLEX_HELIUS_DENIAL_GATE_ENABLED']=='1' and e.output_env()['DEV004_MIGRATION_TERMINAL_EVIDENCE_ENABLED']=='1'
 terminal=e.tm.TerminalEvidenceStore(paths['ops'],e.journal.path,generation='fresh',validate_record=e.journal._validate,validate_pair=lambda p:e.g.validate_pair(core_path=env['DB_PATH'],ops_path=str(p)))
 journal=e.jm.MigrationJournal(e.journal.path,terminal_owner=terminal)
 assert journal.admit(protected['signature'],protected['source'],999)==protected
 assert journal.snapshot()==[pending] and not journal.begin_attempt(protected['signature'])


def test_snapshot_after_stop_includes_latest_counters(case,monkeypatch):
 e=case;paths,m,dest=target(e);prepared=e.ns['prepare_helius_recovery'](e.env,dest);e.gate.result(200);latest=e.gate.read()
 e.ns['freeze_helius_recovery'](prepared);env=activate(e,paths,m,dest,monkeypatch);e.ns['finish_helius_recovery'](env)
 assert e.module.Gate(e.gate.path,'fresh').read()==dict(latest,generation='fresh')

@pytest.mark.parametrize('defect',['missing_snapshot','changed_source','destination','mixed_pair','corrupt_after_rebind'])
def test_transfer_failure_never_reinitializes_allowance(case,monkeypatch,defect):
 e=case;paths,m,dest=target(e);e.ns['freeze_helius_recovery'](e.ns['prepare_helius_recovery'](e.env,dest));env=activate(e,paths,m,dest,monkeypatch)
 if defect=='missing_snapshot':(e.root/'authority/helius-denial-recovery.json').unlink()
 elif defect=='changed_source':e.gate.result(200)
 elif defect=='destination':dest.write_text(dest.read_text()+' ')
 elif defect=='mixed_pair':env['WT_OPS_DB_PATH']=str(e.ops);monkeypatch.setenv('WT_OPS_DB_PATH',str(e.ops))
 else:Path(e.gate.path).write_text('{')
 with pytest.raises(Exception):e.ns['finish_helius_recovery'](env)
 if defect!='corrupt_after_rebind':assert e.gate.read()['state']=='PAYMENT_DENIED'


def test_crash_after_gate_commit_before_receipt_ack(case,monkeypatch):
 e=case;paths,m,dest=target(e);e.ns['freeze_helius_recovery'](e.ns['prepare_helius_recovery'](e.env,dest));env=activate(e,paths,m,dest,monkeypatch);save=e.ns['save']
 def crash(*a):raise RuntimeError('CRASH_AFTER_GATE_COMMIT')
 e.ns['save']=crash
 with pytest.raises(RuntimeError):e.ns['finish_helius_recovery'](env)
 assert e.module.Gate(e.gate.path,'fresh').blocked()
 e.ns['save']=save;e.ns['finish_helius_recovery'](env)
 assert e.module.Gate(e.gate.path,'fresh').read()['state']=='PAYMENT_DENIED'


@pytest.mark.parametrize('boundary',['rename','file_sync','directory_sync'])
def test_real_atomic_config_publication_crash(case,monkeypatch,boundary):
 e=case;before=e.live.read_bytes();old=e.gate.read()
 if boundary=='rename':monkeypatch.setattr(e.ns['os'],'replace',lambda *a:(_ for _ in ()).throw(OSError('injected before rename')))
 else:
  sync=e.ns['os'].fsync;count=[0]
  def fail(fd):
   count[0]+=1
   if count[0]==(1 if boundary=='file_sync' else 2):raise OSError('injected sync')
   return sync(fd)
  monkeypatch.setattr(e.ns['os'],'fsync',fail)
 with pytest.raises(OSError):e.ns['write_config']('v2')
 assert all(c[0]=='status' for c in e.controls) and e.gate.read()==old
 if boundary!='directory_sync':assert e.live.read_bytes()==before
 else:assert e.output_env()['FLEX_HELIUS_DENIAL_GATE_ENABLED']=='1'


def test_terminal_protected_evidence_unchanged(case):
 e=case;seed_and_reclaim(e);before=e.owner.lookup('exhausted-signature');e.journal.admit('new-pending','PUMPSWAP',100)
 e.ns['write_config']('v2');assert e.owner.lookup('exhausted-signature')==before
 row=next(r for r in e.journal.snapshot() if r['signature']=='new-pending');assert row['signature']=='new-pending' and row['attempts']==0 and row['ack'] is None


def test_fresh_script_orders_gate_receipt_after_stops_before_publication():
 text=(ROOT/'scripts/flex_v2_fresh_pair_recovery.py').read_text()
 assert text.index('prepare_helius_recovery')<text.index('c.hold_lanes(')<text.index("c.ctl('stop'")<text.index('freeze_helius_recovery')
 assert text.index('finish_helius_recovery')<text.index("c.write_config('v2')")



def test_first_activation_rejects_live_legacy_roles(case):
 e=case;e.running[0]=True;before=e.live.read_bytes()
 with pytest.raises(RuntimeError,match='REQUIRES_QUIESCENCE'):e.ns['write_config']('v2')
 assert e.live.read_bytes()==before and all(c[0]=='status' for c in e.controls)


def test_changed_pair_rebind_requires_quiescence(case,monkeypatch):
 e=case;e.ns['write_config']('v2');e.controls.clear();paths,m,dest=target(e)
 e.ns['freeze_helius_recovery'](e.ns['prepare_helius_recovery'](e.env,dest));env=activate(e,paths,m,dest,monkeypatch);e.ns['finish_helius_recovery'](env)
 e.ns['settings']=lambda mode:dict(env);before=e.live.read_bytes()
 with pytest.raises(RuntimeError,match='REQUIRES_QUIESCENCE'):e.ns['write_config']('v2')
 assert e.live.read_bytes()==before


@pytest.mark.parametrize('defect',['missing','hash','unrelated_live_drift'])
def test_frozen_configuration_authority_required(case,defect):
 e=case
 if defect=='missing':e.plan.pop('recovery_supervisor_preimage_path')
 elif defect=='hash':e.plan['recovery_supervisor_preimage_sha256']='wrong'
 else:e.live.write_bytes(e.live.read_bytes().replace(b'never-start',b'new-accepted-dormant-command'))
 before=e.live.read_bytes()
 with pytest.raises(RuntimeError):e.ns['write_config']('v2')
 assert e.live.read_bytes()==before and not e.controls
