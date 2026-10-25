"""Explicit emergency recovery using the already-qualified fresh compact pair.
Does nothing without the execution argument. Never un-retires or copies V1.
"""
import importlib.util,json,os,pathlib,sqlite3,resource,subprocess,shutil,sys,hashlib

def execute():
 s=importlib.util.spec_from_file_location('c',pathlib.Path(__file__).resolve().parents[1]/'docs/dev/lightweight_dev004_first_v2/quarantine_continuation/production_control.py');c=importlib.util.module_from_spec(s);s.loader.exec_module(c);c.bind('v2');assert c.g.validate_pair()==c.P['generation']
 pair=c.S/'authority/rollback-pair.json';manifest=json.load(open(pair));root=c.S/'rollback';owner_path=c.S/'authority/owner.json';generation=manifest['generation'];assert shutil.disk_usage(c.S).free>sum((c.S/'databases'/(k+'.sqlite')).stat().st_size for k in ['core','ops'])+2_000_000_000+64*1024*1024
 c.hold_lanes([('core',c.settings('v2')['DB_PATH']),('ops',c.settings('v2')['WT_OPS_DB_PATH'])],lambda:c.owner('v2','TRANSFER'))
 for name in ['watchtower_listener','creator_resolution_worker','walkback_worker']:
  if 'STOPPED' not in c.ctl('status',name):print(c.ctl('stop',name),flush=True)
 from src.core import database_write_service as dws
 for k in ['core','ops']:dws.retire_database(k,str(c.S/'databases'/(k+'.sqlite')),generation=c.P['generation'],timeout=15)
 aux=json.load(open(c.S/'authority/auxiliary.json'));aux.update(generation=generation,pair_manifest_sha256=c.sha(pair))
 for name,row in aux['stores'].items():row['generation']=generation
 auxiliary=c.S/'authority/rollback-auxiliary.json';c.save(auxiliary,aux)
 state={'version':1,'authority_id':manifest['authority']['id'],'origin_generation':manifest['authority']['origin_generation'],'generation':generation,'manifest_path':str(pair),'manifest_sha256':c.sha(pair),'phase':'TRANSFER','owner':'DEV004-production-owner','transfer_complete':False};c.save(owner_path,state)
 env=c.settings('v2');env.update(DB_PATH=str(root/'core.sqlite'),WT_OPS_DB_PATH=str(root/'ops.sqlite'),RPC_METRICS_DB=str(root/'core.sqlite'),FLEX_DB_PAIR_MANIFEST=str(pair),FLEX_DB_AUXILIARY_MANIFEST=str(auxiliary),FLEX_DB_AUXILIARY_SHA256=c.sha(auxiliary),FLEX_DB_PAIR_ROLE='transfer',FLEX_DB_PAIR_OWNER='DEV004-production-owner');os.environ.update(env)
 def module(name,rel):
  spec=importlib.util.spec_from_file_location(name,c.R/rel);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
 core=module('core_owner','runtime/creator_resolution/src/ops/dev004_core_base_schema.py');ops=module('ops_owner','runtime/listener/src/ops/dev004_watchtower_references.py')
 frozen=c.S/'authority/rollback-bootstrap.json';profile={'production_bootstrap':{'version':1,'root':str(root),'generation':generation,'pair_manifest_path':str(pair),'pair_manifest_sha256':c.sha(pair),'authority_path':str(owner_path),'transfer_owner':'DEV004-production-owner','stores':manifest['stores'],'schema_authority':{'core':{'contract':hashlib.sha256((core.CONTRACT+':token_analysis').encode()).hexdigest(),'owner_sha256':c.sha(core.__file__)},'ops':ops.base._schema_authority()}}};c.save(frozen,profile);os.environ.update(DEV004_PRODUCTION_BOOTSTRAP_MANIFEST=str(frozen),DEV004_PRODUCTION_BOOTSTRAP_SHA256=c.sha(frozen))
 resource.setrlimit(resource.RLIMIT_FSIZE,(64*1024*1024,499000000));core.initialize(root/'core.sqlite',disposable_root=root,allow_disposable_v2=True,price_identity_parent='token_analysis')
 with dws._native_connect(root/'ops.sqlite') as d:d.execute('pragma foreign_keys=on');ops.initialize(d,disposable_root=root,allow_disposable_v2=True)
 resource.setrlimit(resource.RLIMIT_FSIZE,(499000000,499000000))
 for k in ['core','ops']:
  source=c.S/'databases'/(k+'.sqlite');destination=root/(k+'.sqlite');assert c.g.database_identity(str(destination),generation,k)==manifest['stores'][k]
  src=dws._native_connect(source.as_uri()+'?mode=ro',uri=True);dst=dws._native_connect(destination)
  def progress(*unused):
   assert shutil.disk_usage(c.S).free>2_000_000_000+64*1024*1024;assert c.g.validate_pair(write_path=str(destination),purpose='write')==generation
  src.backup(dst,pages=128,progress=progress);src.close();assert not dst.execute('pragma foreign_key_check').fetchall();dst.close();assert c.g.database_identity(str(destination),generation,k)==manifest['stores'][k]
 # Copied claims never carry stopped process ownership into the fresh pair.
 for k,table,expiry in [('core','creator_resolution_queue','locked_until'),('core','creator_funding_queue','locked_until'),('ops','wt_walkback_queue','lease_expires_at')]:
  with dws._native_connect(root/(k+'.sqlite')) as d:
   d.row_factory=sqlite3.Row;info=d.execute('pragma table_info("'+table+'")').fetchall();keys=[r[1] for r in info if r[5]];columns={r[1]:r for r in info}
   while True:
    rows=d.execute('select * from "'+table+'" where status="running" limit 128').fetchall()
    if not rows:break
    for row in rows:
     changes={'status':'retry'}
     if expiry in columns:changes[expiry]=0
     for name in ['lease_owner','locked_by','claimed_by']:
      if name in columns:changes[name]='' if columns[name][3] else None
     d.execute('update "'+table+'" set '+','.join('"'+x+'"=?' for x in changes)+' where '+' and '.join('"'+x+'" is ?' for x in keys),tuple(changes.values())+tuple(row[x] for x in keys))
    d.commit()
 for name in ['DEV004_PRODUCTION_BOOTSTRAP_MANIFEST','DEV004_PRODUCTION_BOOTSTRAP_SHA256']:os.environ.pop(name,None)
 c.g.seal_manifest(str(pair));state.update(phase='ACTIVE',owner='runtime',transfer_complete=True);c.save(owner_path,state);env.update(FLEX_DB_PAIR_ROLE='runtime',FLEX_DB_PAIR_OWNER='');os.environ.update(env);assert c.g.validate_pair()==generation
 c.settings=lambda mode:dict(env);c.write_config('v2');c.phase('FRESH_COMPACT_RECOVERY_ACTIVE',generation=generation,core=str(root/'core.sqlite'),ops=str(root/'ops.sqlite'),original_V1_unchanged=True,journals_preserved=True)

if __name__=='__main__':
 if sys.argv[1:]!=['--execute-authorized-recovery']:raise SystemExit('EXPLICIT_RECOVERY_EXECUTION_REQUIRED')
 execute()
