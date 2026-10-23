import ast,json,sqlite3,hashlib,time,types,sys,importlib,contextlib
from pathlib import Path
from typing import Any,Optional
O=Path('/private/tmp/dev004-operational-seed-qualified');P=Path('/Users/kevinkeaveney/Production/flex-walkback-dev002');r={'tests':[],'missing_dependencies':[]}
for name,sub in [('src','src'),('src.ops','src/ops')]:m=types.ModuleType(name);m.__path__=[str(P/sub)];sys.modules[name]=m
ns={'sqlite3':sqlite3,'Any':Any,'Optional':Optional,'json':json,'time':time}
def functions(path,names,target):
 t=ast.parse(path.read_text());nodes=[n for n in t.body if isinstance(n,ast.FunctionDef) and n.name in names];assert len(nodes)==len(names);exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0)]+nodes,type_ignores=[])),str(path),'exec'),target)
gov=types.ModuleType('src.ops.operator_identity_governance');gov.__dict__.update(ns);functions(P/'src/ops/operator_identity_governance.py',{'_read_conn','_tables','read_identity_lifecycle'},gov.__dict__);sys.modules[gov.__name__]=gov
reader=importlib.import_module('src.ops.operator_reader');deep=importlib.import_module('src.ops.watchtower_deep_prospective');matcher=importlib.import_module('src.ops.p3r_profile_candidate_matcher')
paths={'v1':{'ops':'/Users/kevinkeaveney/Dev/claude/flex/database/wt_ops_v2.db','core':'/Users/kevinkeaveney/Dev/claude/flex/database/flex_complete_database.db'},'v2':{n:str(O/(n+'_v2.sqlite')) for n in ['core','ops']}};conns={}
for version,p in paths.items():
 conns[version]={}
 for db,path in p.items():
  c=sqlite3.connect('file:'+path+'?mode=ro',uri=True);c.row_factory=sqlite3.Row;c.execute('pragma query_only=on');c.execute('begin');conns[version][db]=c

def test(name,fn):
 try:r['tests'].append({'name':name,'status':'PASS','evidence':fn()})
 except Exception as e:r['tests'].append({'name':name,'status':'HOLD','error':type(e).__name__+':'+str(e)[:400]})
def known():
 a=reader.OperatorReader(paths['v1']['ops']).fetch_all_operators(exclude_rejected=False);b=reader.OperatorReader(paths['v2']['ops']).fetch_all_operators(exclude_rejected=False);assert len(a)==len(b)==10;assert a==b
 r['known_operations']=[{k:x.get(k) for k in ['operator_id','display_name','status','confidence','review_state','identity_status','activity_status']} for x in b]
 return {'identities':10,'status_confidence_review_and_lifecycle_exact':True}
test('installed_operator_reader_ten_identity_equivalence',known)
functions(P/'src/core/watchtower_registry_promotion.py',{'_table_exists','_has_role_distinguishing_direct_edge','validate_role_normalized_queue_funding_route'},ns)
functions(P/'src/core/walkback_queue.py',{'_resolve_treasury_via_op_graph','classify_creator'},ns)
attr=types.ModuleType('src.ops.attribution_outcome');attr.__dict__.update(ns)
t=ast.parse((P/'src/ops/attribution_outcome.py').read_text())
for node in t.body:
 if isinstance(node,ast.Assign):
  for target in node.targets:
   if isinstance(target,ast.Name):
    try:attr.__dict__[target.id]=ast.literal_eval(node.value)
    except (ValueError,TypeError):
     if target.id in {'MIN_LAUNCHER_OBSERVATION_SECONDS','FRESH_PROVISIONING_SECONDS'}:exec(compile(ast.Module(body=[node],type_ignores=[]),'installed-constants','exec'),attr.__dict__)
functions(P/'src/ops/attribution_outcome.py',{'_table','_columns','_one','_normalize_timestamp','_creator_launch_history_span','evaluate_launcher_profile'},attr.__dict__);sys.modules[attr.__name__]=attr

def membership():
 a=[tuple(x) for x in conns['v1']['ops'].execute('select * from operator_launch_membership order by mint limit 2001')];b=[tuple(x) for x in conns['v2']['ops'].execute("select * from operator_launch_membership where mint not like 'DEV004%' order by mint limit 2001")];assert len(a)<2001;assert a==b
 r['missing_event_id_mints']=[x[0] for x in a if x[-1] is None];return {'rows':len(a),'null_event_id_preserved':len(r['missing_event_id_mints']),'automatic_promotion':False}
test('complete_membership_and_missing_event_identity',membership)
def cases():
 results=[]
 for op in conns['v1']['ops'].execute('select operator_id,display_name from operators'):
  row=conns['v1']['ops'].execute('select m.mint,q.creator from operator_launch_membership m left join wt_walkback_queue q using(mint) where m.operator_id=? order by m.mint limit 1',(op[0],)).fetchone()
  if not row:results.append({'operation':op[1],'state':'NO_ASSIGNED_CASE'});continue
  mint,creator=row;a=ns['validate_role_normalized_queue_funding_route'](conns['v1']['ops'],mint);b=ns['validate_role_normalized_queue_funding_route'](conns['v2']['ops'],mint);classification={}
  for v in conns:
   c=conns[v]['ops'];deadline=time.monotonic()+.5;c.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
   try:classification[v]=ns['classify_creator'](creator,c,conns[v]['core'])
   except sqlite3.Error as e:classification[v]={'missing_or_unbounded':str(e)}
   finally:c.set_progress_handler(None,0)
  results.append({'operation':op[1],'mint':mint,'route_equal':a==b,'creator_equal':classification['v1']==classification['v2'],'v1_creator':classification['v1'],'v2_creator':classification['v2']})
 r['operation_cases']=results;assert all(x.get('route_equal',True) and x.get('creator_equal',True) for x in results),'See exact operation_cases'
 return results
test('installed_route_creator_classifiers_frozen_operation_cases',cases)
def p3r():
 assert matcher.load_contracts(conns['v1']['ops'])==matcher.load_contracts(conns['v2']['ops']);return {'reviewed_contracts_equal':True,'scope':'P3R loader only; not Watchtower/Deep discovery'}
test('installed_behavioural_profile_contracts',p3r)
def deep_cases():
 out=[]
 for row in conns['v1']['ops'].execute('select mint from operator_launch_membership where operator_id=? order by mint limit 33',(deep.DEEP_OPERATOR_ID,)):
  mint=row[0];a=deep.assess_prospective_route(conns['v1']['ops'],mint);b=deep.assess_prospective_route(conns['v2']['ops'],mint);assert a==b;out.append({'mint':mint,'result':a})
 return {'assigned_cases':out,'limitation':'Existing ownership gate only; full address-independent route recovery needs transaction-role/funding-parent closure.'}
test('installed_Deep_existing_ownership_gate',deep_cases)
# Exact unresolved funding-history keys from existing closure, no new history acquisition.
fund=[]
for row in conns['v2']['core'].execute('select distinct creator_address from creator_funders limit 1001'):
 creator=row[0];s=conns['v1']['core'];deadline=time.monotonic()+.2;s.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
 try:
  source=s.execute('select * from creator_funders where creator_address=? limit 258',(creator,)).fetchall();target=conns['v2']['core'].execute('select * from creator_funders where creator_address=? limit 258',(creator,)).fetchall()
  if [tuple(x) for x in source]!=[tuple(x) for x in target]:fund.append({'creator':creator,'v1_bounded_rows':len(source),'v2_bounded_rows':len(target),'source_capped':len(source)==258})
 except sqlite3.Error as e:fund.append({'creator':creator,'error':str(e)})
 finally:s.set_progress_handler(None,0)
r['funding_history_gaps']=fund
for v in conns.values():
 for c in v.values():c.close()
(O/'operation_results.json').write_text(json.dumps(r,indent=2,default=str)+'\n');print([(x['name'],x['status'],x.get('error')) for x in r['tests']]);print('funding_gap_keys',len(fund))
