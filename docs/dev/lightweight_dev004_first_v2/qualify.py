"""DEV004 one retained case. No production write/transport; incomplete closure is HOLD."""
from __future__ import annotations
import ast, contextlib, hashlib, json, os, resource, socket, sqlite3, sys, time
from pathlib import Path
from typing import Any, Optional

ROOT=Path('/Users/kevinkeaveney/Dev/claude/flex')
OUT=Path(sys.argv[1]) if len(sys.argv)>1 else Path('/private/tmp/dev004-first-v2-build/artifacts')
MINT='HXpDLpoH6Vx6MtqybpmsC6gFFoZpeb6bYTTpkrrypump'
W=Path('/Users/kevinkeaveney/Production/flex-walkback-dev002')
P=Path('/Users/kevinkeaveney/Production/flex-main-workers')
MAX_FILE=16_000_000
resource.setrlimit(resource.RLIMIT_FSIZE,(MAX_FILE,MAX_FILE))
def denied(*a,**k): raise AssertionError('PROVIDER_TRANSPORT_FORBIDDEN')
socket.create_connection=denied
socket.socket.connect=denied
OUT.mkdir(exist_ok=True)
assert not list(OUT.glob('*.sqlite')), 'Do not overwrite completed fixtures'
report={'status':'HOLD','mint':MINT,'rows':{},'selectors':[], 'comparisons':{},'holds':[], 'sources':{},'storage_bound':MAX_FILE,'production_writes':0,'provider_calls':0}
def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,default=str,separators=(',',':')).encode()).hexdigest()
def ro(p):
 c=sqlite3.connect('file:'+str(p.resolve())+'?mode=ro',uri=True,timeout=1);c.row_factory=sqlite3.Row;c.execute('pragma query_only=on');c.execute('pragma cache_size=-2048');return c
src={n:ro(ROOT/'database'/f) for n,f in [('core','flex_complete_database.db'),('ops','wt_ops_v2.db')]}
for c in src.values():c.execute('BEGIN')
selected={'core':{},'ops':{}};schemas={'core':{},'ops':{}};indexes={'core':{},'ops':{}}
total=0
def select(db,table,where,args=(),limit=129):
 global total
 c=src[db]
 schema=c.execute("select sql from sqlite_master where type='table' and name=?",(table,)).fetchone()
 if not schema:
  report['holds'].append(f'MISSING_TABLE:{db}.{table}');return []
 schemas[db][table]=schema[0]
 indexes[db][table]=[x[0] for x in c.execute("select sql from sqlite_master where type='index' and tbl_name=? and sql is not null",(table,))]
 selected[db].setdefault(table,{})
 sql=f'SELECT * FROM "{table}" WHERE {where} LIMIT {limit}'
 try:plan=[x[3] for x in c.execute('explain query plan '+sql,args)]
 except sqlite3.Error as e:
  report['holds'].append(f'UNRESOLVED_SELECTOR:{db}.{table}:{where}:{e}');return []
 if any(x.startswith('SCAN ') for x in plan):
  report['holds'].append('UNQUALIFIED_SELECTOR:'+db+'.'+table+':'+where);return []
 deadline=time.monotonic()+1;steps=[0]
 def progress():
  steps[0]+=1000;return int(steps[0]>200000 or time.monotonic()>deadline)
 c.set_progress_handler(progress,1000)
 try:rows=[dict(r) for r in c.execute(sql,args)]
 except sqlite3.Error as e:
  report['holds'].append(f'SELECTION_FAILED:{db}.{table}:{type(e).__name__}');return []
 finally:c.set_progress_handler(None,0)
 if len(rows)>=limit:report['holds'].append('CAPPED:'+db+'.'+table)
 for r in rows:
  assert len(json.dumps(r,default=str))<1_000_000
  selected[db][table][digest(r)]=r
 total+=len(rows);assert total<2048
 report['selectors'].append({'database':db,'table':table,'predicate':where,'arguments':args,'rows':len(rows),'plan':plan})
 return rows

q=select('ops','wt_walkback_queue','mint=?',(MINT,))[0]
creator,treasury,subprov=q['creator'],q['treasury'],q['subprov']
session=select('ops','wt_provisioning_sessions','source_mint=?',(MINT,))[0]
edges=select('ops','wt_walkback_edge_candidates','mint=?',(MINT,))
for t in ['wt_attribution_outcomes','wt_launch_audit','wt_watchtower_launches','operator_launch_membership','operator_launch_assignment_history','watchtower_token_attribution']:
 select('ops',t,'mint=?',(MINT,))
operator=next(iter(selected['ops']['operator_launch_membership'].values()))['operator_id']
for t in ['operators','operator_identity_state','operator_evidence','operator_reviews']:
 select('ops',t,'operator_id=?',(operator,))
for wallet in [creator,subprov,treasury]:
 select('ops','operator_entities','operator_id=? AND entity_address=?',(operator,wallet))
 for asset in select('ops','operator_identity_assets','asset_value=?',(wallet,)):
  if asset.get('event_id'):select('ops','operator_identity_events','event_id=?',(asset['event_id'],))
select('ops','monitor_admission_outbox','operation_id=? AND mint=?',('watchtower',MINT))
select('ops','wt_confirmed_treasuries','treasury=?',(treasury,))
select('ops','wt_discovered_subprovs','subprov=?',(subprov,))
for wallet in [creator,subprov]:
 for r in select('ops','wt_ops_v2_wallets','wallet=?',(wallet,)):
  select('ops','wt_ops_v2','operation_uuid=?',(r['operation_uuid'],))
  select('ops','wt_ops_v2_wallets','operation_uuid=?',(r['operation_uuid'],))
select('ops','wt_ops_v2','operation_uuid=?',('',))  # required graph schema; no fabricated graph row
for t,col in [('wt_wrap_close_candidates','creator'),('wt_creator_birth_launch','creator'),('wt_candidate_websocket_watches','candidate_wallet')]:
 select('ops',t,col+'=?',(creator,))
select('ops','wt_watchtower_launches','creator_wallet=?',(creator,))
for wallet in [creator,subprov,treasury]+[x['candidate_parent'] for x in edges]:
 select('ops','wt_lineage_quarantine','subject_wallet=?',(wallet,))
for e in edges:
 select('ops','wt_walkback_atomic_flows','signature=?',(e['signature'],))
 select('core','transfer_index','signature=?',(e['signature'],))
fact=select('ops','operation_monitor_facts','operation_id=? AND mint=?',('watchtower',MINT))[0]
observations=select('ops','operation_monitor_observations','operation_id=? AND mint=?',('watchtower',MINT))
select('ops','operation_monitor_entry_provenance','operation_id=? AND mint=?',('watchtower',MINT))
for t in ['token_analysis','tracked_tokens','token_market_cap_peaks','metadata_cache','token_pool_accounts','creator_resolution_queue','creator_funding_queue','migration_walkback_handoff']:
 select('core',t,'mint=?',(MINT,))
select('core','creator_funders','creator_address=?',(creator,))

# Unmodified installed function bodies, isolated from module startup and provider imports.
def functions(path,names,extra=None):
 source=path.read_text();report['sources'][str(path)]=hashlib.sha256(source.encode()).hexdigest()
 tree=ast.parse(source);nodes=[x for x in tree.body if isinstance(x,(ast.FunctionDef,ast.ClassDef)) and x.name in names]
 assert len(nodes)==len(names)
 ns={'sqlite3':sqlite3,'Any':Any,'Optional':Optional,'WATCHTOWER_OPERATOR_ID':operator,'time':time}
 ns.update(extra or {})
 exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0)]+nodes,type_ignores=[])),str(path),'exec'),ns)
 return ns
registry=functions(W/'src/core/watchtower_registry_promotion.py',['_table_exists','_has_role_distinguishing_direct_edge','validate_role_normalized_queue_funding_route','is_canonical_watchtower_outcome'])
walk=functions(W/'src/core/walkback_worker.py',['_is_known_treasury','_is_known_subprov'])
classification=functions(W/'src/core/walkback_queue.py',['_resolve_treasury_via_op_graph','classify_creator'])
price=functions(P/'src/ops/watchtower_price_fact_contract.py',['reduce_watchtower_price_facts'])
def outputs(core,ops):
 deadline=time.monotonic()+2
 for c in [core,ops]:c.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
 try:
  obs=[dict(r) for r in ops.execute('select * from operation_monitor_observations where operation_id=? and mint=? order by observation_timestamp',('watchtower',MINT))]
  f=dict(ops.execute('select * from operation_monitor_facts where operation_id=? and mint=?',('watchtower',MINT)).fetchone())
  candles=[{'timestamp':r['observation_timestamp'],'open':r['open_mc_usd'],'high':r['high_mc_usd'],'low':r['low_mc_usd'],'close':r['close_mc_usd']} for r in obs if all(r[k] is not None for k in ['open_mc_usd','high_mc_usd','low_mc_usd','close_mc_usd'])]
  return {'known_treasury':walk['_is_known_treasury'](ops,treasury),'known_subprovider':walk['_is_known_subprov'](ops,subprov),'watchtower_route':registry['validate_role_normalized_queue_funding_route'](ops,MINT),'creator_classification':classification['classify_creator'](creator,ops,core),'retained_outcome':dict(ops.execute('select * from wt_attribution_outcomes where mint=?',(MINT,)).fetchone()),'parent_candidates':[dict(r) for r in ops.execute('select * from wt_walkback_edge_candidates where mint=? order by evidence_key',(MINT,))],'quarantine':[dict(r) for r in ops.execute('select * from wt_lineage_quarantine where subject_wallet=? order by quarantine_id',(subprov,))],'price_fact':f,'price_observations':obs,'ohlc_reduction':price['reduce_watchtower_price_facts'](f['entry_mc_usd'],f['entry_timestamp'],candles),'creator_funding_history':[dict(r) for r in core.execute('select * from creator_funders where creator_address=?',(creator,))]}
 finally:
  for c in [core,ops]:c.set_progress_handler(None,0)

v1=outputs(src['core'],src['ops'])
(OUT/'frozen_v1_outputs.json').write_text(json.dumps(v1,indent=2,default=str)+'\n')
# New schema and selected rows only. No backup API, production copy, trigger or cache copy.
for db in ['core','ops']:
 c=sqlite3.connect(OUT/(db+'_v2.sqlite'));c.execute('pragma page_size=4096');c.execute('pragma max_page_count=2048');c.execute('pragma journal_mode=DELETE');c.execute('pragma cache_size=-2048')
 for t,ddl in schemas[db].items():
  c.execute(ddl)
  for r in selected[db][t].values():
   cols=list(r);c.execute('INSERT INTO "'+t+'" ('+','.join('"'+x+'"' for x in cols)+') VALUES ('+','.join('?' for _ in cols)+')',list(r.values()))
  for ddl in indexes[db][t]:c.execute(ddl)
  report['rows'][db+'.'+t]=len(selected[db][t])
 c.commit();c.close()
v2c=ro(OUT/'core_v2.sqlite');v2o=ro(OUT/'ops_v2.sqlite');v2=outputs(v2c,v2o)
for k,v in v1.items():report['comparisons'][k]={'equal':v==v2[k],'v1_digest':digest(v),'v2_digest':digest(v2[k])}
report['frozen_values']={'known_treasury':v1['known_treasury'],'known_subprovider':v1['known_subprovider'],'route':v1['watchtower_route'],'creator_classification':v1['creator_classification'],'outcome_type':v1['retained_outcome']['outcome_type'],'entry_mc_usd':fact['entry_mc_usd'],'final_proven_ath_mc':fact['final_proven_ath_mc'],'final_ath_multiple':fact['final_ath_multiple'],'monitor_state':fact['monitor_state'],'ohlc_reduction':v1['ohlc_reduction'],'selected_parents':sum(x['selection_status']=='SELECTED' for x in edges),'alternative_parents':sum(x['selection_status']=='ALTERNATIVE' for x in edges)}
v2c.close();v2o.close()
for c in src.values():c.rollback();c.close()
# A completed retained row must stay completed across process/connection reopening.
for _ in range(2):
 c=ro(OUT/'ops_v2.sqlite');r=c.execute('select status,attempts,create_anchor_signature from wt_walkback_queue where mint=?',(MINT,)).fetchone();assert tuple(r)==(q['status'],q['attempts'],q['create_anchor_signature']);c.close()
report['completed_queue_reopen']='PASS; retained business state only; no cutover lease authority asserted'
report['holds'] += ['No acquisition checkpoint or restartable acquisition envelope has been located for this selected mint in the retained manifest. last_observation_at and finalized price facts are not substituted for a checkpoint.', 'Full canonical CREATE transaction/role payload closure and accepted baseline input window are not established merely by matching anchor signatures.', 'No independently verified distinct Funding Account exists in this retained route: direct funder equals SubProvider; no additional identity invented.', 'Potential Operations discovery/promotion and post-Walkback publisher need full candidate populations, assessment bindings and lineage; this one-token subset cannot qualify those global consumers.', 'Completed queue reopen is not a pending/retry/lease crash-recovery qualification or live cutover reconciliation.']
report['price_recompute_matches_retained_ath']=v1['ohlc_reduction']['peak']['value']==fact['final_proven_ath_mc']
report['fixtures']={p.name:{'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in OUT.glob('*.sqlite')}
report['selected_rows']=sum(report['rows'].values())
report['row_dependency_manifest']=[]
for db,tables in selected.items():
 for table,rows in tables.items():
  # Source connections are already closed; fixture primary keys retain the source DDL.
  with sqlite3.connect('file:'+str((OUT/(db+'_v2.sqlite')).resolve())+'?mode=ro',uri=True) as metadata:
   pk=[r[1] for r in metadata.execute('pragma table_info("'+table+'")') if r[5]]
  for sha,row in rows.items():report['row_dependency_manifest'].append({'database':db,'table':table,'key':{k:row[k] for k in pk},'row_sha256':sha,'inclusion_reason':'selected retained operation case and exact installed-reader lookup closure; completeness not asserted'})
assert all(x['equal'] for x in report['comparisons'].values())
assert all(p.stat().st_size<MAX_FILE for p in OUT.iterdir() if p.is_file())
(OUT/'result.json').write_text(json.dumps(report,indent=2,default=str)+'\n')
print(json.dumps({k:report[k] for k in ['status','selected_rows','fixtures','frozen_values','price_recompute_matches_retained_ath','holds']},indent=2))
