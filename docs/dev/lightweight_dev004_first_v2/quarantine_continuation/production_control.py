import ast,configparser,hashlib,importlib.util,json,os,pathlib,resource,shutil,signal,sqlite3,subprocess,sys,time,uuid
R=pathlib.Path('/Users/kevinkeaveney/.codex/worktrees/dev004-first-v2-prototype/flex');S=pathlib.Path('/Users/kevinkeaveney/Dev/claude/flex/.runtime/dev004-v2');CONF=pathlib.Path('/Users/kevinkeaveney/Dev/claude/flex-development/DEV090/operational-restoration/supervisord.selective-retry2.conf');PY='/Users/kevinkeaveney/anaconda3/envs/algotrader/bin/python';CTL='/Users/kevinkeaveney/anaconda3/envs/algotrader/bin/supervisorctl'
resource.setrlimit(resource.RLIMIT_FSIZE,(499000000,499000000));signal.signal(signal.SIGXFSZ,signal.SIG_IGN);sys.dont_write_bytecode=True
P=json.load(open(S/'authority/frozen-runtime-plan.json'));assert P['immutable_source_commit']=='dfb76562efc0aa598c6750ddb36723b9e5e7b06e';sys.path.insert(0,str(R/'runtime/listener'))
from src.core import paired_binding_guard as g
from src.core.birth_admission_journal import BirthJournal
from src.core.migration_signature_journal import MigrationJournal

def sha(p):return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()
def atomic(p,data,mode=0o600):
 p=pathlib.Path(p);tmp=p.with_name(p.name+'.dev004-stage');old=p.stat() if p.exists() else None
 fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,mode)
 try:
  assert os.write(fd,data)==len(data);os.fsync(fd)
  if old:os.fchmod(fd,old.st_mode&0o7777);os.fchown(fd,old.st_uid,old.st_gid)
 finally:os.close(fd)
 os.replace(tmp,p);fd=os.open(p.parent,os.O_RDONLY)
 try:os.fsync(fd)
 finally:os.close(fd)
def save(p,v):atomic(p,(json.dumps(v,sort_keys=True,indent=2)+'\n').encode())
def phase(name,**kw):save(S/'authority/transition-state.json',dict(phase=name,at_ns=time.time_ns(),**kw));print(json.dumps({'phase':name,**kw}),flush=True)
def ctl(*args):
 p=subprocess.run([CTL,'-c',str(CONF),*args],capture_output=True,text=True,timeout=60)
 if p.returncode and not (args[0]=='status' and p.returncode==3):raise RuntimeError('SUPERVISOR:'+p.stdout[-1000:]+p.stderr[-1000:])
 return p.stdout.strip()
def settings(mode):
 v1=mode=='v1';pair=S/'authority'/('v1-pair.json' if v1 else 'pair.json');aux=S/'authority'/('v1-auxiliary.json' if v1 else 'auxiliary.json');e=dict(P['planned_production_environment']);e.update(DB_PATH=json.load(open(pair))['stores']['core']['path'],WT_OPS_DB_PATH=json.load(open(pair))['stores']['ops']['path'],FLEX_DB_PAIR_MANIFEST=str(pair),FLEX_DB_AUXILIARY_MANIFEST=str(aux),FLEX_DB_AUXILIARY_SHA256=sha(aux),FLEX_DB_PAIR_ROLE='runtime',PYTHONDONTWRITEBYTECODE='1');return e

def bind(mode,transfer=False):
 e=settings(mode);os.environ.update({k:str(v) for k,v in e.items()});os.environ['FLEX_DB_PAIR_ROLE']='transfer' if transfer else 'runtime';os.environ['FLEX_DB_PAIR_OWNER']='DEV004-production-owner' if transfer else ''

def owner(mode,stage):
 pair=S/'authority'/('v1-pair.json' if mode=='v1' else 'pair.json');m=json.load(open(pair));save(S/'authority/owner.json',{'version':1,'authority_id':m['authority']['id'],'origin_generation':m['authority']['origin_generation'],'generation':m['generation'],'manifest_path':str(pair),'manifest_sha256':sha(pair),'phase':stage,'owner':'runtime' if stage=='ACTIVE' else 'DEV004-production-owner','transfer_complete':stage=='ACTIVE'})

def write_config(mode):
 from supervisor.datatypes import dict_of_key_value_pairs
 cfg=configparser.ConfigParser(interpolation=None);cfg.read(S/'source-preimages/supervisor.conf');before={k:dict(cfg[k]) for k in cfg.sections()};mapping={'watchtower_listener':'listener','creator_resolution_worker':'creator_resolution','walkback_worker':'walkback'}
 for service,role in mapping.items():
  section=cfg['program:'+service];env=dict_of_key_value_pairs(section.get('environment',''));env.update(settings(mode));env['PYTHONPATH']=P['roles'][role]['package']
  if role=='listener':env.update(P['roles'][role]['nonsecret_wrapper_environment'])
  section['environment']=','.join(k+'="'+str(v).replace('\\','\\\\').replace('"','\\"')+'"' for k,v in env.items())
  if mode=='v2':section['command']=' '.join(P['roles'][role]['command'])
 for key in cfg.sections():
  if key not in {'program:'+x for x in mapping}:assert dict(cfg[key])==before[key],'UNRELATED_SUPERVISOR_CHANGE'
 import io
 out=io.StringIO();cfg.write(out);atomic(CONF,out.getvalue().encode())
 result=ctl('reread');print('SUPERVISOR_REREAD '+result,flush=True)
 for line in result.splitlines():
  if ':' in line:assert line.split(':',1)[0] in mapping,'UNEXPECTED_CHANGED_SERVICE'
 print(ctl('update',*mapping),flush=True)
 print(ctl('start',*mapping),flush=True)
 save(S/'authority'/('supervisor-'+mode+'-identity.json'),{'configuration_sha256':sha(CONF),'services':ctl('status',*mapping)})

def install_v1():
 assert not (S/'authority/transition-state.json').exists()
 for rel,x in P['deployment_payload'].items():
  p=pathlib.Path(x['target']);assert (sha(p) if p.exists() else None)==x['preimage_sha256'];assert sha(S/'source-payload'/x['staged_name'])==x['sha256']
 assert sha(CONF)==P['original_supervisor_sha256'];assert shutil.disk_usage(S).free>2_000_000_000+256*1024*1024
 print(ctl('stop','watchtower_listener','creator_resolution_worker','walkback_worker'),flush=True);phase('OLD_SERVICES_STOPPED')
 pair=json.load(open(S/'authority/v1-pair.json'))
 holders=subprocess.run(['/usr/sbin/lsof','-nP',*[v['path'] for v in pair['stores'].values()]],capture_output=True,text=True)
 assert not holders.stdout.strip(),'UNEXPECTED_V1_CONNECTION_OWNER'
 for rel,x in P['deployment_payload'].items():
  p=pathlib.Path(x['target']);p.parent.mkdir(parents=True,exist_ok=True);atomic(p,(S/'source-payload'/x['staged_name']).read_bytes(),0o644);assert sha(p)==x['sha256']
 g.seal_manifest(str(S/'authority/v1-pair.json'));owner('v1','ACTIVE');phase('QUALIFIED_SOURCE_INSTALLED_V1_MATCHED')
 write_config('v1');phase('V1_DURABLE_JOURNAL_RUNTIME',services=ctl('status','watchtower_listener','creator_resolution_worker','walkback_worker'))

def helpers():
 scope={'os':os,'DB_PATH':settings('v1')['DB_PATH'],'_listener_ops_db_path':lambda:settings('v1')['WT_OPS_DB_PATH'],'_validate_active_pair':lambda:g.validate_pair(purpose='active')}
 tree=ast.parse((R/'runtime/listener/src/core/pumpfun_curve_listener.py').read_text());names={'_birth_journal_canonical','_birth_journal_readback','_migration_journal_readback'}
 for n in tree.body:
  if isinstance(n,ast.FunctionDef) and n.name in names:exec(compile(ast.Module(body=[n],type_ignores=[]),'qualified-readback','exec'),scope)
 return scope

def hold_lanes(paths,publish_fence):
    # One existing owner per process; never bypass the nested-write prohibition.
    children=[]
    code="""import os,sys,uuid
sys.path.insert(0,sys.argv[1])
from src.core import database_write_service as w
lease=w.acquire_write_lease(sys.argv[2],sys.argv[3],str(uuid.uuid4()),'DEV004-authority-fence',timeout=15,_retirement_metadata_only=True)
try:
 print('READY',flush=True);sys.stdin.buffer.read(1)
finally:w.release_write_lease(lease)
"""
    try:
        for role,path in paths:
            child=subprocess.Popen([PY,'-B','-c',code,str(R/'runtime/listener'),role,path],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            children.append(child)
            ready=child.stdout.readline().strip()
            if ready!='READY':raise RuntimeError('LEASE_OWNER_NOT_READY:'+role+':'+child.stderr.read()[-500:])
        publish_fence()
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.stdin.write('x');child.stdin.flush();child.stdin.close()
            child.wait(timeout=20)
            if child.returncode:raise RuntimeError('LEASE_OWNER_EXIT:'+child.stderr.read()[-500:])

def fence():
    bind('v1');assert g.validate_pair()==P['source_generation']
    for name in ['creator_resolution_worker','walkback_worker']:
        if 'RUNNING' in ctl('status',name):print(ctl('stop',name),flush=True)
    h=helpers();witness={};prior=S/'checkpoints/pre-fence-admission-witness.json'
    if prior.exists():witness=json.load(open(prior))
    for kind,cls,verify in [('birth',BirthJournal,h['_birth_journal_readback']),('migration',MigrationJournal,h['_migration_journal_readback'])]:
        j=cls(S/'journals'/(kind+'.jsonl'),verify_completed=verify);rows=j.snapshot();assert len(rows)<448,'ADMISSION_PRESSURE'
        seen={r['id']:r for r in witness.get(kind,[])}
        seen.update({r['id']:{'id':r['id'],'signature':r['signature'],'mint':r['mint'],'state':r['state'],'ack':r.get('ack')} for r in rows});witness[kind]=list(seen.values())
        save(prior,witness)
        while j.reclaim_completed():pass
    hold_lanes([('core',settings('v1')['DB_PATH']),('ops',settings('v1')['WT_OPS_DB_PATH'])],lambda:owner('v1','TRANSFER'))
    bind('v1',True);assert g.validate_pair(write_path=settings('v1')['DB_PATH'],purpose='write')==P['source_generation'];phase('V1_WRITES_FENCED_NOT_RETIRED',journal_counts={k:len(cls(S/'journals'/(k+'.jsonl')).snapshot()) for k,cls in [('birth',BirthJournal),('migration',MigrationJournal)]})

if __name__=='__main__':{'install_v1':install_v1,'fence':fence}[sys.argv[1]]()
