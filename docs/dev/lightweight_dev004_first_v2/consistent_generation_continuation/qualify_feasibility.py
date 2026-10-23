"""Test-only feasibility interpreter; no runtime entrypoint or production IO."""
import ast,copy,hashlib,importlib.util,json,os,resource,sys,tempfile,types
from pathlib import Path
resource.setrlimit(resource.RLIMIT_FSIZE,(16000000,16000000))
ROOT=Path('/Users/kevinkeaveney/.codex/worktrees/dev004-first-v2-prototype/flex')
OUT=Path(tempfile.mkdtemp(prefix='dev004-continuation-',dir='/private/tmp'))
MAX_N=256;MAX_PROFILE=16384;MAX_INPUT=4194304;MAX_STATE=32768;MAX_OUTPUT=8388608;PAIRS=128;ADMISSIONS=8
checks={};runs={}
def canonical(v):return json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()
def sha(v):return hashlib.sha256(canonical(v)).hexdigest()
def load(name,rel):
 p=ROOT/rel;s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
pop=load('accepted_population','runtime/walkback/src/ops/investigation_population.py');merge=load('accepted_merge','runtime/walkback/src/ops/canonical_merge_contract.py')
source=ROOT/'runtime/walkback/src/ops/emerging_operator_service.py';tree=ast.parse(source.read_text());cl=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='EmergingOperatorService');scope={}
for name in ['_cohesion_pair','_metric','_material_reasons']:
 fn=copy.deepcopy(next(n for n in cl.body if isinstance(n,ast.FunctionDef) and n.name==name));fn.decorator_list=[];exec(compile(ast.Module(body=[fn],type_ignores=[]),str(source),'exec'),scope)
compose=next(n for n in cl.body if isinstance(n,ast.FunctionDef) and n.name=='_compose');seg=compose.body
merge_start=next(i for i,n in enumerate(seg) if isinstance(n,ast.With))
body=seg[merge_start].body;start=next(i for i,n in enumerate(body) if isinstance(n,ast.AnnAssign) and isinstance(n.target,ast.Name) and n.target.id=='absorbed');merge_nodes=copy.deepcopy(body[start:])
rank_start=next(i for i,n in enumerate(seg) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='eligible' for t in n.targets));rank_nodes=copy.deepcopy(seg[rank_start:])
def project_rank(pops,canon,rejected):
 adapter=pop.LegacyFamilyProjectionAdapter(minimum_launches=2,completeness_threshold=50,dormancy_days=30,metric=scope['_metric'],material_reasons=scope['_material_reasons'],evidence_weights={'topology_reconstruction':20,'transaction_provenance':20,'relationship_coverage':15,'identity_link_coverage':15,'independent_evidence_diversity':15,'uncertainty_conflict_coverage':15})
 projected=[adapter.project(p) for p in pops]
 env={'families':copy.deepcopy(canon),'projected':projected,'rejected_wallets':frozenset(rejected),'evaluate_merge':merge.evaluate_merge}
 exec(compile(ast.Module(body=merge_nodes,type_ignores=[]),str(source),'exec'),env)
 # Execute exact accepted attention/rank tail as a function so return remains legal.
 f=ast.FunctionDef(name='rank_tail',args=ast.arguments(posonlyargs=[],args=[ast.arg(arg='self'),ast.arg(arg='families'),ast.arg(arg='now_mono')],kwonlyargs=[],kw_defaults=[],defaults=[]),body=rank_nodes,decorator_list=[])
 mod=ast.fix_missing_locations(ast.Module(body=[f],type_ignores=[]));ns={};exec(compile(mod,str(source),'exec'),ns)
 return ns['rank_tail'](types.SimpleNamespace(attention_budget=5),env['families'],0)
def builder():return pop.InvestigationPopulationBuilder(scope['_cohesion_pair'],lambda group,first:[])
SET_KEYS={'treasuries','creators','mechanisms','signatures','launches','sources','campaigns','provisioning_clients','walkback_descendants'}
def encode_profile(p):return {k:sorted(v) if isinstance(v,set) else v for k,v in p.items()}
def decode_profile(p):return {k:set(v) if k in SET_KEYS else v for k,v in p.items()}
def profile(i,coherent=True,excluded=False):
 w=f'wallet-{i:04}'
 return dict(wallet=w,treasuries={'root-a','root-b'} if coherent else {w},creators={w+f'-c-{n}' for n in range(5)},mechanisms={'PLAIN_XFER'},signatures={w+'-sig'},launches={w+'-mint'},first=1790000000,last=1792700000+i,evidence=[{'type':'SYNTHETIC_PARENT','source':'synthetic','selected':'selected-'+w,'alternative':'alternative-'+w}],sources={'synthetic','review','walkback'},sessions=3,active_sessions=1,outcomes=[],exclusions=['synthetic exclusion'] if excluded else [],warnings=[],edge_times=[1792700000+i],templates={},campaigns=set(),candidate={},session_amounts=[])
class Hold(Exception):pass
def atomic(path,value,cap):
 b=canonical(value)
 if len(b)>cap:raise Hold('ENCODED_CAP')
 temp=path.with_name(path.name+'.pending')
 with open(temp,'wb') as f:f.write(b);f.flush();os.fsync(f.fileno())
 os.replace(temp,path);fd=os.open(path.parent,os.O_RDONLY)
 try:os.fsync(fd)
 finally:os.close(fd)
def read(path,cap):
 with open(path,'rb') as f:b=f.read(cap+1)
 if len(b)>cap:raise Hold('READ_CAP')
 return json.loads(b)
def create(name,records,canonical_families=(),rejected=()):
 profiles={}
 for record_index,p in enumerate(records):
  if record_index>=512:raise Hold('SOURCE_RECORD_CAP')
  p=encode_profile(p);key=p['wallet']
  if len(canonical(p))>MAX_PROFILE:raise Hold('PROFILE_CAP')
  if key in profiles and profiles[key]!=p:raise Hold('CONFLICTING_DUPLICATE')
  profiles[key]=p
  if len(profiles)>MAX_N:raise Hold('POPULATION_CAP')
 inputs={'profiles':profiles,'canonical':list(canonical_families),'rejected':sorted(rejected),'clock':1792747000,'source_revision':'synthetic-generation-1','config':{'attention_budget':5},'source_hash':hashlib.sha256(source.read_bytes()).hexdigest()}
 if len(inputs['canonical'])>16:raise Hold('CANONICAL_CAP')
 if len(canonical(inputs))>MAX_INPUT:raise Hold('INPUT_CAP')
 generation=sha(inputs);base=OUT/name;base.mkdir();atomic(base/'input.json',inputs,MAX_INPUT)
 state={'generation':generation,'i':0,'g':0,'m':0,'groups':[],'complete':False,'steps':0,'pair_count':0};atomic(base/'state.json',state,MAX_STATE);return base
def step(base,revision='synthetic-generation-1'):
 inputs=read(base/'input.json',MAX_INPUT);s=read(base/'state.json',MAX_STATE)
 if sha(inputs)!=s['generation'] or inputs['source_revision']!=revision:raise Hold('SOURCE_GENERATION_CHANGED')
 keys=sorted(inputs['profiles']);profiles={k:decode_profile(v) for k,v in inputs['profiles'].items()};pairs=admissions=0
 while s['i']<len(keys) and pairs<PAIRS and admissions<ADMISSIONS:
  i=s['i'];groups=s['groups']
  if s['g']>=len(groups):groups.append([i]);s['i']+=1;s['g']=s['m']=0;admissions+=1;continue
  group=groups[s['g']]
  if s['m']>=len(group):group.append(i);s['i']+=1;s['g']=s['m']=0;admissions+=1;continue
  valid=scope['_cohesion_pair'](profiles[keys[i]],profiles[keys[group[s['m']]]])['valid'];pairs+=1
  if valid:s['m']+=1
  else:s['g']+=1;s['m']=0
 s['complete']=s['i']==len(keys);s['steps']+=1;s['pair_count']+=pairs;atomic(base/'state.json',s,MAX_STATE)
 assert pairs<=PAIRS and admissions<=ADMISSIONS
 return s,pairs
def final(base,publication,revision='synthetic-generation-1',pressure=False):
 inputs=read(base/'input.json',MAX_INPUT);s=read(base/'state.json',MAX_STATE)
 if sha(inputs)!=s['generation'] or revision!=inputs['source_revision'] or not s['complete']:raise Hold('INCOMPLETE_OR_CHANGED')
 keys=sorted(inputs['profiles']);indices=[i for g in s['groups'] for i in g]
 if sorted(indices)!=list(range(len(keys))):raise Hold('COVERAGE')
 profiles={k:decode_profile(v) for k,v in inputs['profiles'].items()}
 b=builder();actual=[b.build_group([profiles[keys[i]] for i in g]) for g in s['groups']];oracle=b.build(profiles)
 if [x.revision_id for x in actual]!=[x.revision_id for x in oracle]:raise Hold('ORACLE_MISMATCH')
 # Freeze evaluation time for both accepted adapters; no historical recomputation.
 old=pop.time.time;pop.time.time=lambda:inputs['clock']
 try:
  ranked=project_rank(actual,inputs['canonical'],inputs['rejected']);expected=project_rank(oracle,inputs['canonical'],inputs['rejected'])
 finally:pop.time.time=old
 if canonical(ranked)!=canonical(expected):raise Hold('RANK_MISMATCH')
 result={'generation':s['generation'],'complete':True,'population_revisions':[x.revision_id for x in actual],'ranked':ranked}
 if pressure:raise Hold('STORAGE_PRESSURE')
 atomic(publication,result,MAX_OUTPUT)
 return result,s
def run(name,records,canon=(),reject=(),restart=True):
 base=create(name,records,canon,reject);peak=0
 while True:
  s,pairs=step(base);peak=max(peak,pairs)
  # Each call reloads bounded immutable input and durable continuation: reopened every step.
  if s['complete']:break
 result,s=final(base,OUT/(name+'-publication.json'));runs[name]={'profiles':len(read(base/'input.json',MAX_INPUT)['profiles']),'steps':s['steps'],'pair_count':s['pair_count'],'peak_step_pair_evaluations':peak,'groups':len(result['ranked']),'input_bytes':(base/'input.json').stat().st_size,'checkpoint_bytes':(base/'state.json').stat().st_size,'publication_bytes':(OUT/(name+'-publication.json')).stat().st_size};return base,result
def check(name,v):checks[name]=bool(v);assert v,name
try:
 ADMISSIONS=1
 base,res=run('cross_batch',[profile(i) for i in range(3)]);check('cross_batch_exact_revision_and_rank',len(res['ranked'])==1 and runs['cross_batch']['steps']==3)
 ADMISSIONS=8
 base256,res256=run('case256',[profile(i,False) for i in range(256)]);check('case256_exact_full_oracle',len(res256['ranked'])==256);check('pair_step_cap',runs['case256']['peak_step_pair_evaluations']<=128);check('restart_each_step',runs['case256']['steps']>1)
 dup,rdup=run('duplicate',[profile(0),profile(0),profile(1)]);check('duplicate_source_identity',runs['duplicate']['profiles']==2)
 conflict=[profile(1),profile(2,excluded=True)];bc,rc=run('conflicting_parents',conflict);check('exclusion_and_competing_evidence_preserved',len(rc['ranked'])==2 and any(f['exclusion_evidence'] for f in rc['ranked']) and all(any(e.get('alternative') for e in f['supporting_evidence']) for f in rc['ranked']))
 # Canonical exact-wallet-set merge and rejected-wallet refusal through actual accepted merge code.
 canon=copy.deepcopy(res['ranked'][0]);canon.update(family_id='canonical-synthetic',stage='CONFIRMED',lifecycle_state='CONFIRMED',status='Confirmed',attention_eligible=False)
 bm,rm=run('canonical_merge',[profile(i) for i in range(3)],[canon]);check('canonical_merge_oracle',any(f.get('promoted_to_operation_id')=='canonical-synthetic' for f in rm['ranked']))
 br,rr=run('canonical_rejected',[profile(i) for i in range(3)],[canon],['wallet-0000']);check('canonical_rejected_guard',not any(f.get('promoted_to_operation_id') for f in rr['ranked']))
 publication=OUT/'last-good.json';atomic(publication,{'sentinel':'last-good'},MAX_OUTPUT);before=publication.read_bytes();incomplete=create('incomplete',[profile(i) for i in range(12)]);step(incomplete)
 try:final(incomplete,publication);raise AssertionError('partial accepted')
 except Hold:check('incomplete_fence_and_last_good',publication.read_bytes()==before)
 try:step(incomplete,'changed-revision');raise AssertionError('source change accepted')
 except Hold:check('source_change_fence',publication.read_bytes()==before)
 while not step(incomplete)[0]['complete']:pass
 try:final(incomplete,publication,pressure=True);raise AssertionError('pressure accepted')
 except Hold:check('storage_pressure_last_good',publication.read_bytes()==before)
 final(incomplete,publication);published=publication.read_bytes();final(incomplete,publication);check('repeated_finalization_deterministic',publication.read_bytes()==published)
 try:create('too_large',[profile(i,False) for i in range(257)]);raise AssertionError('overflow accepted')
 except Hold:check('total_population_cap_fail_closed',True)
 bad=profile(0);bad['last']=3
 try:create('bad_duplicate',[profile(0),bad]);raise AssertionError('conflicting duplicate accepted')
 except Hold:check('conflicting_duplicate_hold',True)
 changed=create('mutated_generation',[profile(0)]);obj=read(changed/'input.json',MAX_INPUT);obj['rejected']=['new-exclusion'];atomic(changed/'input.json',obj,MAX_INPUT)
 try:step(changed);raise AssertionError('changed evidence accepted')
 except Hold:check('content_change_fence',True)
 status='PASS_CAPPED_GROUPING_MERGE_RANK_CONTINUATION_FEASIBILITY'
except BaseException as exc:
 import traceback
 status='HOLD';runs['error']={'message':str(exc),'traceback':traceback.format_exc(limit=6)}
result={'status':status,'checks':checks,'runs':runs,'fixture_dir':str(OUT),'scope':'Test-only normalized frozen-generation interpreter, not runtime source extraction or deployment. Accepted builder oracle, adapter, merge and rank stages reused.','max_files':sorted([(p.name,p.stat().st_size) for p in OUT.rglob('*') if p.is_file()],key=lambda x:x[1],reverse=True)[:5]}
(OUT/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
