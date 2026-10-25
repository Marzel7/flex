import json,sqlite3,pathlib,hashlib,sys,subprocess,importlib.util,os
R=pathlib.Path(sys.argv[1]);F=pathlib.Path(sys.argv[2]);F.mkdir(exist_ok=True)
if len(sys.argv)>3 and sys.argv[3]=='fresh':
 old=json.loads((F/'pair.json').read_text());manifest=json.loads((F/'manifest.json').read_text())
 spec=importlib.util.spec_from_file_location('fixture_rebind_pair',R/'runtime/listener/src/core/paired_binding_guard.py');g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)
 for role in ['core','ops']:
  source=sqlite3.connect((F/(role+'.sqlite')).as_uri()+'?mode=ro',uri=True);target=sqlite3.connect(F/('fresh-'+role+'.sqlite'));source.backup(target,pages=16);source.close();target.close()
  (F/(role+'.sqlite.retired')).write_text(json.dumps({'version':1,'path':str(F/(role+'.sqlite')),'generation':'fixture-v2','retired':True}))
 pair=F/'fresh-pair.json';owner=F/'owner.json';generation='fresh-fixture-v2'
 value={'version':1,'authority':old['authority'],'generation':generation,'stores':{role:g.database_identity(str(F/('fresh-'+role+'.sqlite')),generation,role) for role in ['core','ops']},'predecessor':{'generation':old['generation'],'stores':old['stores']}}
 def save(p,v):p.write_text(json.dumps(v,sort_keys=True,separators=(',',':')))
 save(pair,value);save(owner,{'version':1,'authority_id':old['authority']['id'],'origin_generation':'fixture-v2','generation':generation,'manifest_path':str(pair),'manifest_sha256':hashlib.sha256(pair.read_bytes()).hexdigest(),'phase':'ACTIVE','owner':'runtime','transfer_complete':True});g.seal_manifest(pair)
 (F/'original-manifest.json').write_text((F/'manifest.json').read_text())
 manifest['generation']=generation;manifest['environment'].update(DB_PATH=str(F/'fresh-core.sqlite'),WT_OPS_DB_PATH=str(F/'fresh-ops.sqlite'),RPC_METRICS_DB=str(F/'fresh-core.sqlite'),FLEX_DB_PAIR_MANIFEST=str(pair))
 save(F/'manifest.json',manifest)
 raise SystemExit(0)

# Source-owned accepted fixture schema, plus explicit accepted initializers in later controls.
schema=json.loads((R/'docs/dev/lightweight_dev004_first_v2/production_profile_bootstrap/accepted_fixture_schema.json').read_text())
for role in ['core','ops']:
 c=sqlite3.connect(F/(role+'.sqlite'))
 for item in schema[role]:
  if item['type']=='table':c.execute(item['sql'])
 for item in schema[role]:
  if item['type']!='table':c.execute(item['sql'])
 c.commit();c.close()
# Actual pair schema owner seals the disposable physical identities.
p=R/'runtime/listener/src/core/paired_binding_guard.py';spec=importlib.util.spec_from_file_location('fixture_pair',p);g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)
pair=F/'pair.json';owner=F/'owner.json';m={'version':1,'authority':{'id':'dormant-fixture','path':str(owner),'origin_generation':'fixture-v2'},'generation':'fixture-v2','stores':{role:g.database_identity(str(F/(role+'.sqlite')),'fixture-v2',role) for role in ['core','ops']},'predecessor':None}
def save(p,v):p.write_text(json.dumps(v,sort_keys=True,separators=(',',':')))
save(pair,m);save(owner,{'version':1,'authority_id':'dormant-fixture','origin_generation':'fixture-v2','generation':'fixture-v2','manifest_path':str(pair),'manifest_sha256':hashlib.sha256(pair.read_bytes()).hexdigest(),'phase':'ACTIVE','owner':'runtime','transfer_complete':True});g.seal_manifest(pair)
queue=F/'queue';queue.mkdir(exist_ok=True);save(queue/'provider_budget.json',{'version':1,'updated_at':1800000000,'calls':[]});(queue/'provider_budget.lock').write_bytes(b'READY_V1\n')
selection=F/'selection.json';save(selection,{'mode':'DEV_005_ISOLATED_SOAK','allowlist':[]})
env={'DB_PATH':str(F/'core.sqlite'),'WT_OPS_DB_PATH':str(F/'ops.sqlite'),'FLEX_DB_PAIR_MANIFEST':str(pair),'FLEX_DB_PAIR_AUTHORITY':str(owner),'FLEX_DB_PAIR_ROLE':'runtime','DEV004_PAIRED_BINDING_REQUIRED':'1','TMPDIR':str(F),'SQLITE_TMPDIR':str(F),'RPC_METRICS_DB':str(F/'core.sqlite')}
for k in ['SQLITE_LIFECYCLE_LOG','DB_CONNECTION_LIFECYCLE_LOG','DB_WRITE_DIAGNOSTICS_PATH','WS_SNAPSHOT_LOG_PATH','WS_PRICE_TRACE_LOG_PATH','LAUNCH_PRICE_LOG_PATH']:env[k]=str(F/(k+'.log'))
manifest={'version':1,'source_commit':subprocess.check_output(['git','-C',str(R),'rev-parse','HEAD'],text=True).strip(),'generation':'fixture-v2','environment':env,'queue_path':str(queue),'budget_path':str(queue/'provider_budget.json'),'budget_contract_sha256':hashlib.sha256((R/'src/ops/dev_provider_budget.py').read_bytes()).hexdigest(),'selection_path':str(selection),'selection_sha256':hashlib.sha256(selection.read_bytes()).hexdigest(),'credential_file':None}
save(F/'manifest.json',manifest)
