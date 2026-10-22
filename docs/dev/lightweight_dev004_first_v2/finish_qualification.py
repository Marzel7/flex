"""Supplement the bounded prototype with installed completed-queue and credential tests."""
from __future__ import annotations
import ast, hashlib, importlib.util, json, os, resource, shutil, sqlite3, sys, time
from pathlib import Path
from unittest.mock import patch

OUT=Path(sys.argv[1])
resource.setrlimit(resource.RLIMIT_FSIZE,(16_000_000,16_000_000))
W=Path('/Users/kevinkeaveney/Production/flex-walkback-dev002')
P=Path('/Users/kevinkeaveney/Production/flex-main-workers')
ROOT=Path('/Users/kevinkeaveney/Dev/claude/flex')
r=json.loads((OUT/'result.json').read_text())
source=W/'src/core/deep_walkback.py';tree=ast.parse(source.read_text());ns={'time':time}
for n in tree.body:
 if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='SCHEMA' for t in n.targets):ns['SCHEMA']=ast.literal_eval(n.value)
nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ['ensure_schema','claim_with_lease']]
exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0)]+nodes,type_ignores=[])),str(source),'exec'),ns)
p=OUT/'completed_queue_recovery.sqlite';assert not p.exists();shutil.copyfile(OUT/'ops_v2.sqlite',p)
checks=[]
for i in range(2):
 c=sqlite3.connect(p);c.execute('pragma max_page_count=2048')
 before=c.execute('select * from wt_walkback_queue where mint=?',(r['mint'],)).fetchone()
 claimed=ns['claim_with_lease'](c,r['mint'],'DEV004-provider-free',300)
 after=c.execute('select * from wt_walkback_queue where mint=?',(r['mint'],)).fetchone();c.close()
 assert not claimed and before==after
 checks.append({'restart_iteration':i+1,'claim':claimed,'row_unchanged':before==after})
r['installed_completed_queue_recovery']={'source':str(source),'sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'checks':checks,'scope':'Completed retained work is not reclaimed on connection restart. No pending/in-flight case certified.'}
r['credential_checks']=[]
for root in [Path('/Users/kevinkeaveney/Production/flex-listener'),P,W]:
 path=root/'src/utils/birdeye_credentials.py';spec=importlib.util.spec_from_file_location('authority',path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 with patch.dict(os.environ,{'BIRDEYE':'synthetic-paid','BIRDEYE_KK':'synthetic-legacy'},clear=True):assert m.birdeye_credential()=='synthetic-paid'
 for env in [{'BIRDEYE':' ','BIRDEYE_KK':'synthetic-legacy'},{'BIRDEYE_KK':'synthetic-legacy'}]:
  with patch.dict(os.environ,env,clear=True),patch.object(Path,'read_text',return_value='BIRDEYE_KK=synthetic-legacy'):
   try:m.birdeye_credential();raise AssertionError('Legacy/blank credential accepted')
   except m.BirdeyeConfigurationError:pass
 r['credential_checks'].append({'source':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'paid_selection':True,'blank_rejected':True,'legacy_only_rejected':True,'secrets':'synthetic only; actual credential files not read'})
r['provider_controls']={str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in [P/'src/ops/birdeye_rate_limit.py',P/'src/ops/operation_monitor_worker.py']}
r['holds']+=['Retained candle reduction peak differs from accepted stored ATH; copying the final fact does not prove historical calculation equivalence.','Retained Watchtower membership/route coexist with unknown treasury/SubProvider dictionaries and UNKNOWN_INFRASTRUCTURE materialized outcome. Preserve distinct authority states.']
r['recovery_fixture_bytes']=p.stat().st_size
names=['potential_operations','living_potential_operations','potential_candidate_matcher','generic_living_active_components','generic_living_persisted_source_reader','generic_living_reverse_resolver','generic_living_pipeline_v2']
d={'status':'HOLD_GLOBAL_DISCOVERY_CLOSURE_NOT_IN_ONE_CASE','sources':{},'schemas':{},'reference_populations':['qualified candidate specifications and frozen membership/ranking artifacts','selected/alternative/rejected parent edges and atomic-flow populations for each candidate','association INCLUDED/EXCLUDED/UNRESOLVED/CONTRADICTORY states and rationales','immutable assessment versions, current pointer, assessment lineage and association bindings','workflow review/promotion/rejection states and canonical review/identity events','original source row identities, high-water marks and freshness generations; new fixture rowids are not equivalent'], 'post_walkback':'Installed _notify_living_after_walkback_commit dispatches after source commit; its failure cannot undo Walkback. Preserve reverse resolver, feature flag, source reader and atomic publisher.', 'prototype_limit':'No discovery/promotion/assessment publication executed; schema presence and one known route do not certify unseen-operation discovery.'}
for root in [W,P]:
 for n in names:
  f=root/'src/ops'/f'{n}.py'
  if f.exists():
   b=f.read_bytes();d['sources'][str(f)]=hashlib.sha256(b).hexdigest()
   if n=='generic_living_active_components':
    for node in ast.parse(b).body:
     if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='FEATURE_FLAG' for t in node.targets):d['feature_flag']=ast.literal_eval(node.value)
c=sqlite3.connect('file:'+str(ROOT/'database/wt_ops_v2.db')+'?mode=ro',uri=True);c.execute('pragma query_only=on')
for t in ['potential_operation_identity','potential_operation_evidence_association','potential_operation_assessment_version','potential_operation_current','potential_operation_assessment_lineage','potential_operation_assessment_association_binding','potential_operation_workflows','potential_operation_child_candidates','p3r_v2_candidate_membership']:
 d['schemas'][t]=c.execute("select type,name,sql from sqlite_master where (type='table' and name=?) or (type='index' and tbl_name=?)",(t,t)).fetchall()
c.close()
r['prior_contract_identities']={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [ROOT/'docs/design/DEV004_REFERENCE_SEED_CORRECTION.md',ROOT/'docs/audits/dev004_minimum_seed/row_dependency_manifest.json',ROOT/'docs/audits/dev004_minimum_seed/supplemental_manifest.json']}
(OUT/'potential_operations_contract.json').write_text(json.dumps(d,indent=2)+'\n')
(OUT/'result.json').write_text(json.dumps(r,indent=2)+'\n')
print('PASS: 11 equal V1/V2 projections; 2 completed-queue restart checks; 9 synthetic credential assertions. OVERALL HOLD: evidence closure unresolved.')
