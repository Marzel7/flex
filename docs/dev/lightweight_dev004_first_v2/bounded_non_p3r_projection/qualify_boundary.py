import ast,importlib.util,sys,json,resource,hashlib
from pathlib import Path
resource.setrlimit(resource.RLIMIT_FSIZE,(16000000,16000000))
r=Path('/Users/kevinkeaveney/.codex/worktrees/dev004-first-v2-prototype/flex')
p=r/'runtime/walkback/src/ops/investigation_population.py';spec=importlib.util.spec_from_file_location('accepted_population',p);m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
src=r/'runtime/walkback/src/ops/emerging_operator_service.py';tree=ast.parse(src.read_text());cls=next(x for x in tree.body if isinstance(x,ast.ClassDef) and x.name=='EmergingOperatorService');fn=next(x for x in cls.body if isinstance(x,ast.FunctionDef) and x.name=='_cohesion_pair');fn.decorator_list=[];ns={};exec(compile(ast.Module(body=[fn],type_ignores=[]),str(src),'exec'),ns);cohesion=ns['_cohesion_pair']
def profile(wallet,compatible=True):
 return dict(wallet=wallet,treasuries={'root-a','root-b'} if compatible else {wallet},creators={f'{wallet}-creator-{i}' for i in range(5)},mechanisms={'PLAIN_XFER'},signatures={wallet+'-sig'},launches={wallet+'-mint'},first=1,last=2,evidence=[],sources={'synthetic'},sessions=0,active_sessions=0,outcomes=[],exclusions=[],warnings=[],edge_times=[],templates={},campaigns=set(),candidate={},session_amounts=[])
profiles={f'wallet-{i:03}':profile(f'wallet-{i:03}') for i in range(3)}
b=m.InvestigationPopulationBuilder(cohesion,lambda g,f:[])
full=b.build(profiles);chunked=b.build(dict(list(profiles.items())[:2]))+b.build(dict(list(profiles.items())[2:]))
assert len(full)==1 and len(chunked)==2
# Actual immutable family identity lives in dataclass fields.
identity=lambda pop:pop.population_id
calls=[0]
def counted(a,b):calls[0]+=1;return cohesion(a,b)
large={f'disjoint-{i:03}':profile(f'disjoint-{i:03}',False) for i in range(256)}
large_result=m.InvestigationPopulationBuilder(counted,lambda g,f:[]).build(large)
assert len(large_result)==256 and calls[0]==256*255//2
snap=r/'runtime/walkback/src/ops/intelligence_snapshots.py';node=next(x for x in ast.parse(snap.read_text()).body if isinstance(x,ast.FunctionDef) and x.name=='_passes_sanity_check');node.returns=None
for a in node.args.args:a.annotation=None
ns2={};exec(compile(ast.Module(body=[node],type_ignores=[]),str(snap),'exec'),ns2)
ok,reason=ns2['_passes_sanity_check']({'family_count':100},{'family_count':70},'family_count',.5);assert ok
result={'status':'HOLD_MISSING_INDEXED_DERIVED_POPULATION_GENERATION','checks':{'cross_batch_independent_publication_changes_population':True,'large_population_pairwise_work_unbounded_by_list_limit':True,'count_sanity_does_not_certify_complete_population':True},'cross_batch':{'full_family_count':len(full),'per_batch_family_count':len(chunked),'full_ids':[identity(x) for x in full],'per_batch_ids':[identity(x) for x in chunked]},'large_synthetic':{'profiles':256,'pair_checks':calls[0],'families':len(large_result)},'partial_sanity':{'previous_count':100,'partial_count':70,'accepted_by_existing_sanity':ok,'note':'Pure sanity-function diagnostic only; no snapshot was written.'},'source_hashes':{str(x.relative_to(r)):hashlib.sha256(x.read_bytes()).hexdigest() for x in [p,src,snap]},'safety':{'provider_calls':0,'database_connections':0,'snapshot_writes':0,'production_actions':0}}
Path('/private/tmp/dev004-bounded-discovery-diagnostic.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
