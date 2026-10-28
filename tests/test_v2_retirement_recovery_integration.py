"""Retirement opt-in through existing recovery and dormant configuration owners."""
import hashlib,json,pathlib,sqlite3,os
import pytest
from tests.test_v2_helius_recovery_config import case as helius_case,base_case,target
from tests.test_v2_terminal_recovery_config import seed_and_reclaim
from tests.test_v2_dormant_runtime import case as dormant_case,probe
from tests.test_v2_predecessor_retirement import load,save,digest
ROOT=pathlib.Path(__file__).resolve().parents[1]

def original_predecessor(g,pair_path,owner_path,env,monkeypatch):
 # Only disposable fixtures are modified; no source or production authority.
 m=json.loads(pair_path.read_text());old={r:pair_path.parent/('legacy-'+r+'.sqlite') for r in ['core','ops']}
 for role,p in old.items():
  with sqlite3.connect(p) as c:c.execute('CREATE TABLE old_evidence(id TEXT PRIMARY KEY)')
  save(pathlib.Path(str(p)+'.retired'),{'version':1,'path':str(p),'generation':'legacy','retired':True})
 m['authority']['origin_generation']='legacy';m['predecessor']={'generation':'legacy','stores':{r:g.database_identity(str(p),'legacy',r) for r,p in old.items()}};save(pair_path,m)
 state=json.loads(owner_path.read_text());state.update(origin_generation='legacy',manifest_sha256=digest(pair_path));save(owner_path,state)
 for v in m['stores'].values():pathlib.Path(v['path']+'.pair-bound').unlink()
 g.seal_manifest(pair_path)
 for k,v in env.items():monkeypatch.setenv(k,v)
 for key in [g.RETIREMENT_PATH_ENV,g.RETIREMENT_SHA_ENV]:monkeypatch.delenv(key,raising=False)
 cert=g.publish_predecessor_retirement(pair_path,pathlib.Path(str(owner_path)+'.predecessor-retirement-v1.json'),authorize=True)
 binding={g.RETIREMENT_PATH_ENV:cert['path'],g.RETIREMENT_SHA_ENV:cert['sha256']}
 for k,v in binding.items():monkeypatch.setenv(k,v)
 for p in old.values():p.unlink()
 assert g.validate_pair()==m['generation']
 return m,binding

def test_real_recovery_preserves_spending_uncertainty_custody_and_configuration(helius_case,monkeypatch):
 e=helius_case;e.pair,binding=original_predecessor(e.g,pathlib.Path(e.env['FLEX_DB_PAIR_MANIFEST']),pathlib.Path(e.env['FLEX_DB_PAIR_AUTHORITY']),e.env,monkeypatch);e.env.update(binding);e.plan['planned_production_environment'].update(binding)
 protected,pending=seed_and_reclaim(e)
 from tests.test_v2_pending_recovery import prepare
 deferred_module=prepare(e,monkeypatch)
 with sqlite3.connect(e.core) as c:
  for table in ['creator_resolution_queue','creator_funding_queue']:
   c.execute('CREATE TABLE '+table+'(id TEXT PRIMARY KEY,status TEXT,attempts INT,next_retry_at INT,locked_until INT,locked_by TEXT)');c.execute('INSERT INTO '+table+' VALUES(?,?,?,?,?,?)',('retained','running',2,500,9999,'old-owner'))
 with sqlite3.connect(e.ops) as c:
  c.execute('CREATE TABLE wt_walkback_queue(mint TEXT PRIMARY KEY,status TEXT,attempts INT,next_retry_at INT,lease_expires_at INT,claimed_by TEXT,create_anchor_signature TEXT,intelligence_outcome TEXT,attribution_source TEXT)');c.execute('INSERT INTO wt_walkback_queue VALUES(?,?,?,?,?,?,?,?,?)',('retained','running',2,500,9999,'old-owner','qualified-CREATE','UNCHANGED','UNCHANGED'))
 policy=json.loads((ROOT/'docs/dev/lightweight_dev004_first_v2/helius_investigation_budget/POLICY.json').read_text());e.gate.monotonic=lambda:1000;e.gate.configure_budget(policy,hashlib.sha256(e.gate.path.read_bytes()).hexdigest());origin=e.gate.register_origin('mint:retirement-fixture','a'*64)
 with e.gate.locked():
  state=e.gate.read();q=state['rpc_budget']['scopes'][origin];q.update(credits=73920,requests=73920,warned=True);q['workers']['listener']=73920
  for i in range(5):
   key=hashlib.sha256(str(i).encode()).hexdigest();state['rpc_budget']['keys'][key]={'attempts':1,'status':'OWNER_EXIT_UNCERTAIN','at':10,'selector':'rpc:getTransaction','role':'listener','pid':99999999,'credits':1,'owner_exit':{'ticket':str(i)*32,'root':origin,'pid':99999999,'original_at':10,'retired_at':11,'reason':'PROCESS_GONE_OUTCOME_UNKNOWN'}}
  e.gate.save(state)
 paths,m,dest=target(e);e.ns['freeze_helius_recovery'](e.ns['prepare_helius_recovery'](e.env,dest));before=e.gate.read()
 for p in [e.core,e.ops]:save(pathlib.Path(str(p)+'.retired'),{'version':1,'path':str(p),'generation':'source','retired':True})
 state={'version':1,'authority_id':m['authority']['id'],'origin_generation':'legacy','generation':'fresh','manifest_path':str(dest),'manifest_sha256':digest(dest),'phase':'ACTIVE','owner':'runtime','transfer_complete':True};save(e.root/'authority/owner.json',state);e.g.seal_manifest(dest)
 env=dict(e.env,DB_PATH=str(paths['core']),WT_OPS_DB_PATH=str(paths['ops']),FLEX_DB_PAIR_MANIFEST=str(dest))
 for k,v in env.items():monkeypatch.setenv(k,v)
 assert e.g.validate_pair()=='fresh'
 from tests.test_v2_fresh_pair_walkback_claims import run as restore_claims
 class Target:
  def __truediv__(self,name):return paths[name.split('.')[0]]
 restore_claims(Target())
 with sqlite3.connect(paths['ops']) as c:assert c.execute('SELECT status,attempts,next_retry_at,create_anchor_signature,intelligence_outcome,attribution_source FROM wt_walkback_queue').fetchone()==('pending',2,500,'qualified-CREATE','UNCHANGED','UNCHANGED')
 deferred=deferred_module.DeferredMigrationStore(paths['ops'],e.journal.path,generation='fresh',validate_record=e.journal._validate,validate_pair=lambda p:e.g.validate_pair(core_path=env['DB_PATH'],ops_path=str(p)))
 assert deferred.lookup('deferred',owned_only=True)==e.pending_row
 e.ns['finish_helius_recovery'](env);fresh=e.module.Gate(e.gate.path,'fresh');assert fresh.read()==dict(before,generation='fresh');assert fresh.read()['rpc_budget']['policy']['lifetime_credits']==100000;assert fresh.read()['rpc_budget']['scopes'][origin]['credits']==73920;assert sum(v['status']=='OWNER_EXIT_UNCERTAIN' for v in fresh.read()['rpc_budget']['keys'].values())==5
 # Existing renderer carries the exact same optional pin; no source rewrite.
 e.running[0]=False;e.ns['settings']=lambda mode:dict(env);e.ns['write_config']('v2');rendered=e.output_env()
 for k,v in binding.items():assert rendered[k]==v
 e.ns['finish_helius_recovery'](env);assert fresh.read()==dict(before,generation='fresh')
 assert protected and pending

@pytest.mark.parametrize('role',['api','monitor','bridge'])
def test_dormant_startup_contract_accepts_retired_predecessor_without_acquisition(dormant_case,monkeypatch,role):
 p=dormant_case/'manifest.json';profile=json.loads(p.read_text());g=load();m,binding=original_predecessor(g,pathlib.Path(profile['environment']['FLEX_DB_PAIR_MANIFEST']),pathlib.Path(profile['environment']['FLEX_DB_PAIR_AUTHORITY']),profile['environment'],monkeypatch);profile['environment'].update(binding);save(p,profile)
 before=(dormant_case/'queue/provider_budget.json').read_bytes();result,child=probe(dormant_case,role);assert child.returncode==0 and result['status']=='PASS',result;assert result['blocked']==[];assert (dormant_case/'queue/provider_budget.json').read_bytes()==before
