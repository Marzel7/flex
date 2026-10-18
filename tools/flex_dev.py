#!/usr/bin/env python3
"""Small, fail-closed Git/qualification workflow. No deployment implementation."""
from __future__ import annotations
import argparse, ast, hashlib, json, os, pathlib, plistlib, re, resource, signal, tempfile, itertools, select, importlib.metadata
import socket, sqlite3, stat, subprocess, sys, time, urllib.request, urllib.error, uuid
P = pathlib.Path
BASE = '0eb749ecd6acd0ab70ec6d09b9170c583d82374b'
TAG = 'DEV043_BOUNDED_RECOVERY_BASELINE'
ROOT = P('/Users/kevinkeaveney/Dev/claude/flex')
RELEASE = ROOT.parent / 'flex-releases' / (TAG + '-0eb749ec')
HOME = ROOT.parent / 'flex-development'
PYTHON = '/Users/kevinkeaveney/anaconda3/envs/algotrader/bin/python'
NODE=P('/opt/homebrew/Cellar/node@22/22.23.2/bin/node')
NODE_SHA256='1f72236fcbfc84855d7c884fbcba0f6a7c635d600d7e493b823e77c467f2ae95'
NODE_VERSION='v22.23.2'
BOOT = 'docs/audits/dev043/isolation_bootstrap.py'
CAP, SMALL = 256_000_000, 2_000_000
FEATURES = {
 'registry': ['UNIVERSAL PRICE MONITOR','Byzantine','Watchtower','Nexus'],
 'history_prices': ['HISTORY — LAST 20','$162,776','$274,477','RETAINED PEAK'],
 'watchtower_admission': ['Confirmed / admitted memberships','REJECTED / NOT ADMITTED'],
 'watchtower_playbook': ['WATCHTOWER — Operation Playbook','Token lifecycle evidence'],
 'byzantine_playbook': ['Byzantine — Operation Playbook','research'],
 'mission_control': ['Mission Control','Inbox'],
 'core_health': ['Platform','Infrastructure'],
 'pending': ['Counts UNKNOWN','UNAVAILABLE: Treasury review workspace','Request exceeded 3 seconds'],
 'byzantine_detail': ['UNAVAILABLE — Operation detail','Request exceeded 3 seconds'],
 'nexus_detail': ['UNAVAILABLE — Operation detail','Request exceeded 3 seconds'],
}
ROUTES = {'registry':'/intelligence/operators','history_prices':'/intelligence/operators',
 'watchtower_admission':'/intelligence/operator/04265d9f-6eb2-568c-a49e-9253091a4dbb',
 'watchtower_playbook':'/operations/watchtower/playbook',
 'byzantine_playbook':'/operations/d8ee4d7a-fcd6-5a5b-b897-24f6ab56e334/playbook',
 'mission_control':'/ops-os','core_health':'/system-health','pending':'/intelligence/treasury-review',
 'byzantine_detail':'/intelligence/operator/d8ee4d7a-fcd6-5a5b-b897-24f6ab56e334',
 'nexus_detail':'/intelligence/operator/bd7d7479-1454-5d41-9f68-115550348f3e'}
class Hold(RuntimeError): pass
def need(value, reason):
 if not value: raise Hold(reason)
def digest(path):
 p=P(path); need(p.is_file() and p.stat().st_size<CAP,'missing/oversized artifact: '+str(p))
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1_048_576),b''):h.update(b)
 return h.hexdigest()
def tool_id():
 return hashlib.sha256((digest(__file__)+digest(P(__file__).with_name('flex_dev_guarded.py'))).encode()).hexdigest()
def room(directory,pattern,limit):
 need(sum(1 for _ in itertools.islice(P(directory).glob(pattern),limit))<limit,'retention budget reached; preserve/review, no automatic cleanup')
def read(path):
 p=P(path);need(p.is_file() and p.stat().st_size<SMALL,'missing/oversized JSON: '+str(p))
 return json.loads(p.read_text())
def save(path, data):
 p=P(path); b=(json.dumps(data,indent=2,sort_keys=True)+'\n').encode();need(len(b)<SMALL,'manifest budget exceeded')
 p.parent.mkdir(parents=True,exist_ok=True)
 room(p.parent,'*.tmp',8)
 # Only mutable run receipts use replacement; candidate/release directories are exclusive and sealed.
 tmp=p.with_name(p.name+'.'+uuid.uuid4().hex+'.tmp')
 with tmp.open('xb') as f:f.write(b);f.flush();os.fsync(f.fileno())
 os.replace(tmp,p)
def git(repo,*args):
 process=subprocess.Popen(['git','-C',str(repo),*args],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,preexec_fn=lambda:resource.setrlimit(resource.RLIMIT_FSIZE,(CAP,CAP)))
 data=bytearray();deadline=time.monotonic()+60
 try:
  while True:
   need(time.monotonic()<deadline,'Git command deadline')
   if not select.select([process.stdout],[],[],.2)[0]:continue
   chunk=os.read(process.stdout.fileno(),65536)
   if not chunk:break
   data.extend(chunk);need(len(data)<8_000_000,'Git output budget')
  process.wait(timeout=max(.1,deadline-time.monotonic()))
  need(process.returncode==0,'Git command failed: '+args[0]+'; '+re.sub(r'https://[^/@\s]+@','https://',data.decode(errors='replace')[-500:]))
  return data.decode()
 except BaseException:
  if process.poll() is None:process.kill();process.wait()
  raise
 finally:process.stdout.close()
def runtime_versions(external):
 mapping=importlib.metadata.packages_distributions();names=set()
 for path,h in external.items():
  need(digest(path)==h,'external dependency changed during qualification')
  if '/site-packages/' not in path:continue
  top=path.split('/site-packages/',1)[1].split('/',1)[0].split('.')[0]
  distributions=mapping.get(top,[]);need(distributions,'unmapped runtime dependency version: '+top);names.update(distributions)
 return {'python':sys.version,'sqlite':sqlite3.sqlite_version,'distributions':{n:importlib.metadata.version(n) for n in sorted(names)},'identity':'exact loaded file hashes plus distribution versions; not a portable wheel bundle'}
def golden(repo=ROOT):
 d=read(RELEASE/'RELEASE.json');need(d['source_sha']==BASE,'golden source mismatch')
 need(git(repo,'rev-parse',TAG+'^{}').strip()==BASE,'golden tag moved')
 need(git(repo,'rev-parse',TAG).strip()==d['tag_object'],'annotated tag mismatch')
 need(RELEASE.stat().st_flags & stat.UF_IMMUTABLE,'golden protection missing')
 for path,h in read(RELEASE/'SHA256SUMS.json').items():need(digest(RELEASE/path)==h,'golden checksum mismatch: '+path)
 for item in read(RELEASE/'dependencies/dependency_hashes.json'):need(digest(item['path'])==item['sha256'],'dependency drift: '+item['module'])
 return d
def ident(value):
 need(re.fullmatch(r'DEV[0-9]{3,6}(?:-[a-z0-9-]+)?',value),'invalid DEV identity');return value
class Dev:
 def __init__(self,name,home=HOME,repo=ROOT):
  self.name=ident(name);self.home=P(home).resolve();self.repo=P(repo).resolve();self.root=self.home/name
  self.meta=self.root/'dev.json';self.source=self.root/'source';self.state=self.root/'state'
 def load(self):
  d=read(self.meta);need(d['id']==self.name,'DEV identity mismatch')
  need(d['baseline_sha']==BASE,'incompatible parent baseline')
  need(P(d['source']).resolve()==self.source and P(d['state']).resolve()==self.state,'foreign source/state binding')
  need(not self.source.is_symlink() and not self.state.is_symlink(),'symlink authority rejected')
  cfg=read(RELEASE/'configuration/safe-runtime.json')
  need(d['canonical_readonly']==cfg['canonical_readonly'],'incorrect database binding')
  need(d['queue']==str(self.state/'monitor/queue'),'shared writable queue')
  need(d['providers']=='DENIED' and d['workers']==0,'unsafe provider/worker configuration')
  need(8150<=d['port']<=8999 and d['level'] in [1,2,3],'unsafe port/risk level')
  return d
 def head(self):return git(self.source,'rev-parse','HEAD').strip()
def port_free(port):
 s=socket.socket()
 try:s.bind(('127.0.0.1',port));return True
 except OSError:return False
 finally:s.close()
def create(dev,objective,scope,level=1,port=None):
 golden(dev.repo);need(objective.strip() and scope.strip(),'objective and permitted scope required')
 need(not any(p in dev.home.parts for p in ['tmp','.codex']),'DEV root must be durable, outside temporary worktrees')
 need(not dev.home.is_relative_to(dev.repo) and not dev.home.is_relative_to(RELEASE.parent),'DEV root overlaps source/releases')
 need(not dev.root.exists(),'DEV identity already exists; never overwrite')
 room(dev.home,'*',100)
 port=port or next((p for p in range(8150,8200) if port_free(p)),None)
 need(port and 8150<=port<=8999 and port_free(port),'preview port unavailable')
 dev.root.mkdir(parents=True);dev.state.mkdir()
 git(dev.repo,'worktree','add','-b','codex/'+dev.name.lower(),str(dev.source),BASE)
 cfg=read(RELEASE/'configuration/safe-runtime.json')
 d={'schema':1,'id':dev.name,'objective':objective,'permitted_scope':scope,'level':level,'baseline_sha':BASE,
  'source':str(dev.source),'state':str(dev.state),'port':port,'queue':str(dev.state/'monitor/queue'),
  'canonical_readonly':cfg['canonical_readonly'],'providers':'DENIED','workers':0,'tool_sha256':tool_id(),
  'known_exclusions':read(RELEASE/'RELEASE.json')['known_exclusions']}
 save(dev.meta,d);return d
def source_manifest(source):
 rows={}
 for entry in git(source,'ls-tree','-rz','HEAD').encode().split(b'\0'):
  if not entry:continue
  meta,name=entry.split(b'\t',1);mode,typ,oid=meta.decode().split();p=source/name.decode()
  need(typ=='blob','submodules need explicit closure qualification')
  need(p.is_symlink() or p.stat().st_size<CAP,'tracked file exceeds read budget')
  data=os.readlink(p).encode() if mode=='120000' else p.read_bytes()
  need(len(data)<CAP,'tracked file exceeds storage budget')
  need(hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()==oid,'source differs from commit: '+name.decode())
  rows[name.decode()]={'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),'git_blob':oid}
  need(len(rows)<=10000 and sum(x['bytes'] for x in rows.values())<CAP,'tracked source count/size budget')
 need(sum(x['bytes'] for x in rows.values())<CAP,'source tree exceeds bounded archive design')
 return rows
# Explicitly qualified historical evidence, never a path-wide Python exemption.
ARCHIVAL_SCRIPTS = {
 'docs/audits/dev051/create-v1/offline_qualification.py': {
  'sha256':'88483a1db4e4a7d25ca99d6340991283d515949296b54ad78c129187236b3674',
  'origin_commit':'28a87467962f975085e2ad21ab8fb3390f8359d7',
  'purpose':'Preserved DEV051 pre-correction offline compatibility experiment; not current executable composition',
 },
}
def verify_import_closure(dev,verification,imported):
 manifest=verification['manifest'];archival={}
 required={x['path'] for x in read(RELEASE/'dependencies/project_modules.json')}
 for path,record in ARCHIVAL_SCRIPTS.items():
  if path not in manifest:
   ancestor=git(dev.source,'merge-base',record['origin_commit'],'HEAD').strip()
   need(ancestor!=record['origin_commit'],'missing preserved archival evidence: '+path)
   continue
  need(path.startswith('docs/audits/') and path.endswith('.py'),'invalid archival classification')
  need(path not in imported and path not in required,'archival evidence used as runtime dependency: '+path)
  need(manifest[path]['sha256']==record['sha256'] and digest(dev.source/path)==record['sha256'],'archival evidence hash mismatch: '+path)
  need(hashlib.sha256(git(dev.source,'show',record['origin_commit']+':'+path).encode()).hexdigest()==record['sha256'],'archival provenance mismatch: '+path)
  # References from executable/test/restore configuration invalidate the classification.
  tokens=(path,P(path).name,P(path).stem,path[:-3].replace('/','.'))
  for other in manifest:
   if other==path or not other.startswith(('src/','tests/','tools/','scripts/','config/','bin/','docs/audits/dev043/')):continue
   if P(other).suffix not in ('.py','.sh','.json','.toml','.ini','.conf','.yaml','.yml',''):continue
   need(not any(token in (dev.source/other).read_text(errors='replace') for token in tokens),'archival evidence referenced by executable contract: '+other)
  archival[path]=record
 need(all(p in imported or p in archival for p in verification['changed_files'] if p.endswith('.py')),'changed Python module not exercised/import-qualified by API or focused tests')
 return {'archival_evidence':archival,'rule':'All other changed Python requires observed import closure; all tracked evidence remains hash-verified and packaged'}

def minimum_level(changed):
 if any(p.startswith(('src/','database/','scripts/')) and re.search(r'(database|migration|attribution|admission|treasury|db_writer|core/db|monitor.*queue)',p) for p in changed):return 3
 if any(p.startswith(('src/','tools/')) for p in changed):return 2
 return 1
def validate_source(dev,d):
 for x in read(RELEASE/'dependencies/project_modules.json'):
  p=dev.source/x['path'];need(p.is_file(),'missing required source module: '+x['path'])
 need(not git(dev.source,'status','--porcelain','--untracked-files=all').strip(),'dirty candidate or untracked runtime dependency')
 need(subprocess.run(['git','-C',str(dev.source),'merge-base','--is-ancestor',BASE,dev.head()],capture_output=True).returncode==0,'unverified baseline ancestry')
 manifest=source_manifest(dev.source);baseline=read(RELEASE/'dependencies/project_modules.json')
 for x in baseline:ast.parse((dev.source/x['path']).read_text(),filename=x['path'])
 need(digest(dev.source/BOOT)==read(RELEASE/'configuration/safe-runtime.json')['bootstrap_sha256'],'isolation guard changed')
 changed=git(dev.source,'diff','--name-only',BASE,'HEAD').splitlines();need(d['level']>=minimum_level(changed),'qualification risk level too low')
 return manifest,changed
def env(state,port):return {'PATH':'/usr/bin:/bin:/usr/sbin:/sbin','HOME':str(state),'DEV043_STATE_ROOT':str(state),'DEV043_PORT':str(port),'TMPDIR':str(state),'PYTHONDONTWRITEBYTECODE':'1'}
def limits():resource.setrlimit(resource.RLIMIT_FSIZE,(8_388_608,8_388_608))
def frontend_runtime():
 need(NODE.is_file() and digest(NODE)==NODE_SHA256,'qualified concrete Node runtime missing/changed; no shim/install fallback')
 return {'path':str(NODE),'sha256':NODE_SHA256,'version':NODE_VERSION}

def run_log(command,log,cwd,environment,timeout=60):
 need(not log.exists(),'run log already exists')
 receipt={'command':list(map(str,command)),'cwd':str(cwd),'timeout_seconds':timeout,
  'environment':{k:environment[k] for k in ('PATH','HOME','TMPDIR') if k in environment},
  'stdout':str(log),'stderr':'STDOUT','file_limit_bytes':8_388_608,'status':'STARTED'}
 metadata=log.with_suffix(log.suffix+'.command.json');save(metadata,receipt);started=time.monotonic()
 try:
  with log.open('xb') as f:
   r=subprocess.run(command,cwd=cwd,env=environment,stdout=f,stderr=subprocess.STDOUT,timeout=timeout,preexec_fn=limits)
  receipt.update(returncode=r.returncode,signal=signal.Signals(-r.returncode).name if r.returncode<0 else None,status='PASS' if r.returncode==0 else 'FAIL')
 except subprocess.TimeoutExpired:
  receipt.update(status='TIMEOUT',timed_out=True,cleanup='subprocess.run killed and waited for its owned child')
 except BaseException as exc:
  receipt.update(status='ERROR',error_type=type(exc).__name__)
  raise
 finally:
  receipt.update(elapsed_seconds=time.monotonic()-started,log_bytes=log.stat().st_size if log.exists() else 0)
  save(metadata,receipt)
 need(receipt['status']=='PASS','guarded command '+receipt['status']+'; returncode='+str(receipt.get('returncode'))+' signal='+str(receipt.get('signal'))+'; inspect '+str(metadata))
 need(log.stat().st_size<8_388_608,'log budget')
 return {'command':command,'log':str(log),'sha256':digest(log),'status':'PASS','execution':receipt,'execution_receipt':str(metadata)}
def preflight(source,state,port):
 state.mkdir(parents=True,exist_ok=False);log=state/'preflight.log'
 run_log([PYTHON,'-I','-B',str(source/BOOT),'--self-test'],log,source,env(state,port))
 result=json.loads(log.read_text().splitlines()[-1]);need(result['status']=='PASS','isolation preflight failed');save(state/'preflight.json',result)
 e=env(state,port);e['DEV043_ISOLATION_RECEIPT']=str(state/'preflight.json');return e
def loaded(source,state,manifest,receipt_name='loaded_source_receipt.json'):
 r=read(state/receipt_name);need(r['source_root']==str(source),'wrong loaded source root');need(not r['isolation_events'],'guard denials during qualification')
 modules=[];external={}
 for m in r['modules']:
  p=P(m['path']).resolve()
  if p.is_relative_to(source):
   rel=str(p.relative_to(source));need(rel in manifest and m['sha256']==manifest[rel]['sha256'],'untracked or altered runtime dependency: '+rel);modules.append(rel)
  else:
   need(p.is_relative_to(P(PYTHON).resolve().parents[1]) or p==P(__file__).with_name('flex_dev_guarded.py').resolve(),'foreign worktree/dependency origin: '+str(p))
   external[str(p)]=m['sha256']
 need(not list((state/'monitor/queue').rglob('*.json')),'shared/operational queue mutation')
 return {'modules':modules,'external_hashes':external,'guard_denials':0,'queue_files':0,'bindings':r['bindings']}
def verify(dev):
 room(dev.state,'verify-*',40)
 golden(dev.repo);d=dev.load();manifest,changed=validate_source(dev,d);state=dev.state/('verify-'+uuid.uuid4().hex[:12]);e=preflight(dev.source,state,d['port'])
 run_log([PYTHON,'-I','-B',str(dev.source/BOOT),'--probe-startup'],state/'probe.log',dev.source,e)
 closure=loaded(dev.source,state,manifest);result={'status':'PASS','source_sha':dev.head(),'baseline_sha':BASE,'manifest':manifest,'changed_files':changed,'closure':closure,'dependencies':runtime_versions(closure['external_hashes']),'config':d,'tool_sha256':tool_id()}
 save(dev.root/'verification.json',result);return result
def http(port,path):
 need(path.startswith('/') and '://' not in path,'invalid API path')
 try:r=urllib.request.urlopen('http://127.0.0.1:'+str(port)+path,timeout=6)
 except urllib.error.HTTPError as x:r=x
 body=r.read(SMALL);need(len(body)<SMALL,'HTTP response budget exceeded')
 return r.code,body
class Server:
 def __init__(self,source,state,port):self.source,self.state,self.port=source,state,port
 def __enter__(self):
  need(port_free(self.port),'preview port became occupied');e=preflight(self.source,self.state,self.port);self.log=(self.state/'server.log').open('xb')
  self.process=subprocess.Popen([PYTHON,'-I','-B',str(self.source/BOOT)],cwd=self.source,env=e,stdout=self.log,stderr=subprocess.STDOUT,preexec_fn=limits)
  try:
   deadline=time.monotonic()+30
   while time.monotonic()<deadline:
    need(self.process.poll() is None,'isolated API exited during startup')
    try:
     status,b=http(self.port,'/dev-runtime/health');r=json.loads(b)
     if status==200 and r['source_root']==str(self.source) and r['state_root']==str(self.state):return self
    except (OSError,ValueError):pass
    time.sleep(.15)
   raise Hold('API startup deadline')
  except BaseException:self.__exit__(None,None,None);raise
 def __exit__(self,*args):
  if self.process.poll() is None:
   self.process.send_signal(signal.SIGINT)
   try:self.process.wait(timeout=5)
   except subprocess.TimeoutExpired:self.process.terminate();self.process.wait(timeout=5)
  self.log.close()
def api_acceptance(port):
 checks={};status,b=http(port,'/api/operations/live-monitor');need(status==200,'registry API unavailable');d=json.loads(b)
 need({o['display_name'].lower() for o in d['operation_summaries']}=={'byzantine','watchtower','nexus'},'registry identity regression')
 rows=d['recent_operation_tokens'];need(len(rows)==20 and len({r['mint'] for r in rows})==20,'latest20 population regression')
 need(rows==sorted(rows,key=lambda r:(r['assignment_timestamp'],r['mint']),reverse=True),'chronology regression')
 expected={'6LdjC13zbAxmruKrqsAQ4znUrjNe7gXXVeCHDBMYpump':(162776,274477),'6KuKphbVWZoz4kagf5QGaJEsUxJBY99LdBetfvwtpump':(125378,125378)}
 by={r['mint']:r for r in rows}
 for mint,(entry,peak) in expected.items():
  need(mint in by and abs(by[mint]['entry_mc_usd']-entry)<1 and abs(by[mint]['peak_mc_usd']-peak)<1,'retained price regression')
 checks['registry_history_prices']={'status':'PASS','rows':20,'identity_timestamps':[[r['mint'],r['assignment_timestamp']] for r in rows],'price_provenance':[[r['mint'],r['entry_method'],r['peak_scope']] for r in rows if r['entry_mc_usd'] is not None]}
 for path in ['/healthz','/ops-os','/system-health','/operations/playbooks','/operations/watchtower/playbook',ROUTES['watchtower_admission']]:
  status,b=http(port,path);need(status==200,'baseline route regression: '+path)
  if path==ROUTES['watchtower_admission']:need(b'REJECTED / NOT ADMITTED' in b and b'Confirmed / admitted memberships' in b,'admission distinction regression')
  checks[path]={'status':status,'sha256':hashlib.sha256(b).hexdigest()}
 for op in ['d8ee4d7a-fcd6-5a5b-b897-24f6ab56e334','bd7d7479-1454-5d41-9f68-115550348f3e']:
  t=time.monotonic();status,b=http(port,'/api/ops/operators/'+op);elapsed=time.monotonic()-t
  need(status in [200,503] and elapsed<3.5,'detail resource deadline regression')
  if status==503:need(b'UNAVAILABLE' in b or b'unavailable' in b,'detail error not explicit')
  checks[op]={'status':status,'seconds':elapsed}
 return checks
def validate_health_browser(api,text):
 """Require visible Health to match the tool-captured read-only authority."""
 states={'HEALTHY','WARNING','CRITICAL','UNKNOWN'}
 need(isinstance(api,dict) and api.get('status') in states,'missing/invalid Health API authority')
 need(isinstance(api.get('ts'),(int,float)) and not isinstance(api.get('ts'),bool),'missing Health observation time')
 caps=api.get('capabilities');incidents=api.get('incidents')
 need(isinstance(caps,dict) and isinstance(caps.get('live_ingestion'),dict) and isinstance(incidents,list),'missing Health capability evidence')
 for cap in caps.values():
  need(isinstance(cap,dict) and cap.get('status') in states,'invalid Health capability state')
  need(isinstance(cap.get('signals'),list),'missing Health signals')
 for inc in incidents:
  need(isinstance(inc,dict) and inc.get('severity') in {'WARNING','CRITICAL'} and isinstance(inc.get('title'),str) and inc['title'],'invalid Health incident')
 state=api['status']
 if state=='HEALTHY':
  need(not incidents and all(c['status']=='HEALTHY' for c in caps.values()),'false Healthy with degraded/unknown evidence')
  need(all(not signal.get('abnormal') for c in caps.values() for signal in c['signals']),'false Healthy with abnormal/stale signals')
  evidence=caps['live_ingestion'].get('evidence',{})
  need(isinstance(evidence,dict) and isinstance(evidence.get('total'),int) and evidence['total']>0,'false Healthy with missing ingestion evidence')
 if any(c['status']=='CRITICAL' for c in caps.values()) or any(i['severity']=='CRITICAL' for i in incidents):
  need(state=='CRITICAL','Health authority hides Critical evidence')
 if state=='UNKNOWN':need(not incidents,'Unknown authority contradicts incidents')
 order={'live_ingestion':0,'creator_funding':1,'operational_intelligence':2,'infrastructure':3,'watchtower':4}
 ordered=sorted(incidents,key=lambda i:order.get(i.get('capability'),999))
 critical=[i for i in ordered if i['severity']=='CRITICAL'];warning=[i for i in ordered if i['severity']=='WARNING']
 if state=='CRITICAL':expected='Critical — '+critical[0]['title'] if critical else 'System Critical'
 elif state=='WARNING':expected='Warning — '+warning[0]['title'] if warning else 'System Warning'
 elif state=='UNKNOWN':expected='System Health Unknown'
 else:expected='System Healthy'
 need(expected in text,'Health API/browser state mismatch: expected '+expected)
 if state!='HEALTHY':need('System Healthy' not in text,'Critical/Unknown incorrectly rendered Healthy')
 if state!='UNKNOWN':need('System Health Unknown' not in text,'known Health state incorrectly rendered Unknown')
 return {'status':state,'expected_banner':expected,'authority':'retained read-only operational evidence','isolated_live_acquisition':False}

def validate_browser(document,session,base_dir):
 need(document.get('source_sha')==session['source_sha'] and document.get('run_id')==session['run_id'],'incorrect browser source SHA/session')
 need(document.get('status')=='PASS','unqualified browser acceptance')
 evidence=document.get('features',{});need(set(FEATURES)<=set(evidence),'missing browser acceptance features')
 result={};total=0
 for feature,terms in FEATURES.items():
  e=evidence[feature];need(e.get('url')=='http://127.0.0.1:'+str(session['port'])+ROUTES[feature],'browser URL/authority mismatch')
  dom=(base_dir/e['dom']).resolve();shot=(base_dir/e['screenshot']).resolve()
  need(dom.is_relative_to(base_dir.resolve()) and shot.is_relative_to(base_dir.resolve()),'browser evidence escapes input directory')
  need(dom.stat().st_size<SMALL and shot.stat().st_size<SMALL,'browser artifact budget')
  text=dom.read_text();need(all(term in text for term in terms),'browser contract regression: '+feature)
  health=None
  if feature=='core_health':
   authority=session.get('health_api');need(isinstance(authority,dict),'missing tool-captured Health authority')
   need(authority.get('source_sha')==session['source_sha'] and authority.get('run_id')==session['run_id'],'Health authority session mismatch')
   need(authority.get('url')=='http://127.0.0.1:'+str(session['port'])+'/api/health/full','Health API authority URL mismatch')
   hp=P(authority['path']).resolve();need(hp.is_relative_to(P(session['receipt_dir']).resolve()),'Health authority escapes session')
   need(digest(hp)==authority['sha256'],'Health API evidence changed')
   health=validate_health_browser(read(hp),text)

  need(shot.stat().st_size>=512,'screenshot is not a complete capture')
  if feature=='mission_control':need(re.search(r'Active Operators [0-9]+ known actors',text) and re.search(r'Inbox [0-9]+ active items',text),'Mission Control data still loading/unavailable')
  if feature=='pending':need('Loading treasury review workspace' not in text,'indefinite pending spinner')
  header=shot.read_bytes()[:8];need(header.startswith(b'\xff\xd8') or header==b'\x89PNG\r\n\x1a\n','missing real screenshot')
  total+=dom.stat().st_size+shot.stat().st_size;need(total<16_000_000,'browser evidence total budget')
  result[feature]={'status':'PASS','dom_path':str(dom),'screenshot_path':str(shot),'dom_sha256':digest(dom),'screenshot_sha256':digest(shot),'url':e['url']}
  if health is not None:result[feature].update(health=health,health_api_path=str(hp),health_api_sha256=authority['sha256'])
 return result
def browser(dev,path):
 session=read(dev.root/'active-session.json');need(session['source_sha']==dev.head(),'source changed during browser check')
 need(session['status']=='AWAITING_BROWSER','no active browser acceptance session')
 doc=read(path);evidence=validate_browser(doc,session,P(path).resolve().parent)
 save(P(session['receipt_dir'])/'browser.json',{'status':'PASS','source_sha':session['source_sha'],'run_id':session['run_id'],'features':evidence})
 return {'status':'PASS','run_id':session['run_id'],'features':list(evidence)}
def check_browser_files(b):
 for item in b['features'].values():
  need(digest(item['dom_path'])==item['dom_sha256'] and digest(item['screenshot_path'])==item['screenshot_sha256'],'browser evidence changed after acceptance')
  if 'health_api_path' in item:need(digest(item['health_api_path'])==item['health_api_sha256'],'Health API evidence changed after acceptance')
def qualify(dev,focused,wait=180,extra=None,restoring=False):
 verification=verify(dev);d=dev.load();need(focused,'focused test required')
 for p in focused:need(p.startswith('tests/') and p in verification['manifest'],'focused test must be tracked')
 if d['level']>=2:
  need(extra,'Level2/3 requires explicit affected-subsystem contract evidence')
  evidence=read(extra);need(evidence.get('source_sha')==dev.head() and evidence.get('status')=='PASS' and evidence.get('dependency_closure') and evidence.get('provider_budget') and evidence.get('queue_safety'),'incomplete Level2 subsystem qualification')
  if d['level']==3:need(evidence.get('canonical_preservation') and evidence.get('rollback_verification') and evidence.get('explicit_target_authorization'),'Level3 evidence/explicit target authorization missing')
  # This command still has no deployment implementation, even with authorization evidence.
 runid=uuid.uuid4().hex[:12];runs=dev.root/'runs';runs.mkdir(exist_ok=True);room(runs,'*',20)
 out=runs/runid;out.mkdir();state=dev.state/runid
 session={'run_id':runid,'source_sha':dev.head(),'port':d['port'],'receipt_dir':str(out),'source':str(dev.source),'state':str(state),'status':'STARTING','restoring':restoring}
 save(dev.root/'active-session.json',session)
 try:
  tests_state=dev.state/('tests-'+runid);test_env=preflight(dev.source,tests_state,d['port'])
  fixed=['tests/test_dev043_detail_resource_safety.py','tests/dev043/pending_request_lifecycle.cjs']
  for path in fixed:need((dev.source/path).read_bytes()==subprocess.check_output(['git','-C',str(dev.source),'show',BASE+':'+path]),'baseline acceptance test modified without a new qualified contract')
  runner=P(__file__).with_name('flex_dev_guarded.py');need(runner.is_file(),'guarded test runner missing')
  tests=run_log([PYTHON,'-I','-B',str(runner),str(dev.source),*dict.fromkeys([fixed[0],*focused])],out/'pytest.log',dev.source,test_env,120)
  test_closure=loaded(dev.source,tests_state,verification['manifest'],'test-module-receipt.json')
  import shutil
  node_runtime=frontend_runtime();node=node_runtime['path']
  js=run_log([node,str(dev.source/fixed[1])],out/'frontend.log',dev.source,{'PATH':os.environ.get('PATH',''),'HOME':str(tests_state)},30)
  js['runtime']=node_runtime
  with Server(dev.source,state,d['port']) as server:
   session.update(status='API_ACCEPTANCE',pid=server.process.pid);save(dev.root/'active-session.json',session)
   checks=api_acceptance(d['port']);save(out/'api.json',checks)
   health_status,health_body=http(d['port'],'/api/health/full');need(health_status==200,'Health API unavailable')
   health_api=json.loads(health_body);need(isinstance(health_api,dict),'invalid Health API response')
   health_path=out/'health-api.json';health_path.write_bytes(health_body)
   session['health_api']={'path':str(health_path),'sha256':digest(health_path),'url':'http://127.0.0.1:'+str(d['port'])+'/api/health/full','source_sha':session['source_sha'],'run_id':runid}

   session['status']='AWAITING_BROWSER';save(dev.root/'active-session.json',session)
   print(json.dumps({'status':'AWAITING_BROWSER','id':dev.name,'run_id':runid,'source_sha':dev.head(),'url':'http://127.0.0.1:'+str(d['port'])+'/intelligence/operators','session_file':str(dev.root/'active-session.json')}),flush=True)
   deadline=time.monotonic()+wait
   while not (out/'browser.json').exists() and time.monotonic()<deadline:time.sleep(.25)
   need((out/'browser.json').exists(),'browser acceptance missing; API stopped without qualification')
   browser_receipt=read(out/'browser.json');check_browser_files(browser_receipt)
  need(tool_id()==verification['tool_sha256'],'tooling changed during qualification')
  closure=loaded(dev.source,state,verification['manifest'])
  imported=set(closure['modules'])|set(test_closure['modules'])
  classification=verify_import_closure(dev,verification,imported)
  need(dev.head()==verification['source_sha'],'source changed during acceptance');validate_source(dev,d)
  comparison={k:{'baseline_behaviour':FEATURES[k],'candidate_behaviour':'Real browser contract PASS, scoped API/tests pass','verdict':'EQUIVALENT'} for k in FEATURES}
  result={'status':'PASS','source_sha':dev.head(),'baseline_sha':BASE,'run_id':runid,'config':d,'level':d['level'],'tests':tests,'frontend':js,'api':checks,'browser':browser_receipt,'closure':closure,'test_closure':test_closure,'source_classification':classification,'dependencies':runtime_versions({**closure['external_hashes'],**test_closure['external_hashes']}),'comparison':comparison,'changed_files':verification['changed_files'],'known_exclusions':d['known_exclusions'],'providers':0,'canonical_writes':0,'queue_mutations':0,'server_stopped':True,'restoring':restoring,'tool_sha256':tool_id()}
  save(out/'qualification.json',result);save(dev.root/('restoration.json' if restoring else 'qualification.json'),result)
  session['status']='STOPPED_PASS';save(dev.root/'active-session.json',session);return result
 except BaseException:
  session['status']='STOPPED_HOLD';save(dev.root/'active-session.json',session);raise
def eligible(dev):
 d=dev.load();manifest,changed=validate_source(dev,d);q=read(dev.root/'qualification.json')
 need(q.get('status')=='PASS' and q['source_sha']==dev.head(),'unqualified or incorrect source SHA')
 need(q['config']==d and q['tool_sha256']==tool_id(),'configuration/tool changed after qualification')
 need(q['server_stopped'] and not q['closure']['guard_denials'],'unsafe qualification')
 need(set(q['browser']['features'])>=set(FEATURES),'unqualified browser acceptance');check_browser_files(q['browser'])
 for key in ['tests','frontend']:need(digest(q[key]['log'])==q[key]['sha256'],'test evidence changed')
 need(all(v['verdict'] in ['EQUIVALENT','IMPROVED'] for v in q['comparison'].values()),'unqualified regression/UNKNOWN')
 return d,manifest,q
def seal(directory):
 for p in sorted(directory.rglob('*'),key=lambda p:len(p.parts),reverse=True)+[directory]:
  need(p.stat().st_size<CAP if p.is_file() else True,'candidate storage budget')
  p.chmod(0o555 if p.is_dir() else 0o444);os.chflags(p,p.stat().st_flags|stat.UF_IMMUTABLE)
def candidate(dev):
 d,manifest,q=eligible(dev);need(RELEASE.is_dir(),'rollback reference missing');golden(dev.repo)
 import shutil
 sha=dev.head();name=dev.name+'_CANDIDATE_'+sha[:12];dest=RELEASE.parent/'candidates'/name;need(not dest.exists(),'candidate already preserved; never overwrite')
 need(subprocess.run(['git','-C',str(dev.source),'show-ref','--verify','--quiet','refs/tags/'+name]).returncode!=0,'candidate tag already exists')
 dest.mkdir(parents=True);save(dest/'source-manifest.json',manifest);save(dest/'configuration.json',d);save(dest/'qualification.json',q)
 logs=dest/'test-evidence';logs.mkdir()
 for kind in ['tests','frontend']:shutil.copyfile(q[kind]['log'],logs/(kind+'.log'))
 save(dest/'loaded-module-hashes.json',{p:manifest[p]['sha256'] for p in set(q['closure']['modules'])|set(q['test_closure']['modules'])})
 import shutil
 for name0 in ['project_modules.json','dependency_hashes.json']:shutil.copyfile(RELEASE/'dependencies'/name0,dest/name0)
 save(dest/'versions.json',q['dependencies'])
 save(dest/'rollback.json',{'source_sha':BASE,'release':str(RELEASE),'configuration':str(RELEASE/'configuration/safe-runtime.json'),'instructions':str(RELEASE/'instructions/RESTORE_AND_ROLLBACK.md'),'verified_source_sha':read(RELEASE/'RELEASE.json')['source_sha']})
 with (dest/'source.tar.gz').open('xb') as f:
  r=subprocess.run(['git','-C',str(dev.source),'archive','--format=tar.gz',sha],stdout=f,stderr=subprocess.PIPE,timeout=60,preexec_fn=lambda:resource.setrlimit(resource.RLIMIT_FSIZE,(CAP,CAP)));need(r.returncode==0,'candidate archive failed storage/command limit')
 save(dest/'release.json',{'id':dev.name,'source_sha':sha,'parent_baseline':BASE,'tag':name,'status':'QUALIFIED_NOT_DEPLOYED','archive_sha256':digest(dest/'source.tar.gz'),'scope':'bounded recovery plus explicitly qualified changes','deployment_authorized':False,'known_exclusions':d['known_exclusions']})
 # Include browser artifacts, not just transient links, so release evidence survives the DEV workspace.
 b=dest/'browser';b.mkdir()
 for feature,e in q['browser']['features'].items():
  shutil.copyfile(e['dom_path'],b/(feature+'.txt'));shutil.copyfile(e['screenshot_path'],b/(feature+'.jpg'))
 save(dest/'SHA256SUMS.json',{str(p.relative_to(dest)):digest(p) for p in dest.rglob('*') if p.is_file()})
 git(dev.source,'tag','-a',name,sha,'-m','Qualified candidate; manifest '+digest(dest/'release.json')+'; NO DEPLOYMENT AUTHORIZATION')
 tagpath=P(git(dev.source,'rev-parse','--git-path','refs/tags/'+name).strip()).resolve();tagpath.chmod(0o444);os.chflags(tagpath,tagpath.stat().st_flags|stat.UF_IMMUTABLE)
 seal(dest);receipt={'status':'PASS','candidate':str(dest),'tag':name,'source_sha':sha,'manifest_sha256':digest(dest/'release.json'),'deployment_authorized':False};save(dev.root/'candidate.json',receipt);return receipt
def validate_candidate(dev):
 c=read(dev.root/'candidate.json');dest=P(c['candidate']);need(dest.is_relative_to(RELEASE.parent/'candidates'),'foreign candidate authority')
 need(dest.stat().st_flags & stat.UF_IMMUTABLE,'candidate protection missing')
 for p,h in read(dest/'SHA256SUMS.json').items():need(digest(dest/p)==h,'candidate manifest/hash mismatch')
 r=read(dest/'release.json');need(r['source_sha']==c['source_sha']==dev.head(),'incorrect candidate source SHA')
 need(git(dev.source,'rev-parse',c['tag']+'^{}').strip()==dev.head(),'candidate tag/source mismatch')
 rb=read(dest/'rollback.json');need(rb['source_sha']==BASE and P(rb['release'])==RELEASE,'missing rollback reference');golden(dev.repo)
 return dest,r
def restore(dev,focused,wait,extra=None):
 room(dev.root/'restorations','*',20);room(dev.root/'runs','*',20)
 dest,r=validate_candidate(dev);original=dev.source;new=dev.root/'restorations'/uuid.uuid4().hex[:12]/'source';new.parent.mkdir(parents=True)
 git(dev.repo,'worktree','add','--detach',str(new),r['source_sha']);expected=read(dest/'source-manifest.json');need(source_manifest(new)==expected,'clean restoration source mismatch')
 # New source is temporary qualification authority only; saved DEV bindings remain canonical.
 rd=Dev(dev.name,dev.home,dev.repo);rd.source=new
 original_meta=dev.load();restored_config={**original_meta,'source':str(new)}
 class Restored(Dev):
  def load(self):return restored_config
 rd.__class__=Restored
 result=qualify(rd,focused,wait,extra,restoring=True);need(result['source_sha']==dev.head(),'restored SHA mismatch');return result
def promote_dry_run(dev,target,evidence):
 dest,r=validate_candidate(dev);need(evidence,'runtime/rollback authority evidence required; no target guessed')
 restored=read(dev.root/'restoration.json');need(restored['status']=='PASS' and restored['source_sha']==r['source_sha'] and restored['server_stopped'],'clean-checkout restoration unqualified')
 e=read(evidence);need(e.get('target')==target,'incorrect intended target')
 need(e.get('source_sha') and e.get('source_path') and e.get('manager_identity') and e.get('configuration_path'),'unknown installed runtime authority')
 installed=P(e['source_path']);need(git(installed,'rev-parse','HEAD').strip()==e['source_sha'],'installed source mismatch')
 need(not git(installed,'status','--porcelain','--untracked-files=all').strip(),'dirty installed rollback source')
 need(digest(e['configuration_path'])==e.get('configuration_sha256'),'unverified rollback configuration')
 need(e.get('rollback_source_sha')==e['source_sha'] and e.get('rollback_source_manifest'),'rollback source/configuration incomplete')
 need(source_manifest(installed)==read(e['rollback_source_manifest']),'rollback source bytes not verified')
 need(e.get('canonical_readonly')==dev.load()['canonical_readonly'] and e.get('provider_transport')=='DENIED','unknown database/provider authority')
 need(P(e['writable_state']).resolve().is_relative_to(dev.root) and P(e['queue']).resolve().is_relative_to(dev.root),'shared writable state/queue')
 need(e.get('post_deployment_acceptance'),'post-deployment acceptance missing')
 if not e.get('disposable_fixture'):
  need(e.get('pid') and e.get('rollback_configuration_path') and e.get('rollback_acceptance'),'runtime/rollback evidence incomplete')
  cfg=plistlib.loads(P(e['configuration_path']).read_bytes())
  need(cfg.get('Label')==e['manager_identity'] and P(cfg.get('WorkingDirectory','')).resolve()==installed.resolve(),'service manager configuration mismatch')
  info=subprocess.run(['launchctl','print','gui/'+str(os.getuid())+'/'+cfg['Label']],capture_output=True,text=True,timeout=5)
  need(info.returncode==0 and re.search(r'pid = '+str(int(e['pid']))+r'\b',info.stdout),'service ownership not observed')
  cwd=subprocess.run(['lsof','-a','-p',str(int(e['pid'])),'-d','cwd','-Fn'],capture_output=True,text=True,timeout=5)
  need(('n'+str(installed.resolve())) in cwd.stdout,'process source working directory mismatch')
  need(digest(e['rollback_configuration_path'])==e['configuration_sha256'],'rollback configuration copy not verified')
  rb=read(e['rollback_acceptance']);need(rb.get('source_sha')==e['source_sha'] and rb.get('status')=='PASS' and rb.get('configuration_sha256')==e['configuration_sha256'],'rollback restoration not qualified')
  need(e.get('health_port') and 1024<=int(e['health_port'])<=65535,'unknown health authority')
  status,b=http(int(e['health_port']),'/dev-runtime/health');health=json.loads(b)
  need(status==200 and health['source_root']==str(installed) and health['mode']=='read-only','installed health/source mismatch')
 else:
  need(e.get('fixture_qualification') and read(e['fixture_qualification']).get('source_sha')==e['source_sha'],'fixture has no observed qualification receipt')
 result={'status':'PASS_FIXTURE_DRY_RUN' if e.get('disposable_fixture') else 'PASS_QUALIFICATION_ONLY','target':target,'candidate_sha':r['source_sha'],'installed_source_sha':e['source_sha'],'runtime_evidence_sha256':digest(evidence),'rollback_ready':True,'deployment_authorized':False,'actions_executed':[],'procedure':['separate explicit authorization naming target and exactrelease','preserve previoussource/configuration/statebindings','install durable candidate;never temporaryworktree','change only authorized service target;one controlledrestart','run recorded acceptance/isolation checks','on failure restore verified previous source/configuration;never canonical DB orqueue rewind']}
 save(dev.root/'promotion-dry-run.json',result);return result
def main(argv=None):
 resource.setrlimit(resource.RLIMIT_FSIZE,(CAP,CAP))
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--home',type=P,default=HOME);p.add_argument('--repository',type=P,default=ROOT)
 sub=p.add_subparsers(dest='command',required=True)
 c=sub.add_parser('create');c.add_argument('id');c.add_argument('--objective',required=True);c.add_argument('--scope',required=True);c.add_argument('--level',type=int,choices=[1,2,3],default=1);c.add_argument('--port',type=int)
 for name in ['verify','candidate']:s=sub.add_parser(name);s.add_argument('id')
 for name in ['qualify','restore']:
  s=sub.add_parser(name);s.add_argument('id');s.add_argument('--focused-test',action='append',required=True);s.add_argument('--browser-wait',type=int,default=180);s.add_argument('--contract-evidence',type=P)
 s=sub.add_parser('browser');s.add_argument('id');s.add_argument('--input',type=P,required=True)
 s=sub.add_parser('promote');s.add_argument('id');s.add_argument('--dry-run',action='store_true',required=True);s.add_argument('--target',choices=['persistent-dev','production'],required=True);s.add_argument('--runtime-evidence',type=P,required=True)
 a=p.parse_args(argv);dev=Dev(a.id,a.home,a.repository)
 try:
  if a.command=='create':r=create(dev,a.objective,a.scope,a.level,a.port)
  elif a.command=='verify':r=verify(dev);r={k:v for k,v in r.items() if k not in ['manifest','closure']}
  elif a.command=='qualify':r=qualify(dev,a.focused_test,a.browser_wait,a.contract_evidence)
  elif a.command=='browser':r=browser(dev,a.input)
  elif a.command=='candidate':r=candidate(dev)
  elif a.command=='restore':r=restore(dev,a.focused_test,a.browser_wait,a.contract_evidence)
  else:r=promote_dry_run(dev,a.target,a.runtime_evidence)
  print(json.dumps({k:v for k,v in r.items() if k not in ['manifest','closure','comparison','browser','api']},indent=2));return 0
 except (Hold,OSError,ValueError,subprocess.SubprocessError) as ex:
  print(json.dumps({'status':'HOLD','reason':str(ex)}),file=sys.stderr);return 2
if __name__=='__main__':raise SystemExit(main())
