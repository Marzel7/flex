"""Provider-free dormant package qualification; no live databases or services."""
import hashlib,json,os,pathlib,subprocess,sys
import pytest
ROOT=pathlib.Path(__file__).resolve().parents[1]
PYTHON=sys.executable

@pytest.fixture
def case(tmp_path):
 root=tmp_path.resolve()
 result=subprocess.run([PYTHON,'-B',str(ROOT/'tests/dormant_fixture_support.py'),str(ROOT),str(root)],capture_output=True,text=True,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
 assert result.returncode==0,result.stderr[-2000:]
 yield root
 assert all(p.stat().st_size<500000000 for p in root.rglob('*') if p.is_file())

def probe(case,role='api',action=None):
 args=[PYTHON,'-B',str(ROOT/'tests/dormant_probe_support.py'),str(ROOT),str(case),role]
 if action:args.append(action)
 child=subprocess.run(args,capture_output=True,text=True,timeout=30,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
 lines=[line[12:] for line in child.stdout.splitlines() if line.startswith('RESULT_JSON ')]
 assert lines,(child.stdout[-3000:],child.stderr[-2000:])
 return json.loads(lines[-1]),child

def mutate(case,fn):
 p=case/'manifest.json';v=json.loads(p.read_text());fn(v);p.write_text(json.dumps(v,sort_keys=True,separators=(',',':')))

@pytest.mark.parametrize('role',['api','monitor','bridge'])
def test_cold_import_and_restart_no_side_effects(case,role):
 for _ in range(2):
  result,child=probe(case,role)
  assert child.returncode==0 and result['status']=='PASS',result
  assert result['blocked']==[]
  if role=='api':assert result['routes']>=600 and result['assets']
 assert json.loads((case/'queue/provider_budget.json').read_text())['calls']==[]

@pytest.mark.parametrize('fault',['core','ops','generation','phase','manifest','source','telemetry','budget','budget_pending','missing_env','retired','replaced','selection'])
def test_bad_authority_fails_before_component_import(case,fault):
 if fault in ['core','ops']:mutate(case,lambda m:m['environment'].__setitem__('DB_PATH' if fault=='core' else 'WT_OPS_DB_PATH',str(case/'absent.sqlite')))
 elif fault=='generation':mutate(case,lambda m:m.__setitem__('generation','wrong-generation'))
 elif fault=='phase':
  p=case/'owner.json';v=json.loads(p.read_text());v['phase']='TRANSFER';p.write_text(json.dumps(v))
 elif fault=='manifest':mutate(case,lambda m:m['environment'].__setitem__('FLEX_DB_PAIR_MANIFEST',str(case/'absent.json')))
 elif fault=='source':mutate(case,lambda m:m.__setitem__('source_commit','0'*40))
 elif fault=='telemetry':mutate(case,lambda m:m['environment'].__setitem__('RPC_METRICS_DB',str(case/'ops.sqlite')))
 elif fault=='budget':(case/'queue/provider_budget.json').write_text('{')
 elif fault=='missing_env':mutate(case,lambda m:m['environment'].pop('DB_PATH'))
 elif fault=='retired':(case/'core.sqlite.retired').write_text('{}')
 elif fault=='replaced':
  (case/'ops.sqlite').rename(case/'previous.sqlite');(case/'ops.sqlite').write_bytes(b'not-authorized')
 elif fault=='selection':(case/'selection.json').write_text('{}')
 else:(case/'queue/provider_budget.lock').write_bytes(b'PENDING_V1\n')
 result,child=probe(case)
 assert child.returncode!=0 and result['status']=='HOLD'
 assert result['blocked']==[]

@pytest.mark.parametrize('role',['api','monitor','bridge'])
def test_shared_write_fence_rejects_stale_connection(case,role):
 result,child=probe(case,role,'write_guard')
 assert child.returncode==0 and result['status']=='PASS',result
 assert result['stale_writer_rejected'] and result['blocked']==[]


def test_completed_window_and_budget_survive_fresh_import(case):
 result,child=probe(case,'monitor','checkpoint_budget')
 assert child.returncode==0 and result['budget_reserved_without_dispatch']==1,result
 result,child=probe(case,'monitor')
 assert child.returncode==0 and result['status']=='PASS',result
 state=json.loads((case/'queue/provider_budget.json').read_text());assert len(state['calls'])==1
 assert state['calls'][0]['mint']=='synthetic-mint'


def test_ui_assets_are_unmodified_from_accepted_source():
 # Packaging never rewrites templates or static assets.
 assert subprocess.check_output(['git','diff','c0682a30','--','templates','static'],cwd=ROOT)==b''


@pytest.mark.parametrize('role',['api','monitor','bridge'])
def test_unknown_read_target_is_rejected_without_creating_file(case,role):
 result,child=probe(case,role,'read_fallback_guard')
 assert child.returncode==0 and result['unknown_read_rejected'],result
 assert result['blocked']==[]

@pytest.mark.parametrize('action,field',[('api_cache','expired_cache_preserved'),('api_asset','static_asset_response_verified')])
def test_api_cache_and_existing_asset_contracts(case,action,field):
 result,child=probe(case,'api',action)
 assert child.returncode==0 and result[field],result
 assert result['blocked']==[]


@pytest.mark.parametrize('role',['api','monitor','bridge'])
def test_fresh_compact_pair_rebind_preserves_checkpoint_and_budget(case,role):
 result,child=probe(case,'monitor','checkpoint_budget')
 assert child.returncode==0,result
 old_budget=(case/'queue/provider_budget.json').read_bytes()
 child=subprocess.run([PYTHON,'-B',str(ROOT/'tests/dormant_fixture_support.py'),str(ROOT),str(case),'fresh'],capture_output=True,text=True)
 assert child.returncode==0,child.stderr
 result,child=probe(case,role)
 assert child.returncode==0 and result['status']=='PASS' and not result['blocked'],result
 assert (case/'queue/provider_budget.json').read_bytes()==old_budget
 import sqlite3
 with sqlite3.connect(case/'fresh-ops.sqlite') as c:
  assert c.execute('SELECT count(*) FROM operation_monitor_window_completion').fetchone()[0]==1
  assert c.execute('PRAGMA foreign_key_check').fetchall()==[]


def test_recovery_source_requires_explicit_execution_argument():
 child=subprocess.run([PYTHON,'-B',str(ROOT/'scripts/flex_v2_fresh_pair_recovery.py')],capture_output=True,text=True)
 assert child.returncode!=0 and 'EXPLICIT_RECOVERY_EXECUTION_REQUIRED' in child.stderr
