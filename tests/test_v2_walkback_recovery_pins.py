"""Exact renderer/pin contracts; disposable pairs, blocked network, no runtime writes."""
import ast,copy,hashlib,json,pathlib,subprocess,types
import pytest
from tests.test_v2_helius_recovery_config import case,base_case,target,activate
from tests.test_v2_terminal_recovery_config import seed_and_reclaim
from tests.test_v2_pending_recovery import prepare
ROOT=pathlib.Path(__file__).resolve().parents[1]
RENDERER=ROOT/'docs/dev/lightweight_dev004_first_v2/quarantine_continuation/production_control.py'
OLD='0f52420bf82a0ba6090ab98f6630a36278318988d1af1a8f6165a19b7ca9d2d7'
NEW='75a069a5c1ff0859550b929a2f1f2ce2a719dd109bb5ffbb5dcead25e5736414'
BASE='c4ae598c7db29147a17b43f95cc59fc8eb5304d3'

def test_only_required_renderer_pin_changes():
 before=ast.parse(subprocess.check_output(['git','show','b203c114:'+str(RENDERER.relative_to(ROOT))],cwd=ROOT,text=True));after=ast.parse(RENDERER.read_text())
 def pins_node(tree):return next(n for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='HELIUS_GATE_PAYLOAD' for t in n.targets))
 b=pins_node(before);a=pins_node(after);old=ast.literal_eval(b.value);new=ast.literal_eval(a.value)
 assert old['listener']==new['listener'] and old['creator_resolution']==new['creator_resolution']
 for relative in ['src/core/walkback_worker.py']:
  old['walkback'][relative]=hashlib.sha256((ROOT/'runtime/walkback'/relative).read_bytes()).hexdigest()
 assert old==new
 b.value=a.value
 assert ast.dump(before)==ast.dump(after)
 assert hashlib.sha256((ROOT/'runtime/walkback/src/core/walkback_worker.py').read_bytes()).hexdigest()==NEW

def test_candidate_profile_restarts_and_readbacks(case):
 e=case;assert e.ns['HELIUS_GATE_PAYLOAD']['walkback']['src/core/walkback_worker.py']==NEW
 before=e.gate.read();e.ns['helius_module']('walkback');e.ns['write_config']('v2');first=e.live.read_bytes();e.ns['write_config']('v2')
 assert e.live.read_bytes()==first and e.gate.read()==before
 assert e.output_env()['FLEX_HELIUS_DENIAL_GATE_ENABLED']=='1'

@pytest.mark.parametrize('defect',['old_walkback','gate_unaware','mixed_gate','mixed_listener'])
def test_new_profile_rejects_stale_and_mixed_sources(case,defect):
 e=case;wb=pathlib.Path(e.plan['roles']['walkback']['package'])/'src/core';before=e.live.read_bytes()
 if defect=='old_walkback':(wb/'walkback_worker.py').write_bytes(subprocess.check_output(['git','show','b203c114:runtime/walkback/src/core/walkback_worker.py'],cwd=ROOT))
 elif defect=='gate_unaware':(wb/'walkback_worker.py').write_bytes(b'# old unguarded source')
 elif defect=='mixed_gate':(wb/'helius_denial_gate.py').write_bytes(b'# wrong gate')
 else:(e.package/'src/core/pumpfun_curve_listener.py').write_bytes(b'# stale listener')
 with pytest.raises(RuntimeError,match='TERMINAL_UNAWARE_SOURCE_FALLBACK' if defect=='mixed_listener' else 'HELIUS_GATE_UNAWARE_FALLBACK'):e.ns['write_config']('v2')
 assert e.live.read_bytes()==before and not e.controls

def test_explicit_old_profile_fallback_remains_gate_aware(case):
 e=case;tree=ast.parse(subprocess.check_output(['git','show',BASE+':'+str(RENDERER.relative_to(ROOT))],cwd=ROOT,text=True));node=next(n for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='HELIUS_GATE_PAYLOAD' for t in n.targets));oldpins=ast.literal_eval(node.value)
 wb=pathlib.Path(e.plan['roles']['walkback']['package'])/'src/core';(wb/'walkback_worker.py').write_bytes(subprocess.check_output(['git','show',BASE+':runtime/walkback/src/core/walkback_worker.py'],cwd=ROOT));e.ns['HELIUS_GATE_PAYLOAD']=oldpins
 before=e.gate.read();e.ns['write_config']('v2');assert e.gate.read()==before and e.output_env()['FLEX_HELIUS_DENIAL_GATE_ENABLED']=='1'
 (wb/'helius_http_protection.py').write_bytes(b'# no gate')
 with pytest.raises(RuntimeError,match='HELIUS_GATE_UNAWARE_FALLBACK'):e.ns['write_config']('v2')

def test_fresh_pair_v2_budget_terminal_deferred_and_restart(case,monkeypatch):
 e=case;pending_module=prepare(e,monkeypatch);terminal,pending=seed_and_reclaim(e)
 policy=json.loads((ROOT/'docs/dev/lightweight_dev004_first_v2/helius_investigation_budget/POLICY.json').read_text());e.gate.monotonic=lambda:1000;e.gate.configure_budget(policy,hashlib.sha256(e.gate.path.read_bytes()).hexdigest());root=e.gate.register_origin('mint:fixture','1'*64);e.gate.bind_child(root,'2'*64,'3'*64)
 with e.gate.locked():s=e.gate.read();s['state']='OPEN';e.gate.save(s)
 ticket=e.gate.reserve_http(role='walkback',scopes=('2'*64,),key='4'*64,selector='rpc:getTransaction',origin='5'*64);e.gate.finish_http(ticket,200)
 e.gate.reserve_http(role='walkback',scopes=('2'*64,),key='6'*64,selector='rpc:getTransaction',origin='5'*64)
 with e.gate.locked():s=e.gate.read();s['state']='PAYMENT_DENIED';e.gate.save(s)
 before=e.gate.read();e.ns['write_config']('v2');paths,manifest,destination=target(e);e.running[0]=False;e.ns['freeze_helius_recovery'](e.ns['prepare_helius_recovery'](e.env,destination));env=activate(e,paths,manifest,destination,monkeypatch);e.ns['finish_helius_recovery'](env)
 fresh=e.module.Gate(e.gate.path,'fresh');assert fresh.read()==dict(before,generation='fresh');assert fresh.investigation_owner('2'*64)==root;assert fresh.read()['rpc_budget']['policy']['lifetime_credits']==100000
 deferred=pending_module.DeferredMigrationStore(paths['ops'],e.journal.path,generation='fresh',validate_record=e.journal._validate,validate_pair=lambda p:e.g.validate_pair(core_path=env['DB_PATH'],ops_path=str(p)));assert deferred.lookup('deferred',owned_only=True)==e.pending_row
 owner=e.tm.TerminalEvidenceStore(paths['ops'],e.journal.path,generation='fresh',validate_record=e.journal._validate,validate_pair=lambda p:e.g.validate_pair(core_path=env['DB_PATH'],ops_path=str(p)));assert owner.lookup(terminal['signature'])==terminal
 e.ns['settings']=lambda _:dict(env)
 for _ in range(3):
  e.running[0]=False;e.ns['write_config']('v2');e.ns['finish_helius_recovery'](env);assert fresh.read()==dict(before,generation='fresh');assert e.output_env()['DEV004_MIGRATION_DEFERRED_EVIDENCE_ENABLED']=='1'
 assert e.journal.snapshot()==[pending]

@pytest.mark.parametrize('defect',['wrong_commit','dirty','symlink'])
def test_exact_immutable_source_authority_rejects_ambiguity(tmp_path,defect):
 tree=ast.parse(RENDERER.read_text());fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='qualified_source_root');root=tmp_path/'source';root.mkdir();path=root
 if defect=='symlink':path=tmp_path/'alias';path.symlink_to(root,target_is_directory=True)
 fake=types.SimpleNamespace(check_output=lambda *a,**k:'a'*40+'\n',call=lambda *a,**k:int(defect=='dirty'));ns={'pathlib':pathlib,'subprocess':fake};exec(compile(ast.Module(body=[fn],type_ignores=[]),str(RENDERER),'exec'),ns)
 with pytest.raises(RuntimeError):ns['qualified_source_root']({'immutable_source_root':str(path),'immutable_source_commit':('b' if defect=='wrong_commit' else 'a')*40})


@pytest.mark.parametrize('relative',['src/ops/anchor_reconciliation.py'])
def test_missing_or_stale_progress_dependencies_fail_closed(case,relative):
 e=case;path=pathlib.Path(e.plan['roles']['walkback']['package'])/relative;path.write_text('# incompatible source')
 before=e.live.read_bytes()
 with pytest.raises(RuntimeError,match='HELIUS_GATE_UNAWARE_FALLBACK'):e.ns['write_config']('v2')
 assert e.live.read_bytes()==before


def test_fresh_compact_pair_preserves_continuation_metadata(case,monkeypatch):
 import sqlite3,importlib.util
 e=case
 spec=importlib.util.spec_from_file_location('qualified_progress_owner',ROOT/'runtime/walkback/src/ops/anchor_reconciliation.py');owner=importlib.util.module_from_spec(spec);spec.loader.exec_module(owner)
 with sqlite3.connect(e.ops) as conn:
  # This fixture has only custody tables; add the minimal synthetic queue.
  conn.execute("CREATE TABLE IF NOT EXISTS wt_walkback_queue(mint TEXT PRIMARY KEY,status TEXT,path_state TEXT,enqueued_at INTEGER)")
  owner.ensure_schema(conn)
  conn.execute("INSERT INTO wt_anchor_reconciliation_cursor_v1 VALUES(1,10,'mint-a',20,'mint-z')")
 paths,manifest,destination=target(e)
 with sqlite3.connect(paths['ops']) as conn:
  assert conn.execute('SELECT * FROM wt_anchor_reconciliation_cursor_v1').fetchone()==(1,10,'mint-a',20,'mint-z')
 before=e.gate.read();e.ns['freeze_helius_recovery'](e.ns['prepare_helius_recovery'](e.env,destination));env=activate(e,paths,manifest,destination,monkeypatch);e.ns['finish_helius_recovery'](env)
 assert e.module.Gate(e.gate.path,'fresh').read()==dict(before,generation='fresh')
 e.ns['settings']=lambda _:dict(env);e.ns['write_config']('v2')
 assert e.output_env()['FLEX_HELIUS_DENIAL_GATE_ENABLED']=='1'
