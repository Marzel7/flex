from pathlib import Path
import subprocess,os,json,hashlib,resource,importlib.util,sqlite3
D=Path('/Users/kevinkeaveney/.codex/worktrees/dev004-first-v2-prototype/flex');O=Path('/private/tmp/dev004-startup-integration-final');O.mkdir(exist_ok=True)
P='/Users/kevinkeaveney/anaconda3/envs/algotrader/bin/python';results=[]
def run(label,script,pkg,env=None):
 e=dict(os.environ,PYTHONDONTWRITEBYTECODE='1');e.update(env or {})
 p=subprocess.run([P,'-B',script,str(D/'runtime'/pkg),'unused',str(O)],env=e,capture_output=True,text=True,timeout=45)
 (O/(label+'.stdout')).write_text(p.stdout[-40000:]);(O/(label+'.stderr')).write_text(p.stderr[-20000:]);results.append(dict(case=label,exit=p.returncode,tail=p.stdout[-600:],error=p.stderr[-500:] if p.returncode else None));print(label,p.returncode,p.stdout[-350:])
run('core','/private/tmp/dev004-schema-bootstrap-child.py','listener',{'BOOTSTRAP_KIND':'core'})
run('ops','/private/tmp/dev004-schema-bootstrap-child.py','walkback',{'BOOTSTRAP_KIND':'ops'})
run('parent','/private/tmp/dev004-vcore-parent.py','creator_resolution')
resource.setrlimit(resource.RLIMIT_FSIZE,(16000000,16000000))
spec=importlib.util.spec_from_file_location('base',D/'runtime/creator_resolution/src/ops/dev004_core_base_schema.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
for i in range(2):results.append(dict(case='versioned_index_'+str(i),result=m.initialize_creator_index(O/'core.sqlite',disposable_root=O,allow_disposable_v2=True)))
run('additional','/private/tmp/dev004-integration-additional.py','creator_resolution')
run('quarantine','/private/tmp/dev004-integration-quarantine.py','listener')
for mode,pkg in [('compact','walkback'),('resolver','creator_resolution'),('walkback','walkback')]:run(mode,'/private/tmp/dev004-schema-startup-check.py',pkg,{'SCHEMA_CHECK_MODE':mode})
run('consumer','/private/tmp/dev004-vcore-consumers.py','creator_resolution')
run('listener','/private/tmp/dev004-integration-listener.py','listener')
(O/'results.json').write_text(json.dumps(results,indent=2));print('RESULTS',O/'results.json')
