import hashlib,importlib.util,json,os,pathlib,resource,signal,sqlite3,sys,tempfile,shutil
R=pathlib.Path('/Users/kevinkeaveney/.codex/worktrees/dev004-first-v2-prototype/flex');B=pathlib.Path(tempfile.mkdtemp(prefix='dev004-large-rollback-',dir='/private/tmp'))
resource.setrlimit(resource.RLIMIT_FSIZE,(499000000,499000000));signal.signal(signal.SIGXFSZ,signal.SIG_IGN);sys.dont_write_bytecode=True
sys.path.insert(0,str(R/'runtime/listener'));from src.core import paired_binding_guard as g
spec=importlib.util.spec_from_file_location('core_owner',R/'runtime/creator_resolution/src/ops/dev004_core_base_schema.py');core=importlib.util.module_from_spec(spec);spec.loader.exec_module(core)
spec=importlib.util.spec_from_file_location('ops_owner',R/'runtime/listener/src/ops/dev004_watchtower_references.py');ops=importlib.util.module_from_spec(spec);spec.loader.exec_module(ops)
SCHEMA=json.load(open(R/'docs/dev/lightweight_dev004_first_v2/production_profile_bootstrap/accepted_fixture_schema.json'));OWNED={'core':{'token_market_cap_peaks'},'ops':{'operator_launch_membership','operator_membership_reference_v1'}}
paths={}
for name in ['source','rollback']:
 root=B/('dev004-'+name);root.mkdir();paths[name]={k:root/(k+'.sqlite') for k in ['core','ops']}
 for kind,p in paths[name].items():
  with sqlite3.connect(p) as c:
   for x in SCHEMA[kind]:
    if x['name'].startswith('dev004_') or x['name'] in OWNED[kind] or x['table'] in OWNED[kind]:continue
    c.execute(x['sql'])
# Prepare a compact synthetic source with the accepted owners before growth.
resource.setrlimit(resource.RLIMIT_FSIZE,(64*1024*1024,499000000))
core.initialize(paths['source']['core'],disposable_root=paths['source']['core'].parent,allow_disposable_v2=True,price_identity_parent='token_analysis')
with sqlite3.connect(paths['source']['ops']) as c:c.execute('pragma foreign_keys=on');ops.initialize(c,disposable_root=paths['source']['ops'].parent,allow_disposable_v2=True)
resource.setrlimit(resource.RLIMIT_FSIZE,(499000000,499000000))
with sqlite3.connect(paths['source']['ops']) as c:
 for i in range(13000):c.execute('insert into wt_lineage_quarantine values (?,?,?,?,?,?,?,?,?,?,?,?)',(f'{i:064x}','synthetic',i,'wallet','parent','sig'+str(i),'NEGATIVE','CONFLICT','SYNTHETIC',json.dumps({'negative':'x'*6000,'i':i}),1,1700000000))
assert paths['source']['ops'].stat().st_size>64*1024*1024
owner=B/'owner.json';authority={'id':'large-rollback-fixture','path':str(owner),'origin_generation':'source'}
old={'version':1,'authority':authority,'generation':'source','stores':{k:g.database_identity(str(p),'source',k) for k,p in paths['source'].items()},'predecessor':None}
def publish(p,x):
 b=json.dumps(x,sort_keys=True,separators=(',',':')).encode();f=open(p,'wb');f.write(b);f.flush();os.fsync(f.fileno());f.close()
for k,p in paths['source'].items():publish(pathlib.Path(str(p)+'.retired'),{'version':1,'path':str(p),'generation':'source','retired':True})
pair=B/'pair.json';new={'version':1,'authority':authority,'generation':'rollback','stores':{k:g.database_identity(str(p),'rollback',k) for k,p in paths['rollback'].items()},'predecessor':{'generation':'source','stores':old['stores']}};publish(pair,new)
state={'version':1,'authority_id':authority['id'],'origin_generation':'source','generation':'rollback','manifest_path':str(pair),'manifest_sha256':hashlib.sha256(pair.read_bytes()).hexdigest(),'phase':'TRANSFER','owner':'fixture-owner','transfer_complete':False};publish(owner,state)
frozen=B/'frozen.json';profile={'production_bootstrap':{'version':1,'root':str(paths['rollback']['core'].parent),'generation':'rollback','pair_manifest_path':str(pair),'pair_manifest_sha256':hashlib.sha256(pair.read_bytes()).hexdigest(),'authority_path':str(owner),'transfer_owner':'fixture-owner','stores':new['stores'],'schema_authority':{'core':{'contract':hashlib.sha256((core.CONTRACT+':token_analysis').encode()).hexdigest(),'owner_sha256':hashlib.sha256(pathlib.Path(core.__file__).read_bytes()).hexdigest()},'ops':ops.base._schema_authority()}}};publish(frozen,profile)
os.environ.update(DB_PATH=str(paths['rollback']['core']),WT_OPS_DB_PATH=str(paths['rollback']['ops']),DEV004_PAIRED_BINDING_REQUIRED='1',FLEX_DB_PAIR_MANIFEST=str(pair),FLEX_DB_PAIR_AUTHORITY=str(owner),FLEX_DB_PAIR_ROLE='transfer',FLEX_DB_PAIR_OWNER='fixture-owner',DEV004_PRODUCTION_BOOTSTRAP_MANIFEST=str(frozen),DEV004_PRODUCTION_BOOTSTRAP_SHA256=hashlib.sha256(frozen.read_bytes()).hexdigest())
# Exact same owners, empty matched destination first, restore after completion.
resource.setrlimit(resource.RLIMIT_FSIZE,(64*1024*1024,499000000))
core.initialize(paths['rollback']['core'],disposable_root=paths['rollback']['core'].parent,allow_disposable_v2=True,price_identity_parent='token_analysis')
with sqlite3.connect(paths['rollback']['ops']) as c:c.execute('pragma foreign_keys=on');ops.initialize(c,disposable_root=paths['rollback']['ops'].parent,allow_disposable_v2=True)
resource.setrlimit(resource.RLIMIT_FSIZE,(499000000,499000000))
for kind,p in paths['rollback'].items():
 src=sqlite3.connect(paths['source'][kind].as_uri()+'?mode=ro',uri=True);dst=sqlite3.connect(p)
 def progress(*args):
  assert shutil.disk_usage(B).free>2_000_000_000+64*1024*1024
  assert g.validate_pair(write_path=str(p),purpose='write')=='rollback'
 src.backup(dst,pages=128,progress=progress);src.close();dst.close();assert g.database_identity(str(p),'rollback',kind)==new['stores'][kind]
for key in ['DEV004_PRODUCTION_BOOTSTRAP_MANIFEST','DEV004_PRODUCTION_BOOTSTRAP_SHA256']:os.environ.pop(key)
g.seal_manifest(str(pair));state.update(phase='ACTIVE',owner='runtime',transfer_complete=True);publish(owner,state);os.environ.update(FLEX_DB_PAIR_ROLE='runtime');assert g.validate_pair()=='rollback'
with sqlite3.connect(paths['rollback']['ops']) as c:
 assert c.execute('select count(*) from wt_lineage_quarantine').fetchone()[0]==13000;assert not c.execute('pragma foreign_key_check').fetchall()
with sqlite3.connect(paths['rollback']['core']) as c:c.execute("insert into token_analysis(mint) values('fresh-rollback-write')")
result={'status':'PASS','root':str(B),'source_ops_bytes':paths['source']['ops'].stat().st_size,'rollback_ops_bytes':paths['rollback']['ops'].stat().st_size,'quarantine_rows':13000,'same_destination_physical_identity':True,'bootstrap_before_restore':True,'matched_active_generation':'rollback','fresh_write':True,'providers':0,'production_paths_opened':0};publish(B/'result.json',result);print(json.dumps(result,indent=2))
