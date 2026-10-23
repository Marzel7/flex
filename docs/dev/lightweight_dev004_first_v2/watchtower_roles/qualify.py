import sqlite3,json,ast,shutil,hashlib,time,resource
from pathlib import Path
from typing import Any
resource.setrlimit(resource.RLIMIT_FSIZE,(16000000,16000000))
D=Path('/Users/kevinkeaveney/.codex/worktrees/dev004-first-v2-prototype/flex/docs/dev/lightweight_dev004_first_v2'); O=Path('/private/tmp/dev004-watchtower-roles-qualified'); O.mkdir(exist_ok=False)
S=Path('/private/tmp/dev004-operational-seed-qualified')
for n in ['core_v2.sqlite','ops_v2.sqlite']:shutil.copy2(S/n,O/n)
c=sqlite3.connect('file:/Users/kevinkeaveney/Dev/claude/flex/database/wt_ops_v2.db?mode=ro',uri=True);c.row_factory=sqlite3.Row;c.execute('pragma query_only=on')
v=sqlite3.connect(O/'ops_v2.sqlite');v.row_factory=sqlite3.Row;v.execute('pragma max_page_count=3840')
plans={}
def read(sql,args=(),cap=65):
 plan=[r[3] for r in c.execute('explain query plan '+sql,args)];assert not any('SCAN' in x.upper() for x in plan),(sql,plan)
 plans[sql]=plan;deadline=time.monotonic()+.5;c.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
 try:r=[dict(x) for x in c.execute(sql,args).fetchmany(cap)]
 finally:c.set_progress_handler(None,0)
 assert len(r)<cap,(sql,'cap');return r
def schema(t):return c.execute('select sql from sqlite_master where name=?',(t,)).fetchone()[0]
added={}
def insert(t,r):
 if not v.execute('select 1 from sqlite_master where name=?',(t,)).fetchone():v.execute(schema(t))
 cols=list(r);q='insert or ignore into '+t+' ('+','.join(cols)+') values ('+','.join('?' for _ in cols)+')';before=v.total_changes;v.execute(q,[r[k] for k in cols]);added[t]=added.get(t,0)+v.total_changes-before
src=Path('/Users/kevinkeaveney/Production/flex-walkback-dev002/src/core/watchtower_registry_promotion.py');tree=ast.parse(src.read_text());names={'_table_exists','_has_role_distinguishing_direct_edge','validate_role_normalized_queue_funding_route'};exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names],type_ignores=[]),str(src),'exec'))
cases=json.loads((D/'operational_seed/missing_verified_route_pairs.json').read_text());pairs={};allproof=[]
for pair in sorted({(x['sender'],x['recipient']) for x in cases}):
 active=read('select * from wt_active_subprov_sessions where subprov_wallet=? and treasury_wallet=? limit 65',(pair[1],pair[0]));proofs=[]
 for a in active:
  edges=read('select * from wt_lineage_verified_session_edges where session_id=?',(a['id'],));quarantine=read('select * from wt_lineage_quarantine where source_table=? and source_row_id=?',('wt_active_subprov_sessions',a['id']))
  for e in edges:
   verified=(e['sender'],e['recipient'],e['signature'],e['relationship_type'])==(pair[0],pair[1],a['funding_signature'],'DIRECT_SOL_TRANSFER') and not any(q['exclude_from_tier1'] for q in quarantine)
   proofs.append({'session':a,'edge':e,'quarantine':quarantine,'verified':verified})
 selected=next(iter(sorted([p for p in proofs if p['verified']],key=lambda p:(p['edge']['verified_at'],p['edge']['session_id']))),None)
 policies=read('select * from wt_lineage_root_policies where subject_wallet=?',(pair[1],))
 pairs[pair]={'proofs':proofs,'selected':selected,'root_policies':policies,'bounded_active_rows':len(active)}
 if selected:
  insert('wt_active_subprov_sessions',selected['session']);insert('wt_lineage_verified_session_edges',selected['edge'])
  for p in policies:insert('wt_lineage_root_policies',p)
 allproof.append({'sender':pair[0],'recipient':pair[1],**pairs[pair]})
# Existing eligible-view contract, no new runtime logic.
view=c.execute("select sql from sqlite_master where name='wt_lineage_eligible_sessions'").fetchone()
if view and not v.execute("select 1 from sqlite_master where name='wt_lineage_eligible_sessions'").fetchone():v.execute(view[0])
results=[];operator='04265d9f-6eb2-568c-a49e-9253091a4dbb'
for x in cases:
 mint=x['mint'];p=pairs[(x['sender'],x['recipient'])];q=read('select * from wt_walkback_queue where mint=?',(mint,));s=read('select * from wt_provisioning_sessions where source_mint=? limit 65',(mint,));assert len(q)==1 and len(s)==1
 for t,rows,key in [('wt_walkback_queue',q,'mint'),('wt_provisioning_sessions',s,'session_id')]:
  for r in rows:
   old=v.execute('select * from '+t+' where '+key+'=?',(r[key],)).fetchone();assert old and dict(old)==r,(mint,t,'source drift')
 edges=read('select * from wt_walkback_edge_candidates where mint=? limit 65',(mint,));entities=[];recognition=[]
 for wallet in sorted(set([x['sender'],x['recipient'],q[0]['subprov'],q[0]['creator']])):
  entities+=read('select * from operator_entities where operator_id=? and entity_address=?',(operator,wallet))
  for t,k in [('wt_confirmed_treasuries','treasury'),('wt_discovered_subprovs','subprov')]:
   rows=read('select * from '+t+' where '+k+'=?',(wallet,))
   old=[dict(z) for z in v.execute('select * from '+t+' where '+k+'=?',(wallet,))]
   # recognition dependencies are exact source rows, never synthesize confirmation.
   for r in rows:
    if not old:insert(t,r)
   assert [dict(z) for z in v.execute('select * from '+t+' where '+k+'=?',(wallet,))]==rows
   recognition.append({'table':t,'wallet':wallet,'source_rows':rows})
 actual=validate_role_normalized_queue_funding_route(v,mint);was99=x['source_result'].get('eligible') is True
 classification='VERIFIED_ROLE_LINK' if p['selected'] else 'INSUFFICIENT_RETAINED_EVIDENCE'
 if was99:assert p['selected'] and actual==x['source_result'],(mint,actual)
 results.append({'mint':mint,'original_group':'99_PROVEN_REGRESSIONS' if was99 else 'FIVE_UNQUALIFIED','classification':classification,'v1_frozen':x['source_result'],'v2':actual,'canonical_treasury':x['sender'],'upstream_session_parent':x['recipient'],'direct_funding_account':q[0]['subprov'],'creator':q[0]['creator'],'queue':q[0],'provisioning_session':s[0],'selected_role_session_id':p['selected']['edge']['session_id'] if p['selected'] else None,'alternative_and_selected_edges':edges,'entity_confidence_provenance':entities,'recognition':recognition})
v.commit();v.close();c.close()
result={'status':'PASS_99_ROLE_REGRESSIONS; HOLD_FOUR_OF_FIVE_PREVIOUSLY_UNQUALIFIED','fixed_99':sum(r['original_group']=='99_PROVEN_REGRESSIONS' and r['v2'].get('eligible') for r in results),'five':[{'mint':r['mint'],'classification':r['classification'],'v2':r['v2']} for r in results if r['original_group']=='FIVE_UNQUALIFIED'],'added_rows':added,'proof_pairs':allproof,'cases':results,'source_plans':plans,'installed_reader_sha256':hashlib.sha256(src.read_bytes()).hexdigest(),'limitations':['One existence witness per verified pair; all indexed alternative proofs retained in manifest.','No global absence claim for unindexed edge pairs without active-session lookup results.','No capital-continuity inference from a role-distinguishing historical direct transfer.','99 comparisons use retained frozen V1 results plus independently indexed source witnesses; no unindexed production reader scan.'],'sizes':{n:(O/n).stat().st_size for n in ['core_v2.sqlite','ops_v2.sqlite']}}
(O/'role_results.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items() if k in ['status','fixed_99','five','added_rows','sizes']},indent=2))
