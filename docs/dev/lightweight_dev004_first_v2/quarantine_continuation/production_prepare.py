import configparser,hashlib,importlib.util,json,os,pathlib,resource,shutil,signal,sqlite3,subprocess,sys
R=pathlib.Path('/Users/kevinkeaveney/.codex/worktrees/dev004-first-v2-prototype/flex');STATE=pathlib.Path('/Users/kevinkeaveney/Dev/claude/flex/.runtime/dev004-v2');STAGE=pathlib.Path('/private/tmp/dev004-production-compact-stage-20261024');CONF=pathlib.Path('/Users/kevinkeaveney/Dev/claude/flex-development/DEV090/operational-restoration/supervisord.selective-retry2.conf')
resource.setrlimit(resource.RLIMIT_FSIZE,(499000000,499000000));signal.signal(signal.SIGXFSZ,signal.SIG_IGN)
SOURCE=subprocess.check_output(['git','rev-parse','HEAD'],cwd=R,text=True).strip();assert SOURCE.startswith('dfb76562')
plan=json.load(open(R/'docs/dev/lightweight_dev004_first_v2/journal_capacity_reuse/runtime_plan.json'))
def sha(p):return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()
def publish(p,value):
 data=(json.dumps(value,sort_keys=True,indent=2)+'\n').encode();assert len(data)<2_000_000
 tmp=p.with_suffix(p.suffix+'.tmp');fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 try:assert os.write(fd,data)==len(data);os.fsync(fd)
 finally:os.close(fd)
 os.replace(tmp,p);fd=os.open(p.parent,os.O_RDONLY)
 try:os.fsync(fd)
 finally:os.close(fd)
assert not STATE.exists(),'UNEXPECTED_STATE_ROOT';assert shutil.disk_usage(STATE.parent.parent).free>2_000_000_000+256*1024*1024
for rel,x in plan['deployment_payload'].items():
 p=pathlib.Path(x['target']);actual=sha(p) if p.exists() else None;assert actual==x['preimage_sha256'],str(p);assert sha(R/rel)==x['sha256'],rel
STATE.parent.mkdir(mode=0o700,exist_ok=True);STATE.mkdir(mode=0o700);os.chmod(STATE,0o700)
for n in ['databases','journals','authority','reference','rollback','tmp','checkpoints','source-preimages','source-payload']:(STATE/n).mkdir(mode=0o700)
# Only already compact staged V2 files. Never V1 copies.
for role in ['core','ops']:
 src=STAGE/(role+'.sqlite');assert src.stat().st_size<64*1024*1024;shutil.copy2(src,STATE/'databases'/(role+'.sqlite'))
# Required accepted source index for restart deduplication, compact Core only.
src=sqlite3.connect('file:/Users/kevinkeaveney/Dev/claude/flex/database/flex_complete_database.db?mode=ro',uri=True);ddl=src.execute("select sql from sqlite_master where name='idx_psi0a_token_analysis_migration_tx'").fetchone()[0];src.close()
c=sqlite3.connect(STATE/'databases/core.sqlite');c.execute(ddl);c.commit();c.close()
for rel,x in plan['deployment_payload'].items():
 target=pathlib.Path(x['target']);name=hashlib.sha256(str(target).encode()).hexdigest()+'.py'
 if target.exists():shutil.copy2(target,STATE/'source-preimages'/name);assert sha(STATE/'source-preimages'/name)==x['preimage_sha256']
 shutil.copyfile(R/rel,STATE/'source-payload'/name);assert sha(STATE/'source-payload'/name)==x['sha256'];x['staged_name']=name
# Never print or publish Supervisor credential-containing bytes.
shutil.copyfile(CONF,STATE/'source-preimages/supervisor.conf');os.chmod(STATE/'source-preimages/supervisor.conf',0o600)
assets=json.load(open(R/'docs/dev/lightweight_dev004_first_v2/production_bundle_qualification/bundle_manifest.json'))['auxiliary_flags']
for key,name in [('DEV004_CREATOR_HISTORY_PATH','creator_history.json'),('LIVING_V2_FRESHNESS_MAPPING_PATH','freshness_mapping.json')]:
 a=assets[key];assert sha(a['retained_qualified_artifact'])==a['sha256'];shutil.copyfile(a['retained_qualified_artifact'],STATE/'reference'/name);os.chmod(STATE/'reference'/name,0o600)
sys.path.insert(0,str(R/'runtime/listener'));from src.core import paired_binding_guard as guard
origin='DEV004-V1-20261024';generation='DEV004-V2-20261024-'+SOURCE[:8];owner=STATE/'authority/owner.json'
paths={'core':pathlib.Path('/Users/kevinkeaveney/Dev/claude/flex/database/flex_complete_database.db'),'ops':pathlib.Path('/Users/kevinkeaveney/Dev/claude/flex/database/wt_ops_v2.db')}
v1={'version':1,'authority':{'id':'DEV004-production-transfer-20261024','path':str(owner),'origin_generation':origin},'generation':origin,'stores':{k:guard.database_identity(str(p),origin,k) for k,p in paths.items()},'predecessor':None};publish(STATE/'authority/v1-pair.json',v1)
v2={'version':1,'authority':v1['authority'],'generation':generation,'stores':{k:guard.database_identity(str(STATE/'databases'/(k+'.sqlite')),generation,k) for k in paths},'predecessor':{'generation':origin,'stores':v1['stores']}};publish(STATE/'authority/pair.json',v2)
aux=pathlib.Path('/Users/kevinkeaveney/Dev/claude/flex/pumpswap_tokens.db')
for name,pairfile,pair in [('v1-auxiliary.json','v1-pair.json',v1),('auxiliary.json','pair.json',v2)]:
 value={'version':1,'generation':pair['generation'],'pair_manifest_sha256':sha(STATE/'authority'/pairfile),'stores':{'creator_repository':guard.database_identity(str(aux),pair['generation'],'creator_repository')}};publish(STATE/'authority'/name,value)
from src.core.birth_admission_journal import BirthJournal
from src.core.migration_signature_journal import MigrationJournal
BirthJournal(STATE/'journals/birth.jsonl').initialize();MigrationJournal(STATE/'journals/migration.jsonl').initialize()
plan.update(immutable_source_commit=SOURCE,generation=generation,status='PRODUCTION_STAGED_NOT_ACTIVATED',production_changes={'new_compact_destinations_created':True,'source_installation':False,'service_actions':0,'authority_switched':False},source_generation=origin,original_supervisor_sha256=sha(CONF),v1_pair_path=str(STATE/'authority/v1-pair.json'),v1_pair_sha256=sha(STATE/'authority/v1-pair.json'),v2_pair_sha256=sha(STATE/'authority/pair.json'),quarantine_checkpoint=str(STATE/'checkpoints/quarantine.json'),quarantine_tool={'path':str(R/'runtime/listener/src/ops/dev004_quarantine_transfer.py'),'sha256':sha(R/'runtime/listener/src/ops/dev004_quarantine_transfer.py')},migration_tx_index=ddl)
plan['planned_production_environment']['FLEX_DB_AUXILIARY_SHA256']=sha(STATE/'authority/auxiliary.json')
plan['runtime_trees']={k:subprocess.check_output(['git','rev-parse',SOURCE+':runtime/'+k],cwd=R,text=True).strip() for k in plan['runtime_trees']}
publish(STATE/'authority/frozen-runtime-plan.json',plan)
print(json.dumps({'status':'PREPARED_NOT_ACTIVE','source':SOURCE,'plan_sha256':sha(STATE/'authority/frozen-runtime-plan.json'),'destinations':{k:str(STATE/'databases'/(k+'.sqlite')) for k in paths},'bytes':{k:(STATE/'databases'/(k+'.sqlite')).stat().st_size for k in paths},'free':shutil.disk_usage(STATE).free},indent=2))
