"""Read-only complete selected-seed relational preflight. No target import/DDL.
Coverage: every declared FK under accepted V2 overrides, typed membership guard,
and explicitly enumerated accepted assessment/monitor ownership references.
"""
import ast,collections,hashlib,json,os,resource,socket,sqlite3,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4];REF=Path('/private/tmp/dev004-compact-creator-qualified/classification_closure');SRC=Path('/Users/kevinkeaveney/Dev/claude/flex/database')
BATCH=128;MAX_ROWS=30000;MAX_BYTES=64*1024*1024;MAX_FILE=16*1024*1024

def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()
def ident(x):return '"'+x.replace('"','""')+'"'
def save(p,x):
 data=(json.dumps(x,sort_keys=True,indent=2)+'\n').encode();assert len(data)<MAX_FILE
 tmp=p.with_suffix(p.suffix+'.tmp')
 with tmp.open('wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
 os.replace(tmp,p);fd=os.open(p.parent,os.O_RDONLY)
 try:os.fsync(fd)
 finally:os.close(fd)
def ro(p):
 c=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True,timeout=.2);c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON');c.execute('PRAGMA cache_size=-2048');c.execute('BEGIN');return c

# Concrete reader/writer ownership contracts, not invented FKs on every ID-like field.
LOGICAL=[
 ('monitor_admission_outbox',('mint','operation_id','membership_id'),'operator_launch_membership',('mint','operator_id','event_id')),
 ('potential_operation_assessment_version',('potential_operation_id',),'potential_operation_identity',('potential_operation_id',)),
 ('potential_operation_evidence_association',('potential_operation_id',),'potential_operation_identity',('potential_operation_id',)),
 ('potential_operation_current',('potential_operation_id',),'potential_operation_identity',('potential_operation_id',)),
 ('potential_operation_current',('assessment_id',),'potential_operation_assessment_version',('assessment_id',)),
 ('potential_operation_assessment_lineage',('potential_operation_id',),'potential_operation_identity',('potential_operation_id',)),
 ('potential_operation_assessment_association_binding',('association_id',),'potential_operation_evidence_association',('association_id',)),
 ('operator_identity_state',('merged_into_operator_id',),'operators',('operator_id',)),
 ('operator_reviews',('related_operator_id',),'operators',('operator_id',)),
 ('operator_launch_assignment_history',('from_operator_id',),'operators',('operator_id',)),
 ('operator_launch_assignment_history',('to_operator_id',),'operators',('operator_id',)),
 ('operation_monitor_observations',('operation_id','mint'),'operation_monitor_facts',('operation_id','mint')),
]

def main(base):
 assert not base.exists();base.mkdir();resource.setrlimit(resource.RLIMIT_FSIZE,(MAX_FILE,MAX_FILE));sys.dont_write_bytecode=True
 def denied(*a,**kw):raise RuntimeError('NETWORK_FORBIDDEN')
 socket.socket.connect=denied;socket.socket.connect_ex=denied;socket.create_connection=denied
 def audit(event,args):
  if event=='sqlite3.connect':
   raw=str(args[0]);assert raw.startswith('file:') and 'mode=ro' in raw,'ALL_DATABASES_MUST_BE_READ_ONLY'
  if event=='open' and isinstance(args[0],(str,bytes,os.PathLike)):
   p=Path(os.fsdecode(args[0]));assert p.name not in ('.env','.env.production'),'CREDENTIAL_READ_FORBIDDEN';flags=args[2] if isinstance(args[2],int) else 0
   if flags&(os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND):assert p.resolve().is_relative_to(base),'OUTSIDE_WRITE_FORBIDDEN'
 sys.addaudithook(audit)
 def storage():
  v=os.statvfs(base);free=v.f_bavail*v.f_frsize;assert free>=2_000_000_000+64*1024*1024
  files=[p for p in base.iterdir() if p.is_file()];assert len(files)<=12 and all(p.stat().st_size<MAX_FILE for p in files);assert sum(p.stat().st_blocks*512 for p in files)<MAX_BYTES
  return {'free_bytes':free,'allocated_bytes':sum(p.stat().st_blocks*512 for p in files),'largest_file_bytes':max([p.stat().st_size for p in files] or [0])}
 before=storage();source={k:ro(SRC/n) for k,n in [('core','flex_complete_database.db'),('ops','wt_ops_v2.db')]};reference={k:ro(REF/(k+'_v2.sqlite')) for k in ['core','ops']}
 ledger=json.loads((ROOT/'docs/dev/lightweight_dev004_first_v2/seed_minimization/row_classification_manifest.json').read_text());excluded={(r['database'],r['table'],json.dumps(r['key'],sort_keys=True)) for r in ledger if r['class']=='HISTORICAL_ONLY' and r['reason'].startswith('Explicit synthetic qualification input;')}
 rows={};schemas={};keys={};findings=[];cache={};plans={};source_bytes=0;selected=0;excluded_seen=[];source_digest=hashlib.sha256();metadata=[];checkpoint_seq=0;null_edges=0;edges=0;resolved=0;resolved_types=collections.Counter();edge_summary=collections.Counter();supplementary=collections.Counter()
 def checkpoint(phase,kind,table,last_key):
  nonlocal checkpoint_seq
  checkpoint_seq+=1;save(base/'checkpoint.json',{'phase':phase,'batch':checkpoint_seq,'database':kind,'table':table,'last_key':last_key,'selected_rows':selected,'source_digest_prefix':source_digest.hexdigest(),'edges_checked':edges,'findings':len(findings),'complete':False,'restart_contract':'Replay deterministic source selectors and verify row digests before reusing progress; connection-local snapshot identity is not durable authority.'});storage()
 def lookup(kind,table,columns,values):
  ck=(kind,table,tuple(columns),tuple(values))
  if ck in cache:return cache[ck]
  c=source[kind]
  if not c.execute('SELECT 1 FROM sqlite_schema WHERE type=\'table\' AND name=?',(table,)).fetchone():out=('MISSING_SOURCE_SCHEMA',[])
  else:
   sql='SELECT * FROM '+ident(table)+' WHERE '+' AND '.join(ident(k)+' IS ?' for k in columns)+' LIMIT 2'
   try:plan=[r[3] for r in c.execute('EXPLAIN QUERY PLAN '+sql,values)]
   except sqlite3.Error as e:out=('INCOMPATIBLE_SOURCE_SCHEMA',[]);plan=[str(e)]
   else:
    plans.setdefault(kind+'.'+table+':'+','.join(columns),plan)
    if any('SCAN ' in p for p in plan):out=('UNQUALIFIED_UNINDEXED_LOOKUP',[])
    else:
     start=time.monotonic();c.set_progress_handler(lambda:int(time.monotonic()-start>1),1000)
     try:data=[dict(r) for r in c.execute(sql,values).fetchmany(2)];out=('OK',data)
     except sqlite3.OperationalError:out=('LOOKUP_BUDGET_EXCEEDED',[])
     finally:c.set_progress_handler(None,0)
  cache[ck]=out;assert len(cache)<60000;return out
 def key(kind,table,row):return {k:row[k] for k in keys[(kind,table)]}
 def impact(table):
  if table.startswith('potential_'):return ['Potential Operations immutable assessment/publication/review']
  if table.startswith('operation_monitor') or table=='token_market_cap_peaks':return ['price monitoring/lifecycle','accepted retained price evidence']
  if table.startswith('operator_') or table=='confirmed_operation_matches':return ['known operation identities/memberships','operation attribution/provenance','Walkback reference recognition','accepted research continuity']
  return ['selected operational/evidence contract; precise consumer significance unresolved']
 def finding(kind,table,row,relation,cause,parent,values,extra=None):
  f={'database':kind,'table':table,'key':key(kind,table,row),'relationship':relation,'cause':cause,'parent':parent,'parent_values':values,'row_sha256':digest(row),'operational_dependencies':impact(table)}
  for k in ['operator_id','source_population_id','event_id','assessment_id','potential_operation_id']: 
   if k in row:f[k]=row[k]
  if extra:f.update(extra)
  findings.append(f);assert len(findings)<10000
 # Bounded deterministic selected-key capture, no complete production table scan.
 for kind in ['core','ops']:
  objects=reference[kind].execute("SELECT name,sql FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name LIMIT 81").fetchall();assert len(objects)<=80
  for table,ddl in objects:
   schemas[(kind,table)]=ddl;cols=reference[kind].execute('PRAGMA table_info('+ident(table)+')').fetchall();pk=tuple(r[1] for r in sorted(cols,key=lambda r:r[5]) if r[5]);assert pk;keys[(kind,table)]=pk;rows[(kind,table)]={};last=None
   while True:
    expr='('+','.join(map(ident,pk))+')' if len(pk)>1 else ident(pk[0]);where='' if last is None else ' WHERE '+expr+' > '+('('+','.join('?' for _ in pk)+')' if len(pk)>1 else '?')
    sql='SELECT '+','.join(map(ident,pk))+' FROM '+ident(table)+where+' ORDER BY '+','.join(map(ident,pk))+' LIMIT '+str(BATCH)
    batch=reference[kind].execute(sql,() if last is None else last).fetchall()
    if not batch:break
    for krow in batch:
     values=tuple(krow);assert all(v is not None for v in values);k=dict(krow)
     if (kind,table,json.dumps(k,sort_keys=True)) in excluded:excluded_seen.append({'database':kind,'table':table,'key':k});continue
     selected+=1;assert selected<=MAX_ROWS
     if table=='dev004_retained_reference_evidence':data=[dict(reference[kind].execute('SELECT * FROM '+ident(table)+' WHERE '+ ' AND '.join(ident(x)+' IS ?' for x in pk),values).fetchone())];status='OK'
     else:status,data=lookup(kind,table,pk,values)
     if status!='OK' or len(data)!=1:
      stub=dict(k);finding(kind,table,stub,'SELECTED_SOURCE_IDENTITY',status if status!='OK' else 'MISSING_SELECTED_SOURCE_ROW',{'table':table,'columns':pk},values);continue
     row=data[0];n=len(json.dumps(row,default=str).encode());assert n<65536;source_bytes+=n;assert source_bytes<MAX_BYTES;rows[(kind,table)][values]=row;source_digest.update((digest([kind,table,k,row])+'\n').encode())
    last=tuple(batch[-1]);checkpoint('capture',kind,table,list(last))
 # Add only the already-qualified six current Byzantine reference closures.
 six=json.loads((ROOT/'docs/dev/lightweight_dev004_first_v2/full_operational_seed_migration/authority_gate_manifest.json').read_text())['missing_event_dependencies'];added=collections.Counter()
 for item in six:
  for kind,table,columns,values in [('ops','operator_launch_membership',('mint',),(item['mint'],)),('ops','confirmed_operation_matches',('match_id',),(item['event_id'],)),('core','token_analysis',('mint',),(item['mint'],))]:
   status,data=lookup(kind,table,columns,values)
   if status!='OK' or len(data)!=1:findings.append({'database':kind,'table':table,'key':dict(zip(columns,values)),'relationship':'ACCEPTED_SIX_CASE_CLOSURE','cause':status if status!='OK' else 'MISSING_SELECTED_SOURCE_ROW'});continue
   row=data[0];pk=tuple(row[k] for k in keys[(kind,table)])
   if pk not in rows[(kind,table)]:rows[(kind,table)][pk]=row;selected+=1;added[kind+'.'+table]+=1;source_digest.update((digest([kind,table,pk,row])+'\n').encode())
 assert selected<=MAX_ROWS
 indexes={}
 def present(kind,table,columns,values):
  k=(kind,table,tuple(columns))
  if k not in indexes:indexes[k]={tuple(r.get(c) for c in columns) for r in rows.get((kind,table),{}).values()}
  return tuple(values) in indexes[k]
 def edge(kind,table,row,childcols,parent,parentcols,contract):
  nonlocal edges,resolved,null_edges
  values=tuple(row.get(k) for k in childcols)
  if any(v is None for v in values):null_edges+=1;return
  edges+=1;edge_summary[kind+'.'+table+':'+contract]+=1
  if present(kind,parent,parentcols,values):resolved+=1;return
  status,data=lookup(kind,parent,parentcols,values)
  cause=status if status!='OK' else ('INCOMPLETE_SEED_SELECTION' if len(data)==1 else 'MISSING_SOURCE_PARENT' if not data else 'NON_UNIQUE_SOURCE_AUTHORITY')
  finding(kind,table,row,contract,cause,{'table':parent,'columns':list(parentcols)},list(values),{'source_parent_count':len(data)})
 # Every accepted V2 declared FK, adjusted only by the two qualified profiles.
 contracts=[]
 for kind,table in sorted(rows):
  groups=collections.defaultdict(list)
  for fk in reference[kind].execute('PRAGMA foreign_key_list('+ident(table)+')'):groups[fk[0]].append(tuple(fk))
  for fid,items in sorted(groups.items()):
   items.sort(key=lambda r:r[1]);parent=items[0][2];child=tuple(r[3] for r in items);pcols=tuple(r[4] for r in items)
   if kind=='ops' and table=='operator_launch_membership' and child==('event_id',):continue # Replaced by qualified typed branch below.
   if kind=='core' and table=='token_market_cap_peaks':parent='token_analysis'
   if any(x is None for x in pcols):pcols=keys[(kind,parent)]
   contracts.append((kind,table,child,parent,pcols,'DECLARED_FK:'+str(fid)))
 for table,child,parent,pcols in LOGICAL:
  contracts.append(('ops',table,child,parent,pcols,'ACCEPTED_READER_OWNERSHIP:'+','.join(child)))
 for kind,table,child,parent,pcols,contract in contracts:
  for i,row in enumerate(rows.get((kind,table),{}).values()):
   edge(kind,table,row,child,parent,pcols,contract)
   if (i+1)%BATCH==0:checkpoint('references',kind,table,[contract,i+1])
 # Typed membership parents must match the actual operation/mint and CONFIRMED_MATCH state.
 for i,row in enumerate(rows[('ops','operator_launch_membership')].values()):
  event=row['event_id']
  if event is None:null_edges+=1;resolved_types['NONE']+=1;continue
  edges+=1;edge_summary['ops.operator_launch_membership:TYPED_REFERENCE']+=1
  es,ev=lookup('ops','operator_identity_events',('event_id',),(event,));ms,mv=lookup('ops','confirmed_operation_matches',('match_id',),(event,))
  eok=es=='OK' and len(ev)==1 and ev[0]['operator_id']==row['operator_id'];mok=ms=='OK' and len(mv)==1 and mv[0]['operator_id']==row['operator_id'] and mv[0]['mint']==row['mint'] and mv[0]['state']=='CONFIRMED_MATCH'
  if int(eok)+int(mok)==1:
   parent='operator_identity_events' if eok else 'confirmed_operation_matches';cols=('event_id','operator_id') if eok else ('match_id','operator_id','mint');vals=(event,row['operator_id']) if eok else (event,row['operator_id'],row['mint']);resolved_types['IDENTITY_EVENT' if eok else 'CONFIRMED_MATCH']+=1
   if present('ops',parent,cols,vals):resolved+=1
   else:finding('ops','operator_launch_membership',row,'TYPED_REFERENCE','INCOMPLETE_SEED_SELECTION',{'table':parent,'columns':cols},vals,{'source_parent_count':1})
  else:
   cause='LEGACY_REFERENCE_TYPE_UNRESOLVED' if es==ms=='OK' and not ev and not mv else 'REFERENCE_AUTHORITY_CONFLICT' if es==ms=='OK' else 'UNQUALIFIED_SOURCE_LOOKUP'
   finding('ops','operator_launch_membership',row,'TYPED_REFERENCE',cause,{'alternatives':['operator_identity_events(event_id,operator_id)','confirmed_operation_matches(match_id,operator_id,mint,state)']},[event],{'identity_event_rows':len(ev),'confirmed_match_rows':len(mv),'candidate_identity_matches_operation':eok,'candidate_match_matches_operation_mint_state':mok})
  if (i+1)%BATCH==0:checkpoint('typed_references','ops','operator_launch_membership',[i+1,row['mint']])
 # Assessment ownership pairs: a present parent ID alone must not bind another candidate.
 for table in ['potential_operation_current','potential_operation_assessment_lineage']:
  for row in rows[('ops',table)].values():
   edges+=1;edge_summary['ops.'+table+':ASSESSMENT_CANDIDATE_OWNERSHIP']+=1;aid=row['assessment_id'];status,data=lookup('ops','potential_operation_assessment_version',('assessment_id',),(aid,))
   if status=='OK' and len(data)==1 and data[0]['potential_operation_id']==row['potential_operation_id']:resolved+=1
   else:finding('ops',table,row,'ASSESSMENT_CANDIDATE_OWNERSHIP','ASSESSMENT_OWNER_MISMATCH' if data else 'MISSING_ASSESSMENT_OWNER',{'table':'potential_operation_assessment_version'},[aid])
 for row in rows[('ops','potential_operation_assessment_association_binding')].values():
  edges+=1;edge_summary['ops.potential_operation_assessment_association_binding:CANDIDATE_OWNERSHIP']+=1;ss,a=lookup('ops','potential_operation_assessment_version',('assessment_id',),(row['assessment_id'],));ts,b=lookup('ops','potential_operation_evidence_association',('association_id',),(row['association_id'],))
  if ss==ts=='OK' and len(a)==len(b)==1 and a[0]['potential_operation_id']==b[0]['potential_operation_id']:resolved+=1
  else:finding('ops','potential_operation_assessment_association_binding',row,'CANDIDATE_OWNERSHIP','ASSOCIATION_ASSESSMENT_OWNER_MISMATCH',{'tables':['potential_operation_assessment_version','potential_operation_evidence_association']},[row['assessment_id'],row['association_id']])
 # Reuse the accepted pure confirmation predicate and outbox identity algorithm.
 promotion=ROOT/'runtime/walkback/src/core/watchtower_registry_promotion.py';tree=ast.parse(promotion.read_text());ns={'WATCHTOWER_OPERATOR_ID':'04265d9f-6eb2-568c-a49e-9253091a4dbb'}
 node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='is_canonical_watchtower_outcome');exec(compile(ast.Module(body=[node],type_ignores=[]),'accepted-confirmation-predicate','exec'),ns)
 admission=ROOT/'runtime/walkback/src/ops/monitor_live_admission.py';atree=ast.parse(admission.read_text());ans={'hashlib':hashlib,'json':json}
 for n in atree.body:
  if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='VERSION' for t in n.targets):exec(compile(ast.Module(body=[n],type_ignores=[]),'accepted-outbox-version','exec'),ans)
 node=next(n for n in atree.body if isinstance(n,ast.FunctionDef) and n.name=='_id');exec(compile(ast.Module(body=[node],type_ignores=[]),'accepted-outbox-identity','exec'),ans)
 # Supplemental exact evidence probes for unresolved legacy types: only indexed reads,
 # no automatic compatibility mapping inferred from signatures or similar values.
 for f in findings:
  if f.get('cause')!='LEGACY_REFERENCE_TYPE_UNRESOLVED':continue
  event=f['event_id'];mint=f['key']['mint'];c=source['ops'];q='SELECT evidence_key,mint,signature FROM wt_walkback_atomic_flows WHERE signature=? LIMIT 33';plan=[r[3] for r in c.execute('EXPLAIN QUERY PLAN '+q,(event,))];assert not any('SCAN ' in x for x in plan)
  result=c.execute(q,(event,)).fetchmany(33);f['retained_atomic_signature_evidence']={'lookup_index':plan,'matching_rows_bounded':len(result),'truncated':len(result)==33,'same_mint_rows':sum(r['mint']==mint for r in result),'evidence_keys':[r['evidence_key'] for r in result[:32]],'qualification':'SUPPORT_ONLY_NOT_AN_ACCEPTED_TYPED_PARENT_MAPPING'};supplementary['atomic_signature_probes']+=1
  status,outcome=lookup('ops','wt_attribution_outcomes',('mint',),(mint,));status2,queue=lookup('ops','wt_walkback_queue',('mint',),(mint,));f['same_mint_retained_operational_rows']={'selected_outcome':present('ops','wt_attribution_outcomes',('mint',),(mint,)),'selected_queue':present('ops','wt_walkback_queue',('mint',),(mint,)),'source_outcome':len(outcome),'source_queue':len(queue)}
  # Record the actual legacy namespace owner without introducing a new mapping.
  if outcome and queue:
   oq=outcome[0];qq=queue[0]
   f['operational_state']={'queue_status':qq.get('status'),'intelligence_outcome':qq.get('intelligence_outcome'),'outcome_type':oq.get('outcome_type'),'outcome_operator_id':oq.get('operator_id'),'outcome_confidence':oq.get('confidence'),'canonical_watchtower_outcome':ns['is_canonical_watchtower_outcome'](oq.get('outcome_type'),oq.get('operator_id')),'funding_signature_matches_reference':qq.get('funder_sig')==event,'pending_retry':bool(oq.get('should_retry')),'quarantine_row_selected':present('ops','wt_lineage_quarantine',('mint',),(mint,))}
  if f['source_population_id']=='walkback_watchtower_confirmed_projection_v1':
   status,outs=lookup('ops','monitor_admission_outbox',('membership_id',),(event,));member=rows[('ops','operator_launch_membership')][(mint,)]
   ok=status=='OK' and len(outs)==1
   if ok:
    o=outs[0];ok=o['operation_id']==member['operator_id'] and o['mint']==mint and o['membership_id']==event and o['committed_at']==member['assigned_at'] and event==f"WATCHTOWER_CONFIRMED:{mint}:{member['assigned_at']}" and o['event_id']==ans['_id'](o['operation_id'],o['mint'],o['membership_id'],o['committed_at'],o['semantic_version'],o['opening_id'])
   f['legacy_namespace']={'kind':'MEMBERSHIP_COMMIT_ID_WITH_DURABLE_OUTBOX','existing_source_contract_verified':ok,'selected_outbox':len(outs)==1 and present('ops','monitor_admission_outbox',('event_id',),(outs[0]['event_id'],)),'outbox_state':outs[0].get('state') if outs else None,'source_writer':'watchtower_registry_promotion.project_watchtower_confirmed_membership + monitor_live_admission.commit_membership_and_outbox','mapping_status':'EXISTING_SOURCE_RELATIONSHIP_VERIFIED; NEW_TYPED_IMPORT_BRANCH_NOT_QUALIFIED' if ok else 'UNQUALIFIED_SOURCE_RELATIONSHIP'}
  else:
   ok=bool(outcome and queue and queue[0].get('funder_sig')==event and ns['is_canonical_watchtower_outcome'](outcome[0].get('outcome_type'),outcome[0].get('operator_id')))
   f['legacy_namespace']={'kind':'LEGACY_FUNDER_SIGNATURE_REFERENCE','existing_source_contract_verified':ok,'source_owner':'same-mint wt_walkback_queue funding signature + canonical wt_attribution_outcomes; not a governance identity event','mapping_status':'RETAINED_LINK_VERIFIED; NEW_TYPED_IMPORT_BRANCH_NOT_QUALIFIED' if ok else 'UNQUALIFIED_SOURCE_RELATIONSHIP'}
 # Aggregate all findings; no records are changed or automatically excluded.
 bytable=collections.defaultdict(collections.Counter)
 for f in findings:bytable[f['database']+'.'+f['table']][f['cause']]+=1
 byop=collections.Counter(f.get('operator_id','NO_OPERATOR_COLUMN') for f in findings)
 unknown=sum(1 for f in findings if f['cause'] in ['LEGACY_REFERENCE_TYPE_UNRESOLVED','UNQUALIFIED_SOURCE_LOOKUP','UNQUALIFIED_UNINDEXED_LOOKUP','LOOKUP_BUDGET_EXCEEDED'])
 for kind,c in source.items():
  path=SRC/('flex_complete_database.db' if kind=='core' else 'wt_ops_v2.db');st=path.stat();metadata.append({'database':kind,'path':str(path),'device':st.st_dev,'inode':st.st_ino,'schema_version':c.execute('PRAGMA schema_version').fetchone()[0]})
 for c in list(source.values())+list(reference.values()):c.close()
 result={'status':'HOLD' if findings else 'PASS','total_selected_rows':selected,'selected_rows_with_source_authority':sum(len(x) for x in rows.values()),'base_selected_rows':selected-sum(added.values()),'six_case_closure_additions':dict(added),'total_reference_edges_checked':edges,'resolved_reference_edges':resolved,'null_reference_edges_not_constrained':null_edges,'unresolved_reference_count':len(findings),'unique_affected_rows':len({digest([f['database'],f['table'],f['key']]) for f in findings}),'unresolved_by_table':{k:dict(v) for k,v in bytable.items()},'unresolved_by_operator':dict(byop),'legacy_namespace_groups':dict(collections.Counter(f.get('legacy_namespace',{}).get('kind','OTHER') for f in findings)),'namespace_source_links_verified':sum(bool(f.get('legacy_namespace',{}).get('existing_source_contract_verified')) for f in findings),'unknown_reference_count':unknown,'typed_reference_branches':dict(resolved_types),'qualified_exclusions':{'existing_synthetic_controls':len(excluded_seen),'new_exclusions':0,'authority':'seed_minimization/row_classification_manifest.json explicit synthetic reason; no operational orphan excluded'},'edge_counts_by_contract':dict(edge_summary),'lookup_plans':plans,'supplementary_evidence_probes':dict(supplementary),'source_metadata':metadata,'source_row_digest':source_digest.hexdigest(),'source_selected_encoded_bytes':source_bytes,'selection_contract':'Same selected reference PK populations as20b0f4e1, excluding29 documented syntheticcontrols, retaining15 externally authoritative rows, plus accepted sixByzantine closures. No new population search.','coverage':'All declared FKs under accepted price and typed-membership profiles; all typed membership branches; listed accepted assessment, registry and monitor ownership references. Opaque provenance strings/JSON are retained and hashed, not invented as relational FK contracts. This is not a new full semantic/schema audit.','snapshot_limit':'Each source uses its own read-only SQLite snapshot; no cross-database atomic generation/cutover authority claimed. Repeat source drift invalidates reuse unless selector row digests verify.','checkpoint_batches':checkpoint_seq,'batch_rows':BATCH,'findings_sha256':digest(findings),'production_mutations':0,'provider_calls':0,'target_databases_created':0,'full_database_copies':0,'corrections_applied':0,'exclusions_applied':0,'before_storage':before,'after_storage_before_final_metadata':storage(),'runtime_deployment_required':False,'runtime_deployment_reason':'Read-only reference preflight and bounded diagnostic metadata only.'}
 save(base/'findings.json',findings);save(base/'excluded_controls.json',excluded_seen);save(base/'contracts.json',{'declared_and_named_contracts':contracts,'logical_contracts':LOGICAL,'typed_membership_contract':'20b0f4e1 includes202e30b4; exclusive real identity-event OR confirmed-match namespace; operation/mint checks','price_contract':'token_market_cap_peaks.mint -> token_analysis.mint; explicit accepted canonical-price-identity profile'})
 save(base/'result.json',result);result['after_storage']=storage();save(base/'result.json',result);save(base/'checkpoint.json',{'phase':'COMPLETE','complete':True,'selected_rows':selected,'reference_edges':edges,'findings':len(findings),'source_row_digest':source_digest.hexdigest(),'findings_sha256':digest(findings),'batch_count':checkpoint_seq});print(json.dumps({k:v for k,v in result.items() if k not in ['lookup_plans','edge_counts_by_contract']},indent=2))
if __name__=='__main__':main(Path(sys.argv[1]))
