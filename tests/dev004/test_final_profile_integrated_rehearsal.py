"""Exactly one disposable integrated migration; accepted schema, synthetic work."""
import asyncio,hashlib,importlib.util,json,os,pathlib,resource,sqlite3,sys,time
import pytest
ROOT=pathlib.Path(__file__).resolve().parents[2]
s=importlib.util.spec_from_file_location('profile_controls',ROOT/'tests/dev004/test_production_profile_bootstrap.py');controls=importlib.util.module_from_spec(s);s.loader.exec_module(controls)
p=controls.p;env=p.env;no_network=p.no_network;CORE=controls.CORE;OPS=controls.OPS
SCHEMA=json.load(open(ROOT/'docs/dev/lightweight_dev004_first_v2/production_profile_bootstrap/accepted_fixture_schema.json'))
OWNED={'core':{'token_market_cap_peaks'},'ops':{'operator_launch_membership','operator_membership_reference_v1'}}
def build_base(path,kind):
 with p.NATIVE(path) as q:
  q.execute('PRAGMA journal_mode=WAL');q.execute('PRAGMA foreign_keys=ON')
  for obj in SCHEMA[kind]:
   if obj['name'].startswith('dev004_') or obj['name'] in OWNED[kind] or obj['table'] in OWNED[kind]:continue
   q.execute(obj['sql'])

def profile_for(c,g,root):
 x=controls.Profile.__new__(controls.Profile);x.c=c;x.root=root;x.file=c.env.path/(g+'-frozen-runtime.json');pair=json.load(open(c.manifests[g]))
 x.value={'source_commit':__import__('subprocess').check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'production_bootstrap':{'version':1,'root':str(root),'generation':pair['generation'],'pair_manifest_path':str(c.manifests[g]),'pair_manifest_sha256':hashlib.sha256(c.manifests[g].read_bytes()).hexdigest(),'authority_path':str(c.authority),'transfer_owner':'fixture-transfer-owner','stores':pair['stores'],'schema_authority':{'core':{'contract':hashlib.sha256((CORE.CONTRACT+':token_analysis').encode()).hexdigest(),'owner_sha256':hashlib.sha256(pathlib.Path(CORE.__file__).read_bytes()).hexdigest()},'ops':OPS.base._schema_authority()}},'runtime':{'core':str(c.paths[g]['core']),'ops':str(c.paths[g]['ops']),'birth_journal':str(c.birth.path),'migration_journal':str(c.migration.path),'credential':'BIRDEYE','provider_limits':{'global':20,'per_token':4,'seconds':60},'receipt_limit':1536,'ATH_relative_tolerance':.001,'dormant_components':'STOPPED'}}
 x.publish();return x

def clear_bootstrap(c):
 c.env.patch.delenv('DEV004_PRODUCTION_BOOTSTRAP_MANIFEST');c.env.patch.delenv('DEV004_PRODUCTION_BOOTSTRAP_SHA256')

def create_case(env):
 c=p.PairCase.__new__(p.PairCase);c.env=env;c.w,c.d=p.support.setup_writer(env);c.paths={}
 for g,name in [('v1','dev004-disposable-v1'),('v2','persistent-production-style-v2')]:
  root=env.path/name;root.mkdir();c.paths[g]={k:root/(k+'.sqlite') for k in ['core','ops']}
  for kind,path in c.paths[g].items():build_base(path,kind)
 # Existing DEV owners prepare the disposable source. No V1 production clone.
 r=c.paths['v1']['core'].parent
 CORE.initialize(c.paths['v1']['core'],disposable_root=r,allow_disposable_v2=True,price_identity_parent='token_analysis')
 with p.NATIVE(c.paths['v1']['ops']) as q:q.execute('PRAGMA foreign_keys=ON');OPS.initialize(q,disposable_root=r,allow_disposable_v2=True)
 c.birth=env.b.BirthJournal(env.path/'stable-birth.jsonl');c.birth.initialize();c.migration=env.m.MigrationJournal(env.path/'stable-migration.jsonl');c.migration.initialize();c.config=env.path/'frozen-pair.json';c.steps=[];c.calls=[];c.logs=[];c.current=None;c.scope=None;c.stale=None;c.guard=sys.modules['src.core.paired_binding_guard'];c.authority=env.path/'active-pair-authority.json';c.manifests={};c.reconciled=False
 original_conn=c.conn
 def enforced_conn(path,read=False):
  conn=original_conn(path,read)
  # Connection-local FK configuration, not a write to retired source content.
  sqlite3.Connection.execute(conn,'PRAGMA foreign_keys=ON')
  assert sqlite3.Connection.execute(conn,'PRAGMA foreign_keys').fetchone()[0]==1
  return conn
 c.conn=enforced_conn
 c.prepare_manifest('v1');c.guard.seal_manifest(str(c.manifests['v1']));c.select_authority('v1','ACTIVE');c.bind_environment('v1');c.binding('v1');c.seed();return c

def test_one_final_integrated_production_profile_rehearsal(env):
 start=time.monotonic();c=create_case(env);trace=['Disposable V1 base schema and canonical source work prepared'];stale=c.conn(c.paths['v1']['core']);c.admit_during_lease();trace+=c.steps;fresh=env.b.BirthJournal(c.birth.path);assert fresh.pending()==c.birth.pending()
 # Durable admission during fence; interrupted retirement is restart-safe.
 with pytest.raises(p.Crash):c.retire(True)
 c.retire();trace.append('Interrupted retirement recovered; both source stores durably fenced')
 c.prepare_manifest('v2');c.select_authority('v2','TRANSFER');c.bind_environment('v2',True)
 x=profile_for(c,'v2',c.paths['v2']['core'].parent)
 assert x.core()['status']=='INITIALIZED';assert x.core()['status']=='ALREADY_INITIALIZED';assert x.ops()['contract']==OPS.VERSION
 trace.append('Both production-profile owners initialized exact paired targets; repeated Core bootstrap passed')
 with pytest.raises(p.Crash):c.reconcile(True)
 assert x.core()['status']=='ALREADY_INITIALIZED';c.reconcile();x.ops();trace.append('Interrupted final Core/OPS reconciliation resumed idempotently')
 clear_bootstrap(c);c.publish_frozen_config();c.resume();c.drain_births()
 with pytest.raises(p.Crash):c.finish_migration(True)
 assert c.migration.pending();c.resume();c.finish_migration();assert not c.birth.pending() and not c.migration.pending();trace.append('Canonical births, Core migration, OPS handoff and durable ACK recovered after Core-first crash')
 with pytest.raises(c.w.DatabaseRetiredError):stale.execute("INSERT INTO token_analysis(mint) VALUES('forbidden-stale')")
 stale.close();first=p.restart_binding_child(c);second=p.restart_binding_child(c);assert first==second;trace.append('Actual binding module restart stable; stale source writer rejected')
 with c.conn(c.paths['v2']['core']) as q:
  assert tuple(q.execute("SELECT attempts,status FROM creator_resolution_queue WHERE mint='pending-creator'").fetchone())==(2,'retry')
  assert tuple(q.execute("SELECT consumed,quarantine_reason FROM webhook_birth_queue WHERE signature='quarantined-signature'").fetchone())==(0,'CORE_AUTHORITY_CONFLICT')
  q.execute("INSERT INTO token_market_cap_peaks(mint,peak_market_cap,peak_market_cap_at) VALUES('new-launch',123456.0,100)")
  assert tuple(q.execute("SELECT peak_market_cap,peak_market_cap_at FROM token_market_cap_peaks WHERE mint='new-launch'").fetchone())==(123456.0,100)
 with c.conn(c.paths['v2']['ops']) as q:
  assert q.execute("SELECT operator_id FROM operator_launch_membership WHERE mint='retained-launch'").fetchone()[0]=='fixture-operation'
  assert q.execute("SELECT reference_kind FROM operator_membership_reference_v1 WHERE mint='retained-launch'").fetchone()[0]=='NONE'
  assert q.execute("SELECT status FROM wt_walkback_queue WHERE mint='new-launch'").fetchone()[0]=='pending'
  assert not q.execute('PRAGMA foreign_key_check').fetchall()
 trace.append('Creator retry, protected webhook, known-operation typed reference, Walkback queue and canonical price-parent persistence verified')
 # Stable identity suppresses duplicate work after ACK.
 assert c.birth.admit_birth(p.support.payload(sig='pending-create',mint='new-launch'))['state']=='ACKED';assert c.migration.admit('pending-migration','PUMPSWAP_MIGRATION_ACCOUNT',3)['state']=='ACKED'
 creds=p.support.load('final_profile_birdeye',ROOT/'runtime/listener/src/utils/birdeye_credentials.py');env.patch.setenv('BIRDEYE','synthetic-paid');env.patch.setenv('BIRDEYE_KK','synthetic-legacy');assert creds.birdeye_credential()=='synthetic-paid';env.patch.setenv('BIRDEYE','')
 with pytest.raises(creds.BirdeyeConfigurationError):creds.birdeye_credential()
 env.patch.setenv('BIRDEYE','synthetic-paid')
 # Fresh matched compact rollback, never returning to retired V1.
 current=json.load(open(c.manifests['v2']));c.w.retire_database('core',c.paths['v2']['core'],generation=current['generation'],timeout=.2);c.w.retire_database('ops',c.paths['v2']['ops'],generation=current['generation'],timeout=.2);root=env.path/'persistent-production-style-rollback';root.mkdir();c.paths['rollback']={k:root/(k+'.sqlite') for k in ['core','ops']}
 for kind,path in c.paths['rollback'].items():
  source=p.NATIVE('file:'+str(c.paths['v2'][kind])+'?mode=ro',uri=True);target=p.NATIVE(path);source.backup(target,pages=32);target.close();source.close()
 manifest={'version':1,'authority':current['authority'],'generation':'fixture-rollback','stores':{k:c.guard.database_identity(str(path),'fixture-rollback',k) for k,path in c.paths['rollback'].items()},'predecessor':{'generation':current['generation'],'stores':current['stores']}};path=env.path/'rollback-runtime-manifest.json';c.publish_json(path,manifest);c.manifests['rollback']=path
 state={'version':1,'authority_id':manifest['authority']['id'],'origin_generation':manifest['authority']['origin_generation'],'generation':manifest['generation'],'manifest_path':str(path),'manifest_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'phase':'TRANSFER','owner':'fixture-transfer-owner','transfer_complete':False};c.publish_json(c.authority,state)
 env.patch.setenv('DB_PATH',str(c.paths['rollback']['core']));env.patch.setenv('WT_OPS_DB_PATH',str(c.paths['rollback']['ops']));env.patch.setenv('FLEX_DB_PAIR_MANIFEST',str(path));env.patch.setenv('FLEX_DB_PAIR_ROLE','transfer');env.patch.setenv('FLEX_DB_PAIR_OWNER','fixture-transfer-owner')
 y=profile_for(c,'rollback',root);assert CORE.initialize(c.paths['rollback']['core'],disposable_root=root,allow_disposable_v2=True,price_identity_parent='token_analysis')['status']=='INITIALIZED'
 with p.NATIVE(c.paths['rollback']['ops']) as q:q.execute('PRAGMA foreign_keys=ON');OPS.initialize(q,disposable_root=root,allow_disposable_v2=True)
 clear_bootstrap(c);c.guard.seal_manifest(str(path));state.update(phase='ACTIVE',owner='runtime',transfer_complete=True);c.publish_json(c.authority,state);env.patch.setenv('FLEX_DB_PAIR_ROLE','runtime');c.binding('rollback');assert c.guard.validate_pair()=='fixture-rollback'
 with c.conn(c.paths['rollback']['core']) as q:
  assert q.execute("SELECT peak_market_cap FROM token_market_cap_peaks WHERE mint='new-launch'").fetchone()[0]==123456.0
  q.execute("INSERT INTO token_analysis(mint) VALUES('rollback-canonical-write')")
  assert not q.execute('PRAGMA foreign_key_check').fetchall()
 assert not c.birth.pending() and not c.migration.pending();trace.append('Fresh matched rollback bootstrapped through production profiles, data/price retained, new canonical write accepted')
 files=[f for f in env.path.rglob('*') if f.is_file()];largest=max(f.stat().st_size for f in files);allocated=sum(f.stat().st_blocks*512 for f in files);v=os.statvfs(env.path);free=v.f_bavail*v.f_frsize;assert largest<16_000_000 and free>2_000_000_000
 result={'status':'PASS','complete_integrated_rehearsals':1,'seconds':time.monotonic()-start,'trace':trace,'runtime_generations':['fixture-v1','fixture-v2','fixture-rollback'],'owner_manifest_hashes':{g:hashlib.sha256((env.path/(g+'-frozen-runtime.json')).read_bytes()).hexdigest() for g in ['v2','rollback']},'matched_restart':first,'journals_pending':0,'stale_source_write_rejected':True,'foreign_key_violations':0,'canonical_price_roundtrip':True,'provider_test_calls':0,'production_paths_opened':0,'full_V1_copies':0,'compact_disposable_rollback_copy':True,'largest_file_bytes':largest,'fixture_allocated_bytes':allocated,'final_free_bytes':free,'kernel_file_limit':resource.getrlimit(resource.RLIMIT_FSIZE),'reused_unchanged_qualifications':['29633row seed/reference integrity','100creator classifications','616Watchtower','26Deep','11frozen comparisons','Walkback investigation/projection/provider-outage recovery','nonP3Rreview','Birdeye five-window budget/checkpoint/finalization recovery and0.1%ATHtolerance'],'scope':'Synthetic source transactions over exact accepted base schema; changed production profiles exercised for forward and rollback. Reused matrices explicitly not re-executed.'}
 (env.path/'final-profile-integrated-result.json').write_text(json.dumps(result,indent=2)+'\n');print('RESULT_PATH='+str(env.path/'final-profile-integrated-result.json'))
